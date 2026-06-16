"""Executive digest — paste-ready summary + scheduled server-side delivery.

`build_digest(...)` is pure (tested): it composes a markdown summary from already-
computed numbers (spend, forecast, alerts, savings, top driver). The Digest page
renders it for copy/paste; `digest_task_sql(...)` generates a Snowflake TASK that
emails a server-side summary on a schedule so leadership gets it without the app.
"""

from __future__ import annotations

import config
from . import formulas


def build_digest(
    company: str,
    days: int,
    metrics: dict,
    alert_summary: dict,
    savings_total: float,
    projection: dict,
    budget_state: dict,
    top_driver: tuple[str, float] | None = None,
) -> str:
    """Return a paste-ready markdown digest. All inputs are pre-computed."""
    m = metrics or {}
    a = alert_summary or {}
    fmt = formulas.fmt_usd

    mtd = m.get("mtd_spend_usd", 0)
    proj = projection.get("projection", 0) if projection else 0
    low = projection.get("low", 0) if projection else 0
    high = projection.get("high", 0) if projection else 0
    pct = budget_state.get("pct_of_budget", 0) if budget_state else 0
    state = budget_state.get("state", "") if budget_state else ""

    lines = [
        f"# SnowMonitor digest — {company}",
        f"_Scope: last {days} days · auto-generated_",
        "",
        "## Spend",
        f"- **Month-to-date:** {fmt(mtd)}",
        f"- **Forecast month-end:** {fmt(proj)}  (range {fmt(low)}–{fmt(high)})"
        + (f"  ·  **{pct:.0f}% of budget — {state}**" if budget_state and budget_state.get("has_budget") else ""),
        f"- **Daily run-rate:** {fmt(projection.get('run_rate_daily', 0))}/day" if projection else "",
    ]
    if top_driver:
        lines.append(f"- **Top cost driver:** {top_driver[0]} ({fmt(top_driver[1])})")

    lines += [
        "",
        "## Reliability & security",
        f"- **Open alerts:** {a.get('total', 0)} "
        f"({a.get('Critical', 0)} critical, {a.get('High', 0)} high)",
        f"- **Failed tasks (window):** {int(metrics.get('failed_task_runs', 0))}",
        f"- **Failed logins (window):** {int(metrics.get('failed_logins', 0))}",
        f"- **Users without MFA:** {int(metrics.get('users_without_mfa', 0))}",
        "",
        "## Savings opportunity",
        f"- **Est. recoverable:** {fmt(savings_total)}/mo  (~{fmt(_num(savings_total) * 12)}/yr) — see Recommendations.",
        "",
        f"_Source: SNOWFLAKE.ACCOUNT_USAGE (latency up to ~3h). Allocated/forecast figures are estimates._",
    ]
    return "\n".join(ln for ln in lines if ln is not None)


def _num(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def digest_task_sql(
    name: str,
    warehouse: str,
    schedule: str,
    recipients: str | None = None,
    integration: str | None = None,
) -> str:
    """Generate a Snowflake TASK that emails a server-side spend summary on a schedule.

    Queries the cost mart (deploy setup/setup.sql first) so it runs without the app.
    `schedule` is a Snowflake task schedule string, e.g. 'USING CRON 0 13 * * MON America/New_York'
    or '1440 MINUTE'.
    """
    integration = integration or config.NOTIFICATION_INTEGRATION
    recipients = recipients or config.DEFAULT_ALERT_RECIPIENTS
    mart = config.monitoring_fqn(config.MART_WAREHOUSE_DAILY)
    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in name.upper()).strip("_") or "SNOWMONITOR_DIGEST"
    rate = config.CREDIT_PRICE_USD
    return f"""-- Scheduled executive digest (server-side email). Requires the cost mart
-- (setup/setup.sql) and an email notification integration.
CREATE OR REPLACE TASK {safe}
  WAREHOUSE = {warehouse}
  SCHEDULE = '{schedule}'
AS
  CALL SYSTEM$SEND_EMAIL(
    '{integration}',
    '{recipients}',
    'SnowMonitor weekly digest',
    (
      SELECT 'Last 7 days spend: $' || TO_VARCHAR(ROUND(SUM(total_credits) * {rate}, 0))
          || '  |  Top warehouse: ' || COALESCE(MAX_BY(warehouse_name, total_credits), 'n/a')
          || '  |  Active warehouses: ' || TO_VARCHAR(COUNT(DISTINCT warehouse_name))
      FROM {mart}
      WHERE usage_date >= DATEADD('day', -7, CURRENT_DATE())
    )
  );

ALTER TASK {safe} RESUME;
"""
