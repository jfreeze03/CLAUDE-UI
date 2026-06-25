"""Unified issue feed — one ranked list of everything that needs a DBA's attention.

Merges the proactive/reactive alert engine (cost/perf/tasks/security thresholds),
mart-based spend anomalies, and the Tier-1 security detections into a single
severity-ranked list of `Alert` objects. The Command Center and Alerts page both
render from this, so "what's wrong" is computed once, consistently.

rank() and counts() are pure and unit-tested; gather_issues() does the I/O.
"""

from __future__ import annotations

from . import alerts as engine, metrics, security_intel, mart, anomaly, session

_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def rank(issues: list) -> list:
    return sorted(issues, key=lambda a: (_RANK.get(a.severity, 9), a.domain, a.title))


def counts(issues: list) -> dict:
    out = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "total": len(issues)}
    for a in issues:
        out[a.severity] = out.get(a.severity, 0) + 1
    return out


def _spend_anomaly_alerts(company: str, days: int) -> list:
    if not mart.is_available():
        return []  # per-day baseline needs the mart's daily grain
    df = session.run(mart.warehouse_daily_for_anomaly_sql(days, company),
                     tier="standard", salt=session.refresh_salt(), quiet=True)
    if df is None or df.empty:
        return []
    found = anomaly.detect_anomalies(df, "WAREHOUSE", "COST_USD", "USAGE_DATE")
    return anomaly.to_alerts(found, "Cost", "Warehouse spend")


def _security_alerts(company: str, days: int) -> list:
    salt = session.refresh_salt()
    checks = [
        (security_intel.takeover_candidates_sql(days, company), security_intel.assess_takeover),
        (security_intel.single_factor_logins_sql(days, company), security_intel.assess_single_factor),
        (security_intel.privilege_grants_sql(days), security_intel.assess_grants),
        (security_intel.new_ip_logins_sql(days, company), security_intel.assess_new_ip),
    ]
    out = []
    for sql, assess in checks:
        df = session.run(sql, tier="standard", salt=salt, quiet=True)
        rows = [] if df is None or df.empty else df.to_dict("records")
        a = assess(rows)
        if a.get("fired"):
            out.append(a["alert"])
    return out


def gather_issues(company: str, days: int, metrics_dict: dict | None = None) -> list:
    """Every open issue across cost, performance, tasks, and security — ranked."""
    m = metrics_dict if metrics_dict is not None else metrics.gather(company, days)
    issues = list(engine.evaluate(m))
    issues += _spend_anomaly_alerts(company, days)
    issues += _security_alerts(company, days)
    return rank(issues)
