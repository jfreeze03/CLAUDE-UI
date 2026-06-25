"""Controls — guarded state-changing actions: warehouse timeouts and Cortex limits.

Safe by default. When CONTROLS_ENABLED is False (or the current role is not an
operator), this page only *generates* SQL with a rollback statement for you to run
as an operator. When enabled and run as an operator role, actions can be executed
in-app behind a typed confirmation, and each execution writes an audit row.
"""

from __future__ import annotations

import streamlit as st

import config
from lib import controls, queries, session, observability
from ._common import header, subview


def _exec_mode() -> bool:
    role = st.session_state.get("_role", "") or observability.current_role()
    return observability.operator_allowed(role)


def _present_action(action: controls.ControlAction, confirm_token: str, key: str, can_exec: bool) -> None:
    st.write(f"**{action.title}** — {action.summary}")
    st.caption(action.privilege_note)
    st.code(action.sql, language="sql")
    with st.expander("Rollback SQL"):
        st.code(action.rollback_sql, language="sql")

    if not can_exec:
        st.info("Generate-only mode. Copy the SQL above and run it as an operator. "
                "Enable in-app execution via CONTROLS_ENABLED + CONTROLS_OPERATOR_ROLES in config.py.")
        return

    typed = st.text_input(f"Type `{confirm_token}` to enable execution", key=f"confirm_{key}")
    if st.button("Execute", key=f"exec_{key}", type="primary", disabled=(typed != confirm_token)):
        try:
            sess = session.get_session()
            sess.sql(action.sql).collect()
            actor = st.session_state.get("_user", "") or observability.current_user()
            try:
                sess.sql(controls.audit_insert_sql(actor, action)).collect()
            except Exception:
                pass  # audit table may not be deployed; action still succeeded
            st.success("Executed. Rollback SQL is above if you need to revert.")
        except Exception as exc:
            st.error(f"Execution failed: {str(exc)[:300]}")


def render() -> None:
    header("Controls", "State-changing admin actions. Review the SQL and rollback before executing.")
    can_exec = _exec_mode()
    st.warning("These actions change account state. "
               + ("In-app execution is ENABLED for your role — confirm carefully."
                  if can_exec else "Running in generate-only mode (safe)."))

    view = subview(["Warehouse timeouts", "Cortex limits"], key="controls")

    # ---------------- Warehouse timeouts ----------------
    if view == "Warehouse timeouts":
        wh_df = session.run(queries.warehouse_names_sql(), tier="metadata", salt=session.refresh_salt())
        names = list(wh_df["WAREHOUSE"]) if not wh_df.empty and "WAREHOUSE" in wh_df.columns else []
        warehouse = (st.selectbox("Warehouse", names) if names
                     else st.text_input("Warehouse name", value="MONITOR_WH"))

        cur_stmt, cur_queued = None, None
        if warehouse:
            try:
                cur = session.run(controls.warehouse_timeout_current_sql(warehouse), tier="metadata",
                                  salt=session.refresh_salt())
                if not cur.empty:
                    kcol = "KEY" if "KEY" in cur.columns else cur.columns[0]
                    vcol = "VALUE" if "VALUE" in cur.columns else cur.columns[1]
                    params = {str(r[kcol]).upper(): r[vcol] for _, r in cur.iterrows()}
                    cur_stmt = int(float(params.get("STATEMENT_TIMEOUT_IN_SECONDS", 0) or 0))
                    cur_queued = int(float(params.get("STATEMENT_QUEUED_TIMEOUT_IN_SECONDS", 0) or 0))
                    st.caption(f"Current: statement={cur_stmt}s · queued={cur_queued}s")
            except Exception:
                st.caption("Could not read current timeouts (need access to the warehouse).")

        c1, c2 = st.columns(2)
        new_stmt = c1.number_input("Statement timeout (s)", min_value=config.WAREHOUSE_TIMEOUT_MIN_S,
                                   max_value=config.WAREHOUSE_TIMEOUT_MAX_S,
                                   value=int(cur_stmt) if cur_stmt is not None else 3600, step=30)
        set_queued = c2.checkbox("Also set queued timeout")
        new_queued = c2.number_input("Queued timeout (s)", min_value=config.WAREHOUSE_TIMEOUT_MIN_S,
                                     max_value=config.WAREHOUSE_TIMEOUT_MAX_S,
                                     value=int(cur_queued) if cur_queued is not None else 0, step=30,
                                     disabled=not set_queued)

        if warehouse:
            try:
                action = controls.set_warehouse_timeout_action(
                    warehouse, int(new_stmt), int(new_queued) if set_queued else None, cur_stmt, cur_queued)
                st.divider()
                _present_action(action, controls.safe_identifier(warehouse), "wh_timeout", can_exec)
            except ValueError as e:
                st.error(str(e))

    # ---------------- Cortex limits ----------------
    elif view == "Cortex limits":
        st.markdown("**Cortex access** — turn Cortex functions on/off for a role.")
        a1, a2 = st.columns([1, 2])
        act = a1.selectbox("Action", ["GRANT", "REVOKE"])
        role = a2.text_input("Role", value="ANALYST_ROLE")
        if role:
            try:
                action = controls.cortex_access_action(act, role)
                _present_action(action, controls.safe_identifier(role), "cortex_access", can_exec)
            except ValueError as e:
                st.error(str(e))

        st.divider()
        st.markdown("**Cortex model allowlist** — restrict to approved models (cost control).")
        restrict = st.radio("Mode", ["Restrict to models", "Reset (allow all)"], horizontal=True)
        models_text = st.text_input("Models (comma-separated)", value="mistral-large2, llama3.1-8b",
                                    disabled=(restrict != "Restrict to models"))
        try:
            models = [m.strip() for m in models_text.split(",") if m.strip()] if restrict == "Restrict to models" else None
            action = controls.cortex_model_allowlist_action(models)
            _present_action(action, "CORTEX", "cortex_models", can_exec)
        except ValueError as e:
            st.error(str(e))
