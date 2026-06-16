"""Task Graphs — per-graph rollup of runs, failures, duration; recent run detail."""

from __future__ import annotations

import streamlit as st

from lib import session, queries
from ._common import scope, header


def render() -> None:
    company, env, days = scope()
    header("Task Graphs", "Task run health by graph (root task) with failures and durations.")

    graph = session.run(queries.task_graph_sql(days, company), tier="standard", salt=session.refresh_salt())
    runs = session.run(queries.task_runs_sql(days, company), tier="standard", salt=session.refresh_salt())

    total_runs = int(graph["TOTAL_RUNS"].sum()) if not graph.empty and "TOTAL_RUNS" in graph else 0
    failed_runs = int(graph["FAILED_RUNS"].sum()) if not graph.empty and "FAILED_RUNS" in graph else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Task graphs", len(graph))
    c2.metric("Total runs", total_runs)
    c3.metric("Failed runs", failed_runs, delta=None if failed_runs == 0 else f"{failed_runs}", delta_color="inverse")

    st.subheader("Graphs (root tasks)")
    if graph.empty:
        st.info("No task runs in range.")
    else:
        st.dataframe(graph, width="stretch", hide_index=True)

    st.subheader("Recent runs")
    if runs.empty:
        st.info("No task runs in range.")
    else:
        only_failed = st.checkbox("Failures only", value=failed_runs > 0)
        view = runs[runs["STATE"] == "FAILED"] if only_failed and "STATE" in runs.columns else runs
        st.dataframe(view, width="stretch", hide_index=True)
