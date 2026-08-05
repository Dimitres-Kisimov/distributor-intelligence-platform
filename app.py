"""Distributor Intelligence Platform — Flask application.

One API in front of every engine. Expensive computations are run once per
dataset and cached, so the dashboard and its live controls stay snappy. The
only per-request compute is the parameterised optimisers (assortment budget,
price guardrail), which are fast.

The app starts on the seeded synthetic dataset. ``POST /api/import`` swaps the
whole platform onto the user's own Excel workbook (template from
``GET /api/import/template``): KPIs, forecast, assortment, pricing, the plan
and both exports all run on the imported data through the same single-plan
path. ``POST /api/reset`` restores the synthetic dataset — the exact startup
objects, so behaviour is byte-identical to a fresh boot. Imported data lives
in process memory only; nothing is written to disk.

Routes
------
HTML : ``/`` dashboard, ``/routes`` and ``/assortment`` focused sub-views,
       ``/reconcile`` the executive brief (headline numbers -> engine sources)
API  : ``/api/kpis`` ``/api/kpis/drilldown`` ``/api/forecast``
       ``/api/margin-bridge`` ``/api/abc-xyz`` ``/api/rfm`` ``/api/revenue``
       ``/api/crosssell`` ``/api/optimize/assortment`` ``/api/optimize/prices``
       ``/api/optimize/routes`` ``/api/prescribe`` ``/api/explain``
       ``/api/scenario/compare`` (POST) ``/api/reconcile``
       ``/api/import`` ``/api/import/template`` ``/api/reset``
       ``/api/export/pdf`` ``/api/export/excel`` ``/api/health``
"""

from __future__ import annotations

import hmac
import math
import os
import threading

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

from dip import (
    analytics,
    crosssell,
    explain,
    exports,
    forecast,
    importer,
    optimize,
    prescribe,
    reconcile,
    scenario,
)
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


# ---- dataset state ---------------------------------------------------------
def _build_cache(ds, routes: dict | None = None) -> dict:
    """Run every engine once for ``ds``; ``routes`` may reuse an earlier solve."""
    cache = {
        "kpis": analytics.kpis(ds),
        "kpi_drilldown": analytics.kpi_drilldown(ds),
        "forecast": forecast.forecast_revenue(ds),
        "margin_bridge": analytics.margin_bridge(ds),
        "abc_xyz": analytics.abc_xyz(ds),
        "rfm": analytics.rfm_segments(ds),
        "revenue": analytics.revenue_breakdown(ds),
        "crosssell": crosssell.mine_crosssell(ds),
        "prices": optimize.optimize_prices(ds),
        "routes": routes if routes is not None else optimize.optimize_routes(ds),
    }
    # The plan is composed FROM the cached engine results, never re-solved: the
    # routes endpoint, the prescribe cards and the exports must all quote the
    # same km and the same uplift, so routing and pricing are solved exactly
    # once per dataset above.
    cache["prescribe"] = prescribe.build_plan(ds, routes=cache["routes"], prices=cache["prices"])
    return cache


def _make_state(ds, source: dict, routes: dict | None = None) -> dict:
    """Bundle a dataset with its engine cache + source label, swapped atomically."""
    return {"ds": ds, "cache": _build_cache(ds, routes=routes), "source": source}


_SYNTHETIC_STATE = _make_state(
    build_dataset(),
    {
        "synthetic": True,
        "label": "seeded synthetic dataset",
        "filename": None,
        "n_skus": len(build_dataset().skus),
        "assumptions": [],
    },
)
# Imports replace STATE wholesale; /api/reset points it back at the startup
# objects themselves, so reset behaviour is byte-identical to a fresh boot.
STATE = _SYNTHETIC_STATE
_LOCK = threading.Lock()

DEFAULT_MAX_CHANGE = 0.15


# ---- reconciliation snapshot (the "no silent drift" ledger, for a viewer) ---
# ``dip.reconcile`` re-runs the engines on the *seeded synthetic* dataset and
# ties every published headline number to the engine field it comes from — it is
# about the platform's published figures, so it is computed on the synthetic
# dataset regardless of any imported state. Built once, lazily, and cached (it
# re-runs every engine, same cost as one dataset build), matching the app's
# compute-once-then-cache philosophy. Deterministic, so the cache is safe.
_RECONCILE_CACHE: dict | None = None

# The five figures the executive brief leads with, each pulled *from* a reconcile
# claim by its engine-source key so the hero can never drift from the ledger.
# (source-field, tile label, method/provenance sub-label, accent class)
_BRIEF_KPIS = [
    ("analytics.kpis.revenue", "Revenue (24 mo)", "sum of the monthly ledger · analytics.kpis", ""),
    ("analytics.kpis.gross_margin", "Gross margin", "revenue − COGS · analytics.kpis", "green"),
    ("prescribe.build_plan.expected_uplift_eur", "Expected annual uplift",
     "modelled: sum of 3 levers · prescribe.build_plan", "pink"),
    ("forecast.forecast_revenue.mase", "Forecast accuracy",
     "rolling-origin backtest · forecast.forecast_revenue", "amber"),
    ("optimize.optimize_routes.pct_saved", "Routing efficiency",
     "km saved per run vs nearest-neighbour · optimize.optimize_routes", ""),
]


def _reconcile_snapshot() -> dict:
    """Return the cached cross-engine reconciliation for the seeded dataset."""
    global _RECONCILE_CACHE
    if _RECONCILE_CACHE is None:
        with _LOCK:
            if _RECONCILE_CACHE is None:
                _RECONCILE_CACHE = reconcile.reconcile()
    return _RECONCILE_CACHE


# ---- optional API auth (deployment stub) -----------------------------------
@app.before_request
def _require_api_token():
    """Single-token bearer auth for ``/api/*`` — only when DIP_API_TOKEN is set.

    Unset (the default) leaves the app exactly as before: an open local demo.
    Set, every API route demands ``Authorization: Bearer <token>`` and answers
    401 otherwise. This is a deployment stub, not production auth — no users,
    no scopes, no rotation; a real deployment needs a proper identity provider.
    The env var is read per request so tests (and operators) can toggle it
    without restarting. Comparison is constant-time via ``hmac.compare_digest``.
    """
    token = os.environ.get("DIP_API_TOKEN")
    if not token or not request.path.startswith("/api/"):
        return None
    supplied = request.headers.get("Authorization", "")
    if hmac.compare_digest(supplied, f"Bearer {token}"):
        return None
    return jsonify({"error": "unauthorized: this API requires 'Authorization: Bearer <token>'"}), 401


def _plan_for(budget: float | None, max_change: float, st: dict | None = None) -> dict:
    """The one plan for a scenario — shared by /api/prescribe and both exports.

    The default scenario returns the state-cached plan object itself; any
    other scenario re-runs only the parameterised engines (assortment MILP,
    pricing) on top of the same cached routing solve, so every consumer of a
    given scenario sees identical numbers. ``st`` pins a state snapshot so a
    request that builds several artefacts (plan + explanation + export) can
    never straddle a concurrent import/reset.
    """
    st = st or STATE
    cache = st["cache"]
    if budget is None and max_change == DEFAULT_MAX_CHANGE:
        return cache["prescribe"]
    return prescribe.build_plan(
        st["ds"],
        budget=budget,
        max_change=max_change,
        routes=cache["routes"],
        prices=cache["prices"] if max_change == DEFAULT_MAX_CHANGE else None,
    )


def _explain_for(budget: float | None, max_change: float, st: dict | None = None) -> dict:
    """The one explanation for a scenario — /api/explain and both exports.

    Reuses the state's routing solve and (for the default guardrail) pricing
    solve, plus the exact plan `_plan_for` serves, so the story and the numbers
    can never diverge from what is on screen.
    """
    st = st or STATE
    cache = st["cache"]
    prices = (
        cache["prices"]
        if max_change == DEFAULT_MAX_CHANGE
        else optimize.optimize_prices(st["ds"], max_change=max_change)
    )
    return explain.explain_plan(
        st["ds"],
        max_change=max_change,
        plan=_plan_for(budget, max_change, st=st),
        routes=cache["routes"],
        prices=prices,
        source=st["source"],
    )


def _scenario_for(budget: float | None, max_change: float, st: dict | None = None) -> dict:
    """The A/B compare baked into a deliverable: default baseline vs on-screen.

    Scenario A is the default plan (the optimiser's own 40%-of-capital budget,
    ±15% guardrail); scenario B is the scenario the export was requested for.
    Reuses the state's routing solve, so scenario B ties to the exact plan
    `_plan_for` serves and to the workbook's Summary sheet.
    """
    st = st or STATE
    return scenario.compare_scenarios(
        st["ds"],
        scenario_a={
            "name": "Baseline (default budget & guardrail)",
            "budget": None,
            "max_change": DEFAULT_MAX_CHANGE,
        },
        scenario_b={
            "name": "On-screen scenario",
            "budget": budget,
            "max_change": max_change,
        },
        source=st["source"],
        routes=st["cache"]["routes"],
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


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    """Parse an integer query parameter strictly: absent -> default, junk -> 400.

    Same philosophy as :func:`_float_arg` — reject unparseable input instead of
    silently answering a different question. Values are clamped to [lo, hi].
    """
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(make_response(
            jsonify({"error": f"query parameter '{name}' must be an integer, got {raw!r}"}), 400
        ))
    return max(lo, min(hi, value))


# ---- HTML ------------------------------------------------------------------
def _render_dashboard(view: str) -> str:
    st = STATE
    return render_template(
        "dashboard.html",
        view=view,
        kpis=st["cache"]["kpis"],
        plan=st["cache"]["prescribe"],
        source=st["source"],
    )


@app.route("/")
def dashboard() -> str:
    return _render_dashboard("overview")


@app.route("/routes")
def routes_view() -> str:
    return _render_dashboard("routes")


@app.route("/assortment")
def assortment_view() -> str:
    return _render_dashboard("assortment")


@app.route("/reconcile")
def reconcile_page() -> str:
    """Executive brief: the honest headline KPIs + the cross-engine ledger.

    A server-rendered, JS-free presentation of :func:`dip.reconcile.reconcile`
    — every headline number traced to the engine field it comes from, and the
    cross-engine identities that keep the composed figures honest, shown to a
    viewer rather than only asserted in the test suite. Always reports on the
    seeded synthetic dataset (the platform's published figures), so it is
    stable whatever data is loaded on the main dashboard.
    """
    rec = _reconcile_snapshot()
    by_source = {c["source"]: c for c in rec["claims"]}
    brief_kpis = [
        {"claim": by_source[src], "label": label, "sub": sub, "accent": accent}
        for src, label, sub, accent in _BRIEF_KPIS
        if src in by_source
    ]
    return render_template("reconcile.html", rec=rec, brief_kpis=brief_kpis)


# ---- JSON API --------------------------------------------------------------
@app.route("/api/health")
def api_health():
    st = STATE
    return jsonify(
        {
            "status": "ok",
            "skus": len(st["ds"].skus),
            "months": st["ds"].n_months,
            "source": st["source"],
        }
    )


@app.route("/api/reconcile")
def api_reconcile():
    """The cross-engine reconciliation ledger as JSON (the ``/reconcile`` data).

    Headline numbers each traced to their engine source field, the cross-engine
    identities with their pass/fail flags, and the ``all_ok`` verdict — computed
    on the seeded synthetic dataset, the same object the executive brief renders.
    """
    return jsonify(_reconcile_snapshot())


@app.route("/api/kpis")
def api_kpis():
    return jsonify(STATE["cache"]["kpis"])


@app.route("/api/forecast")
def api_forecast():
    return jsonify(STATE["cache"]["forecast"])


@app.route("/api/margin-bridge")
def api_margin_bridge():
    return jsonify(STATE["cache"]["margin_bridge"])


@app.route("/api/abc-xyz")
def api_abc_xyz():
    return jsonify(STATE["cache"]["abc_xyz"])


@app.route("/api/rfm")
def api_rfm():
    return jsonify(STATE["cache"]["rfm"])


@app.route("/api/revenue")
def api_revenue():
    return jsonify(STATE["cache"]["revenue"])


@app.route("/api/kpis/drilldown")
def api_kpi_drilldown():
    """Revenue-by-segment and margin-by-category behind the KPI headline tiles.

    Served from the per-dataset cache, so the numbers here are the exact ones
    the tiles (and the exported workbook's Drill-downs sheet) quote — the
    segment rows sum to the headline KPIs to the cent.
    """
    st = STATE
    return jsonify({**st["cache"]["kpi_drilldown"], "source": st["source"]})


@app.route("/api/crosssell")
def api_crosssell():
    """Cross-sell association rules mined from the synthetic order baskets.

    ``?product=SKU-0001`` narrows to the top-N recommendations for one SKU
    (404 for a SKU the dataset does not contain); ``?top=N`` caps the list
    (default 5 per product / 25 overall, max 50). Every payload carries the
    honesty note: synthetic demo baskets, lift = co-occurrence, not causation.
    """
    st = STATE
    result = st["cache"]["crosssell"]
    product = request.args.get("product")
    base = {
        "available": result["available"],
        "note": result["note"],
        "n_baskets": result["n_baskets"],
        "n_rules": result["n_rules"],
        "params": result["params"],
        "source": st["source"],
    }
    if product is None:
        top = _int_arg("top", default=25, lo=1, hi=50)
        return jsonify({**base, "rules": result["rules"][:top], "products": result["products"]})
    top = _int_arg("top", default=5, lo=1, hi=50)
    known = {s["sku_id"] for s in st["ds"].skus}
    if product not in known:
        return jsonify(
            {"error": f"unknown product {product!r} — use a sku_id from /api/abc-xyz per_sku"}
        ), 404
    recs = crosssell.recommendations_for(result, product, top=top)
    return jsonify({**base, "product": product, "recommendations": recs})


@app.route("/api/optimize/assortment")
def api_assortment():
    st = STATE
    budget = _float_arg("budget")
    return jsonify(optimize.optimize_assortment(st["ds"], budget=budget))


@app.route("/api/optimize/prices")
def api_prices():
    st = STATE
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    if max_change == DEFAULT_MAX_CHANGE:
        return jsonify(st["cache"]["prices"])
    return jsonify(optimize.optimize_prices(st["ds"], max_change=max_change))


@app.route("/api/optimize/routes")
def api_routes():
    return jsonify(STATE["cache"]["routes"])


@app.route("/api/prescribe")
def api_prescribe():
    budget = _float_arg("budget")
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    return jsonify(_plan_for(budget, max_change))


@app.route("/api/explain")
def api_explain():
    budget = _float_arg("budget")
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    return jsonify(_explain_for(budget, max_change))


@app.route("/api/scenario/compare", methods=["POST"])
def api_scenario_compare():
    """A/B compare two named budget/guardrail scenarios over the served dataset.

    Body (JSON)::

        {"scenario_a": {"name"?, "budget"?, "max_change"?},
         "scenario_b": {"name"?, "budget"?, "max_change"?}}

    ``budget`` in EUR (null/omitted = the optimiser's default 40%-of-capital);
    ``max_change`` a guardrail fraction in (0, 1] (default 0.15). Returns each
    scenario's KPIs, the per-KPI absolute/% deltas, and a data-source provenance
    label. 400 on a non-JSON body or invalid parameters; the compare is
    deterministic (it reuses the state's single routing solve).
    """
    st = STATE
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(
            {"error": 'POST a JSON body: {"scenario_a": {...}, "scenario_b": {...}}'}
        ), 400
    try:
        result = scenario.compare_scenarios(
            st["ds"],
            scenario_a=payload.get("scenario_a"),
            scenario_b=payload.get("scenario_b"),
            source=st["source"],
            routes=st["cache"]["routes"],
        )
    except scenario.ScenarioError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


# ---- Excel round-trip: import your data / reset ----------------------------
@app.route("/api/import/template")
def api_import_template():
    """Serve the empty import template (SKUs sheet + Instructions sheet)."""
    return Response(
        importer.build_template(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=dip_import_template.xlsx"},
    )


@app.route("/api/import", methods=["POST"])
def api_import():
    """Swap the whole platform onto an uploaded workbook (field ``workbook``).

    Validation failures return 400 with row/cell-addressed ``errors`` and leave
    the running state untouched. On success every engine re-runs on the
    imported data through the same single-plan path; the synthetic routing
    solve is reused because the customer/routing layer is not part of the
    import (disclosed in ``source.assumptions``). In-memory only — nothing is
    written to disk, and a restart (or /api/reset) restores the synthetic data.
    """
    global STATE
    upload = request.files.get("workbook")
    if upload is None or not upload.filename:
        return jsonify(
            {
                "error": "attach an .xlsx workbook (multipart field 'workbook'); "
                "start from GET /api/import/template"
            }
        ), 400
    data = upload.read(importer.MAX_XLSX_BYTES + 1)
    try:
        rows = importer.parse_workbook(data, filename=upload.filename)
        ds, assumptions = importer.dataset_from_rows(rows)
    except importer.XlsxImportError as exc:
        return jsonify({"error": str(exc), "errors": exc.errors}), 400
    source = {
        "synthetic": False,
        "label": f"imported workbook ({upload.filename})",
        "filename": upload.filename,
        "n_skus": len(ds.skus),
        "assumptions": assumptions,
    }
    with _LOCK:
        STATE = _make_state(ds, source, routes=_SYNTHETIC_STATE["cache"]["routes"])
    return jsonify({"ok": True, "synthetic": False, "n_skus": len(ds.skus), "source": source})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Restore the seeded synthetic dataset (the exact startup objects)."""
    global STATE
    with _LOCK:
        STATE = _SYNTHETIC_STATE
    return jsonify({"ok": True, "synthetic": True, "source": STATE["source"]})


# ---- exports ---------------------------------------------------------------
@app.route("/api/export/pdf")
def api_export_pdf():
    st = STATE
    budget = _float_arg("budget")
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    data = exports.build_pdf(
        budget=budget,
        max_change=max_change,
        plan=_plan_for(budget, max_change, st=st),
        ds=st["ds"],
        source=st["source"],
        explanation=_explain_for(budget, max_change, st=st),
    )
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=dip_executive_review.pdf"},
    )


@app.route("/api/export/excel")
def api_export_excel():
    st = STATE
    budget = _float_arg("budget")
    max_change = _float_arg("max_change", default=DEFAULT_MAX_CHANGE)
    data = exports.build_excel(
        budget=budget,
        max_change=max_change,
        plan=_plan_for(budget, max_change, st=st),
        ds=st["ds"],
        source=st["source"],
        explanation=_explain_for(budget, max_change, st=st),
        scenario_compare=_scenario_for(budget, max_change, st=st),
    )
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=dip_plan.xlsx"},
    )


if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=5000, debug=False)
