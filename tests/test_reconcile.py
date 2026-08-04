"""Cross-engine reconciliation tests — the "no silent drift" enforcement.

These pin the contract of :mod:`dip.reconcile`:

1. every cross-engine identity holds (the composed figures equal their parts);
2. every headline number the README prints traces to an engine field and is
   still present in the README (the doc-drift guard);
3. a few identities are pinned to exact fixture numbers so the arithmetic
   itself is guarded, not just its self-consistency;
4. the structure is deterministic and the written reports are byte-identical
   across runs and match the committed copies in ``docs/`` (so a stale
   deliverable fails CI instead of shipping).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from dip import reconcile
from dip.data import build_dataset

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Identities + doc-drift guard
# ---------------------------------------------------------------------------


def test_all_cross_engine_identities_hold():
    rec = reconcile.reconcile(build_dataset())
    failed = [i["id"] for i in rec["identities"] if not i["ok"]]
    assert failed == [], f"identities drifted: {failed}"
    assert rec["identities_ok"] is True
    # the ledger actually checks the levers, the KPIs and the forecast (not a
    # single trivial family) — guard the coverage so it can't quietly shrink
    ids = {i["id"] for i in rec["identities"]}
    assert {
        "uplift_composition",
        "routing_eur_bridge",
        "pricing_lever_identity",
        "assortment_lever_identity",
        "gross_margin_identity",
        "annual_gross_margin_basis",
        "uplift_pct_basis",
        "plan_mase_tie",
    } <= ids


def test_every_headline_number_traces_and_is_in_the_readme():
    rec = reconcile.reconcile(build_dataset())
    assert rec["claims"], "expected a non-empty set of headline claims"
    for c in rec["claims"]:
        assert c["source"], f"{c['label']} has no engine source"
        assert c["in_readme"], f"{c['label']} ({c['display']}) not found in README"
    assert rec["readme_missing"] == []
    assert rec["readme_ok"] is True
    assert rec["all_ok"] is True


def test_doc_drift_guard_trips_when_readme_omits_a_number():
    """If the README no longer prints a headline number, the guard must fail —
    that is the whole point (numbers can't silently drift out of the docs)."""
    rec = reconcile.reconcile(build_dataset(), readme_text="an empty readme with no numbers")
    assert rec["readme_ok"] is False
    assert rec["readme_missing"]  # every claim reported missing
    assert rec["all_ok"] is False
    # the identities are about the engines, not the README, so they still hold
    assert rec["identities_ok"] is True


# ---------------------------------------------------------------------------
# 2. Exact fixture numbers (guards the arithmetic, not only its consistency)
# ---------------------------------------------------------------------------


def test_identity_values_match_fixture():
    rec = reconcile.reconcile(build_dataset())
    ident = {i["id"]: i for i in rec["identities"]}
    # headline uplift is exactly the sum of the three levers
    assert ident["uplift_composition"]["lhs"] == ident["uplift_composition"]["rhs"] == 136972.2
    # routing lever = 140.31 km x 1.15 EUR/km x 250 runs
    assert ident["routing_eur_bridge"]["rhs"] == 40339.12
    # assortment edge over greedy
    assert ident["assortment_lever_identity"]["lhs"] == 1023.66
    # uplift is quoted against ANNUAL gross margin (24-mo history halved)
    assert ident["annual_gross_margin_basis"]["lhs"] == 1628753.53
    assert ident["uplift_pct_basis"]["lhs"] == 0.0841


def test_headline_claim_values_match_fixture():
    rec = reconcile.reconcile(build_dataset())
    by_source = {c["source"]: c for c in rec["claims"]}
    assert by_source["analytics.kpis.revenue"]["value"] == 4788971.2
    assert by_source["forecast.forecast_revenue.mase"]["value"] == 0.3757
    assert by_source["forecast.forecast_revenue.mase"]["display"] == "MASE 0.38"
    assert by_source["prescribe.build_plan.expected_uplift_eur"]["display"] == "EUR 136,972"
    assert by_source["optimize.optimize_routes.pct_saved"]["display"] == "25.0%"


# ---------------------------------------------------------------------------
# 3. Determinism + committed reports are current
# ---------------------------------------------------------------------------


def test_reconcile_structure_is_deterministic():
    assert reconcile.reconcile(build_dataset()) == reconcile.reconcile(build_dataset())


def test_written_reports_are_byte_identical_across_runs(tmp_path):
    def digests(target: Path) -> dict[str, str]:
        paths = reconcile.write_reports(target)
        return {name: hashlib.sha256(p.read_bytes()).hexdigest() for name, p in paths.items()}

    first = digests(tmp_path / "a")
    second = digests(tmp_path / "b")
    assert first == second
    assert set(first) == {"markdown", "csv"}


def test_committed_reports_match_a_fresh_regeneration(tmp_path):
    """docs/RECONCILIATION.md + reconciliation.csv must be regenerated whenever
    the engines change — a stale committed copy fails here (deterministic, so
    byte comparison is fair)."""
    fresh = reconcile.write_reports(tmp_path)
    for fresh_path in fresh.values():
        committed = _REPO_ROOT / "docs" / fresh_path.name
        assert committed.exists(), f"missing committed report: {committed}"
        assert committed.read_bytes() == fresh_path.read_bytes(), (
            f"{committed.name} is stale — run `python -m dip --reconcile`"
        )
