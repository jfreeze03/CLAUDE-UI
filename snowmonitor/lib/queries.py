"""ACCOUNT_USAGE SQL builders, company-scoped.

Every builder returns a SQL string scoped to the selected company (ALFA default)
and environment, with NULL-safe credit math and dollarization sourced from
formulas.py. Warehouse cost is exact (warehouse-hour metering); user/role/db/
application cost is *allocated* by query elapsed-time share and labeled as such.
"""

from __future__ import annotations

import config
from . import company, formulas

AU = "SNOWFLAKE.ACCOUNT_USAGE"

# Dimensions available for cost attribution and their QUERY_HISTORY column.
COST_DIMENSIONS = {
    "Warehouse": "warehouse_name",
    "Database": "database_name",
    "Schema": "schema_name",
    "User": "user_name",
    "Role": "role_name",
    "Query Type": "query_type",
}


def _window(days: int) -> str:
    days = max(1, min(int(days or config.DEFAULT_LOOKBACK_DAYS), config.MAX_LOOKBACK_DAYS))
    return f"DATEADD('day', -{days}, CURRENT_TIMESTAMP())"


# --------------------------------------------------------------------------
# COST
# --------------------------------------------------------------------------

def warehouse_cost_sql(days: int, company_name: str, top: int = 50) -> str:
    """Exact per-warehouse credits + dollars from WAREHOUSE_METERING_HISTORY."""
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT
        warehouse_name AS WAREHOUSE,
        {formulas.SQL_COMPUTE_CREDITS} AS COMPUTE_CREDITS,
        {formulas.SQL_CLOUD_SERVICES_CREDITS} AS CLOUD_SERVICES_CREDITS,
        {formulas.SQL_TOTAL_CREDITS} AS TOTAL_CREDITS,
        {formulas.cost_sql(formulas.SQL_TOTAL_CREDITS, alias='COST_USD')}
    FROM {AU}.WAREHOUSE_METERING_HISTORY
    WHERE start_time >= {_window(days)}
      AND warehouse_name IS NOT NULL
      {scope}
    GROUP BY warehouse_name
    ORDER BY TOTAL_CREDITS DESC
    LIMIT {int(top)}
    """


def daily_spend_sql(days: int, company_name: str) -> str:
    """Daily total credits + dollars trend (warehouse metering)."""
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT
        TO_DATE(start_time) AS USAGE_DATE,
        {formulas.SQL_TOTAL_CREDITS} AS TOTAL_CREDITS,
        {formulas.cost_sql(formulas.SQL_TOTAL_CREDITS, alias='COST_USD')}
    FROM {AU}.WAREHOUSE_METERING_HISTORY
    WHERE start_time >= {_window(days)}
      AND warehouse_name IS NOT NULL
      {scope}
    GROUP BY USAGE_DATE
    ORDER BY USAGE_DATE
    """


def cost_by_dimension_sql(dimension: str, days: int, company_name: str, top: int = 50) -> str:
    """Allocated cost by a chosen dimension (user/role/db/schema/query_type).

    Allocation = warehouse-hour compute credits * query elapsed share. Warehouse
    metering is exact; this split is an allocation estimate.
    """
    dim_col = COST_DIMENSIONS.get(dimension, "user_name")
    win = _window(days)
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    return f"""
    WITH metered AS (
        SELECT warehouse_name,
               DATE_TRUNC('hour', start_time) AS hr,
               {formulas.SQL_COMPUTE_CREDITS} AS hourly_credits
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= {win}
        GROUP BY warehouse_name, hr
    ),
    q AS (
        SELECT
            COALESCE(TO_VARCHAR({dim_col}), '(none)') AS dim,
            warehouse_name,
            DATE_TRUNC('hour', start_time) AS hr,
            execution_time AS exec_ms,
            SUM(execution_time) OVER (
                PARTITION BY warehouse_name, DATE_TRUNC('hour', start_time)
            ) AS hour_total_ms
        FROM {AU}.QUERY_HISTORY
        WHERE start_time >= {win}
          AND warehouse_name IS NOT NULL
          AND execution_time > 0
          {scope}
    ),
    alloc AS (
        SELECT q.dim,
               SUM(m.hourly_credits * q.exec_ms / NULLIF(q.hour_total_ms, 0)) AS credits
        FROM q
        JOIN metered m ON q.warehouse_name = m.warehouse_name AND q.hr = m.hr
        GROUP BY q.dim
    )
    SELECT
        dim AS {dimension.upper().replace(' ', '_')},
        ROUND(COALESCE(credits, 0), 2) AS ALLOCATED_CREDITS,
        {formulas.cost_sql('COALESCE(credits, 0)', alias='ALLOCATED_COST_USD')}
    FROM alloc
    ORDER BY ALLOCATED_CREDITS DESC
    LIMIT {int(top)}
    """


def application_cost_sql(days: int, company_name: str, top: int = 50) -> str:
    """Allocated cost by client application (QUERY_HISTORY joined to SESSIONS)."""
    win = _window(days)
    scope = company.company_scope_sql(company_name, wh_col="q.warehouse_name", db_col="q.database_name", user_col="q.user_name")
    return f"""
    WITH metered AS (
        SELECT warehouse_name,
               DATE_TRUNC('hour', start_time) AS hr,
               {formulas.SQL_COMPUTE_CREDITS} AS hourly_credits
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= {win}
        GROUP BY warehouse_name, hr
    ),
    q AS (
        SELECT
            COALESCE(s.client_application_name, 'Unknown') AS app,
            q.warehouse_name,
            DATE_TRUNC('hour', q.start_time) AS hr,
            q.execution_time AS exec_ms,
            SUM(q.execution_time) OVER (
                PARTITION BY q.warehouse_name, DATE_TRUNC('hour', q.start_time)
            ) AS hour_total_ms
        FROM {AU}.QUERY_HISTORY q
        LEFT JOIN {AU}.SESSIONS s ON q.session_id = s.session_id
        WHERE q.start_time >= {win}
          AND q.warehouse_name IS NOT NULL
          AND q.execution_time > 0
          {scope}
    ),
    alloc AS (
        SELECT q.app,
               COUNT(*) AS query_count,
               SUM(m.hourly_credits * q.exec_ms / NULLIF(q.hour_total_ms, 0)) AS credits
        FROM q
        JOIN metered m ON q.warehouse_name = m.warehouse_name AND q.hr = m.hr
        GROUP BY q.app
    )
    SELECT
        app AS APPLICATION,
        query_count AS QUERY_COUNT,
        ROUND(COALESCE(credits, 0), 2) AS ALLOCATED_CREDITS,
        {formulas.cost_sql('COALESCE(credits, 0)', alias='ALLOCATED_COST_USD')}
    FROM alloc
    ORDER BY ALLOCATED_CREDITS DESC
    LIMIT {int(top)}
    """


def storage_by_database_sql(company_name: str, top: int = 50) -> str:
    """Latest-day storage TB + monthly $ by database."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    return f"""
    WITH latest AS (
        SELECT database_name,
               average_database_bytes AS db_bytes,
               average_failsafe_bytes AS fs_bytes,
               ROW_NUMBER() OVER (PARTITION BY database_name ORDER BY usage_date DESC) AS rn
        FROM {AU}.DATABASE_STORAGE_USAGE_HISTORY
        WHERE usage_date >= DATEADD('day', -2, CURRENT_DATE())
    )
    SELECT
        database_name AS DATABASE,
        ROUND((db_bytes) / POWER(1024, 4), 3) AS STORAGE_TB,
        ROUND((fs_bytes) / POWER(1024, 4), 3) AS FAILSAFE_TB,
        ROUND(((db_bytes) / POWER(1024, 4)) * {formulas.STORAGE_COST_PER_TB_USD}, 2) AS MONTHLY_COST_USD
    FROM latest
    WHERE rn = 1 AND database_name IS NOT NULL
      {scope}
    ORDER BY STORAGE_TB DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# TASKS / TASK GRAPHS
# --------------------------------------------------------------------------

def task_runs_sql(days: int, company_name: str, top: int = 200) -> str:
    """Recent task runs with state, duration, and error."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    return f"""
    SELECT
        name AS TASK,
        database_name AS DATABASE,
        schema_name AS SCHEMA,
        state AS STATE,
        scheduled_time AS SCHEDULED_TIME,
        completed_time AS COMPLETED_TIME,
        DATEDIFF('second', query_start_time, completed_time) AS DURATION_SEC,
        error_code AS ERROR_CODE,
        error_message AS ERROR_MESSAGE,
        root_task_id AS ROOT_TASK_ID
    FROM {AU}.TASK_HISTORY
    WHERE scheduled_time >= {_window(days)}
      AND database_name IS NOT NULL
      {scope}
    ORDER BY scheduled_time DESC
    LIMIT {int(top)}
    """


def task_graph_sql(days: int, company_name: str, top: int = 100) -> str:
    """Per-root-task graph rollup: runs, failures, avg duration, last state."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    return f"""
    WITH runs AS (
        SELECT
            COALESCE(root_task_id, name) AS graph_key,
            name, database_name, schema_name, state,
            DATEDIFF('second', query_start_time, completed_time) AS dur_sec,
            scheduled_time
        FROM {AU}.TASK_HISTORY
        WHERE scheduled_time >= {_window(days)}
          AND database_name IS NOT NULL
          {scope}
    )
    SELECT
        graph_key AS GRAPH,
        ANY_VALUE(database_name) AS DATABASE,
        COUNT(DISTINCT name) AS TASKS_IN_GRAPH,
        COUNT(*) AS TOTAL_RUNS,
        SUM(IFF(state = 'FAILED', 1, 0)) AS FAILED_RUNS,
        SUM(IFF(state = 'SUCCEEDED', 1, 0)) AS SUCCEEDED_RUNS,
        ROUND(AVG(dur_sec), 1) AS AVG_DURATION_SEC,
        MAX(scheduled_time) AS LAST_RUN
    FROM runs
    GROUP BY graph_key
    ORDER BY FAILED_RUNS DESC, TOTAL_RUNS DESC
    LIMIT {int(top)}
    """


# --------------------------------------------------------------------------
# SECURITY
# --------------------------------------------------------------------------

def failed_logins_sql(days: int, company_name: str, top: int = 200) -> str:
    """Failed login events with user, reason, and source IP."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col=None, user_col="user_name")
    return f"""
    SELECT
        event_timestamp AS EVENT_TIME,
        user_name AS USER,
        reported_client_type AS CLIENT,
        error_message AS REASON,
        client_ip AS CLIENT_IP,
        first_authentication_factor AS FIRST_FACTOR,
        second_authentication_factor AS SECOND_FACTOR
    FROM {AU}.LOGIN_HISTORY
    WHERE event_timestamp >= {_window(days)}
      AND is_success = 'NO'
      {scope}
    ORDER BY event_timestamp DESC
    LIMIT {int(top)}
    """


def users_without_mfa_sql(company_name: str, days: int = 90) -> str:
    """Password users genuinely at MFA risk — cross-checked against LOGIN_HISTORY.

    The naive check (USERS.ext_authn_duo = false) over-flags: it catches users who
    actually authenticate via SSO/OAuth or key-pair and don't need a Duo enrollment.
    This version excludes those by their real recent auth behavior and prioritizes
    users with a *confirmed* recent password login that used no second factor.

    Note: LOGIN_HISTORY factor strings can vary by account; the SSO/key-pair set
    below covers the common values and is easy to extend.
    """
    scope = company.company_scope_sql(company_name, wh_col=None, db_col=None, user_col="u.name")
    win = _window(days)
    sso_or_keypair = "('SAML_2_0', 'OAUTH', 'OAUTH_ACCESS_TOKEN', 'KEY_PAIR', 'RSA_PUBLIC_KEY')"
    return f"""
    WITH pw_only AS (
        -- Confirmed: a recent SUCCESSFUL login via password with no second factor.
        SELECT DISTINCT UPPER(user_name) AS u
        FROM {AU}.LOGIN_HISTORY
        WHERE event_timestamp >= {win} AND is_success = 'YES'
          AND first_authentication_factor = 'PASSWORD'
          AND COALESCE(second_authentication_factor, '') = ''
    ),
    federated AS (
        -- Users who actually authenticate via SSO/OAuth/key-pair (not at password risk).
        SELECT DISTINCT UPPER(user_name) AS u
        FROM {AU}.LOGIN_HISTORY
        WHERE event_timestamp >= {win} AND is_success = 'YES'
          AND first_authentication_factor IN {sso_or_keypair}
    )
    SELECT
        u.name AS USER,
        u.email AS EMAIL,
        u.default_role AS DEFAULT_ROLE,
        u.last_success_login AS LAST_LOGIN,
        CASE
            WHEN pw.u IS NOT NULL THEN 'Confirmed: password login, no MFA'
            ELSE 'Potential: password enabled, no MFA enrolled'
        END AS RISK_BASIS
    FROM {AU}.USERS u
    LEFT JOIN pw_only pw ON pw.u = UPPER(u.name)
    LEFT JOIN federated f ON f.u = UPPER(u.name)
    WHERE u.deleted_on IS NULL
      AND u.disabled = FALSE
      AND u.has_password = TRUE
      AND COALESCE(u.ext_authn_duo, FALSE) = FALSE
      AND COALESCE(u.has_rsa_public_key, FALSE) = FALSE  -- exclude key-pair service accounts
      AND f.u IS NULL                                    -- exclude users who actually use SSO/key-pair
      {scope}
    ORDER BY (pw.u IS NOT NULL) DESC, u.last_success_login DESC NULLS LAST
    """


def recent_grants_sql(days: int, company_name: str, top: int = 200) -> str:
    """Recently granted privileges to roles (review trigger)."""
    return f"""
    SELECT
        created_on AS GRANTED_ON,
        privilege AS PRIVILEGE,
        granted_on AS OBJECT_TYPE,
        name AS OBJECT,
        granted_to AS GRANTED_TO,
        grantee_name AS GRANTEE,
        granted_by AS GRANTED_BY
    FROM {AU}.GRANTS_TO_ROLES
    WHERE created_on >= {_window(days)}
      AND deleted_on IS NULL
    ORDER BY created_on DESC
    LIMIT {int(top)}
    """
