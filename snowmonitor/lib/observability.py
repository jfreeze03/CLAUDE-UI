"""App self-observability + access control.

- log_error: records a page render failure to APP_LOG so failures are visible
  off-box instead of silently swallowed (the lesson from the prior tool).
- access helpers: optional role-based gating and per-role company lock.
All best-effort: if APP_LOG is absent, logging no-ops; if no roles are configured,
access is open (governed by who can run the app).
"""

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
    """True if the role may view the app (open when no allow-list configured)."""
    allowed = tuple(r.upper() for r in config.ALLOWED_VIEWER_ROLES)
    if not allowed:
        return True
    return str(role or "").upper() in allowed


def company_lock(role: str) -> str | None:
    """Return the single company a role is locked to, or None (free choice)."""
    locks = {k.upper(): v for k, v in config.ROLE_COMPANY_LOCK.items()}
    return locks.get(str(role or "").upper())


def log_error(page: str, exc: BaseException) -> None:
    """Best-effort write of a render failure to APP_LOG."""
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
