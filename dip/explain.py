"""Why this plan? A structured, honest explanation of the current plan.

One source of truth: :func:`explain_plan` returns a plain dict that the JSON
API (``/api/explain``), the dashboard panel, the PDF's "Why this plan?" page
and the workbook's Explanation sheet all render from — never four separately
computed stories.

The explanation answers four questions:

1. **Which constraints bind?** Working-capital budget utilisation and how many
   price recommendations are clipped at the guardrail.
2. **What changes vs doing nothing?** Per lever, the top 5 SKU-level moves
   with their individual EUR contributions plus the remainder — the moves and
   the remainder always sum to the lever total.
3. **How sensitive is the number?** The same plan re-run at budget -10% and
   +10% (two extra deterministic solves that reuse the one routing solve).
4. **What should you not over-trust?** Auto-included caveats: data source
   (synthetic vs imported), the elasticity assumption, the routing
   solved-once note.
"""

from __future__ import annotations

from .data import Dataset, build_dataset
from .optimize import (
    _sku_economics,
    optimize_assortment,
    optimize_prices,
    optimize_routes,
)
from .prescribe import COST_PER_KM, ROUTING_RUNS_PER_YEAR, build_plan

# |change_pct| within this tolerance of max_change counts as clipped (change_pct
# is rounded to 4 decimals upstream).
_GUARDRAIL_EPS = 5e-5
TOP_MOVES = 5


def _pricing_lever(prices: dict, total_eur: float) -> dict:
    recs = prices["recommendations"]
    top = recs[:TOP_MOVES]
    moves = [
        {
            "sku_id": r["sku_id"],
            "action": (
                f"price EUR {r['price_old']:.2f} -> EUR {r['price_new']:.2f} "
                f"({r['change_pct']:+.1%}, elasticity {r['elasticity']:.2f})"
            ),
            "contribution_eur": r["profit_delta"],
        }
        for r in top
    ]
    remainder = round(total_eur - sum(m["contribution_eur"] for m in moves), 2)
    return {
        "lever": "pricing",
        "total_eur": round(total_eur, 2),
        "moves": moves,
        "n_moves": len(recs),
        "remainder_eur": remainder,
        "note": (
            f"top {len(moves)} of {len(recs)} recommended price moves; the remaining "
            f"{len(recs) - len(moves)} contribute EUR {remainder:,.0f} together"
        ),
    }


def _assortment_lever(ds: Dataset, assort: dict, total_eur: float) -> dict:
    """SKU-level diff between the MILP assortment and the greedy baseline.

    The lever total is MILP margin minus greedy margin; SKUs carried by both
    plans cancel, so the adds (+margin) and drops (-margin) sum *exactly* to
    the lever total.
    """
    eco = _sku_economics(ds)
    margin_by_sku = dict(zip(eco["ids"], (float(m) for m in eco["margin"])))
    milp = set(assort["carried_skus"])
    greedy = set(assort["greedy_carried_skus"])
    diffs = [
        {"sku_id": s, "contribution_eur": round(margin_by_sku[s], 2), "kind": "add"}
        for s in milp - greedy
    ] + [
        {"sku_id": s, "contribution_eur": round(-margin_by_sku[s], 2), "kind": "drop"}
        for s in greedy - milp
    ]
    diffs.sort(key=lambda d: abs(d["contribution_eur"]), reverse=True)
    moves = [
        {
            "sku_id": d["sku_id"],
            "action": (
                f"carry {d['sku_id']} (the greedy plan skips it)"
                if d["kind"] == "add"
                else f"drop {d['sku_id']} (the greedy plan carries it)"
            ),
            "contribution_eur": d["contribution_eur"],
        }
        for d in diffs[:TOP_MOVES]
    ]
    remainder = round(total_eur - sum(m["contribution_eur"] for m in moves), 2)
    return {
        "lever": "assortment",
        "total_eur": round(total_eur, 2),
        "moves": moves,
        "n_moves": len(diffs),
        "remainder_eur": remainder,
        "note": (
            f"{len(diffs)} SKUs differ between the optimal and the greedy assortment; "
            f"the top {len(moves)} swaps are shown, the rest contribute EUR {remainder:,.0f}"
        ),
    }


def _routing_lever(routes: dict, total_eur: float) -> dict:
    move = {
        "sku_id": None,
        "action": (
            f"re-sequence the delivery run: {routes['baseline_km']:,.0f} km "
            f"(current practice) -> {routes['optimized_km']:,.0f} km with "
            f"{routes['n_vehicles_used']} vehicles ({routes['pct_saved']:.1%} less), "
            f"valued at EUR {COST_PER_KM:.2f}/km x {ROUTING_RUNS_PER_YEAR} runs/yr"
        ),
        "contribution_eur": round(total_eur, 2),
    }
    return {
        "lever": "routing",
        "total_eur": round(total_eur, 2),
        "moves": [move],
        "n_moves": 1,
        "remainder_eur": 0.0,
        "note": "one delivery run, solved once per dataset and reused everywhere",
    }


def explain_plan(
    ds: Dataset | None = None,
    budget: float | None = None,
    max_change: float = 0.15,
    plan: dict | None = None,
    routes: dict | None = None,
    prices: dict | None = None,
    source: dict | None = None,
) -> dict:
    """Build the full explanation structure for one scenario.

    Callers that already hold the scenario's ``plan`` (and the shared
    ``routes`` / ``prices`` solves) pass them in, so the explanation quotes the
    exact object the dashboard serves. Everything recomputed here (assortment,
    the two sensitivity plans) is deterministic, so the numbers can never drift
    from the plan's.
    """
    ds = ds or build_dataset()
    routes = routes if routes is not None else optimize_routes(ds)
    prices = prices if prices is not None else optimize_prices(ds, max_change=max_change)
    plan = plan if plan is not None else build_plan(
        ds, budget=budget, max_change=max_change, routes=routes, prices=prices
    )
    assort = optimize_assortment(ds, budget=plan["budget"])

    # --- sensitivity: the SAME plan path at budget -/+10% (routing reused) ---
    b = plan["budget"]
    plan_lo = build_plan(ds, budget=b * 0.9, max_change=max_change, routes=routes, prices=prices)
    plan_hi = build_plan(ds, budget=b * 1.1, max_change=max_change, routes=routes, prices=prices)

    # --- binding constraints -------------------------------------------------
    used = assort["milp"]["capital_used"]
    utilisation = round(used / plan["budget"], 4) if plan["budget"] else 0.0
    budget_binding = assort["milp"]["count"] < len(ds.skus) and plan["full_capital"] > plan["budget"]
    hits = sum(
        1 for r in prices["recommendations"]
        if abs(r["change_pct"]) >= prices["max_change"] - _GUARDRAIL_EPS
    )
    n_recs = len(prices["recommendations"])
    binding_constraints = [
        {
            "name": "working-capital budget",
            "binding": bool(budget_binding),
            "used_eur": used,
            "available_eur": plan["budget"],
            "utilisation": utilisation,
            "detail": (
                f"EUR {used:,.0f} of the EUR {plan['budget']:,.0f} budget is committed "
                f"({utilisation:.1%}); the full range would need EUR {plan['full_capital']:,.0f} — "
                + (
                    f"the budget is binding: it forces {len(ds.skus) - assort['milp']['count']} "
                    f"of {len(ds.skus)} SKUs out of the assortment."
                    if budget_binding
                    else "the budget is not binding: every SKU worth carrying fits."
                )
            ),
        },
        {
            "name": f"price guardrail (+/-{prices['max_change']:.0%})",
            "binding": hits > 0,
            "hits": hits,
            "total": n_recs,
            "detail": (
                f"{hits} of {n_recs} price recommendations are clipped at the "
                f"+/-{prices['max_change']:.0%} guardrail"
                + (
                    " — the guardrail is binding; widening it would unlock more pricing uplift."
                    if hits
                    else " — the guardrail is slack at this setting."
                )
            ),
        },
    ]

    levers = [
        _pricing_lever(prices, plan["levers"]["pricing"]),
        _assortment_lever(ds, assort, plan["levers"]["assortment"]),
        _routing_lever(routes, plan["levers"]["routing"]),
    ]
    levers.sort(key=lambda lv: lv["total_eur"], reverse=True)

    # --- caveats (auto-included, source-aware) -------------------------------
    source = source or {"synthetic": True, "label": "seeded synthetic dataset", "filename": None}
    caveats = []
    if source.get("synthetic", True):
        caveats.append(
            "All numbers are computed on a seeded synthetic dataset — a demonstration "
            "of the method, not measured company data."
        )
    else:
        caveats.append(
            f"Computed on your imported workbook '{source.get('filename')}' "
            f"({source.get('n_skus')} SKUs). Customers and delivery routing remain the "
            "synthetic demo layer — the import template covers products only."
        )
    caveats.append(
        "Pricing uplift assumes a constant-elasticity demand response per SKU"
        + (
            " (imported mode uses category-default elasticities)"
            if not source.get("synthetic", True)
            else ""
        )
        + "; validate with controlled price tests before rollout."
    )
    caveats.append(
        "Routing is solved once per dataset with a fixed solution budget and reused by "
        "every scenario, so the budget sensitivity below is driven by assortment "
        "(and pricing) only."
    )
    caveats.append(
        "Assortment margin annualises mean monthly demand; capital tied up per SKU is a "
        "cycle-stock model (cost x lead-time demand + shelf allowance), not a balance-sheet figure."
    )

    return {
        "scenario": {
            "budget": plan["budget"],
            "full_capital": plan["full_capital"],
            "budget_pct_of_full": round(plan["budget"] / plan["full_capital"], 4)
            if plan["full_capital"]
            else 0.0,
            "max_change": plan["max_change"],
        },
        "data_source": source,
        "headline": {
            "expected_uplift_eur": plan["expected_uplift_eur"],
            "expected_uplift_pct": plan["expected_uplift_pct"],
            "baseline": (
                "do nothing: keep today's prices, fill the budget with the greedy "
                "assortment, drive the current-practice routes"
            ),
        },
        "binding_constraints": binding_constraints,
        "levers": levers,
        "sensitivity": {
            "budget_minus_10pct": {
                "budget": plan_lo["budget"],
                "expected_uplift_eur": plan_lo["expected_uplift_eur"],
                "delta_eur": round(plan_lo["expected_uplift_eur"] - plan["expected_uplift_eur"], 2),
            },
            "budget_plus_10pct": {
                "budget": plan_hi["budget"],
                "expected_uplift_eur": plan_hi["expected_uplift_eur"],
                "delta_eur": round(plan_hi["expected_uplift_eur"] - plan["expected_uplift_eur"], 2),
            },
            "note": (
                "the same deterministic plan path re-run at -10% and +10% working-capital "
                "budget; guardrail unchanged, routing solve reused. The assortment lever is "
                "the optimiser's edge over the greedy baseline at the same budget, so a "
                "looser budget can shrink the edge (the baseline catches up) — a larger "
                "budget still captures more absolute margin"
            ),
        },
        "caveats": caveats,
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    ex = explain_plan()
    print(f"Uplift EUR {ex['headline']['expected_uplift_eur']:,.0f} "
          f"({ex['headline']['expected_uplift_pct']:.1%})")
    for bc in ex["binding_constraints"]:
        print(f"  [{'BINDING' if bc['binding'] else 'slack '}] {bc['name']}")
    for lv in ex["levers"]:
        print(f"  {lv['lever']}: EUR {lv['total_eur']:,.0f} "
              f"({len(lv['moves'])} moves shown, remainder EUR {lv['remainder_eur']:,.0f})")
