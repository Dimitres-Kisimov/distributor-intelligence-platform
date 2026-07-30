"""build_star.py — export the platform's plan as a Power BI star schema.

Power BI Desktop imports these CSVs and models them into a classic star:

    fact_sales  (grain = month x SKU)  -- the additive measures live here
        |  sku       -> dim_sku[sku]
        |  category  -> dim_category[category]
        |  date_key  -> dim_date[date_key]

    kpi_headline   (disconnected 1-column-per-row scalar table)
    provenance     (disconnected data-labelling table)

No Power BI licence or tenant is needed to *generate* the model — the point of
this pack is to demonstrate dimensional modelling + DAX. Run it from the repo
root (it imports the ``dip`` package directly, so the CSVs are always in step
with the engines — no intermediate JSON to drift):

    python powerbi/build_star.py     # writes powerbi/data/*.csv

Everything is computed on the one seeded synthetic dataset (``dip/data.py``),
so the CSVs are deterministic and byte-identical across runs. The fact table
ties to the KPI headline to the cent (revenue, COGS, gross margin) — verified
at the end of :func:`build` and pinned by a test.

Author: Dimitres Kisimov, 2026. All rights reserved (portfolio review).
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Allow ``python powerbi/build_star.py`` from the repo root to import ``dip``
# (the script's own directory, not the repo root, is what lands on sys.path).
# The dip imports live inside the functions below so this insert always runs
# first, without tripping import-ordering lints.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_OUT = Path(__file__).resolve().parent / "data"

_MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _date_key(label: str) -> int:
    """``YYYY-MM`` -> integer ``YYYYMM01`` surrogate key."""
    y, m = int(label[:4]), int(label[5:7])
    return int(f"{y:04d}{m:02d}01")


def _writer(path: Path):
    # LF line endings on every platform (Python's csv default is CRLF): matches
    # the repo's ``eol=lf`` .gitattributes so the committed CSVs stay byte-stable
    # and don't show as modified after a regenerate.
    f = path.open("w", newline="", encoding="utf-8")
    return f, csv.writer(f, lineterminator="\n")


def build() -> dict[str, Path]:
    """Generate the star-schema CSVs and return ``{name: path}``.

    Deterministic: the dataset is seeded and every engine terminates on a fixed
    seed / solution budget, so repeated runs write byte-identical files.
    """
    from dip.analytics import kpis
    from dip.data import build_dataset
    from dip.forecast import forecast_revenue
    from dip.optimize import optimize_assortment, optimize_prices, optimize_routes
    from dip.prescribe import build_plan

    ds = build_dataset()
    k = kpis(ds)
    routes = optimize_routes(ds)
    prices = optimize_prices(ds)  # default guardrail (±15%)
    plan = build_plan(ds, routes=routes, prices=prices)
    assort = optimize_assortment(ds)  # default budget (40% of full capital)
    fc = forecast_revenue(ds)

    _OUT.mkdir(parents=True, exist_ok=True)
    skus = ds.skus
    meta = {s["sku_id"]: s for s in skus}
    carried = set(assort["carried_skus"])
    price_by = {r["sku_id"]: r for r in prices["recommendations"]}

    # ---- dim_date (the 24 history months of the ledger) -------------------
    dim_date = _OUT / "dim_date.csv"
    f, w = _writer(dim_date)
    w.writerow(["date_key", "date", "year", "month_num", "month_name", "quarter"])
    for label in ds.months:
        y, m = int(label[:4]), int(label[5:7])
        w.writerow([_date_key(label), f"{label}-01", y, m, _MONTH_NAMES[m], f"Q{(m - 1) // 3 + 1}"])
    f.close()

    # ---- dim_category -----------------------------------------------------
    cat_rows: dict[str, dict] = {}
    for s in skus:
        d = cat_rows.setdefault(s["category"], {"n": 0, "elast": 0.0, "cost": 0.0, "price": 0.0})
        d["n"] += 1
        d["elast"] += s["elasticity"]
        d["cost"] += s["cost"]
        d["price"] += s["price"]
    dim_category = _OUT / "dim_category.csv"
    f, w = _writer(dim_category)
    w.writerow(["category", "n_skus", "avg_elasticity", "avg_unit_cost_eur", "avg_unit_price_eur"])
    for cat in sorted(cat_rows):
        d = cat_rows[cat]
        n = d["n"]
        w.writerow([cat, n, round(d["elast"] / n, 3), round(d["cost"] / n, 2), round(d["price"] / n, 2)])
    f.close()

    # ---- dim_sku (attributes + the SKU-level plan decisions) --------------
    dim_sku = _OUT / "dim_sku.csv"
    f, w = _writer(dim_sku)
    w.writerow([
        "sku", "name", "category", "region", "channel",
        "unit_cost_eur", "unit_price_eur", "unit_margin_eur", "elasticity",
        "lead_time_days", "shelf_space", "carried_in_plan",
        "recommended_price_eur", "price_change_pct", "pricing_profit_delta_eur",
    ])
    for sku in sorted(meta):
        s = meta[sku]
        pr = price_by.get(sku, {})
        w.writerow([
            sku, s["name"], s["category"], s["region"], s["channel"],
            round(s["cost"], 2), round(s["price"], 2), round(s["unit_margin"], 2), s["elasticity"],
            s["lead_time_days"], s["shelf_space"], 1 if sku in carried else 0,
            pr.get("price_new", ""), pr.get("change_pct", ""), pr.get("profit_delta", ""),
        ])
    f.close()

    # ---- fact_sales (grain = month x SKU) ---------------------------------
    # The additive measures aggregate this. Its revenue / COGS / gross-margin
    # sums tie to the KPI headline to the cent (checked below).
    m = ds.monthly
    rev_sum = cogs_sum = gm_sum = 0.0
    fact = _OUT / "fact_sales.csv"
    f, w = _writer(fact)
    w.writerow([
        "date_key", "sku", "category", "region", "channel",
        "units", "revenue_eur", "cogs_eur", "gross_margin_eur",
    ])
    for month, sku, region, channel, category, units, revenue, cogs in zip(
        m["month"], m["sku_id"], m["region"], m["channel"], m["category"],
        m["units"], m["revenue"], m["cogs"],
    ):
        gross = round(float(revenue) - float(cogs), 2)
        rev_sum += float(revenue)
        cogs_sum += float(cogs)
        gm_sum += gross
        w.writerow([
            _date_key(str(month)), str(sku), str(category), str(region), str(channel),
            round(float(units), 1), round(float(revenue), 2), round(float(cogs), 2), gross,
        ])
    f.close()

    # ---- kpi_headline (disconnected 1-row-per-metric scalar table) --------
    kpi = _OUT / "kpi_headline.csv"
    f, w = _writer(kpi)
    w.writerow(["metric", "value"])
    for metric, value in [
        ("revenue_eur", k["revenue"]),
        ("cogs_eur", k["cogs"]),
        ("gross_margin_eur", k["gross_margin"]),
        ("gross_margin_pct", k["gross_margin_pct"]),
        ("yoy_growth", k["yoy"]),
        ("aov_eur", k["aov"]),
        ("otif_service", k["otif"]),
        ("n_skus", k["n_skus"]),
        ("n_customers", k["n_customers"]),
        ("expected_uplift_eur", plan["expected_uplift_eur"]),
        ("expected_uplift_pct", plan["expected_uplift_pct"]),
        ("pricing_uplift_eur", plan["levers"]["pricing"]),
        ("assortment_uplift_eur", plan["levers"]["assortment"]),
        ("routing_uplift_eur", plan["levers"]["routing"]),
        ("assortment_budget_eur", plan["budget"]),
        ("assortment_full_capital_eur", plan["full_capital"]),
        ("assortment_capital_used_eur", assort["milp"]["capital_used"]),
        ("assortment_margin_captured_eur", assort["milp"]["margin"]),
        ("assortment_skus_carried", assort["milp"]["count"]),
        ("price_guardrail", plan["max_change"]),
        ("forecast_mase", fc["mase"]),
        ("next_month_revenue_eur", plan["next_month_revenue"]),
    ]:
        w.writerow([metric, value])
    f.close()

    # ---- provenance (disconnected data-labelling table) -------------------
    prov = _OUT / "provenance.csv"
    f, w = _writer(prov)
    w.writerow(["key", "value"])
    for key, value in [
        ("data_label", "SYNTHETIC - seeded, deterministic; models no real company"),
        ("dataset_seed", "20260724"),
        ("n_skus", len(skus)),
        ("n_months", ds.n_months),
        ("generated_by", "powerbi/build_star.py"),
        ("author", "Dimitres Kisimov, 2026"),
        ("license", "All rights reserved (portfolio review)"),
    ]:
        w.writerow([key, value])
    f.close()

    # ---- tie check: fact sums == KPI headline, to the cent ----------------
    ties = {
        "revenue_eur": (round(rev_sum, 2), k["revenue"]),
        "cogs_eur": (round(cogs_sum, 2), k["cogs"]),
        "gross_margin_eur": (round(gm_sum, 2), k["gross_margin"]),
    }
    mismatches = {m_: pair for m_, pair in ties.items() if pair[0] != pair[1]}
    if mismatches:  # pragma: no cover - defensive; the seeded data always ties
        raise SystemExit(f"fact_sales does not tie to the KPI headline: {mismatches}")

    return {
        "fact_sales": fact,
        "dim_sku": dim_sku,
        "dim_category": dim_category,
        "dim_date": dim_date,
        "kpi_headline": kpi,
        "provenance": prov,
    }


def verify_ties() -> dict[str, tuple[float, float]]:
    """Recompute the fact→headline ties from the written CSVs (used by tests)."""
    from dip.analytics import kpis
    from dip.data import build_dataset

    k = kpis(build_dataset())
    rev = cogs = gm = 0.0
    with (_OUT / "fact_sales.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rev += float(row["revenue_eur"])
            cogs += float(row["cogs_eur"])
            gm += float(row["gross_margin_eur"])
    return {
        "revenue_eur": (round(rev, 2), k["revenue"]),
        "cogs_eur": (round(cogs, 2), k["cogs"]),
        "gross_margin_eur": (round(gm, 2), k["gross_margin"]),
    }


if __name__ == "__main__":
    paths = build()
    for name, p in paths.items():
        n = sum(1 for _ in p.open(encoding="utf-8")) - 1
        print(f"  ok {name:<16} {n:>5} rows  -> {p.relative_to(_ROOT)}")
    print("  tie check (fact_sales sums == KPI headline):")
    for metric, (fact_sum, headline) in verify_ties().items():
        flag = "OK" if fact_sum == headline else "MISMATCH"
        print(f"    [{flag}] {metric:<18} fact={fact_sum:,.2f}  headline={headline:,.2f}")
