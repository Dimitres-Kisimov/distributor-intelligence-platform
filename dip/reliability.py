"""Supplier lead-time reliability: measured receipts -> the safety stock they cost.

The inventory engine prices every SKU's safety stock off the *quoted* supplier
lead time, and its own caveat says so: the lead time is taken as deterministic.
This engine closes that gap with the discipline a purchasing desk actually
applies — **measure the receipts, not the vendor master.** From the platform's
purchase-order receipt history (quoted vs actually observed lead time, per SKU,
per supplier) it derives:

- a **supplier scorecard**: on-time rate (against a stated grace window),
  mean delay, quoted-vs-observed lead time, lead-time coefficient of variation,
  and a letter grade — one row per supplier, ranked by what its performance
  costs;
- the **safety-stock consequence**: for every SKU, safety stock is re-derived
  with the textbook *variable lead-time* formula
  ``sigma_LTD = sqrt(L_bar * sigma_D^2 + D_bar^2 * sigma_L^2)`` (mean measured
  lead time ``L_bar``, lead-time std ``sigma_L``) at the *same* ABC-XYZ service
  target the inventory policy uses, and the EUR gap to the quoted-basis figure
  is split exactly into a **delay effect** (the mean lead time is longer or
  shorter than quoted) and a **variability effect** (the lead time wobbles);
- the **portfolio consequence**: how much extra working capital, and extra
  annual holding cost, supplier unreliability ties up versus the lead times the
  vendor master promises.

What it reuses vs. what is new
------------------------------
- **Reuses** the ABC-XYZ service-target matrix, the normal safety factor and
  the day/month conventions from :mod:`dip.inventory` — the quoted-basis
  safety stock computed here is *identical, SKU by SKU and in total*, to the
  inventory engine's (asserted by tests), so the delta is a true consequence,
  never a re-modelling artefact. The classification comes from
  :func:`dip.analytics.abc_xyz`, as everywhere else.
- **New** is the measurement layer (receipt statistics, on-time/grace scoring,
  supplier aggregation) and the variable lead-time safety-stock formula, which
  lives nowhere else in the platform.

Deterministic: pure arithmetic over the seeded receipt history — no RNG, no
wall clock — so :func:`supplier_reliability` returns the same structure every
run. Excel imports carry no procurement history, so the engine reports itself
unavailable there instead of inventing receipts (the cross-sell pattern).

Everything is computed on the seeded synthetic dataset; the receipts are drawn
from the generator's own supplier profiles, so the scorecard measures the
synthetic world, not any real vendor. Illustrative of the method, not a claim.
"""

from __future__ import annotations

import math

import numpy as np

from .analytics import abc_xyz
from .data import Dataset, build_dataset
from .inventory import (
    DAYS_PER_MONTH,
    HOLDING_COST_RATE,
    SERVICE_LEVEL_MATRIX,
    _z_for,
)

# ---- scoring policy (stated, not measured) ----------------------------------
# A receipt is on time when actual <= quoted + tolerance. Two days of grace is
# a common OTD window in MRO/distribution scorecards; it is a stated policy
# definition, not a measurement — ``tolerance_days`` re-scores against any
# window in [0, 30].
DEFAULT_TOLERANCE_DAYS = 2.0
_TOL_LO, _TOL_HI = 0.0, 30.0

# Letter-grade bands on the on-time rate — a reporting convention (policy
# choice), not a certification. The safety-stock EUR consequence is the
# measured column; the grade only makes the table scannable.
GRADE_BANDS = [("A", 0.95), ("B", 0.85), ("C", 0.70)]  # else "D"

# Scorecards on fewer receipts than this are flagged thin_sample (the
# cross-sell thin_support pattern): the stats are reported, not hidden, but a
# small sample makes them unstable.
THIN_SAMPLE_RECEIPTS = 36

UNAVAILABLE_NOTE = (
    "Supplier reliability needs a purchase-order receipt history (quoted vs "
    "actual lead times). The Excel import template covers products and monthly "
    "units only, so this view is available on the seeded synthetic dataset — "
    "POST /api/reset to return to it."
)


class ReliabilityError(ValueError):
    """Invalid reliability request — mapped to HTTP 400 by the API layer."""


def _validate_tolerance(tol: float) -> float:
    if isinstance(tol, bool) or not isinstance(tol, (int, float)):
        raise ReliabilityError(
            f"tolerance_days must be a number in [{_TOL_LO:.0f}, {_TOL_HI:.0f}], got {tol!r}"
        )
    tol = float(tol)
    if not math.isfinite(tol) or not (_TOL_LO <= tol <= _TOL_HI):
        raise ReliabilityError(
            f"tolerance_days must be a finite number of days in "
            f"[{_TOL_LO:.0f}, {_TOL_HI:.0f}], got {tol!r}"
        )
    return tol


def _grade_for(on_time_rate: float) -> str:
    for grade, floor in GRADE_BANDS:
        if on_time_rate >= floor:
            return grade
    return "D"


def _sku_reliability(
    *,
    demand_mean: float,
    demand_std: float,
    quoted_days: float,
    actual_days: np.ndarray,
    cost: float,
    z: float,
    tolerance_days: float,
) -> dict:
    """Measured lead-time stats + safety-stock consequence for one SKU.

    Pure arithmetic. ``demand_mean`` / ``demand_std`` are per *month* (the
    inventory engine's convention); lead times are in days. The quoted-basis
    safety stock uses the inventory engine's exact formula
    ``SS_q = z * sigma_D * sqrt(L_q / 30)``; the measured basis uses the
    variable lead-time form
    ``SS_m = z * sqrt((L_bar/30) * sigma_D^2 + D_bar^2 * (sigma_L/30)^2)``,
    which collapses to the quoted figure exactly when every receipt lands on
    the quoted lead time (``L_bar = L_q``, ``sigma_L = 0``).
    """
    n = int(actual_days.size)
    mean_days = float(actual_days.mean()) if n else quoted_days
    # sample std (ddof=1): this is a measurement from n receipts, not a census
    std_days = float(actual_days.std(ddof=1)) if n >= 2 else 0.0

    on_time_rate = float((actual_days <= quoted_days + tolerance_days).mean()) if n else 1.0
    mean_delay_days = float((actual_days - quoted_days).mean()) if n else 0.0
    lead_time_cv = std_days / mean_days if mean_days > 0 else 0.0

    quoted_m = quoted_days / DAYS_PER_MONTH
    mean_m = mean_days / DAYS_PER_MONTH
    std_m = std_days / DAYS_PER_MONTH

    # quoted basis — the inventory engine's formula, term for term
    ss_quoted = max(0.0, z * demand_std * math.sqrt(quoted_m))
    # measured mean, still treating lead time as deterministic (delay only)
    ss_at_mean = max(0.0, z * demand_std * math.sqrt(mean_m))
    # measured basis — variable lead time
    sigma_ltd = math.sqrt(mean_m * demand_std**2 + demand_mean**2 * std_m**2)
    ss_measured = max(0.0, z * sigma_ltd)

    delay_units = ss_at_mean - ss_quoted  # negative for early-delivering suppliers
    variability_units = ss_measured - ss_at_mean  # >= 0: variance never helps
    delta_units = ss_measured - ss_quoted  # == delay + variability, exactly

    rop_quoted = demand_mean * quoted_m + ss_quoted
    rop_measured = demand_mean * mean_m + ss_measured

    return {
        "n_receipts": n,
        "quoted_days": round(quoted_days, 1),
        "measured_mean_days": round(mean_days, 2),
        "measured_std_days": round(std_days, 2),
        "lead_time_cv": round(lead_time_cv, 4),
        "on_time_rate": round(on_time_rate, 4),
        "mean_delay_days": round(mean_delay_days, 2),
        "safety_stock_quoted": round(ss_quoted, 1),
        "safety_stock_measured": round(ss_measured, 1),
        "delta_units": round(delta_units, 1),
        "reorder_point_quoted": round(rop_quoted, 1),
        "reorder_point_measured": round(rop_measured, 1),
        "safety_stock_quoted_eur": round(ss_quoted * cost, 2),
        "safety_stock_measured_eur": round(ss_measured * cost, 2),
        "delta_eur": round(delta_units * cost, 2),
        "delay_effect_eur": round(delay_units * cost, 2),
        "variability_effect_eur": round(variability_units * cost, 2),
        # unrounded accumulators, so supplier/portfolio totals sum without
        # collecting 200 rounding errors (the published per-SKU value is rounded)
        "_ss_quoted_eur_exact": ss_quoted * cost,
        "_ss_measured_eur_exact": ss_measured * cost,
        "_delay_eur_exact": delay_units * cost,
        "_variability_eur_exact": variability_units * cost,
        "_on_time_count": float(on_time_rate * n),
    }


def supplier_reliability(
    ds: Dataset | None = None,
    *,
    tolerance_days: float = DEFAULT_TOLERANCE_DAYS,
    abc_xyz_result: dict | None = None,
    source: dict | None = None,
) -> dict:
    """Supplier scorecards + the safety-stock consequence of measured lead times.

    ``tolerance_days`` is the on-time grace window in days (default 2.0 — a
    stated scoring policy); it re-scores the on-time rates and grades but never
    touches the safety-stock arithmetic, which depends only on the receipt
    statistics. The ABC-XYZ classification (and with it each SKU's service
    target) is reused from :func:`dip.analytics.abc_xyz`, exactly as the
    inventory engine does. Deterministic.
    """
    ds = ds or build_dataset()
    tol = _validate_tolerance(tolerance_days)
    source = source or {"synthetic": True, "label": "seeded synthetic dataset", "filename": None}

    if ds.receipts is None or ds.suppliers is None:
        return {
            "available": False,
            "note": UNAVAILABLE_NOTE,
            "params": {"tolerance_days": round(tol, 1)},
            "n_suppliers": 0,
            "n_receipts": 0,
            "data_source": source,
        }

    abcxyz = abc_xyz_result if abc_xyz_result is not None else abc_xyz(ds)
    cell_by_sku = {row["sku_id"]: row["cell"] for row in abcxyz["per_sku"]}

    z_cache: dict[float, float] = {}

    def _z(sl: float) -> float:
        if sl not in z_cache:
            z_cache[sl] = _z_for(sl)
        return z_cache[sl]

    rec = ds.receipts
    # group receipt rows once: sku -> (supplier_id, actual lead times)
    by_sku: dict[str, dict] = {}
    for sku_id, supplier_id, actual in zip(rec["sku_id"], rec["supplier_id"], rec["actual_days"]):
        entry = by_sku.setdefault(str(sku_id), {"supplier_id": str(supplier_id), "actual": []})
        entry["actual"].append(float(actual))

    supplier_names = {s["supplier_id"]: s["name"] for s in ds.suppliers}
    sup_agg: dict[str, dict] = {
        s["supplier_id"]: {
            "n_skus": 0, "n_receipts": 0, "on_time": 0.0, "delay_sum": 0.0,
            "quoted_sum": 0.0, "actual_sum": 0.0, "cv_sum": 0.0,
            "ss_q": 0.0, "ss_m": 0.0, "delay_eur": 0.0, "var_eur": 0.0,
        }
        for s in ds.suppliers
    }

    skus_out: list[dict] = []
    skus_without_receipts: list[str] = []
    tot = {"ss_q": 0.0, "ss_m": 0.0, "delay_eur": 0.0, "var_eur": 0.0, "on_time": 0.0}
    tot_receipts = 0

    for s in ds.skus:
        entry = by_sku.get(s["sku_id"])
        if entry is None:
            skus_without_receipts.append(s["sku_id"])
            continue
        cell = cell_by_sku.get(s["sku_id"], "CZ")
        sl = SERVICE_LEVEL_MATRIX[cell]
        z = _z(sl)
        actual = np.asarray(entry["actual"], dtype=float)
        r = _sku_reliability(
            demand_mean=s["demand_mean"],
            demand_std=s["demand_std"],
            quoted_days=float(s["lead_time_days"]),
            actual_days=actual,
            cost=s["cost"],
            z=z,
            tolerance_days=tol,
        )
        row = {
            "sku_id": s["sku_id"],
            "name": s["name"],
            "category": s["category"],
            "supplier_id": entry["supplier_id"],
            "supplier_name": supplier_names.get(entry["supplier_id"], entry["supplier_id"]),
            "cell": cell,
            "service_level": round(sl, 4),
            **{k: v for k, v in r.items() if not k.startswith("_")},
        }
        skus_out.append(row)

        a = sup_agg[entry["supplier_id"]]
        a["n_skus"] += 1
        a["n_receipts"] += r["n_receipts"]
        a["on_time"] += r["_on_time_count"]
        a["delay_sum"] += r["mean_delay_days"] * r["n_receipts"]
        a["quoted_sum"] += r["quoted_days"] * r["n_receipts"]
        a["actual_sum"] += r["measured_mean_days"] * r["n_receipts"]
        a["cv_sum"] += r["lead_time_cv"] * r["n_receipts"]
        a["ss_q"] += r["_ss_quoted_eur_exact"]
        a["ss_m"] += r["_ss_measured_eur_exact"]
        a["delay_eur"] += r["_delay_eur_exact"]
        a["var_eur"] += r["_variability_eur_exact"]

        tot["ss_q"] += r["_ss_quoted_eur_exact"]
        tot["ss_m"] += r["_ss_measured_eur_exact"]
        tot["delay_eur"] += r["_delay_eur_exact"]
        tot["var_eur"] += r["_variability_eur_exact"]
        tot["on_time"] += r["_on_time_count"]
        tot_receipts += r["n_receipts"]

    # worst consequence first: these are the SKUs the buyer walks into a
    # supplier review with
    skus_out.sort(key=lambda row: row["delta_eur"], reverse=True)

    suppliers_out: list[dict] = []
    for sid, a in sup_agg.items():
        if a["n_receipts"] == 0:
            continue
        n_rec = a["n_receipts"]
        on_time_rate = a["on_time"] / n_rec
        delta_eur = a["ss_m"] - a["ss_q"]
        suppliers_out.append(
            {
                "supplier_id": sid,
                "name": supplier_names[sid],
                "n_skus": a["n_skus"],
                "n_receipts": n_rec,
                "on_time_rate": round(on_time_rate, 4),
                "grade": _grade_for(on_time_rate),
                "thin_sample": n_rec < THIN_SAMPLE_RECEIPTS,
                "avg_quoted_days": round(a["quoted_sum"] / n_rec, 2),
                "avg_actual_days": round(a["actual_sum"] / n_rec, 2),
                "mean_delay_days": round(a["delay_sum"] / n_rec, 2),
                "lead_time_cv": round(a["cv_sum"] / n_rec, 4),
                "safety_stock_quoted_eur": round(a["ss_q"], 2),
                "safety_stock_measured_eur": round(a["ss_m"], 2),
                "delta_eur": round(delta_eur, 2),
                "delay_effect_eur": round(a["delay_eur"], 2),
                "variability_effect_eur": round(a["var_eur"], 2),
                "extra_holding_cost_eur": round(delta_eur * HOLDING_COST_RATE, 2),
            }
        )
    suppliers_out.sort(key=lambda row: row["delta_eur"], reverse=True)

    delta_total = tot["ss_m"] - tot["ss_q"]
    totals = {
        "n_suppliers": len(suppliers_out),
        "n_skus": len(skus_out),
        "n_skus_without_receipts": len(skus_without_receipts),
        "n_receipts": tot_receipts,
        "on_time_rate": round(tot["on_time"] / tot_receipts, 4) if tot_receipts else None,
        "safety_stock_quoted_eur": round(tot["ss_q"], 2),
        "safety_stock_measured_eur": round(tot["ss_m"], 2),
        "delta_eur": round(delta_total, 2),
        "delay_effect_eur": round(tot["delay_eur"], 2),
        "variability_effect_eur": round(tot["var_eur"], 2),
        "extra_holding_cost_eur": round(delta_total * HOLDING_COST_RATE, 2),
        "worst_supplier": suppliers_out[0]["supplier_id"] if suppliers_out else None,
    }

    if source.get("synthetic", True):
        provenance = (
            "Seeded synthetic dataset — deterministic. Lead-time statistics are measured "
            "from the generated PO receipt history (12 receipts per SKU); safety-stock "
            "consequences are re-derived at the same ABC-XYZ service targets the inventory "
            "policy uses."
        )
    else:  # pragma: no cover - imports return the unavailable payload above
        provenance = f"Imported workbook '{source.get('filename')}' — no receipt history."

    return {
        "available": True,
        "params": {
            "tolerance_days": round(tol, 1),
            "receipts_per_sku": tot_receipts / len(skus_out) if skus_out else 0,
            "grade_bands": {g: f for g, f in GRADE_BANDS},
            "thin_sample_receipts": THIN_SAMPLE_RECEIPTS,
            "holding_rate": round(HOLDING_COST_RATE, 4),
        },
        "totals": totals,
        "suppliers": suppliers_out,
        "skus": skus_out,
        "provenance": provenance,
        "data_source": source,
        "note": (
            "Supplier scorecards measured from the PO receipt history (on-time = actual <= "
            f"quoted + {tol:.0f}d grace). Safety stock is re-derived per SKU with the variable "
            "lead-time formula sigma_LTD = sqrt(L_bar*sigma_D^2 + D_bar^2*sigma_L^2) at the "
            "same ABC-XYZ service targets the inventory policy uses; the quoted-basis column "
            "reproduces the inventory engine's safety stock exactly, so the delta is the "
            "working-capital consequence of lead-time reality vs the vendor master. It splits "
            "exactly into a delay effect (longer/shorter average) and a variability effect "
            "(wobble; never negative)."
        ),
        "caveats": [
            (
                "The receipt history is seeded synthetic data: the scorecard measures the "
                "generator's own supplier profiles, not any real vendor's performance."
            ),
            (
                "The variable lead-time formula assumes demand and lead time are independent "
                "and normally distributed; 12 receipts per SKU is a small sample, so the "
                "measured std carries estimation error a production system would smooth "
                "(e.g. with a hierarchical or Bayesian estimate across a supplier's SKUs)."
            ),
            (
                "This is a working-capital consequence, not an uplift lever: it prices what "
                "buffering supplier behaviour costs (or frees), and is deliberately kept out "
                "of the platform's expected-uplift total."
            ),
            (
                f"On-time uses a stated {tol:.0f}-day grace window and the letter grades are "
                "reporting bands — both policy choices, not certifications."
            ),
        ],
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    rel = supplier_reliability()
    t = rel["totals"]
    print(
        f"Suppliers: {t['n_suppliers']}  receipts: {t['n_receipts']}  "
        f"on-time (2d grace): {t['on_time_rate']:.1%}"
    )
    print(
        f"Safety stock: quoted EUR {t['safety_stock_quoted_eur']:,.0f} -> measured "
        f"EUR {t['safety_stock_measured_eur']:,.0f}  (delta EUR {t['delta_eur']:,.0f}: "
        f"delay EUR {t['delay_effect_eur']:,.0f} + variability EUR {t['variability_effect_eur']:,.0f})"
    )
    print(f"Extra annual holding cost: EUR {t['extra_holding_cost_eur']:,.0f}")
    worst = rel["suppliers"][0]
    print(
        f"Worst: {worst['supplier_id']} {worst['name']} — grade {worst['grade']}, "
        f"on-time {worst['on_time_rate']:.0%}, delta EUR {worst['delta_eur']:,.0f}"
    )
