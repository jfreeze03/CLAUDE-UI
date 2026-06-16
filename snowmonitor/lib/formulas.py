"""Cost / credit formulas — one tested source of truth.

Rates come from config. Every credit->dollar conversion in the app routes through
these functions so the rate and rounding live in exactly one place, and the SQL
fragments below are the canonical, NULL-safe credit expressions used by queries.py.
"""

from __future__ import annotations

import config

CREDIT_PRICE_USD = config.CREDIT_PRICE_USD
AI_CREDIT_PRICE_USD = config.AI_CREDIT_PRICE_USD
STORAGE_COST_PER_TB_USD = config.STORAGE_COST_PER_TB_USD

# Canonical NULL-safe SQL fragments (ACCOUNT_USAGE column names).
SQL_TOTAL_CREDITS = "SUM(COALESCE(credits_used, 0))"
SQL_COMPUTE_CREDITS = "SUM(COALESCE(credits_used_compute, credits_used, 0))"
SQL_CLOUD_SERVICES_CREDITS = "SUM(COALESCE(credits_used_cloud_services, 0))"


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def warehouse_cost_usd(credits: object, rate_usd: float | None = None) -> float:
    """Dollarize compute credits. Rounds to cents."""
    rate = CREDIT_PRICE_USD if rate_usd is None else _num(rate_usd)
    return round(_num(credits) * rate, 2)


def cortex_ai_cost_usd(ai_credits: object, rate_usd: float | None = None) -> float:
    """Dollarize Cortex AI credits. Rounds to cents."""
    rate = AI_CREDIT_PRICE_USD if rate_usd is None else _num(rate_usd)
    return round(_num(ai_credits) * rate, 2)


def storage_cost_usd(terabytes: object, rate_usd: float | None = None) -> float:
    """Dollarize storage (TB-months). Rounds to cents."""
    rate = STORAGE_COST_PER_TB_USD if rate_usd is None else _num(rate_usd)
    return round(_num(terabytes) * rate, 2)


def allocate_credits(warehouse_hour_credits: object, group_elapsed_ms: object, total_elapsed_ms: object) -> float:
    """Allocate warehouse-hour credits to a group by elapsed-time share.

    Allocation, not exact billing. Returns 0.0 when the warehouse-hour had no
    measured elapsed time, and clamps the share to [0, 1] so a malformed total
    can never over-allocate.
    """
    total = _num(total_elapsed_ms)
    if total <= 0:
        return 0.0
    share = max(0.0, min(1.0, _num(group_elapsed_ms) / total))
    return round(_num(warehouse_hour_credits) * share, 6)


def cost_sql(credits_expr: str, rate_usd: float | None = None, alias: str = "COST_USD") -> str:
    """Wrap a credits SQL expression as a dollarized, rounded column."""
    rate = CREDIT_PRICE_USD if rate_usd is None else _num(rate_usd)
    return f"ROUND(({credits_expr}) * {rate}, 2) AS {alias}"


def fmt_usd(value: object) -> str:
    """Human dollar string."""
    v = _num(value)
    if abs(v) >= 1000:
        return f"${v:,.0f}"
    return f"${v:,.2f}"


def fmt_credits(value: object) -> str:
    v = _num(value)
    if v < 1:
        return f"{v:.3f}"
    if v < 100:
        return f"{v:.2f}"
    return f"{v:,.0f}"
