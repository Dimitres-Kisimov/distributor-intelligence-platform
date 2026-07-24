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
