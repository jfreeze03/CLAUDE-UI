"""Task Graphs — freshness/SLA, active failures, duration degradation, cost, DAGs."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import session, queries, tasks_intel, tasks_logic, anomaly
from ._common import scope, header


_STATUS_EMOJI = {"On time": "🟢", "Late": "🟡", "Stale": "🔴", "Unknown": "⚪"}


def _df(sql):
    return session.run(sql, tier="standard", salt=session.refresh_salt())


def _consecutive(states_df: pd.DataFrame) -> pd.DataFrame:
    if states_df is None or states_df.empty or "TASK" not in states_df.columns:
        return pd.DataFrame()
    rows = []
    for task, grp in states_df.groupby("TASK"):
        states = list(grp.sort_values("SCHEDULED_TIME", ascending=False)["STATE"])
        c = tasks_logic.consecutive_failures(states)
        if c > 0:
            rows.append({"TASK": task, "CONSECUTIVE_FAILURES": c, "LAST_STATE": states[0]})
    df = pd.DataFrame(rows)
    return df.sort_values("CONSECUTIVE_FAILURES", ascending=False) if not df.empty else df


def render() -> None:
    company, env, days = scope()
    header("Task Graphs", "Freshness, active failures, duration drift, and cost — not just run counts.")

    sla = _df(tasks_intel.task_sla_sql(days, company))
    health = _df(tasks_intel.task_health_sql(days, company))
    states = _df(tasks_intel.recent_task_states_sql(days, company))
    consec = _consecutive(states)

    if not sla.empty:
        sla["STATUS"] = sla.apply(
            lambda r: tasks_logic.sla_status(r.get("MINUTES_SINCE_LAST"), r.get("EXPECTED_INTERVAL_MIN")), axis=1)
        sla_summary = tasks_logic.sla_summary(sla.to_dict("records"))
    else:
        sla_summary = {"On time": 0, "Late": 0, "Stale": 0, "Unknown": 0}

    total_tasks = len(health)
    failed_runs = int(health["FAILED"].sum()) if not health.empty and "FAILED" in health else 0
    succ = int(health["SUCCEEDED"].sum()) if not health.empty and "SUCCEEDED" in health else 0
    runs = int(health["RUNS"].sum()) if not health.empty and "RUNS" in health else 0
    success_pct = round(succ * 100.0 / runs, 1) if runs else 0
    broken = int((consec["CONSECUTIVE_FAILURES"] >= 2).sum()) if not consec.empty else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tasks", total_tasks)
    c2.metric("Success rate", f"{success_pct:.0f}%")
    c3.metric("🔴 Stale (overdue)", sla_summary["Stale"],
              delta=None if sla_summary["Stale"] == 0 else "fresh data at risk", delta_color="inverse")
    c4.metric("Actively broken", broken,
              delta=None if broken == 0 else "≥2 consecutive fails", delta_color="inverse")
    c5.metric("Failed runs", failed_runs)

    t_fresh, t_fail, t_dur, t_cost, t_graph = st.tabs(
        ["Freshness / SLA", "Failures", "Duration", "Cost", "Graphs"])

    # ---- Freshness / SLA ----
    with t_fresh:
        st.caption("Time since last success vs each task's own cadence. Stale = pipeline likely stopped.")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("🟢 On time", sla_summary["On time"])
        s2.metric("🟡 Late", sla_summary["Late"])
        s3.metric("🔴 Stale", sla_summary["Stale"])
        s4.metric("⚪ Unknown cadence", sla_summary["Unknown"])
        if sla.empty:
            st.info("No task runs in range.")
        else:
            sla_view = sla.copy()
            sla_view.insert(0, "", sla_view["STATUS"].map(lambda s: _STATUS_EMOJI.get(s, "")))
            # Stale/Late first
            order = {"Stale": 0, "Late": 1, "Unknown": 2, "On time": 3}
            sla_view = sla_view.sort_values("STATUS", key=lambda col: col.map(order))
            st.dataframe(sla_view, use_container_width=True, hide_index=True)

    # ---- Failures ----
    with t_fail:
        st.subheader("Actively broken (consecutive failures)")
        if consec.empty:
            st.success("No task is currently in a failure streak.")
        else:
            st.dataframe(consec, use_container_width=True, hide_index=True)
        st.subheader("Failure clusters (by error)")
        errors = _df(tasks_intel.error_clusters_sql(days, company))
        if errors.empty:
            st.success("No task failures in range.")
        else:
            st.dataframe(errors, use_container_width=True, hide_index=True)
            st.caption("One error across many tasks ⇒ a systemic cause (permissions, source, warehouse).")

    # ---- Duration ----
    with t_dur:
        st.caption("p95 duration and tasks running slower than their own baseline.")
        dur_daily = _df(tasks_intel.task_duration_daily_sql(days, company))
        anoms = anomaly.detect_anomalies(dur_daily, "TASK", "AVG_DURATION_SEC", "USAGE_DATE",
                                         min_abs=30, min_baseline_days=4) if not dur_daily.empty else []
        if anoms:
            st.warning(f"{len(anoms)} task(s) ran notably slower than baseline:")
            st.dataframe(pd.DataFrame(anoms)[["entity", "latest", "baseline_mean", "pct_above_mean"]]
                         .rename(columns={"entity": "TASK", "latest": "LATEST_SEC",
                                          "baseline_mean": "BASELINE_SEC", "pct_above_mean": "PCT_ABOVE"}),
                         use_container_width=True, hide_index=True)
        if not health.empty:
            st.subheader("Per-task duration & success")
            cols = [c for c in ["TASK", "DATABASE", "RUNS", "SUCCESS_PCT", "AVG_DURATION_SEC", "P95_DURATION_SEC", "LAST_RUN"]
                    if c in health.columns]
            st.dataframe(health[cols].sort_values("P95_DURATION_SEC", ascending=False) if "P95_DURATION_SEC" in health.columns
                         else health[cols], use_container_width=True, hide_index=True)
        else:
            st.info("No task runs in range.")

    # ---- Cost ----
    with t_cost:
        st.caption("Serverless task credits. Warehouse-run tasks bill via warehouse metering (see Cost).")
        cost = _df(tasks_intel.serverless_task_cost_sql(days, company))
        if cost.empty:
            st.info("No serverless task cost in range (or no serverless tasks).")
        else:
            if "COST_USD" in cost.columns and "TASK" in cost.columns:
                st.bar_chart(cost.head(15).set_index("TASK")["COST_USD"])
            st.dataframe(cost, use_container_width=True, hide_index=True)

    # ---- Graphs ----
    with t_graph:
        graph = _df(queries.task_graph_sql(days, company))
        if graph.empty:
            st.info("No task graphs in range.")
        else:
            st.dataframe(graph, use_container_width=True, hide_index=True)
            st.caption("Per root task: runs, failures, avg duration. High FAILED_RUNS ⇒ inspect the failing member.")
