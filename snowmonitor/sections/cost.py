"""Cost — dimension breakdown, all-service spend, Cortex, chargeback, storage, apps.

Sub-views are lazy: only the selected view runs its queries (no eager st.tabs),
which keeps the page fast under concurrent load.
"""

from __future__ import annotations

import streamlit as st

from lib import session, queries, mart, cost_intel, formulas
from ._common import scope, header, subview, kpi_row, render_table, alt_bar, empty, loading


def _df(sql, tier="standard", quiet=False):
    return session.run(sql, tier=tier, salt=session.refresh_salt(), quiet=quiet)


def render() -> None:
    company, env, days = scope()
    header("Cost", "Warehouse cost is exact; user/role/db/app cost is allocated by query elapsed-time share.")

    use_mart = mart.is_available()
    st.caption("⚡ Reading from pre-aggregated mart." if use_mart
               else "Reading live from ACCOUNT_USAGE. Deploy setup/setup.sql for faster mart-backed reads.")

    view = subview(["By dimension", "Efficiency & RCA", "All services", "Cortex AI",
                    "Chargeback", "Storage", "Capacity", "Applications"], key="cost")

    # ---- By dimension ----
    if view == "By dimension":
        dim = st.selectbox("Break down by", list(queries.COST_DIMENSIONS.keys()), index=0)
        mart_ok = use_mart and mart.supports_dimension(dim)
        if dim == "Warehouse":
            sql = mart.warehouse_cost_sql(days, company) if mart_ok else queries.warehouse_cost_sql(days, company)
            value_col = "COST_USD"
        else:
            sql = (mart.cost_by_dimension_sql(dim, days, company) if mart_ok
                   else queries.cost_by_dimension_sql(dim, days, company))
            value_col = "ALLOCATED_COST_USD"
        with loading("Loading cost breakdown…"):
            df = _df(sql)
        if df.empty:
            empty("No cost data in range.")
        else:
            if value_col in df.columns:
                alt_bar(df, x=df.columns[0], y=value_col, money=True)
            render_table(df)
            if dim != "Warehouse":
                st.caption("Allocated estimate — warehouse metering is exact at warehouse-hour grain.")

    # ---- Efficiency & RCA ----
    elif view == "Efficiency & RCA":
        eff_mart = mart.is_efficiency_available()
        if eff_mart:
            st.caption("⚡ Efficiency metrics from the pre-aggregated mart.")
        st.subheader("Unit economics")
        with loading("Computing unit economics…"):
            eff = _df(mart.efficiency_summary_sql(days, company) if eff_mart
                      else cost_intel.efficiency_summary_sql(days, company))
        if eff.empty:
            empty("No query/metering data in range.")
        else:
            r = eff.iloc[0]
            kpi_row([
                {"label": "Cost / query", "value": formulas.fmt_usd(r.get("COST_PER_QUERY_USD", 0))},
                {"label": "Cost / TB scanned", "value": formulas.fmt_usd(r.get("COST_PER_TB_USD", 0))},
                {"label": "Avg cache hit", "value": f"{float(r.get('AVG_CACHE_PCT', 0) or 0):.0f}%",
                 "help": "Higher = more queries served from cache = more efficient.",
                 "status": "ok" if float(r.get('AVG_CACHE_PCT', 0) or 0) >= 30 else "med"},
                {"label": "Failed-query waste", "value": formulas.fmt_usd(r.get("FAILED_QUERY_WASTE_USD", 0)),
                 "delta": f"{int(r.get('FAILED_QUERIES', 0) or 0)} failed",
                 "status": "high" if float(r.get("FAILED_QUERY_WASTE_USD", 0) or 0) > 0 else "ok"},
            ])

        st.subheader("Cost RCA — what changed vs the prior period")
        var = _df(cost_intel.cost_variance_sql(days, company))
        if var.empty:
            empty("No variance data in range.")
        else:
            if "DELTA_USD" in var.columns and "WAREHOUSE" in var.columns:
                st.caption("Top movers (Δ vs prior window) — positive = increase")
                alt_bar(var, x="WAREHOUSE", y="DELTA_USD", money=True, top=10)
            render_table(var)

        st.subheader("Warehouse efficiency")
        we = _df(mart.warehouse_efficiency_sql(days, company) if eff_mart
                 else cost_intel.warehouse_efficiency_sql(days, company))
        if not we.empty:
            render_table(we)
            st.caption("High Queue Seconds ⇒ undersized; high Remote Spill GB ⇒ memory pressure / bad queries. "
                       "See Recommendations for upsize/downsize calls.")

        st.subheader("Automatic clustering cost")
        cl = _df(cost_intel.clustering_cost_sql(days, company))
        if cl.empty:
            empty("No automatic-clustering activity in range.")
        else:
            if "CLUSTERING_COST_USD" in cl.columns and "TABLE_NAME" in cl.columns:
                alt_bar(cl, x="TABLE_NAME", y="CLUSTERING_COST_USD", money=True, top=12)
            render_table(cl)
            st.caption("High Cost Per TB Reclustered ⇒ the cluster key churns a lot (poor key or heavy DML). "
                       "See Recommendations to review/suspend.")

    # ---- All services ----
    elif view == "All services":
        st.caption("Every credit-consuming service (account-total — METERING_DAILY_HISTORY has no company/object grain).")
        svc = _df(cost_intel.service_cost_sql(days))
        if svc.empty:
            empty("No service metering in range.")
        else:
            total = float(svc["COST_USD"].sum()) if "COST_USD" in svc.columns else 0
            kpi_row([{"label": "Total account spend (window)", "value": formulas.fmt_usd(total)}])
            alt_bar(svc, x="SERVICE", y="COST_USD", money=True, top=20)
            render_table(svc)

    # ---- Cortex AI ----
    elif view == "Cortex AI":
        st.subheader("Cortex functions / models")
        cf = _df(cost_intel.cortex_functions_cost_sql(days), quiet=True)
        if cf.empty:
            empty("No Cortex function usage in range (or view not available — see VALIDATION §3).")
        else:
            alt_bar(cf, x="MODEL_OR_FUNCTION", y="COST_USD", money=True, top=15)
            render_table(cf)
        st.subheader("Cortex Code (CLI) by user")
        cc = _df(cost_intel.cortex_code_cost_sql(days), quiet=True)
        if cc.empty:
            empty("No Cortex Code usage in range — this is a newer ACCOUNT_USAGE view whose "
                  "columns vary by account/region; it degrades silently if unavailable. See VALIDATION §3/§8.")
        else:
            render_table(cc)

    # ---- Chargeback ----
    elif view == "Chargeback":
        cb_dim = st.selectbox("Chargeback by", ["Database", "User", "Role"], index=0, key="cb_dim")
        cb = _df(cost_intel.chargeback_sql(days, cb_dim))
        if cb.empty:
            empty("No chargeback data in range.")
        else:
            if "COMPANY" in cb.columns and "COST_USD" in cb.columns:
                pivot = cb.groupby("COMPANY", as_index=False)["COST_USD"].sum()
                alt_bar(pivot, x="COMPANY", y="COST_USD", money=True)
            render_table(cb)
            st.download_button("⬇ Download chargeback CSV", cb.to_csv(index=False).encode("utf-8"),
                               file_name=f"chargeback_{cb_dim.lower()}_{days}d.csv", mime="text/csv")

    # ---- Storage ----
    elif view == "Storage":
        sd = _df(cost_intel.storage_detail_sql(company))
        if sd.empty:
            empty("No detailed storage available; falling back to summary.")
            sd = _df(queries.storage_by_database_sql(company))
            if not sd.empty:
                render_table(sd)
        else:
            if "MONTHLY_COST_USD" in sd.columns and "DATABASE" in sd.columns:
                alt_bar(sd, x="DATABASE", y="MONTHLY_COST_USD", money=True, top=15)
            render_table(sd)
            st.caption("Time-travel and failsafe are often hidden storage cost — see Recommendations.")

    # ---- Capacity ----
    elif view == "Capacity":
        st.caption("Remaining contract capacity + burn. Requires SNOWFLAKE.ORGANIZATION_USAGE (ORGADMIN).")
        cap = _df(cost_intel.capacity_summary_sql(), quiet=True)
        if cap.empty:
            empty("No capacity data — requires ORGANIZATION_USAGE access (ORGADMIN). "
                  "Ask your org admin to grant the role access, then refresh.")
        else:
            r = cap.iloc[0]
            cur = str(r.get("CURRENCY", "USD"))
            remaining = (float(r.get("CAPACITY_BALANCE", 0) or 0) + float(r.get("FREE_BALANCE", 0) or 0)
                         + float(r.get("ROLLOVER_BALANCE", 0) or 0))
            burn = float(r.get("DAILY_BURN", 0) or 0)
            days_left = (remaining / burn) if burn > 0 else 0
            kpi_row([
                {"label": f"Remaining capacity ({cur})", "value": f"{remaining:,.0f}"},
                {"label": "Daily burn", "value": f"{burn:,.0f}"},
                {"label": "Days to exhaustion", "value": (f"{days_left:,.0f}" if burn > 0 else "—"),
                 "status": "crit" if (burn > 0 and days_left < 30) else ("high" if (burn > 0 and days_left < 60) else "ok"),
                 "help": "Remaining capacity / 30-day avg daily burn."},
            ])
            render_table(cap)
            if burn > 0 and days_left < 60:
                st.warning(f"At current burn, capacity exhausts in ~{days_left:,.0f} days — plan budget/contract.")

    # ---- Applications ----
    elif view == "Applications":
        ap = _df(queries.application_cost_sql(days, company))
        if ap.empty:
            empty("No application data in range (SESSIONS has limited retention).")
        else:
            alt_bar(ap, x="APPLICATION", y="ALLOCATED_COST_USD", money=True, top=15)
            render_table(ap)
