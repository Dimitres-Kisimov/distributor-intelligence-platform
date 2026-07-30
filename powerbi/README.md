# Power BI pack — Distributor Intelligence Platform

This folder is a **Power BI Desktop showcase** built from the platform's plan
and monthly ledger. It needs no Power BI tenant, licence, or gateway to
*produce* — the CSVs and the DAX are generated and written out here so the
dimensional model can be imported and reviewed. Honest framing: I don't ship a
`.pbix` (that needs the Desktop app to author), I ship the **star schema + DAX +
a build spec** so the modelling work is fully reproducible by anyone with Power
BI Desktop.

> **All data is synthetic** — generated deterministically by `dip/data.py`
> (seeded NumPy). It models no real company. The `provenance.csv` table carries
> that label into the model itself.

## What's here

```
powerbi/
  build_star.py        generates the CSVs below by importing the dip package
  data/
    fact_sales.csv          grain = month × SKU (the additive measures aggregate this)
    dim_sku.csv             SKU attributes + the SKU-level plan decisions
    dim_category.csv        category attributes (counts, avg elasticity / cost / price)
    dim_date.csv            24-month calendar (mark as a date table)
    kpi_headline.csv        disconnected 1-row-per-metric table of plan-level scalars
    provenance.csv          disconnected data-labelling table (synthetic, seed, author)
  DAX_measures.md      the KPIs as real, paste-ready DAX
  README.md            this file
```

Regenerate any time the engines change (deterministic — byte-identical output):

```bash
python powerbi/build_star.py   # writes powerbi/data/*.csv
```

The script imports `dip` directly (no intermediate JSON), so the CSVs can never
drift from what the dashboard and exports quote. It ends by checking that the
`fact_sales` revenue / COGS / gross-margin sums tie to the `kpi_headline`
scalars **to the cent** (a test pins the same tie).

## The model (star schema)

```
                +----------------+
                |   dim_date     |
                | date_key (PK)  |
                +--------+-------+
                         | 1
                         | *
+-------------+   *   +--v----------------+   *   +---------------+
|  dim_sku    |-------|   fact_sales      |-------|  dim_category |
| sku (PK)    | 1   * |   sku (FK)        | *   1 | category (PK) |
+-------------+       |   category (FK)   |       +---------------+
                      |   date_key (FK)   |
                      |   units           |
                      |   revenue_eur     |
                      |   cogs_eur        |
                      |   gross_margin_eur|
                      +-------------------+

  kpi_headline  (disconnected — plan-level scalars, read by metric name)
  provenance    (disconnected — data label / seed / author)
```

## Import steps (Power BI Desktop)

1. **Get Data → Text/CSV** and load all six files from `powerbi/data/`.
   Accept the auto-detected types; set `date_key` to Whole Number and
   `dim_date[date]` to Date.
2. **Model view → create relationships** (all single-direction, one-to-many
   from the dim to the fact):
   - `dim_sku[sku]` 1 — * `fact_sales[sku]`
   - `dim_category[category]` 1 — * `fact_sales[category]`
   - `dim_date[date_key]` 1 — * `fact_sales[date_key]`
   - leave `kpi_headline` and `provenance` disconnected (no relationship).
   - Mark `dim_date` as a date table (Table tools → Mark as date table →
     `dim_date[date]`) so time intelligence works.
3. **Add the measures** from `DAX_measures.md` (create an empty `_Measures`
   table to home them, then paste each measure).

## Report pages to build

### Page 1 — Executive Overview
- KPI cards: **Revenue**, **Gross Margin %**, **Total Expected Uplift**,
  **SKUs Carried**, **Forecast MASE**.
- **Uplift waterfall** (Waterfall visual): a small "lever" field
  (Pricing / Assortment / Routing) on the axis, value = the matching measure;
  or read the `kpi_headline` rows directly.
- **Revenue trend** (line): `[Revenue]` by `dim_date[date]` — the seasonal +
  trend shape of the ledger; add `[Revenue YoY %]` as a secondary card.

### Page 2 — Assortment & Pricing
- **Matrix**: rows `dim_category[category]` → `dim_sku[sku]`, values
  `[Revenue]`, `[Gross Margin]`, `[Carried Gross Margin %]`, `[SKUs Carried]`.
- **Scatter**: x = `dim_sku[unit_margin_eur]`, y = `dim_sku[pricing_profit_delta_eur]`,
  legend = `carried_in_plan` — where the elasticity-priced margin comes from.
- **Card row**: `[Capital Utilization %]`, `[Carry Rate %]`,
  `[Avg Price Change %]`.

### Page 3 — Revenue & Category Mix
- **Stacked column**: `[Revenue]` by `dim_date[date]` (axis) and
  `dim_sku[category]` (legend) — category mix over the 24 months.
- **Bar**: `[Gross Margin]` by `dim_category[category]`, sorted descending.
- **Tie tile**: `[Revenue Ties]` — a live ✓/✗ that the additive fact equals the
  headline scalar to the cent.

## Why this demonstrates Power BI ability without a tenant

Everything a reviewer needs to judge dimensional-modelling and DAX skill is
here in text: a normalized star (fact + conformed dimensions + two disconnected
scalar tables), single-direction relationships, a marked date table, and
measures that use `CALCULATE`, `DIVIDE`, `SUMX`/`AVERAGEX`, `RELATED`,
`DISTINCTCOUNT`, and a `DATEADD` time-intelligence pattern. Loading the six CSVs
and pasting the DAX reproduces the whole model in a few minutes — no licence
required, and the additive measures tie to the plan headline to the cent.

Author: Dimitres Kisimov, 2026. All rights reserved (portfolio review).
