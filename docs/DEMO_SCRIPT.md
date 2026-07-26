# 90-Second Demo Script

The interview walkthrough. Every figure below is measured, not remembered: it
was re-verified against the live app on this build, and the test suite pins
the ones that matter. Total speaking time is about 90 seconds.

## Before the call

```bash
python app.py        # -> http://localhost:5000
```

Start it a couple of minutes early: the app solves every engine once at boot
(roughly 30-60 seconds), then everything is instant. Have the dashboard open
in a tab.

## The six steps

### 1. Open the dashboard — the headline (0:00-0:20)

> "This is a command center for a mid-size industrial distributor — about 200
> SKUs, 52 customers, 24 months of demand. The data is fully synthetic and
> seeded, on purpose: every number you'll see is reproducible to the cent on
> any machine. Revenue EUR 4.79M, 68.0% gross margin — and the headline: the
> optimisers find an expected annual uplift of EUR 136,972, 8.4% of gross
> margin."

Land on: **EUR 136,972 / 8.4%** (uplift KPI tile), revenue **EUR 4,788,971**,
margin **68.0%**.
Caveat to volunteer: *synthetic data — these figures demonstrate the method,
they are not a real-world benchmark.*

### 2. Forecast (0:20-0:32)

> "Next month's revenue forecast is EUR 204,618. The error is earned, not
> asserted: MASE 0.38 over a nine-fold rolling-origin backtest, so it beats a
> seasonal-naive benchmark on this series."

Land on: **EUR 204,618**, **MASE 0.38** (9 folds).
Caveat: *that is a claim about this seeded series, not about real demand.*

### 3. Budget slider — the plan re-optimises live (0:32-0:52)

Drag the working-capital slider from 40% down to **30%** and click the
**+/-5%** price guardrail.

> "Watch the plan re-solve live: the assortment MILP and the pricing engine
> run per request. Uplift drops to EUR 74,559 — pricing falls from EUR 95,609
> to EUR 33,892 under the tighter guardrail. Routing stays at EUR 40,339
> because it's solved exactly once at startup, on a fixed solution budget, so
> it's deterministic — the same km on every machine, never two answers from
> one process."

Land on: **EUR 74,559** uplift; pricing **95,609 -> 33,892**; routing
**40,339** unchanged.
Caveat: *the deterministic-solver disclosure above — determinism was chosen
over squeezing the last km out of the CVRP search.*

### 4. Drill into a cell (0:52-1:05)

Click the **CZ** cell of the ABC-XYZ matrix.

> "Classifications resolve to things you can act on: CZ — low revenue,
> erratic demand — is 17 actual SKUs with names, like DAI 967-C, not just a
> count. Same for the RFM segments: 'at risk' is a customer list."

Land on: **17 SKUs** in CZ; first row e.g. **DAI 967-C**.

### 5. Pin the scenario and compare (1:05-1:20)

Click **Pin scenario**, then restore the defaults (slider back to 40%,
guardrail +/-15%).

> "I can pin the tightened scenario and compare: the two plans are EUR 62,413
> apart, and the compare strip is honest about where that comes from —
> pricing and assortment only, because routing is identical in both."

Land on: delta **EUR 62,413** (74,559 vs 136,972).

### 6. Export — the numbers match (1:20-1:30)

Click **Excel** (or PDF).

> "The export is built from the same plan object the dashboard just rendered
> — the Summary sheet says EUR 136,972 too, and a test asserts the workbook
> equals the on-screen plan. One solve, one plan, everywhere."

Land on: Summary row "Expected annual uplift (EUR)" = **136,972**.

## If asked "is this production-ready?"

No, and it doesn't pretend to be: data is synthetic, OTIF is a modelled
proxy, the API auth is a single-token deployment stub (`DIP_API_TOKEN`), and
the MILP only beats greedy by ~EUR 1k here — a knapsack shape greedy already
solves nearly optimally, which the README says out loud.
