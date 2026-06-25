"""Alerts & Risks — unified issue feed, ledger history, server-side ALERT SQL."""

from __future__ import annotations

import streamlit as st

import config
from lib import issues, queries, ledger, observability, alerts as engine
from ._common import (scope, header, kpi_row, issue_card, empty, loading, SEVERITY_EMOJI, md_escape)


def _goto(page: str):
    def _f():
        st.session_state["page"] = page
        st.rerun()
    return _f


def _ack(domain: str, title: str, company: str, a):
    def _f():
        ledger.record([a], company)
        ledger.acknowledge(ledger.alert_key(domain, title, company), observability.current_user())
        st.rerun()
    return _f


def render() -> None:
    company, env, days = scope()
    header("Alerts & Risks", "Every open issue across cost, performance, tasks, and security — ranked.")

    with loading("Evaluating alerts and detections…"):
        feed = issues.gather_issues(company, days)
    summary = issues.counts(feed)
    st.session_state["_issue_counts"] = summary
    ledger.record(feed, company)

    kpi_row([
        {"label": "Critical", "value": summary["Critical"], "status": "crit" if summary["Critical"] else "ok",
         "good": summary["Critical"] == 0},
        {"label": "High", "value": summary["High"], "status": "high" if summary["High"] else "ok",
         "good": summary["High"] == 0},
        {"label": "Medium", "value": summary["Medium"], "status": "med" if summary["Medium"] else "ok"},
        {"label": "Total open", "value": summary["total"], "status": "neutral"},
    ])

    st.divider()
    _DOMAIN_PAGE = {"Cost": "Cost", "Tasks": "Task Graphs", "Security": "Security",
                    "Performance": "Query Explorer"}

    proactive = [a for a in feed if a.kind == engine.PROACTIVE]
    reactive = [a for a in feed if a.kind == engine.REACTIVE]

    col_p, col_r = st.columns(2)
    with col_p:
        st.subheader(f"Proactive ({len(proactive)})")
        st.caption("Fires before a budget/SLA is breached.")
        if not proactive:
            empty("Nothing trending toward a breach.")
        for i, a in enumerate(proactive):
            issue_card(a.severity, a.domain, a.title, a.detail, a.action, key=f"p_{i}",
                       on_investigate=_goto(_DOMAIN_PAGE.get(a.domain, "Cost")))
    with col_r:
        st.subheader(f"Reactive ({len(reactive)})")
        st.caption("Fires on current failures and detections.")
        if not reactive:
            empty("No active failures or detections.")
        for i, a in enumerate(reactive):
            issue_card(a.severity, a.domain, a.title, a.detail, a.action, key=f"r_{i}",
                       on_investigate=_goto(_DOMAIN_PAGE.get(a.domain, "Security")),
                       on_ack=_ack(a.domain, a.title, company, a))

    st.divider()
    st.subheader("Alert history")
    hist = ledger.recent(company)
    if hist.empty:
        empty("No alert history yet (deploy setup/setup.sql to enable the ledger).")
    else:
        open_rows = hist[hist["STATUS"] != "ACK"] if "STATUS" in hist.columns else hist
        st.caption(f"{len(hist)} tracked · {len(open_rows)} open. Acknowledge to mute until it recurs.")
        for _, row in hist.head(20).iterrows():
            cc1, cc2 = st.columns([6, 1])
            with cc1:
                emj = SEVERITY_EMOJI.get(row.get("SEVERITY", ""), "")
                status = row.get("STATUS", "OPEN")
                st.markdown(f"{emj} **{md_escape(row.get('TITLE',''))}** · {row.get('DOMAIN','')} · "
                            f"runs={int(row.get('RUNS',0) or 0)} · _{status}_")
                st.caption(f"first {row.get('FIRST_SEEN','')} · last {row.get('LAST_SEEN','')}"
                           + (f" · ack by {row.get('ACK_BY','')}" if status == "ACK" else ""))
            with cc2:
                if status != "ACK" and st.button("Ack", key=f"hack_{row.get('ALERT_KEY','')}"):
                    ledger.acknowledge(row.get("ALERT_KEY", ""), observability.current_user())
                    st.rerun()

    st.divider()
    with st.expander("Deploy these as server-side Snowflake alerts (email delivery)"):
        st.caption(
            "SnowMonitor evaluates alerts live while open. For notification without the app open, "
            "create a native Snowflake ALERT object. Set the warehouse, recipients, and integration."
        )
        wh = st.text_input("Alerting warehouse", value="MONITOR_WH")
        recipients = st.text_input("Recipients", value=config.DEFAULT_ALERT_RECIPIENTS)
        integration = st.text_input("Notification integration", value=config.NOTIFICATION_INTEGRATION)
        sched = st.number_input("Check every (minutes)", min_value=5, max_value=1440, value=30, step=5)
        condition = f"""    SELECT 1
    FROM {queries.AU}.TASK_HISTORY
    WHERE scheduled_time >= DATEADD('hour', -{max(1, int(sched) // 60 or 1)}, CURRENT_TIMESTAMP())
      AND state = 'FAILED'"""
        sql = engine.build_alert_object_sql(
            name="SnowMonitor Failed Tasks", warehouse=wh, schedule_minutes=int(sched),
            condition_sql=condition,
            message="One or more Snowflake tasks failed. Open SnowMonitor > Task Graphs.",
            recipients=recipients, integration=integration,
        )
        st.code(sql, language="sql")
