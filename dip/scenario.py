"""Scenario compare: A/B of the plan under two named real-parameter scenarios.

Pure and deterministic. Runs the same optimiser stack :mod:`dip.prescribe`
composes, under two named scenarios — each a working-capital ``budget`` and a
price guardrail (``max_change``) — over the *one* seeded dataset, and returns
each scenario's KPIs plus the absolute and % deltas (scenario B relative to A).

One source of truth: every number a scenario reports comes from the same
engines the dashboard, the plan and the exports already use, so a compare can
never quote a figure the rest of the platform disagrees with.

Determinism
-----------
The routing solve is shared by both scenarios (routing depends on neither knob),
and every engine terminates on a fixed seed / solution budget, never a wall
clock — so two runs return byte-identical numbers.

Honesty
-------
The assortment lever is the MILP's *edge over the greedy baseline at that
budget*. Because a looser budget lets the greedy baseline catch up, a scenario
with **more** budget can post a **smaller** assortment delta even though it
captures more absolute margin — exactly the effect :mod:`dip.explain` surfaces
in its budget sensitivity. The compare reports it in ``note`` rather than hiding
it.
"""

from __future__ import annotations

import math

from .data import Dataset, build_dataset
from .optimize import optimize_assortment, optimize_routes
from .prescribe import build_plan

DEFAULT_MAX_CHANGE = 0.15
# Validation bounds for the price guardrail: a fraction in (0, 1].
_MAX_CHANGE_LO = 1e-6
_MAX_CHANGE_HI = 1.0
_NAME_MAX = 80

# The KPI keys compared side by side, in display order. Every value is a plain
# number so the delta table (and the exported sheet) can be exact.
KPI_KEYS = (
    "expected_uplift_eur",
    "expected_uplift_pct",
    "pricing_uplift_eur",
    "assortment_uplift_eur",
    "routing_uplift_eur",
    "margin_captured_eur",
    "capital_used_eur",
    "skus_carried",
    "budget_eur",
)


class ScenarioError(ValueError):
    """Invalid scenario spec — mapped to HTTP 400 by the API layer."""


def normalize_scenario(raw: dict | None, *, label: str) -> dict:
    """Validate and normalise one scenario spec.

    Accepts ``{"name"?, "budget"?, "max_change"?}``:

    - ``budget`` may be omitted or ``null`` (the optimiser's default 40%-of-
      capital); if given it must be a finite number > 0.
    - ``max_change`` defaults to 0.15 and must be a finite number in (0, 1].
    - ``name`` is optional free text (defaulting to the label, trimmed/capped).

    Raises :class:`ScenarioError` with a human-readable message otherwise.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ScenarioError(f"scenario '{label}' must be an object")

    name = raw.get("name", label)
    if name is None:
        name = label
    if not isinstance(name, str):
        raise ScenarioError(f"scenario '{label}' name must be a string")
    name = name.strip()[:_NAME_MAX] or label

    budget = raw.get("budget")
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise ScenarioError(
                f"scenario '{label}' budget must be a number or null, got {budget!r}"
            )
        budget = float(budget)
        if not math.isfinite(budget) or budget <= 0:
            raise ScenarioError(
                f"scenario '{label}' budget must be a finite number > 0, got {budget!r}"
            )

    max_change = raw.get("max_change", DEFAULT_MAX_CHANGE)
    if max_change is None:
        max_change = DEFAULT_MAX_CHANGE
    if isinstance(max_change, bool) or not isinstance(max_change, (int, float)):
        raise ScenarioError(
            f"scenario '{label}' max_change must be a number, got {max_change!r}"
        )
    max_change = float(max_change)
    if not math.isfinite(max_change) or not (_MAX_CHANGE_LO <= max_change <= _MAX_CHANGE_HI):
        raise ScenarioError(
            f"scenario '{label}' max_change must be in (0, 1], got {max_change!r}"
        )

    return {"name": name, "budget": budget, "max_change": max_change}


def _scenario_kpis(ds: Dataset, spec: dict, routes: dict) -> dict:
    """Run the plan for one scenario and pull its comparable KPIs.

    The assortment is solved with the scenario's *own* budget (the same value
    :func:`dip.prescribe.build_plan` passes internally), so the enrichment KPIs
    (margin captured, capital used, SKUs carried) come from the identical MILP
    solve that produced the plan's assortment lever — they can never disagree.
    """
    plan = build_plan(ds, budget=spec["budget"], max_change=spec["max_change"], routes=routes)
    assort = optimize_assortment(ds, budget=spec["budget"])
    # Invariant: the plan's assortment lever IS this MILP's edge over greedy.
    assert assort["uplift_vs_greedy"] == plan["levers"]["assortment"]

    # Coerce to plain Python numbers: the routing solve threads NumPy float64
    # scalars through the uplift totals, and clean JSON / exact cell equality is
    # easier to reason about with builtins than with np scalars.
    return {
        "name": spec["name"],
        "budget": float(plan["budget"]),
        "full_capital": float(plan["full_capital"]),
        "budget_pct_of_full": (
            round(plan["budget"] / plan["full_capital"], 4) if plan["full_capital"] else 0.0
        ),
        "max_change": float(plan["max_change"]),
        "kpis": {
            "expected_uplift_eur": float(plan["expected_uplift_eur"]),
            "expected_uplift_pct": float(plan["expected_uplift_pct"]),
            "pricing_uplift_eur": float(plan["levers"]["pricing"]),
            "assortment_uplift_eur": float(plan["levers"]["assortment"]),
            "routing_uplift_eur": float(plan["levers"]["routing"]),
            "margin_captured_eur": float(assort["milp"]["margin"]),
            "capital_used_eur": float(assort["milp"]["capital_used"]),
            "skus_carried": int(assort["milp"]["count"]),
            "budget_eur": float(plan["budget"]),
        },
    }


def _delta(key: str, a_val: float, b_val: float) -> dict:
    """Absolute (B − A) and % delta for one KPI, rounded appropriately."""
    diff = b_val - a_val
    if key.endswith("_pct"):
        abs_delta: float = float(round(diff, 4))
    elif key == "skus_carried":
        abs_delta = int(diff)
    else:
        abs_delta = float(round(diff, 2))
    pct_delta = float(round(diff / a_val, 4)) if a_val else None
    return {"abs": abs_delta, "pct": pct_delta}


def compare_scenarios(
    ds: Dataset | None = None,
    *,
    scenario_a: dict | None = None,
    scenario_b: dict | None = None,
    source: dict | None = None,
    routes: dict | None = None,
) -> dict:
    """A/B compare two named budget/guardrail scenarios over ``ds``.

    Returns each scenario's resolved parameters + KPIs, the per-KPI absolute and
    % deltas (B − A), the data-source provenance label, and an honesty note.
    Deterministic: both scenarios share one routing solve (passed in as
    ``routes`` by callers that already hold one, else solved once here).
    """
    ds = ds or build_dataset()
    spec_a = normalize_scenario(scenario_a, label="A")
    spec_b = normalize_scenario(scenario_b, label="B")
    routes = routes if routes is not None else optimize_routes(ds)

    a = _scenario_kpis(ds, spec_a, routes)
    b = _scenario_kpis(ds, spec_b, routes)
    deltas = {key: _delta(key, a["kpis"][key], b["kpis"][key]) for key in KPI_KEYS}

    source = source or {"synthetic": True, "label": "seeded synthetic dataset", "filename": None}
    if source.get("synthetic", True):
        provenance = (
            "Seeded synthetic dataset — deterministic (fixed seed + MILP/CVRP solution "
            "budgets, never wall-clock). Both scenarios share one routing solve; only "
            "pricing and assortment re-optimise per scenario."
        )
    else:
        provenance = (
            f"Imported workbook '{source.get('filename')}' ({source.get('n_skus')} SKUs) — "
            "customers and delivery routing remain the synthetic demo layer. Deterministic; "
            "routing shared across both scenarios."
        )

    return {
        "provenance": provenance,
        "data_source": source,
        "scenario_a": a,
        "scenario_b": b,
        "deltas": deltas,
        "routing_identical": a["kpis"]["routing_uplift_eur"] == b["kpis"]["routing_uplift_eur"],
        "note": (
            "Deltas are scenario B minus scenario A. Routing is identical in both "
            "(one shared solve), so the uplift delta is driven by pricing and assortment "
            "only. The assortment lever is the MILP's edge over the greedy baseline at that "
            "budget, so a larger budget can post a smaller assortment delta even while it "
            "captures more absolute margin — the same effect the budget sensitivity surfaces."
        ),
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    _ds = build_dataset()
    _full = optimize_assortment(_ds)["full_capital"]
    res = compare_scenarios(
        _ds,
        scenario_a={"name": "Tight (25% capital)", "budget": 0.25 * _full, "max_change": 0.15},
        scenario_b={"name": "Loose (80% capital)", "budget": 0.80 * _full, "max_change": 0.15},
    )
    a, b = res["scenario_a"], res["scenario_b"]
    print(f"A {a['name']}: uplift EUR {a['kpis']['expected_uplift_eur']:,.0f}")
    print(f"B {b['name']}: uplift EUR {b['kpis']['expected_uplift_eur']:,.0f}")
    d = res["deltas"]["expected_uplift_eur"]
    print(f"delta uplift EUR {d['abs']:,.2f} ({d['pct']:+.2%})" if d["pct"] is not None else d)
