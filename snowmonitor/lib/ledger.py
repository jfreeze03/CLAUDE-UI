"""Alert ledger — gives alerts memory. Upserts each fired alert (one row per stable
alert_key with run_count + first/last seen), supports acknowledgment, reads history.
All writes best-effort: absent table => app runs without history."""

from __future__ import annotations

import hashlib

import pandas as pd

import config
from .session import get_session, run

LEDGER = config.monitoring_fqn(config.ALERT_LEDGER_TABLE)


def alert_key(domain: str, title: str, company: str) -> str:
    raw = f"{company}|{domain}|{title}".upper()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _esc(v: object) -> str:
    return "'" + str(v if v is not None else "").replace("'", "''")[:1000] + "'"


def record(alerts: list, company: str) -> bool:
    if not alerts:
        return True
    rows = []
    for a in alerts:
        k = alert_key(a.domain, a.title, company)
        rows.append(
            f"({_esc(k)}, {_esc(a.severity)}, {_esc(a.kind)}, {_esc(a.domain)}, "
            f"{_esc(a.title)}, {_esc(a.detail)}, {_esc(company)})"
        )
    values = ",\n".join(rows)
    sql = f"""
    MERGE INTO {LEDGER} t
    USING (
        SELECT column1 AS alert_key, column2 AS severity, column3 AS kind, column4 AS domain,
               column5 AS title, column6 AS detail, column7 AS company
        FROM VALUES {values}
    ) s
    ON t.alert_key = s.alert_key
    WHEN MATCHED THEN UPDATE SET
        last_seen = CURRENT_TIMESTAMP(), run_count = t.run_count + 1,
        severity = s.severity, detail = s.detail,
        status = IFF(t.status = 'ACK', 'ACK', 'OPEN')
    WHEN NOT MATCHED THEN INSERT
        (alert_key, severity, kind, domain, title, detail, company,
         first_seen, last_seen, run_count, status)
        VALUES (s.alert_key, s.severity, s.kind, s.domain, s.title, s.detail, s.company,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 1, 'OPEN')
    """
    try:
        get_session().sql(sql).collect()
        return True
    except Exception:
        return False


def acknowledge(key: str, user: str) -> bool:
    sql = f"""UPDATE {LEDGER}
              SET status = 'ACK', ack_by = {_esc(user)}, ack_at = CURRENT_TIMESTAMP()
              WHERE alert_key = {_esc(key)}"""
    try:
        get_session().sql(sql).collect()
        return True
    except Exception:
        return False


def recent(company: str, limit: int = 100) -> pd.DataFrame:
    scope = "" if str(company).upper() == "ALL" else f"WHERE company = {_esc(company)}"
    return run(
        f"""SELECT title AS TITLE, domain AS DOMAIN, severity AS SEVERITY, kind AS KIND,
                   status AS STATUS, run_count AS RUNS, first_seen AS FIRST_SEEN,
                   last_seen AS LAST_SEEN, ack_by AS ACK_BY, alert_key AS ALERT_KEY
            FROM {LEDGER} {scope}
            ORDER BY last_seen DESC LIMIT {int(limit)}""",
        tier="live",
    )
