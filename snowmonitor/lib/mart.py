"""Mart-first reads.

When setup/setup.sql has been deployed, the app reads pre-aggregated daily marts
instead of scanning ACCOUNT_USAGE live — fast, cheap, and consistent. `is_available()`
detects the marts once (cached); the sections fall back to lib.queries when absent.
"""

from __future__ import annotations

import streamlit as st

import config
from . import company, formulas
from .session import run

WH_MART = config.monitoring_fqn(config.MART_WAREHOUSE_DAILY)
ATTR_MART = config.monitoring_fqn(config.MART_QUERY_ATTR_DAILY)

# Mart attribution dimension -> column in MART_QUERY_ATTR_DAILY.
MART_DIMENSIONS = {
    "Database": "database_name",
    "User": "user_name",
    "Role": "role_name",
}


@st.cache_data(ttl=300, show_spinner=False)
def is_available() -> bool:
    """True when both marts exist (checked once per 5 min)."""
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
    """Mart covers warehouse + db/user/role. Schema/query-type stay live-only."""
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
    GROUP BY warehouse_name
    ORDER BY TOTAL_CREDITS DESC
    LIMIT {int(top)}
    """


def daily_spend_sql(days: int, company_name: str) -> str:
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT usage_date AS USAGE_DATE,
           SUM(total_credits) AS TOTAL_CREDITS,
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
    GROUP BY {dim_col}
    ORDER BY ALLOCATED_CREDITS DESC
    LIMIT {int(top)}
    """


def warehouse_daily_for_anomaly_sql(days: int, company_name: str) -> str:
    """Per-warehouse daily dollars for anomaly detection (from the mart)."""
    scope = company.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    return f"""
    SELECT usage_date AS USAGE_DATE, warehouse_name AS WAREHOUSE,
           {formulas.cost_sql('SUM(total_credits)', alias='COST_USD')}
    FROM {WH_MART}
    WHERE usage_date >= DATEADD('day', -{max(7, int(days))}, CURRENT_DATE())
      AND warehouse_name IS NOT NULL {scope}
    GROUP BY usage_date, warehouse_name
    """
