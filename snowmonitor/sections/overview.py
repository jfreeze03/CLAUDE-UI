"""Overview — KPIs, budget & forecast, spend trend, top warehouses, live alerts."""

from __future__ import annotations

import calendar
from datetime import datetime

import pandas as pd
import streamlit as st

from lib import session, queries, metrics, alerts, formulas, mart, forecast
import config
from ._common import scope, header, SEVERITY_EMOJI, md_escape


def render() -> None:
    company, env, days = scope()
    header("Overview", "Spend, forecast, reliability, and security at a glance.")

    m = metrics.gather(company, days)
    fired = alerts.evaluate(m)
    summary = alerts.summarize(fired)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("MTD spend", formulas.fmt_usd(m["mtd_spend_usd"]))
    delta = m["today_spend_usd"] - m["avg_daily_spend_usd_7d"]
    c2.metric("Today's spend", formulas.fmt_usd(m["today_spend_usd"]),
              delta=formulas.fmt_usd(delta), delta_color="inverse")
    c3.metric("Failed tasks", int(m["failed_task_runs"]))
    c4.metric("Failed logins", int(m["failed_logins"]))
    c5.metric("Open alerts", summary["total"],
              delta=f"{summary['Critical']} critical" if summary["Critical"] else None,
              delta_color="inverse")

    # ---- Budget & forecast ----
    _render_forecast(company, m)

    st.divider()
    left, right = st.columns([2, 1])
    use_mart = mart.is_available()
    with left:
        st.subheader("Daily spend trend")
        trend_sql = (mart.daily_spend_sql(max(days, 14), company) if use_mart
                     else queries.daily_spend_sql(max(days, 14), company))
        df = session.run(trend_sql, tier="standard", salt=session.refresh_salt())
        if not df.empty and "USAGE_DATE" in df.columns:
            st.line_chart(df.set_index("USAGE_DATE")["COST_USD"])
        else:
            st.info("No spend data in range.")

        st.subheader("Top warehouses")
        wh_sql = (mart.warehouse_cost_sql(days, company, top=10) if use_mart
                  else queries.warehouse_cost_sql(days, company, top=10))
        wh = session.run(wh_sql, tier="standard", salt=session.refresh_salt())
        if not wh.empty:
            st.dataframe(wh, use_container_width=True, hide_index=True)
        else:
            st.info("No warehouse metering in range.")

    with right:
        st.subheader("Active alerts")
        if not fired:
            st.success("No alerts. All monitored thresholds are within range.")
        else:
            for a in fired[:8]:
                with st.container(border=True):
                    st.markdown(f"{SEVERITY_EMOJI.get(a.severity,'')} **{md_escape(a.title)}** · {a.kind}")
                    st.caption(md_escape(a.detail))
            if summary["total"] > 8:
                st.caption(f"+{summary['total'] - 8} more on the Alerts page.")


def _render_forecast(company: str, m: dict) -> None:
    st.subheader("Budget & forecast")
    daily_df = session.run(queries.mtd_daily_spend_sql(company), tier="standard", salt=session.refresh_salt())
    daily = list(pd.to_numeric(daily_df["COST_USD"], errors="coerce").fillna(0)) if (
        not daily_df.empty and "COST_USD" in daily_df.columns) else []

    now = datetime.utcnow()
    dim = calendar.monthrange(now.year, now.month)[1]
    proj = forecast.month_end_projection(m["mtd_spend_usd"], now.day, dim, daily_spends=daily)
    budget = config.THRESHOLDS.get("monthly_budget_usd", 0)
    bstate = forecast.budget_status(proj["projection"], budget, m["mtd_spend_usd"], now.day, dim)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Forecast month-end", formulas.fmt_usd(proj["projection"]),
              help=f"Range {formulas.fmt_usd(proj['low'])}–{formulas.fmt_usd(proj['high'])} (±{proj['band_pct']:.0f}%)")
    k2.metric("Run-rate", f"{formulas.fmt_usd(proj['run_rate_daily'])}/day")
    if bstate.get("has_budget"):
        k3.metric("% of budget", f"{bstate['pct_of_budget']:.0f}%",
                  delta=bstate["state"], delta_color="inverse" if bstate["state"] != "On track" else "normal")
        k4.metric("Projected overage", formulas.fmt_usd(bstate["projected_overage"]))
    else:
        k3.metric("Budget", "not set")

    if daily and budget > 0:
        series = forecast.burndown_series(daily, budget, dim)
        if series:
            bdf = pd.DataFrame(series).set_index("DAY")
            st.caption("Budget burndown — cumulative actual vs straight-line budget")
            st.line_chart(bdf[["CUMULATIVE_ACTUAL", "BUDGET_LINE"]])
