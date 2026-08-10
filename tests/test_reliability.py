"""Supplier lead-time reliability tests.

Pins the contract of :mod:`dip.reliability`: the variable lead-time
safety-stock maths against hand-computed expectations, the base-case collapse
(every receipt on the quoted lead time -> the measured basis IS the quoted
basis, delta exactly zero), the exact decomposition identity (delay effect +
variability effect = delta, per SKU / per supplier / in total), the
cross-engine reuse identity (the quoted-basis safety stock reproduces
``dip.inventory``'s, SKU by SKU and in total), the rng-stream append guard
(the supplier draws disturb no previously published number), determinism,
purity, validation, and the ``/api/reliability`` endpoint (cached, override
and 400 paths). Everything is deterministic, so seed numbers are pinned exactly.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from app import app as flask_app
from dip.analytics import abc_xyz, kpis
from dip.data import (
    N_SUPPLIERS,
    RECEIPTS_PER_SKU,
    SEED,
    _build_suppliers_and_receipts,
    build_dataset,
)
from dip.inventory import (
    HOLDING_COST_RATE,
    SERVICE_LEVEL_MATRIX,
    _z_for,
    inventory_policy,
)
from dip.reliability import (
    DEFAULT_TOLERANCE_DAYS,
    THIN_SAMPLE_RECEIPTS,
    ReliabilityError,
    _grade_for,
    _sku_reliability,
    supplier_reliability,
)


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


@pytest.fixture(autouse=True)
def _always_restore_synthetic():
    """Any test that touches state leaves the next one on the synthetic data."""
    yield
    flask_app.test_client().post("/api/reset")


def _perfect_receipts(ds):
    """A receipt history where every delivery lands exactly on the quote."""
    rec = ds.receipts
    return {
        "sku_id": rec["sku_id"].copy(),
        "supplier_id": rec["supplier_id"].copy(),
        "quoted_days": rec["quoted_days"].copy(),
        "actual_days": rec["quoted_days"].copy(),
    }


# ---------------------------------------------------------------------------
# 1. Single-SKU maths against hand-computed expectations
# ---------------------------------------------------------------------------


def test_variability_only_case_matches_hand_computation():
    # dm=100/mo, ds=30/mo, quoted 30d, receipts [24,30,30,36]:
    # mean = 30 (== quoted), std(ddof=1) = sqrt(72/3) = sqrt(24) = 4.89898
    # z(0.95) = 1.644854; SS_q = z*30*sqrt(30/30) = 49.35
    # sigma_LTD = sqrt((30/30)*900 + 100^2*(4.89898/30)^2) = sqrt(1166.67) = 34.157
    # SS_m = z*34.157 = 56.18 -> delta 6.84, ALL variability (mean == quote)
    r = _sku_reliability(
        demand_mean=100.0, demand_std=30.0, quoted_days=30.0,
        actual_days=np.array([24.0, 30.0, 30.0, 36.0]), cost=4.0,
        z=_z_for(0.95), tolerance_days=2.0,
    )
    assert r["n_receipts"] == 4
    assert r["measured_mean_days"] == 30.0
    assert r["measured_std_days"] == pytest.approx(4.9, abs=0.01)
    assert r["lead_time_cv"] == pytest.approx(0.1633, abs=0.0002)
    assert r["safety_stock_quoted"] == pytest.approx(49.3, abs=0.05)
    assert r["safety_stock_measured"] == pytest.approx(56.2, abs=0.05)
    assert r["delta_units"] == pytest.approx(6.8, abs=0.1)
    assert r["delay_effect_eur"] == pytest.approx(0.0, abs=0.01)  # mean == quote
    assert r["variability_effect_eur"] == pytest.approx(r["delta_eur"], abs=0.01)
    # on-time at 2d grace: 24, 30, 30 pass; 36 > 32 fails -> 3/4
    assert r["on_time_rate"] == 0.75
    assert r["mean_delay_days"] == 0.0
    assert r["reorder_point_quoted"] == pytest.approx(149.3, abs=0.05)  # 100 + SS_q
    assert r["reorder_point_measured"] == pytest.approx(156.2, abs=0.05)  # 100 + SS_m


def test_delay_only_case_matches_hand_computation():
    # Every receipt at 33d against a 30d quote: mean 33, std 0.
    # SS_at_mean = z*30*sqrt(33/30) = 49.3456*1.048809 = 51.75; var effect = 0.
    r = _sku_reliability(
        demand_mean=100.0, demand_std=30.0, quoted_days=30.0,
        actual_days=np.array([33.0, 33.0, 33.0, 33.0]), cost=4.0,
        z=_z_for(0.95), tolerance_days=2.0,
    )
    assert r["measured_std_days"] == 0.0
    assert r["safety_stock_measured"] == pytest.approx(51.8, abs=0.05)
    assert r["variability_effect_eur"] == 0.0
    assert r["delay_effect_eur"] == pytest.approx(r["delta_eur"], abs=0.01)
    assert r["on_time_rate"] == 0.0  # 33 > 30 + 2 every time
    assert r["mean_delay_days"] == 3.0


def test_early_but_erratic_supplier_still_costs_safety_stock():
    """Deliveries EARLY on average can still require MORE safety stock: the
    variability term outweighs a negative delay term. The scorecard's central
    lesson — averages hide the wobble the buffer must cover."""
    r = _sku_reliability(
        demand_mean=100.0, demand_std=30.0, quoted_days=30.0,
        actual_days=np.array([12.0, 40.0, 14.0, 42.0]), cost=4.0,  # mean 27 < 30
        z=_z_for(0.95), tolerance_days=2.0,
    )
    assert r["mean_delay_days"] < 0  # early on average
    assert r["delay_effect_eur"] < 0  # the mean alone would FREE capital
    assert r["delta_eur"] > 0  # ...but the wobble costs more than that


def test_collapse_receipts_on_quote_reproduce_the_quoted_basis_exactly():
    r = _sku_reliability(
        demand_mean=100.0, demand_std=30.0, quoted_days=30.0,
        actual_days=np.array([30.0, 30.0, 30.0, 30.0]), cost=4.0,
        z=_z_for(0.95), tolerance_days=0.0,
    )
    assert r["safety_stock_measured"] == r["safety_stock_quoted"]
    assert r["delta_eur"] == 0.0
    assert r["delay_effect_eur"] == 0.0
    assert r["variability_effect_eur"] == 0.0
    assert r["on_time_rate"] == 1.0
    assert r["reorder_point_measured"] == r["reorder_point_quoted"]


def test_grade_bands():
    assert _grade_for(0.99) == "A"
    assert _grade_for(0.95) == "A"
    assert _grade_for(0.949) == "B"
    assert _grade_for(0.85) == "B"
    assert _grade_for(0.75) == "C"
    assert _grade_for(0.70) == "C"
    assert _grade_for(0.69) == "D"


# ---------------------------------------------------------------------------
# 2. Base-case collapse on the whole portfolio
# ---------------------------------------------------------------------------


def test_perfect_deliveries_collapse_to_the_inventory_engine_exactly():
    """With every receipt on the quoted lead time, the measured basis IS the
    quoted basis, and both reproduce dip.inventory's safety stock in total."""
    ds = build_dataset()
    perfect = dataclasses.replace(ds, receipts=_perfect_receipts(ds))
    rel = supplier_reliability(perfect)
    t = rel["totals"]
    assert t["delta_eur"] == 0.0
    assert t["delay_effect_eur"] == 0.0
    assert t["variability_effect_eur"] == 0.0
    assert t["on_time_rate"] == 1.0
    assert t["safety_stock_measured_eur"] == t["safety_stock_quoted_eur"]
    assert t["safety_stock_quoted_eur"] == inventory_policy(ds)["totals"]["safety_stock_eur"]
    assert all(s["grade"] == "A" for s in rel["suppliers"])


# ---------------------------------------------------------------------------
# 3. Cross-engine reuse: the quoted basis IS the inventory engine's number
# ---------------------------------------------------------------------------


def test_quoted_basis_reproduces_inventory_safety_stock_per_sku_and_total():
    ds = build_dataset()
    rel = supplier_reliability(ds)
    inv = inventory_policy(ds)
    assert rel["totals"]["safety_stock_quoted_eur"] == inv["totals"]["safety_stock_eur"]
    inv_by_sku = {row["sku_id"]: row for row in inv["skus"]}
    for row in rel["skus"]:
        assert row["safety_stock_quoted"] == inv_by_sku[row["sku_id"]]["safety_stock"]
        assert row["safety_stock_quoted_eur"] == inv_by_sku[row["sku_id"]]["safety_stock_eur"]


def test_service_targets_follow_the_same_abc_xyz_matrix():
    ds = build_dataset()
    rel = supplier_reliability(ds, abc_xyz_result=abc_xyz(ds))
    for row in rel["skus"]:
        assert row["service_level"] == SERVICE_LEVEL_MATRIX[row["cell"]]


def test_passing_cached_abc_xyz_gives_identical_output():
    ds = build_dataset()
    assert supplier_reliability(ds) == supplier_reliability(ds, abc_xyz_result=abc_xyz(ds))


# ---------------------------------------------------------------------------
# 4. Decomposition and aggregation identities
# ---------------------------------------------------------------------------


def test_per_sku_delta_splits_exactly_into_delay_plus_variability():
    for row in supplier_reliability(build_dataset())["skus"]:
        assert row["delta_eur"] == pytest.approx(
            row["delay_effect_eur"] + row["variability_effect_eur"], abs=0.02
        )
        assert row["delta_eur"] == pytest.approx(
            row["safety_stock_measured_eur"] - row["safety_stock_quoted_eur"], abs=0.02
        )
        assert row["variability_effect_eur"] >= 0.0  # variance never helps


def test_supplier_rows_sum_to_totals_exactly():
    rel = supplier_reliability(build_dataset())
    t = rel["totals"]
    for field, key in [
        ("safety_stock_quoted_eur", "safety_stock_quoted_eur"),
        ("safety_stock_measured_eur", "safety_stock_measured_eur"),
        ("delta_eur", "delta_eur"),
        ("delay_effect_eur", "delay_effect_eur"),
        ("variability_effect_eur", "variability_effect_eur"),
    ]:
        assert t[field] == pytest.approx(
            sum(s[key] for s in rel["suppliers"]), abs=0.05
        )
    assert sum(s["n_skus"] for s in rel["suppliers"]) == t["n_skus"] == 200
    assert sum(s["n_receipts"] for s in rel["suppliers"]) == t["n_receipts"] == 2400


def test_totals_delta_is_delay_plus_variability_and_holding_cost_is_priced():
    t = supplier_reliability(build_dataset())["totals"]
    assert t["delta_eur"] == pytest.approx(
        t["delay_effect_eur"] + t["variability_effect_eur"], abs=0.02
    )
    assert t["extra_holding_cost_eur"] == pytest.approx(
        t["delta_eur"] * HOLDING_COST_RATE, abs=0.02
    )


def test_rows_are_ranked_by_consequence():
    rel = supplier_reliability(build_dataset())
    sup_deltas = [s["delta_eur"] for s in rel["suppliers"]]
    sku_deltas = [s["delta_eur"] for s in rel["skus"]]
    assert sup_deltas == sorted(sup_deltas, reverse=True)
    assert sku_deltas == sorted(sku_deltas, reverse=True)


# ---------------------------------------------------------------------------
# 5. Seed pins (deterministic, so exact)
# ---------------------------------------------------------------------------


def test_fixture_totals_are_exact_on_the_seed():
    t = supplier_reliability(build_dataset())["totals"]
    assert t["n_suppliers"] == 10
    assert t["n_skus"] == 200
    assert t["n_skus_without_receipts"] == 0
    assert t["n_receipts"] == 2400
    assert t["on_time_rate"] == 0.7429
    assert t["safety_stock_quoted_eur"] == 16282.28
    assert t["safety_stock_measured_eur"] == 18626.01
    assert t["delta_eur"] == 2343.72
    assert t["delay_effect_eur"] == 575.87
    assert t["variability_effect_eur"] == 1767.85
    assert t["extra_holding_cost_eur"] == 585.93
    assert t["worst_supplier"] == "SUP-06"


def test_fixture_scorecard_rows_are_exact_on_the_seed():
    rel = supplier_reliability(build_dataset())
    by_id = {s["supplier_id"]: s for s in rel["suppliers"]}
    # the star performer: on-time and predictable, near-zero consequence
    best = by_id["SUP-04"]
    assert (best["grade"], best["n_skus"], best["on_time_rate"]) == ("A", 45, 0.9926)
    assert best["delta_eur"] == 68.37
    # the early-but-erratic one: negative delay effect, big variability bill
    erratic = by_id["SUP-02"]
    assert erratic["mean_delay_days"] == -0.45
    assert erratic["delay_effect_eur"] == -79.17
    assert erratic["delta_eur"] == 542.03
    # the single-SKU supplier is flagged as a thin sample, not hidden
    thin = by_id["SUP-08"]
    assert thin["thin_sample"] is True
    assert thin["n_receipts"] == RECEIPTS_PER_SKU < THIN_SAMPLE_RECEIPTS
    assert sum(1 for s in rel["suppliers"] if s["thin_sample"]) == 1


# ---------------------------------------------------------------------------
# 6. rng-stream append guard: no previously published number moved
# ---------------------------------------------------------------------------


def test_existing_headline_numbers_unchanged_by_supplier_generation():
    """The supplier/receipt draws are appended after every other entity, so
    every number published before this feature must still be byte-identical."""
    ds = build_dataset()
    k = kpis(ds)
    assert k["revenue"] == 4788971.2  # the README's EUR 4,788,971
    assert k["gross_margin"] == 3257507.06
    assert k["gross_margin_pct"] == 0.6802
    assert len(ds.order_lines) == 647  # the cross-sell baskets, undisturbed
    assert inventory_policy(ds)["totals"]["working_capital_eur"] == 127421.39


def test_supplier_builder_is_deterministic():
    ds = build_dataset()
    a_sup, a_rec = _build_suppliers_and_receipts(np.random.default_rng(SEED), ds.skus)
    b_sup, b_rec = _build_suppliers_and_receipts(np.random.default_rng(SEED), ds.skus)
    assert a_sup == b_sup
    assert all(np.array_equal(a_rec[k], b_rec[k]) for k in a_rec)
    assert len(a_sup) == N_SUPPLIERS
    assert len(a_rec["sku_id"]) == len(ds.skus) * RECEIPTS_PER_SKU


# ---------------------------------------------------------------------------
# 7. Tolerance semantics, determinism, purity, validation, availability
# ---------------------------------------------------------------------------


def test_tolerance_rescores_on_time_but_never_touches_the_safety_stock():
    ds = build_dataset()
    strict = supplier_reliability(ds, tolerance_days=0)
    loose = supplier_reliability(ds, tolerance_days=10)
    assert strict["totals"]["on_time_rate"] < loose["totals"]["on_time_rate"]
    for field in (
        "safety_stock_quoted_eur", "safety_stock_measured_eur",
        "delta_eur", "delay_effect_eur", "variability_effect_eur",
    ):
        assert strict["totals"][field] == loose["totals"][field]


def test_deterministic_across_runs():
    ds = build_dataset()
    assert supplier_reliability(ds) == supplier_reliability(ds)
    assert supplier_reliability(ds, tolerance_days=5) == supplier_reliability(ds, tolerance_days=5)


def test_does_not_mutate_the_shared_base_dataset():
    ds = build_dataset()
    skus_before = [dict(s) for s in ds.skus]
    actual_before = ds.receipts["actual_days"].copy()
    supplier_reliability(ds, tolerance_days=7)
    assert [dict(s) for s in ds.skus] == skus_before
    assert np.array_equal(ds.receipts["actual_days"], actual_before)


@pytest.mark.parametrize("bad", ["abc", -0.1, 30.5, float("inf"), float("nan"), True, None])
def test_tolerance_validation_rejects_bad_values(bad):
    with pytest.raises(ReliabilityError):
        supplier_reliability(build_dataset(), tolerance_days=bad)


def test_dataset_without_receipts_reports_unavailable_not_invented():
    ds = build_dataset()
    for stripped in (
        dataclasses.replace(ds, receipts=None),
        dataclasses.replace(ds, suppliers=None),
    ):
        rel = supplier_reliability(stripped)
        assert rel["available"] is False
        assert "receipt history" in rel["note"]
        assert "suppliers" not in rel  # no fabricated scorecards


# ---------------------------------------------------------------------------
# 8. The /api/reliability endpoint
# ---------------------------------------------------------------------------


def test_endpoint_serves_cached_default_with_full_schema(client):
    resp = client.get("/api/reliability")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {
        "available", "params", "totals", "suppliers", "skus",
        "provenance", "data_source", "note", "caveats",
    }
    assert body["available"] is True
    assert body["params"]["tolerance_days"] == DEFAULT_TOLERANCE_DAYS
    assert body["totals"]["n_suppliers"] == 10
    assert len(body["suppliers"]) == 10
    assert len(body["skus"]) == 200
    assert body["data_source"]["synthetic"] is True
    # the cached endpoint equals the engine on the synthetic dataset
    assert body["totals"]["delta_eur"] == supplier_reliability(build_dataset())["totals"]["delta_eur"]


def test_endpoint_tolerance_override_and_determinism(client):
    resp = client.get("/api/reliability?tolerance_days=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["params"]["tolerance_days"] == 0.0
    assert body["totals"]["on_time_rate"] == 0.3337  # strict zero-grace scoring
    # the safety-stock consequence is tolerance-invariant
    assert body["totals"]["delta_eur"] == 2343.72
    # deterministic across calls (byte-identical payloads)
    assert client.get("/api/reliability").data == client.get("/api/reliability").data
    assert (
        client.get("/api/reliability?tolerance_days=0").data
        == client.get("/api/reliability?tolerance_days=0").data
    )


def test_endpoint_rejects_bad_tolerance(client):
    assert client.get("/api/reliability?tolerance_days=abc").status_code == 400
    assert client.get("/api/reliability?tolerance_days=-1").status_code == 400
    assert client.get("/api/reliability?tolerance_days=31").status_code == 400
    assert client.get("/api/reliability?tolerance_days=nan").status_code == 400
    # a bad request never disturbs the served dataset
    assert client.get("/api/health").get_json()["skus"] == 200


def test_endpoint_honoured_by_the_api_token_guard(client, monkeypatch):
    monkeypatch.setenv("DIP_API_TOKEN", "s3cret")
    assert client.get("/api/reliability").status_code == 401
    ok = client.get("/api/reliability", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_module_smoke_values_are_finite():
    """Guard the __main__ smoke path's inputs (module uses stdlib math)."""
    t = supplier_reliability(build_dataset())["totals"]
    assert math.isfinite(t["delta_eur"]) and math.isfinite(t["on_time_rate"])
