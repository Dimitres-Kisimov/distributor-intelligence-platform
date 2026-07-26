"""Executive deliverables generated from the same plan the dashboard shows.

- :func:`build_pdf`   -> multi-page executive review (matplotlib ``PdfPages``)
- :func:`build_excel` -> multi-sheet workbook (openpyxl)

Both take an in-memory buffer so the Flask endpoints can stream them and the
CLI script can write them to ``deliverables/``.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # headless: safe in CI / containers
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from .analytics import (
    abc_xyz,
    kpis,
    margin_bridge,
    revenue_breakdown,
    rfm_segments,
)
from .data import build_dataset
from .forecast import forecast_revenue
from .optimize import optimize_assortment, optimize_prices
from .prescribe import build_plan

INK = "#1a2233"
BLUE = "#2f6bff"
GREEN = "#1d9e6f"
PINK = "#ea4b71"
GREY = "#8a93a6"


def _eur(x: float) -> str:
    return f"EUR {x:,.0f}"


def _scenario_line(plan: dict) -> str:
    """One line naming the scenario a deliverable was computed for."""
    budget_pct = plan["budget"] / plan["full_capital"] if plan.get("full_capital") else 0.0
    return (
        f"Scenario: working-capital budget {_eur(plan['budget'])} of {_eur(plan['full_capital'])} "
        f"({budget_pct:.0%}), price guardrail +/-{plan['max_change']:.0%}"
    )


def build_pdf(budget: float | None = None, max_change: float = 0.15) -> bytes:
    """Render the executive-review PDF for the given scenario and return its bytes.

    ``budget`` / ``max_change`` mirror the dashboard controls so the exported
    deck matches what was on screen when the user clicked Export.
    """
    ds = build_dataset()
    k = kpis(ds)
    fc = forecast_revenue(ds)
    plan = build_plan(ds, budget=budget, max_change=max_change)
    mb = margin_bridge(ds)
    rb = revenue_breakdown(ds)
    az = abc_xyz(ds)

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # ---- Page 1: cover + KPIs ----------------------------------------
        fig = plt.figure(figsize=(11.69, 8.27))  # A4 landscape
        fig.patch.set_facecolor("white")
        fig.text(0.06, 0.90, "Distributor Intelligence Platform", fontsize=26, weight="bold", color=INK)
        fig.text(0.06, 0.855, "Executive decision review", fontsize=14, color=GREY)
        fig.text(0.06, 0.80, f"Expected annual uplift: {_eur(plan['expected_uplift_eur'])} "
                             f"({plan['expected_uplift_pct']:.1%} of annual gross margin)",
                 fontsize=15, color=GREEN, weight="bold")
        fig.text(0.06, 0.765, _scenario_line(plan), fontsize=10.5, color=GREY)

        tiles = [
            ("Revenue (24 mo)", _eur(k["revenue"])),
            ("Gross margin", f"{k['gross_margin_pct']:.1%}"),
            ("YoY growth", f"{k['yoy']:+.1%}"),
            ("Forecast MASE", f"{fc['mase']}"),
            ("OTIF service", f"{k['otif']:.1%}"),
            ("SKUs / customers", f"{k['n_skus']} / {k['n_customers']}"),
        ]
        for i, (lab, val) in enumerate(tiles):
            x = 0.06 + (i % 3) * 0.31
            y = 0.60 - (i // 3) * 0.22
            fig.text(x, y, val, fontsize=20, weight="bold", color=BLUE)
            fig.text(x, y - 0.05, lab, fontsize=11, color=GREY)
        fig.text(0.06, 0.06, "Synthetic data - generated deterministically. Author: Dimitres Kisimov, 2026.",
                 fontsize=8, color=GREY)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- Page 2: revenue history + forecast --------------------------
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        hist_x = list(range(len(fc["history"])))
        fut_x = list(range(len(fc["history"]), len(fc["history"]) + len(fc["forecast"])))
        ax.plot(hist_x, fc["history"], color=BLUE, lw=2.2, label="Actual revenue")
        ax.plot(fut_x, fc["forecast"], color=GREEN, lw=2.2, ls="--", label="Forecast")
        ax.fill_between(fut_x, fc["lower"], fc["upper"], color=GREEN, alpha=0.15, label="~80% band")
        ax.set_title(f"Monthly revenue and forecast  |  {fc['method']}  |  MASE {fc['mase']}",
                     fontsize=14, color=INK, weight="bold")
        ax.set_ylabel("EUR / month")
        ax.legend(frameon=False)
        ax.spines[["top", "right"]].set_visible(False)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- Page 3: margin bridge + revenue by region -------------------
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.69, 8.27))
        # waterfall
        labels = ["Baseline"] + [s["label"] for s in mb["steps"]] + ["Current"]
        vals = [mb["baseline"]] + [s["value"] for s in mb["steps"]] + [mb["current"]]
        running = mb["baseline"]
        for i, (lab, v) in enumerate(zip(labels, vals)):
            if i == 0 or i == len(labels) - 1:
                ax1.bar(i, v, color=INK)
            else:
                color = GREEN if v >= 0 else PINK
                ax1.bar(i, v, bottom=running, color=color)
                running += v
        ax1.set_xticks(range(len(labels)))
        ax1.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
        ax1.set_title("Gross-margin bridge (H1 -> H2)", fontsize=12, color=INK, weight="bold")
        ax1.spines[["top", "right"]].set_visible(False)
        # region bars
        regs = rb["region"]
        ax2.barh([r["label"] for r in regs], [r["revenue"] for r in regs], color=BLUE)
        ax2.invert_yaxis()
        ax2.set_title("Revenue by region", fontsize=12, color=INK, weight="bold")
        ax2.spines[["top", "right"]].set_visible(False)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- Page 4: recommended actions ---------------------------------
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.text(0.06, 0.92, "Recommended actions", fontsize=20, weight="bold", color=INK)
        y = 0.82
        for c in plan["cards"]:
            fig.text(0.06, y, f"[{c['lever']}]  {c['title']}", fontsize=13, weight="bold", color=BLUE)
            fig.text(0.06, y - 0.035, c["detail"], fontsize=10, color=INK, wrap=True)
            fig.text(0.82, y, _eur(c["impact_eur"]), fontsize=12, weight="bold", color=GREEN)
            y -= 0.13
        _ = az  # abc-xyz already summarised in workbook; keep import meaningful
        pdf.savefig(fig)
        plt.close(fig)

    return buf.getvalue()


def build_excel(budget: float | None = None, max_change: float = 0.15) -> bytes:
    """Render the multi-sheet workbook for the given scenario and return its bytes."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    ds = build_dataset()
    k = kpis(ds)
    fc = forecast_revenue(ds)
    plan = build_plan(ds, budget=budget, max_change=max_change)
    rb = revenue_breakdown(ds)
    az = abc_xyz(ds)
    assort = optimize_assortment(ds, budget=budget)
    prices = optimize_prices(ds, max_change=max_change)
    rfm = rfm_segments(ds)

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="2F6BFF")
    head_font = Font(bold=True, color="FFFFFF")

    def _header(ws, row: int, cols: list[str]):
        for j, c in enumerate(cols, start=1):
            cell = ws.cell(row=row, column=j, value=c)
            cell.fill = head_fill
            cell.font = head_font

    # Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Distributor Intelligence Platform - Executive Summary"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = _scenario_line(plan)
    ws["A2"].font = Font(italic=True, size=10, color="6B7488")
    rows = [
        ("Revenue (24 mo)", k["revenue"]),
        ("Gross margin", k["gross_margin"]),
        ("Gross margin %", k["gross_margin_pct"]),
        ("YoY growth", k["yoy"]),
        ("OTIF service (modelled)", k["otif"]),
        ("Forecast MASE", fc["mase"]),
        ("Scenario: working-capital budget (EUR)", plan["budget"]),
        ("Scenario: price guardrail (+/-)", plan["max_change"]),
        ("Expected annual uplift (EUR)", plan["expected_uplift_eur"]),
        ("Uplift % of annual gross margin", plan["expected_uplift_pct"]),
    ]
    _header(ws, 3, ["Metric", "Value"])
    for i, (m, v) in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=m)
        ws.cell(row=i, column=2, value=v)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 20

    # Forecast
    ws = wb.create_sheet("Forecast")
    _header(ws, 1, ["Month", "Type", "Revenue", "Lower", "Upper"])
    r = 2
    for m, v in zip(fc["history_months"], fc["history"]):
        ws.cell(row=r, column=1, value=m); ws.cell(row=r, column=2, value="actual")
        ws.cell(row=r, column=3, value=v); r += 1
    for m, v, lo, hi in zip(fc["forecast_months"], fc["forecast"], fc["lower"], fc["upper"]):
        ws.cell(row=r, column=1, value=m); ws.cell(row=r, column=2, value="forecast")
        ws.cell(row=r, column=3, value=v); ws.cell(row=r, column=4, value=lo)
        ws.cell(row=r, column=5, value=hi); r += 1

    # Revenue breakdown
    ws = wb.create_sheet("Revenue")
    _header(ws, 1, ["Dimension", "Label", "Revenue"])
    r = 2
    for dim in ("region", "category", "channel"):
        for row in rb[dim]:
            ws.cell(row=r, column=1, value=dim); ws.cell(row=r, column=2, value=row["label"])
            ws.cell(row=r, column=3, value=row["revenue"]); r += 1

    # ABC-XYZ
    ws = wb.create_sheet("ABC-XYZ")
    _header(ws, 1, ["Cell", "SKU count", "Revenue"])
    for r, (cell, v) in enumerate(sorted(az["grid"].items()), start=2):
        ws.cell(row=r, column=1, value=cell); ws.cell(row=r, column=2, value=v["count"])
        ws.cell(row=r, column=3, value=v["revenue"])

    # Assortment
    ws = wb.create_sheet("Assortment")
    _header(ws, 1, ["Plan", "SKUs carried", "Margin", "Capital used"])
    ws.append(["MILP (optimal)", assort["milp"]["count"], assort["milp"]["margin"], assort["milp"]["capital_used"]])
    ws.append(["Greedy baseline", assort["greedy"]["count"], assort["greedy"]["margin"], assort["greedy"]["capital_used"]])
    ws.append(["Uplift", "", assort["uplift_vs_greedy"], ""])

    # Pricing (top elasticity-guided moves by profit delta)
    ws = wb.create_sheet("Pricing")
    _header(ws, 1, ["SKU", "Category", "Price old", "Price new", "Change %", "Elasticity", "Profit delta (EUR)"])
    for r, rec in enumerate(prices["recommendations"][:40], start=2):
        ws.cell(row=r, column=1, value=rec["sku_id"]); ws.cell(row=r, column=2, value=rec["category"])
        ws.cell(row=r, column=3, value=rec["price_old"]); ws.cell(row=r, column=4, value=rec["price_new"])
        ws.cell(row=r, column=5, value=rec["change_pct"]); ws.cell(row=r, column=6, value=rec["elasticity"])
        ws.cell(row=r, column=7, value=rec["profit_delta"])
    ws.column_dimensions["B"].width = 16

    # RFM segments
    ws = wb.create_sheet("RFM")
    _header(ws, 1, ["Segment", "Customers", "Monetary (EUR)"])
    for r, seg in enumerate(rfm["segments"], start=2):
        ws.cell(row=r, column=1, value=seg["segment"]); ws.cell(row=r, column=2, value=seg["count"])
        ws.cell(row=r, column=3, value=seg["monetary"])
    ws.column_dimensions["A"].width = 24

    # Actions
    ws = wb.create_sheet("Actions")
    _header(ws, 1, ["Lever", "Action", "Impact (EUR)", "Confidence"])
    for r, c in enumerate(plan["cards"], start=2):
        ws.cell(row=r, column=1, value=c["lever"]); ws.cell(row=r, column=2, value=c["title"])
        ws.cell(row=r, column=3, value=c["impact_eur"]); ws.cell(row=r, column=4, value=c["confidence"])
    ws.column_dimensions["B"].width = 44

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
