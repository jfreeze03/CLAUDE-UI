"""Recommendations — ranked cost-saving actions with estimated $ impact + fix SQL."""

from __future__ import annotations

import streamlit as st

from lib import session, recommend, formulas, cost_intel, mart
from ._common import scope, header, SEVERITY_EMOJI


def _rows(df):
    return [] if df is None or df.empty else df.to_dict("records")


def render() -> None:
    company, env, days = scope()
    header("Recommendations", "Ranked actions to cut spend, with estimated monthly savings and the SQL to apply them.")
    st.caption("Savings are conservative estimates. Review each fix against your workload before applying.")

    salt = session.refresh_salt()
    fast = mart.is_efficiency_available()
    if fast:
        st.caption("⚡ Warehouse/query signals from the pre-aggregated mart (fast).")
    idle_sql = mart.idle_signal_sql(days, company) if fast else recommend.idle_warehouse_signal_sql(days)
    rq_sql = mart.repeated_query_signal_sql(days) if fast else recommend.repeated_query_signal_sql(days)
    we_sql = mart.warehouse_efficiency_sql(days, company) if fast else cost_intel.warehouse_efficiency_sql(days, company)
    idle_df = session.run(idle_sql, tier="standard", salt=salt)
    tt_df = session.run(recommend.time_travel_signal_sql(), tier="standard", salt=salt)
    rq_df = session.run(rq_sql, tier="standard", salt=salt)
    we_df = session.run(we_sql, tier="standard", salt=salt)
    cl_df = session.run(cost_intel.clustering_cost_sql(days, company), tier="standard", salt=salt)

    recs = recommend.rank(
        recommend.idle_warehouse_recs(_rows(idle_df), window_days=days),
        recommend.time_travel_recs(_rows(tt_df)),
        recommend.repeated_query_recs(_rows(rq_df)),
        recommend.warehouse_sizing_recs(_rows(we_df), days),
        recommend.clustering_recs(_rows(cl_df), days),
    )
    total = recommend.total_savings(recs)

    c1, c2, c3 = st.columns(3)
    c1.metric("Recommendations", len(recs))
    c2.metric("Est. monthly savings", formulas.fmt_usd(total))
    c3.metric("Est. annual savings", formulas.fmt_usd(total * 12))

    if not recs:
        st.success("No cost-saving opportunities detected above threshold. (Needs warehouse/storage/query history.)")
        return

    by_cat = {}
    for r in recs:
        by_cat.setdefault(r.category, []).append(r)

    st.divider()
    for r in recs:
        with st.container(border=True):
            top = st.columns([5, 1])
            with top[0]:
                st.markdown(f"{SEVERITY_EMOJI.get(r.severity,'')} **{r.title}** · _{r.category}_")
                st.write(r.detail)
            with top[1]:
                st.metric("Save/mo", formulas.fmt_usd(r.monthly_savings_usd))
            with st.expander("Fix SQL"):
                st.code(r.fix_sql, language="sql")

    st.divider()
    st.caption("By category: " + " · ".join(f"{k} {formulas.fmt_usd(recommend.total_savings(v))}/mo"
                                            for k, v in by_cat.items()))
