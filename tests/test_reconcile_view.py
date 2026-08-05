"""The reconciliation is *surfaced*, not only asserted — view + API render tests.

`dip.reconcile` already machine-checks that every headline number ties to its
engine (``tests/test_reconcile.py``). These tests pin the viewer-facing paths
added on top of it:

1. ``GET /api/reconcile`` serves the same ledger structure as ``dip.reconcile``;
2. ``GET /reconcile`` (the executive brief) renders **every** claim's display
   string and **every** identity statement, so the page cannot drift from the
   engine ledger it is built from;
3. the brief leads with the honest, provenance-labelled headline KPIs;
4. the brief carries the proprietary licence, never a stale ``MIT`` string;
5. the ``/api/*`` guard covers ``/api/reconcile`` while the HTML brief stays open.
"""

from __future__ import annotations

import pytest
from markupsafe import escape  # Jinja's HTML encoder — so apostrophes etc. match

from app import app as flask_app
from dip import reconcile
from dip.data import build_dataset


def _rendered(text: str) -> str:
    """HTML-escape a ledger string exactly as the template does, for `in html`."""
    return str(escape(text))


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_api_reconcile_matches_engine_ledger(client):
    resp = client.get("/api/reconcile")
    assert resp.status_code == 200
    body = resp.get_json()
    rec = reconcile.reconcile(build_dataset())
    # the served ledger is the engine ledger — same verdict, same coverage
    assert body["all_ok"] is True
    assert body["identities_ok"] == rec["identities_ok"]
    assert body["readme_ok"] == rec["readme_ok"]
    assert [i["id"] for i in body["identities"]] == [i["id"] for i in rec["identities"]]
    assert [c["source"] for c in body["claims"]] == [c["source"] for c in rec["claims"]]
    assert all(i["ok"] for i in body["identities"])


def test_reconcile_brief_renders_every_claim_and_identity(client):
    """The page is built *from* the ledger, so every headline number the engine
    reconciles must appear on screen verbatim — no silent divergence."""
    resp = client.get("/reconcile")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    rec = reconcile.reconcile(build_dataset())
    # every headline number, exactly as the ledger formats it, is on the page,
    # traced to its engine source field
    for c in rec["claims"]:
        assert _rendered(c["display"]) in html, f"brief is missing headline {c['label']} ({c['display']})"
        assert _rendered(c["source"]) in html, f"brief is missing engine source {c['source']}"
    # every cross-engine identity statement is shown
    for i in rec["identities"]:
        assert _rendered(i["statement"]) in html, f"brief is missing identity {i['id']}"


def test_reconcile_brief_shows_the_no_drift_verdict(client):
    html = client.get("/reconcile").get_data(as_text=True)
    rec = reconcile.reconcile(build_dataset())
    assert rec["all_ok"] is True
    assert "No silent drift" in html
    n_ids = len(rec["identities"])
    assert f"{n_ids}/{n_ids} cross-engine identities hold" in html
    assert "Reconciliation found drift" not in html


def test_reconcile_brief_leads_with_honest_headline_kpis(client):
    """The hero tiles quote the reconciled displays, each with a method label."""
    html = client.get("/reconcile").get_data(as_text=True)
    rec = reconcile.reconcile(build_dataset())
    by_source = {c["source"]: c for c in rec["claims"]}
    for src in (
        "analytics.kpis.revenue",
        "prescribe.build_plan.expected_uplift_eur",
        "forecast.forecast_revenue.mase",
        "optimize.optimize_routes.pct_saved",
    ):
        assert by_source[src]["display"] in html
    # honesty labels the reader must see up top
    assert "seeded synthetic dataset" in html
    assert "modelled, not measured" in html


def test_reconcile_brief_is_proprietary_not_mit(client):
    html = client.get("/reconcile").get_data(as_text=True)
    assert "all rights reserved" in html
    assert "MIT" not in html


def test_dashboard_links_to_the_reconciliation_brief(client):
    assert b'href="/reconcile"' in client.get("/").data


def test_api_reconcile_is_token_guarded_but_brief_stays_open(client, monkeypatch):
    monkeypatch.setenv("DIP_API_TOKEN", "s3cret")
    assert client.get("/api/reconcile").status_code == 401
    ok = client.get("/api/reconcile", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    # the HTML brief is not part of the API surface — it stays open like `/`
    assert client.get("/reconcile").status_code == 200
