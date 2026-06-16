"""Page registry and dispatch."""

from __future__ import annotations

import streamlit as st

from . import overview, cost, tasks, security, alerts as alerts_page

PAGES = ["Overview", "Cost", "Task Graphs", "Security", "Alerts"]

_RENDER = {
    "Overview": overview.render,
    "Cost": cost.render,
    "Task Graphs": tasks.render,
    "Security": security.render,
    "Alerts": alerts_page.render,
}


def render(page: str) -> None:
    fn = _RENDER.get(page, overview.render)
    try:
        fn()
    except Exception as exc:  # never crash the whole app on one page
        st.error(f"{page} could not render.")
        st.caption(str(exc)[:300])
        # Record the failure off-box so it's observable, not silently swallowed.
        try:
            from lib import observability
            observability.log_error(page, exc)
        except Exception:
            pass
