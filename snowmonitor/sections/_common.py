"""Shared helpers for pages."""

from __future__ import annotations

import streamlit as st

import config


def scope() -> tuple[str, str, int]:
    return (
        st.session_state.get("company", config.DEFAULT_COMPANY),
        st.session_state.get("environment", config.DEFAULT_ENVIRONMENT),
        int(st.session_state.get("days", config.DEFAULT_LOOKBACK_DAYS)),
    )


def header(title: str, subtitle: str = "") -> None:
    company, env, days = scope()
    st.header(title)
    if subtitle:
        st.caption(subtitle)
    st.caption(f"**{company}** · {env} · {days}d  ·  {config.ACCOUNT_USAGE_FRESHNESS}")


SEVERITY_EMOJI = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "⚪"}
