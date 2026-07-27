"""Prescriptive engines: assortment (MILP), pricing (elasticity), routing (CVRP).

Each optimiser ships with a transparent baseline so the *lift* from optimising
is always quantified:

- assortment : ``scipy.optimize.milp`` 0/1 knapsack  vs  greedy density heuristic
- pricing    : elasticity-based Lerner markup with guardrails  vs  status quo
- routing    : OR-Tools CVRP  vs  nearest-neighbour construction
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import LinearConstraint, milp

from .data import Dataset, build_dataset

# ----------------------------------------------------------------------------
# (a) Assortment — 0/1 knapsack: pick the SKU set that maximises annual margin
#     under a working-capital budget (capital tied up = cost x cycle stock).
# ----------------------------------------------------------------------------


def _sku_economics(ds: Dataset) -> dict:
    """Annual margin contribution and capital footprint per SKU."""
    skus = ds.skus
    annual_units = np.array([s["demand_mean"] * 12 for s in skus])
    unit_margin = np.array([s["price"] - s["cost"] for s in skus])
    margin = annual_units * unit_margin
    # capital tied up ~ cost x cycle+safety stock, scaled by lead time & shelf
    cycle_stock = np.array([s["demand_mean"] * (s["lead_time_days"] / 30.0) for s in skus])
    capital = np.array([s["cost"] for s in skus]) * (cycle_stock + np.array([s["shelf_space"] for s in skus]) * 5)
    return {
        "ids": [s["sku_id"] for s in skus],
        "margin": margin,
        "capital": np.maximum(capital, 1.0),
    }


def optimize_assortment(ds: Dataset | None = None, budget: float | None = None) -> dict:
    """MILP assortment vs greedy baseline under a working-capital ``budget``."""
    ds = ds or build_dataset()
    eco = _sku_economics(ds)
    margin = eco["margin"]
    capital = eco["capital"]
    n = len(margin)
    full_capital = float(capital.sum())
    if budget is None:
        budget = 0.4 * full_capital  # default: 40% of full-range capital (tight)

    # ---- MILP: maximise margin s.t. sum(capital_i x_i) <= budget, x in {0,1}
    # scipy.optimize.milp minimises c @ x, so negate the margin.
    constraints = LinearConstraint(capital, ub=budget)
    integrality = np.ones(n)
    bounds_lo = np.zeros(n)
    bounds_hi = np.ones(n)
    from scipy.optimize import Bounds

    res = milp(
        c=-margin,
        constraints=constraints,
        integrality=integrality,
        bounds=Bounds(bounds_lo, bounds_hi),
    )
    x = np.round(res.x).astype(int) if res.x is not None else np.zeros(n, dtype=int)
    milp_margin = float(margin @ x)
    milp_capital = float(capital @ x)
    milp_count = int(x.sum())

    # ---- Greedy baseline: sort by margin-per-capital density, fill the budget
    density = margin / capital
    order = np.argsort(-density)
    g = np.zeros(n, dtype=int)
    used = 0.0
    for i in order:
        if used + capital[i] <= budget:
            g[i] = 1
            used += capital[i]
    greedy_margin = float(margin @ g)
    greedy_capital = float(capital @ g)
    greedy_count = int(g.sum())

    uplift = milp_margin - greedy_margin
    carried = [eco["ids"][i] for i in range(n) if x[i] == 1]
    greedy_carried = [eco["ids"][i] for i in range(n) if g[i] == 1]
    return {
        "budget": round(budget, 2),
        "full_capital": round(full_capital, 2),
        "milp": {
            "margin": round(milp_margin, 2),
            "capital_used": round(milp_capital, 2),
            "count": milp_count,
        },
        "greedy": {
            "margin": round(greedy_margin, 2),
            "capital_used": round(greedy_capital, 2),
            "count": greedy_count,
        },
        "uplift_vs_greedy": round(uplift, 2),
        "uplift_pct": round(uplift / greedy_margin, 4) if greedy_margin else 0.0,
        "carried_skus": carried,
        # the greedy baseline's picks too, so the plan explanation can show the
        # SKU-level diff (adds/drops) whose contributions sum to the uplift
        "greedy_carried_skus": greedy_carried,
        "status": res.message if hasattr(res, "message") else "optimal",
    }


# ----------------------------------------------------------------------------
# (b) Pricing — elasticity-based Lerner markup with guardrails.
# ----------------------------------------------------------------------------


def optimize_prices(ds: Dataset | None = None, max_change: float = 0.15) -> dict:
    """Recommend a price per SKU from its elasticity (constant-elasticity demand).

    Profit-maximising markup over marginal cost is the Lerner rule
    ``(p - c)/p = -1/e`` for elastic goods (``|e| > 1``). Recommendations are
    clipped to +/- ``max_change`` of today's price so they stay commercially sane.
    """
    ds = ds or build_dataset()
    rows = []
    base_profit = 0.0
    new_profit = 0.0
    for s in ds.skus:
        c, p0, e = s["cost"], s["price"], s["elasticity"]
        q0 = s["demand_mean"] * 12
        if e < -1:  # elastic: interior optimum exists
            p_star = c / (1 + 1 / e)  # = c * e/(e+1)
        else:  # inelastic: push toward the guardrail ceiling
            p_star = p0 * (1 + max_change)
        p_new = float(np.clip(p_star, p0 * (1 - max_change), p0 * (1 + max_change)))
        p_new = max(p_new, c * 1.02)  # never price below cost
        # constant-elasticity volume response to the price move
        q_new = q0 * (p_new / p0) ** e
        bp = (p0 - c) * q0
        npf = (p_new - c) * q_new
        base_profit += bp
        new_profit += npf
        rows.append(
            {
                "sku_id": s["sku_id"],
                "category": s["category"],
                "price_old": round(p0, 2),
                "price_new": round(p_new, 2),
                "change_pct": round(p_new / p0 - 1, 4),
                "elasticity": e,
                "profit_delta": round(npf - bp, 2),
            }
        )
    rows.sort(key=lambda r: r["profit_delta"], reverse=True)
    uplift = new_profit - base_profit
    return {
        "max_change": max_change,
        "base_profit": round(base_profit, 2),
        "new_profit": round(new_profit, 2),
        "uplift": round(uplift, 2),
        "uplift_pct": round(uplift / base_profit, 4) if base_profit else 0.0,
        "recommendations": rows,
    }


# ----------------------------------------------------------------------------
# (c) Routing — capacitated vehicle routing (CVRP) via OR-Tools.
# ----------------------------------------------------------------------------

# Deterministic termination for the guided-local-search phase: stop after a
# fixed number of search solutions instead of a wall-clock time limit. A time
# limit makes the answer depend on machine speed and load — two solves in the
# same process can return different km, so the dashboard, the prescribe layer
# and the exports drift apart. A solution budget makes every solve identical.
# 600 sits past the GLS improvement plateau on this instance (a 2000-solution
# search finds no better tour) and solves in roughly two seconds.
GLS_SOLUTION_LIMIT = 600


def _distance_matrix(points: list[tuple[float, float]]) -> np.ndarray:
    pts = np.array(points)
    diff = pts[:, None, :] - pts[None, :, :]
    return np.sqrt((diff**2).sum(axis=2))


def _nn_cvrp(dist: np.ndarray, demands: list[int], capacity: float) -> tuple[float, list[list[int]]]:
    """Capacity-aware nearest-neighbour construction (the CVRP baseline).

    Builds routes greedily: from the depot repeatedly hop to the nearest
    unvisited customer that still fits the vehicle's remaining capacity; when
    none fits, return to the depot and dispatch the next vehicle. This is the
    fair apples-to-apples baseline for the OR-Tools CVRP solution. Returns the
    total distance and the node sequences (each starting and ending at 0).
    """
    n = dist.shape[0]
    unvisited = set(range(1, n))
    total = 0.0
    routes: list[list[int]] = []
    while unvisited:
        cur = 0
        load = 0.0
        seq = [0]
        while True:
            feasible = [j for j in unvisited if load + demands[j] <= capacity]
            if not feasible:
                break
            nxt = min(feasible, key=lambda j: dist[cur, j])
            total += dist[cur, nxt]
            load += demands[nxt]
            cur = nxt
            seq.append(nxt)
            unvisited.remove(nxt)
        total += dist[cur, 0]  # return to depot
        seq.append(0)
        routes.append(seq)
    return float(total), routes


def optimize_routes(
    ds: Dataset | None = None,
    n_vehicles: int = 6,
    capacity_kg: float = 2200.0,
    solution_limit: int = GLS_SOLUTION_LIMIT,
) -> dict:
    """CVRP with OR-Tools vs a nearest-neighbour baseline for the day's run.

    The search terminates on ``solution_limit`` (a fixed solution budget, not a
    wall clock), so repeated solves return byte-identical routes and km.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    ds = ds or build_dataset()
    depot = ds.depot
    stops = ds.route_stops
    points = [(depot["x"], depot["y"])] + [(s["x"], s["y"]) for s in stops]
    demands = [0] + [math.ceil(s["demand_kg"]) for s in stops]
    dist = _distance_matrix(points)
    scale = 100  # OR-Tools works in integers
    idist = np.round(dist * scale).astype(int)

    manager = pywrapcp.RoutingIndexManager(len(points), n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(from_index, to_index):
        return int(idist[manager.IndexToNode(from_index), manager.IndexToNode(to_index)])

    transit_idx = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    def demand_cb(from_index):
        return demands[manager.IndexToNode(from_index)]

    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_idx, 0, [int(capacity_kg)] * n_vehicles, True, "Capacity"
    )

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.solution_limit = solution_limit  # deterministic: no wall-clock cutoff

    solution = routing.SolveWithParameters(params)
    routes = []
    total_km = 0.0
    if solution:
        for v in range(n_vehicles):
            index = routing.Start(v)
            node_seq = []
            load = 0
            route_km = 0.0
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                node_seq.append(node)
                load += demands[node]
                prev = index
                index = solution.Value(routing.NextVar(index))
                route_km += idist[
                    manager.IndexToNode(prev), manager.IndexToNode(index)
                ] / scale
            node_seq.append(manager.IndexToNode(index))  # return to depot
            if len(node_seq) > 2:  # vehicle actually used
                routes.append(
                    {
                        "vehicle": v,
                        "stops": [
                            (
                                {"id": "DEPOT", "x": depot["x"], "y": depot["y"]}
                                if node == 0
                                else {
                                    "id": stops[node - 1]["customer_id"],
                                    "name": stops[node - 1]["name"],
                                    "x": stops[node - 1]["x"],
                                    "y": stops[node - 1]["y"],
                                }
                            )
                            for node in node_seq
                        ],
                        "load_kg": round(load, 1),
                        "distance_km": round(route_km, 2),
                    }
                )
                total_km += route_km

    baseline_km, baseline_seqs = _nn_cvrp(dist, demands, capacity_kg)

    def _node_point(node: int) -> dict:
        if node == 0:
            return {"id": "DEPOT", "x": depot["x"], "y": depot["y"]}
        return {
            "id": stops[node - 1]["customer_id"],
            "name": stops[node - 1]["name"],
            "x": stops[node - 1]["x"],
            "y": stops[node - 1]["y"],
        }

    baseline_routes = []
    for v, seq in enumerate(baseline_seqs):
        seg_km = sum(dist[seq[i], seq[i + 1]] for i in range(len(seq) - 1))
        baseline_routes.append(
            {
                "vehicle": v,
                "stops": [_node_point(n) for n in seq],
                "load_kg": round(float(sum(demands[n] for n in seq)), 1),
                "distance_km": round(float(seg_km), 2),
            }
        )

    saved = baseline_km - total_km
    return {
        "depot": {"x": depot["x"], "y": depot["y"]},
        "routes": routes,
        "baseline_routes": baseline_routes,
        "n_vehicles_used": len(routes),
        "capacity_kg": capacity_kg,
        "optimized_km": round(total_km, 2),
        "baseline_km": round(baseline_km, 2),
        "km_saved": round(saved, 2),
        "pct_saved": round(saved / baseline_km, 4) if baseline_km else 0.0,
        "n_stops": len(stops),
    }


if __name__ == "__main__":  # pragma: no cover
    a = optimize_assortment()
    print(f"Assortment MILP margin EUR {a['milp']['margin']:,.0f} vs greedy EUR {a['greedy']['margin']:,.0f} "
          f"(+EUR {a['uplift_vs_greedy']:,.0f})  carrying {a['milp']['count']} SKUs")
    p = optimize_prices()
    print(f"Pricing uplift EUR {p['uplift']:,.0f} ({p['uplift_pct']:.1%})")
    r = optimize_routes()
    print(f"Routing {r['optimized_km']:.0f} km vs {r['baseline_km']:.0f} km baseline "
          f"({r['km_saved']:.0f} km / {r['pct_saved']:.1%} saved), {r['n_vehicles_used']} vehicles")
