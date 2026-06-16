"""SnowMonitor — Snowflake usage, cost, task, security monitoring + guarded controls.

Entry point: global scope (company / environment / window) in the sidebar; ALFA is
the default company. Each page is a focused module under sections/.
"""

from __future__ import annotations

import streamlit as st

from lib import compat  # noqa: F401  -- applies Streamlit version shims; must be first
import config
from lib import session, observability

st.set_page_config(page_title=f"{config.APP_NAME}", page_icon="❄", layout="wide")

import sections  # noqa: E402


def _init_state() -> None:
    st.session_state.setdefault("company", config.DEFAULT_COMPANY)        # ALFA default
    st.session_state.setdefault("environment", config.DEFAULT_ENVIRONMENT)
    st.session_state.setdefault("days", config.DEFAULT_LOOKBACK_DAYS)
    st.session_state.setdefault("page", "Overview")


def _apply_access() -> None:
    """Optional role-based access gate + per-role company lock. Fails open."""
    try:
        role = observability.current_role()
        st.session_state["_role"] = role
        st.session_state["_user"] = observability.current_user()
    except Exception:
        return
    if role and not observability.access_allowed(role):
        st.error(f"Role {role} is not permitted to view {config.APP_NAME}.")
        st.stop()
    locked = observability.company_lock(role)
    st.session_state["_company_lock"] = locked
    if locked:
        st.session_state["company"] = locked


def _sidebar() -> None:
    with st.sidebar:
        st.title(f"❄ {config.APP_NAME}")
        st.caption(f"v{config.APP_VERSION} · Snowflake monitoring")
        st.divider()
        st.subheader("Scope")

        companies = list(config.COMPANIES.keys()) + ["ALL"]
        locked = st.session_state.get("_company_lock")
        if locked:
            st.selectbox("Company", [locked], index=0, disabled=True, help="Your role is restricted to this company.")
            st.session_state["company"] = locked
        else:
            st.selectbox(
                "Company", companies,
                index=companies.index(st.session_state["company"]),
                key="company",
                help="ALFA is the default. Trexis = its dedicated warehouses / TRXS_ databases & users. 'ALL' shows both.",
            )
        st.selectbox("Environment", list(config.ENVIRONMENTS),
                     index=list(config.ENVIRONMENTS).index(st.session_state["environment"]), key="environment")
        st.slider("Lookback (days)", 1, config.MAX_LOOKBACK_DAYS, key="days")

        if st.button("↻ Refresh data", use_container_width=True):
            session.bump_refresh()
            st.rerun()

        st.divider()
        st.subheader("View")
        for page in sections.PAGES:
            is_active = st.session_state["page"] == page
            if st.button(page, key=f"nav_{page}", use_container_width=True,
                         type="primary" if is_active else "secondary"):
                st.session_state["page"] = page
                st.rerun()

        st.divider()
        company = st.session_state["company"]
        color = config.COMPANIES.get(company, {}).get("color", "#94a3b8")
        st.markdown(
            f"<div style='font-size:0.8rem'>Active: <b style='color:{color}'>{company}</b> · "
            f"{st.session_state['environment']} · {st.session_state['days']}d</div>",
            unsafe_allow_html=True,
        )
        st.caption(config.ACCOUNT_USAGE_FRESHNESS)


def main() -> None:
    _init_state()
    _apply_access()
    _sidebar()
    sections.render(st.session_state["page"])


main()
