"""Mart-first reads. When setup/setup.sql is deployed, the app reads pre-aggregated
daily marts instead of scanning ACCOUNT_USAGE live. `is_available()` detects them;
sections fall back to lib.queries when absent."""

from __future__ import annotations

import streamlit as st

import config
from . import company, formulas
from .session import run

WH_MART = config.monitoring_fqn(config.MART_WAREHOUSE_DAILY)
ATTR_MART = config.monitoring_fqn(config.MART_QUERY_ATTR_DAILY)
EFF_MART = config.monitoring_fqn("MART_WAREHOUSE_EFFICIENCY_DAILY")
PATTERN_MART = config.monitoring_fqn("MART_QUERY_PATTERN_DAILY")
_RATE = config.CREDIT_PRICE_USD

MART_DIMENSIONS = {"Database": "database_name", "User": "user_name", "Role": "role_name"}


@st.cache_data(ttl=300, show_spinner=False)
def is_available() -> bool:
    df = run(
        f"""SELECT COUNT(*) AS N FROM {config.MONITORING_DATABASE}.INFORMATION_SCHEMA.TABLES
            WHERE table_schema = '{config.MONITORING_SCHEMA}'
              AND table_name IN ('{config.MART_WAREHOUSE_DAILY}', '{config.MART_QUERY_ATTR_DAILY}')""",
        tier="metadata",
    )
    try:
        return (not df.empty) and int(df.iloc[0]["N"]) >= 2
    except Exception:
        return False


def supports_dimension(dimension: str) -> bool:
    return dimension == "Warehouse" or dimension in MART_DIMENSIONS


def warehouse_cost_sql(days: int, company_name: str, top: int = 50) -> str:
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT warehouse_name AS WAREHOUSE,
           SUM(compute_credits) AS COMPUTE_CREDITS,
           SUM(cloud_services_credits) AS CLOUD_SERVICES_CREDITS,
           SUM(total_credits) AS TOTAL_CREDITS,
           {formulas.cost_sql('SUM(total_credits)', alias='COST_USD')}
    FROM {WH_MART}
    WHERE usage_date >= DATEADD('day', -{max(1, int(days))}, CURRENT_DATE())
      AND warehouse_name IS NOT NULL {scope}
    GROUP BY warehouse_name ORDER BY TOTAL_CREDITS DESC LIMIT {int(top)}
    """


def daily_spend_sql(days: int, company_name: str) -> str:
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT usage_date AS USAGE_DATE, SUM(total_credits) AS TOTAL_CREDITS,
           {formulas.cost_sql('SUM(total_credits)', alias='COST_USD')}
    FROM {WH_MART}
    WHERE usage_date >= DATEADD('day', -{max(1, int(days))}, CURRENT_DATE())
      AND warehouse_name IS NOT NULL {scope}
    GROUP BY usage_date ORDER BY usage_date
    """


def cost_by_dimension_sql(dimension: str, days: int, company_name: str, top: int = 50) -> str:
    dim_col = MART_DIMENSIONS.get(dimension, "user_name")
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    return f"""
    SELECT {dim_col} AS {dimension.upper().replace(' ', '_')},
           ROUND(SUM(allocated_credits), 2) AS ALLOCATED_CREDITS,
           {formulas.cost_sql('SUM(allocated_credits)', alias='ALLOCATED_COST_USD')}
    FROM {ATTR_MART}
    WHERE usage_date >= DATEADD('day', -{max(1, int(days))}, CURRENT_DATE()) {scope}
    GROUP BY {dim_col} ORDER BY ALLOCATED_CREDITS DESC LIMIT {int(top)}
    """


def warehouse_daily_for_anomaly_sql(days: int, company_name: str) -> str:
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT usage_date AS USAGE_DATE, warehouse_name AS WAREHOUSE,
           {formulas.cost_sql('SUM(total_credits)', alias='COST_USD')}
    FROM {WH_MART}
    WHERE usage_date >= DATEADD('day', -{max(7, int(days))}, CURRENT_DATE())
      AND warehouse_name IS NOT NULL {scope}
    GROUP BY usage_date, warehouse_name
    """


# --------------------------------------------------------------------------
# Query-metrics mart (efficiency + repeated-query) — fast reads for the heavy
# pages. is_efficiency_available() gates them; callers fall back to live SQL.
# --------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def is_efficiency_available() -> bool:
    df = run(
        f"""SELECT COUNT(*) AS N FROM {config.MONITORING_DATABASE}.INFORMATION_SCHEMA.TABLES
            WHERE table_schema = '{config.MONITORING_SCHEMA}'
              AND table_name IN ('MART_WAREHOUSE_EFFICIENCY_DAILY', 'MART_QUERY_PATTERN_DAILY')""",
        tier="metadata",
    )
    try:
        return (not df.empty) and int(df.iloc[0]["N"]) >= 2
    except Exception:
        return False


def _eff_window(days: int) -> str:
    return f"usage_date >= DATEADD('day', -{max(1, int(days))}, CURRENT_DATE())"


def efficiency_summary_sql(days: int, company_name: str) -> str:
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT
        ROUND(SUM(credits) * {_RATE}, 2) AS TOTAL_COST_USD,
        SUM(query_count) AS QUERIES,
        ROUND(SUM(credits) * {_RATE} / NULLIF(SUM(query_count), 0), 4) AS COST_PER_QUERY_USD,
        ROUND(SUM(bytes_scanned) / POWER(1024, 4), 2) AS TB_SCANNED,
        ROUND(SUM(credits) * {_RATE} / NULLIF(SUM(bytes_scanned) / POWER(1024, 4), 0), 2) AS COST_PER_TB_USD,
        ROUND(SUM(cache_bytes) / NULLIF(SUM(bytes_scanned), 0) * 100, 1) AS AVG_CACHE_PCT,
        SUM(failed_queries) AS FAILED_QUERIES,
        ROUND(SUM(credits) * {_RATE} * SUM(failed_exec_ms) / NULLIF(SUM(exec_ms), 0), 2) AS FAILED_QUERY_WASTE_USD
    FROM {EFF_MART}
    WHERE {_eff_window(days)} AND warehouse_name IS NOT NULL {scope}
    """


def warehouse_efficiency_sql(days: int, company_name: str, top: int = 50) -> str:
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT warehouse_name AS WAREHOUSE,
           ROUND(SUM(credits) * {_RATE}, 2) AS COST_USD,
           SUM(query_count) AS QUERIES,
           ROUND(SUM(credits) / NULLIF(SUM(exec_ms) / 1000.0 / 3600.0, 0), 2) AS CREDITS_PER_EXEC_HOUR,
           ROUND(SUM(queue_ms) / 1000.0, 0) AS QUEUE_SECONDS,
           ROUND(SUM(remote_spill_bytes) / POWER(1024, 3), 1) AS REMOTE_SPILL_GB
    FROM {EFF_MART}
    WHERE {_eff_window(days)} AND warehouse_name IS NOT NULL {scope}
    GROUP BY warehouse_name
    ORDER BY COST_USD DESC
    LIMIT {int(top)}
    """


def idle_signal_sql(days: int, company_name: str) -> str:
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT warehouse_name AS WAREHOUSE,
           ROUND(SUM(credits) * {_RATE}, 2) AS WINDOW_COST_USD,
           ROUND(100 * (1 - LEAST(1, SUM(exec_ms) / 1000.0 / NULLIF(SUM(billed_hours) * 3600.0, 0))), 1) AS IDLE_PCT
    FROM {EFF_MART}
    WHERE {_eff_window(days)} AND warehouse_name IS NOT NULL {scope}
    GROUP BY warehouse_name
    HAVING SUM(credits) > 0
    ORDER BY WINDOW_COST_USD DESC
    """


def repeated_query_signal_sql(days: int, min_runs: int = 50) -> str:
    return f"""
    SELECT query_parameterized_hash AS QUERY_HASH,
           SUM(runs) AS RUNS,
           ROUND(SUM(exec_ms) / 1000.0 / 3600.0, 2) AS TOTAL_EXEC_HOURS,
           ANY_VALUE(sample_query) AS SAMPLE_QUERY
    FROM {PATTERN_MART}
    WHERE {_eff_window(days)}
    GROUP BY query_parameterized_hash
    HAVING SUM(runs) >= {int(min_runs)}
    ORDER BY TOTAL_EXEC_HOURS DESC
    LIMIT 50
    """
