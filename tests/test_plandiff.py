"""Plan-diff tests — the reconciliation discipline, applied to change.

`dip.plandiff` claims something strong: that every euro between two plan runs'
headline uplifts is attributed to a named cause. These tests hold it to that.

1. the EUR bridge closes **exactly** — its named steps sum to the whole change,
   and the unattributed rounding line stays inside a bound derived from how many
   published figures it spans (so it is rounding, not a missing cause);
2. every change identity in the payload holds, and the identity set cannot
   quietly shrink;
3. the change tables are *complete*: the SKUs left out of the price-move table
   really do carry EUR 0, and the range diff is a true set difference;
4. the honest boundary is enforced — the inventory policy is diffed in
   working-capital EUR but is deliberately **not** in the uplift bridge, so a
   run pair that only moves the replenishment knobs shows a EUR 0 uplift delta;
5. the diff agrees with `dip.scenario` on the one number they both compute;
6. it is deterministic, and bad input is rejected rather than answered;
7. the pre-existing reconciliation guard is untouched by all of this.
"""

from __future__ import annotations

import json

import pytest

from dip import reconcile
from dip.analytics import abc_xyz
from dip.data import build_dataset
from dip.optimize import optimize_routes
from dip.plandiff import (
    COST_PER_KM,
    DEFAULT_CAPACITY_KG,
    DEFAULT_N_VEHICLES,
    ROUTING_RUNS_PER_YEAR,
    PlanDiffError,
    plan_diff,
)
from dip.scenario import compare_scenarios

# Every identity the ledger is expected to carry. Pinned so a future edit cannot
# make the diff look clean by quietly dropping a check.
EXPECTED_IDENTITIES = {
    "uplift_lever_bridge",
    "publication_rounding_within_two_cents",
    "pricing_attribution",
    "assortment_attribution",
    "assortment_set_closure",
    "routing_attribution",
    "routing_km_bridge",
    "bridge_closes",
    "rounding_within_tolerance",
    "inventory_sku_attribution",
    "inventory_split_bridge",
}


@pytest.fixture(scope="module")
def ctx():
    """One dataset, one default routing solve, one fleet cache for the module.

    Every diff below goes through this, so the whole file pays for the CVRP at
    most once per distinct fleet instead of once per test.
    """
    ds = build_dataset()
    routes = optimize_routes(ds)
    return {
        "ds": ds,
        "routes": routes,
        "abc": abc_xyz(ds),
        "fleets": {(DEFAULT_N_VEHICLES, DEFAULT_CAPACITY_KG): routes},
    }


def diff_for(ctx, run_a=None, run_b=None):
    return plan_diff(
        ctx["ds"],
        run_a=run_a,
        run_b=run_b,
        routes=ctx["routes"],
        routes_cache=ctx["fleets"],
        abc_xyz_result=ctx["abc"],
    )


@pytest.fixture(scope="module")
def default_diff(ctx):
    """The shipped default pair — the one the Change station opens on."""
    return diff_for(ctx)


# ---------------------------------------------------------------------------
# 1. The bridge closes, to the cent
# ---------------------------------------------------------------------------


def test_bridge_steps_sum_to_the_whole_change(default_diff):
    br = default_diff["bridge"]
    assert round(sum(s["eur"] for s in br["steps"]), 2) == br["delta_eur"]
    assert br["attributed_eur"] == br["delta_eur"]
    # and the endpoints really are the two plans' headline uplifts
    assert br["start"]["eur"] == default_diff["run_a"]["kpis"]["expected_uplift_eur"]
    assert br["end"]["eur"] == default_diff["run_b"]["kpis"]["expected_uplift_eur"]
    assert round(br["end"]["eur"] - br["start"]["eur"], 2) == br["delta_eur"]


def test_the_change_is_real_and_every_lever_moves(default_diff):
    """The shipped pair is worth showing: all three levers actually move."""
    by_id = {s["id"]: s["eur"] for s in default_diff["bridge"]["steps"]}
    assert by_id["pricing_moves"] != 0.0
    assert by_id["assortment_dropped"] != 0.0
    assert by_id["greedy_baseline_shift"] != 0.0
    assert by_id["routing"] != 0.0
    assert default_diff["bridge"]["delta_eur"] != 0.0


def test_rounding_line_is_only_rounding(default_diff):
    """The unattributed line is bounded by half a cent per published value."""
    br = default_diff["bridge"]
    assert abs(br["rounding_eur"]) <= br["rounding_tolerance_eur"]
    # it is genuinely small change, not a bucket absorbing a missing cause
    assert abs(br["rounding_eur"]) < 1.0
    assert abs(br["rounding_eur"]) < abs(br["delta_eur"]) * 1e-4
    # and it is the sum of its own independently measured parts
    assert round(sum(p["eur"] for p in br["rounding_parts"]), 4) == br["rounding_eur"]


def test_named_causes_carry_the_whole_change_without_the_rounding_line(default_diff):
    """Strip the rounding line and the attribution still explains ~all of it."""
    br = default_diff["bridge"]
    named = sum(s["eur"] for s in br["steps"] if s["id"] != "rounding")
    assert abs(named - br["delta_eur"]) <= br["rounding_tolerance_eur"]


# ---------------------------------------------------------------------------
# 2. The identity ledger
# ---------------------------------------------------------------------------


def test_every_identity_holds(default_diff):
    failed = [i["id"] for i in default_diff["identities"] if not i["ok"]]
    assert failed == [], f"plan-diff identities drifted: {failed}"
    assert default_diff["identities_ok"] is True
    assert default_diff["all_ok"] is True


def test_identity_coverage_cannot_shrink(default_diff):
    ids = {i["id"] for i in default_diff["identities"]}
    assert ids == EXPECTED_IDENTITIES
    for i in default_diff["identities"]:
        assert i["statement"], f"{i['id']} has no statement"
        assert i["op"] in ("==", "<=")


@pytest.mark.parametrize(
    "run_b",
    [
        {"budget": 12000.0},
        {"max_change": 0.05},
        {"service_level": 0.99},
        {"order_cost_eur": 120.0},
        {"capacity_kg": 2000.0},
        {},  # nothing changed at all
    ],
)
def test_identities_hold_for_single_knob_diffs(ctx, run_b):
    """One knob at a time — the bridge must still close for each of them."""
    res = diff_for(ctx, run_a={"budget": 9000.0}, run_b={"budget": 9000.0, **run_b})
    failed = [i["id"] for i in res["identities"] if not i["ok"]]
    assert failed == [], f"identities drifted for {run_b}: {failed}"
    assert res["bridge"]["attributed_eur"] == res["bridge"]["delta_eur"]


def test_a_run_diffed_against_itself_changes_nothing(ctx):
    res = diff_for(ctx, run_a={"budget": 9000.0}, run_b={"budget": 9000.0})
    assert res["changed_parameters"] == []
    assert res["headline"]["delta_eur"] == 0.0
    assert res["headline"]["n_skus_added"] == 0
    assert res["headline"]["n_skus_dropped"] == 0
    assert res["headline"]["n_price_moves"] == 0
    assert res["headline"]["n_policy_changes"] == 0
    assert res["all_ok"] is True


# ---------------------------------------------------------------------------
# 3. The change tables are complete
# ---------------------------------------------------------------------------


def test_range_diff_is_a_true_set_difference(ctx, default_diff):
    from dip.optimize import optimize_assortment

    sa = default_diff["run_a"]["spec"]["budget_eur"]
    sb = default_diff["run_b"]["spec"]["budget_eur"]
    carried_a = set(optimize_assortment(ctx["ds"], budget=sa)["carried_skus"])
    carried_b = set(optimize_assortment(ctx["ds"], budget=sb)["carried_skus"])
    added = {r["sku_id"] for r in default_diff["assortment"]["added"]}
    dropped = {r["sku_id"] for r in default_diff["assortment"]["dropped"]}
    assert added == carried_b - carried_a
    assert dropped == carried_a - carried_b
    # and the counts close against the two published range sizes
    t = default_diff["assortment"]["totals"]
    assert t["skus_carried_b"] == t["skus_carried_a"] + t["n_added"] - t["n_dropped"]


def test_range_rows_sum_to_their_bridge_lines(default_diff):
    a = default_diff["assortment"]
    by_id = {s["id"]: s["eur"] for s in default_diff["bridge"]["steps"]}
    assert round(sum(r["annual_margin_eur"] for r in a["added"]), 2) == by_id["assortment_added"]
    assert round(-sum(r["annual_margin_eur"] for r in a["dropped"]), 2) == by_id["assortment_dropped"]


def test_price_moves_leave_out_only_skus_that_carry_nothing(ctx, default_diff):
    """A SKU absent from the move table must have an identical recommendation."""
    from dip.optimize import optimize_prices

    ga = default_diff["run_a"]["spec"]["max_change"]
    gb = default_diff["run_b"]["spec"]["max_change"]
    rows_a = {r["sku_id"]: r for r in optimize_prices(ctx["ds"], max_change=ga)["recommendations"]}
    rows_b = {r["sku_id"]: r for r in optimize_prices(ctx["ds"], max_change=gb)["recommendations"]}
    moved = {m["sku_id"] for m in default_diff["pricing"]["moves"]}
    assert moved, "expected the guardrail change to move some recommendations"
    for sku_id, ra in rows_a.items():
        if sku_id in moved:
            continue
        rb = rows_b[sku_id]
        assert ra["price_new"] == rb["price_new"]
        assert ra["profit_delta"] == rb["profit_delta"]  # i.e. exactly EUR 0 of change


def test_price_move_rows_sum_to_the_pricing_bridge_line(default_diff):
    by_id = {s["id"]: s["eur"] for s in default_diff["bridge"]["steps"]}
    total = round(sum(m["delta_eur"] for m in default_diff["pricing"]["moves"]), 2)
    assert total == by_id["pricing_moves"]
    assert default_diff["pricing"]["totals"]["moved_profit_delta_eur"] == total


def test_policy_changes_leave_out_only_unchanged_skus(ctx, default_diff):
    from dip.inventory import inventory_policy

    sa, sb = default_diff["run_a"]["spec"], default_diff["run_b"]["spec"]
    pol = {}
    for tag, spec in (("a", sa), ("b", sb)):
        res = inventory_policy(
            ctx["ds"],
            service_level=spec["service_level"],
            holding_rate=spec["holding_rate"],
            order_cost=spec["order_cost_eur"],
            abc_xyz_result=ctx["abc"],
        )
        pol[tag] = {r["sku_id"]: r for r in res["skus"]}
    changed = {c["sku_id"] for c in default_diff["inventory"]["changes"]}
    for sku_id, ra in pol["a"].items():
        if sku_id in changed:
            continue
        rb = pol["b"][sku_id]
        for field in ("safety_stock", "reorder_point", "eoq", "working_capital_eur"):
            assert ra[field] == rb[field]


def test_policy_changes_report_rop_eoq_and_safety_stock(default_diff):
    """The three quantities a purchasing desk actually re-plans on."""
    changes = default_diff["inventory"]["changes"]
    assert changes, "expected the service/PO-cost change to move the policy"
    row = changes[0]
    for field in ("safety_stock_delta", "reorder_point_delta", "eoq_delta",
                  "working_capital_delta_eur"):
        assert field in row
    # the shipped pair moves the order cost, so EOQ must actually shift
    assert any(c["eoq_delta"] != 0 for c in changes)
    assert any(c["safety_stock_delta"] != 0 for c in changes)
    assert any(c["reorder_point_delta"] != 0 for c in changes)


# ---------------------------------------------------------------------------
# 4. The honest boundary: inventory is diffed, but not in the uplift bridge
# ---------------------------------------------------------------------------


def test_replenishment_only_change_moves_capital_but_not_the_uplift(ctx):
    """The plan's uplift is pricing + assortment + routing. Nothing else.

    Quietly folding an inventory saving into the headline is exactly the drift
    the platform's reconciliation guard exists to stop, so a run pair that moves
    only the replenishment knobs must post a EUR 0 uplift delta while still
    reporting the working capital it moved.
    """
    res = diff_for(
        ctx,
        run_a={"budget": 9000.0},
        run_b={"budget": 9000.0, "service_level": 0.99, "order_cost_eur": 150.0},
    )
    assert res["headline"]["delta_eur"] == 0.0
    assert all(s["eur"] == 0.0 for s in res["bridge"]["steps"])
    assert res["inventory"]["totals"]["n_changed"] > 0
    assert res["headline"]["working_capital_delta_eur"] != 0.0
    assert res["all_ok"] is True
    # and the payload says so in words, not only in numbers
    assert any("NOT in the uplift bridge" in c for c in res["caveats"])


def test_routing_is_shared_when_the_fleet_is_unchanged(ctx):
    res = diff_for(ctx, run_a={"budget": 9000.0}, run_b={"budget": 12000.0})
    assert res["routing"]["shared_solve"] is True
    assert res["headline"]["routing_changed"] is False
    by_id = {s["id"]: s["eur"] for s in res["bridge"]["steps"]}
    assert by_id["routing"] == 0.0  # exactly zero, not "close to zero"
    assert res["routing"]["totals"]["km_saved_delta"] == 0.0
    assert res["routing"]["totals"]["n_stops_regrouped"] == 0


def test_routing_change_is_priced_at_the_documented_rate(default_diff):
    t = default_diff["routing"]["totals"]
    assert default_diff["routing"]["shared_solve"] is False
    assert t["km_saved_delta"] != 0.0
    expected = t["km_saved_delta"] * COST_PER_KM * ROUTING_RUNS_PER_YEAR
    assert abs(t["routing_uplift_delta_eur"] - expected) < 0.02


# ---------------------------------------------------------------------------
# 5. Agreement with the engine that computes the same number
# ---------------------------------------------------------------------------


def test_uplift_delta_matches_the_scenario_compare(ctx):
    """Two features, one number: they must not be able to disagree."""
    spec_a = {"budget": 9000.0, "max_change": 0.15}
    spec_b = {"budget": 15000.0, "max_change": 0.05}
    diff = diff_for(ctx, run_a=dict(spec_a), run_b=dict(spec_b))
    cmp_ = compare_scenarios(
        ctx["ds"],
        scenario_a={"name": "A", **spec_a},
        scenario_b={"name": "B", **spec_b},
        routes=ctx["routes"],
    )
    assert diff["bridge"]["delta_eur"] == cmp_["deltas"]["expected_uplift_eur"]["abs"]
    assert diff["run_a"]["kpis"]["expected_uplift_eur"] == cmp_["scenario_a"]["kpis"]["expected_uplift_eur"]
    assert diff["run_b"]["kpis"]["expected_uplift_eur"] == cmp_["scenario_b"]["kpis"]["expected_uplift_eur"]
    assert diff["run_a"]["kpis"]["skus_carried"] == cmp_["scenario_a"]["kpis"]["skus_carried"]


# ---------------------------------------------------------------------------
# 6. Determinism and input validation
# ---------------------------------------------------------------------------


def test_diff_is_deterministic(ctx, default_diff):
    again = diff_for(ctx)
    assert json.dumps(default_diff, sort_keys=True) == json.dumps(again, sort_keys=True)


def test_payload_carries_no_wall_clock(default_diff):
    blob = json.dumps(default_diff).lower()
    for token in ("timestamp", "generated_at", "datetime", "utcnow"):
        assert token not in blob


@pytest.mark.parametrize(
    "spec",
    [
        {"budget": 0},
        {"budget": -5},
        {"budget": "lots"},
        {"max_change": 0},
        {"max_change": 5},
        {"service_level": 0},
        {"service_level": 1},
        {"service_level": 2},
        {"n_vehicles": 1.5},
        {"n_vehicles": 0},
        {"n_vehicles": 99},
        {"capacity_kg": 0},
        {"capacity_kg": float("nan")},
        {"holding_rate": -1},
        {"order_cost_eur": -10},
        {"name": 17},
    ],
)
def test_invalid_run_specs_are_rejected(ctx, spec):
    with pytest.raises(PlanDiffError):
        diff_for(ctx, run_b=spec)


def test_a_fleet_that_cannot_serve_the_run_is_rejected_not_hung(ctx):
    """An infeasible CVRP has a solution budget, not a time limit, so it would
    search forever; the necessary conditions are checked before it is launched."""
    with pytest.raises(PlanDiffError, match="heaviest stop"):
        diff_for(ctx, run_b={"capacity_kg": 500.0})
    with pytest.raises(PlanDiffError, match="cannot carry"):
        diff_for(ctx, run_b={"n_vehicles": 1, "capacity_kg": 2200.0})


def test_a_non_object_run_spec_is_rejected(ctx):
    with pytest.raises(PlanDiffError):
        diff_for(ctx, run_b=[1, 2, 3])


# ---------------------------------------------------------------------------
# 7. The pre-existing guard is untouched
# ---------------------------------------------------------------------------


def test_the_reconciliation_guard_still_holds_all_sixteen(ctx):
    """Adding a change ledger must not disturb the levels ledger."""
    rec = reconcile.reconcile(ctx["ds"])
    assert len(rec["identities"]) == 16
    assert [i["id"] for i in rec["identities"] if not i["ok"]] == []
    assert rec["all_ok"] is True


def test_the_two_ledgers_stay_separate(ctx, default_diff):
    """The change ledger is its own object: same discipline, different claims."""
    rec_ids = {i["id"] for i in reconcile.reconcile(ctx["ds"])["identities"]}
    assert rec_ids.isdisjoint({i["id"] for i in default_diff["identities"]})
    # run A is the platform's published plan, so its uplift IS the reconciled one
    published = {c["source"]: c["value"] for c in reconcile.reconcile(ctx["ds"])["claims"]}
    assert (
        default_diff["run_a"]["kpis"]["expected_uplift_eur"]
        == published["prescribe.build_plan.expected_uplift_eur"]
    )


# ---------------------------------------------------------------------------
# 8. The API + the station it feeds
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_get_plan_diff_serves_the_default_pair(client):
    resp = client.get("/api/plan-diff")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["all_ok"] is True
    assert body["bridge"]["attributed_eur"] == body["bridge"]["delta_eur"]
    for key in ("run_a", "run_b", "headline", "bridge", "assortment", "pricing",
                "inventory", "routing", "identities", "provenance", "caveats"):
        assert key in body


def test_get_plan_diff_is_cached_and_stable(client):
    """Computed once per dataset — a second GET is the same bytes, not a re-solve."""
    first = client.get("/api/plan-diff").data
    assert client.get("/api/plan-diff").data == first


def test_post_plan_diff_diffs_only_the_knobs_you_set(client):
    resp = client.post("/api/plan-diff", json={"run_b": {"budget": 12000.0}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["changed_parameters"] == ["budget_eur"]
    assert body["routing"]["shared_solve"] is True
    assert body["all_ok"] is True


def test_post_plan_diff_names_the_runs(client):
    resp = client.post(
        "/api/plan-diff",
        json={"run_a": {"name": "Approved", "budget": 9000.0},
              "run_b": {"name": "Proposal", "budget": 14000.0}},
    )
    body = resp.get_json()
    assert body["run_a"]["name"] == "Approved"
    assert body["run_b"]["name"] == "Proposal"
    assert body["bridge"]["start"]["label"] == "Approved"


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"run_b": {"max_change": 9}}, "max_change"),
        ({"run_b": {"service_level": 4}}, "service_level"),
        ({"run_b": {"n_vehicles": 0}}, "n_vehicles"),
        ({"run_b": {"capacity_kg": 400}}, "heaviest stop"),
        ({"run_a": {"budget": -1}}, "budget"),
    ],
)
def test_post_plan_diff_rejects_bad_specs_with_400(client, payload, fragment):
    resp = client.post("/api/plan-diff", json=payload)
    assert resp.status_code == 400
    assert fragment in resp.get_json()["error"]


def test_post_plan_diff_rejects_a_non_json_body(client):
    resp = client.post("/api/plan-diff", data="not json")
    assert resp.status_code == 400
    assert "run_a" in resp.get_json()["error"]


def test_plan_diff_is_behind_the_api_token_guard(client, monkeypatch):
    monkeypatch.setenv("DIP_API_TOKEN", "s3cret")
    assert client.get("/api/plan-diff").status_code == 401
    ok = client.get("/api/plan-diff", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_change_station_ships_with_the_dashboard(client):
    """The station is server-rendered markup, not something the JS invents."""
    html = client.get("/").data
    for marker in (
        b"st-change",            # the station
        b"ST-07",                # its station code, in the command-center language
        b"sec-plandiff",         # the bridge card
        b"pdBridgeChart",        # the waterfall canvas
        b"pdBridgeTable",        # the named-cause ledger
        b"pdProofStrip",         # the identity checklist, like ST-06's
        b"pdRangeTable",         # SKUs in / out
        b"pdPriceTable",         # price moves
        b"pdPolicyTable",        # ROP / EOQ / safety stock
        b"pdRoutingTable",       # the routing change
        b"runPlanDiff",          # the form's action
    ):
        assert marker in html, f"dashboard is missing {marker!r}"
