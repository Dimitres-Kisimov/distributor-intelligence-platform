# DAX measures — Distributor Intelligence Platform

Paste these into Power BI Desktop (right-click the `fact_sales` table →
**New measure**) after importing the star schema from `powerbi/data/`. They are
written against the model in `README.md`: a `fact_sales` grain-of-(month × SKU)
fact related to `dim_sku`, `dim_category`, `dim_date`, plus a disconnected
one-row-per-metric `kpi_headline` table for plan-level scalars and a
disconnected `provenance` table that labels the data synthetic.

I group them into a dedicated `_Measures` table (Home → Enter Data → empty
table named `_Measures`) so they are easy to find, but any home table works.

> **All figures are computed on a seeded synthetic dataset** — a demonstration
> of dimensional modelling + DAX, not a claim about real company data.

---

## Helper pattern: read one scalar from `kpi_headline`

`kpi_headline` is a tall `(metric, value)` table. DAX has no user-defined
functions, so each headline measure inlines this filter:

```DAX
CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "<metric_name>" )
```

---

## 1. Revenue (ties to the headline)

The additive fact measure and its headline scalar — they are equal to the cent
(the fact rows partition the same monthly ledger the KPI is computed from).

```DAX
Revenue = SUM ( fact_sales[revenue_eur] )
```

```DAX
Revenue (headline) =
CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "revenue_eur" )
```

```DAX
Revenue Ties =
IF ( ROUND ( [Revenue], 2 ) = ROUND ( [Revenue (headline)], 2 ), "✓ tie", "✗ drift" )
```

## 2. COGS & Gross Margin

```DAX
COGS = SUM ( fact_sales[cogs_eur] )
```

```DAX
Gross Margin = SUM ( fact_sales[gross_margin_eur] )
```

```DAX
Gross Margin % = DIVIDE ( [Gross Margin], [Revenue] )
```

`[Gross Margin]` equals the `gross_margin_eur` headline scalar to the cent, and
`[Gross Margin %]` reconstructs the `gross_margin_pct` headline.

## 3. Total Expected Uplift (EUR / year)

The plan's headline: pricing + assortment + routing.

```DAX
Total Expected Uplift =
CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "expected_uplift_eur" )
```

Reconstructed additively (proves the levers tie out):

```DAX
Total Expected Uplift (built) = [Pricing Uplift] + [Assortment Uplift] + [Routing Uplift]
```

```DAX
Pricing Uplift =
CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "pricing_uplift_eur" )
```

```DAX
Assortment Uplift =
CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "assortment_uplift_eur" )
```

```DAX
Routing Uplift =
CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "routing_uplift_eur" )
```

`[Pricing Uplift]` also reconstructs from the SKU grain, since the per-SKU
profit deltas roll up to the pricing lever:

```DAX
Pricing Uplift (from SKUs) = SUM ( dim_sku[pricing_profit_delta_eur] )
```

## 4. Gross Margin % from the SKU cost (uses RELATED)

Demand-weighted margin over the carried range, reaching into `dim_sku` for the
unit cost:

```DAX
Carried Gross Margin % =
VAR _rev =
    SUMX ( FILTER ( fact_sales, RELATED ( dim_sku[carried_in_plan] ) = 1 ), fact_sales[revenue_eur] )
VAR _gm =
    SUMX ( FILTER ( fact_sales, RELATED ( dim_sku[carried_in_plan] ) = 1 ), fact_sales[gross_margin_eur] )
RETURN
    DIVIDE ( _gm, _rev )
```

## 5. SKUs Carried & Carry Rate

```DAX
SKUs Carried = CALCULATE ( DISTINCTCOUNT ( dim_sku[sku] ), dim_sku[carried_in_plan] = 1 )
```

```DAX
Carry Rate % = DIVIDE ( [SKUs Carried], DISTINCTCOUNT ( dim_sku[sku] ) )
```

## 6. Capital Utilization %

Working capital the plan commits against the assortment budget (both scalars):

```DAX
Capital Utilization % =
DIVIDE (
    CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "assortment_capital_used_eur" ),
    CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "assortment_budget_eur" )
)
```

## 7. Avg Price Change % (repriced range)

```DAX
Avg Price Change % =
AVERAGEX ( FILTER ( dim_sku, dim_sku[carried_in_plan] = 1 ), dim_sku[price_change_pct] )
```

## 8. Forecast MASE

Held-out mean absolute scaled error vs the seasonal-naive baseline (< 1 beats
naive):

```DAX
Forecast MASE =
CALCULATE ( SUM ( kpi_headline[value] ), kpi_headline[metric] = "forecast_mase" )
```

```DAX
Beats Naive = IF ( [Forecast MASE] < 1, "Yes", "No" )
```

## 9. YoY Revenue Growth (time intelligence)

`dim_date` spans 24 months, so a real prior-year period exists — mark it as a
date table (Table tools → Mark as date table → `dim_date[date]`) first.

```DAX
Revenue PY = CALCULATE ( [Revenue], DATEADD ( dim_date[date], -12, MONTH ) )
```

```DAX
Revenue YoY % = DIVIDE ( [Revenue] - [Revenue PY], [Revenue PY] )
```

(Rolling last-12-vs-prior-12 reproduces the `yoy_growth` headline scalar.)

---

### Notes on correctness

- `DIVIDE` is used everywhere instead of `/` for safe BLANK handling on a zero
  denominator.
- `RELATED` reaches from `fact_sales` into `dim_sku`; this requires the
  single-direction `dim_sku[sku] 1 —— * fact_sales[sku]` relationship from the
  README.
- `kpi_headline` and `provenance` are intentionally **disconnected** (no
  relationship) — they hold plan-level scalars and data labels that have no
  SKU/date grain, read by an explicit `metric = "…"` / `key = "…"` filter.
- The additive fact measures (`[Revenue]`, `[COGS]`, `[Gross Margin]`) equal
  their `kpi_headline` scalars to the cent by construction (the fact rows are
  the monthly ledger the KPIs are computed from) — `[Revenue Ties]` shows it.
