"""Cost — dimension breakdown, all-service spend, Cortex, chargeback, storage, apps."""

from __future__ import annotations

import streamlit as st

from lib import session, queries, mart, cost_intel, formulas
from ._common import scope, header


def _df(sql, tier="standard"):
    return session.run(sql, tier=tier, salt=session.refresh_salt())


def render() -> None:
    company, env, days = scope()
    header("Cost", "Warehouse cost is exact; user/role/db/app cost is allocated by query elapsed-time share.")

    use_mart = mart.is_available()
    st.caption("⚡ Reading from pre-aggregated mart." if use_mart
               else "Reading live from ACCOUNT_USAGE. Deploy setup/setup.sql for faster mart-backed reads.")

    t_dim, t_eff, t_svc, t_cortex, t_charge, t_storage, t_cap, t_app = st.tabs(
        ["By dimension", "Efficiency & RCA", "All services", "Cortex AI", "Chargeback",
         "Storage", "Capacity", "Applications"])

    # ---- By dimension (warehouse/user/role/db, mart-first) ----
    with t_dim:
        dim = st.selectbox("Break down by", list(queries.COST_DIMENSIONS.keys()), index=0)
        mart_ok = use_mart and mart.supports_dimension(dim)
        if dim == "Warehouse":
            sql = mart.warehouse_cost_sql(days, company) if mart_ok else queries.warehouse_cost_sql(days, company)
            value_col = "COST_USD"
        else:
            sql = (mart.cost_by_dimension_sql(dim, days, company) if mart_ok
                   else queries.cost_by_dimension_sql(dim, days, company))
            value_col = "ALLOCATED_COST_USD"
        df = _df(sql)
        if df.empty:
            st.info("No cost data in range.")
        else:
            if value_col in df.columns:
                st.bar_chart(df.head(15).set_index(df.columns[0])[value_col])
            st.dataframe(df, use_container_width=True, hide_index=True)
            if dim != "Warehouse":
                st.caption("Allocated estimate — warehouse metering is exact at warehouse-hour grain.")

    # ---- Efficiency & RCA (unit economics + what drove the change) ----
    with t_eff:
        st.subheader("Unit economics")
        eff = _df(cost_intel.efficiency_summary_sql(days, company))
        if eff.empty:
            st.info("No query/metering data in range.")
        else:
            r = eff.iloc[0]
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Cost / query", formulas.fmt_usd(r.get("COST_PER_QUERY_USD", 0)))
            e2.metric("Cost / TB scanned", formulas.fmt_usd(r.get("COST_PER_TB_USD", 0)))
            e3.metric("Avg cache hit", f"{float(r.get('AVG_CACHE_PCT', 0) or 0):.0f}%",
                      help="Higher = more queries served from cache = more efficient.")
            e4.metric("Failed-query waste", formulas.fmt_usd(r.get("FAILED_QUERY_WASTE_USD", 0)),
                      delta=f"{int(r.get('FAILED_QUERIES', 0) or 0)} failed", delta_color="inverse")

        st.subheader("Cost RCA — what changed vs the prior period")
        var = _df(cost_intel.cost_variance_sql(days, company))
        if var.empty:
            st.info("No variance data in range.")
        else:
            if "DELTA_USD" in var.columns and "WAREHOUSE" in var.columns:
                movers = var.head(10).set_index("WAREHOUSE")["DELTA_USD"]
                st.caption("Top movers (Δ vs prior window) — positive = increase")
                st.bar_chart(movers)
            st.dataframe(var, use_container_width=True, hide_index=True)

        st.subheader("Warehouse efficiency")
        we = _df(cost_intel.warehouse_efficiency_sql(days, company))
        if not we.empty:
            st.dataframe(we, use_container_width=True, hide_index=True)
            st.caption("High QUEUE_SECONDS ⇒ undersized; high REMOTE_SPILL_GB ⇒ memory pressure / bad queries. "
                       "See Recommendations for upsize/downsize calls.")

        st.subheader("Automatic clustering cost")
        cl = _df(cost_intel.clustering_cost_sql(days, company))
        if cl.empty:
            st.info("No automatic-clustering activity in range.")
        else:
            if "CLUSTERING_COST_USD" in cl.columns and "TABLE_NAME" in cl.columns:
                st.bar_chart(cl.head(12).set_index("TABLE_NAME")["CLUSTERING_COST_USD"])
            st.dataframe(cl, use_container_width=True, hide_index=True)
            st.caption("High COST_PER_TB_RECLUSTERED ⇒ the cluster key churns a lot (poor key or heavy DML). "
                       "See Recommendations to review/suspend.")

    # ---- All services (the full spend picture, account-level) ----
    with t_svc:
        st.caption("Every credit-consuming service (account-total — METERING_DAILY_HISTORY has no company/object grain).")
        svc = _df(cost_intel.service_cost_sql(days))
        if svc.empty:
            st.info("No service metering in range.")
        else:
            total = float(svc["COST_USD"].sum()) if "COST_USD" in svc.columns else 0
            st.metric("Total account spend (window)", formulas.fmt_usd(total))
            st.bar_chart(svc.set_index("SERVICE")["COST_USD"])
            st.dataframe(svc, use_container_width=True, hide_index=True)

    # ---- Cortex AI + Cortex Code ----
    with t_cortex:
        st.subheader("Cortex functions / models")
        cf = _df(cost_intel.cortex_functions_cost_sql(days))
        if cf.empty:
            st.info("No Cortex function usage in range (or view not available — see VALIDATION §3).")
        else:
            st.bar_chart(cf.head(15).set_index("MODEL_OR_FUNCTION")["COST_USD"])
            st.dataframe(cf, use_container_width=True, hide_index=True)
        st.subheader("Cortex Code (CLI) by user")
        cc = _df(cost_intel.cortex_code_cost_sql(days))
        if cc.empty:
            st.info("No Cortex Code usage in range (or view not available).")
        else:
            st.dataframe(cc, use_container_width=True, hide_index=True)

    # ---- Chargeback (company x dimension, exportable) ----
    with t_charge:
        cb_dim = st.selectbox("Chargeback by", ["Database", "User", "Role"], index=0, key="cb_dim")
        cb = _df(cost_intel.chargeback_sql(days, cb_dim))
        if cb.empty:
            st.info("No chargeback data in range.")
        else:
            if "COMPANY" in cb.columns and "COST_USD" in cb.columns:
                pivot = cb.groupby("COMPANY")["COST_USD"].sum()
                st.bar_chart(pivot)
            st.dataframe(cb, use_container_width=True, hide_index=True)
            st.download_button("⬇ Download chargeback CSV", cb.to_csv(index=False).encode("utf-8"),
                               file_name=f"chargeback_{cb_dim.lower()}_{days}d.csv", mime="text/csv")

    # ---- Storage detail (active / time-travel / failsafe) ----
    with t_storage:
        sd = _df(cost_intel.storage_detail_sql(company))
        if sd.empty:
            st.info("No detailed storage available; falling back to summary.")
            sd = _df(queries.storage_by_database_sql(company))
            if not sd.empty:
                st.dataframe(sd, use_container_width=True, hide_index=True)
        else:
            st.bar_chart(sd.head(15).set_index("DATABASE")["MONTHLY_COST_USD"])
            st.dataframe(sd, use_container_width=True, hide_index=True)
            st.caption("Time-travel and failsafe are often hidden storage cost — see Recommendations.")

    # ---- Capacity / contract burn (ORGANIZATION_USAGE; ORGADMIN) ----
    with t_cap:
        st.caption("Remaining contract capacity + burn. Requires SNOWFLAKE.ORGANIZATION_USAGE (ORGADMIN).")
        cap = _df(cost_intel.capacity_summary_sql())
        if cap.empty:
            st.info("No capacity data — requires ORGANIZATION_USAGE access (ORGADMIN). "
                    "Ask your org admin to grant the role access, then refresh.")
        else:
            r = cap.iloc[0]
            cur = str(r.get("CURRENCY", "USD"))
            remaining = (float(r.get("CAPACITY_BALANCE", 0) or 0) + float(r.get("FREE_BALANCE", 0) or 0)
                         + float(r.get("ROLLOVER_BALANCE", 0) or 0))
            burn = float(r.get("DAILY_BURN", 0) or 0)
            days_left = (remaining / burn) if burn > 0 else 0
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric(f"Remaining capacity ({cur})", f"{remaining:,.0f}")
            cc2.metric("Daily burn", f"{burn:,.0f}")
            cc3.metric("Days to exhaustion", f"{days_left:,.0f}" if burn > 0 else "—",
                       help="Remaining capacity / 30-day avg daily burn.")
            st.dataframe(cap, use_container_width=True, hide_index=True)
            if burn > 0 and days_left < 60:
                st.warning(f"At current burn, capacity exhausts in ~{days_left:,.0f} days — plan budget/contract.")

    # ---- Applications ----
    with t_app:
        ap = _df(queries.application_cost_sql(days, company))
        if ap.empty:
            st.info("No application data in range (SESSIONS has limited retention).")
        else:
            st.bar_chart(ap.head(15).set_index("APPLICATION")["ALLOCATED_COST_USD"])
            st.dataframe(ap, use_container_width=True, hide_index=True)
