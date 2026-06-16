-- ============================================================================
-- SnowMonitor server-side objects (run once).
-- Cost mart (so the app reads pre-aggregated data instead of scanning
-- ACCOUNT_USAGE on every page load), alert ledger, app error log, control-action
-- audit, and a task that refreshes the mart.
--
-- Run as a role that can CREATE in the monitoring database AND has access to
-- SNOWFLAKE.ACCOUNT_USAGE (IMPORTED PRIVILEGES on the SNOWFLAKE database).
-- Change SNOWMONITOR_DB / PUBLIC / MONITOR_WH below to match your account.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS SNOWMONITOR_DB;
CREATE SCHEMA   IF NOT EXISTS SNOWMONITOR_DB.PUBLIC;
USE SCHEMA SNOWMONITOR_DB.PUBLIC;

-- Marts -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS MART_WAREHOUSE_COST_DAILY (
    USAGE_DATE DATE, WAREHOUSE_NAME STRING, COMPUTE_CREDITS FLOAT,
    CLOUD_SERVICES_CREDITS FLOAT, TOTAL_CREDITS FLOAT,
    LOADED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
CREATE TABLE IF NOT EXISTS MART_QUERY_ATTR_DAILY (
    USAGE_DATE DATE, WAREHOUSE_NAME STRING, USER_NAME STRING, ROLE_NAME STRING,
    DATABASE_NAME STRING, ALLOCATED_CREDITS FLOAT, QUERY_COUNT NUMBER,
    LOADED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Alert ledger + app log + control-action audit -------------------------------
CREATE TABLE IF NOT EXISTS ALERT_LEDGER (
    ALERT_KEY STRING, SEVERITY STRING, KIND STRING, DOMAIN STRING, TITLE STRING,
    DETAIL STRING, COMPANY STRING, FIRST_SEEN TIMESTAMP_NTZ, LAST_SEEN TIMESTAMP_NTZ,
    RUN_COUNT NUMBER, STATUS STRING, ACK_BY STRING, ACK_AT TIMESTAMP_NTZ
);
CREATE TABLE IF NOT EXISTS APP_LOG (
    LOG_TIME TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(), SF_USER STRING, SF_ROLE STRING,
    PAGE STRING, EVENT_TYPE STRING, MESSAGE STRING
);
CREATE TABLE IF NOT EXISTS ACTION_AUDIT (
    ACTION_TIME TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    ACTOR STRING, TITLE STRING, SUMMARY STRING, SQL_TEXT STRING, ROLLBACK_SQL STRING
);

-- Mart refresh procedure (idempotent MERGE of the trailing window) -------------
CREATE OR REPLACE PROCEDURE SP_REFRESH_MART(DAYS_BACK NUMBER)
RETURNS STRING LANGUAGE SQL AS
$$
BEGIN
    MERGE INTO MART_WAREHOUSE_COST_DAILY t
    USING (
        SELECT TO_DATE(start_time) AS usage_date, warehouse_name,
               SUM(COALESCE(credits_used_compute, credits_used, 0)) AS compute_credits,
               SUM(COALESCE(credits_used_cloud_services, 0))        AS cloud_services_credits,
               SUM(COALESCE(credits_used, 0))                       AS total_credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= DATEADD('day', -:DAYS_BACK, CURRENT_TIMESTAMP()) AND warehouse_name IS NOT NULL
        GROUP BY 1, 2
    ) s ON t.usage_date = s.usage_date AND t.warehouse_name = s.warehouse_name
    WHEN MATCHED THEN UPDATE SET compute_credits = s.compute_credits,
        cloud_services_credits = s.cloud_services_credits, total_credits = s.total_credits,
        loaded_at = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        (usage_date, warehouse_name, compute_credits, cloud_services_credits, total_credits)
        VALUES (s.usage_date, s.warehouse_name, s.compute_credits, s.cloud_services_credits, s.total_credits);

    MERGE INTO MART_QUERY_ATTR_DAILY t
    USING (
        WITH metered AS (
            SELECT warehouse_name, DATE_TRUNC('hour', start_time) AS hr,
                   SUM(COALESCE(credits_used_compute, credits_used, 0)) AS hourly_credits
            FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
            WHERE start_time >= DATEADD('day', -:DAYS_BACK, CURRENT_TIMESTAMP())
            GROUP BY 1, 2
        ),
        q AS (
            SELECT TO_DATE(start_time) AS usage_date, warehouse_name,
                   COALESCE(user_name, '(none)') AS user_name,
                   COALESCE(role_name, '(none)') AS role_name,
                   COALESCE(database_name, '(none)') AS database_name,
                   DATE_TRUNC('hour', start_time) AS hr, execution_time AS exec_ms,
                   SUM(execution_time) OVER (PARTITION BY warehouse_name, DATE_TRUNC('hour', start_time)) AS hour_total_ms
            FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
            WHERE start_time >= DATEADD('day', -:DAYS_BACK, CURRENT_TIMESTAMP())
              AND warehouse_name IS NOT NULL AND execution_time > 0
        )
        SELECT q.usage_date, q.warehouse_name, q.user_name, q.role_name, q.database_name,
               SUM(m.hourly_credits * q.exec_ms / NULLIF(q.hour_total_ms, 0)) AS allocated_credits,
               COUNT(*) AS query_count
        FROM q JOIN metered m ON q.warehouse_name = m.warehouse_name AND q.hr = m.hr
        GROUP BY 1, 2, 3, 4, 5
    ) s
    ON  t.usage_date = s.usage_date AND t.warehouse_name = s.warehouse_name
    AND t.user_name = s.user_name AND t.role_name = s.role_name AND t.database_name = s.database_name
    WHEN MATCHED THEN UPDATE SET allocated_credits = s.allocated_credits,
        query_count = s.query_count, loaded_at = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        (usage_date, warehouse_name, user_name, role_name, database_name, allocated_credits, query_count)
        VALUES (s.usage_date, s.warehouse_name, s.user_name, s.role_name, s.database_name,
                s.allocated_credits, s.query_count);

    RETURN 'SnowMonitor mart refreshed';
END;
$$;

-- Backfill 90 days now (run off-hours), then refresh trailing 3 days hourly.
CALL SP_REFRESH_MART(90);

CREATE OR REPLACE TASK TASK_REFRESH_MART
    WAREHOUSE = MONITOR_WH
    SCHEDULE = '60 MINUTE'
AS
    CALL SP_REFRESH_MART(3);

ALTER TASK TASK_REFRESH_MART RESUME;
