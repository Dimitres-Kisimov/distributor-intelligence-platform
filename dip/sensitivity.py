"""Driver sensitivity: how robust is the plan to the assumptions it rides on?

The dashboard leads with one headline — an expected annual € uplift — computed
on a set of *exogenous drivers* nobody at the distributor actually controls:
how much demand shows up, how long suppliers take to replenish, and what the
goods cost. This engine answers the sceptical-exec question directly: **if those
drivers move, how much does the headline move with them?**

It is a one-at-a-time (OAT) sensitivity / tornado analysis. For each driver —
demand level, supplier lead time, unit cost — it shocks that driver alone by
``±shock`` across the whole range, re-runs the *same* engine stack the dashboard
uses on a perturbed copy of the dataset, and records how both the descriptive
KPIs (revenue, gross margin, margin %, OTIF) and the prescriptive plan (the
uplift and its three monetised levers) respond. The drivers are then ranked by
the width of their effect on the headline uplift — that ranking is the tornado.

What makes it honest rather than hand-wavy
------------------------------------------
- **Nothing is re-invented.** Every perturbed figure comes from
  :func:`dip.analytics.kpis` and :func:`dip.prescribe.build_plan` — the exact
  functions the live dashboard serves — run on a perturbed dataset, so a
  perturbed uplift still equals the sum of its three levers (the same identity
  :mod:`dip.reconcile` pins on the base case) and cannot quote a figure the rest
  of the platform would disagree with.
- **Routing is shared, so its lever is flat.** The delivery run depends on the
  customer/geography layer, not on any of the three SKU drivers, so the base
  routing solve is threaded into every perturbed plan. The routing lever is
  therefore identical in every row — a genuine finding (these drivers don't move
  logistics), disclosed rather than hidden.
- **Some non-effects are the point.** A pure demand (volume) shock scales
  revenue and COGS together, so gross-margin *percentage* is left unchanged; a
  lead-time shock never touches the revenue ledger. The analysis surfaces which
  drivers move which numbers, including the ones they leave alone.

Deterministic: the dataset is seeded, the shared routing solve terminates on a
fixed solution budget (never a wall clock), and the perturbations are pure
arithmetic — so :func:`driver_sensitivity` returns the same structure every run.

Everything is computed on the seeded synthetic dataset (or a user's imported
workbook); it is illustrative of the method, not a real-world claim.
"""

from __future__ import annotations

import math
import os
import sys
from contextlib import contextmanager
from dataclasses import replace

import numpy as np

from .analytics import kpis
from .data import Dataset, build_dataset
from .optimize import optimize_routes
from .prescribe import build_plan


@contextmanager
def _silence_solver_stdout():
    """Mute the C-level HiGHS branch-and-bound line the MILP writes to stdout.

    ``scipy.optimize.milp``'s HiGHS backend prints an internal progress line to
    the process's stdout *file descriptor* on some builds; running the optimiser
    six times for the tornado would otherwise splatter it across app startup and
    CLI output (a Python-level ``redirect_stdout`` can't catch a C-level write).
    This redirects fd 1 to ``os.devnull`` for the duration of the perturbed
    solves only, then restores it — a contained, cosmetic fix that changes no
    engine number. Falls back to running unsuppressed if fd 1 isn't a real,
    dup-able descriptor (e.g. an exotic capture harness).
    """
    try:
        sys.stdout.flush()
        saved_fd = os.dup(1)
    except (OSError, ValueError):  # pragma: no cover - no real stdout fd
        yield
        return
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 1)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved_fd, 1)
        os.close(devnull_fd)
        os.close(saved_fd)

DEFAULT_SHOCK = 0.10
_SHOCK_LO = 1e-6
_SHOCK_HI = 1.0  # exclusive: a shock must stay in (0, 1)

# The three exogenous drivers, in display order. (id, label, which KPIs it can
# move, one-line reading of the mechanism.)
DRIVERS: tuple[tuple[str, str, str], ...] = (
    (
        "demand",
        "Demand level (units sold)",
        (
            "scales revenue, COGS and gross margin together — so margin % is left "
            "unchanged — and lifts the pricing and assortment levers with volume; "
            "routing is unaffected."
        ),
    ),
    (
        "lead_time",
        "Supplier lead time (days)",
        (
            "raises the working capital each SKU ties up (longer replenishment = "
            "more cycle stock), tightening the assortment budget and lowering the "
            "modelled OTIF; it never touches the revenue ledger."
        ),
    ),
    (
        "cost",
        "Unit cost of goods",
        (
            "flows straight into COGS and unit margin — the biggest lever on gross "
            "margin and on the pricing/assortment economics — while leaving revenue "
            "(prices held) and routing untouched."
        ),
    ),
)

# The comparable outcome fields pulled for every scenario, in display order.
OUTCOME_KEYS: tuple[str, ...] = (
    "revenue",
    "gross_margin",
    "gross_margin_pct",
    "otif",
    "expected_uplift_eur",
    "expected_uplift_pct",
    "pricing_uplift_eur",
    "assortment_uplift_eur",
    "routing_uplift_eur",
)


class SensitivityError(ValueError):
    """Invalid sensitivity request — mapped to HTTP 400 by the API layer."""


def _validate_shock(shock: float) -> float:
    if isinstance(shock, bool) or not isinstance(shock, (int, float)):
        raise SensitivityError(f"shock must be a number in (0, 1), got {shock!r}")
    shock = float(shock)
    if not math.isfinite(shock) or not (_SHOCK_LO <= shock < _SHOCK_HI):
        raise SensitivityError(f"shock must be a finite fraction in (0, 1), got {shock!r}")
    return shock


def _perturb(
    ds: Dataset, *, demand_mult: float = 1.0, lead_time_mult: float = 1.0, cost_mult: float = 1.0
) -> Dataset:
    """Return a new dataset with one or more drivers scaled — the base untouched.

    Builds fresh SKU dicts and monthly arrays (the cached ``build_dataset``
    objects are shared, so they must never be mutated in place). Prices are held
    — a cost shock is a margin shock, not a pass-through repricing — and the
    monthly ledger is recomputed from the scaled units and shocked cost so it
    stays internally consistent (revenue = units·price, COGS = units·cost).
    Customers, routing geography, months and order baskets are unchanged: these
    three drivers live entirely on the SKU/ledger layer.
    """
    price_by_sku = {s["sku_id"]: s["price"] for s in ds.skus}
    cost_by_sku = {s["sku_id"]: s["cost"] for s in ds.skus}

    new_skus = []
    for s in ds.skus:
        new_cost = s["cost"] * cost_mult
        new_skus.append(
            {
                **s,
                "cost": new_cost,
                "unit_margin": s["price"] - new_cost,  # price held
                "demand_mean": s["demand_mean"] * demand_mult,
                "demand_std": s["demand_std"] * demand_mult,
                "lead_time_days": max(1.0, s["lead_time_days"] * lead_time_mult),
            }
        )

    m = ds.monthly
    new_units = m["units"] * demand_mult
    sku_price = np.array([price_by_sku[sid] for sid in m["sku_id"]], dtype=float)
    sku_cost = np.array([cost_by_sku[sid] for sid in m["sku_id"]], dtype=float) * cost_mult
    # Round revenue/COGS per row to 2dp exactly as ``dip.data`` builds the ledger,
    # so the perturbed KPIs sit on the same basis as the base: an identity shock
    # reproduces the base ledger byte-for-byte, and a genuine non-effect (a
    # cost shock on revenue, a lead-time shock on the whole ledger) is exactly
    # zero rather than a few euros of rounding drift.
    new_monthly = {
        **m,
        "units": new_units,
        "revenue": np.round(new_units * sku_price, 2),
        "cogs": np.round(new_units * sku_cost, 2),
    }
    return replace(ds, skus=new_skus, monthly=new_monthly)


def _outcome(ds: Dataset, routes: dict) -> dict:
    """Descriptive + prescriptive outcome for one (possibly perturbed) dataset.

    Reuses the shared ``routes`` solve so the routing lever is on an identical
    basis across every scenario; everything else re-runs on ``ds``.
    """
    k = kpis(ds)
    plan = build_plan(ds, routes=routes)
    return {
        "revenue": float(k["revenue"]),
        "gross_margin": float(k["gross_margin"]),
        "gross_margin_pct": float(k["gross_margin_pct"]),
        "otif": float(k["otif"]),
        "expected_uplift_eur": float(plan["expected_uplift_eur"]),
        "expected_uplift_pct": float(plan["expected_uplift_pct"]),
        "pricing_uplift_eur": float(plan["levers"]["pricing"]),
        "assortment_uplift_eur": float(plan["levers"]["assortment"]),
        "routing_uplift_eur": float(plan["levers"]["routing"]),
    }


def _outcome_from_cached(k: dict, plan: dict) -> dict:
    """Base outcome built from the platform's own cached KPI + plan objects.

    Guarantees the sensitivity base is byte-for-byte the number the dashboard
    shows — the analysis is anchored to the live plan, not a re-solve of it.
    """
    return {
        "revenue": float(k["revenue"]),
        "gross_margin": float(k["gross_margin"]),
        "gross_margin_pct": float(k["gross_margin_pct"]),
        "otif": float(k["otif"]),
        "expected_uplift_eur": float(plan["expected_uplift_eur"]),
        "expected_uplift_pct": float(plan["expected_uplift_pct"]),
        "pricing_uplift_eur": float(plan["levers"]["pricing"]),
        "assortment_uplift_eur": float(plan["levers"]["assortment"]),
        "routing_uplift_eur": float(plan["levers"]["routing"]),
    }


def _delta_block(base: dict, low: dict, high: dict, key: str) -> dict:
    """Base/low/high values of one metric plus the deltas and the tornado width."""
    b, lo, hi = base[key], low[key], high[key]
    dp = 4 if key.endswith("_pct") else 2
    return {
        "base": round(b, dp),
        "low": round(lo, dp),
        "high": round(hi, dp),
        "low_delta": round(lo - b, dp),
        "high_delta": round(hi - b, dp),
        "swing": round(abs(hi - lo), dp),  # full tornado-bar width (low → high)
    }


def driver_sensitivity(
    ds: Dataset | None = None,
    *,
    shock: float = DEFAULT_SHOCK,
    routes: dict | None = None,
    base_plan: dict | None = None,
    base_kpis: dict | None = None,
    source: dict | None = None,
) -> dict:
    """One-at-a-time driver sensitivity of the plan to demand / lead time / cost.

    Shocks each driver alone by ``±shock`` (a fraction in ``(0, 1)``, default
    0.10), re-runs the engine stack on a perturbed dataset copy, and returns each
    driver's low/high outcomes with deltas versus the base, a tornado ranking by
    the effect on the headline uplift, and a provenance label.

    Callers holding the live plan/KPIs/routing (the Flask cache, the CLI) pass
    them in so the base ties exactly to the served plan and the routing solve is
    shared with every perturbed run. Deterministic: same inputs → same output.
    """
    ds = ds or build_dataset()
    shock = _validate_shock(shock)
    routes = routes if routes is not None else optimize_routes(ds)

    lo_mult, hi_mult = 1.0 - shock, 1.0 + shock
    # Which perturbation argument each driver drives.
    driver_arg = {"demand": "demand_mult", "lead_time": "lead_time_mult", "cost": "cost_mult"}

    # All the optimiser re-solves happen inside one stdout-muted block (the MILP
    # backend chatters on stdout when run many times); the results are pure data.
    with _silence_solver_stdout():
        if base_plan is not None and base_kpis is not None:
            base = _outcome_from_cached(base_kpis, base_plan)
        else:
            base = _outcome(ds, routes)

        drivers_out = []
        for driver_id, label, mechanism in DRIVERS:
            arg = driver_arg[driver_id]
            low = _outcome(_perturb(ds, **{arg: lo_mult}), routes)
            high = _outcome(_perturb(ds, **{arg: hi_mult}), routes)
            drivers_out.append(
                {
                    "id": driver_id,
                    "label": label,
                    "mechanism": mechanism,
                    "low_mult": round(lo_mult, 6),
                    "high_mult": round(hi_mult, 6),
                    "low": {k: round(low[k], 4 if k.endswith("_pct") else 2) for k in OUTCOME_KEYS},
                    "high": {k: round(high[k], 4 if k.endswith("_pct") else 2) for k in OUTCOME_KEYS},
                    "metrics": {k: _delta_block(base, low, high, k) for k in OUTCOME_KEYS},
                }
            )

    # Tornado: drivers ranked by how wide they swing the headline uplift.
    tornado = sorted(
        (
            {
                "id": d["id"],
                "label": d["label"],
                "low_delta": d["metrics"]["expected_uplift_eur"]["low_delta"],
                "high_delta": d["metrics"]["expected_uplift_eur"]["high_delta"],
                "swing": d["metrics"]["expected_uplift_eur"]["swing"],
            }
            for d in drivers_out
        ),
        key=lambda t: t["swing"],
        reverse=True,
    )

    source = source or {"synthetic": True, "label": "seeded synthetic dataset", "filename": None}
    if source.get("synthetic", True):
        provenance = (
            "Seeded synthetic dataset — deterministic (fixed seed + shared CVRP solution "
            "budget, never wall-clock). One-at-a-time shocks; the base ties to the live plan."
        )
    else:
        provenance = (
            f"Imported workbook '{source.get('filename')}' ({source.get('n_skus')} SKUs) — "
            "customers and delivery routing remain the synthetic demo layer. Deterministic; "
            "routing shared across the base and every perturbed run."
        )

    return {
        "shock": round(shock, 6),
        "shock_label": f"±{shock * 100:.0f}%",
        "base": {k: round(base[k], 4 if k.endswith("_pct") else 2) for k in OUTCOME_KEYS},
        "drivers": drivers_out,
        "tornado": tornado,
        "most_sensitive_driver": tornado[0]["id"] if tornado else None,
        "provenance": provenance,
        "data_source": source,
        "note": (
            "One-at-a-time (OAT) driver sensitivity: each driver is shocked alone "
            f"by {round(shock * 100, 1):g}% across the whole range and the entire engine "
            "stack is re-run on a perturbed copy of the dataset. Deltas are the perturbed "
            "outcome minus the base; the tornado ranks drivers by how wide they swing the "
            "expected annual uplift (low → high). The routing lever is identical in every "
            "row — the base routing solve is shared, because none of these three drivers "
            "moves the delivery run."
        ),
        "caveats": [
            (
                "OAT holds the other two drivers fixed, so it does not capture "
                "interactions between drivers (a joint demand-and-cost move is not the "
                "sum of the two single moves)."
            ),
            (
                "The shock is applied uniformly across the whole range; a real shock "
                "would hit categories or SKUs unevenly."
            ),
            (
                "Prices are held under a cost shock — this measures margin exposure, "
                "not a pricing response; the pricing engine's reaction is a separate lever."
            ),
            (
                "The perturbed uplift still equals the sum of its three levers (the same "
                "identity the reconciliation guard pins on the base case), so the analysis "
                "cannot quote a figure the rest of the platform disagrees with."
            ),
        ],
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    s = driver_sensitivity()
    print(f"Base expected uplift: EUR {s['base']['expected_uplift_eur']:,.2f}  (shock {s['shock_label']})")
    print("Tornado (widest swing on the uplift first):")
    for t in s["tornado"]:
        print(
            f"  {t['id']:<10} low {t['low_delta']:+,.0f}  high {t['high_delta']:+,.0f}  "
            f"swing EUR {t['swing']:,.0f}"
        )
