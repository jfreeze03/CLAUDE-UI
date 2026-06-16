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

-- Per-warehouse-day efficiency facts (feeds Efficiency tab + idle/sizing recs fast).
CREATE TABLE IF NOT EXISTS MART_WAREHOUSE_EFFICIENCY_DAILY (
    USAGE_DATE DATE, WAREHOUSE_NAME STRING,
    CREDITS FLOAT, BILLED_HOURS NUMBER,
    QUERY_COUNT NUMBER, EXEC_MS FLOAT, QUEUE_MS FLOAT,
    BYTES_SCANNED FLOAT, CACHE_BYTES FLOAT, REMOTE_SPILL_BYTES FLOAT,
    FAILED_QUERIES NUMBER, FAILED_EXEC_MS FLOAT,
    LOADED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Per-query-pattern-day rollup (feeds repeated-query recommendations fast).
CREATE TABLE IF NOT EXISTS MART_QUERY_PATTERN_DAILY (
    USAGE_DATE DATE, QUERY_PARAMETERIZED_HASH STRING,
    RUNS NUMBER, EXEC_MS FLOAT, SAMPLE_QUERY STRING,
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

    -- Per-warehouse-day efficiency facts (metering + query history).
    MERGE INTO MART_WAREHOUSE_EFFICIENCY_DAILY t
    USING (
        WITH m AS (
            SELECT TO_DATE(start_time) AS d, warehouse_name,
                   SUM(COALESCE(credits_used, 0)) AS credits,
                   COUNT(DISTINCT DATE_TRUNC('hour', start_time)) AS billed_hours
            FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
            WHERE start_time >= DATEADD('day', -:DAYS_BACK, CURRENT_TIMESTAMP()) AND warehouse_name IS NOT NULL
            GROUP BY 1, 2
        ),
        q AS (
            SELECT TO_DATE(start_time) AS d, warehouse_name,
                   COUNT(*) AS query_count, SUM(execution_time) AS exec_ms,
                   SUM(COALESCE(queued_overload_time, 0) + COALESCE(queued_provisioning_time, 0)) AS queue_ms,
                   SUM(COALESCE(bytes_scanned, 0)) AS bytes_scanned,
                   SUM(COALESCE(bytes_scanned, 0) * COALESCE(percentage_scanned_from_cache, 0)) AS cache_bytes,
                   SUM(COALESCE(bytes_spilled_to_remote_storage, 0)) AS remote_spill_bytes,
                   SUM(IFF(execution_status = 'FAIL', 1, 0)) AS failed_queries,
                   SUM(IFF(execution_status = 'FAIL', execution_time, 0)) AS failed_exec_ms
            FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
            WHERE start_time >= DATEADD('day', -:DAYS_BACK, CURRENT_TIMESTAMP()) AND warehouse_name IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT COALESCE(m.d, q.d) AS usage_date, COALESCE(m.warehouse_name, q.warehouse_name) AS warehouse_name,
               COALESCE(m.credits, 0) AS credits, COALESCE(m.billed_hours, 0) AS billed_hours,
               COALESCE(q.query_count, 0) AS query_count, COALESCE(q.exec_ms, 0) AS exec_ms,
               COALESCE(q.queue_ms, 0) AS queue_ms, COALESCE(q.bytes_scanned, 0) AS bytes_scanned,
               COALESCE(q.cache_bytes, 0) AS cache_bytes, COALESCE(q.remote_spill_bytes, 0) AS remote_spill_bytes,
               COALESCE(q.failed_queries, 0) AS failed_queries, COALESCE(q.failed_exec_ms, 0) AS failed_exec_ms
        FROM m FULL OUTER JOIN q ON m.d = q.d AND m.warehouse_name = q.warehouse_name
    ) s
    ON t.usage_date = s.usage_date AND t.warehouse_name = s.warehouse_name
    WHEN MATCHED THEN UPDATE SET credits = s.credits, billed_hours = s.billed_hours, query_count = s.query_count,
        exec_ms = s.exec_ms, queue_ms = s.queue_ms, bytes_scanned = s.bytes_scanned, cache_bytes = s.cache_bytes,
        remote_spill_bytes = s.remote_spill_bytes, failed_queries = s.failed_queries,
        failed_exec_ms = s.failed_exec_ms, loaded_at = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        (usage_date, warehouse_name, credits, billed_hours, query_count, exec_ms, queue_ms,
         bytes_scanned, cache_bytes, remote_spill_bytes, failed_queries, failed_exec_ms)
        VALUES (s.usage_date, s.warehouse_name, s.credits, s.billed_hours, s.query_count, s.exec_ms, s.queue_ms,
                s.bytes_scanned, s.cache_bytes, s.remote_spill_bytes, s.failed_queries, s.failed_exec_ms);

    -- Per-query-pattern-day rollup (RUNS>=5/day to bound size).
    MERGE INTO MART_QUERY_PATTERN_DAILY t
    USING (
        SELECT TO_DATE(start_time) AS usage_date, query_parameterized_hash,
               COUNT(*) AS runs, SUM(execution_time) AS exec_ms, ANY_VALUE(LEFT(query_text, 120)) AS sample_query
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE start_time >= DATEADD('day', -:DAYS_BACK, CURRENT_TIMESTAMP())
          AND execution_time > 0 AND query_parameterized_hash IS NOT NULL
        GROUP BY 1, 2
        HAVING COUNT(*) >= 5
    ) s
    ON t.usage_date = s.usage_date AND t.query_parameterized_hash = s.query_parameterized_hash
    WHEN MATCHED THEN UPDATE SET runs = s.runs, exec_ms = s.exec_ms, sample_query = s.sample_query,
        loaded_at = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT (usage_date, query_parameterized_hash, runs, exec_ms, sample_query)
        VALUES (s.usage_date, s.query_parameterized_hash, s.runs, s.exec_ms, s.sample_query);

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
