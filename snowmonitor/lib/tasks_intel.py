"""Task intelligence SQL — SLA/freshness, health, failures, duration, cost.

Company-scoped by database_name (tasks carry a database). Each builder fails
gracefully (the page shows an info/warn message, not a crash) if a column/view
differs in the account.
"""

from __future__ import annotations

import config
from . import company

AU = "SNOWFLAKE.ACCOUNT_USAGE"
_RATE = config.CREDIT_PRICE_USD


def _win(days: int) -> str:
    n = max(1, min(int(days or config.DEFAULT_LOOKBACK_DAYS), config.MAX_LOOKBACK_DAYS))
    return f"DATEADD('day', -{n}, CURRENT_TIMESTAMP())"


def task_sla_sql(days: int, company_name: str, top: int = 200) -> str:
    """Per task: last success, expected cadence (median scheduled gap), minutes since.

    The page classifies On-time / Late / Stale from these via tasks_logic.sla_status.
    """
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    win = _win(days)
    return f"""
    WITH runs AS (
        SELECT name, database_name, scheduled_time, completed_time, state,
               LAG(scheduled_time) OVER (PARTITION BY name ORDER BY scheduled_time) AS prev_sched
        FROM {AU}.TASK_HISTORY
        WHERE scheduled_time >= {win} AND database_name IS NOT NULL {scope}
    ),
    gaps AS (
        SELECT name, MEDIAN(DATEDIFF('second', prev_sched, scheduled_time)) / 60.0 AS expected_interval_min
        FROM runs WHERE prev_sched IS NOT NULL GROUP BY name
    ),
    last_run AS (
        SELECT name, ANY_VALUE(database_name) AS database_name,
               MAX(IFF(state = 'SUCCEEDED', completed_time, NULL)) AS last_success,
               MAX_BY(state, scheduled_time) AS last_state
        FROM runs GROUP BY name
    )
    SELECT l.name AS TASK, l.database_name AS DATABASE, l.last_state AS LAST_STATE,
           l.last_success AS LAST_SUCCESS,
           ROUND(COALESCE(g.expected_interval_min, 0), 1) AS EXPECTED_INTERVAL_MIN,
           ROUND(DATEDIFF('second', l.last_success, CURRENT_TIMESTAMP()) / 60.0, 1) AS MINUTES_SINCE_LAST
    FROM last_run l LEFT JOIN gaps g ON l.name = g.name
    ORDER BY MINUTES_SINCE_LAST DESC NULLS FIRST
    LIMIT {int(top)}
    """


def task_health_sql(days: int, company_name: str, top: int = 200) -> str:
    """Per task: runs, success %, failed/skipped, avg + p95 duration, last run."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    dur = "DATEDIFF('second', query_start_time, completed_time)"
    return f"""
    SELECT name AS TASK, ANY_VALUE(database_name) AS DATABASE,
           COUNT(*) AS RUNS,
           SUM(IFF(state = 'SUCCEEDED', 1, 0)) AS SUCCEEDED,
           SUM(IFF(state = 'FAILED', 1, 0)) AS FAILED,
           SUM(IFF(state = 'SKIPPED', 1, 0)) AS SKIPPED,
           ROUND(SUM(IFF(state = 'SUCCEEDED', 1, 0)) * 100.0 / NULLIF(COUNT(*), 0), 1) AS SUCCESS_PCT,
           ROUND(AVG(IFF(state = 'SUCCEEDED', {dur}, NULL)), 1) AS AVG_DURATION_SEC,
           ROUND(APPROX_PERCENTILE(IFF(state = 'SUCCEEDED', {dur}, NULL), 0.95), 1) AS P95_DURATION_SEC,
           MAX(scheduled_time) AS LAST_RUN
    FROM {AU}.TASK_HISTORY
    WHERE scheduled_time >= {_win(days)} AND database_name IS NOT NULL {scope}
    GROUP BY name
    ORDER BY FAILED DESC, RUNS DESC
    LIMIT {int(top)}
    """


def recent_task_states_sql(days: int, company_name: str, per_task: int = 20) -> str:
    """Recent run states per task (most-recent first) for consecutive-failure counting."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    return f"""
    SELECT name AS TASK, scheduled_time AS SCHEDULED_TIME, state AS STATE
    FROM {AU}.TASK_HISTORY
    WHERE scheduled_time >= {_win(days)} AND database_name IS NOT NULL
      AND state IN ('SUCCEEDED', 'FAILED', 'SKIPPED', 'CANCELLED') {scope}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY name ORDER BY scheduled_time DESC) <= {int(per_task)}
    ORDER BY name, scheduled_time DESC
    """


def error_clusters_sql(days: int, company_name: str, top: int = 50) -> str:
    """Group task failures by error message to find systemic causes."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    return f"""
    SELECT COALESCE(error_message, '(no message)') AS ERROR, error_code AS CODE,
           COUNT(*) AS OCCURRENCES, COUNT(DISTINCT name) AS TASKS_AFFECTED, MAX(scheduled_time) AS LAST_SEEN
    FROM {AU}.TASK_HISTORY
    WHERE scheduled_time >= {_win(days)} AND state = 'FAILED' AND database_name IS NOT NULL {scope}
    GROUP BY error_message, error_code
    ORDER BY OCCURRENCES DESC
    LIMIT {int(top)}
    """


def serverless_task_cost_sql(days: int, company_name: str, top: int = 50) -> str:
    """Serverless task credits + $ by task (warehouse-run tasks bill via metering)."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    return f"""
    SELECT task_name AS TASK, database_name AS DATABASE,
           ROUND(SUM(COALESCE(credits_used, 0)), 3) AS CREDITS,
           ROUND(SUM(COALESCE(credits_used, 0)) * {_RATE}, 2) AS COST_USD,
           COUNT(*) AS RUNS
    FROM {AU}.SERVERLESS_TASK_HISTORY
    WHERE start_time >= {_win(days)} AND database_name IS NOT NULL {scope}
    GROUP BY task_name, database_name
    ORDER BY COST_USD DESC
    LIMIT {int(top)}
    """


def task_duration_daily_sql(days: int, company_name: str) -> str:
    """Per-task per-day avg duration (seconds) for duration anomaly detection."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    return f"""
    SELECT TO_DATE(scheduled_time) AS USAGE_DATE, name AS TASK,
           AVG(DATEDIFF('second', query_start_time, completed_time)) AS AVG_DURATION_SEC
    FROM {AU}.TASK_HISTORY
    WHERE scheduled_time >= {_win(max(7, days))} AND state = 'SUCCEEDED' AND database_name IS NOT NULL {scope}
    GROUP BY USAGE_DATE, name
    """
