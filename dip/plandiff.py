"""Plan diff: what changed between two plan runs, and why — to the euro.

The platform can already answer *"what is the plan?"*. The question a planner
actually asks on the second visit is **"what changed since last time, and
why?"** — and a KPI-level A/B (:mod:`dip.scenario`) does not answer it: knowing
the uplift moved by EUR 40k says nothing about *which SKUs* entered the range,
*which prices* moved, *which reorder points* shifted, or how those add up.

This engine diffs two **plan runs** and produces:

- the **assortment** set difference: SKUs added to / dropped from the MILP
  range (and from the greedy baseline the lever is measured against);
- the **price moves**: per-SKU recommended-price changes and the profit each
  one carries;
- the **inventory-policy** changes: safety stock, reorder point and EOQ per
  SKU, plus the working-capital consequence;
- the **routing** change: kilometres, vehicles and stop assignments;
- and the **EUR bridge** from run A's headline uplift to run B's, in which
  *every euro of the difference is attributed to a named cause*.

The reconciliation discipline, applied to change
-----------------------------------------------
:mod:`dip.reconcile` keeps the platform's *published levels* honest. This module
applies the same rule to *deltas*: the bridge is not a picture, it is an
identity. With ``P``/``A``/``R`` the pricing, assortment and routing levers::

    uplift(B) - uplift(A)
        = SUM over moved SKUs of  d(profit_delta)                  [pricing]
        + SUM margin(added to MILP)   - SUM margin(dropped from MILP)
        - SUM margin(added to greedy) + SUM margin(dropped from greedy)
        + (routing lever B - routing lever A)                      [routing]
        + publication rounding                                     [named]

Every line is computed from the engines' own published fields; nothing is
re-derived. The final line is *not* a plug: it is the sum of four residuals,
each computed independently from a single run's own published numbers (the
difference between a run's published total and the sum of its own published
rows). Because each residual is defined without reference to the other run, the
bridge closing is a real check — mis-attribute one SKU and it stops closing.
:func:`plan_diff` reports the identities it checked, their two sides, and the
verdict, exactly like the reconciliation ledger does.

What this does **not** claim
----------------------------
The inventory policy is diffed and reported in working-capital EUR, but it is
**not** part of the uplift bridge: the plan's expected uplift is composed from
pricing, assortment and routing only, and inventing an inventory term for it
would be exactly the silent drift the rest of the platform guards against. The
two bridges are kept separate and labelled.

Determinism
-----------
Fixed seed, MILP/CVRP solution budgets (never a wall clock), no RNG and no
timestamp in the output — two runs of the same diff are byte-identical. Runs
that share a fleet share one routing solve, so their routing delta is exactly
zero rather than solver noise.

Everything is computed on the seeded synthetic dataset (or a user's imported
workbook); the figures are illustrative of the method, not a real-world claim.
"""

from __future__ import annotations

import math

from .analytics import abc_xyz
from .data import Dataset, build_dataset
from .inventory import (
    HOLDING_COST_RATE,
    ORDER_COST_EUR,
    InventoryError,
    _validate_service_level,
    inventory_policy,
)
from .optimize import (
    _sku_economics,
    optimize_assortment,
    optimize_prices,
    optimize_routes,
)
from .prescribe import COST_PER_KM, ROUTING_RUNS_PER_YEAR, build_plan
from .scenario import DEFAULT_MAX_CHANGE, ScenarioError, normalize_scenario

# ---- fleet defaults: the signature `optimize_routes` is called with elsewhere
# in the platform, so a run that leaves them alone reuses the app's one cached
# routing solve instead of paying for a second CVRP search.
DEFAULT_N_VEHICLES = 6
DEFAULT_CAPACITY_KG = 2200.0

_N_VEHICLES_LO, _N_VEHICLES_HI = 1, 20
_CAPACITY_LO, _CAPACITY_HI = 100.0, 100_000.0
_HOLDING_LO, _HOLDING_HI = 1e-6, 5.0
_ORDER_COST_LO, _ORDER_COST_HI = 0.0, 1e6

# Half of the cent every published figure is rounded to — the per-value bound on
# publication rounding, used to prove the bridge's rounding line is only that.
_HALF_CENT = 0.005

# The default pair the station and the un-parameterised endpoint show: the
# approved plan against a replan that moves every knob the platform owns —
# tighter working capital, a tighter price guardrail, a flat 95% service target,
# a dearer purchase order and smaller vans — so every section of the diff has
# something real to say and the bridge has all three levers moving.
DEFAULT_RUN_A: dict = {
    "name": "Baseline plan",
    "budget": None,  # the optimiser's own 40%-of-capital default
    "max_change": DEFAULT_MAX_CHANGE,
    "service_level": None,  # per-SKU targets from the ABC-XYZ matrix
    "holding_rate": HOLDING_COST_RATE,
    "order_cost_eur": ORDER_COST_EUR,
    "n_vehicles": DEFAULT_N_VEHICLES,
    "capacity_kg": DEFAULT_CAPACITY_KG,
}
# Budget is expressed as a share of the range's full working capital so the
# default survives any dataset (including an imported one).
DEFAULT_RUN_B_BUDGET_SHARE = 0.30
DEFAULT_RUN_B: dict = {
    "name": "Revised plan",
    "max_change": 0.10,
    "service_level": 0.95,
    "holding_rate": HOLDING_COST_RATE,
    "order_cost_eur": 65.0,
    "n_vehicles": DEFAULT_N_VEHICLES,
    "capacity_kg": 2000.0,
}


class PlanDiffError(ValueError):
    """Invalid plan-diff request — mapped to HTTP 400 by the API layer."""


# ---- run specs --------------------------------------------------------------


def _number(raw: dict, key: str, default: float, lo: float, hi: float, label: str) -> float:
    """Validate one numeric run parameter into ``[lo, hi]`` (or raise)."""
    value = raw.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanDiffError(f"run '{label}' {key} must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or not (lo <= value <= hi):
        raise PlanDiffError(f"run '{label}' {key} must be a finite number in [{lo}, {hi}], got {value!r}")
    return value


def normalize_run(raw: dict | None, *, label: str, defaults: dict | None = None) -> dict:
    """Validate and normalise one run spec.

    A run is the full set of knobs the platform exposes to a planner:
    ``budget`` and ``max_change`` (the plan levers, validated by
    :func:`dip.scenario.normalize_scenario` so the two features can never
    disagree on what a valid scenario is), ``service_level`` /``holding_rate``
    /``order_cost_eur`` (the replenishment policy) and ``n_vehicles``
    /``capacity_kg`` (the fleet the CVRP is solved for).

    Raises :class:`PlanDiffError` with a human-readable message on bad input.
    """
    if raw is not None and not isinstance(raw, dict):
        raise PlanDiffError(f"run '{label}' must be an object")
    defaults = dict(defaults or DEFAULT_RUN_A)
    merged = {**defaults, **(raw or {})}

    try:
        base = normalize_scenario(
            {"name": merged.get("name"), "budget": merged.get("budget"),
             "max_change": merged.get("max_change")},
            label=label,
        )
    except ScenarioError as exc:  # one vocabulary for "bad scenario", two features
        raise PlanDiffError(str(exc)) from exc

    service_level = merged.get("service_level")
    if service_level is not None:
        try:
            service_level = _validate_service_level(service_level)
        except InventoryError as exc:
            raise PlanDiffError(f"run '{label}' {exc}") from exc

    n_vehicles = merged.get("n_vehicles", DEFAULT_N_VEHICLES)
    if n_vehicles is None:
        n_vehicles = DEFAULT_N_VEHICLES
    if isinstance(n_vehicles, bool) or not isinstance(n_vehicles, int):
        raise PlanDiffError(f"run '{label}' n_vehicles must be an integer, got {n_vehicles!r}")
    if not (_N_VEHICLES_LO <= n_vehicles <= _N_VEHICLES_HI):
        raise PlanDiffError(
            f"run '{label}' n_vehicles must be in [{_N_VEHICLES_LO}, {_N_VEHICLES_HI}], got {n_vehicles!r}"
        )

    return {
        **base,
        "service_level": service_level,
        "holding_rate": _number(merged, "holding_rate", HOLDING_COST_RATE, _HOLDING_LO, _HOLDING_HI, label),
        "order_cost_eur": _number(merged, "order_cost_eur", ORDER_COST_EUR, _ORDER_COST_LO, _ORDER_COST_HI, label),
        "n_vehicles": int(n_vehicles),
        "capacity_kg": _number(merged, "capacity_kg", DEFAULT_CAPACITY_KG, _CAPACITY_LO, _CAPACITY_HI, label),
    }


def _check_fleet_feasible(ds: Dataset, spec: dict) -> None:
    """Reject a fleet that cannot serve the run before the CVRP is launched.

    An under-sized fleet does not fail fast: the guided-local-search phase has a
    solution budget, not a time limit, so an infeasible instance leaves the
    solver hunting for a first solution that does not exist. Two cheap
    necessary conditions — every stop must fit in one vehicle, and the fleet
    must be able to carry the whole run — turn that hang into an honest 400.
    They are necessary, not sufficient: a fleet that passes can still be tight.
    """
    # the CVRP rounds each stop's kilos up to an integer, so check what it loads
    demands = [float(math.ceil(s["demand_kg"])) for s in ds.route_stops]
    if not demands:
        return
    capacity, n_vehicles = spec["capacity_kg"], spec["n_vehicles"]
    heaviest = max(demands)
    if heaviest > capacity:
        raise PlanDiffError(
            f"run '{spec['name']}' capacity_kg {capacity:,.0f} cannot serve its heaviest stop "
            f"({heaviest:,.0f} kg) — no routing solution exists"
        )
    total = sum(demands)
    if total > n_vehicles * capacity:
        raise PlanDiffError(
            f"run '{spec['name']}' fleet ({n_vehicles} x {capacity:,.0f} kg = "
            f"{n_vehicles * capacity:,.0f} kg) cannot carry the run's {total:,.0f} kg — "
            "no routing solution exists"
        )


def _fleet_key(spec: dict) -> tuple[int, float]:
    return (spec["n_vehicles"], spec["capacity_kg"])


def _same_fleet(a: dict, b: dict) -> bool:
    return _fleet_key(a) == _fleet_key(b)


# ---- one run ----------------------------------------------------------------


def _run_engines(ds: Dataset, spec: dict, *, routes: dict, abcxyz: dict) -> dict:
    """Run the engine stack for one spec and keep every published object.

    The plan is composed by :func:`dip.prescribe.build_plan` from the *same*
    pricing and routing objects held here, so the levers the bridge attributes
    are literally the engines' published numbers — asserted below rather than
    assumed.
    """
    prices = optimize_prices(ds, max_change=spec["max_change"])
    assort = optimize_assortment(ds, budget=spec["budget"])
    plan = build_plan(
        ds, budget=spec["budget"], max_change=spec["max_change"], routes=routes, prices=prices
    )
    inv = inventory_policy(
        ds,
        service_level=spec["service_level"],
        holding_rate=spec["holding_rate"],
        order_cost=spec["order_cost_eur"],
        abc_xyz_result=abcxyz,
    )
    # The plan's levers ARE these engines' published figures (same invariant the
    # scenario compare asserts) — the bridge below is meaningless without it.
    assert plan["levers"]["pricing"] == prices["uplift"]
    assert plan["levers"]["assortment"] == assort["uplift_vs_greedy"]
    return {"spec": spec, "prices": prices, "assort": assort, "plan": plan, "inv": inv, "routes": routes}


def _run_summary(run: dict) -> dict:
    """The comparable headline of one run, coerced to plain Python numbers."""
    plan, assort, inv, routes = run["plan"], run["assort"], run["inv"], run["routes"]
    spec = run["spec"]
    return {
        "name": spec["name"],
        "spec": {
            "budget_eur": float(plan["budget"]),
            "budget_pct_of_full": (
                round(plan["budget"] / plan["full_capital"], 4) if plan["full_capital"] else 0.0
            ),
            "max_change": float(spec["max_change"]),
            "service_level": spec["service_level"],
            "service_mode": inv["service_policy"]["mode"],
            "holding_rate": float(spec["holding_rate"]),
            "order_cost_eur": float(spec["order_cost_eur"]),
            "n_vehicles": int(spec["n_vehicles"]),
            "capacity_kg": float(spec["capacity_kg"]),
        },
        "kpis": {
            "expected_uplift_eur": float(plan["expected_uplift_eur"]),
            "expected_uplift_pct": float(plan["expected_uplift_pct"]),
            "pricing_uplift_eur": float(plan["levers"]["pricing"]),
            "assortment_uplift_eur": float(plan["levers"]["assortment"]),
            "routing_uplift_eur": float(plan["levers"]["routing"]),
            "margin_captured_eur": float(assort["milp"]["margin"]),
            "capital_used_eur": float(assort["milp"]["capital_used"]),
            "skus_carried": int(assort["milp"]["count"]),
            "working_capital_eur": float(inv["totals"]["working_capital_eur"]),
            "safety_stock_eur": float(inv["totals"]["safety_stock_eur"]),
            "annual_inventory_cost_eur": float(inv["totals"]["annual_inventory_cost_eur"]),
            "fill_rate": float(inv["totals"]["demand_weighted_fill_rate"] or 0.0),
            "optimized_km": float(routes["optimized_km"]),
            "km_saved": float(routes["km_saved"]),
            "vehicles_used": int(routes["n_vehicles_used"]),
        },
    }


# ---- section diffs ----------------------------------------------------------


def _assortment_diff(a: dict, b: dict, eco: dict, meta: dict) -> dict:
    """SKU set difference for the MILP range and for the greedy baseline.

    Per-SKU annual margin and capital come from :func:`dip.optimize._sku_economics`
    — the *same* vectors the MILP optimised over, so the rows sum back to the
    published ``milp.margin`` exactly (to publication rounding). Deriving a
    second margin here would be re-deriving, and could disagree.

    The sums use each SKU's margin **as this diff publishes it** (to the cent),
    so the figures in the table add up to the figure in the bridge; the gap to
    the optimiser's own total is carried by the rounding line, measured per run.
    """
    margin_by = eco["margin_pub"]
    capital_by = eco["capital_by"]

    def rows(ids: list[str]) -> list[dict]:
        out = [
            {
                "sku_id": sku_id,
                "name": meta[sku_id]["name"],
                "category": meta[sku_id]["category"],
                "annual_margin_eur": margin_by[sku_id],
                "capital_eur": round(capital_by[sku_id], 2),
            }
            for sku_id in ids
        ]
        out.sort(key=lambda r: (-r["annual_margin_eur"], r["sku_id"]))
        return out

    def diff(key: str) -> tuple[list[str], list[str]]:
        set_a = set(a["assort"][key])
        set_b = set(b["assort"][key])
        return sorted(set_b - set_a), sorted(set_a - set_b)

    milp_added, milp_dropped = diff("carried_skus")
    greedy_added, greedy_dropped = diff("greedy_carried_skus")

    def total(ids: list[str]) -> float:
        return round(sum(margin_by[i] for i in ids), 2)

    return {
        "added": rows(milp_added),
        "dropped": rows(milp_dropped),
        "greedy_added": rows(greedy_added),
        "greedy_dropped": rows(greedy_dropped),
        "_sums": {
            "milp_added": total(milp_added),
            "milp_dropped": total(milp_dropped),
            "greedy_added": total(greedy_added),
            "greedy_dropped": total(greedy_dropped),
        },
        "totals": {
            "skus_carried_a": int(a["assort"]["milp"]["count"]),
            "skus_carried_b": int(b["assort"]["milp"]["count"]),
            "n_added": len(milp_added),
            "n_dropped": len(milp_dropped),
            "n_greedy_added": len(greedy_added),
            "n_greedy_dropped": len(greedy_dropped),
            "added_margin_eur": float(total(milp_added)),
            "dropped_margin_eur": float(total(milp_dropped)),
            "margin_captured_delta_eur": round(
                b["assort"]["milp"]["margin"] - a["assort"]["milp"]["margin"], 2
            ),
            "capital_used_delta_eur": round(
                b["assort"]["milp"]["capital_used"] - a["assort"]["milp"]["capital_used"], 2
            ),
            "budget_delta_eur": round(b["assort"]["budget"] - a["assort"]["budget"], 2),
        },
    }


def _pricing_diff(a: dict, b: dict) -> dict:
    """Per-SKU recommended-price moves and the profit each one carries."""
    rows_a = {r["sku_id"]: r for r in a["prices"]["recommendations"]}
    rows_b = {r["sku_id"]: r for r in b["prices"]["recommendations"]}

    moves: list[dict] = []
    attributed = 0.0
    n_up = n_down = 0
    for sku_id in sorted(rows_a):
        ra, rb = rows_a[sku_id], rows_b[sku_id]
        delta = round(rb["profit_delta"] - ra["profit_delta"], 2)
        if rb["price_new"] == ra["price_new"] and delta == 0.0:
            continue  # this SKU's recommendation did not move: it carries no euros
        attributed += delta
        price_delta = round(rb["price_new"] - ra["price_new"], 2)
        if price_delta > 0:
            n_up += 1
        elif price_delta < 0:
            n_down += 1
        moves.append(
            {
                "sku_id": sku_id,
                "category": ra["category"],
                "elasticity": ra["elasticity"],
                "price_old_eur": ra["price_old"],
                "price_a_eur": ra["price_new"],
                "price_b_eur": rb["price_new"],
                "price_delta_eur": price_delta,
                "change_pct_a": ra["change_pct"],
                "change_pct_b": rb["change_pct"],
                "profit_delta_a_eur": ra["profit_delta"],
                "profit_delta_b_eur": rb["profit_delta"],
                "delta_eur": delta,
            }
        )
    moves.sort(key=lambda r: (-abs(r["delta_eur"]), r["sku_id"]))

    return {
        "moves": moves,
        "_sums": {"moved_profit_delta": attributed},
        "totals": {
            "n_skus": len(rows_a),
            "n_moved": len(moves),
            "n_priced_up": n_up,
            "n_priced_down": n_down,
            "guardrail_a": float(a["prices"]["max_change"]),
            "guardrail_b": float(b["prices"]["max_change"]),
            "pricing_uplift_delta_eur": round(b["prices"]["uplift"] - a["prices"]["uplift"], 2),
            "moved_profit_delta_eur": round(attributed, 2),
        },
    }


def _inventory_diff(a: dict, b: dict) -> dict:
    """Per-SKU replenishment-policy changes: safety stock, ROP, EOQ, capital."""
    rows_a = {r["sku_id"]: r for r in a["inv"]["skus"]}
    rows_b = {r["sku_id"]: r for r in b["inv"]["skus"]}
    fields = ("service_level", "safety_stock", "reorder_point", "eoq", "working_capital_eur")

    changes: list[dict] = []
    attributed = 0.0
    n_rop_up = n_rop_down = 0
    for sku_id in sorted(rows_a):
        ra, rb = rows_a[sku_id], rows_b[sku_id]
        if all(ra[f] == rb[f] for f in fields):
            continue  # policy identical for this SKU: no capital moves
        wc_delta = round(rb["working_capital_eur"] - ra["working_capital_eur"], 2)
        attributed += wc_delta
        rop_delta = round(rb["reorder_point"] - ra["reorder_point"], 1)
        if rop_delta > 0:
            n_rop_up += 1
        elif rop_delta < 0:
            n_rop_down += 1
        changes.append(
            {
                "sku_id": sku_id,
                "name": ra["name"],
                "cell": ra["cell"],
                "service_level_a": ra["service_level"],
                "service_level_b": rb["service_level"],
                "safety_stock_a": ra["safety_stock"],
                "safety_stock_b": rb["safety_stock"],
                "safety_stock_delta": round(rb["safety_stock"] - ra["safety_stock"], 1),
                "reorder_point_a": ra["reorder_point"],
                "reorder_point_b": rb["reorder_point"],
                "reorder_point_delta": rop_delta,
                "eoq_a": ra["eoq"],
                "eoq_b": rb["eoq"],
                "eoq_delta": round(rb["eoq"] - ra["eoq"], 1),
                "working_capital_a_eur": ra["working_capital_eur"],
                "working_capital_b_eur": rb["working_capital_eur"],
                "working_capital_delta_eur": wc_delta,
            }
        )
    changes.sort(key=lambda r: (-abs(r["working_capital_delta_eur"]), r["sku_id"]))

    ta, tb = a["inv"]["totals"], b["inv"]["totals"]
    return {
        "changes": changes,
        "_sums": {"changed_working_capital": attributed},
        "totals": {
            "n_skus": len(rows_a),
            "n_changed": len(changes),
            "n_reorder_point_up": n_rop_up,
            "n_reorder_point_down": n_rop_down,
            "service_mode_a": a["inv"]["service_policy"]["mode"],
            "service_mode_b": b["inv"]["service_policy"]["mode"],
            "working_capital_a_eur": float(ta["working_capital_eur"]),
            "working_capital_b_eur": float(tb["working_capital_eur"]),
            "working_capital_delta_eur": round(
                tb["working_capital_eur"] - ta["working_capital_eur"], 2
            ),
            "safety_stock_delta_eur": round(tb["safety_stock_eur"] - ta["safety_stock_eur"], 2),
            "cycle_stock_delta_eur": round(tb["cycle_stock_eur"] - ta["cycle_stock_eur"], 2),
            "annual_cost_delta_eur": round(
                tb["annual_inventory_cost_eur"] - ta["annual_inventory_cost_eur"], 2
            ),
            "turns_a": float(ta["inventory_turns"]),
            "turns_b": float(tb["inventory_turns"]),
            "fill_rate_a": ta["demand_weighted_fill_rate"],
            "fill_rate_b": tb["demand_weighted_fill_rate"],
            "changed_working_capital_eur": round(attributed, 2),
        },
    }


def _stop_rounds(routes: dict) -> dict[str, frozenset]:
    """Each stop -> the set of other stops sharing its delivery round.

    Deliberately *not* the vehicle index: that is a solver label with no meaning
    across two independent solves, so comparing indices would report almost
    every stop as "moved" even when the same customers travel together. Comparing
    the company a stop keeps is label-invariant and is what a depot manager
    actually means by a round changing.
    """
    out: dict[str, frozenset] = {}
    for route in routes["routes"]:
        ids = [s["id"] for s in route["stops"] if s["id"] != "DEPOT"]
        members = set(ids)
        for sku_stop in ids:
            out[sku_stop] = frozenset(members - {sku_stop})
    return out


def _routing_diff(a: dict, b: dict, *, shared: bool) -> dict:
    """Kilometres, vehicles and stop assignments between the two fleet solves."""
    ra, rb = a["routes"], b["routes"]
    rounds_a, rounds_b = _stop_rounds(ra), _stop_rounds(rb)
    regrouped = sorted(k for k in rounds_a if rounds_b.get(k) != rounds_a[k])

    def vehicles(routes: dict) -> list[dict]:
        return [
            {
                "vehicle": r["vehicle"],
                "stops": len(r["stops"]) - 2,  # the sequence opens and closes at the depot
                "distance_km": r["distance_km"],
                "load_kg": r["load_kg"],
            }
            for r in routes["routes"]
        ]

    return {
        "shared_solve": shared,
        "fleet_a": {"n_vehicles": a["spec"]["n_vehicles"], "capacity_kg": a["spec"]["capacity_kg"]},
        "fleet_b": {"n_vehicles": b["spec"]["n_vehicles"], "capacity_kg": b["spec"]["capacity_kg"]},
        "vehicles_a": vehicles(ra),
        "vehicles_b": vehicles(rb),
        "stops_regrouped": regrouped,
        "totals": {
            "n_stops": int(ra["n_stops"]),
            "n_stops_regrouped": len(regrouped),
            "optimized_km_a": float(ra["optimized_km"]),
            "optimized_km_b": float(rb["optimized_km"]),
            "optimized_km_delta": round(rb["optimized_km"] - ra["optimized_km"], 2),
            "baseline_km_a": float(ra["baseline_km"]),
            "baseline_km_b": float(rb["baseline_km"]),
            "baseline_km_delta": round(rb["baseline_km"] - ra["baseline_km"], 2),
            "km_saved_a": float(ra["km_saved"]),
            "km_saved_b": float(rb["km_saved"]),
            "km_saved_delta": round(rb["km_saved"] - ra["km_saved"], 2),
            "pct_saved_a": float(ra["pct_saved"]),
            "pct_saved_b": float(rb["pct_saved"]),
            "vehicles_used_a": int(ra["n_vehicles_used"]),
            "vehicles_used_b": int(rb["n_vehicles_used"]),
            "routing_uplift_delta_eur": round(
                b["plan"]["levers"]["routing"] - a["plan"]["levers"]["routing"], 2
            ),
        },
    }


# ---- publication-rounding residuals ----------------------------------------
# Each of these is a property of ONE run: the gap between a figure that run
# publishes and the sum of the rows that same run publishes underneath it. They
# are computed without reference to the other run, so using their difference as
# the bridge's rounding line is not a plug — it is a named, bounded cause.


def _residuals(run: dict, eco: dict) -> dict:
    margin_by = eco["margin_pub"]
    assort, prices, plan, inv = run["assort"], run["prices"], run["plan"], run["inv"]
    levers = plan["levers"]
    return {k: float(v) for k, v in {
        # published pricing uplift  vs  the sum of its own per-SKU rows
        "pricing_rows": prices["uplift"] - sum(r["profit_delta"] for r in prices["recommendations"]),
        # published MILP / greedy margin  vs  the summed economics of its own picks
        "assortment_milp": assort["milp"]["margin"] - sum(margin_by[i] for i in assort["carried_skus"]),
        "assortment_greedy": (
            assort["greedy"]["margin"] - sum(margin_by[i] for i in assort["greedy_carried_skus"])
        ),
        # published edge over greedy  vs  the difference of its own two published
        # margins: the engine rounds all three independently, so they can sit a
        # cent apart (the gap `dip.reconcile.assortment_lever_identity` bounds)
        "assortment_lever": (
            assort["uplift_vs_greedy"] - (assort["milp"]["margin"] - assort["greedy"]["margin"])
        ),
        # published routing lever  vs  its own km priced at the documented rate
        "routing_lever": (
            levers["routing"] - run["routes"]["km_saved"] * COST_PER_KM * ROUTING_RUNS_PER_YEAR
        ),
        # published km saved  vs  its own baseline and optimised kilometres
        "routing_km": (
            run["routes"]["km_saved"]
            - (run["routes"]["baseline_km"] - run["routes"]["optimized_km"])
        ),
        # published headline uplift  vs  the sum of its own three published levers
        "plan_composition": plan["expected_uplift_eur"]
        - (levers["pricing"] + levers["assortment"] + levers["routing"]),
        # published working capital  vs  the sum of its own per-SKU rows
        "inventory_rows": inv["totals"]["working_capital_eur"]
        - sum(r["working_capital_eur"] for r in inv["skus"]),
        # published working capital  vs  its own safety + cycle split
        "inventory_split": inv["totals"]["working_capital_eur"]
        - inv["totals"]["safety_stock_eur"]
        - inv["totals"]["cycle_stock_eur"],
    }.items()}


def _rounding_tolerance(a: dict, b: dict) -> tuple[float, int]:
    """The bound the bridge's rounding line must respect, and the values behind it.

    Every residual above is a difference of figures each rounded to the cent, so
    it is bounded by half a cent per rounded value it spans. Counting those
    values gives a hard tolerance: a rounding line inside it really is only
    rounding; one outside it means a cause is missing.
    """
    n = 0
    for run in (a, b):
        n += len(run["prices"]["recommendations"]) + 1  # rows + the published uplift
        n += len(run["assort"]["carried_skus"]) + 1
        n += len(run["assort"]["greedy_carried_skus"]) + 1
        n += 1  # the published edge over greedy, rounded apart from both margins
        n += 4  # the headline uplift + its three levers
    return _HALF_CENT * n, n


# ---- the bridge -------------------------------------------------------------


def _bridge(a: dict, b: dict, assortment: dict, pricing: dict, routing: dict, resid_delta: dict) -> dict:
    """Run A's uplift -> run B's uplift, every euro attributed to a named cause.

    ``resid_delta`` holds, per residual, run B's value minus run A's — each side
    of which was measured on one run alone (see :func:`_residuals`).
    """
    sums = assortment["_sums"]
    start = float(a["plan"]["expected_uplift_eur"])
    end = float(b["plan"]["expected_uplift_eur"])

    # Sub-cent by construction, so these are kept at four decimals: publishing a
    # EUR 0.004 line as "EUR 0.00" would hide the very thing it accounts for.
    rounding_parts = [
        {
            "id": "pricing_rows",
            "label": "per-SKU price rows",
            "eur": round(resid_delta["pricing_rows"], 4),
        },
        {
            "id": "assortment_milp",
            "label": "MILP range total",
            "eur": round(resid_delta["assortment_milp"], 4),
        },
        {
            "id": "assortment_greedy",
            "label": "greedy baseline total",
            "eur": round(-resid_delta["assortment_greedy"], 4),
        },
        {
            "id": "assortment_lever",
            "label": "MILP edge over greedy",
            "eur": round(resid_delta["assortment_lever"], 4),
        },
        {
            "id": "plan_composition",
            "label": "plan lever composition",
            "eur": round(resid_delta["plan_composition"], 4),
        },
    ]
    rounding = sum(p["eur"] for p in rounding_parts)

    steps = [
        {
            "id": "pricing_moves",
            "label": "Price moves",
            "lever": "Pricing",
            "eur": round(pricing["_sums"]["moved_profit_delta"], 2),
            "cause": (
                f"{pricing['totals']['n_moved']} SKU price recommendations moved when the "
                f"guardrail went from +/-{pricing['totals']['guardrail_a']:.0%} to "
                f"+/-{pricing['totals']['guardrail_b']:.0%}; each carries its own profit delta"
            ),
        },
        {
            "id": "assortment_added",
            "label": "SKUs added",
            "lever": "Assortment",
            "eur": round(sums["milp_added"], 2),
            "cause": (
                f"{assortment['totals']['n_added']} SKUs enter the MILP range at the new budget, "
                "bringing their annual margin with them"
            ),
        },
        {
            "id": "assortment_dropped",
            "label": "SKUs dropped",
            "lever": "Assortment",
            "eur": round(-sums["milp_dropped"], 2),
            "cause": (
                f"{assortment['totals']['n_dropped']} SKUs leave the MILP range, taking their "
                "annual margin out"
            ),
        },
        {
            "id": "greedy_baseline_shift",
            "label": "Greedy baseline",
            "lever": "Assortment",
            "eur": round(-sums["greedy_added"] + sums["greedy_dropped"], 2),
            "cause": (
                f"the assortment lever is the MILP's edge over the greedy baseline, and greedy "
                f"also re-picks at the new budget ({assortment['totals']['n_greedy_added']} in, "
                f"{assortment['totals']['n_greedy_dropped']} out) — its gain is subtracted"
            ),
        },
        {
            "id": "routing",
            "label": "Routing",
            "lever": "Logistics",
            "eur": routing["totals"]["routing_uplift_delta_eur"],
            "cause": (
                f"{routing['totals']['km_saved_delta']:+,.2f} km saved per run at "
                f"EUR {COST_PER_KM:.2f}/km over {ROUTING_RUNS_PER_YEAR} runs/yr"
                if not routing["shared_solve"]
                else "both runs use the same fleet, so one routing solve is shared and nothing moves"
            ),
        },
        {
            "id": "rounding",
            "label": "Rounding",
            "lever": "—",
            "eur": round(rounding, 4),
            "cause": (
                "publication rounding: every engine publishes to the cent, so a run's total and "
                "the sum of its own rows differ by fractions of a cent. Each part is measured on "
                "one run alone, never fitted to close the bridge"
            ),
        },
    ]
    attributed = sum(s["eur"] for s in steps)
    tolerance, n_values = _rounding_tolerance(a, b)
    return {
        "start": {"label": a["spec"]["name"], "eur": start},
        "end": {"label": b["spec"]["name"], "eur": end},
        "steps": steps,
        "delta_eur": round(end - start, 2),
        "attributed_eur": round(attributed, 2),
        "rounding_eur": round(rounding, 4),
        "rounding_parts": rounding_parts,
        "rounding_tolerance_eur": round(tolerance, 4),
        "rounding_values_spanned": n_values,
    }


# ---- identities -------------------------------------------------------------


def _identities(a: dict, b: dict, *, bridge: dict, assortment: dict, pricing: dict,
                inventory: dict, routing: dict, resid_a: dict, resid_b: dict) -> list[dict]:
    """The change-side ledger: what the diff claims, and whether it holds.

    Same discipline as :func:`dip.reconcile._identities` — both sides normalised
    to the precision the figures are published at, so ``ok`` means the diff is
    consistent to the cent, not merely close.
    """
    ids: list[dict] = []

    def add(id_: str, statement: str, lhs: float, rhs: float, dp: int, op: str = "==") -> None:
        left = round(float(lhs), dp)
        right = round(float(rhs), dp)
        ok = (left == right) if op == "==" else (abs(left) <= right)
        ids.append(
            {"id": id_, "statement": statement, "lhs": left, "rhs": right, "dp": dp, "op": op, "ok": ok}
        )

    lev_a, lev_b = a["plan"]["levers"], b["plan"]["levers"]
    d_uplift = b["plan"]["expected_uplift_eur"] - a["plan"]["expected_uplift_eur"]
    d_pricing = lev_b["pricing"] - lev_a["pricing"]
    d_assort = lev_b["assortment"] - lev_a["assortment"]
    d_routing = lev_b["routing"] - lev_a["routing"]
    sums = assortment["_sums"]

    add(
        "uplift_lever_bridge",
        "Change in expected annual uplift = change in the pricing + assortment + routing levers, "
        "plus each plan's own cent-level composition rounding",
        d_uplift,
        d_pricing + d_assort + d_routing + (resid_b["plan_composition"] - resid_a["plan_composition"]),
        2,
    )
    add(
        "publication_rounding_within_two_cents",
        "Every gap this ledger absorbs between two figures an engine publishes separately (the "
        "headline vs its levers, the MILP edge vs its two margins, the routing lever vs its km, "
        "the working capital vs its safety/cycle split) is at most two cents - rounding, not drift",
        max(
            abs(r[key])
            for r in (resid_a, resid_b)
            for key in ("plan_composition", "assortment_lever", "routing_lever",
                        "routing_km", "inventory_split")
        ),
        0.02,
        4,
        op="<=",
    )
    add(
        "pricing_attribution",
        "Change in the pricing lever = the moved SKUs' profit deltas (unmoved SKUs carry EUR 0)",
        d_pricing,
        pricing["_sums"]["moved_profit_delta"] + (resid_b["pricing_rows"] - resid_a["pricing_rows"]),
        2,
    )
    add(
        "assortment_attribution",
        "Change in the assortment lever = margin of SKUs added - dropped, less the greedy baseline's own re-pick",
        d_assort,
        (sums["milp_added"] - sums["milp_dropped"])
        - (sums["greedy_added"] - sums["greedy_dropped"])
        + (resid_b["assortment_milp"] - resid_a["assortment_milp"])
        - (resid_b["assortment_greedy"] - resid_a["assortment_greedy"])
        + (resid_b["assortment_lever"] - resid_a["assortment_lever"]),
        2,
    )
    add(
        "assortment_set_closure",
        "SKUs carried in B = SKUs carried in A + added - dropped",
        assortment["totals"]["skus_carried_b"],
        assortment["totals"]["skus_carried_a"]
        + assortment["totals"]["n_added"]
        - assortment["totals"]["n_dropped"],
        0,
    )
    add(
        "routing_attribution",
        f"Change in the routing lever = change in km saved x EUR {COST_PER_KM:.2f}/km x {ROUTING_RUNS_PER_YEAR} runs/yr",
        d_routing,
        routing["totals"]["km_saved_delta"] * COST_PER_KM * ROUTING_RUNS_PER_YEAR
        + (resid_b["routing_lever"] - resid_a["routing_lever"]),
        2,
    )
    add(
        # deliberately not `routing_km_identity`: that id belongs to the levels
        # ledger in dip.reconcile, and the two must never be mistaken for each
        # other when both verdicts are on screen
        "routing_km_bridge",
        "Change in km saved = change in baseline km - change in optimised km",
        routing["totals"]["km_saved_delta"],
        routing["totals"]["baseline_km_delta"]
        - routing["totals"]["optimized_km_delta"]
        + (resid_b["routing_km"] - resid_a["routing_km"]),
        2,
    )
    add(
        "bridge_closes",
        "The bridge's named causes sum to the whole change in expected annual uplift",
        bridge["attributed_eur"],
        bridge["delta_eur"],
        2,
    )
    add(
        "rounding_within_tolerance",
        f"The unattributed rounding line stays within half a cent per published value it spans ({bridge['rounding_values_spanned']} values)",
        bridge["rounding_eur"],
        bridge["rounding_tolerance_eur"],
        4,
        op="<=",
    )
    add(
        "inventory_sku_attribution",
        "Change in inventory working capital = the changed SKUs' working-capital deltas",
        inventory["totals"]["working_capital_delta_eur"],
        inventory["_sums"]["changed_working_capital"]
        + (resid_b["inventory_rows"] - resid_a["inventory_rows"]),
        2,
    )
    add(
        "inventory_split_bridge",
        "Change in working capital = change in safety stock + change in cycle stock",
        inventory["totals"]["working_capital_delta_eur"],
        inventory["totals"]["safety_stock_delta_eur"]
        + inventory["totals"]["cycle_stock_delta_eur"]
        + (resid_b["inventory_split"] - resid_a["inventory_split"]),
        2,
    )
    return ids


# ---- entry point ------------------------------------------------------------


def plan_diff(
    ds: Dataset | None = None,
    *,
    run_a: dict | None = None,
    run_b: dict | None = None,
    source: dict | None = None,
    routes: dict | None = None,
    routes_cache: dict | None = None,
    abc_xyz_result: dict | None = None,
) -> dict:
    """Diff two plan runs and return the change ledger + the reconciling bridge.

    ``run_a`` / ``run_b`` are run specs (see :func:`normalize_run`); omitted
    fields fall back to :data:`DEFAULT_RUN_A` / :data:`DEFAULT_RUN_B`, the pair
    the dashboard's Change station shows. ``routes`` lets a caller that already
    holds the default-fleet CVRP solve (the Flask cache does) hand it in instead
    of paying for it again; a run on any other fleet is solved here, once, and
    shared if both runs ask for the same one. ``routes_cache`` is a caller-owned
    ``{(n_vehicles, capacity_kg): solve}`` dict that survives between calls, so a
    dashboard exploring fleets pays for each distinct one only the first time.

    Deterministic and additive: no engine is re-derived, and nothing about the
    published plan changes.
    """
    ds = ds or build_dataset()
    spec_a = normalize_run(run_a, label="A", defaults=DEFAULT_RUN_A)

    eco_raw = _sku_economics(ds)
    eco = {
        # the optimiser's own per-SKU vectors, plus the to-the-cent form this
        # diff publishes in its tables and sums over in its bridge
        "margin_by": {i: float(m) for i, m in zip(eco_raw["ids"], eco_raw["margin"])},
        "margin_pub": {i: round(float(m), 2) for i, m in zip(eco_raw["ids"], eco_raw["margin"])},
        "capital_by": {i: round(float(c), 2) for i, c in zip(eco_raw["ids"], eco_raw["capital"])},
    }
    meta = {s["sku_id"]: s for s in ds.skus}

    # Run B falls back to the *shipped replan* only when it is not asked for at
    # all (the dashboard's opening view). A caller who names run B gets the
    # baseline as its floor instead, so a request that sets one knob diffs that
    # knob alone rather than silently inheriting four other changes.
    if run_b is None:
        defaults_b = dict(DEFAULT_RUN_B)
        defaults_b.setdefault(
            "budget", round(DEFAULT_RUN_B_BUDGET_SHARE * float(eco_raw["capital"].sum()), 2)
        )
    else:
        defaults_b = {**DEFAULT_RUN_A, "name": "Run B"}
    spec_b = normalize_run(run_b, label="B", defaults=defaults_b)

    # One CVRP solve per distinct fleet; identical fleets share the object, so a
    # "routing unchanged" verdict is exact rather than solver luck.
    solved: dict[tuple[int, float], dict] = routes_cache if routes_cache is not None else {}
    if routes is not None:
        solved[(DEFAULT_N_VEHICLES, DEFAULT_CAPACITY_KG)] = routes

    def routes_for(spec: dict) -> dict:
        key = _fleet_key(spec)
        if key not in solved:
            _check_fleet_feasible(ds, spec)
            solved[key] = optimize_routes(ds, n_vehicles=key[0], capacity_kg=key[1])
        return solved[key]

    abcxyz = abc_xyz_result if abc_xyz_result is not None else abc_xyz(ds)
    a = _run_engines(ds, spec_a, routes=routes_for(spec_a), abcxyz=abcxyz)
    b = _run_engines(ds, spec_b, routes=routes_for(spec_b), abcxyz=abcxyz)

    assortment = _assortment_diff(a, b, eco, meta)
    pricing = _pricing_diff(a, b)
    inventory = _inventory_diff(a, b)
    routing = _routing_diff(a, b, shared=_same_fleet(spec_a, spec_b))
    resid_a, resid_b = _residuals(a, eco), _residuals(b, eco)
    bridge = _bridge(a, b, assortment, pricing, routing, {
        k: resid_b[k] - resid_a[k] for k in resid_a
    })
    identities = _identities(
        a, b, bridge=bridge, assortment=assortment, pricing=pricing,
        inventory=inventory, routing=routing, resid_a=resid_a, resid_b=resid_b,
    )
    identities_ok = all(i["ok"] for i in identities)

    summary_a, summary_b = _run_summary(a), _run_summary(b)
    source = source or {"synthetic": True, "label": "seeded synthetic dataset", "filename": None}
    if source.get("synthetic", True):
        provenance = (
            "Seeded synthetic dataset — deterministic (fixed seed + MILP/CVRP solution budgets, "
            "never wall-clock). Both runs are solved with the same engines the dashboard, the "
            "plan and the exports use; nothing here is re-derived."
        )
    else:
        provenance = (
            f"Imported workbook '{source.get('filename')}' ({source.get('n_skus')} SKUs) — "
            "customers and delivery routing remain the synthetic demo layer, so a fleet change "
            "diffs the demo routing, not your network. Deterministic."
        )

    changed_fields = [
        key
        for key in ("budget_eur", "max_change", "service_level", "holding_rate",
                    "order_cost_eur", "n_vehicles", "capacity_kg")
        if summary_a["spec"][key] != summary_b["spec"][key]
    ]

    return {
        "run_a": summary_a,
        "run_b": summary_b,
        "changed_parameters": changed_fields,
        "headline": {
            "uplift_a_eur": summary_a["kpis"]["expected_uplift_eur"],
            "uplift_b_eur": summary_b["kpis"]["expected_uplift_eur"],
            "delta_eur": bridge["delta_eur"],
            "delta_pct": (
                round(bridge["delta_eur"] / summary_a["kpis"]["expected_uplift_eur"], 4)
                if summary_a["kpis"]["expected_uplift_eur"]
                else None
            ),
            "n_skus_added": assortment["totals"]["n_added"],
            "n_skus_dropped": assortment["totals"]["n_dropped"],
            "n_price_moves": pricing["totals"]["n_moved"],
            "n_policy_changes": inventory["totals"]["n_changed"],
            "n_stops_regrouped": routing["totals"]["n_stops_regrouped"],
            "working_capital_delta_eur": inventory["totals"]["working_capital_delta_eur"],
            "routing_changed": not routing["shared_solve"],
        },
        "bridge": bridge,
        "assortment": {k: v for k, v in assortment.items() if not k.startswith("_")},
        "pricing": {k: v for k, v in pricing.items() if not k.startswith("_")},
        "inventory": {k: v for k, v in inventory.items() if not k.startswith("_")},
        "routing": routing,
        "identities": identities,
        "identities_ok": identities_ok,
        "all_ok": identities_ok,
        "provenance": provenance,
        "data_source": source,
        "note": (
            "Every euro between the two headline uplifts is attributed to a named cause: the "
            "moved price recommendations, the SKUs the MILP added and dropped, the greedy "
            "baseline's own re-pick (the assortment lever is the MILP's edge over it), the "
            "routing kilometres, and publication rounding. The bridge is checked as an "
            "identity, not drawn as an illustration."
        ),
        "caveats": [
            (
                "The inventory-policy diff is reported in working-capital EUR but is deliberately "
                "NOT in the uplift bridge: the plan's expected uplift is composed from pricing, "
                "assortment and routing only, and adding an inventory term to it would be exactly "
                "the silent drift the platform's reconciliation guard exists to prevent."
            ),
            (
                "The rounding line is real, not a plug. Each part is the gap between one run's "
                "own published total and the sum of that run's own published rows, measured "
                "without reference to the other run, and bounded at half a cent per value."
            ),
            (
                "The assortment lever can move against intuition: it is the MILP's edge over the "
                "greedy baseline at that budget, so a looser budget lets greedy catch up and the "
                "lever can shrink even while more absolute margin is captured. The bridge shows "
                "that as its own line rather than burying it."
            ),
            (
                "'Regrouped stops' counts stops that travel with a different set of customers in "
                "the two solves, not stops whose vehicle number changed: the vehicle index is a "
                "solver label with no meaning across two independent searches, so comparing "
                "indices would report almost every stop as moved even when nothing did."
            ),
            (
                "The bridge carries a rounding line because the platform publishes the headline "
                "uplift and each of its three levers rounded independently, so a plan's own "
                "composition can differ from the sum of its levers by up to a cent. That is "
                "measured and shown here rather than absorbed into the largest bucket."
            ),
            (
                "Both runs are modelled on the same seeded dataset. A diff shows what the models "
                "say changes, not what a real quarter would deliver."
            ),
        ],
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    d = plan_diff()
    h = d["headline"]
    print(f"{d['run_a']['name']} -> {d['run_b']['name']}: EUR {h['delta_eur']:,.2f}")
    for s in d["bridge"]["steps"]:
        print(f"  {s['label']:<18} EUR {s['eur']:>14,.2f}   {s['id']}")
    print(f"  {'= total':<18} EUR {d['bridge']['attributed_eur']:>14,.2f} "
          f"(target {d['bridge']['delta_eur']:,.2f})")
    for i in d["identities"]:
        print(f"  [{'OK' if i['ok'] else 'FAIL'}] {i['id']}: {i['lhs']} {i['op']} {i['rhs']}")
    print("  ALL OK" if d["all_ok"] else "  FAILURES PRESENT")
