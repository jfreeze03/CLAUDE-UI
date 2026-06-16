"""Overview — headline KPIs, spend trend, top warehouses, live alert summary."""

from __future__ import annotations

import streamlit as st

import config
from lib import session, queries, metrics, alerts, formulas, mart
from ._common import scope, header, SEVERITY_EMOJI


def render() -> None:
    company, env, days = scope()
    header("Overview", "Spend, reliability, and security at a glance.")

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
            st.dataframe(wh, width="stretch", hide_index=True)
        else:
            st.info("No warehouse metering in range.")

    with right:
        st.subheader("Active alerts")
        if not fired:
            st.success("No alerts. All monitored thresholds are within range.")
        else:
            for a in fired[:8]:
                with st.container(border=True):
                    st.markdown(f"{SEVERITY_EMOJI.get(a.severity,'')} **{a.title}** · {a.kind}")
                    st.caption(a.detail)
            if summary["total"] > 8:
                st.caption(f"+{summary['total'] - 8} more on the Alerts page.")
