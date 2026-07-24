"""Distributor Intelligence Platform — analytics, forecasting and optimisation
engines behind one Flask decision-intelligence application.

Author: Dimitres Kisimov, 2026. MIT licensed.
"""

__version__ = "1.0.0"
__author__ = "Dimitres Kisimov"

from .analytics import abc_xyz, kpis, margin_bridge, revenue_breakdown, rfm_segments
from .data import build_dataset
from .forecast import forecast_revenue
from .optimize import optimize_assortment, optimize_prices, optimize_routes
from .prescribe import build_plan

__all__ = [
    "build_dataset",
    "kpis",
    "revenue_breakdown",
    "abc_xyz",
    "rfm_segments",
    "margin_bridge",
    "forecast_revenue",
    "optimize_assortment",
    "optimize_prices",
    "optimize_routes",
    "build_plan",
]
