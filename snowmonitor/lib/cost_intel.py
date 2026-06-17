"""Cost intelligence — the full spend picture beyond warehouses.

Covers ALL Snowflake credit consumption (not just warehouses): serverless services,
Cortex AI + Cortex Code, chargeback by company, and detailed storage. Where a source
carries warehouse/db/user, queries are company-scoped; account-level sources
(METERING_DAILY_HISTORY, Cortex usage views) are account-total and labeled as such.

Schema note: the Cortex usage views vary by account/edition and are the most likely
to need column adjustment (see VALIDATION.md). Each query fails gracefully (the app
shows a warning, not a crash) so an unavailable view never breaks the page.
"""

from __future__ import annotations

import config
from . import company, formulas

AU = "SNOWFLAKE.ACCOUNT_USAGE"


def _window(days: int) -> str:
    days = max(1, min(int(days or config.DEFAULT_LOOKBACK_DAYS), config.MAX_LOOKBACK_DAYS))
    return f"DATEADD('day', -{days}, CURRENT_DATE())"


# --------------------------------------------------------------------------
# All-service spend (the "others": pipes, clustering, MVs, search opt, replication,
# serverless tasks, AI services, etc.) — account-level from METERING_DAILY_HISTORY.
# --------------------------------------------------------------------------

def service_cost_sql(days: int) -> str:
    """Credits + $ by SERVICE_TYPE — the complete account spend breakdown."""
    return f"""
    SELECT
        service_type AS SERVICE,
        {formulas.SQL_TOTAL_CREDITS} AS TOTAL_CREDITS,
        {formulas.cost_sql(formulas.SQL_TOTAL_CREDITS, alias='COST_USD')}
    FROM {AU}.METERING_DAILY_HISTORY
    WHERE usage_date >= {_window(days)}
    GROUP BY service_type
    ORDER BY TOTAL_CREDITS DESC
    """


def service_daily_sql(days: int) -> str:
    """Daily credits by service for a stacked trend."""
    return f"""
    SELECT usage_date AS USAGE_DATE, service_type AS SERVICE,
           {formulas.SQL_TOTAL_CREDITS} AS TOTAL_CREDITS
    FROM {AU}.METERING_DAILY_HISTORY
    WHERE usage_date >= {_window(days)}
    GROUP BY usage_date, service_type
    ORDER BY usage_date
    """


# --------------------------------------------------------------------------
# Cortex AI + Cortex Code (the user's "cortex code charges")
# --------------------------------------------------------------------------

def cortex_functions_cost_sql(days: int, top: int = 50) -> str:
    """Cortex function/model token credits + $ (account-level)."""
    return f"""
    SELECT
        COALESCE(model_name, function_name) AS MODEL_OR_FUNCTION,
        SUM(COALESCE(tokens, 0)) AS TOKENS,
        SUM(COALESCE(token_credits, 0)) AS AI_CREDITS,
        ROUND(SUM(COALESCE(token_credits, 0)) * {config.AI_CREDIT_PRICE_USD}, 2) AS COST_USD
    FROM {AU}.CORTEX_FUNCTIONS_USAGE_HISTORY
    WHERE start_time >= DATEADD('day', -{max(1, int(days))}, CURRENT_TIMESTAMP())
    GROUP BY 1
    ORDER BY AI_CREDITS DESC
    LIMIT {int(top)}
    """


def cortex_code_cost_sql(days: int, top: int = 50) -> str:
    """Cortex Code (CLI) token credits + $ by user.

    NOTE: CORTEX_CODE_CLI_USAGE_HISTORY is a newer ACCOUNT_USAGE view whose exact
    column names (time column, token columns) vary by account/region and are NOT
    verified here. Callers run this with quiet=True so a wrong column / missing
    view degrades silently to "no usage" instead of an error banner. To enable it,
    confirm the real columns in your account (DESC VIEW ...) and edit below.
    See VALIDATION §3/§8.
    """
    return f"""
    SELECT
        user_name AS USER,
        SUM(COALESCE(tokens, 0)) AS TOKENS,
        SUM(COALESCE(token_credits, 0)) AS AI_CREDITS,
        ROUND(SUM(COALESCE(token_credits, 0)) * {config.AI_CREDIT_PRICE_USD}, 2) AS COST_USD,
        COUNT(*) AS REQUESTS
    FROM {AU}.CORTEX_CODE_CLI_USAGE_HISTORY
    WHERE start_time >= DATEADD('day', -{max(1, int(days))}, CURRENT_TIMESTAMP())
    GROUP BY user_name
    ORDER BY AI_CREDITS DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# Chargeback — allocated cost by company x dimension (with $)
# --------------------------------------------------------------------------

def chargeback_sql(days: int, dimension: str = "database_name", top: int = 200) -> str:
    """Allocated cost grouped by COMPANY and a dimension, for chargeback reports."""
    dim = {"Database": "database_name", "User": "user_name", "Role": "role_name"}.get(dimension, dimension)
    win = f"DATEADD('day', -{max(1, int(days))}, CURRENT_TIMESTAMP())"
    company_case = company.company_case_sql(
        wh_col="q.warehouse_name", db_col="q.database_name", user_col="q.user_name", alias="COMPANY")
    return f"""
    WITH metered AS (
        SELECT warehouse_name, DATE_TRUNC('hour', start_time) AS hr,
               {formulas.SQL_COMPUTE_CREDITS} AS hourly_credits
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= {win}
        GROUP BY warehouse_name, hr
    ),
    q AS (
        SELECT
            {company_case},
            COALESCE(TO_VARCHAR(q.{dim}), '(none)') AS DIM,
            q.warehouse_name, DATE_TRUNC('hour', q.start_time) AS hr,
            q.execution_time AS exec_ms,
            SUM(q.execution_time) OVER (PARTITION BY q.warehouse_name, DATE_TRUNC('hour', q.start_time)) AS hour_total_ms
        FROM {AU}.QUERY_HISTORY q
        WHERE q.start_time >= {win} AND q.warehouse_name IS NOT NULL AND q.execution_time > 0
    )
    SELECT q.COMPANY, q.DIM,
           ROUND(SUM(m.hourly_credits * q.exec_ms / NULLIF(q.hour_total_ms, 0)), 2) AS ALLOCATED_CREDITS,
           {formulas.cost_sql('SUM(m.hourly_credits * q.exec_ms / NULLIF(q.hour_total_ms, 0))', alias='COST_USD')}
    FROM q JOIN metered m ON q.warehouse_name = m.warehouse_name AND q.hr = m.hr
    GROUP BY q.COMPANY, q.DIM
    ORDER BY ALLOCATED_CREDITS DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# Storage detail — active vs time-travel vs failsafe (from TABLE_STORAGE_METRICS)
# --------------------------------------------------------------------------

def storage_detail_sql(company_name: str, top: int = 50) -> str:
    """Per-database active / time-travel / failsafe storage TB + monthly $."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="table_catalog", user_col=None)
    rate = config.STORAGE_COST_PER_TB_USD
    return f"""
    SELECT
        table_catalog AS DATABASE,
        ROUND(SUM(active_bytes) / POWER(1024,4), 3) AS ACTIVE_TB,
        ROUND(SUM(time_travel_bytes) / POWER(1024,4), 3) AS TIME_TRAVEL_TB,
        ROUND(SUM(failsafe_bytes) / POWER(1024,4), 3) AS FAILSAFE_TB,
        ROUND((SUM(active_bytes + time_travel_bytes + failsafe_bytes) / POWER(1024,4)) * {rate}, 2) AS MONTHLY_COST_USD
    FROM {AU}.TABLE_STORAGE_METRICS
    WHERE deleted = FALSE AND table_catalog IS NOT NULL
      {scope}
    GROUP BY table_catalog
    ORDER BY MONTHLY_COST_USD DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# Cost RCA / variance — what drove the change (current window vs prior window)
# --------------------------------------------------------------------------

def cost_variance_sql(days: int, company_name: str, top: int = 50) -> str:
    """Per-warehouse spend change vs the prior equal-length window (cost RCA)."""
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    n = max(1, int(days))
    rate = config.CREDIT_PRICE_USD
    return f"""
    WITH cur AS (
        SELECT warehouse_name, {formulas.SQL_TOTAL_CREDITS} AS c
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= DATEADD('day', -{n}, CURRENT_TIMESTAMP())
          AND warehouse_name IS NOT NULL {scope}
        GROUP BY warehouse_name
    ),
    prior AS (
        SELECT warehouse_name, {formulas.SQL_TOTAL_CREDITS} AS c
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= DATEADD('day', -{n*2}, CURRENT_TIMESTAMP())
          AND start_time <  DATEADD('day', -{n}, CURRENT_TIMESTAMP())
          AND warehouse_name IS NOT NULL {scope}
        GROUP BY warehouse_name
    )
    SELECT COALESCE(cur.warehouse_name, prior.warehouse_name) AS WAREHOUSE,
           ROUND(COALESCE(cur.c, 0) * {rate}, 2) AS CURRENT_USD,
           ROUND(COALESCE(prior.c, 0) * {rate}, 2) AS PRIOR_USD,
           ROUND((COALESCE(cur.c, 0) - COALESCE(prior.c, 0)) * {rate}, 2) AS DELTA_USD,
           ROUND((COALESCE(cur.c, 0) - COALESCE(prior.c, 0)) / NULLIF(prior.c, 0) * 100, 1) AS PCT_CHANGE
    FROM cur FULL OUTER JOIN prior ON cur.warehouse_name = prior.warehouse_name
    ORDER BY DELTA_USD DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# Efficiency / unit economics — is the spend productive?
# --------------------------------------------------------------------------

def efficiency_summary_sql(days: int, company_name: str) -> str:
    """Single-row unit economics: cost/query, cost/TB, cache %, failed-query waste."""
    wh_scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    q_scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    n = max(1, int(days))
    rate = config.CREDIT_PRICE_USD
    win = f"DATEADD('day', -{n}, CURRENT_TIMESTAMP())"
    return f"""
    WITH wh AS (
        SELECT {formulas.SQL_TOTAL_CREDITS} AS credits
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= {win} AND warehouse_name IS NOT NULL {wh_scope}
    ),
    q AS (
        SELECT COUNT(*) AS queries,
               SUM(COALESCE(bytes_scanned, 0)) / POWER(1024, 4) AS tb_scanned,
               AVG(COALESCE(percentage_scanned_from_cache, 0)) * 100 AS cache_pct,
               SUM(IFF(execution_status = 'FAIL', 1, 0)) AS failed_queries,
               SUM(IFF(execution_status = 'FAIL', execution_time, 0)) / NULLIF(SUM(execution_time), 0) AS failed_time_share
        FROM {AU}.QUERY_HISTORY
        WHERE start_time >= {win} AND warehouse_name IS NOT NULL {q_scope}
    )
    SELECT
        ROUND(wh.credits * {rate}, 2) AS TOTAL_COST_USD,
        q.queries AS QUERIES,
        ROUND(wh.credits * {rate} / NULLIF(q.queries, 0), 4) AS COST_PER_QUERY_USD,
        ROUND(q.tb_scanned, 2) AS TB_SCANNED,
        ROUND(wh.credits * {rate} / NULLIF(q.tb_scanned, 0), 2) AS COST_PER_TB_USD,
        ROUND(q.cache_pct, 1) AS AVG_CACHE_PCT,
        q.failed_queries AS FAILED_QUERIES,
        ROUND(wh.credits * {rate} * COALESCE(q.failed_time_share, 0), 2) AS FAILED_QUERY_WASTE_USD
    FROM wh, q
    """


def warehouse_efficiency_sql(days: int, company_name: str, top: int = 50) -> str:
    """Per-warehouse efficiency: credits/active-hour, queue seconds, remote spill."""
    wh_scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    q_scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    n = max(1, int(days))
    rate = config.CREDIT_PRICE_USD
    win = f"DATEADD('day', -{n}, CURRENT_TIMESTAMP())"
    return f"""
    WITH m AS (
        SELECT warehouse_name, {formulas.SQL_TOTAL_CREDITS} AS credits
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= {win} AND warehouse_name IS NOT NULL {wh_scope}
        GROUP BY warehouse_name
    ),
    q AS (
        SELECT warehouse_name, COUNT(*) AS queries,
               SUM(execution_time) / 1000.0 / 3600.0 AS exec_hours,
               SUM(COALESCE(queued_overload_time, 0) + COALESCE(queued_provisioning_time, 0)) / 1000.0 AS queue_seconds,
               SUM(COALESCE(bytes_spilled_to_remote_storage, 0)) / POWER(1024, 3) AS remote_spill_gb
        FROM {AU}.QUERY_HISTORY
        WHERE start_time >= {win} AND warehouse_name IS NOT NULL {q_scope}
        GROUP BY warehouse_name
    )
    SELECT m.warehouse_name AS WAREHOUSE,
           ROUND(m.credits * {rate}, 2) AS COST_USD,
           COALESCE(q.queries, 0) AS QUERIES,
           ROUND(m.credits / NULLIF(q.exec_hours, 0), 2) AS CREDITS_PER_EXEC_HOUR,
           ROUND(COALESCE(q.queue_seconds, 0), 0) AS QUEUE_SECONDS,
           ROUND(COALESCE(q.remote_spill_gb, 0), 1) AS REMOTE_SPILL_GB
    FROM m LEFT JOIN q ON m.warehouse_name = q.warehouse_name
    ORDER BY COST_USD DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# Automatic clustering — cost + efficiency by table (often silent runaway)
# --------------------------------------------------------------------------

def clustering_cost_sql(days: int, company_name: str, top: int = 50) -> str:
    """Per-table automatic-clustering credits + $ and recluster churn.

    High cost with high TB reclustered = the cluster key churns a lot (frequent DML
    or a poor key) — a prime candidate to review or suspend.
    """
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    rate = config.CREDIT_PRICE_USD
    win = f"DATEADD('day', -{max(1, int(days))}, CURRENT_TIMESTAMP())"
    return f"""
    SELECT
        database_name || '.' || schema_name || '.' || table_name AS TABLE_NAME,
        ROUND(SUM(COALESCE(credits_used, 0)), 3) AS CLUSTERING_CREDITS,
        ROUND(SUM(COALESCE(credits_used, 0)) * {rate}, 2) AS CLUSTERING_COST_USD,
        ROUND(SUM(COALESCE(num_bytes_reclustered, 0)) / POWER(1024, 4), 3) AS TB_RECLUSTERED,
        SUM(COALESCE(num_rows_reclustered, 0)) AS ROWS_RECLUSTERED,
        ROUND(SUM(COALESCE(credits_used, 0)) * {rate}
              / NULLIF(SUM(COALESCE(num_bytes_reclustered, 0)) / POWER(1024, 4), 0), 2) AS COST_PER_TB_RECLUSTERED
    FROM {AU}.AUTOMATIC_CLUSTERING_HISTORY
    WHERE start_time >= {win} AND database_name IS NOT NULL
      {scope}
    GROUP BY database_name, schema_name, table_name
    ORDER BY CLUSTERING_COST_USD DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# Contract / capacity burn (ORGANIZATION_USAGE — requires ORGADMIN access)
# --------------------------------------------------------------------------

def capacity_summary_sql() -> str:
    """Latest remaining capacity balances + 30-day avg daily currency burn.

    Requires SNOWFLAKE.ORGANIZATION_USAGE access (ORGADMIN). Fails gracefully when
    unavailable — the UI shows a 'no org access' message rather than an error.
    """
    return f"""
    WITH bal AS (
        SELECT capacity_balance, free_usage_balance, rollover_balance,
               on_demand_consumption_balance, currency
        FROM SNOWFLAKE.ORGANIZATION_USAGE.REMAINING_BALANCE_DAILY
        QUALIFY ROW_NUMBER() OVER (ORDER BY date DESC) = 1
    ),
    burn AS (
        SELECT SUM(usage_in_currency) / 30.0 AS daily_burn, ANY_VALUE(currency) AS currency
        FROM SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY
        WHERE usage_date >= DATEADD('day', -30, CURRENT_DATE())
    )
    SELECT
        ROUND(COALESCE(bal.capacity_balance, 0), 2) AS CAPACITY_BALANCE,
        ROUND(COALESCE(bal.free_usage_balance, 0), 2) AS FREE_BALANCE,
        ROUND(COALESCE(bal.rollover_balance, 0), 2) AS ROLLOVER_BALANCE,
        ROUND(COALESCE(bal.on_demand_consumption_balance, 0), 2) AS ON_DEMAND_BALANCE,
        ROUND(COALESCE(burn.daily_burn, 0), 2) AS DAILY_BURN,
        COALESCE(bal.currency, burn.currency, 'USD') AS CURRENCY
    FROM bal, burn
    """
