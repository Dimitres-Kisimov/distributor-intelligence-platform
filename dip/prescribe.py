"""Composition layer: forecast + optimisers -> one plan with decision cards.

This is where the platform earns its name. It runs each engine, converts the
individual lifts into a single expected annual € uplift versus today's
baseline, and produces the "recommended actions" cards the dashboard shows.
"""

from __future__ import annotations

from .analytics import kpis
from .data import Dataset, build_dataset
from .forecast import forecast_revenue
from .optimize import optimize_assortment, optimize_prices, optimize_routes

# assume ~250 delivery runs per year to annualise a single-day routing saving,
# and a nominal cost per km for the € value of distance saved.
ROUTING_RUNS_PER_YEAR = 250
COST_PER_KM = 1.15


def build_plan(
    ds: Dataset | None = None,
    budget: float | None = None,
    max_change: float = 0.15,
    routes: dict | None = None,
    prices: dict | None = None,
) -> dict:
    """Run every engine and assemble the unified plan.

    ``budget`` is the assortment working-capital budget; ``max_change`` the
    price guardrail passed to the pricing engine. Callers that already hold a
    routing result (independent of both knobs) or a pricing result *computed
    with the same* ``max_change`` can pass them in to skip the re-solve.
    """
    ds = ds or build_dataset()

    k = kpis(ds)
    fc = forecast_revenue(ds)
    assort = optimize_assortment(ds, budget=budget)
    prices = prices if prices is not None else optimize_prices(ds, max_change=max_change)
    routes = routes if routes is not None else optimize_routes(ds)

    # --- € uplift per lever (all annualised) --------------------------------
    pricing_uplift = prices["uplift"]
    assortment_uplift = assort["uplift_vs_greedy"]
    routing_uplift = routes["km_saved"] * COST_PER_KM * ROUTING_RUNS_PER_YEAR
    total_uplift = pricing_uplift + assortment_uplift + routing_uplift

    baseline_margin = k["gross_margin"]
    # The uplift is annual, so quote it against *annual* gross margin, not the
    # full multi-month horizon total (24 months would understate the ratio 2x).
    horizon_months = len(k["months"]) or 12
    annual_gross_margin = baseline_margin * 12.0 / horizon_months

    cards = [
        {
            "id": "pricing",
            "title": "Re-price the range on elasticity",
            "detail": (
                f"Apply elasticity-guided prices (max +/-{int(prices['max_change'] * 100)}%). "
                f"Model lifts annual gross profit by EUR {pricing_uplift:,.0f} "
                f"({prices['uplift_pct']:.1%})."
            ),
            "impact_eur": round(pricing_uplift, 2),
            "confidence": "High",
            "lever": "Pricing",
        },
        {
            "id": "assortment",
            "title": f"Carry the optimal {assort['milp']['count']} SKUs",
            "detail": (
                f"Under a EUR {assort['budget']:,.0f} working-capital budget, the MILP "
                f"assortment captures EUR {assort['milp']['margin']:,.0f} of margin, "
                f"EUR {assortment_uplift:,.0f} more than the greedy plan."
            ),
            "impact_eur": round(assortment_uplift, 2),
            "confidence": "High",
            "lever": "Assortment",
        },
        {
            "id": "routing",
            "title": f"Run OR-Tools delivery routes ({routes['n_vehicles_used']} vehicles)",
            "detail": (
                f"Optimised routing cuts {routes['km_saved']:,.0f} km per run "
                f"({routes['pct_saved']:.1%}); at EUR {COST_PER_KM:.2f}/km over "
                f"{ROUTING_RUNS_PER_YEAR} runs that is EUR {routing_uplift:,.0f}/yr."
            ),
            "impact_eur": round(routing_uplift, 2),
            "confidence": "Medium",
            "lever": "Logistics",
        },
        {
            "id": "forecast",
            "title": "Plan to the demand forecast",
            "detail": (
                f"Next month revenue is forecast at EUR {fc['next_month_revenue']:,.0f} "
                f"(Holt-Winters, backtest MASE {fc['mase']}). Align purchasing and "
                f"safety stock to the seasonal curve."
            ),
            "impact_eur": 0.0,
            "confidence": "High",
            "lever": "Forecast",
        },
    ]
    cards.sort(key=lambda c: c["impact_eur"], reverse=True)

    return {
        "baseline_gross_margin": round(baseline_margin, 2),
        "annual_gross_margin": round(annual_gross_margin, 2),
        "expected_uplift_eur": round(total_uplift, 2),
        "expected_uplift_pct": round(total_uplift / annual_gross_margin, 4) if annual_gross_margin else 0.0,
        "levers": {
            "pricing": round(pricing_uplift, 2),
            "assortment": round(assortment_uplift, 2),
            "routing": round(routing_uplift, 2),
        },
        "budget": assort["budget"],
        "full_capital": assort["full_capital"],
        "max_change": prices["max_change"],
        "forecast_mase": fc["mase"],
        "next_month_revenue": fc["next_month_revenue"],
        "next_month_band": [fc["lower"][0], fc["upper"][0]],
        "cards": cards,
    }


if __name__ == "__main__":  # pragma: no cover
    p = build_plan()
    print(f"Expected annual uplift: EUR {p['expected_uplift_eur']:,.0f} "
          f"({p['expected_uplift_pct']:.1%} of annual gross margin)")
    for c in p["cards"]:
        print(f"  - [{c['lever']}] {c['title']}: EUR {c['impact_eur']:,.0f}")
