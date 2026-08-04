"""Cross-engine reconciliation: every headline number traces to an engine.

The platform quotes a handful of headline figures — the annual uplift, the
forecast MASE, the routing saving, revenue, gross margin — on the dashboard, in
the README table and in the exported deck. This module is the machine-checked
ledger that keeps them honest: it re-runs the engines on the one seeded dataset,
maps each headline number to the *specific engine field* it comes from, and
verifies the **cross-engine identities** that tie the composed figures back to
their parts. Nothing is re-invented here; it only re-runs
:mod:`dip.analytics`, :mod:`dip.forecast`, :mod:`dip.optimize` and
:mod:`dip.prescribe` and asserts they agree.

Two things are checked:

1. **Identities** — relational invariants across engines, e.g. the headline
   uplift equals the sum of the three monetised levers, and the routing €
   lever equals the routing engine's km saved priced at the documented
   ``EUR/km`` over the documented runs/year. If a future edit makes one engine
   drift from another, an identity fails.
2. **Doc-drift guard** — each headline number is formatted exactly as the
   README displays it and checked to be *present* in ``README.md``. If an
   engine value changes and the README is not updated, the guard trips.

Deterministic: the dataset is seeded and every engine terminates on a fixed
seed / solution budget (never a wall clock), so :func:`reconcile` returns the
same structure every run and :func:`write_reports` writes byte-identical files.
The reports carry no timestamp for exactly this reason.

Everything is computed on the **seeded synthetic dataset** — it models no real
company; the figures are illustrative of the method, not a real-world claim.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .analytics import kpis
from .data import SEED, Dataset, build_dataset
from .forecast import forecast_revenue
from .optimize import optimize_assortment, optimize_prices, optimize_routes
from .prescribe import COST_PER_KM, ROUTING_RUNS_PER_YEAR, build_plan

_REPO_ROOT = Path(__file__).resolve().parents[1]
_README = _REPO_ROOT / "README.md"
_DEFAULT_OUT = _REPO_ROOT / "docs"

DATA_LABEL = (
    "seeded synthetic dataset — deterministic (fixed seed + MILP/CVRP solution "
    "budgets, never wall-clock); models no real company"
)


# ---- display formatters (must reproduce the README/dashboard strings) -------


def _eur(x: float) -> str:
    return f"EUR {float(x):,.0f}"


def _eur_signed(x: float) -> str:
    x = float(x)
    return f"{'+' if x >= 0 else '-'}EUR {abs(x):,.0f}"


def _pct1(x: float) -> str:
    return f"{float(x) * 100:.1f}%"


def _pct1_signed(x: float) -> str:
    return f"{float(x) * 100:+.1f}%"


def _km0(x: float) -> str:
    return f"{float(x):,.0f} km"


def _mase2(x: float) -> str:
    return f"MASE {float(x):.2f}"


def _skus(x: int) -> str:
    return f"{int(x)} SKUs"


def _vehicles(x: int) -> str:
    return f"{int(x)} vehicles"


def _num(v: object) -> str:
    """Stable string form of a claim value (coerced off any NumPy scalar)."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    return str(float(v))  # NumPy float64 -> plain float repr


# ---- the reconciliation -----------------------------------------------------


def _engine_run(ds: Dataset) -> dict:
    """Run every engine once, sharing routing+pricing into the plan.

    Mirrors how :mod:`app` and :mod:`powerbi.build_star` compose the plan: the
    routing and pricing solves are computed once and threaded into
    :func:`dip.prescribe.build_plan`, so the plan quotes the same objects the
    standalone optimisers return — the identities below cannot be gamed by a
    second, differently-seeded solve.
    """
    k = kpis(ds)
    fc = forecast_revenue(ds)
    routes = optimize_routes(ds)
    prices = optimize_prices(ds)
    assort = optimize_assortment(ds)  # default budget (40% of full capital)
    plan = build_plan(ds, routes=routes, prices=prices)
    return {"k": k, "fc": fc, "routes": routes, "prices": prices, "assort": assort, "plan": plan}


def _claims(run: dict) -> list[dict]:
    """One row per headline number: value, engine source, README display."""
    k, fc, routes, prices, assort, plan = (
        run["k"], run["fc"], run["routes"], run["prices"], run["assort"], run["plan"]
    )
    # (label, value, source-field, README display string)
    specs = [
        ("Revenue (24 mo)", k["revenue"], "analytics.kpis.revenue", _eur(k["revenue"])),
        ("Gross margin", k["gross_margin"], "analytics.kpis.gross_margin", _eur(k["gross_margin"])),
        ("Gross margin %", k["gross_margin_pct"], "analytics.kpis.gross_margin_pct", _pct1(k["gross_margin_pct"])),
        ("YoY revenue growth", k["yoy"], "analytics.kpis.yoy", _pct1_signed(k["yoy"])),
        ("OTIF service (modelled)", k["otif"], "analytics.kpis.otif", _pct1(k["otif"])),
        ("Forecast accuracy", fc["mase"], "forecast.forecast_revenue.mase", _mase2(fc["mase"])),
        ("Next-month revenue", fc["next_month_revenue"], "forecast.forecast_revenue.next_month_revenue", _eur(fc["next_month_revenue"])),
        ("Assortment MILP margin", assort["milp"]["margin"], "optimize.optimize_assortment.milp.margin", _eur(assort["milp"]["margin"])),
        ("Assortment greedy margin", assort["greedy"]["margin"], "optimize.optimize_assortment.greedy.margin", _eur(assort["greedy"]["margin"])),
        ("Assortment uplift vs greedy", assort["uplift_vs_greedy"], "optimize.optimize_assortment.uplift_vs_greedy", _eur_signed(assort["uplift_vs_greedy"])),
        ("Assortment SKUs carried", assort["milp"]["count"], "optimize.optimize_assortment.milp.count", _skus(assort["milp"]["count"])),
        ("Assortment budget", assort["budget"], "optimize.optimize_assortment.budget", _eur(assort["budget"])),
        ("Pricing uplift", prices["uplift"], "optimize.optimize_prices.uplift", _eur_signed(prices["uplift"])),
        ("Pricing uplift %", prices["uplift_pct"], "optimize.optimize_prices.uplift_pct", _pct1_signed(prices["uplift_pct"])),
        ("Routing optimised km", routes["optimized_km"], "optimize.optimize_routes.optimized_km", _km0(routes["optimized_km"])),
        ("Routing baseline km", routes["baseline_km"], "optimize.optimize_routes.baseline_km", _km0(routes["baseline_km"])),
        ("Routing km saved", routes["km_saved"], "optimize.optimize_routes.km_saved", _km0(routes["km_saved"])),
        ("Routing % saved", routes["pct_saved"], "optimize.optimize_routes.pct_saved", _pct1(routes["pct_saved"])),
        ("Routing vehicles used", routes["n_vehicles_used"], "optimize.optimize_routes.n_vehicles_used", _vehicles(routes["n_vehicles_used"])),
        ("Expected annual uplift", plan["expected_uplift_eur"], "prescribe.build_plan.expected_uplift_eur", _eur(plan["expected_uplift_eur"])),
        ("Uplift % of annual gross margin", plan["expected_uplift_pct"], "prescribe.build_plan.expected_uplift_pct", _pct1(plan["expected_uplift_pct"])),
    ]
    return [
        {"label": label, "value": value, "source": source, "display": display}
        for label, value, source, display in specs
    ]


def _identities(run: dict) -> list[dict]:
    """Cross-engine identities, each compared at the precision it is published.

    Both sides are normalised to the displayed decimal places before comparison,
    so an ``ok: True`` means the published numbers are mutually consistent to the
    cent (or to the reported %), which is exactly the "no silent drift" claim.
    """
    k, fc, routes, prices, assort, plan = (
        run["k"], run["fc"], run["routes"], run["prices"], run["assort"], run["plan"]
    )
    horizon = len(k["months"]) or 12
    lev = plan["levers"]
    ids: list[dict] = []

    def add(id_: str, statement: str, lhs: float, rhs: float, dp: int) -> None:
        left = round(float(lhs), dp)
        right = round(float(rhs), dp)
        ids.append(
            {"id": id_, "statement": statement, "lhs": left, "rhs": right, "dp": dp, "ok": left == right}
        )

    add(
        "uplift_composition",
        "Expected annual uplift = pricing lever + assortment lever + routing lever",
        plan["expected_uplift_eur"],
        lev["pricing"] + lev["assortment"] + lev["routing"],
        2,
    )
    add(
        "routing_eur_bridge",
        f"Routing lever = km saved x EUR {COST_PER_KM:.2f}/km x {ROUTING_RUNS_PER_YEAR} runs/yr",
        lev["routing"],
        routes["km_saved"] * COST_PER_KM * ROUTING_RUNS_PER_YEAR,
        2,
    )
    add(
        "routing_km_identity",
        "Routing km saved = baseline km - optimised km",
        routes["km_saved"],
        routes["baseline_km"] - routes["optimized_km"],
        2,
    )
    add(
        "routing_pct_identity",
        "Routing % saved = km saved / baseline km",
        routes["pct_saved"],
        routes["km_saved"] / routes["baseline_km"],
        4,
    )
    add(
        "pricing_lever_identity",
        "Pricing lever = new profit - base profit (from the pricing engine)",
        lev["pricing"],
        prices["new_profit"] - prices["base_profit"],
        2,
    )
    add(
        "pricing_lever_ties_engine",
        "Plan pricing lever = pricing engine's reported uplift",
        lev["pricing"],
        prices["uplift"],
        2,
    )
    add(
        "assortment_lever_identity",
        "Assortment lever = MILP margin - greedy margin",
        lev["assortment"],
        assort["milp"]["margin"] - assort["greedy"]["margin"],
        2,
    )
    add(
        "assortment_lever_ties_engine",
        "Plan assortment lever = assortment engine's edge over greedy",
        lev["assortment"],
        assort["uplift_vs_greedy"],
        2,
    )
    add(
        "gross_margin_identity",
        "Gross margin = revenue - COGS",
        k["gross_margin"],
        k["revenue"] - k["cogs"],
        2,
    )
    add(
        "gross_margin_pct_identity",
        "Gross margin % = gross margin / revenue",
        k["gross_margin_pct"],
        k["gross_margin"] / k["revenue"],
        4,
    )
    add(
        "annual_gross_margin_basis",
        f"Annual gross margin = gross margin x 12 / {horizon} months of history",
        plan["annual_gross_margin"],
        k["gross_margin"] * 12.0 / horizon,
        2,
    )
    add(
        "uplift_pct_basis",
        "Uplift % = expected annual uplift / annual gross margin",
        plan["expected_uplift_pct"],
        plan["expected_uplift_eur"] / plan["annual_gross_margin"],
        4,
    )
    add(
        "assortment_budget_basis",
        "Default budget = 40% of the range's full working capital",
        plan["budget"],
        0.4 * assort["full_capital"],
        2,
    )
    add(
        "next_month_forecast_tie",
        "Plan next-month revenue = forecast engine's next-month point",
        plan["next_month_revenue"],
        fc["next_month_revenue"],
        2,
    )
    add(
        "baseline_gross_margin_tie",
        "Plan baseline gross margin = analytics gross-margin KPI",
        plan["baseline_gross_margin"],
        k["gross_margin"],
        2,
    )
    add(
        "plan_mase_tie",
        "Plan-quoted MASE = forecast engine's backtest MASE",
        plan["forecast_mase"],
        fc["mase"],
        4,
    )
    return ids


def reconcile(ds: Dataset | None = None, readme_text: str | None = None) -> dict:
    """Run the engines and return the reconciliation structure.

    ``readme_text`` overrides the on-disk README (used by tests); by default the
    repo-root ``README.md`` is read if present. Each claim gets an ``in_readme``
    flag: the headline number, formatted exactly as the README displays it, must
    appear verbatim in the README text.
    """
    ds = ds or build_dataset()
    run = _engine_run(ds)
    claims = _claims(run)
    identities = _identities(run)

    if readme_text is None:
        readme_text = _README.read_text(encoding="utf-8") if _README.exists() else ""
    for c in claims:
        c["in_readme"] = c["display"] in readme_text

    readme_missing = [c["source"] for c in claims if not c["in_readme"]]
    identities_ok = all(i["ok"] for i in identities)
    readme_ok = not readme_missing
    return {
        "data_label": DATA_LABEL,
        "seed": SEED,
        "claims": claims,
        "identities": identities,
        "readme_missing": readme_missing,
        "identities_ok": identities_ok,
        "readme_ok": readme_ok,
        "all_ok": identities_ok and readme_ok,
    }


# ---- report emitters (deterministic, LF line endings, no timestamp) ---------


def _csv_text(rec: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["metric", "value", "source_engine_field", "readme_display", "in_readme"])
    for c in rec["claims"]:
        w.writerow([c["label"], _num(c["value"]), c["source"], c["display"], "yes" if c["in_readme"] else "no"])
    return buf.getvalue()


def _md_text(rec: dict) -> str:
    lines: list[str] = []
    lines.append("# Numbers reconciliation")
    lines.append("")
    lines.append(
        "Machine-checked ledger tying every headline figure the platform quotes "
        "to the specific engine field it comes from, plus the cross-engine "
        "identities that keep the composed numbers honest. Regenerate with "
        "`python -m dip --reconcile`; the identities and the README-presence "
        "guard are enforced by `tests/test_reconcile.py`."
    )
    lines.append("")
    lines.append(f"- Data: {rec['data_label']} (seed {rec['seed']}).")
    verdict = "all identities hold and every headline number is present in the README"
    fail = "SOME CHECKS FAILED — see the flags below"
    lines.append(f"- Status: **{verdict if rec['all_ok'] else fail}.**")
    lines.append("")

    lines.append("## Headline numbers -> engine source")
    lines.append("")
    lines.append("| Metric | Value | Traces to engine field | README shows | In README |")
    lines.append("| --- | --- | --- | --- | --- |")
    for c in rec["claims"]:
        flag = "yes" if c["in_readme"] else "**NO**"
        lines.append(f"| {c['label']} | {_num(c['value'])} | `{c['source']}` | `{c['display']}` | {flag} |")
    lines.append("")

    lines.append("## Cross-engine identities")
    lines.append("")
    lines.append("| Identity | Check | Left | Right | Holds |")
    lines.append("| --- | --- | --- | --- | --- |")
    for i in rec["identities"]:
        holds = "yes" if i["ok"] else "**NO**"
        lines.append(f"| `{i['id']}` | {i['statement']} | {i['lhs']} | {i['rhs']} | {holds} |")
    lines.append("")

    lines.append("## Plain-language read")
    lines.append("")
    lines.append(
        "The platform's headline is an **expected annual uplift**. This report "
        "shows it is not a free-floating number: it is exactly the sum of three "
        "monetised levers, each of which traces to its own optimiser. The "
        "pricing lever is the pricing engine's new-minus-base profit; the "
        "assortment lever is the MILP's margin edge over the greedy baseline; "
        "the routing lever is the CVRP's kilometres saved, priced at "
        f"EUR {COST_PER_KM:.2f}/km over {ROUTING_RUNS_PER_YEAR} runs a year. The "
        "descriptive KPIs tie the same way — gross margin is revenue minus COGS, "
        "and the uplift percentage is quoted against *annual* gross margin so a "
        "24-month history does not halve the ratio. Every one of those "
        "relationships is checked above, and every number is checked to still "
        "match what the README prints."
    )
    lines.append("")

    lines.append("## Honest limitations")
    lines.append("")
    lines.append(
        "- **Synthetic data.** Every figure is measured on one seeded synthetic "
        "dataset; it is illustrative of the method, not a real-world benchmark or "
        "a claim of state-of-the-art performance."
    )
    lines.append(
        "- **The README-presence guard works at display precision.** A figure the "
        "README rounds (e.g. MASE to two decimals) is checked at that precision, "
        "so a sub-display drift in a rounded value need not trip the guard. The "
        "identity checks and the value column of `reconciliation.csv` carry the "
        "engines' full reported precision."
    )
    lines.append(
        "- **This reconciles internal consistency, not correctness.** It proves "
        "the engines agree with each other and with the docs; it does not "
        "validate the underlying models against real demand, prices or routes."
    )
    lines.append("")
    return "\n".join(lines)


def write_reports(out_dir: Path | str = _DEFAULT_OUT, ds: Dataset | None = None) -> dict[str, Path]:
    """Write ``RECONCILIATION.md`` + ``reconciliation.csv`` and return their paths.

    Byte-identical across runs (deterministic engines, no timestamp) and written
    with LF line endings to match the repo's ``eol=lf`` .gitattributes so a
    regenerate does not show a spurious diff.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = reconcile(ds)
    md_path = out_dir / "RECONCILIATION.md"
    csv_path = out_dir / "reconciliation.csv"
    with md_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(_md_text(rec))
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(_csv_text(rec))
    return {"markdown": md_path, "csv": csv_path}


if __name__ == "__main__":  # pragma: no cover - manual/CLI use
    _rec = reconcile()
    print(f"Reconciliation on {_rec['data_label']} (seed {_rec['seed']})")
    for _i in _rec["identities"]:
        print(f"  [{'OK' if _i['ok'] else 'FAIL'}] {_i['id']}: {_i['lhs']} == {_i['rhs']}")
    _missing = _rec["readme_missing"]
    print(f"  README presence: {'all headline numbers found' if not _missing else f'MISSING {_missing}'}")
    print("  ALL OK" if _rec["all_ok"] else "  FAILURES PRESENT")
