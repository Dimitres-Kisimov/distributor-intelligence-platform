"""Distributor Intelligence Platform — analytics, forecasting and optimisation
engines behind one Flask decision-intelligence application.

Author: Dimitres Kisimov, 2026. All rights reserved (portfolio review — see LICENSE).
"""

__version__ = "1.0.0"
__author__ = "Dimitres Kisimov"

from .analytics import (
    abc_xyz,
    kpi_drilldown,
    kpis,
    margin_bridge,
    revenue_breakdown,
    rfm_segments,
)
from .crosssell import mine_crosssell
from .data import build_dataset
from .forecast import forecast_revenue
from .inventory import inventory_policy
from .optimize import optimize_assortment, optimize_prices, optimize_routes
from .plandiff import plan_diff
from .prescribe import build_plan
from .reliability import supplier_reliability
from .scenario import compare_scenarios
from .sensitivity import driver_sensitivity

__all__ = [
    "abc_xyz",
    "build_dataset",
    "build_plan",
    "compare_scenarios",
    "driver_sensitivity",
    "forecast_revenue",
    "inventory_policy",
    "kpi_drilldown",
    "kpis",
    "margin_bridge",
    "mine_crosssell",
    "optimize_assortment",
    "optimize_prices",
    "optimize_routes",
    "plan_diff",
    "revenue_breakdown",
    "rfm_segments",
    "supplier_reliability",
]
