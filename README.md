# Distributor Intelligence Platform

I built this to answer a question I kept running into: a mid-size industrial
distributor sits on a mountain of transactional data — every SKU, every
delivery, every month of demand — and yet the people who set prices, choose
what to stock, and plan the vans are usually working off gut feel and last
quarter's spreadsheet. This platform is my attempt at the MRO command center
those decisions deserve: one place where the descriptive numbers, the forecast,
and the actual optimisation of price / assortment / routing all live behind a
single API and a single dashboard.

Everything runs on a **synthetic, deterministic dataset** (seeded NumPy, built
in `dip/data.py`) — roughly 200 SKUs across 8 categories, 52 delivery
customers, and 24 months of demand history. It represents no real company. That
choice is deliberate: it means the dashboard, the exported PDF, and the test
suite all agree on the same numbers on any machine, and I can quote figures
below that you can reproduce exactly by running the code.

## What it does

Four things, layered:

1. **Describe** — headline KPIs, revenue breakdowns (region / category /
   channel), an ABC-XYZ inventory classification, RFM customer segmentation,
   and a price/volume/mix gross-margin bridge.
2. **Forecast** — monthly revenue projected six months out with an additive
   Holt-Winters model, with the error *earned* through a rolling-origin
   backtest rather than asserted.
3. **Optimise** — three prescriptive engines, each shipped next to a fair
   baseline so the lift is always quantified: a MILP assortment (vs a greedy
   density heuristic), elasticity-based pricing (vs status quo), and an
   OR-Tools CVRP for the delivery run (vs nearest-neighbour construction).
4. **Prescribe** — the composition layer that rolls the three lifts into one
   expected annual € uplift and a set of ranked "recommended action" cards, and
   emits an executive PDF and Excel workbook from the exact same plan.

## The numbers it currently produces

These come straight out of the engines on the seeded dataset — run
`python -m dip --deliverables` or the module `__main__` blocks and you'll get
the same values:

| Metric | Value |
| --- | --- |
| Revenue (24 months) | EUR 4,788,971 |
| Gross margin | EUR 3,257,507 (68.0%) |
| YoY revenue growth | +9.9% |
| OTIF service level (modelled) | 85.7% |
| Forecast accuracy | MASE 0.38 over 9 rolling folds (Holt-Winters additive) |
| Next-month revenue forecast | EUR 204,618 |
| Assortment: MILP vs greedy | EUR 935,527 vs EUR 934,503 margin (+EUR 1,024) on 108 SKUs under a EUR 10,558 working-capital budget |
| Pricing uplift | +EUR 95,609 (+6.4% of gross profit) |
| Routing | 420 km vs 560 km baseline — 140 km / 25.0% saved per run, 6 vehicles |
| **Expected annual uplift** | **EUR 136,972 (8.4% of annual gross margin)** |

A note of honesty on those: the assortment MILP only edges out the greedy
heuristic by ~EUR 1k here — knapsack-style problems with this cost structure
are ones greedy already solves nearly optimally, and I'd rather show that than
pretend the gap is huge. The MASE below 1.0 means the model beats a
seasonal-naive benchmark on this seeded series; it is not a claim about real
demand. The routing saving is a single day's run annualised over an assumed 250
runs at EUR 1.15/km. The CVRP search terminates on a fixed solution budget
rather than a wall-clock limit, so the 140 km figure — and every number built
on it, including the EUR 136,972 headline — is identical on every solve; an
earlier build used a 3-second time limit, which let the same process quote two
different km depending on machine load.

## Running it

```bash
pip install -r requirements.txt

# 1) the dashboard
python app.py                 # -> http://localhost:5000
#    (or: python -m dip)

# 2) the executive deliverables
python -m dip --deliverables  # writes deliverables/executive_review.pdf + .xlsx

# 3) containerised
docker compose up             # serves on http://localhost:5000 via gunicorn
```

Tests and lint:

```bash
python -m ruff check .
python -m pytest -q
```

## Architecture

```
dip/data.py        one seeded synthetic dataset (single source of truth)
   |
dip/analytics.py   KPIs, breakdowns, ABC-XYZ, RFM, margin bridge
dip/forecast.py    Holt-Winters + rolling-origin MASE backtest
dip/optimize.py    assortment MILP / elasticity pricing / OR-Tools CVRP
dip/prescribe.py   composes the lifts into one plan + action cards
dip/exports.py     PDF (matplotlib) and Excel (openpyxl) from that plan
   |
app.py             Flask: expensive engines cached at startup, one JSON API
   |
templates/, static/  the executive command-center dashboard (hand-built charts)
```

The Flask layer runs every expensive computation once at boot and caches it, so
the dashboard stays snappy; only the parameterised optimisers (assortment
budget, price guardrail) compute per request. Routing and pricing are solved
exactly once at startup, the prescription plan is composed *from* those cached
results, and the PDF/Excel endpoints and the `--deliverables` CLI reuse the
same plan object — the routes view, the action cards and the exports cannot
quote different numbers for the same scenario.

## Screenshots

![Command Center dashboard — KPI tiles, revenue and demand forecast with confidence band, revenue by region, gross-margin bridge and the ABC-XYZ portfolio matrix](docs/img/command-center.png)

Captured from a fresh local run (`python app.py`, synthetic seeded data).

## Synthetic data & limitations

- **All data is synthetic and deterministic.** It models no real distributor,
  customer, or transaction, and there is no external dataset dependency.
- The figures above are *as-measured on this seed* — they are illustrative of
  the method, not a benchmark against real-world data or a claim of
  state-of-the-art performance.
- OTIF is a modelled proxy from lead-time reliability, not observed service
  data. The margin bridge's price term is a residual (prices are static in the
  synthetic world) and is reported as such rather than fabricated.
- The routing saving annualises one representative delivery run; real fleets
  vary day to day.

## Author & licence

Dimitres Kisimov, 2026. © 2026 Dimitres Kisimov — all rights reserved; published for portfolio review. See LICENSE. See `CREDITS.md` for the open-source
components and `docs/BUSINESS_CASE.md` for the business framing.
