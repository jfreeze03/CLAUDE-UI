"""Stored-procedure performance intelligence.

Stored procedures show up in QUERY_HISTORY as QUERY_TYPE='CALL'; the CALL row's
TOTAL_ELAPSED_TIME is the full procedure runtime (it blocks until the proc finishes),
so per-SP runtime, p95, and degradation come straight from those rows. The heavy-query
triage targets the individual statements (incl. those inside procs) where the actual
spill / scan / poor-pruning lives — that's what the optimization engine acts on.

Company-scoped by warehouse/db/user. Graceful on column differences.
"""

from __future__ import annotations

import re

import config
from . import company

AU = "SNOWFLAKE.ACCOUNT_USAGE"
_RATE = config.CREDIT_PRICE_USD

# SQL-side proc-name extraction (case-insensitive, capture the qualified name after CALL).
_PROC_RE_SQL = r"CALL\\s+([A-Za-z_][\\w$.]*)"
_PROC_RE_PY = re.compile(r"\bCALL\s+([A-Za-z_][\w$.]*)", re.IGNORECASE)


def parse_proc_name(query_text: str) -> str | None:
    """Python parity for the SQL proc-name extraction (used in tests)."""
    m = _PROC_RE_PY.search(str(query_text or ""))
    return m.group(1).upper() if m else None


def _win(days: int) -> str:
    n = max(1, min(int(days or config.DEFAULT_LOOKBACK_DAYS), config.MAX_LOOKBACK_DAYS))
    return f"DATEADD('day', -{n}, CURRENT_TIMESTAMP())"


def sp_performance_sql(days: int, company_name: str, top: int = 100) -> str:
    """Per stored proc: calls, avg/p95/max seconds, total minutes (SLA impact), cost."""
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    return f"""
    SELECT
        UPPER(REGEXP_SUBSTR(query_text, '{_PROC_RE_SQL}', 1, 1, 'ie', 1)) AS PROC,
        COUNT(*) AS CALLS,
        ROUND(AVG(total_elapsed_time) / 1000.0, 1) AS AVG_SEC,
        ROUND(APPROX_PERCENTILE(total_elapsed_time, 0.95) / 1000.0, 1) AS P95_SEC,
        ROUND(MAX(total_elapsed_time) / 1000.0, 1) AS MAX_SEC,
        ROUND(SUM(total_elapsed_time) / 1000.0 / 60.0, 1) AS TOTAL_MINUTES,
        SUM(IFF(execution_status = 'FAIL', 1, 0)) AS FAILED_CALLS,
        MAX(start_time) AS LAST_RUN
    FROM {AU}.QUERY_HISTORY
    WHERE start_time >= {_win(days)} AND query_type = 'CALL' AND warehouse_name IS NOT NULL {scope}
    GROUP BY PROC
    HAVING PROC IS NOT NULL
    ORDER BY TOTAL_MINUTES DESC
    LIMIT {int(top)}
    """


def sp_degradation_sql(days: int, company_name: str, top: int = 100) -> str:
    """Per proc: current-window avg/p95 seconds vs the prior equal window (regression)."""
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    n = max(1, int(days))
    proc = f"UPPER(REGEXP_SUBSTR(query_text, '{_PROC_RE_SQL}', 1, 1, 'ie', 1))"
    base = f"FROM {AU}.QUERY_HISTORY WHERE query_type = 'CALL' AND warehouse_name IS NOT NULL {scope}"
    return f"""
    WITH cur AS (
        SELECT {proc} AS proc, AVG(total_elapsed_time)/1000.0 AS avg_sec,
               APPROX_PERCENTILE(total_elapsed_time, 0.95)/1000.0 AS p95_sec, COUNT(*) AS calls
        {base} AND start_time >= DATEADD('day', -{n}, CURRENT_TIMESTAMP())
        GROUP BY proc
    ),
    prior AS (
        SELECT {proc} AS proc, AVG(total_elapsed_time)/1000.0 AS avg_sec
        {base} AND start_time >= DATEADD('day', -{n*2}, CURRENT_TIMESTAMP())
              AND start_time < DATEADD('day', -{n}, CURRENT_TIMESTAMP())
        GROUP BY proc
    )
    SELECT cur.proc AS PROC, cur.calls AS CALLS,
           ROUND(cur.avg_sec, 1) AS CURRENT_AVG_SEC, ROUND(prior.avg_sec, 1) AS PRIOR_AVG_SEC,
           ROUND(cur.p95_sec, 1) AS CURRENT_P95_SEC,
           ROUND((cur.avg_sec - prior.avg_sec) / NULLIF(prior.avg_sec, 0) * 100, 1) AS PCT_CHANGE
    FROM cur LEFT JOIN prior ON cur.proc = prior.proc
    WHERE cur.proc IS NOT NULL
    ORDER BY PCT_CHANGE DESC NULLS LAST
    LIMIT {int(top)}
    """


def sp_duration_daily_sql(days: int, company_name: str) -> str:
    """Per-proc per-day avg seconds for trend/anomaly."""
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    proc = f"UPPER(REGEXP_SUBSTR(query_text, '{_PROC_RE_SQL}', 1, 1, 'ie', 1))"
    return f"""
    SELECT TO_DATE(start_time) AS USAGE_DATE, {proc} AS TASK,
           AVG(total_elapsed_time)/1000.0 AS AVG_DURATION_SEC
    FROM {AU}.QUERY_HISTORY
    WHERE start_time >= {_win(max(7, days))} AND query_type = 'CALL' AND warehouse_name IS NOT NULL {scope}
    GROUP BY USAGE_DATE, TASK
    HAVING TASK IS NOT NULL
    """


def heavy_query_sql(days: int, company_name: str, top: int = 100) -> str:
    """Optimization candidates: statements with spill / big scans / poor pruning.

    These are the queries (often inside procs) where the inefficiency actually is.
    Surfaces the diagnostic signals the optimization engine reasons over.
    """
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    return f"""
    SELECT
        query_id AS QUERY_ID,
        user_name AS USER, warehouse_name AS WAREHOUSE, query_type AS TYPE,
        ROUND(total_elapsed_time / 1000.0, 1) AS DURATION_SEC,
        ROUND(COALESCE(bytes_scanned, 0) / POWER(1024, 3), 2) AS GB_SCANNED,
        ROUND(COALESCE(bytes_spilled_to_remote_storage, 0) / POWER(1024, 3), 2) AS REMOTE_SPILL_GB,
        ROUND(COALESCE(bytes_spilled_to_local_storage, 0) / POWER(1024, 3), 2) AS LOCAL_SPILL_GB,
        ROUND(COALESCE(percentage_scanned_from_cache, 0) * 100, 1) AS CACHE_PCT,
        partitions_scanned AS PARTITIONS_SCANNED, partitions_total AS PARTITIONS_TOTAL,
        ROUND(partitions_scanned / NULLIF(partitions_total, 0) * 100, 1) AS PRUNING_PCT,
        LEFT(query_text, 250) AS QUERY
    FROM {AU}.QUERY_HISTORY
    WHERE start_time >= {_win(days)} AND warehouse_name IS NOT NULL AND execution_time > 0 {scope}
      AND (COALESCE(bytes_spilled_to_remote_storage, 0) > 0
           OR COALESCE(bytes_scanned, 0) > POWER(1024, 3) * 50
           OR total_elapsed_time > 60000)
    ORDER BY REMOTE_SPILL_GB DESC, DURATION_SEC DESC
    LIMIT {int(top)}
    """
