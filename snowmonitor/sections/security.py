"""Security — failed logins, users without MFA, recent privilege grants."""

from __future__ import annotations

import streamlit as st

from lib import session, queries
from ._common import scope, header


def render() -> None:
    company, env, days = scope()
    header("Security", "Authentication, MFA coverage, and grant activity.")

    logins = session.run(queries.failed_logins_sql(days, company), tier="standard", salt=session.refresh_salt())
    mfa = session.run(queries.users_without_mfa_sql(company), tier="standard", salt=session.refresh_salt())
    grants = session.run(queries.recent_grants_sql(days, company), tier="standard", salt=session.refresh_salt())

    c1, c2, c3 = st.columns(3)
    c1.metric("Failed logins", 0 if logins.empty else len(logins))
    c2.metric("Users without MFA", 0 if mfa.empty else len(mfa),
              delta=None if mfa.empty else "review", delta_color="inverse")
    c3.metric("Recent grants", 0 if grants.empty else len(grants))

    st.subheader("Users without MFA")
    if mfa.empty:
        st.success("No password users at MFA risk (SSO / key-pair users excluded).")
    else:
        st.warning("These password users lack MFA and are not using SSO/key-pair. Enforce MFA/SSO.")
        st.dataframe(mfa, use_container_width=True, hide_index=True)

    st.subheader("Failed logins")
    if logins.empty:
        st.success("No failed logins in range.")
    else:
        st.dataframe(logins, use_container_width=True, hide_index=True)

    st.subheader("Recent privilege grants")
    if grants.empty:
        st.info("No grants in range.")
    else:
        st.dataframe(grants, use_container_width=True, hide_index=True)
