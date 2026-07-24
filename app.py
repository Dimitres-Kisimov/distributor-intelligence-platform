"""Distributor Intelligence Platform — Flask application.

One API in front of every engine. Expensive computations are run once at
startup and cached, so the dashboard and its live controls stay snappy. The
only per-request compute is the parameterised optimisers (assortment budget,
price guardrail), which are fast.

Routes
------
HTML : ``/`` dashboard, ``/routes`` and ``/assortment`` focused sub-views
API  : ``/api/kpis`` ``/api/forecast`` ``/api/margin-bridge`` ``/api/abc-xyz``
       ``/api/rfm`` ``/api/revenue`` ``/api/optimize/assortment``
       ``/api/optimize/prices`` ``/api/optimize/routes`` ``/api/prescribe``
       ``/api/export/pdf`` ``/api/export/excel`` ``/api/health``
"""

from __future__ import annotations

import numpy as np
from flask import Flask, Response, jsonify, render_template, request
from flask.json.provider import DefaultJSONProvider

from dip import analytics, exports, forecast, optimize, prescribe
from dip.data import build_dataset


class _NumpyJSONProvider(DefaultJSONProvider):
    """Serialise NumPy scalars/arrays the API returns without manual casting."""

    @staticmethod
    def default(obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.str_):
            return str(obj)
        return DefaultJSONProvider.default(obj)


app = Flask(__name__)
app.json = _NumpyJSONProvider(app)

# ---- startup cache ---------------------------------------------------------
DS = build_dataset()
CACHE = {
    "kpis": analytics.kpis(DS),
    "forecast": forecast.forecast_revenue(DS),
    "margin_bridge": analytics.margin_bridge(DS),
    "abc_xyz": analytics.abc_xyz(DS),
    "rfm": analytics.rfm_segments(DS),
    "revenue": analytics.revenue_breakdown(DS),
    "prices": optimize.optimize_prices(DS),
    "routes": optimize.optimize_routes(DS),
    "prescribe": prescribe.build_plan(DS),
}


# ---- HTML ------------------------------------------------------------------
@app.route("/")
def dashboard() -> str:
    return render_template("dashboard.html", view="overview", kpis=CACHE["kpis"], plan=CACHE["prescribe"])


@app.route("/routes")
def routes_view() -> str:
    return render_template("dashboard.html", view="routes", kpis=CACHE["kpis"], plan=CACHE["prescribe"])


@app.route("/assortment")
def assortment_view() -> str:
    return render_template("dashboard.html", view="assortment", kpis=CACHE["kpis"], plan=CACHE["prescribe"])


# ---- JSON API --------------------------------------------------------------
@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "skus": len(DS.skus), "months": DS.n_months})


@app.route("/api/kpis")
def api_kpis():
    return jsonify(CACHE["kpis"])


@app.route("/api/forecast")
def api_forecast():
    return jsonify(CACHE["forecast"])


@app.route("/api/margin-bridge")
def api_margin_bridge():
    return jsonify(CACHE["margin_bridge"])


@app.route("/api/abc-xyz")
def api_abc_xyz():
    return jsonify(CACHE["abc_xyz"])


@app.route("/api/rfm")
def api_rfm():
    return jsonify(CACHE["rfm"])


@app.route("/api/revenue")
def api_revenue():
    return jsonify(CACHE["revenue"])


@app.route("/api/optimize/assortment")
def api_assortment():
    budget = request.args.get("budget", type=float)
    return jsonify(optimize.optimize_assortment(DS, budget=budget))


@app.route("/api/optimize/prices")
def api_prices():
    max_change = request.args.get("max_change", default=0.15, type=float)
    if max_change == 0.15:
        return jsonify(CACHE["prices"])
    return jsonify(optimize.optimize_prices(DS, max_change=max_change))


@app.route("/api/optimize/routes")
def api_routes():
    return jsonify(CACHE["routes"])


@app.route("/api/prescribe")
def api_prescribe():
    budget = request.args.get("budget", type=float)
    if budget is None:
        return jsonify(CACHE["prescribe"])
    return jsonify(prescribe.build_plan(DS, budget=budget))


@app.route("/api/export/pdf")
def api_export_pdf():
    data = exports.build_pdf()
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=dip_executive_review.pdf"},
    )


@app.route("/api/export/excel")
def api_export_excel():
    data = exports.build_excel()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=dip_plan.xlsx"},
    )


if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=5000, debug=False)
