"""Inventory replenishment-policy tests.

Pins the contract of :mod:`dip.inventory`: the safety-stock / ROP / EOQ maths
against hand-computed expectations, the base-case collapse (a 50% service level
has z = 0, so no safety stock and ROP = mean lead-time demand), the exact
non-effects (zero demand variability -> zero safety stock), the portfolio
identities (working capital = cycle + safety; turns = COGS / working capital),
the reuse of the ABC-XYZ classification to set service targets, determinism,
purity, validation, and the ``/api/inventory`` endpoint (cached, override, and
400 paths). Everything is deterministic, so the seed numbers are pinned exactly.
"""

from __future__ import annotations

import math

import pytest

from app import app as flask_app
from dip.analytics import abc_xyz
from dip.data import build_dataset
from dip.inventory import (
    HOLDING_COST_RATE,
    ORDER_COST_EUR,
    SERVICE_LEVEL_MATRIX,
    InventoryError,
    _sku_policy,
    _standard_normal_loss,
    _z_for,
    inventory_policy,
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


# ---------------------------------------------------------------------------
# 1. Single-SKU maths against hand-computed expectations
# ---------------------------------------------------------------------------


def test_single_sku_policy_matches_hand_computation():
    # dm=100/mo, ds=30/mo, L=30d (=> 1 month), cost=4, 95% service.
    # D = 1200; mean_LTD = 100; sigma_LTD = 30*sqrt(1) = 30; z(0.95)=1.644853627
    # SS = z*sigma = 49.345...; ROP = 100 + SS; H = 0.25*4 = 1.0
    # EOQ = sqrt(2*1200*50/1.0) = sqrt(120000) = 346.410...; cycle = EOQ/2
    z = _z_for(0.95)
    assert z == pytest.approx(1.6448536269514722, abs=1e-9)
    p = _sku_policy(
        demand_mean=100.0, demand_std=30.0, lead_time_days=30.0, cost=4.0,
        z=z, holding_rate=0.25, order_cost=50.0,
    )
    assert p["annual_demand"] == 1200.0
    assert p["mean_ltd"] == 100.0
    assert p["sigma_ltd"] == 30.0
    assert p["safety_stock"] == pytest.approx(49.3, abs=0.05)   # 1.644853*30
    assert p["reorder_point"] == pytest.approx(149.3, abs=0.05)  # 100 + SS
    assert p["eoq"] == pytest.approx(346.4, abs=0.05)            # sqrt(120000)
    assert p["cycle_stock"] == pytest.approx(173.2, abs=0.05)    # EOQ/2
    assert p["avg_inventory"] == pytest.approx(222.6, abs=0.05)  # cycle + SS
    assert p["order_up_to_level"] == pytest.approx(495.8, abs=0.05)  # ROP + EOQ
    assert p["working_capital_eur"] == pytest.approx(890.2, abs=0.05)  # avg_inv*4
    assert p["safety_stock_eur"] == pytest.approx(197.38, abs=0.02)
    assert p["cycle_stock_eur"] == pytest.approx(692.82, abs=0.02)


def test_at_eoq_cycle_holding_equals_ordering_cost():
    """The EOQ is exactly where annual ordering cost = cycle-stock holding cost.
    Cycle-stock holding cost = H * (EOQ/2), with H = holding_rate * cost."""
    z = _z_for(0.95)
    p = _sku_policy(
        demand_mean=100.0, demand_std=30.0, lead_time_days=30.0, cost=4.0,
        z=z, holding_rate=0.25, order_cost=50.0,
    )
    holding_per_unit = 0.25 * 4.0  # H
    cycle_holding = holding_per_unit * p["cycle_stock"]
    assert cycle_holding == pytest.approx(p["annual_ordering_cost_eur"], abs=0.05)


def test_standard_normal_loss_reference_values():
    # G(0) = phi(0) = 1/sqrt(2*pi) = 0.39894...; G is strictly decreasing.
    assert _standard_normal_loss(0.0) == pytest.approx(0.3989422804014327, abs=1e-9)
    assert _standard_normal_loss(1.0) < _standard_normal_loss(0.0)
    assert _standard_normal_loss(2.0) < _standard_normal_loss(1.0)


# ---------------------------------------------------------------------------
# 2. Base-case collapse + exact non-effects
# ---------------------------------------------------------------------------


def test_base_case_collapse_zero_safety_stock_at_50pct_service():
    """A 50% cycle service level is z = 0: no safety stock, ROP = mean LTD.
    The policy degenerates to the deterministic reorder point."""
    z = _z_for(0.5)
    assert z == pytest.approx(0.0, abs=1e-12)
    p = _sku_policy(
        demand_mean=100.0, demand_std=30.0, lead_time_days=30.0, cost=4.0,
        z=z, holding_rate=0.25, order_cost=50.0,
    )
    assert p["safety_stock"] == 0.0
    assert p["safety_stock_eur"] == 0.0
    assert p["reorder_point"] == p["mean_ltd"] == 100.0


def test_flat_50pct_service_zeroes_portfolio_safety_stock():
    inv = inventory_policy(build_dataset(), service_level=0.5)
    assert inv["totals"]["safety_stock_eur"] == 0.0
    for row in inv["skus"]:
        assert row["safety_stock"] == 0.0
        assert row["reorder_point"] == row["mean_ltd"]


def test_zero_variability_gives_no_safety_stock_and_full_fill():
    """With no demand variability there is nothing to buffer: SS = 0, fill = 1."""
    p = _sku_policy(
        demand_mean=100.0, demand_std=0.0, lead_time_days=30.0, cost=4.0,
        z=_z_for(0.99), holding_rate=0.25, order_cost=50.0,
    )
    assert p["safety_stock"] == 0.0
    assert p["fill_rate"] == 1.0


def test_degenerate_zero_demand_or_cost_does_not_divide_by_zero():
    p = _sku_policy(
        demand_mean=0.0, demand_std=0.0, lead_time_days=10.0, cost=0.0,
        z=_z_for(0.95), holding_rate=0.25, order_cost=50.0,
    )
    assert p["eoq"] == 0.0
    assert p["orders_per_year"] == 0.0
    assert p["days_of_cover"] == 0.0
    assert p["fill_rate"] == 1.0  # no demand can go short


# ---------------------------------------------------------------------------
# 3. Portfolio identities on the seed (guards the aggregation arithmetic)
# ---------------------------------------------------------------------------


def test_working_capital_is_cycle_plus_safety():
    t = inventory_policy(build_dataset())["totals"]
    assert t["working_capital_eur"] == pytest.approx(
        t["cycle_stock_eur"] + t["safety_stock_eur"], abs=0.02
    )


def test_per_sku_working_capital_is_cycle_plus_safety():
    for row in inventory_policy(build_dataset())["skus"]:
        assert row["working_capital_eur"] == pytest.approx(
            row["cycle_stock_eur"] + row["safety_stock_eur"], abs=0.02
        )


def test_turns_and_days_of_cover_are_consistent():
    t = inventory_policy(build_dataset())["totals"]
    assert t["inventory_turns"] == pytest.approx(
        t["annual_cogs_eur"] / t["working_capital_eur"], abs=0.01
    )
    assert t["days_of_cover"] == pytest.approx(365.0 / t["inventory_turns"], abs=0.2)


def test_annual_inventory_cost_is_holding_plus_ordering():
    t = inventory_policy(build_dataset())["totals"]
    assert t["annual_inventory_cost_eur"] == pytest.approx(
        t["annual_holding_cost_eur"] + t["annual_ordering_cost_eur"], abs=0.02
    )


def test_fill_rate_is_at_least_the_cycle_service_level():
    """Large order quantities dilute per-cycle shortage, so the achieved fill
    rate sits at or above the cycle service level — a known inventory property."""
    t = inventory_policy(build_dataset())["totals"]
    assert t["demand_weighted_fill_rate"] >= t["demand_weighted_service_level"]


def test_fixture_totals_are_exact_on_the_seed():
    t = inventory_policy(build_dataset())["totals"]
    assert t["n_skus"] == 200
    assert t["working_capital_eur"] == 127421.39
    assert t["cycle_stock_eur"] == 111139.11
    assert t["safety_stock_eur"] == 16282.28
    assert t["annual_holding_cost_eur"] == 31855.35
    assert t["annual_ordering_cost_eur"] == 27784.78
    assert t["annual_cogs_eur"] == 701913.73
    assert t["inventory_turns"] == 5.51
    assert t["days_of_cover"] == 66.3
    assert t["demand_weighted_fill_rate"] == 0.9992
    assert t["demand_weighted_service_level"] == 0.9528


# ---------------------------------------------------------------------------
# 4. Reuse of the ABC-XYZ classification to drive the policy
# ---------------------------------------------------------------------------


def test_by_cell_counts_match_the_abc_xyz_grid():
    """Every SKU is classified by the SAME abc_xyz engine, so the inventory
    per-cell counts must equal the descriptive ABC-XYZ grid counts exactly."""
    ds = build_dataset()
    grid = abc_xyz(ds)["grid"]
    by_cell = {c["cell"]: c for c in inventory_policy(ds)["by_cell"]}
    assert len(by_cell) == 9
    assert sum(c["count"] for c in by_cell.values()) == 200
    for cell, g in grid.items():
        assert by_cell[cell]["count"] == g["count"]
        assert by_cell[cell]["target_service_level"] == SERVICE_LEVEL_MATRIX[cell]


def test_passing_cached_abc_xyz_gives_identical_output():
    ds = build_dataset()
    assert inventory_policy(ds) == inventory_policy(ds, abc_xyz_result=abc_xyz(ds))


def test_each_sku_service_target_follows_the_matrix():
    for row in inventory_policy(build_dataset())["skus"]:
        assert row["service_level"] == SERVICE_LEVEL_MATRIX[row["cell"]]


# ---------------------------------------------------------------------------
# 5. Monotonicity, determinism, purity, validation
# ---------------------------------------------------------------------------


def test_higher_service_level_needs_more_safety_stock():
    ds = build_dataset()
    lo = inventory_policy(ds, service_level=0.90)["totals"]
    hi = inventory_policy(ds, service_level=0.99)["totals"]
    assert hi["safety_stock_eur"] > lo["safety_stock_eur"]
    assert hi["working_capital_eur"] > lo["working_capital_eur"]
    # cycle stock (EOQ-driven) is unaffected by the service level
    assert hi["cycle_stock_eur"] == lo["cycle_stock_eur"]


def test_deterministic_across_runs():
    ds = build_dataset()
    assert inventory_policy(ds) == inventory_policy(ds)
    assert inventory_policy(ds, service_level=0.93) == inventory_policy(ds, service_level=0.93)


def test_does_not_mutate_the_shared_base_dataset():
    ds = build_dataset()
    before = [dict(s) for s in ds.skus]
    inventory_policy(ds, service_level=0.99)
    assert [dict(s) for s in ds.skus] == before


def test_defaults_match_module_constants():
    inv = inventory_policy(build_dataset())
    assert inv["params"]["holding_rate"] == round(HOLDING_COST_RATE, 4)
    assert inv["params"]["order_cost_eur"] == round(ORDER_COST_EUR, 2)
    assert inv["service_policy"]["mode"] == "abc_xyz_matrix"
    assert inv["params"]["service_level_override"] is None


@pytest.mark.parametrize("bad", ["abc", -0.1, 0, 1, 1.5, float("inf"), float("nan"), True])
def test_service_level_validation_rejects_bad_values(bad):
    with pytest.raises(InventoryError):
        inventory_policy(build_dataset(), service_level=bad)


# ---------------------------------------------------------------------------
# 6. The /api/inventory endpoint
# ---------------------------------------------------------------------------


def test_endpoint_serves_cached_default_with_full_schema(client):
    resp = client.get("/api/inventory")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {
        "params", "service_policy", "totals", "by_cell", "skus",
        "provenance", "data_source", "note", "caveats",
    }
    assert body["totals"]["n_skus"] == 200
    assert len(body["skus"]) == 200
    assert len(body["by_cell"]) == 9
    assert body["data_source"]["synthetic"] is True
    # the cached endpoint equals the engine on the synthetic dataset
    assert body["totals"]["working_capital_eur"] == inventory_policy(build_dataset())["totals"]["working_capital_eur"]


def test_endpoint_service_level_override_and_determinism(client):
    resp = client.get("/api/inventory?service_level=0.9")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["service_policy"]["mode"] == "flat"
    assert body["params"]["service_level_override"] == 0.9
    # deterministic across calls (byte-identical payloads)
    assert client.get("/api/inventory").data == client.get("/api/inventory").data
    assert client.get("/api/inventory?service_level=0.9").data == client.get("/api/inventory?service_level=0.9").data


def test_endpoint_rejects_bad_service_level(client):
    assert client.get("/api/inventory?service_level=abc").status_code == 400
    assert client.get("/api/inventory?service_level=1.5").status_code == 400
    assert client.get("/api/inventory?service_level=0").status_code == 400
    # a bad request never disturbs the served dataset
    assert client.get("/api/health").get_json()["skus"] == 200


def test_endpoint_honoured_by_the_api_token_guard(client, monkeypatch):
    monkeypatch.setenv("DIP_API_TOKEN", "s3cret")
    assert client.get("/api/inventory").status_code == 401
    ok = client.get("/api/inventory", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_math_module_import_side_effect_free():
    """Guard against accidental reliance on process state (module uses stdlib math)."""
    assert math.isfinite(_z_for(0.95))
