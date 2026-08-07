"""Inventory replenishment policy: turn the demand signal into stock decisions.

The platform already *describes* the range (ABC-XYZ, the demand history) and
*forecasts* it, but it stops short of the question a distributor's purchasing
desk actually asks every week: **for each SKU, how much do we hold and when do
we re-order?** This engine answers it with the textbook continuous-review
``(reorder point, order quantity)`` policy, differentiated by the ABC-XYZ class
the descriptive layer already computes — so the classification finally drives a
decision instead of only colouring a grid.

For every SKU it derives, from the seeded demand process (mean + variability),
the supplier lead time and the unit cost:

- a **cycle service-level target** read from an ABC-XYZ policy matrix (important,
  predictable lines are protected harder than long-tail erratic ones);
- **safety stock** ``SS = z · σ_L`` — the buffer against demand variability over
  the replenishment lead time (``σ_L`` is the monthly demand std scaled to the
  lead-time window, ``z`` the normal quantile of the service target);
- the **reorder point** ``ROP = μ_L + SS`` — order when on-hand crosses it;
- the **economic order quantity** ``EOQ = √(2·D·S / H)`` (the Wilson lot size
  trading ordering cost ``S`` against annual holding cost ``H = holding_rate·cost``);
- the resulting **average inventory** (cycle stock ``EOQ/2`` + safety stock),
  its **working-capital** value, **inventory turns**, **days of cover**, the
  annual **holding + ordering** cost, and the **fill rate** implied by the
  policy via the standard-normal loss function.

What it reuses vs. what is new
------------------------------
- **Reuses** :func:`dip.analytics.abc_xyz` (previously descriptive-only) to set
  the per-SKU service target, and the per-SKU demand/lead-time/cost the seeded
  dataset already carries. It re-derives none of the forecasting, pricing,
  routing or assortment maths.
- **New** is the inventory maths itself (safety stock / ROP / EOQ / fill rate),
  which lives nowhere else in the platform. The assortment MILP carries only a
  *crude cycle-stock proxy* for working capital; this engine computes the
  rigorous figure the proxy stands in for.

Deterministic: pure arithmetic plus the fixed normal quantile/loss functions on
the seeded dataset — no RNG, no wall clock — so :func:`inventory_policy` returns
the same structure every run.

Everything is computed on the seeded synthetic dataset (or a user's imported
workbook); the figures are illustrative of the method, not a real-world claim.
"""

from __future__ import annotations

import math

from scipy.stats import norm

from .analytics import abc_xyz
from .data import Dataset, build_dataset

# ---- policy assumptions (stated, not measured) -----------------------------
# Annual inventory carrying rate: capital + storage + insurance + obsolescence,
# as a fraction of unit cost. 25%/yr is a common MRO/distribution planning
# figure; it is an assumption, not a figure observed from this synthetic world.
HOLDING_COST_RATE = 0.25
# Fixed cost to raise and receive one replenishment purchase order (admin,
# inbound handling), in EUR. Drives the EOQ ordering-vs-holding trade-off.
ORDER_COST_EUR = 50.0
DAYS_PER_YEAR = 365.0
DAYS_PER_MONTH = 30.0

# ABC-XYZ cycle service-level policy matrix. ABC (business importance, by
# revenue Pareto) sets the protection tier; XYZ (demand predictability, by
# coefficient of variation) trims it slightly for erratic lines where chasing
# the last point of service ties up capital inefficiently. Informed by standard
# ABC-XYZ segmentation practice — a policy choice, not a certification.
SERVICE_LEVEL_MATRIX: dict[str, float] = {
    "AX": 0.98, "AY": 0.97, "AZ": 0.96,
    "BX": 0.95, "BY": 0.94, "BZ": 0.92,
    "CX": 0.92, "CY": 0.90, "CZ": 0.88,
}

# Guardrails so a flat-service override is a real cycle-service level in (0, 1).
_SL_LO = 1e-6
_SL_HI = 1.0  # exclusive


class InventoryError(ValueError):
    """Invalid inventory request — mapped to HTTP 400 by the API layer."""


def _validate_service_level(sl: float) -> float:
    if isinstance(sl, bool) or not isinstance(sl, (int, float)):
        raise InventoryError(f"service_level must be a number in (0, 1), got {sl!r}")
    sl = float(sl)
    if not math.isfinite(sl) or not (_SL_LO <= sl < _SL_HI):
        raise InventoryError(f"service_level must be a finite fraction in (0, 1), got {sl!r}")
    return sl


def _z_for(service_level: float) -> float:
    """Safety factor: the standard-normal quantile of the cycle service level."""
    return float(norm.ppf(service_level))


def _standard_normal_loss(z: float) -> float:
    """Standard-normal loss function G(z) = φ(z) − z·(1 − Φ(z)).

    The expected shortfall of a unit-normal beyond ``z``; expected units short
    per replenishment cycle is ``σ_L · G(z)``. G(0) = φ(0) ≈ 0.3989, and G is
    strictly decreasing in z (a higher reorder point leaks less demand).
    """
    return float(norm.pdf(z) - z * norm.sf(z))


def _sku_policy(
    *,
    demand_mean: float,
    demand_std: float,
    lead_time_days: float,
    cost: float,
    z: float,
    holding_rate: float,
    order_cost: float,
) -> dict:
    """Continuous-review (ROP, EOQ) policy for one SKU. Pure arithmetic.

    ``demand_mean`` / ``demand_std`` are per *month*; ``lead_time_days`` is the
    supplier replenishment lead time. Returns unit-level quantities and their
    EUR / cost consequences. Degenerate inputs (no demand, no cost) collapse to
    zero rather than dividing by zero.
    """
    lead_months = lead_time_days / DAYS_PER_MONTH
    annual_demand = demand_mean * 12.0

    # Lead-time demand: mean scales with time, std with its square root
    # (serially independent demand — the standard safety-stock assumption).
    mean_ltd = demand_mean * lead_months
    sigma_ltd = demand_std * math.sqrt(lead_months) if lead_months > 0 else 0.0

    safety_stock = max(0.0, z * sigma_ltd)
    reorder_point = mean_ltd + safety_stock

    holding_per_unit = holding_rate * cost  # H = annual holding cost / unit
    if annual_demand > 0 and holding_per_unit > 0:
        eoq = math.sqrt(2.0 * annual_demand * order_cost / holding_per_unit)
    else:
        eoq = 0.0

    cycle_stock = eoq / 2.0
    avg_inventory = cycle_stock + safety_stock
    order_up_to_level = reorder_point + eoq  # the (s, S) ceiling this implies

    orders_per_year = annual_demand / eoq if eoq > 0 else 0.0
    days_between_orders = DAYS_PER_YEAR / orders_per_year if orders_per_year > 0 else 0.0

    working_capital = avg_inventory * cost
    safety_stock_value = safety_stock * cost
    cycle_stock_value = cycle_stock * cost
    annual_holding_cost = holding_per_unit * avg_inventory
    annual_ordering_cost = orders_per_year * order_cost

    # Fill rate = fraction of demand met from stock = 1 − (expected units short
    # per cycle) / (units per cycle). With no demand nothing can be short.
    expected_short_per_cycle = sigma_ltd * _standard_normal_loss(z)
    fill_rate = 1.0 - expected_short_per_cycle / eoq if eoq > 0 else 1.0
    fill_rate = min(1.0, max(0.0, fill_rate))

    days_of_cover = DAYS_PER_YEAR * avg_inventory / annual_demand if annual_demand > 0 else 0.0

    return {
        "annual_demand": round(annual_demand, 1),
        "lead_time_days": round(float(lead_time_days), 1),
        "mean_ltd": round(mean_ltd, 1),
        "sigma_ltd": round(sigma_ltd, 2),
        "safety_stock": round(safety_stock, 1),
        "reorder_point": round(reorder_point, 1),
        "eoq": round(eoq, 1),
        "order_up_to_level": round(order_up_to_level, 1),
        "cycle_stock": round(cycle_stock, 1),
        "avg_inventory": round(avg_inventory, 1),
        "orders_per_year": round(orders_per_year, 2),
        "days_between_orders": round(days_between_orders, 1),
        "working_capital_eur": round(working_capital, 2),
        "safety_stock_eur": round(safety_stock_value, 2),
        "cycle_stock_eur": round(cycle_stock_value, 2),
        "annual_holding_cost_eur": round(annual_holding_cost, 2),
        "annual_ordering_cost_eur": round(annual_ordering_cost, 2),
        "fill_rate": round(fill_rate, 4),
        "days_of_cover": round(days_of_cover, 1),
        # unrounded working capital, so the portfolio total sums without
        # accumulating 200 rounding errors (the published per-SKU value is rounded)
        "_working_capital_exact": working_capital,
        "_safety_value_exact": safety_stock_value,
        "_cycle_value_exact": cycle_stock_value,
        "_holding_exact": annual_holding_cost,
        "_ordering_exact": annual_ordering_cost,
        "_annual_cogs_exact": annual_demand * cost,
    }


def inventory_policy(
    ds: Dataset | None = None,
    *,
    service_level: float | None = None,
    holding_rate: float = HOLDING_COST_RATE,
    order_cost: float = ORDER_COST_EUR,
    abc_xyz_result: dict | None = None,
    source: dict | None = None,
) -> dict:
    """Per-SKU replenishment policy plus portfolio inventory economics.

    ``service_level`` ``None`` (default) reads each SKU's cycle service target
    from the ABC-XYZ policy matrix; a float in ``(0, 1)`` overrides every SKU
    with that flat target (used for what-ifs and the base-case collapse). The
    ABC-XYZ classification is reused from :func:`dip.analytics.abc_xyz` (passed
    in by the Flask cache, else computed here). Deterministic.
    """
    ds = ds or build_dataset()
    override = _validate_service_level(service_level) if service_level is not None else None

    abcxyz = abc_xyz_result if abc_xyz_result is not None else abc_xyz(ds)
    cell_by_sku = {row["sku_id"]: row["cell"] for row in abcxyz["per_sku"]}

    # cache z per distinct service level so norm.ppf runs a handful of times
    z_cache: dict[float, float] = {}

    def _z(sl: float) -> float:
        if sl not in z_cache:
            z_cache[sl] = _z_for(sl)
        return z_cache[sl]

    skus_out: list[dict] = []
    # per-cell aggregation (all nine ABC-XYZ cells reported, even if empty)
    cells: dict[str, dict] = {
        cell: {"count": 0, "wc": 0.0, "ss": 0.0, "sl_sum": 0.0, "fill_w": 0.0, "dem": 0.0}
        for cell in SERVICE_LEVEL_MATRIX
    }

    tot_wc = tot_ss = tot_cs = tot_hold = tot_order = tot_cogs = 0.0
    dem_sum = fill_w_sum = sl_w_sum = 0.0

    for s in ds.skus:
        cell = cell_by_sku.get(s["sku_id"], "CZ")  # unknown SKU -> most permissive tier
        sl = override if override is not None else SERVICE_LEVEL_MATRIX[cell]
        z = _z(sl)
        pol = _sku_policy(
            demand_mean=s["demand_mean"],
            demand_std=s["demand_std"],
            lead_time_days=float(s["lead_time_days"]),
            cost=s["cost"],
            z=z,
            holding_rate=holding_rate,
            order_cost=order_cost,
        )
        row = {
            "sku_id": s["sku_id"],
            "name": s["name"],
            "category": s["category"],
            "abc": cell[0],
            "xyz": cell[1],
            "cell": cell,
            "service_level": round(sl, 4),
            "z": round(z, 4),
            **{k: v for k, v in pol.items() if not k.startswith("_")},
        }
        skus_out.append(row)

        wc = pol["_working_capital_exact"]
        dem = pol["annual_demand"]
        tot_wc += wc
        tot_ss += pol["_safety_value_exact"]
        tot_cs += pol["_cycle_value_exact"]
        tot_hold += pol["_holding_exact"]
        tot_order += pol["_ordering_exact"]
        tot_cogs += pol["_annual_cogs_exact"]
        dem_sum += dem
        fill_w_sum += row["fill_rate"] * dem
        sl_w_sum += sl * dem

        c = cells[cell]
        c["count"] += 1
        c["wc"] += wc
        c["ss"] += pol["_safety_value_exact"]
        c["sl_sum"] += sl
        c["fill_w"] += row["fill_rate"] * dem
        c["dem"] += dem

    skus_out.sort(key=lambda r: r["working_capital_eur"], reverse=True)

    by_cell = []
    for cell, c in cells.items():
        n = c["count"]
        by_cell.append(
            {
                "cell": cell,
                "abc": cell[0],
                "xyz": cell[1],
                "count": n,
                "target_service_level": round(SERVICE_LEVEL_MATRIX[cell], 4),
                "working_capital_eur": round(c["wc"], 2),
                "safety_stock_eur": round(c["ss"], 2),
                "avg_service_level": round(c["sl_sum"] / n, 4) if n else None,
                "avg_fill_rate": round(c["fill_w"] / c["dem"], 4) if c["dem"] else None,
            }
        )
    by_cell.sort(key=lambda r: r["working_capital_eur"], reverse=True)

    inventory_turns = tot_cogs / tot_wc if tot_wc > 0 else 0.0
    totals = {
        "n_skus": len(skus_out),
        "working_capital_eur": round(tot_wc, 2),
        "cycle_stock_eur": round(tot_cs, 2),
        "safety_stock_eur": round(tot_ss, 2),
        "annual_holding_cost_eur": round(tot_hold, 2),
        "annual_ordering_cost_eur": round(tot_order, 2),
        "annual_inventory_cost_eur": round(tot_hold + tot_order, 2),
        "annual_cogs_eur": round(tot_cogs, 2),
        "inventory_turns": round(inventory_turns, 2),
        "days_of_cover": round(DAYS_PER_YEAR / inventory_turns, 1) if inventory_turns > 0 else 0.0,
        "demand_weighted_fill_rate": round(fill_w_sum / dem_sum, 4) if dem_sum else None,
        "demand_weighted_service_level": round(sl_w_sum / dem_sum, 4) if dem_sum else None,
    }

    source = source or {"synthetic": True, "label": "seeded synthetic dataset", "filename": None}
    if source.get("synthetic", True):
        provenance = (
            "Seeded synthetic dataset — deterministic. Per-SKU demand mean/variability, "
            "lead time and cost drive a continuous-review (ROP, EOQ) policy; service "
            "targets read from the ABC-XYZ matrix."
        )
    else:
        provenance = (
            f"Imported workbook '{source.get('filename')}' ({source.get('n_skus')} SKUs) — "
            "lead time is the import default (10 days) and demand variability is the seeded "
            "per-SKU process, so the policy is directional, not a purchasing instruction."
        )

    mode = "flat" if override is not None else "abc_xyz_matrix"
    return {
        "params": {
            "holding_rate": round(holding_rate, 4),
            "order_cost_eur": round(order_cost, 2),
            "days_per_year": DAYS_PER_YEAR,
            "days_per_month": DAYS_PER_MONTH,
            "service_level_override": round(override, 4) if override is not None else None,
        },
        "service_policy": {
            "mode": mode,
            "matrix": dict(SERVICE_LEVEL_MATRIX) if mode == "abc_xyz_matrix" else None,
            "flat_level": round(override, 4) if override is not None else None,
        },
        "totals": totals,
        "by_cell": by_cell,
        "skus": skus_out,
        "provenance": provenance,
        "data_source": source,
        "note": (
            "Continuous-review (reorder point, order quantity) policy. Safety stock "
            "SS = z·σ_L buffers demand variability over the supplier lead time; the reorder "
            "point is mean lead-time demand + SS; the order quantity is the EOQ (Wilson) lot "
            "that trades ordering cost against holding cost. Service targets come from the "
            "ABC-XYZ matrix, so the classification drives the policy. Working capital is the "
            "average-inventory value; inventory turns = annual COGS / working capital."
        ),
        "caveats": [
            (
                "Demand is modelled as normal and serially independent (σ scales with √lead-"
                "time). Real demand — especially the erratic Z lines — is lumpier, so a "
                "production system would fit a per-SKU distribution and forecast residuals."
            ),
            (
                f"Holding rate ({holding_rate:.0%}/yr) and order cost (EUR {order_cost:,.0f}) are "
                "stated planning assumptions, not figures observed from this synthetic world."
            ),
            (
                "The ABC-XYZ service matrix is a policy choice informed by standard segmentation "
                "practice, not a certification of the right service level for any real business."
            ),
            (
                "This is the rigorous working-capital figure the assortment MILP only proxies "
                "with a crude cycle-stock term; the two are different models and are not equal."
            ),
        ],
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    inv = inventory_policy()
    t = inv["totals"]
    print(
        f"Working capital EUR {t['working_capital_eur']:,.0f}  "
        f"(safety EUR {t['safety_stock_eur']:,.0f} + cycle EUR {t['cycle_stock_eur']:,.0f})"
    )
    print(
        f"Inventory turns {t['inventory_turns']}  days of cover {t['days_of_cover']}  "
        f"fill rate {t['demand_weighted_fill_rate']:.1%}"
    )
    print(f"Annual holding + ordering cost: EUR {t['annual_inventory_cost_eur']:,.0f}")
