"""Monthly-revenue forecasting with an honest rolling-origin backtest.

A compact additive Holt-Winters ("triple exponential smoothing") with a
12-month seasonal cycle, plus a seasonal-naive fallback for short histories.
Accuracy is measured with **MASE** (Mean Absolute Scaled Error) via a
rolling-origin backtest, so the number reported on the dashboard is earned,
not asserted.
"""

from __future__ import annotations

import numpy as np

from .analytics import _monthly_revenue_series
from .data import Dataset, build_dataset

SEASON = 12


def _holt_winters_additive(
    y: np.ndarray,
    horizon: int,
    season: int = SEASON,
    alpha: float = 0.4,
    beta: float = 0.1,
    gamma: float = 0.3,
) -> np.ndarray:
    """Additive Holt-Winters. Falls back to seasonal-naive+trend if the history
    is shorter than one full season plus two points."""
    n = len(y)
    if n < season + 2:
        return _seasonal_naive(y, horizon, season)

    # initialise level/trend/seasonals from the first cycle (+ whatever follows)
    level = float(y[:season].mean())
    tail = y[season:]
    trend = float((tail.mean() - level) / season) if len(tail) else 0.0
    seasonal = list(y[:season] - level)

    for t in range(season, n):
        last_level = level
        s = seasonal[t - season]
        level = alpha * (y[t] - s) + (1 - alpha) * (last_level + trend)
        trend = beta * (level - last_level) + (1 - beta) * trend
        seasonal.append(gamma * (y[t] - level) + (1 - gamma) * s)

    out = np.empty(horizon)
    for h in range(1, horizon + 1):
        s = seasonal[n - season + ((h - 1) % season)]
        out[h - 1] = level + h * trend + s
    return np.clip(out, 0.0, None)


def _seasonal_naive(y: np.ndarray, horizon: int, season: int = SEASON) -> np.ndarray:
    n = len(y)
    if n == 0:
        return np.zeros(horizon)
    if n < season:
        # naive + drift
        drift = (y[-1] - y[0]) / max(n - 1, 1)
        return np.clip(np.array([y[-1] + drift * h for h in range(1, horizon + 1)]), 0.0, None)
    out = np.empty(horizon)
    for h in range(1, horizon + 1):
        out[h - 1] = y[n - season + ((h - 1) % season)]
    return np.clip(out, 0.0, None)


def _mase(actual: np.ndarray, predicted: np.ndarray, train: np.ndarray, season: int = SEASON) -> float:
    """MASE scaled by the in-sample seasonal-naive MAE of the training set."""
    m = season if len(train) > season else 1
    denom = np.mean(np.abs(train[m:] - train[:-m])) if len(train) > m else np.mean(np.abs(np.diff(train)))
    if denom == 0 or not np.isfinite(denom):
        return float("nan")
    return float(np.mean(np.abs(actual - predicted)) / denom)


def backtest(y: np.ndarray, min_train: int | None = None, season: int = SEASON) -> dict:
    """Rolling-origin one-step-ahead backtest, returns mean MASE + fold detail."""
    n = len(y)
    min_train = min_train or (season + 3)
    errors, folds = [], []
    for origin in range(min_train, n):
        train = y[:origin]
        actual = y[origin]
        pred = _holt_winters_additive(train, 1, season)[0]
        score = _mase(np.array([actual]), np.array([pred]), train, season)
        if np.isfinite(score):
            errors.append(score)
            folds.append({"origin": origin, "actual": round(float(actual), 2), "pred": round(float(pred), 2)})
    mase = float(np.mean(errors)) if errors else float("nan")
    return {"mase": round(mase, 4) if np.isfinite(mase) else None, "n_folds": len(errors), "folds": folds}


def forecast_revenue(ds: Dataset | None = None, horizon: int = 6) -> dict:
    """Forecast total monthly revenue ``horizon`` months ahead with a band."""
    ds = ds or build_dataset()
    months, y = _monthly_revenue_series(ds)
    point = _holt_winters_additive(y, horizon)

    bt = backtest(y)
    # residual scale from backtest folds -> a simple symmetric prediction band
    if bt["folds"]:
        resid = np.array([f["actual"] - f["pred"] for f in bt["folds"]])
        sigma = float(np.std(resid))
    else:
        sigma = float(np.std(y) * 0.1)
    # widen the band with horizon (uncertainty grows)
    z = 1.28  # ~80% interval
    lower = [max(0.0, float(point[h] - z * sigma * np.sqrt(h + 1))) for h in range(horizon)]
    upper = [float(point[h] + z * sigma * np.sqrt(h + 1)) for h in range(horizon)]

    # future month labels
    last = months[-1]
    fy, fm = int(last[:4]), int(last[5:])
    fut_labels = []
    for _ in range(horizon):
        fm += 1
        if fm == 13:
            fm = 1
            fy += 1
        fut_labels.append(f"{fy:04d}-{fm:02d}")

    return {
        "history_months": months,
        "history": [round(float(v), 2) for v in y],
        "forecast_months": fut_labels,
        "forecast": [round(float(v), 2) for v in point],
        "lower": [round(v, 2) for v in lower],
        "upper": [round(v, 2) for v in upper],
        "mase": bt["mase"],
        "n_folds": bt["n_folds"],
        "method": "Holt-Winters additive (12-month season)"
        if len(y) >= SEASON + 2
        else "seasonal-naive + drift",
        "next_month_revenue": round(float(point[0]), 2),
        "horizon_revenue": round(float(point.sum()), 2),
    }


if __name__ == "__main__":  # pragma: no cover
    f = forecast_revenue()
    print(f"MASE={f['mase']} folds={f['n_folds']} method={f['method']}")
    print(f"next month EUR {f['next_month_revenue']:,.0f}  6-mo EUR {f['horizon_revenue']:,.0f}")
