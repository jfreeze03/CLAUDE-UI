"""Cost — breakdown by warehouse, database, user, role, application; storage."""

from __future__ import annotations

import streamlit as st

from lib import session, queries, mart
from ._common import scope, header


def render() -> None:
    company, env, days = scope()
    header("Cost", "Warehouse cost is exact; user/role/db/app cost is allocated by query elapsed-time share.")

    use_mart = mart.is_available()
    st.caption("⚡ Reading from pre-aggregated mart." if use_mart
               else "Reading live from ACCOUNT_USAGE. Deploy setup/setup.sql for faster mart-backed reads.")

    tab_dim, tab_app, tab_storage = st.tabs(["By dimension", "By application", "Storage"])

    with tab_dim:
        dim = st.selectbox("Break down by", list(queries.COST_DIMENSIONS.keys()), index=0)
        mart_ok = use_mart and mart.supports_dimension(dim)
        if dim == "Warehouse":
            sql = mart.warehouse_cost_sql(days, company) if mart_ok else queries.warehouse_cost_sql(days, company)
            df = session.run(sql, tier="standard", salt=session.refresh_salt())
            value_col = "COST_USD"
        else:
            sql = (mart.cost_by_dimension_sql(dim, days, company) if mart_ok
                   else queries.cost_by_dimension_sql(dim, days, company))
            df = session.run(sql, tier="standard", salt=session.refresh_salt())
            value_col = "ALLOCATED_COST_USD"
        if df.empty:
            st.info("No cost data in range.")
        else:
            label_col = df.columns[0]
            top = df.head(15).set_index(label_col)
            if value_col in top.columns:
                st.bar_chart(top[value_col])
            st.dataframe(df, use_container_width=True, hide_index=True)
            if dim != "Warehouse":
                st.caption("Allocated estimate — not exact billing. Warehouse metering is exact at warehouse-hour grain.")

    with tab_app:
        df = session.run(queries.application_cost_sql(days, company), tier="standard", salt=session.refresh_salt())
        if df.empty:
            st.info("No application data in range (requires SESSIONS visibility).")
        else:
            st.bar_chart(df.head(15).set_index("APPLICATION")["ALLOCATED_COST_USD"])
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_storage:
        df = session.run(queries.storage_by_database_sql(company), tier="standard", salt=session.refresh_salt())
        if df.empty:
            st.info("No storage data available.")
        else:
            st.bar_chart(df.head(15).set_index("DATABASE")["MONTHLY_COST_USD"])
            st.dataframe(df, use_container_width=True, hide_index=True)
