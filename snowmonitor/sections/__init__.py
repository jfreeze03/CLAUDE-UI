"""Page registry and dispatch."""

from __future__ import annotations

import streamlit as st

from . import (overview, cost, tasks, security, alerts as alerts_page,
               controls, recommendations, explorer, digest, optimize)

# Grouped navigation — mirrors how a DBA works: monitor -> investigate -> act -> report.
NAV_GROUPS = [
    ("Monitor", ["Command Center", "Alerts"]),
    ("Investigate", ["Cost", "Task Graphs", "Security", "Query Explorer"]),
    ("Act", ["Recommendations", "Optimization", "Controls"]),
    ("Report", ["Digest"]),
]

PAGES = [p for _g, items in NAV_GROUPS for p in items]

_RENDER = {
    "Command Center": overview.render,
    "Overview": overview.render,  # back-compat alias
    "Cost": cost.render,
    "Recommendations": recommendations.render,
    "Optimization": optimize.render,
    "Query Explorer": explorer.render,
    "Digest": digest.render,
    "Task Graphs": tasks.render,
    "Security": security.render,
    "Alerts": alerts_page.render,
    "Controls": controls.render,
}


def render(page: str) -> None:
    fn = _RENDER.get(page, overview.render)
    try:
        fn()
    except Exception as exc:  # never crash the whole app on one page
        st.error(f"{page} couldn't load. The rest of the app is unaffected.")
        with st.expander("Technical details"):
            st.caption(str(exc)[:400])
        try:
            from lib import observability
            observability.log_error(page, exc)
        except Exception:
            pass
