"""App self-observability + access control. log_error records render failures to
APP_LOG; access helpers provide optional role gating and per-role company lock.
All best-effort: absent table => logging no-ops; no roles configured => access open."""

from __future__ import annotations

import streamlit as st

import config
from .session import get_session, run

APP_LOG = config.monitoring_fqn(config.APP_LOG_TABLE)


@st.cache_data(ttl=600, show_spinner=False)
def current_role() -> str:
    df = run("SELECT CURRENT_ROLE() AS R", tier="metadata")
    try:
        return str(df.iloc[0]["R"]).upper() if not df.empty else ""
    except Exception:
        return ""


@st.cache_data(ttl=600, show_spinner=False)
def current_user() -> str:
    df = run("SELECT CURRENT_USER() AS U", tier="metadata")
    try:
        return str(df.iloc[0]["U"]) if not df.empty else "unknown"
    except Exception:
        return "unknown"


def access_allowed(role: str) -> bool:
    allowed = tuple(r.upper() for r in config.ALLOWED_VIEWER_ROLES)
    if not allowed:
        return True
    return str(role or "").upper() in allowed


def company_lock(role: str) -> str | None:
    locks = {k.upper(): v for k, v in config.ROLE_COMPANY_LOCK.items()}
    return locks.get(str(role or "").upper())


def operator_allowed(role: str) -> bool:
    """True if the current role may EXECUTE controls (state-changing actions)."""
    ops = tuple(r.upper() for r in config.CONTROLS_OPERATOR_ROLES)
    return bool(config.CONTROLS_ENABLED and ops and str(role or "").upper() in ops)


def log_error(page: str, exc: BaseException) -> None:
    try:
        role = st.session_state.get("_role", "")
        user = st.session_state.get("_user", "")
        msg = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:1000].replace("'", "''")
        get_session().sql(
            f"""INSERT INTO {APP_LOG} (SF_USER, SF_ROLE, PAGE, EVENT_TYPE, MESSAGE)
                VALUES ('{user}', '{role}', '{page}', 'ERROR', '{msg}')"""
        ).collect()
    except Exception:
        pass
