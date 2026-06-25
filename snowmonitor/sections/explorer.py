"""Query Explorer — ad-hoc search across query history with rich filters."""

from __future__ import annotations

import streamlit as st

from lib import session, queries
from ._common import scope, header, kpi_row, render_table, empty, loading


def render() -> None:
    company, env, days = scope()
    header("Query Explorer", "Search query history by user, warehouse, status, and duration.")

    c1, c2, c3, c4 = st.columns([2, 2, 1.3, 1.3])
    user_contains = c1.text_input("User contains", value="")
    wh_contains = c2.text_input("Warehouse contains", value="")
    status = c3.selectbox("Status", ["All", "Success", "Failed"], index=0)
    min_seconds = c4.number_input("Min duration (s)", min_value=0.0, value=0.0, step=5.0)

    sql = queries.query_search_sql(days, company, user_contains, wh_contains, status, min_seconds, top=300)
    with loading("Searching query history…"):
        df = session.run(sql, tier="standard", salt=session.refresh_salt())

    if df.empty:
        empty("No queries match these filters in range.")
        return

    cards = [{"label": "Matches (top 300)", "value": len(df)}]
    if "DURATION_SEC" in df.columns:
        cards.append({"label": "Slowest", "value": f"{float(df['DURATION_SEC'].max()):,.0f}s"})
    if "GB_SCANNED" in df.columns:
        cards.append({"label": "Most scanned", "value": f"{float(df['GB_SCANNED'].max()):,.1f} GB"})
    kpi_row(cards)

    render_table(df)
    st.download_button("⬇ Download CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name=f"queries_{days}d.csv", mime="text/csv")
