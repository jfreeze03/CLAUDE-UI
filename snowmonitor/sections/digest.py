"""Digest — paste-ready executive summary + scheduled server-side email SQL."""

from __future__ import annotations

import calendar
from datetime import datetime

import pandas as pd
import streamlit as st

import config
from lib import (session, queries, metrics, alerts, mart, forecast, digest as digest_lib,
                 recommend)
from ._common import scope, header


def _rows(df):
    return [] if df is None or df.empty else df.to_dict("records")


def render() -> None:
    company, env, days = scope()
    header("Digest", "A paste-ready executive summary, plus SQL to email it on a schedule.")

    salt = session.refresh_salt()
    m = metrics.gather(company, days)
    summary = alerts.summarize(alerts.evaluate(m))

    # Forecast
    now = datetime.utcnow()
    dim = calendar.monthrange(now.year, now.month)[1]
    daily_df = session.run(queries.mtd_daily_spend_sql(company), tier="standard", salt=salt)
    daily = list(pd.to_numeric(daily_df["COST_USD"], errors="coerce").fillna(0)) if (
        not daily_df.empty and "COST_USD" in daily_df.columns) else []
    proj = forecast.month_end_projection(m["mtd_spend_usd"], now.day, dim, daily_spends=daily)
    budget = config.THRESHOLDS.get("monthly_budget_usd", 0)
    bstate = forecast.budget_status(proj["projection"], budget, m["mtd_spend_usd"], now.day, dim)

    # Top cost driver
    use_mart = mart.is_available()
    wh_sql = mart.warehouse_cost_sql(days, company, top=1) if use_mart else queries.warehouse_cost_sql(days, company, top=1)
    wh = session.run(wh_sql, tier="standard", salt=salt)
    top_driver = None
    if not wh.empty and "WAREHOUSE" in wh.columns and "COST_USD" in wh.columns:
        top_driver = (str(wh.iloc[0]["WAREHOUSE"]), float(wh.iloc[0]["COST_USD"]))

    # Savings (recommendations)
    idle = session.run(recommend.idle_warehouse_signal_sql(days), tier="standard", salt=salt)
    tt = session.run(recommend.time_travel_signal_sql(), tier="standard", salt=salt)
    rq = session.run(recommend.repeated_query_signal_sql(days), tier="standard", salt=salt)
    recs = recommend.rank(
        recommend.idle_warehouse_recs(_rows(idle), window_days=days),
        recommend.time_travel_recs(_rows(tt)),
        recommend.repeated_query_recs(_rows(rq)),
    )
    savings = recommend.total_savings(recs)

    md = digest_lib.build_digest(company, days, m, summary, savings, proj, bstate, top_driver)

    st.markdown(md)
    st.download_button("⬇ Download digest (.md)", md.encode("utf-8"),
                       file_name=f"snowmonitor_digest_{company}.md", mime="text/markdown")

    st.divider()
    with st.expander("Schedule this digest as a server-side email"):
        st.caption("Runs as a Snowflake task against the cost mart — no app needed. Requires setup/setup.sql "
                   "and an email notification integration.")
        wh_name = st.text_input("Task warehouse", value="MONITOR_WH", key="dg_wh")
        recipients = st.text_input("Recipients", value=config.DEFAULT_ALERT_RECIPIENTS, key="dg_to")
        integration = st.text_input("Notification integration", value=config.NOTIFICATION_INTEGRATION, key="dg_int")
        sched = st.selectbox("Schedule", ["Weekly (Mon 8am ET)", "Daily (8am ET)"], key="dg_sched")
        cron = ("USING CRON 0 8 * * MON America/New_York" if sched.startswith("Weekly")
                else "USING CRON 0 8 * * * America/New_York")
        sql = digest_lib.digest_task_sql("SnowMonitor Digest", wh_name, cron, recipients, integration)
        st.code(sql, language="sql")
