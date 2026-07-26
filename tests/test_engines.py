"""Engine tests: determinism, forecasting, optimisation, prescription, exports.

Everything is seeded, so these assertions are exact where they can be and
inequality-based (optimal >= baseline) where the point is the *lift*.
"""

from __future__ import annotations

import numpy as np

from dip import analytics, exports, forecast, optimize, prescribe
from dip.data import build_dataset


def _fresh_dataset():
    """Rebuild the dataset bypassing the module-level lru_cache."""
    build_dataset.cache_clear()
    ds = build_dataset()
    build_dataset.cache_clear()
    return ds


def test_dataset_shape():
    ds = build_dataset()
    assert len(ds.skus) == 200
    assert ds.n_months == 24
    assert len(ds.monthly["sku_id"]) == 200 * 24
    assert len(ds.customers) == 52


def test_dataset_determinism_identical_kpis():
    """Same seed -> byte-identical KPIs across two independent builds."""
    ds1 = _fresh_dataset()
    k1 = analytics.kpis(ds1)
    ds2 = _fresh_dataset()
    k2 = analytics.kpis(ds2)
    assert k1["revenue"] == k2["revenue"]
    assert k1["gross_margin"] == k2["gross_margin"]
    assert k1["revenue_series"] == k2["revenue_series"]


def test_kpis_are_coherent():
    k = analytics.kpis(build_dataset())
    assert k["revenue"] > 0
    assert abs(k["gross_margin"] - (k["revenue"] - k["cogs"])) < 1.0
    assert 0.0 < k["gross_margin_pct"] < 1.0
    assert 0.80 <= k["otif"] <= 0.99


def test_abc_xyz_grid_totals():
    az = analytics.abc_xyz(build_dataset())
    assert sum(cell["count"] for cell in az["grid"].values()) == 200
    assert len(az["per_sku"]) == 200
    assert az["total_revenue"] > 0


def test_abc_xyz_per_sku_backs_the_drill_down():
    """Each per-SKU row carries the name/category the drill-down table shows,
    and the per-SKU rows must re-total to the grid cell they belong to."""
    ds = build_dataset()
    az = analytics.abc_xyz(ds)
    names = {s["sku_id"]: (s["name"], s["category"]) for s in ds.skus}
    for row in az["per_sku"]:
        assert (row["name"], row["category"]) == names[row["sku_id"]]
        assert row["cell"] == row["abc"] + row["xyz"]
    for cell, agg in az["grid"].items():
        rows = [r for r in az["per_sku"] if r["cell"] == cell]
        assert len(rows) == agg["count"]
        assert abs(sum(r["revenue"] for r in rows) - agg["revenue"]) < 1.0


def test_rfm_covers_every_customer():
    ds = build_dataset()
    rfm = analytics.rfm_segments(ds)
    assert len(rfm["per_customer"]) == len(ds.customers)
    assert sum(s["count"] for s in rfm["segments"]) == len(ds.customers)


def test_rfm_per_customer_backs_the_drill_down():
    """Drill-down rows expose the raw recency/frequency behind the quintile
    scores, and per-customer monetary re-totals to the segment bars."""
    ds = build_dataset()
    rfm = analytics.rfm_segments(ds)
    raw = {c["customer_id"]: c for c in ds.customers}
    for row in rfm["per_customer"]:
        src = raw[row["customer_id"]]
        assert row["recency_months"] == src["recency_months"]
        assert row["frequency"] == src["frequency"] >= 1
    for seg in rfm["segments"]:
        rows = [r for r in rfm["per_customer"] if r["segment"] == seg["segment"]]
        assert len(rows) == seg["count"]
        assert abs(sum(r["monetary"] for r in rows) - seg["monetary"]) < 1.0


def test_margin_bridge_steps():
    mb = analytics.margin_bridge(build_dataset())
    labels = [s["label"] for s in mb["steps"]]
    assert labels == ["Volume", "Mix", "Price"]
    # bridge closes: baseline + steps == current (within rounding)
    assert abs(mb["baseline"] + sum(s["value"] for s in mb["steps"]) - mb["current"]) < 1.0


def test_forecast_shapes_and_mase():
    ds = build_dataset()
    fc = forecast.forecast_revenue(ds, horizon=6)
    assert len(fc["forecast"]) == 6
    assert len(fc["lower"]) == 6 == len(fc["upper"])
    assert len(fc["history"]) == ds.n_months
    assert len(fc["forecast_months"]) == 6
    assert fc["mase"] is not None
    assert 0.0 < fc["mase"] < 3.0  # a sane, honestly-earned error
    assert fc["n_folds"] > 0


def test_forecast_band_ordering():
    fc = forecast.forecast_revenue(build_dataset())
    for lo, pt, hi in zip(fc["lower"], fc["forecast"], fc["upper"]):
        assert lo <= pt <= hi


def test_optimize_assortment_milp_beats_greedy():
    res = optimize.optimize_assortment(build_dataset())
    assert res["milp"]["margin"] >= res["greedy"]["margin"]
    assert res["uplift_vs_greedy"] >= 0
    assert res["milp"]["capital_used"] <= res["budget"] + 1e-6
    assert res["milp"]["count"] > 0


def test_optimize_assortment_respects_budget():
    ds = build_dataset()
    res = optimize.optimize_assortment(ds, budget=5000.0)
    assert res["milp"]["capital_used"] <= 5000.0 + 1e-6


def test_optimize_prices_uplift_nonnegative():
    p = optimize.optimize_prices(build_dataset())
    assert p["new_profit"] >= p["base_profit"]
    assert p["uplift"] >= 0
    assert len(p["recommendations"]) == 200


def test_optimize_routes_saves_distance():
    r = optimize.optimize_routes(build_dataset())
    assert r["optimized_km"] > 0
    assert r["baseline_km"] > 0
    assert r["optimized_km"] <= r["baseline_km"]
    assert r["km_saved"] >= 0
    assert r["n_vehicles_used"] >= 1


def test_optimize_routes_deterministic_across_solves():
    """Termination is a fixed solution budget, not a wall clock: two consecutive
    in-process solves must return byte-identical routes and km (the regression
    that once made the dashboard, prescribe cards and exports disagree)."""
    ds = build_dataset()
    assert optimize.optimize_routes(ds) == optimize.optimize_routes(ds)


def test_prescribe_build_plan_positive_uplift_and_cards():
    plan = prescribe.build_plan(build_dataset())
    assert plan["expected_uplift_eur"] > 0
    assert plan["baseline_gross_margin"] > 0
    assert len(plan["cards"]) == 4
    for c in plan["cards"]:
        assert {"id", "title", "detail", "impact_eur", "confidence", "lever"} <= set(c)
        assert isinstance(c["detail"], str) and c["detail"]
    # cards are sorted by impact descending
    impacts = [c["impact_eur"] for c in plan["cards"]]
    assert impacts == sorted(impacts, reverse=True)
    # total uplift equals the sum of the three monetised levers
    assert abs(sum(plan["levers"].values()) - plan["expected_uplift_eur"]) < 1.0


def test_prescribe_uplift_pct_is_an_annual_ratio():
    """Annual uplift must be quoted against *annual* gross margin (24 mo / 2)."""
    ds = build_dataset()
    plan = prescribe.build_plan(ds)
    k = analytics.kpis(ds)
    annual_margin = k["gross_margin"] * 12.0 / len(k["months"])
    assert abs(plan["expected_uplift_pct"] - plan["expected_uplift_eur"] / annual_margin) < 1e-3
    assert plan["annual_gross_margin"] == round(annual_margin, 2)


def test_prescribe_guardrail_flows_into_pricing_lever():
    ds = build_dataset()
    tight = prescribe.build_plan(ds, max_change=0.05)
    default = prescribe.build_plan(ds)
    assert tight["max_change"] == 0.05
    assert tight["levers"]["pricing"] <= default["levers"]["pricing"]
    # band accompanies the next-month point forecast for the board summary
    lo, hi = default["next_month_band"]
    assert lo <= default["next_month_revenue"] <= hi


def test_exports_build_pdf_nonempty_bytes():
    data = exports.build_pdf()
    assert isinstance(data, bytes)
    assert len(data) > 10_000
    assert data[:4] == b"%PDF"


def test_exports_build_excel_nonempty_bytes():
    data = exports.build_excel()
    assert isinstance(data, bytes)
    assert len(data) > 10_000
    assert data[:2] == b"PK"  # xlsx is a zip container


def test_exports_are_scenario_aware():
    """A non-default budget/guardrail must change the deliverable's content."""
    default = exports.build_excel()
    scenario = exports.build_excel(budget=8000.0, max_change=0.05)
    assert scenario[:2] == b"PK"
    assert scenario != default


def test_numpy_scalars_not_leaking_into_prescribe_totals():
    """expected_uplift_eur must be JSON-friendly (float, not np scalar edge)."""
    plan = prescribe.build_plan(build_dataset())
    assert float(plan["expected_uplift_eur"]) == plan["expected_uplift_eur"]
    assert np.isfinite(plan["expected_uplift_eur"])
