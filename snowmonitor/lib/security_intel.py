"""Tier-1 security detections — evidence-first.

Each detection returns (a) an evidence DataFrame of the exact rows/metrics that
triggered it (so a human can see *why* it fired and judge false positives) and
(b) a pure assessor that turns those rows into an Alert with a detail string that
enumerates the offending specifics. The page renders the evidence table next to
the verdict and offers an Acknowledge action (ledger-backed) for false positives.

Detections:
  - Account-takeover pattern: a burst of failed logins for a user, then a success.
  - Single-factor password logins: successful PASSWORD logins with no 2nd factor.
  - Privilege escalation: ACCOUNTADMIN/SECURITYADMIN/SYSADMIN/OWNERSHIP grants.
  - New-IP login: successful login from an IP not seen for that user in baseline.

Pure helpers (grant_severity, takeover_severity, the assess_* functions) are unit
tested; SQL builders are validated for the columns/filters they must contain.
All columns are confirmed against the working queries in lib/queries.py.
"""

from __future__ import annotations

import config
from . import company
from .alerts import Alert, REACTIVE

AU = "SNOWFLAKE.ACCOUNT_USAGE"

# Roles whose grant is a privilege-escalation event.
ADMIN_ROLES = {"ACCOUNTADMIN", "SECURITYADMIN", "SYSADMIN", "ORGADMIN"}

# Detection thresholds (config overrides win).
FAILED_BURST_WARN = int(config.THRESHOLDS.get("failed_login_burst_warn", 5))
NEW_IP_BASELINE_DAYS = 30


def _win(days: int) -> str:
    n = max(1, min(int(days or config.DEFAULT_LOOKBACK_DAYS), config.MAX_LOOKBACK_DAYS))
    return f"DATEADD('day', -{n}, CURRENT_TIMESTAMP())"


def _alert(severity: str, title: str, detail: str, value: str, threshold: str, action: str) -> Alert:
    return Alert(severity, REACTIVE, "Security", title, detail, value, threshold, action)


# --------------------------------------------------------------------------
# SQL builders
# --------------------------------------------------------------------------

def takeover_candidates_sql(days: int, company_name: str, min_failed: int | None = None) -> str:
    """Per user: failed-login burst with a success in the same window (ATO signal)."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col=None, user_col="user_name")
    th = int(min_failed if min_failed is not None else FAILED_BURST_WARN)
    return f"""
    SELECT
        user_name AS USER,
        COUNT_IF(is_success = 'NO') AS FAILED_ATTEMPTS,
        COUNT_IF(is_success = 'YES') AS SUCCESSES,
        COUNT(DISTINCT IFF(is_success = 'NO', client_ip, NULL)) AS FAIL_IPS,
        MIN(IFF(is_success = 'NO', event_timestamp, NULL)) AS FIRST_FAIL,
        MAX(IFF(is_success = 'NO', event_timestamp, NULL)) AS LAST_FAIL,
        MAX(IFF(is_success = 'YES', event_timestamp, NULL)) AS LAST_SUCCESS,
        (MAX(IFF(is_success = 'YES', event_timestamp, NULL))
            > MIN(IFF(is_success = 'NO', event_timestamp, NULL))) AS SUCCEEDED_AFTER,
        MAX(IFF(is_success = 'NO', error_message, NULL)) AS LAST_ERROR
    FROM {AU}.LOGIN_HISTORY
    WHERE event_timestamp >= {_win(days)} {scope}
    GROUP BY user_name
    HAVING COUNT_IF(is_success = 'NO') >= {th}
    ORDER BY SUCCEEDED_AFTER DESC, FAILED_ATTEMPTS DESC
    """


def single_factor_logins_sql(days: int, company_name: str) -> str:
    """Successful PASSWORD logins with no second factor — MFA actually bypassed."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col=None, user_col="user_name")
    return f"""
    SELECT
        user_name AS USER,
        COUNT(*) AS LOGINS,
        COUNT(DISTINCT client_ip) AS DISTINCT_IPS,
        MAX(event_timestamp) AS LAST_TIME,
        MAX(reported_client_type) AS CLIENT
    FROM {AU}.LOGIN_HISTORY
    WHERE event_timestamp >= {_win(days)} AND is_success = 'YES'
      AND first_authentication_factor = 'PASSWORD'
      AND COALESCE(second_authentication_factor, '') = '' {scope}
    GROUP BY user_name
    ORDER BY LOGINS DESC
    """


def privilege_grants_sql(days: int, top: int = 200) -> str:
    """Recent grants (account-wide — grants aren't cleanly company-scoped).

    Severity is weighted in Python via grant_severity(); the page shows every grant
    as evidence but only the admin/ownership ones fire.
    """
    return f"""
    SELECT
        created_on AS GRANTED_ON, privilege AS PRIVILEGE, granted_on AS OBJECT_TYPE,
        name AS OBJECT, granted_to AS GRANTED_TO, grantee_name AS GRANTEE, granted_by AS GRANTED_BY
    FROM {AU}.GRANTS_TO_ROLES
    WHERE created_on >= {_win(days)} AND deleted_on IS NULL
    ORDER BY created_on DESC
    LIMIT {int(top)}
    """


def new_ip_logins_sql(days: int, company_name: str, baseline_days: int | None = None) -> str:
    """Successful logins from a (user, IP) pair not seen in the prior baseline window."""
    scope = company.company_scope_sql(company_name, wh_col=None, db_col=None, user_col="user_name")
    base = int(baseline_days or NEW_IP_BASELINE_DAYS)
    recent_start = _win(days)
    base_start = f"DATEADD('day', -{base + int(days)}, CURRENT_TIMESTAMP())"
    return f"""
    WITH recent_logins AS (
        SELECT user_name, client_ip, MIN(event_timestamp) AS FIRST_SEEN, COUNT(*) AS LOGINS
        FROM {AU}.LOGIN_HISTORY
        WHERE event_timestamp >= {recent_start} AND is_success = 'YES'
          AND client_ip IS NOT NULL {scope}
        GROUP BY user_name, client_ip
    ),
    historical AS (
        SELECT DISTINCT user_name, client_ip
        FROM {AU}.LOGIN_HISTORY
        WHERE event_timestamp >= {base_start} AND event_timestamp < {recent_start}
          AND is_success = 'YES' AND client_ip IS NOT NULL {scope}
    )
    SELECT r.user_name AS USER, r.client_ip AS NEW_IP, r.FIRST_SEEN AS FIRST_SEEN, r.LOGINS AS LOGINS
    FROM recent_logins r
    LEFT JOIN historical h ON h.user_name = r.user_name AND h.client_ip = r.client_ip
    WHERE h.client_ip IS NULL
    ORDER BY r.FIRST_SEEN DESC
    """


# --------------------------------------------------------------------------
# Pure severity helpers
# --------------------------------------------------------------------------

def grant_severity(privilege: object, object_type: object, object_name: object) -> str:
    """High for OWNERSHIP transfers and grants of admin roles; else Low."""
    priv = str(privilege or "").upper()
    otype = str(object_type or "").upper()
    oname = str(object_name or "").upper()
    if priv == "OWNERSHIP":
        return "High"
    if otype == "ROLE" and oname in ADMIN_ROLES:
        return "High"
    return "Low"


def takeover_severity(failed_attempts: object, succeeded_after: object) -> str:
    """High if a success followed the failed burst (possible compromise); else Medium."""
    return "High" if bool(succeeded_after) else "Medium"


def _rows(df) -> list[dict]:
    return [] if df is None or df.empty else df.to_dict("records")


def _short(ts: object) -> str:
    return str(ts)[:16] if ts is not None else "?"


# --------------------------------------------------------------------------
# Assessors: rows -> {fired, severity, alert, count}
# --------------------------------------------------------------------------

def assess_takeover(rows: list[dict]) -> dict:
    rows = list(rows or [])
    if not rows:
        return {"fired": False}
    compromised = [r for r in rows if bool(r.get("SUCCEEDED_AFTER"))]
    sev = "High" if compromised else "Medium"
    head = compromised[0] if compromised else rows[0]
    bits = []
    for r in (compromised or rows)[:3]:
        flag = " then a SUCCESS" if r.get("SUCCEEDED_AFTER") else ""
        bits.append(f"{r.get('USER')}: {int(r.get('FAILED_ATTEMPTS', 0))} fails "
                    f"from {int(r.get('FAIL_IPS', 0))} IP(s){flag}")
    detail = ("Failed-login burst" + (" with a successful login after the failures "
              "(possible account takeover): " if compromised else ": ") + "; ".join(bits)
              + (f". Last error: {head.get('LAST_ERROR')}" if head.get("LAST_ERROR") else "")) + "."
    alert = _alert(sev, "Account-takeover pattern", detail,
                   f"{len(rows)} user(s), {len(compromised)} with success-after",
                   f">= {FAILED_BURST_WARN} fails",
                   "Confirm with the user; if unrecognized, reset credentials and review session activity.")
    return {"fired": True, "severity": sev, "alert": alert, "count": len(rows),
            "compromised": len(compromised)}


def assess_single_factor(rows: list[dict]) -> dict:
    rows = list(rows or [])
    if not rows:
        return {"fired": False}
    bits = [f"{r.get('USER')} ({int(r.get('LOGINS', 0))} login(s), last {_short(r.get('LAST_TIME'))})"
            for r in rows[:3]]
    detail = (f"{len(rows)} user(s) signed in with a password and NO second factor — MFA bypassed: "
              + "; ".join(bits) + ".")
    alert = _alert("Medium", "Single-factor password logins", detail,
                   f"{len(rows)} user(s)", "any single-factor password login",
                   "Enforce MFA/SSO for these users; this is an active gap, not just a config snapshot.")
    return {"fired": True, "severity": "Medium", "alert": alert, "count": len(rows)}


def assess_grants(rows: list[dict]) -> dict:
    rows = list(rows or [])
    flagged = []
    for r in rows:
        sev = grant_severity(r.get("PRIVILEGE"), r.get("OBJECT_TYPE"), r.get("OBJECT"))
        if sev == "High":
            r = {**r, "_SEV": sev}
            flagged.append(r)
    if not flagged:
        return {"fired": False, "all_rows": rows}
    bits = [f"{r.get('PRIVILEGE')} of {r.get('OBJECT')} -> {r.get('GRANTEE')} by {r.get('GRANTED_BY')}"
            for r in flagged[:3]]
    detail = (f"{len(flagged)} privilege-escalation grant(s) (admin role / OWNERSHIP): "
              + "; ".join(bits) + ".")
    alert = _alert("High", "Privilege escalation grant", detail,
                   f"{len(flagged)} admin/ownership grant(s)", "any ACCOUNTADMIN/SECURITYADMIN/OWNERSHIP grant",
                   "Verify each grant was approved; revoke unexpected admin or ownership grants.")
    return {"fired": True, "severity": "High", "alert": alert, "count": len(flagged),
            "flagged": flagged, "all_rows": rows}


def assess_new_ip(rows: list[dict]) -> dict:
    rows = list(rows or [])
    if not rows:
        return {"fired": False}
    bits = [f"{r.get('USER')} from {r.get('NEW_IP')} (first {_short(r.get('FIRST_SEEN'))})"
            for r in rows[:3]]
    detail = (f"{len(rows)} login(s) from an IP not seen for that user in the prior "
              f"{NEW_IP_BASELINE_DAYS}d baseline: " + "; ".join(bits) + ".")
    alert = _alert("Medium", "Login from new IP", detail,
                   f"{len(rows)} new (user, IP) pair(s)", f"unseen in prior {NEW_IP_BASELINE_DAYS}d",
                   "Confirm the user/location is expected; investigate if the IP or geography is unusual.")
    return {"fired": True, "severity": "Medium", "alert": alert, "count": len(rows)}
