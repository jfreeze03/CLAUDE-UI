"""Metric gathering — runs the queries that feed Overview KPIs and the alert engine."""

from __future__ import annotations

import calendar
from datetime import datetime

import pandas as pd

import config
from . import queries, session
from .session import run

AU = queries.AU


def _scalar(df: pd.DataFrame, col: str, default: float = 0.0) -> float:
    if df is None or df.empty or col not in df.columns:
        return default
    try:
        return float(pd.to_numeric(df[col], errors="coerce").fillna(0).iloc[0])
    except Exception:
        return default


def gather(company_name: str, days: int) -> dict:
    from . import company as company_mod
    salt = session.refresh_salt()
    now = datetime.utcnow()
    days_in_month = calendar.monthrange(now.year, now.month)[1]

    wh_scope = company_mod.company_scope_sql(company_name, wh_col="warehouse_name", db_col=None, user_col=None)
    q_scope = company_mod.company_scope_sql(company_name, wh_col="warehouse_name", db_col="database_name", user_col="user_name")
    db_scope = company_mod.company_scope_sql(company_name, wh_col=None, db_col="database_name", user_col=None)
    user_scope = company_mod.company_scope_sql(company_name, wh_col=None, db_col=None, user_col="user_name")

    cost_df = run(f"""
        SELECT
            SUM(IFF(start_time >= DATE_TRUNC('month', CURRENT_TIMESTAMP()),
                    COALESCE(credits_used,0), 0)) * {config.CREDIT_PRICE_USD} AS MTD_SPEND_USD,
            SUM(IFF(start_time >= DATE_TRUNC('day', CURRENT_TIMESTAMP()),
                    COALESCE(credits_used,0), 0)) * {config.CREDIT_PRICE_USD} AS TODAY_SPEND_USD,
            SUM(IFF(start_time >= DATEADD('day', -7, DATE_TRUNC('day', CURRENT_TIMESTAMP()))
                    AND start_time < DATE_TRUNC('day', CURRENT_TIMESTAMP()),
                    COALESCE(credits_used,0), 0)) * {config.CREDIT_PRICE_USD} / 7 AS AVG_DAILY_SPEND_USD_7D
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= DATE_TRUNC('month', CURRENT_TIMESTAMP())
          AND warehouse_name IS NOT NULL {wh_scope}
    """, tier="standard", salt=salt)

    storage_df = run(f"""
        SELECT
            SUM(IFF(usage_date = (SELECT MAX(usage_date) FROM {AU}.DATABASE_STORAGE_USAGE_HISTORY),
                    average_database_bytes, 0)) / POWER(1024,4) AS STORAGE_TB_CURRENT,
            SUM(IFF(usage_date = DATEADD('day', -7, (SELECT MAX(usage_date) FROM {AU}.DATABASE_STORAGE_USAGE_HISTORY)),
                    average_database_bytes, 0)) / POWER(1024,4) AS STORAGE_TB_PRIOR
        FROM {AU}.DATABASE_STORAGE_USAGE_HISTORY
        WHERE usage_date >= DATEADD('day', -10, CURRENT_DATE())
          AND database_name IS NOT NULL {db_scope}
    """, tier="standard", salt=salt)

    qh_df = run(f"""
        SELECT
            COUNT(*) AS TOTAL_QUERIES,
            SUM(IFF(execution_status = 'FAIL', 1, 0)) AS FAILED_QUERIES,
            SUM(IFF(execution_status = 'FAIL', 1, 0)) * 100.0 / NULLIF(COUNT(*),0) AS FAILED_QUERY_RATE_PCT,
            SUM(IFF(queued_overload_time > 0 OR queued_provisioning_time > 0, 1, 0)) AS QUEUED_QUERIES,
            SUM(COALESCE(bytes_spilled_to_remote_storage,0)) / POWER(1024,3) AS REMOTE_SPILL_GB
        FROM {AU}.QUERY_HISTORY
        WHERE start_time >= DATEADD('day', -{max(1,int(days))}, CURRENT_TIMESTAMP())
          AND warehouse_name IS NOT NULL {q_scope}
    """, tier="standard", salt=salt)

    task_df = run(f"""
        SELECT SUM(IFF(state = 'FAILED', 1, 0)) AS FAILED_TASK_RUNS
        FROM {AU}.TASK_HISTORY
        WHERE scheduled_time >= DATEADD('day', -{max(1,int(days))}, CURRENT_TIMESTAMP())
          AND database_name IS NOT NULL {db_scope}
    """, tier="standard", salt=salt)

    login_df = run(f"""
        SELECT COUNT(*) AS FAILED_LOGINS
        FROM {AU}.LOGIN_HISTORY
        WHERE event_timestamp >= DATEADD('day', -{max(1,int(days))}, CURRENT_TIMESTAMP())
          AND is_success = 'NO' {user_scope}
    """, tier="standard", salt=salt)

    mfa_df = run(queries.users_without_mfa_sql(company_name), tier="standard", salt=salt)
    grants_df = run(f"""
        SELECT COUNT(*) AS NEW_GRANTS
        FROM {AU}.GRANTS_TO_ROLES
        WHERE created_on >= DATEADD('day', -{max(1,int(days))}, CURRENT_TIMESTAMP())
          AND deleted_on IS NULL
    """, tier="standard", salt=salt)

    return {
        "mtd_spend_usd": _scalar(cost_df, "MTD_SPEND_USD"),
        "today_spend_usd": _scalar(cost_df, "TODAY_SPEND_USD"),
        "avg_daily_spend_usd_7d": _scalar(cost_df, "AVG_DAILY_SPEND_USD_7D"),
        "days_elapsed": now.day,
        "days_in_month": days_in_month,
        "storage_tb_current": _scalar(storage_df, "STORAGE_TB_CURRENT"),
        "storage_tb_prior": _scalar(storage_df, "STORAGE_TB_PRIOR"),
        "total_queries": _scalar(qh_df, "TOTAL_QUERIES"),
        "failed_query_rate_pct": _scalar(qh_df, "FAILED_QUERY_RATE_PCT"),
        "queued_queries": _scalar(qh_df, "QUEUED_QUERIES"),
        "remote_spill_gb": _scalar(qh_df, "REMOTE_SPILL_GB"),
        "failed_task_runs": _scalar(task_df, "FAILED_TASK_RUNS"),
        "failed_logins": _scalar(login_df, "FAILED_LOGINS"),
        "users_without_mfa": 0 if mfa_df is None or mfa_df.empty else len(mfa_df),
        "new_grants": _scalar(grants_df, "NEW_GRANTS"),
    }
