"""Flask API tests via ``app.test_client()`` — routes return 200 + JSON keys."""

from __future__ import annotations

import pytest

from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["skus"] == 200
    assert body["months"] == 24


def test_kpis_route(client):
    resp = client.get("/api/kpis")
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("revenue", "gross_margin", "gross_margin_pct", "revenue_series"):
        assert key in body


def test_forecast_route(client):
    resp = client.get("/api/forecast")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "mase" in body
    assert len(body["forecast"]) == 6


def test_prescribe_route(client):
    resp = client.get("/api/prescribe")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["expected_uplift_eur"] > 0
    assert len(body["cards"]) == 4


def test_assortment_route_with_budget(client):
    resp = client.get("/api/optimize/assortment?budget=6000")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["milp"]["margin"] >= body["greedy"]["margin"]


def test_assortment_rejects_non_numeric_budget(client):
    resp = client.get("/api/optimize/assortment?budget=abc")
    assert resp.status_code == 400
    assert "budget" in resp.get_json()["error"]


def test_prices_rejects_non_numeric_max_change(client):
    resp = client.get("/api/optimize/prices?max_change=abc")
    assert resp.status_code == 400
    assert "max_change" in resp.get_json()["error"]


def test_prescribe_respects_budget_and_guardrail(client):
    resp = client.get("/api/prescribe?budget=8000&max_change=0.05")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["budget"] == 8000.0
    assert body["max_change"] == 0.05
    default = client.get("/api/prescribe").get_json()
    # a tighter guardrail must not out-earn the default pricing lever
    assert body["levers"]["pricing"] <= default["levers"]["pricing"]


def test_export_excel_scenario_aware(client):
    resp = client.get("/api/export/excel?budget=8000&max_change=0.05")
    assert resp.status_code == 200
    assert resp.data[:2] == b"PK"


def test_routes_route(client):
    resp = client.get("/api/optimize/routes")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["optimized_km"] <= body["baseline_km"]


def test_export_pdf_route(client):
    resp = client.get("/api/export/pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:4] == b"%PDF"


def test_dashboard_html_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower()
