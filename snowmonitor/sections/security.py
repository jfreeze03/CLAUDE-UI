"""Security — evidence-first threat detections + posture context.

Each Tier-1 detection shows the exact metrics that triggered it (so you can see
*why* it fired and judge false positives) and offers an Acknowledge action that
records + dismisses it in the alert ledger. Posture context (MFA gaps, raw failed
logins, grant log) stays below as reference.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import session, queries, security_intel as sec, ledger
from ._common import scope, header, SEVERITY_EMOJI, md_escape, kpi_row, render_table, empty, loading

_SALT = None


def _df(sql, tier="standard"):
    return session.run(sql, tier=tier, salt=session.refresh_salt())


def _current_user() -> str:
    try:
        u = _df("SELECT CURRENT_USER() AS U", tier="metadata")
        if not u.empty:
            return str(u.iloc[0]["U"])
    except Exception:
        pass
    return str(st.session_state.get("viewer_role", "app-user"))


def _detection(company: str, title: str, assessment: dict, evidence_df, empty_msg: str) -> None:
    """Render one detection: verdict + severity, the evidence metrics, acknowledge."""
    fired = assessment.get("fired")
    with st.container(border=True):
        if not fired:
            st.markdown(f"✅ **{title}** — clear")
            st.caption(empty_msg)
            return
        a = assessment["alert"]
        emoji = SEVERITY_EMOJI.get(a.severity, "")
        st.markdown(f"{emoji} **{md_escape(a.title)}** · {a.severity} · _{a.value}_")
        st.markdown(md_escape(a.detail))
        st.caption(f"Why it fired (the metrics) · threshold {md_escape(a.threshold)}")
        if evidence_df is not None and not evidence_df.empty:
            render_table(evidence_df)
        st.caption(f"Suggested action: {md_escape(a.action)}")
        key = ledger.alert_key(a.domain, a.title, company)
        cols = st.columns([1, 3])
        if cols[0].button("Acknowledge", key=f"ack_{key}",
                          help="Record this and mark it acknowledged (use for confirmed false positives)."):
            ok = ledger.record([a], company) and ledger.acknowledge(key, _current_user())
            if ok:
                cols[1].success("Acknowledged and logged to the alert ledger.")
            else:
                cols[1].info("Ledger not available — deploy setup/setup.sql to enable acknowledge/history.")


def render() -> None:
    company, env, days = scope()
    header("Security", "Evidence-first threat detections, then MFA / login / grant posture.")

    # --- Run all detections ---
    with loading("Running threat detections…"):
        tko_df = _df(sec.takeover_candidates_sql(days, company))
        sf_df = _df(sec.single_factor_logins_sql(days, company))
        grants_df = _df(sec.privilege_grants_sql(days))
        newip_df = _df(sec.new_ip_logins_sql(days, company))

    tko = sec.assess_takeover([] if tko_df.empty else tko_df.to_dict("records"))
    sf = sec.assess_single_factor([] if sf_df.empty else sf_df.to_dict("records"))
    gr = sec.assess_grants([] if grants_df.empty else grants_df.to_dict("records"))
    nip = sec.assess_new_ip([] if newip_df.empty else newip_df.to_dict("records"))

    fired = [x for x in (tko, sf, gr, nip) if x.get("fired")]
    crit = sum(1 for x in fired if x.get("severity") == "High")

    kpi_row([
        {"label": "Detections firing", "value": len(fired),
         "status": "high" if fired else "ok", "good": len(fired) == 0,
         "delta": f"{crit} high" if crit else None},
        {"label": "ATO candidates", "value": tko.get("count", 0) if tko.get("fired") else 0,
         "status": "crit" if tko.get("compromised") else ("high" if tko.get("fired") else "ok"),
         "delta": f"{tko.get('compromised', 0)} w/ success-after" if tko.get("compromised") else None},
        {"label": "Single-factor users", "value": sf.get("count", 0) if sf.get("fired") else 0,
         "status": "med" if sf.get("fired") else "ok"},
    ])

    st.subheader("Threat detections")
    st.caption("Each card shows the metrics that triggered it. Acknowledge dismisses a confirmed false positive.")
    _detection(company, "Account-takeover pattern", tko, tko_df,
               "No user exceeded the failed-login burst threshold.")
    _detection(company, "Single-factor password logins", sf, sf_df,
               "No successful password login without a second factor.")
    flagged_df = pd.DataFrame(gr["flagged"]).drop(columns=["_SEV"], errors="ignore") if gr.get("flagged") else None
    _detection(company, "Privilege escalation grant", gr, flagged_df,
               "No admin-role or OWNERSHIP grants in range.")
    _detection(company, "Login from new IP", nip, newip_df,
               "No logins from IPs unseen in the baseline window.")

    if gr.get("all_rows") and not gr.get("fired"):
        with st.expander(f"All grants in range ({len(gr['all_rows'])}) — none flagged"):
            st.dataframe(grants_df, use_container_width=True, hide_index=True)

    # --- Posture context (reference) ---
    st.divider()
    st.subheader("Posture context")
    mfa = _df(queries.users_without_mfa_sql(company))
    logins = _df(queries.failed_logins_sql(days, company))

    with st.expander(f"Users without MFA ({0 if mfa.empty else len(mfa)})", expanded=not mfa.empty):
        if mfa.empty:
            st.success("No password users at MFA risk (SSO / key-pair users excluded).")
        else:
            st.warning("These password users lack MFA and are not using SSO/key-pair. Enforce MFA/SSO.")
            st.dataframe(mfa, use_container_width=True, hide_index=True)

    with st.expander(f"Failed logins, raw ({0 if logins.empty else len(logins)})"):
        if logins.empty:
            st.success("No failed logins in range.")
        else:
            st.dataframe(logins, use_container_width=True, hide_index=True)
