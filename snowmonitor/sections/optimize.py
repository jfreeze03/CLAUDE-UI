"""Optimization — triage slow/inefficient stored procs and queries, with fixes.

Stored procedures: runtime, p95, SLA impact (total minutes), and degradation vs the
prior window (the "runtime crept up since Informatica" tracker).
Query triage: the heaviest individual statements (incl. those inside procs) ranked by
a badness score, each with rule-based optimization findings and an on-demand AI
(Cortex) suggestion. Sub-views are lazy — only the selected one runs queries.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import session, sp_intel, optimize, anomaly
from ._common import scope, header, subview, kpi_row, render_table, empty, loading

_SEV = {"High": "🔴", "Medium": "🟡", "Low": "⚪"}


def _df(sql):
    return session.run(sql, tier="standard", salt=session.refresh_salt())


def render() -> None:
    company, env, days = scope()
    header("Optimization", "Triage slow/inefficient stored procedures and queries — with the fix for each.")

    view = subview(["Stored procedures", "Query triage"], key="opt")

    # ================= Stored procedures =================
    if view == "Stored procedures":
        with loading("Loading stored-procedure performance…"):
            perf = _df(sp_intel.sp_performance_sql(days, company))
        if perf.empty:
            empty("No stored-procedure CALLs in range.")
        else:
            total_min = float(perf["TOTAL_MINUTES"].sum()) if "TOTAL_MINUTES" in perf.columns else 0
            kpi_row([
                {"label": "Stored procs", "value": len(perf)},
                {"label": "Total SP runtime", "value": f"{total_min:,.0f} min"},
                {"label": "Slowest p95", "value": (f"{float(perf['P95_SEC'].max()):,.0f}s"
                                                    if "P95_SEC" in perf.columns else "—")},
            ])
            st.caption("Ranked by total minutes = the biggest SLA impact (frequency × duration).")
            render_table(perf)

        st.subheader("Runtime degradation (vs prior period)")
        deg = _df(sp_intel.sp_degradation_sql(days, company))
        if deg.empty:
            empty("Not enough history to compare windows.")
        else:
            worse = deg[deg["PCT_CHANGE"] > 0] if "PCT_CHANGE" in deg.columns else deg
            if worse.empty:
                st.success("No stored procedure is slower than the prior period. 🎉")
            else:
                st.warning(f"{len(worse)} stored proc(s) are running slower than the prior period:")
                if "PROC" in worse.columns and "PCT_CHANGE" in worse.columns:
                    st.bar_chart(worse.head(12).set_index("PROC")["PCT_CHANGE"])
                render_table(worse)

        st.subheader("Duration anomalies")
        dd = _df(sp_intel.sp_duration_daily_sql(days, company))
        anoms = anomaly.detect_anomalies(dd, "TASK", "AVG_DURATION_SEC", "USAGE_DATE",
                                         min_abs=10, min_baseline_days=4) if not dd.empty else []
        if anoms:
            render_table(pd.DataFrame(anoms)[["entity", "latest", "baseline_mean", "pct_above_mean"]]
                         .rename(columns={"entity": "TASK", "latest": "LATEST_SEC",
                                          "baseline_mean": "BASELINE_SEC", "pct_above_mean": "PCT_ABOVE"}))
        else:
            st.caption("No proc ran notably slower than its own baseline.")

    # ================= Query triage =================
    elif view == "Query triage":
        st.caption("Heaviest statements (spill / big scans / poor pruning) — usually the slow part inside a proc.")
        with loading("Finding heavy queries…"):
            hq = _df(sp_intel.heavy_query_sql(days, company))
        if hq.empty:
            st.success("No heavy queries (spill / large scans / long runtime) in range.")
            return

        rows = hq.to_dict("records")
        for r in rows:
            r["_SCORE"] = optimize.triage_score(r)
        rows.sort(key=lambda r: r["_SCORE"], reverse=True)

        st.caption(f"{len(rows)} optimization candidates. Showing top 20 by badness score.")
        for r in rows[:20]:
            findings = optimize.optimization_findings(r)
            top_sev = findings[0]["severity"]
            qid = str(r.get("QUERY_ID", ""))
            with st.container(border=True):
                head = st.columns([5, 1])
                with head[0]:
                    st.markdown(f"{_SEV.get(top_sev,'')} **{findings[0]['issue']}** · "
                                f"{optimize._num(r.get('DURATION_SEC')):.0f}s · "
                                f"{optimize._num(r.get('GB_SCANNED')):.0f} GB scanned · "
                                f"{optimize._num(r.get('REMOTE_SPILL_GB')):.1f} GB spill · "
                                f"{optimize._num(r.get('PRUNING_PCT')):.0f}% partitions")
                    st.code(str(r.get("QUERY", ""))[:300], language="sql")
                with head[1]:
                    st.metric("Score", f"{optimize._num(r['_SCORE']):.0f}")
                for f in findings:
                    st.markdown(f"- {_SEV.get(f['severity'],'')} **{f['issue']}** — {f['guidance']}")
                if st.button("🤖 AI optimize (Cortex)", key=f"ai_{qid}"):
                    with st.spinner("Asking Cortex…"):
                        res = session.run(optimize.cortex_optimize_sql(r.get("QUERY", ""), r),
                                          tier="live", salt=qid)
                    if res is not None and not res.empty and "SUGGESTION" in res.columns:
                        st.markdown(str(res.iloc[0]["SUGGESTION"]))
                    else:
                        st.info("AI suggestion unavailable — needs Cortex access and an allowed model "
                                "(config.CORTEX_OPTIMIZE_MODEL). See Controls → Cortex.")
