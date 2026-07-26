"""Descriptive analytics: KPIs, breakdowns, ABC-XYZ, RFM, margin bridge.

Pure NumPy / stdlib on top of the arrays in :mod:`dip.data`. Every function
returns plain Python types (``dict`` / ``list``) so the Flask API can serialise
them straight to JSON.
"""

from __future__ import annotations

import numpy as np

from .data import Dataset, build_dataset


def _monthly_revenue_series(ds: Dataset) -> tuple[list[str], np.ndarray]:
    """Total revenue per calendar month, in month order."""
    m = ds.monthly
    months = ds.months
    rev = np.zeros(len(months))
    idx = {lab: i for i, lab in enumerate(months)}
    for lab, r in zip(m["month"], m["revenue"]):
        rev[idx[lab]] += r
    return months, rev


def kpis(ds: Dataset | None = None) -> dict:
    """Headline KPIs for the tile row."""
    ds = ds or build_dataset()
    m = ds.monthly
    months, rev_series = _monthly_revenue_series(ds)

    revenue = float(m["revenue"].sum())
    cogs = float(m["cogs"].sum())
    gross_margin = revenue - cogs
    gross_margin_pct = gross_margin / revenue if revenue else 0.0

    # YoY: last 12 months vs the prior 12 months
    if len(rev_series) >= 24:
        last12 = float(rev_series[-12:].sum())
        prev12 = float(rev_series[-24:-12].sum())
        yoy = (last12 - prev12) / prev12 if prev12 else 0.0
    else:
        yoy = 0.0

    # Average order value proxy: revenue per customer order across the horizon
    total_orders = sum(c["frequency"] for c in ds.customers)
    aov = revenue / total_orders if total_orders else 0.0

    # OTIF-style service level: modelled from lead-time reliability of the range
    lead_times = np.array([s["lead_time_days"] for s in ds.skus])
    otif = float(np.clip(1.0 - (lead_times.mean() - 3) / 60.0, 0.80, 0.99))

    return {
        "revenue": round(revenue, 2),
        "cogs": round(cogs, 2),
        "gross_margin": round(gross_margin, 2),
        "gross_margin_pct": round(gross_margin_pct, 4),
        "yoy": round(yoy, 4),
        "aov": round(aov, 2),
        "otif": round(otif, 4),
        "n_skus": len(ds.skus),
        "n_customers": len(ds.customers),
        "months": months,
        "revenue_series": [round(x, 2) for x in rev_series.tolist()],
    }


def _group_sum(keys: np.ndarray, values: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in zip(keys, values):
        out[k] = out.get(k, 0.0) + float(v)
    return out


def revenue_breakdown(ds: Dataset | None = None) -> dict:
    """Revenue split by region, category and channel."""
    ds = ds or build_dataset()
    m = ds.monthly

    def _sorted(d: dict[str, float]) -> list[dict]:
        return [
            {"label": k, "revenue": round(v, 2)}
            for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        ]

    return {
        "region": _sorted(_group_sum(m["region"], m["revenue"])),
        "category": _sorted(_group_sum(m["category"], m["revenue"])),
        "channel": _sorted(_group_sum(m["channel"], m["revenue"])),
    }


def abc_xyz(ds: Dataset | None = None) -> dict:
    """ABC (revenue Pareto) x XYZ (demand variability) classification.

    Returns a 9-cell grid with counts and revenue per cell, plus the per-SKU
    labels. A = top 80% of revenue, B = next 15%, C = last 5%. X/Y/Z by the
    coefficient of variation of monthly units.
    """
    ds = ds or build_dataset()
    m = ds.monthly
    skus = [s["sku_id"] for s in ds.skus]
    meta = {s["sku_id"]: s for s in ds.skus}

    rev_by_sku = _group_sum(m["sku_id"], m["revenue"])
    # per-sku monthly units matrix for CV
    idx = {s: i for i, s in enumerate(skus)}
    month_idx = {lab: j for j, lab in enumerate(ds.months)}
    units_mat = np.zeros((len(skus), len(ds.months)))
    for s, lab, u in zip(m["sku_id"], m["month"], m["units"]):
        units_mat[idx[s], month_idx[lab]] += u
    mean = units_mat.mean(axis=1)
    std = units_mat.std(axis=1)
    cv = np.divide(std, mean, out=np.zeros_like(std), where=mean > 0)

    # ABC by cumulative revenue share
    order = sorted(skus, key=lambda s: rev_by_sku[s], reverse=True)
    total_rev = sum(rev_by_sku.values())
    cum = 0.0
    abc_label: dict[str, str] = {}
    for s in order:
        cum += rev_by_sku[s]
        share = cum / total_rev if total_rev else 1.0
        abc_label[s] = "A" if share <= 0.80 else ("B" if share <= 0.95 else "C")

    def _xyz(c: float) -> str:
        return "X" if c <= 0.15 else ("Y" if c <= 0.30 else "Z")

    xyz_label = {s: _xyz(float(cv[idx[s]])) for s in skus}

    grid = {a + x: {"count": 0, "revenue": 0.0} for a in "ABC" for x in "XYZ"}
    per_sku = []
    for s in skus:
        cell = abc_label[s] + xyz_label[s]
        grid[cell]["count"] += 1
        grid[cell]["revenue"] += rev_by_sku[s]
        per_sku.append(
            {
                "sku_id": s,
                "name": meta[s]["name"],
                "category": meta[s]["category"],
                "abc": abc_label[s],
                "xyz": xyz_label[s],
                "cell": cell,
                "revenue": round(rev_by_sku[s], 2),
                "cv": round(float(cv[idx[s]]), 3),
            }
        )
    grid_out = {k: {"count": v["count"], "revenue": round(v["revenue"], 2)} for k, v in grid.items()}
    return {"grid": grid_out, "per_sku": per_sku, "total_revenue": round(total_rev, 2)}


def rfm_segments(ds: Dataset | None = None) -> dict:
    """RFM scoring and named segments for the customer base."""
    ds = ds or build_dataset()
    custs = ds.customers
    rec = np.array([c["recency_months"] for c in custs], dtype=float)
    freq = np.array([c["frequency"] for c in custs], dtype=float)
    mon = np.array([c["monetary"] for c in custs], dtype=float)

    def _score(vals: np.ndarray, invert: bool = False) -> np.ndarray:
        # quintile score 1..5 (higher = better); recency inverted (low = better)
        ranks = vals.argsort().argsort().astype(float)
        q = 1 + np.floor(5 * ranks / max(len(vals), 1)).clip(0, 4)
        return (6 - q) if invert else q

    r_s = _score(rec, invert=True)
    f_s = _score(freq)
    m_s = _score(mon)

    def _segment(r: float, f: float, m: float) -> str:
        if r >= 4 and f >= 4:
            return "Champions"
        if r >= 4 and f >= 2:
            return "Loyal"
        if r >= 4:
            return "New / Promising"
        if f >= 4 and m >= 4:
            return "At Risk (high value)"
        if r <= 2 and f <= 2:
            return "Hibernating"
        return "Needs Attention"

    seg_counts: dict[str, dict] = {}
    per_customer = []
    for i, c in enumerate(custs):
        seg = _segment(r_s[i], f_s[i], m_s[i])
        d = seg_counts.setdefault(seg, {"count": 0, "monetary": 0.0})
        d["count"] += 1
        d["monetary"] += c["monetary"]
        per_customer.append(
            {
                "customer_id": c["customer_id"],
                "name": c["name"],
                "r": int(r_s[i]),
                "f": int(f_s[i]),
                "m": int(m_s[i]),
                "segment": seg,
                "monetary": round(c["monetary"], 2),
                # raw values behind the quintile scores, so the drill-down can
                # answer "when did they last order / how often" without a lookup
                "recency_months": int(c["recency_months"]),
                "frequency": int(c["frequency"]),
            }
        )
    segments = [
        {"segment": k, "count": v["count"], "monetary": round(v["monetary"], 2)}
        for k, v in sorted(seg_counts.items(), key=lambda kv: kv[1]["monetary"], reverse=True)
    ]
    return {"segments": segments, "per_customer": per_customer}


def margin_bridge(ds: Dataset | None = None) -> dict:
    """Price / Volume / Mix gross-margin bridge: first-half vs second-half.

    Decomposes the change in gross margin between the two halves of the horizon
    into price, volume and mix effects — the classic P/V/M walk.
    """
    ds = ds or build_dataset()
    m = ds.monthly
    months = ds.months
    half = len(months) // 2
    first = set(months[:half])
    second = set(months[half:])

    # aggregate per sku for each period
    skus = {s["sku_id"]: s for s in ds.skus}
    agg: dict[str, dict] = {s: {"u0": 0.0, "u1": 0.0} for s in skus}
    for s, lab, u in zip(m["sku_id"], m["month"], m["units"]):
        if lab in first:
            agg[s]["u0"] += u
        elif lab in second:
            agg[s]["u1"] += u

    # baseline margin (period 0) and current margin (period 1)
    gm0 = sum(agg[s]["u0"] * (skus[s]["price"] - skus[s]["cost"]) for s in skus)
    gm1 = sum(agg[s]["u1"] * (skus[s]["price"] - skus[s]["cost"]) for s in skus)

    tot_u0 = sum(agg[s]["u0"] for s in skus)
    tot_u1 = sum(agg[s]["u1"] for s in skus)
    vol_growth = (tot_u1 / tot_u0 - 1.0) if tot_u0 else 0.0

    # Volume effect: scale baseline margin by overall volume growth
    volume_effect = gm0 * vol_growth
    # Mix effect: change from shifting weight toward higher/lower-margin SKUs
    # (holding total volume of period 1), relative to a mix-neutral baseline.
    unit_margin = {s: skus[s]["price"] - skus[s]["cost"] for s in skus}
    avg_margin0 = gm0 / tot_u0 if tot_u0 else 0.0
    mix_effect = sum(agg[s]["u1"] * unit_margin[s] for s in skus) - tot_u1 * avg_margin0
    # Price effect is the residual (prices are static in the synthetic world, so
    # this is small and mostly rounding — reported honestly rather than faked).
    price_effect = (gm1 - gm0) - volume_effect - mix_effect

    return {
        "baseline": round(gm0, 2),
        "current": round(gm1, 2),
        "steps": [
            {"label": "Volume", "value": round(volume_effect, 2)},
            {"label": "Mix", "value": round(mix_effect, 2)},
            {"label": "Price", "value": round(price_effect, 2)},
        ],
    }
