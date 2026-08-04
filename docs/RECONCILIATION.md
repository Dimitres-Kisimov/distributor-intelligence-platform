# Numbers reconciliation

Machine-checked ledger tying every headline figure the platform quotes to the specific engine field it comes from, plus the cross-engine identities that keep the composed numbers honest. Regenerate with `python -m dip --reconcile`; the identities and the README-presence guard are enforced by `tests/test_reconcile.py`.

- Data: seeded synthetic dataset — deterministic (fixed seed + MILP/CVRP solution budgets, never wall-clock); models no real company (seed 20260724).
- Status: **all identities hold and every headline number is present in the README.**

## Headline numbers -> engine source

| Metric | Value | Traces to engine field | README shows | In README |
| --- | --- | --- | --- | --- |
| Revenue (24 mo) | 4788971.2 | `analytics.kpis.revenue` | `EUR 4,788,971` | yes |
| Gross margin | 3257507.06 | `analytics.kpis.gross_margin` | `EUR 3,257,507` | yes |
| Gross margin % | 0.6802 | `analytics.kpis.gross_margin_pct` | `68.0%` | yes |
| YoY revenue growth | 0.0987 | `analytics.kpis.yoy` | `+9.9%` | yes |
| OTIF service (modelled) | 0.8573 | `analytics.kpis.otif` | `85.7%` | yes |
| Forecast accuracy | 0.3757 | `forecast.forecast_revenue.mase` | `MASE 0.38` | yes |
| Next-month revenue | 204617.83 | `forecast.forecast_revenue.next_month_revenue` | `EUR 204,618` | yes |
| Assortment MILP margin | 935526.53 | `optimize.optimize_assortment.milp.margin` | `EUR 935,527` | yes |
| Assortment greedy margin | 934502.87 | `optimize.optimize_assortment.greedy.margin` | `EUR 934,503` | yes |
| Assortment uplift vs greedy | 1023.66 | `optimize.optimize_assortment.uplift_vs_greedy` | `+EUR 1,024` | yes |
| Assortment SKUs carried | 108 | `optimize.optimize_assortment.milp.count` | `108 SKUs` | yes |
| Assortment budget | 10557.54 | `optimize.optimize_assortment.budget` | `EUR 10,558` | yes |
| Pricing uplift | 95609.42 | `optimize.optimize_prices.uplift` | `+EUR 95,609` | yes |
| Pricing uplift % | 0.064 | `optimize.optimize_prices.uplift_pct` | `+6.4%` | yes |
| Routing optimised km | 420.08 | `optimize.optimize_routes.optimized_km` | `420 km` | yes |
| Routing baseline km | 560.39 | `optimize.optimize_routes.baseline_km` | `560 km` | yes |
| Routing km saved | 140.31 | `optimize.optimize_routes.km_saved` | `140 km` | yes |
| Routing % saved | 0.2504 | `optimize.optimize_routes.pct_saved` | `25.0%` | yes |
| Routing vehicles used | 6 | `optimize.optimize_routes.n_vehicles_used` | `6 vehicles` | yes |
| Expected annual uplift | 136972.2 | `prescribe.build_plan.expected_uplift_eur` | `EUR 136,972` | yes |
| Uplift % of annual gross margin | 0.0841 | `prescribe.build_plan.expected_uplift_pct` | `8.4%` | yes |

## Cross-engine identities

| Identity | Check | Left | Right | Holds |
| --- | --- | --- | --- | --- |
| `uplift_composition` | Expected annual uplift = pricing lever + assortment lever + routing lever | 136972.2 | 136972.2 | yes |
| `routing_eur_bridge` | Routing lever = km saved x EUR 1.15/km x 250 runs/yr | 40339.12 | 40339.12 | yes |
| `routing_km_identity` | Routing km saved = baseline km - optimised km | 140.31 | 140.31 | yes |
| `routing_pct_identity` | Routing % saved = km saved / baseline km | 0.2504 | 0.2504 | yes |
| `pricing_lever_identity` | Pricing lever = new profit - base profit (from the pricing engine) | 95609.42 | 95609.42 | yes |
| `pricing_lever_ties_engine` | Plan pricing lever = pricing engine's reported uplift | 95609.42 | 95609.42 | yes |
| `assortment_lever_identity` | Assortment lever = MILP margin - greedy margin | 1023.66 | 1023.66 | yes |
| `assortment_lever_ties_engine` | Plan assortment lever = assortment engine's edge over greedy | 1023.66 | 1023.66 | yes |
| `gross_margin_identity` | Gross margin = revenue - COGS | 3257507.06 | 3257507.06 | yes |
| `gross_margin_pct_identity` | Gross margin % = gross margin / revenue | 0.6802 | 0.6802 | yes |
| `annual_gross_margin_basis` | Annual gross margin = gross margin x 12 / 24 months of history | 1628753.53 | 1628753.53 | yes |
| `uplift_pct_basis` | Uplift % = expected annual uplift / annual gross margin | 0.0841 | 0.0841 | yes |
| `assortment_budget_basis` | Default budget = 40% of the range's full working capital | 10557.54 | 10557.54 | yes |
| `next_month_forecast_tie` | Plan next-month revenue = forecast engine's next-month point | 204617.83 | 204617.83 | yes |
| `baseline_gross_margin_tie` | Plan baseline gross margin = analytics gross-margin KPI | 3257507.06 | 3257507.06 | yes |
| `plan_mase_tie` | Plan-quoted MASE = forecast engine's backtest MASE | 0.3757 | 0.3757 | yes |

## Plain-language read

The platform's headline is an **expected annual uplift**. This report shows it is not a free-floating number: it is exactly the sum of three monetised levers, each of which traces to its own optimiser. The pricing lever is the pricing engine's new-minus-base profit; the assortment lever is the MILP's margin edge over the greedy baseline; the routing lever is the CVRP's kilometres saved, priced at EUR 1.15/km over 250 runs a year. The descriptive KPIs tie the same way — gross margin is revenue minus COGS, and the uplift percentage is quoted against *annual* gross margin so a 24-month history does not halve the ratio. Every one of those relationships is checked above, and every number is checked to still match what the README prints.

## Honest limitations

- **Synthetic data.** Every figure is measured on one seeded synthetic dataset; it is illustrative of the method, not a real-world benchmark or a claim of state-of-the-art performance.
- **The README-presence guard works at display precision.** A figure the README rounds (e.g. MASE to two decimals) is checked at that precision, so a sub-display drift in a rounded value need not trip the guard. The identity checks and the value column of `reconciliation.csv` carry the engines' full reported precision.
- **This reconciles internal consistency, not correctness.** It proves the engines agree with each other and with the docs; it does not validate the underlying models against real demand, prices or routes.
