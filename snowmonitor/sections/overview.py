"""Command Center — triage hero (top issues), KPI strip, spend vs budget trend."""

from __future__ import annotations

import calendar
from datetime import datetime

import pandas as pd
import streamlit as st

from lib import session, queries, metrics, formulas, mart, forecast, issues, ledger, observability
import config
from ._common import (scope, header, kpi_row, issue_card, alt_line, render_table,
                      empty, loading, md_escape)


def _goto(page: str):
    def _f():
        st.session_state["page"] = page
        st.rerun()
    return _f


def render() -> None:
    company, env, days = scope()
    header("Command Center", "What needs your attention right now — then the numbers behind it.")

    with loading("Scanning cost, tasks, and security…"):
        m = metrics.gather(company, days)
        feed = issues.gather_issues(company, days, metrics_dict=m)
    summary = issues.counts(feed)
    st.session_state["_issue_counts"] = summary  # feeds the sidebar status pill
    ledger.record(feed, company)  # best-effort history

    # ---- Triage hero ----
    crit_high = [a for a in feed if a.severity in ("Critical", "High")]
    if not feed:
        st.success("No open issues. Cost, tasks, and security are all within thresholds.")
    elif crit_high:
        st.markdown(f"#### 🔺 {len(crit_high)} issue(s) need attention now")
    else:
        st.markdown(f"#### {summary['total']} lower-severity item(s) to review")

    _DOMAIN_PAGE = {"Cost": "Cost", "Tasks": "Task Graphs", "Security": "Security",
                    "Performance": "Query Explorer"}
    for i, a in enumerate(feed[:3]):
        issue_card(a.severity, a.domain, a.title, a.detail, a.action, key=f"hero_{i}",
                   on_investigate=_goto(_DOMAIN_PAGE.get(a.domain, "Alerts")),
                   investigate_label="Investigate →")
    if summary["total"] > 3:
        if st.button(f"See all {summary['total']} issues →", key="all_issues"):
            _goto("Alerts")()

    st.divider()

    # ---- KPI strip ----
    now = datetime.utcnow()
    dim = calendar.monthrange(now.year, now.month)[1]
    daily_df = session.run(queries.mtd_daily_spend_sql(company), tier="standard", salt=session.refresh_salt())
    daily = list(pd.to_numeric(daily_df["COST_USD"], errors="coerce").fillna(0)) if (
        not daily_df.empty and "COST_USD" in daily_df.columns) else []
    proj = forecast.month_end_projection(m["mtd_spend_usd"], now.day, dim, daily_spends=daily)
    budget = config.THRESHOLDS.get("monthly_budget_usd", 0)
    bstate = forecast.budget_status(proj["projection"], budget, m["mtd_spend_usd"], now.day, dim)

    pct_budget = bstate.get("pct_of_budget", 0) if bstate.get("has_budget") else 0
    budget_status = "crit" if pct_budget >= 100 else ("high" if pct_budget >= 90 else "ok")
    failed_tasks = int(m["failed_task_runs"])
    sec_fired = sum(1 for a in feed if a.domain == "Security")

    kpi_row([
        {"label": "MTD spend", "value": formulas.fmt_usd(m["mtd_spend_usd"]),
         "delta": f"forecast {formulas.fmt_usd(proj['projection'])}", "status": "neutral"},
        {"label": "% of budget", "value": (f"{pct_budget:.0f}%" if bstate.get("has_budget") else "—"),
         "delta": (bstate.get("state") if bstate.get("has_budget") else "no budget set"),
         "status": budget_status, "good": (budget_status == "ok")},
        {"label": "Failed tasks", "value": failed_tasks,
         "status": "high" if failed_tasks else "ok", "good": (failed_tasks == 0)},
        {"label": "Security issues", "value": sec_fired,
         "status": "high" if sec_fired else "ok", "good": (sec_fired == 0)},
        {"label": "Open issues", "value": summary["total"],
         "delta": f"{summary['Critical']} critical · {summary['High']} high",
         "status": "crit" if summary["Critical"] else ("high" if summary["High"] else "ok")},
    ])

    st.divider()

    # ---- Spend vs budget trend ----
    left, right = st.columns([2, 1])
    with left:
        st.subheader("Spend trend & budget")
        use_mart = mart.is_available()
        trend_sql = (mart.daily_spend_sql(max(days, 14), company) if use_mart
                     else queries.daily_spend_sql(max(days, 14), company))
        df = session.run(trend_sql, tier="standard", salt=session.refresh_salt())
        if df.empty or "USAGE_DATE" not in df.columns:
            empty("No spend data in range.")
        else:
            chart_df = df[["USAGE_DATE", "COST_USD"]].copy()
            if budget and dim:
                chart_df["BUDGET_PER_DAY"] = budget / dim
                alt_line(chart_df, "USAGE_DATE", ["COST_USD", "BUDGET_PER_DAY"], money=True)
            else:
                alt_line(chart_df, "USAGE_DATE", ["COST_USD"], money=True)
    with right:
        st.subheader("Top warehouses")
        wh_sql = (mart.warehouse_cost_sql(days, company, top=8) if mart.is_available()
                  else queries.warehouse_cost_sql(days, company, top=8))
        wh = session.run(wh_sql, tier="standard", salt=session.refresh_salt())
        if wh.empty:
            empty("No warehouse metering in range.")
        else:
            render_table(wh, max_rows=8)
