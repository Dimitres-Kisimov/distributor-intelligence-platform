"""Driver-sensitivity (tornado what-if) tests.

Pins the contract of :mod:`dip.sensitivity`: the base ties to the served plan,
the composed-uplift identity survives every perturbation, routing is invariant
to all three drivers, the mechanical non-effects (a demand shock on margin %, a
cost/lead-time shock on revenue) come out exactly zero, the tornado ranks
correctly, the shocks validate, the base dataset is never mutated, and the
``/api/sensitivity`` endpoint serves it (cached, custom-shock, and 400 paths).
Everything is deterministic, so the numbers are pinned exactly on the seed.
"""

from __future__ import annotations

import pytest

from app import app as flask_app
from dip.analytics import kpis
from dip.data import build_dataset
from dip.optimize import optimize_routes
from dip.prescribe import build_plan
from dip.sensitivity import (
    DRIVERS,
    OUTCOME_KEYS,
    SensitivityError,
    _perturb,
    driver_sensitivity,
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


def _synth() -> dict:
    ds = build_dataset()
    routes = optimize_routes(ds)
    return {
        "ds": ds,
        "routes": routes,
        "s": driver_sensitivity(ds, routes=routes),
        "plan": build_plan(ds, routes=routes),
        "k": kpis(ds),
    }


# ---------------------------------------------------------------------------
# 1. Structure + the base ties to the live plan
# ---------------------------------------------------------------------------


def test_structure_has_three_drivers_and_all_outcome_fields():
    s = driver_sensitivity(build_dataset())
    assert [d["id"] for d in s["drivers"]] == [d[0] for d in DRIVERS] == ["demand", "lead_time", "cost"]
    assert set(s["base"]) == set(OUTCOME_KEYS)
    for d in s["drivers"]:
        assert {"id", "label", "mechanism", "low_mult", "high_mult", "low", "high", "metrics"} <= set(d)
        assert set(d["low"]) == set(d["high"]) == set(d["metrics"]) == set(OUTCOME_KEYS)
        for m in d["metrics"].values():
            assert {"base", "low", "high", "low_delta", "high_delta", "swing"} <= set(m)
    assert s["shock"] == 0.10 and s["shock_label"] == "±10%"


def test_base_ties_to_the_served_plan_and_kpis():
    ctx = _synth()
    # base built from the cached plan/KPIs equals the served plan/KPIs exactly
    s_cached = driver_sensitivity(
        ctx["ds"], routes=ctx["routes"], base_plan=ctx["plan"], base_kpis=ctx["k"]
    )
    assert s_cached["base"]["expected_uplift_eur"] == ctx["plan"]["expected_uplift_eur"]
    assert s_cached["base"]["gross_margin"] == ctx["k"]["gross_margin"]
    assert s_cached["base"]["revenue"] == ctx["k"]["revenue"]
    assert s_cached["base"]["routing_uplift_eur"] == ctx["plan"]["levers"]["routing"]
    # and the fresh-solve base (no cache) reproduces it — the two paths agree
    assert ctx["s"]["base"] == s_cached["base"]


# ---------------------------------------------------------------------------
# 2. Identities that must survive every perturbation
# ---------------------------------------------------------------------------


def test_uplift_equals_sum_of_three_levers_in_every_perturbed_run():
    """The reconciliation guard's core identity must hold on the shocked data
    too, or a perturbed row could quote a figure the platform disagrees with."""
    s = driver_sensitivity(build_dataset())
    for d in s["drivers"]:
        for side in (d["low"], d["high"]):
            levers = (
                side["pricing_uplift_eur"]
                + side["assortment_uplift_eur"]
                + side["routing_uplift_eur"]
            )
            # the three levers are each published rounded to the cent, so their
            # sum sits within rounding of the (separately rounded) uplift total
            assert abs(round(levers, 2) - side["expected_uplift_eur"]) <= 0.02


def test_routing_lever_is_invariant_to_all_three_drivers():
    """Routing depends on the customer/geography layer, not on these SKU
    drivers, so its lever is identical in every row (swing exactly zero)."""
    s = driver_sensitivity(build_dataset())
    base_routing = s["base"]["routing_uplift_eur"]
    for d in s["drivers"]:
        assert d["low"]["routing_uplift_eur"] == base_routing
        assert d["high"]["routing_uplift_eur"] == base_routing
        assert d["metrics"]["routing_uplift_eur"]["swing"] == 0.0


def test_demand_shock_leaves_gross_margin_pct_unchanged():
    """A pure volume shock scales revenue and COGS together — margin % holds."""
    s = driver_sensitivity(build_dataset())
    dem = next(d for d in s["drivers"] if d["id"] == "demand")
    assert dem["low"]["gross_margin_pct"] == dem["high"]["gross_margin_pct"] == s["base"]["gross_margin_pct"]
    assert dem["metrics"]["gross_margin_pct"]["swing"] == 0.0
    # but demand DOES move revenue and the uplift (volume feeds pricing/assortment)
    assert dem["metrics"]["revenue"]["swing"] > 0
    assert dem["metrics"]["expected_uplift_eur"]["swing"] > 0


def test_cost_shock_holds_revenue_and_erodes_margin():
    """Prices are held, so a cost shock never moves revenue; higher cost lowers
    gross margin monotonically."""
    s = driver_sensitivity(build_dataset())
    cost = next(d for d in s["drivers"] if d["id"] == "cost")
    assert cost["low"]["revenue"] == cost["high"]["revenue"] == s["base"]["revenue"]
    assert cost["metrics"]["revenue"]["swing"] == 0.0
    assert cost["high"]["gross_margin"] < s["base"]["gross_margin"] < cost["low"]["gross_margin"]


def test_lead_time_shock_touches_service_not_the_revenue_ledger():
    """Lead time drives working capital and OTIF, never revenue or gross margin."""
    s = driver_sensitivity(build_dataset())
    lt = next(d for d in s["drivers"] if d["id"] == "lead_time")
    assert lt["low"]["revenue"] == lt["high"]["revenue"] == s["base"]["revenue"]
    assert lt["low"]["gross_margin"] == lt["high"]["gross_margin"] == s["base"]["gross_margin"]
    assert lt["metrics"]["revenue"]["swing"] == 0.0
    # longer lead time -> lower modelled OTIF (down), shorter -> higher (up)
    assert lt["high"]["otif"] <= s["base"]["otif"] <= lt["low"]["otif"]


# ---------------------------------------------------------------------------
# 3. Tornado ranking + exact fixture numbers (guards the arithmetic itself)
# ---------------------------------------------------------------------------


def test_tornado_is_sorted_by_uplift_swing_descending():
    s = driver_sensitivity(build_dataset())
    swings = [t["swing"] for t in s["tornado"]]
    assert swings == sorted(swings, reverse=True)
    assert s["most_sensitive_driver"] == s["tornado"][0]["id"]
    # each tornado swing equals that driver's uplift metric swing
    by_id = {d["id"]: d for d in s["drivers"]}
    for t in s["tornado"]:
        assert t["swing"] == by_id[t["id"]]["metrics"]["expected_uplift_eur"]["swing"]


def test_fixture_numbers_are_exact_on_the_seed():
    s = driver_sensitivity(build_dataset())
    b = s["base"]
    assert b["expected_uplift_eur"] == 136972.2
    assert b["gross_margin"] == 3257507.06
    assert b["gross_margin_pct"] == 0.6802
    assert b["revenue"] == 4788971.2
    assert b["routing_uplift_eur"] == 40339.12

    m = {d["id"]: d for d in s["drivers"]}
    demand = m["demand"]["metrics"]["expected_uplift_eur"]
    assert (demand["low_delta"], demand["high_delta"], demand["swing"]) == (-10130.1, 8895.15, 19025.25)
    cost = m["cost"]["metrics"]["expected_uplift_eur"]
    assert (cost["low_delta"], cost["high_delta"], cost["swing"]) == (-146.56, 1481.78, 1628.34)
    assert m["cost"]["low"]["gross_margin"] == 3410653.38
    assert m["cost"]["high"]["gross_margin"] == 3104360.48
    assert m["lead_time"]["metrics"]["expected_uplift_eur"]["swing"] == 179.66

    # demand dominates the tornado on this seed
    assert [t["id"] for t in s["tornado"]] == ["demand", "cost", "lead_time"]
    assert s["most_sensitive_driver"] == "demand"


# ---------------------------------------------------------------------------
# 4. Determinism, purity, validation
# ---------------------------------------------------------------------------


def test_deterministic_across_runs():
    ds = build_dataset()
    routes = optimize_routes(ds)
    assert driver_sensitivity(ds, routes=routes) == driver_sensitivity(ds, routes=routes)


def test_perturb_does_not_mutate_the_shared_base_dataset():
    ds = build_dataset()
    before_skus = [dict(s) for s in ds.skus]
    before_rev = ds.monthly["revenue"].copy()
    _perturb(ds, demand_mult=1.5, lead_time_mult=3.0, cost_mult=2.0)
    assert [dict(s) for s in ds.skus] == before_skus
    assert (ds.monthly["revenue"] == before_rev).all()


def test_identity_perturbation_reproduces_the_base_ledger():
    ds = build_dataset()
    identity = _perturb(ds, demand_mult=1.0, lead_time_mult=1.0, cost_mult=1.0)
    assert kpis(identity)["revenue"] == kpis(ds)["revenue"]
    assert kpis(identity)["gross_margin"] == kpis(ds)["gross_margin"]


@pytest.mark.parametrize("bad", ["abc", -0.1, 0, 1, 1.5, float("inf"), float("nan"), True])
def test_shock_validation_rejects_bad_values(bad):
    with pytest.raises(SensitivityError):
        driver_sensitivity(build_dataset(), shock=bad)


def test_shock_scales_the_swings():
    """A smaller shock produces a strictly smaller uplift swing (monotone)."""
    ds = build_dataset()
    routes = optimize_routes(ds)
    small = driver_sensitivity(ds, shock=0.05, routes=routes)
    big = driver_sensitivity(ds, shock=0.10, routes=routes)
    small_dem = next(d for d in small["drivers"] if d["id"] == "demand")
    big_dem = next(d for d in big["drivers"] if d["id"] == "demand")
    assert small_dem["metrics"]["expected_uplift_eur"]["swing"] < big_dem["metrics"]["expected_uplift_eur"]["swing"]


# ---------------------------------------------------------------------------
# 5. The /api/sensitivity endpoint
# ---------------------------------------------------------------------------


def test_endpoint_serves_cached_default_with_full_schema(client):
    resp = client.get("/api/sensitivity")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {
        "shock", "shock_label", "base", "drivers", "tornado",
        "most_sensitive_driver", "provenance", "data_source", "note", "caveats",
    }
    assert body["shock"] == 0.10
    assert len(body["drivers"]) == 3
    assert body["data_source"]["synthetic"] is True
    assert "synthetic" in body["provenance"].lower()
    # the cached endpoint equals the engine on the synthetic dataset
    assert body["base"]["expected_uplift_eur"] == driver_sensitivity(build_dataset())["base"]["expected_uplift_eur"]


def test_endpoint_custom_shock_and_determinism(client):
    a = client.get("/api/sensitivity?shock=0.05")
    assert a.status_code == 200 and a.get_json()["shock"] == 0.05
    # deterministic across calls (byte-identical payloads)
    assert client.get("/api/sensitivity").data == client.get("/api/sensitivity").data
    assert client.get("/api/sensitivity?shock=0.05").data == client.get("/api/sensitivity?shock=0.05").data


def test_endpoint_rejects_bad_shock(client):
    assert client.get("/api/sensitivity?shock=abc").status_code == 400
    assert client.get("/api/sensitivity?shock=1.5").status_code == 400
    assert client.get("/api/sensitivity?shock=0").status_code == 400
    # a bad request never disturbs the served dataset
    assert client.get("/api/health").get_json()["skus"] == 200


def test_endpoint_honoured_by_the_api_token_guard(client, monkeypatch):
    monkeypatch.setenv("DIP_API_TOKEN", "s3cret")
    assert client.get("/api/sensitivity").status_code == 401
    ok = client.get("/api/sensitivity", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
