"""Task Graphs — freshness/SLA, active failures, duration degradation, cost, DAGs.

Top KPIs use the shared SLA/health/state queries; each sub-view loads only its own
extra query (no eager st.tabs), keeping the page fast.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import session, queries, tasks_intel, tasks_logic, anomaly
from ._common import (scope, header, subview, kpi_row, render_table, alt_bar, empty, loading)


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

    with loading("Loading task health…"):
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

    kpi_row([
        {"label": "Tasks", "value": total_tasks},
        {"label": "Success rate", "value": f"{success_pct:.0f}%",
         "status": "ok" if success_pct >= 99 else ("med" if success_pct >= 95 else "high"),
         "good": success_pct >= 99},
        {"label": "Stale (overdue)", "value": sla_summary["Stale"],
         "status": "crit" if sla_summary["Stale"] else "ok", "good": sla_summary["Stale"] == 0},
        {"label": "Actively broken", "value": broken,
         "status": "high" if broken else "ok", "good": broken == 0,
         "help": "≥2 consecutive failures"},
        {"label": "Failed runs", "value": failed_runs,
         "status": "med" if failed_runs else "ok"},
    ])

    view = subview(["Freshness / SLA", "Failures", "Duration", "Cost", "Graphs"], key="tasks")

    # ---- Freshness / SLA ----
    if view == "Freshness / SLA":
        st.caption("Time since last success vs each task's own cadence. Stale = pipeline likely stopped.")
        kpi_row([
            {"label": "🟢 On time", "value": sla_summary["On time"], "status": "ok"},
            {"label": "🟡 Late", "value": sla_summary["Late"], "status": "med" if sla_summary["Late"] else "ok"},
            {"label": "🔴 Stale", "value": sla_summary["Stale"], "status": "crit" if sla_summary["Stale"] else "ok"},
            {"label": "⚪ Unknown cadence", "value": sla_summary["Unknown"]},
        ])
        if sla.empty:
            empty("No task runs in range.")
        else:
            sla_view = sla.copy()
            sla_view.insert(0, "", sla_view["STATUS"].map(lambda s: _STATUS_EMOJI.get(s, "")))
            order = {"Stale": 0, "Late": 1, "Unknown": 2, "On time": 3}
            sla_view = sla_view.sort_values("STATUS", key=lambda col: col.map(order))
            render_table(sla_view)

    # ---- Failures ----
    elif view == "Failures":
        st.subheader("Actively broken (consecutive failures)")
        if consec.empty:
            st.success("No task is currently in a failure streak.")
        else:
            render_table(consec)
        st.subheader("Failure clusters (by error)")
        errors = _df(tasks_intel.error_clusters_sql(days, company))
        if errors.empty:
            st.success("No task failures in range.")
        else:
            render_table(errors)
            st.caption("One error across many tasks ⇒ a systemic cause (permissions, source, warehouse).")

    # ---- Duration ----
    elif view == "Duration":
        st.caption("p95 duration and tasks running slower than their own baseline.")
        dur_daily = _df(tasks_intel.task_duration_daily_sql(days, company))
        anoms = anomaly.detect_anomalies(dur_daily, "TASK", "AVG_DURATION_SEC", "USAGE_DATE",
                                         min_abs=30, min_baseline_days=4) if not dur_daily.empty else []
        if anoms:
            st.warning(f"{len(anoms)} task(s) ran notably slower than baseline:")
            render_table(pd.DataFrame(anoms)[["entity", "latest", "baseline_mean", "pct_above_mean"]]
                         .rename(columns={"entity": "TASK", "latest": "LATEST_SEC",
                                          "baseline_mean": "BASELINE_SEC", "pct_above_mean": "PCT_ABOVE"}))
        if not health.empty:
            st.subheader("Per-task duration & success")
            cols = [c for c in ["TASK", "DATABASE", "RUNS", "SUCCESS_PCT", "AVG_DURATION_SEC",
                                "P95_DURATION_SEC", "LAST_RUN"] if c in health.columns]
            view_df = (health[cols].sort_values("P95_DURATION_SEC", ascending=False)
                       if "P95_DURATION_SEC" in health.columns else health[cols])
            render_table(view_df)
        else:
            empty("No task runs in range.")

    # ---- Cost ----
    elif view == "Cost":
        st.caption("Serverless task credits. Warehouse-run tasks bill via warehouse metering (see Cost).")
        cost = _df(tasks_intel.serverless_task_cost_sql(days, company))
        if cost.empty:
            empty("No serverless task cost in range (or no serverless tasks).")
        else:
            if "COST_USD" in cost.columns and "TASK" in cost.columns:
                alt_bar(cost, x="TASK", y="COST_USD", money=True, top=15)
            render_table(cost)

    # ---- Graphs ----
    elif view == "Graphs":
        graph = _df(queries.task_graph_sql(days, company))
        if graph.empty:
            empty("No task graphs in range.")
        else:
            render_table(graph)
            st.caption("Per root task: runs, failures, avg duration. High Failed Runs ⇒ inspect the failing member.")
