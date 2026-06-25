"""SnowMonitor — Snowflake usage, cost, task, security monitoring + guarded controls.

Entry point: global scope (company / window) in the sidebar; ALFA is the default
company. Navigation is grouped Monitor / Investigate / Act / Report. Each page is a
focused module under sections/.
"""

from __future__ import annotations

import streamlit as st

from lib import compat  # noqa: F401  -- applies Streamlit version shims; must be first
import config
from lib import session, observability

st.set_page_config(page_title=f"{config.APP_NAME}", page_icon="❄", layout="wide")

import sections  # noqa: E402
from sections._common import inject_css  # noqa: E402

_PILL = {"Critical": "#ef4444", "High": "#f59e0b", "Medium": "#eab308"}


def _init_state() -> None:
    st.session_state.setdefault("company", config.DEFAULT_COMPANY)        # ALFA default
    st.session_state.setdefault("environment", config.DEFAULT_ENVIRONMENT)  # internal; not user-filterable
    st.session_state.setdefault("days", config.DEFAULT_LOOKBACK_DAYS)
    st.session_state.setdefault("page", "Command Center")


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


def _status_pill() -> None:
    counts = st.session_state.get("_issue_counts")
    if not counts:
        st.caption("Open Command Center for live issue status.")
        return
    parts = []
    for sev in ("Critical", "High", "Medium"):
        n = counts.get(sev, 0)
        if n:
            parts.append(f"<span class='sm-pill' style='background:{_PILL[sev]}22;color:{_PILL[sev]}'>"
                         f"{n} {sev}</span>")
    if not parts:
        st.markdown("<span class='sm-pill' style='background:#22c55e22;color:#22c55e'>All clear</span>",
                    unsafe_allow_html=True)
    else:
        st.markdown(" ".join(parts), unsafe_allow_html=True)


def _sidebar() -> None:
    with st.sidebar:
        st.title(f"❄ {config.APP_NAME}")
        st.caption(f"v{config.APP_VERSION} · Snowflake monitoring")
        st.divider()

        st.subheader("Scope")
        companies = list(config.COMPANIES.keys()) + ["ALL"]
        locked = st.session_state.get("_company_lock")
        if locked:
            st.selectbox("Company", [locked], index=0, disabled=True,
                         help="Your role is restricted to this company.")
            st.session_state["company"] = locked
        else:
            st.selectbox(
                "Company", companies,
                index=companies.index(st.session_state["company"]),
                key="company",
                help="ALFA is the default. Trexis = its dedicated warehouses / TRXS_ databases & users. 'ALL' shows both.",
            )
        st.slider("Lookback (days)", 1, config.MAX_LOOKBACK_DAYS, key="days")
        if st.button("↻ Refresh data", use_container_width=True):
            session.bump_refresh()
            st.rerun()

        st.divider()
        _status_pill()
        st.divider()

        for group, items in sections.NAV_GROUPS:
            st.markdown(f"<div class='sm-navgroup'>{group}</div>", unsafe_allow_html=True)
            for page in items:
                is_active = st.session_state["page"] == page
                if st.button(page, key=f"nav_{page}", use_container_width=True,
                             type="primary" if is_active else "secondary"):
                    st.session_state["page"] = page
                    st.rerun()

        st.divider()
        st.caption(config.ACCOUNT_USAGE_FRESHNESS)


def main() -> None:
    _init_state()
    inject_css()
    _apply_access()
    _sidebar()
    sections.render(st.session_state["page"])


main()

