"""Spend forecasting + budget burndown — pure, tested.

Projects month-end spend from month-to-date actuals, with a confidence band derived
from daily-spend variability, and compares the projection to budget (pacing,
overage, on-track/at-risk/over). Used by Overview and the executive digest.
"""

from __future__ import annotations

import statistics


def _num(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def month_end_projection(
    mtd_spend: float,
    day_of_month: int,
    days_in_month: int,
    daily_spends: list[float] | None = None,
    default_band_pct: float = 0.15,
) -> dict:
    """Project month-end spend with a low/high confidence band.

    Run-rate projection = (MTD / days elapsed) * days in month. The band widens with
    the day-to-day variability of spend (coefficient of variation), so a spiky
    account gets a wider, more honest range than a steady one.
    """
    mtd = _num(mtd_spend)
    day = max(1, int(day_of_month))
    dim = max(day, int(days_in_month) or 30)
    run_rate = mtd / day
    projection = run_rate * dim

    daily = [_num(d) for d in (daily_spends or []) if _num(d) > 0]
    if len(daily) >= 3:
        mean = statistics.mean(daily)
        sd = statistics.pstdev(daily)
        cv = (sd / mean) if mean > 0 else default_band_pct
        band_frac = min(0.50, max(0.05, cv))
    else:
        band_frac = default_band_pct
    band = projection * band_frac
    return {
        "run_rate_daily": round(run_rate, 2),
        "projection": round(projection, 2),
        "low": round(max(0.0, projection - band), 2),
        "high": round(projection + band, 2),
        "band_pct": round(band_frac * 100, 1),
        "days_remaining": dim - day,
    }


def budget_status(projection: float, budget: float, mtd_spend: float,
                  day_of_month: int, days_in_month: int) -> dict:
    """Compare projection + current pace to budget."""
    budget = _num(budget)
    proj = _num(projection)
    mtd = _num(mtd_spend)
    day = max(1, int(day_of_month))
    dim = max(day, int(days_in_month) or 30)
    if budget <= 0:
        return {"has_budget": False, "state": "No budget set", "pct_of_budget": 0.0,
                "projected_overage": 0.0, "pace_variance": 0.0}
    pct = proj / budget * 100.0
    expected_to_date = budget / dim * day
    pace_variance = mtd - expected_to_date  # positive = ahead of (over) pace
    state = "Over budget" if pct >= 100 else "At risk" if pct >= 90 else "On track"
    return {
        "has_budget": True,
        "state": state,
        "pct_of_budget": round(pct, 1),
        "projected_overage": round(max(0.0, proj - budget), 2),
        "pace_variance": round(pace_variance, 2),
        "budget": round(budget, 2),
    }


def burndown_series(daily_spends: list[float], budget: float, days_in_month: int) -> list[dict]:
    """Cumulative actual spend vs the straight-line budget, by day-of-month."""
    budget = _num(budget)
    dim = max(1, int(days_in_month) or 30)
    per_day = budget / dim if budget > 0 else 0.0
    out = []
    cum = 0.0
    for i, d in enumerate(daily_spends or [], start=1):
        cum += _num(d)
        out.append({
            "DAY": i,
            "CUMULATIVE_ACTUAL": round(cum, 2),
            "BUDGET_LINE": round(per_day * i, 2),
        })
    return out
