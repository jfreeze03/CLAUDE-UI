"""Alerts — proactive + reactive alert list, and generated server-side ALERT SQL."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from lib import metrics, alerts as engine, queries, anomaly, ledger, mart, session, observability
from ._common import scope, header, SEVERITY_EMOJI


def _spend_anomaly_alerts(company: str, days: int) -> list:
    """Per-warehouse daily-spend anomalies vs each warehouse's own baseline."""
    sql = (mart.warehouse_daily_for_anomaly_sql(days, company) if mart.is_available()
           else queries.warehouse_cost_sql(days, company))  # live fallback is coarser
    if not mart.is_available():
        return []  # per-day baseline needs the mart's daily grain
    df = session.run(sql, tier="standard", salt=session.refresh_salt())
    if df.empty:
        return []
    found = anomaly.detect_anomalies(df, "WAREHOUSE", "COST_USD", "USAGE_DATE")
    return anomaly.to_alerts(found, "Cost", "Warehouse spend")


def render() -> None:
    company, env, days = scope()
    header("Alerts", "Proactive (forecast/trend/anomaly) and reactive (current failures).")

    m = metrics.gather(company, days)
    fired = engine.evaluate(m) + _spend_anomaly_alerts(company, days)
    fired.sort(key=lambda a: ({"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(a.severity, 9), a.domain))
    summary = engine.summarize(fired)

    # Give alerts memory (best-effort; no-op if the ledger isn't deployed).
    ledger.record(fired, company)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🔴 Critical", summary["Critical"])
    c2.metric("🟠 High", summary["High"])
    c3.metric("🟡 Medium", summary["Medium"])
    c4.metric("Total", summary["total"])

    proactive = [a for a in fired if a.kind == engine.PROACTIVE]
    reactive = [a for a in fired if a.kind == engine.REACTIVE]

    st.divider()
    col_p, col_r = st.columns(2)
    with col_p:
        st.subheader(f"Proactive ({len(proactive)})")
        st.caption("Fires before a budget/SLA is breached.")
        _render_alert_list(proactive)
    with col_r:
        st.subheader(f"Reactive ({len(reactive)})")
        st.caption("Fires on current failures.")
        _render_alert_list(reactive)

    st.divider()
    st.subheader("Alert history")
    hist = ledger.recent(company)
    if hist.empty:
        st.caption("No alert history yet (deploy setup/setup.sql to enable the ledger).")
    else:
        open_rows = hist[hist["STATUS"] != "ACK"] if "STATUS" in hist.columns else hist
        st.caption(f"{len(hist)} tracked · {len(open_rows)} open. Acknowledge to mute until it recurs.")
        for _, row in hist.head(20).iterrows():
            c1, c2 = st.columns([6, 1])
            with c1:
                emj = SEVERITY_EMOJI.get(row.get("SEVERITY", ""), "")
                status = row.get("STATUS", "OPEN")
                st.markdown(f"{emj} **{row.get('TITLE','')}** · {row.get('DOMAIN','')} · "
                            f"runs={int(row.get('RUNS',0) or 0)} · _{status}_")
                st.caption(f"first {row.get('FIRST_SEEN','')} · last {row.get('LAST_SEEN','')}"
                           + (f" · ack by {row.get('ACK_BY','')}" if status == "ACK" else ""))
            with c2:
                if status != "ACK" and st.button("Ack", key=f"ack_{row.get('ALERT_KEY','')}"):
                    ledger.acknowledge(row.get("ALERT_KEY", ""), observability.current_user())
                    st.rerun()

    st.divider()
    with st.expander("Deploy these as server-side Snowflake alerts (email delivery)"):
        st.caption(
            "SnowMonitor evaluates alerts live while open. To get notified without the app open, "
            "create native Snowflake ALERT objects. Set the warehouse, recipients, and notification "
            "integration, then run the generated SQL."
        )
        wh = st.text_input("Alerting warehouse", value="MONITOR_WH")
        recipients = st.text_input("Recipients", value=config.DEFAULT_ALERT_RECIPIENTS)
        integration = st.text_input("Notification integration", value=config.NOTIFICATION_INTEGRATION)
        sched = st.number_input("Check every (minutes)", min_value=5, max_value=1440, value=30, step=5)

        failed_task_condition = f"""    SELECT 1
    FROM {queries.AU}.TASK_HISTORY
    WHERE scheduled_time >= DATEADD('hour', -{max(1, sched // 60 or 1)}, CURRENT_TIMESTAMP())
      AND state = 'FAILED'"""
        sql = engine.build_alert_object_sql(
            name="SnowMonitor Failed Tasks",
            warehouse=wh, schedule_minutes=int(sched),
            condition_sql=failed_task_condition,
            message="One or more Snowflake tasks failed. Open SnowMonitor > Task Graphs.",
            recipients=recipients, integration=integration,
        )
        st.code(sql, language="sql")


def _render_alert_list(items) -> None:
    if not items:
        st.success("None.")
        return
    for a in items:
        with st.container(border=True):
            st.markdown(f"{SEVERITY_EMOJI.get(a.severity,'')} **{a.title}** — {a.domain}")
            st.write(a.detail)
            st.caption(f"Value: {a.value} (threshold {a.threshold})")
            st.caption(f"→ {a.action}")
