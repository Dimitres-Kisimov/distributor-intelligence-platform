"""Single source of truth: one deterministic synthetic dataset.

Every view in the platform (analytics, forecast, optimisation, prescription)
is fed from the objects built here. Generation is fully seeded, so the numbers
are stable across runs, machines and CI — the dashboard, the exported PDF and
the tests all agree.

Entities
--------
- ``skus``       : ~200 products (cost, price, demand, lead time, elasticity, shelf)
- ``monthly``    : month x sku fact table (units, revenue, cogs) with seasonality+trend
- ``customers``  : ~50 delivery customers with coordinates + RFM order history
- ``route_stops``: one representative delivery run for the routing engine
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

SEED = 20260724
N_SKUS = 200
N_CUSTOMERS = 52
N_MONTHS = 24

REGIONS = ["North", "South", "East", "West", "Central"]
REGION_WEIGHTS = np.array([0.26, 0.19, 0.17, 0.15, 0.23])

CHANNELS = ["Retail", "Wholesale", "Online"]
CHANNEL_WEIGHTS = np.array([0.44, 0.38, 0.18])

CATEGORIES = {
    "Beverages": (2.0, 6.0, -1.6),
    "Snacks": (1.2, 4.5, -1.9),
    "Dairy": (1.6, 4.2, -1.3),
    "Frozen": (2.4, 7.5, -1.1),
    "Household": (3.0, 9.5, -0.9),
    "Personal Care": (3.5, 12.0, -0.8),
    "Confectionery": (1.0, 3.8, -2.1),
    "Pantry": (1.4, 4.0, -1.4),
}


@dataclass
class Dataset:
    """Container for the whole synthetic world."""

    skus: list[dict]
    monthly: dict  # column-oriented arrays, one row per (month, sku)
    customers: list[dict]
    route_stops: list[dict]
    depot: dict
    months: list[str] = field(default_factory=list)

    @property
    def n_months(self) -> int:
        return len(self.months)


def _month_labels(n: int) -> list[str]:
    """Return ``n`` consecutive ``YYYY-MM`` labels ending at 2026-06."""
    end_year, end_month = 2026, 6
    labels = []
    y, m = end_year, end_month
    for _ in range(n):
        labels.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(labels))


def _seasonal_factor(month_index: int, phase: float, amp: float) -> float:
    """Yearly seasonality with a category-specific phase and amplitude."""
    return 1.0 + amp * np.sin(2 * np.pi * (month_index % 12) / 12.0 + phase)


def _build_skus(rng: np.random.Generator) -> list[dict]:
    skus = []
    cats = list(CATEGORIES)
    for i in range(N_SKUS):
        cat = cats[i % len(cats)]
        base_cost, base_price, base_elast = CATEGORIES[cat]
        cost = float(np.round(base_cost * rng.uniform(0.75, 1.4), 2))
        margin_mult = rng.uniform(1.9, 3.2)
        price = float(np.round(max(cost * 1.15, base_price * rng.uniform(0.7, 1.3), cost * margin_mult), 2))
        demand_mean = float(np.round(rng.gamma(2.2, 55.0) + 20, 1))
        demand_std = float(np.round(demand_mean * rng.uniform(0.12, 0.4), 2))
        elasticity = float(np.round(base_elast * rng.uniform(0.8, 1.2), 3))
        lead_time = int(rng.integers(3, 21))
        shelf = float(np.round(rng.uniform(0.4, 3.0), 2))
        region = REGIONS[int(rng.choice(len(REGIONS), p=REGION_WEIGHTS))]
        channel = CHANNELS[int(rng.choice(len(CHANNELS), p=CHANNEL_WEIGHTS))]
        skus.append(
            {
                "sku_id": f"SKU-{i + 1:04d}",
                "name": f"{cat[:3].upper()} {rng.integers(100, 999)}-{chr(65 + i % 26)}",
                "category": cat,
                "cost": cost,
                "price": price,
                "unit_margin": float(np.round(price - cost, 2)),
                "demand_mean": demand_mean,
                "demand_std": demand_std,
                "elasticity": elasticity,
                "lead_time_days": lead_time,
                "shelf_space": shelf,
                "region": region,
                "channel": channel,
            }
        )
    return skus


def _build_monthly(rng: np.random.Generator, skus: list[dict], months: list[str]) -> dict:
    n_m = len(months)
    cat_phase = {c: rng.uniform(0, 2 * np.pi) for c in CATEGORIES}
    cat_amp = {c: rng.uniform(0.08, 0.32) for c in CATEGORIES}

    rows_month, rows_sku, rows_region = [], [], []
    rows_channel, rows_category = [], []
    rows_units, rows_revenue, rows_cogs = [], [], []

    trend_per_month = 0.008  # ~0.8% growth/month
    for si, sku in enumerate(skus):
        phase = cat_phase[sku["category"]]
        amp = cat_amp[sku["category"]]
        for mi in range(n_m):
            season = _seasonal_factor(mi, phase, amp)
            trend = (1 + trend_per_month) ** mi
            noise = rng.normal(1.0, sku["demand_std"] / max(sku["demand_mean"], 1e-9))
            units = max(0.0, sku["demand_mean"] * season * trend * noise)
            units = float(np.round(units, 1))
            revenue = units * sku["price"]
            cogs = units * sku["cost"]
            rows_month.append(months[mi])
            rows_sku.append(sku["sku_id"])
            rows_region.append(sku["region"])
            rows_channel.append(sku["channel"])
            rows_category.append(sku["category"])
            rows_units.append(units)
            rows_revenue.append(float(np.round(revenue, 2)))
            rows_cogs.append(float(np.round(cogs, 2)))

    return {
        "month": np.array(rows_month),
        "sku_id": np.array(rows_sku),
        "region": np.array(rows_region),
        "channel": np.array(rows_channel),
        "category": np.array(rows_category),
        "units": np.array(rows_units, dtype=float),
        "revenue": np.array(rows_revenue, dtype=float),
        "cogs": np.array(rows_cogs, dtype=float),
    }


def _build_customers(rng: np.random.Generator, months: list[str]) -> list[dict]:
    """Retail customers with coordinates (for routing) and RFM order history."""
    customers = []
    n_m = len(months)
    tiers = [("Key Account", 4.0, 0.85), ("Growth", 2.0, 0.55), ("Long Tail", 1.0, 0.3)]
    for i in range(N_CUSTOMERS):
        tier_name, spend_mult, order_prob = tiers[int(rng.choice(3, p=[0.2, 0.45, 0.35]))]
        region = REGIONS[int(rng.choice(len(REGIONS), p=REGION_WEIGHTS))]
        # coordinates spread around a central depot (km)
        angle = rng.uniform(0, 2 * np.pi)
        radius = rng.uniform(3, 48)
        x = float(np.round(np.cos(angle) * radius, 2))
        y = float(np.round(np.sin(angle) * radius, 2))

        order_months, order_values = [], []
        for mi in range(n_m):
            if rng.random() < order_prob:
                val = float(np.round(rng.gamma(2.5, 380.0) * spend_mult, 2))
                order_months.append(mi)
                order_values.append(val)
        if not order_months:  # guarantee at least one order
            order_months.append(int(rng.integers(0, n_m)))
            order_values.append(float(np.round(rng.gamma(2.5, 380.0) * spend_mult, 2)))

        recency = n_m - 1 - max(order_months)  # months since last order
        frequency = len(order_months)
        monetary = float(np.round(sum(order_values), 2))
        demand_kg = float(np.round(rng.uniform(40, 320) * spend_mult, 1))
        customers.append(
            {
                "customer_id": f"CUST-{i + 1:03d}",
                "name": f"{region} {tier_name.split()[0]} {i + 1:02d}",
                "region": region,
                "tier": tier_name,
                "x": x,
                "y": y,
                "recency_months": int(recency),
                "frequency": int(frequency),
                "monetary": monetary,
                "demand_kg": demand_kg,
            }
        )
    return customers


def _build_route_stops(rng: np.random.Generator, customers: list[dict]) -> tuple[list[dict], dict]:
    """Pick a representative single-day delivery run: the highest-demand
    customers that ordered most recently, capped for a tractable CVRP."""
    depot = {"customer_id": "DEPOT", "name": "Central Depot", "x": 0.0, "y": 0.0, "demand_kg": 0.0}
    ranked = sorted(customers, key=lambda c: (c["recency_months"], -c["demand_kg"]))
    stops = [dict(c) for c in ranked[:20]]
    return stops, depot


@lru_cache(maxsize=1)
def build_dataset() -> Dataset:
    """Build (and cache) the entire deterministic dataset."""
    rng = np.random.default_rng(SEED)
    months = _month_labels(N_MONTHS)
    skus = _build_skus(rng)
    monthly = _build_monthly(rng, skus, months)
    customers = _build_customers(rng, months)
    route_stops, depot = _build_route_stops(rng, customers)
    return Dataset(
        skus=skus,
        monthly=monthly,
        customers=customers,
        route_stops=route_stops,
        depot=depot,
        months=months,
    )


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    ds = build_dataset()
    print(f"SKUs: {len(ds.skus)}  months: {ds.n_months}  rows: {len(ds.monthly['sku_id'])}")
    print(f"Customers: {len(ds.customers)}  route stops: {len(ds.route_stops)}")
    print(f"Total revenue: EUR {ds.monthly['revenue'].sum():,.0f}")
