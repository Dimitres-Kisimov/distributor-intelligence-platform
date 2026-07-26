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

import math

import numpy as np
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    render_template,
    request,
)
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
}
# The plan is composed FROM the cached engine results, never re-solved: the
# routes endpoint, the prescribe cards and the exports must all quote the same
# km and the same uplift, so routing and pricing are solved exactly once above.
CACHE["prescribe"] = prescribe.build_plan(DS, routes=CACHE["routes"], prices=CACHE["prices"])


DEFAULT_MAX_CHANGE = 0.15


def _plan_for(budget: float | None, max_change: float) -> dict:
    """The one plan for a scenario — shared by /api/prescribe and both exports.

    The default scenario returns the startup-cached plan object itself; any
    other scenario re-runs only the parameterised engines (assortment MILP,
    pricing) on top of the same cached routing solve, so every consumer of a
    given scenario sees identical numbers.
    """
    if budget is None and max_change == DEFAULT_MAX_CHANGE:
        return CACHE["prescribe"]
    return prescribe.build_plan(
        DS,
        budget=budget,
        max_change=max_change,
        routes=CACHE["routes"],
        prices=CACHE["prices"] if max_change == DEFAULT_MAX_CHANGE else None,
    )


def _float_arg(name: str, default: float | None = None) -> float | None:
    """Parse a float query parameter strictly: absent -> default, junk -> 400.

    Flask's ``type=float`` silently swallows unparseable values (returning the
    default), which hands API consumers a valid-looking answer to an invalid
    question. Reject non-numeric and non-finite input explicitly instead.
    """
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        abort(make_response(
            jsonify({"error": f"query parameter '{name}' must be a finite number, got {raw!r}"}), 400
        ))
    return value


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
    budget = _float_arg("budget")
    return jsonify(optimize.optimize_assortment(DS, budget=budget))


@app.route("/api/optimize/prices")
def api_prices():
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    if max_change == DEFAULT_MAX_CHANGE:
        return jsonify(CACHE["prices"])
    return jsonify(optimize.optimize_prices(DS, max_change=max_change))


@app.route("/api/optimize/routes")
def api_routes():
    return jsonify(CACHE["routes"])


@app.route("/api/prescribe")
def api_prescribe():
    budget = _float_arg("budget")
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    return jsonify(_plan_for(budget, max_change))


@app.route("/api/export/pdf")
def api_export_pdf():
    budget = _float_arg("budget")
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    data = exports.build_pdf(budget=budget, max_change=max_change, plan=_plan_for(budget, max_change))
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=dip_executive_review.pdf"},
    )


@app.route("/api/export/excel")
def api_export_excel():
    budget = _float_arg("budget")
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    data = exports.build_excel(budget=budget, max_change=max_change, plan=_plan_for(budget, max_change))
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=dip_plan.xlsx"},
    )


if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=5000, debug=False)
