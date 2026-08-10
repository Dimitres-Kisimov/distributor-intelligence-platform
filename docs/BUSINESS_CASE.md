# Business Case — Distributor Intelligence Platform

## Situation

**Meridian Industrial Supply** is a mid-size MRO (maintenance, repair &
operations) distributor: ~200 active SKUs across eight categories, a fleet
serving 52 recurring business customers across five regions, and about
EUR 4.8M of revenue over the trailing 24 months at a 68% gross margin. Like most
distributors of its size, it runs on ERP transactional data plus institutional
memory. Pricing is cost-plus and rarely revisited. The stocked range grew by
accretion, not decision. Van routes are planned by the depot supervisor each
morning.

*(Meridian is a stand-in name; all figures are from the platform's synthetic,
deterministic dataset.)*

## Problem, quantified

Three decisions are being left on the table every week:

- **Pricing is blunt.** Prices ignore per-SKU elasticity, so elastic lines are
  over-priced and inelastic lines under-priced. Modelled gross-profit left
  uncaptured: **~EUR 95,600/yr (+6.4%)**.
- **Assortment is unmanaged against working capital.** Choosing the stocked set
  by hand instead of against a capital budget leaves margin on the table versus
  the optimal mix. Modelled gap vs a disciplined heuristic: **~EUR 1,000/yr**
  (small here — greedy is already near-optimal on this cost structure — but the
  MILP guarantees it, and the framework generalises to tighter budgets).
- **Delivery routing is greedy by hand.** The morning nearest-neighbour plan
  runs **560 km**; a solved CVRP runs **420 km** — **140 km (25%) saved per
  run**. Over ~250 runs/yr at EUR 1.15/km that is **~EUR 40,300/yr**.

On top of that, purchasing and safety stock are set without a real forecast.
The platform closes that gap from both ends: it supplies a backtested demand
forecast (**MASE 0.38**, beats seasonal-naive) *and* turns the demand signal into
a **continuous-review inventory policy** — per-SKU safety stock, reorder point
and EOQ, with cycle service levels differentiated by ABC-XYZ class. On the seed
that policy holds **EUR 127,421** of inventory working capital at **5.5 turns**
and a **99.9%** demand-weighted fill rate; it is the rigorous working-capital
figure the assortment MILP only proxies with a crude cycle-stock term. (It is a
stocking *policy*, not an incremental-€ lever, so it is deliberately kept out of
the uplift total below.)

And the lead times that policy trusts are themselves unaudited: buffers are
sized off the *quoted* lead time while suppliers deliver late — or early but
erratically. Measuring the PO receipt history (10 suppliers, 2,400 receipts)
shows the safety stock the vendor master implies (**EUR 16,282**) understates
what measured lead-time behaviour actually requires (**EUR 18,626, +14.4%**) —
and three quarters of that gap is lead-time *variability*, not average delay.
The supplier scorecards put a EUR figure and a letter grade on each vendor, so
the next supplier review argues about predictability with numbers. (Also a
policy consequence, not an uplift lever — kept out of the total below.)

## Solution

One platform that turns that ERP data into decisions:

- Descriptive analytics (KPIs, ABC-XYZ, RFM, margin bridge) for visibility.
- A backtested Holt-Winters forecast for planning.
- Three optimisation engines — MILP assortment, elasticity pricing, OR-Tools
  CVRP routing — each measured against a fair baseline.
- A continuous-review inventory policy (safety stock / reorder point / EOQ) that
  turns the ABC-XYZ classes and lead times into stocking decisions and the
  working capital they commit.
- Supplier-reliability scorecards measured from the PO receipt history — on-time
  rate, mean delay, lead-time variability — with the safety-stock EUR consequence
  of each supplier's behaviour, split into a delay effect and a variability
  effect.
- A prescription layer that rolls the lifts into one number and a ranked set of
  action cards, exported as an executive PDF and Excel workbook.

## ROI

| Lever | Modelled annual impact |
| --- | --- |
| Pricing (elasticity-guided) | EUR 95,609 |
| Routing (CVRP vs nearest-neighbour) | EUR 40,339 |
| Assortment (MILP vs greedy) | EUR 1,024 |
| **Total expected annual uplift** | **EUR 136,972 — 8.4% of annual gross margin** |

The platform is a few hundred lines of Python plus a Flask app; against a
six-figure recurring uplift the payback is immediate. The value is the decision
discipline, not the software cost.

## Stakeholders

- **Commercial / Pricing lead** — owns the elasticity pricing recommendations.
- **Category / Merchandising** — owns the assortment-vs-capital trade-off.
- **Logistics / Depot supervisor** — owns the routing plan.
- **Purchasing / Inventory planner** — owns the safety-stock, reorder-point and
  order-quantity policy and the service levels behind it, plus the supplier
  scorecards and what each vendor's lead-time behaviour costs in buffer stock.
- **Finance** — owns the forecast, the working-capital budget, and the margin
  bridge.
- **Managing Director** — reads the one-page expected-uplift view and the
  action cards.

## Deliverable

A running decision-intelligence dashboard (`http://localhost:5000`) plus two
board-ready exports generated from the identical plan:
`deliverables/executive_review.pdf` (four-page executive review) and
`deliverables/executive_workbook.xlsx` (summary, forecast, revenue, ABC-XYZ,
assortment, pricing, RFM, and actions sheets). Both are produced by
`python -m dip --deliverables`.

## Honest footing

All data is synthetic and deterministic. The numbers are as-measured on the
seed and illustrate the method; they are not a benchmark on real data and make
no state-of-the-art claim. OTIF is a modelled proxy, and the routing figure
annualises a single representative run. The routing solver terminates on a
fixed solution budget (not a wall clock), and the dashboard, the action cards
and both exports are generated from one shared plan per scenario — so every
figure in this document is stable across runs and identical in every artefact.
