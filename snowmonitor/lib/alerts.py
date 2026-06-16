"""Alert engine — proactive and reactive.

`evaluate(metrics)` is pure (fully tested): the UI gathers metric values and passes
them in; this returns a ranked list of alerts. PROACTIVE alerts fire before a
budget/SLA is breached; REACTIVE alerts fire on current failures.
`build_alert_object_sql` generates a real Snowflake ALERT object for server-side email.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import config

PROACTIVE = "Proactive"
REACTIVE = "Reactive"
_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


@dataclass(frozen=True)
class Alert:
    severity: str
    kind: str
    domain: str
    title: str
    detail: str
    value: str
    threshold: str
    action: str

    def as_row(self) -> dict:
        return asdict(self)


def _num(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def evaluate(metrics: dict, thresholds: dict | None = None) -> list[Alert]:
    t = {**config.THRESHOLDS, **(thresholds or {})}
    alerts: list[Alert] = []

    mtd = _num(metrics.get("mtd_spend_usd"))
    days_elapsed = max(1.0, _num(metrics.get("days_elapsed")))
    days_in_month = max(days_elapsed, _num(metrics.get("days_in_month")) or 30.0)
    budget = _num(t.get("monthly_budget_usd"))
    if mtd > 0 and budget > 0:
        forecast = mtd / days_elapsed * days_in_month
        pct = forecast / budget * 100.0
        if pct >= t["budget_pacing_warn_pct"]:
            alerts.append(Alert(
                "High" if pct >= 100 else "Medium", PROACTIVE, "Cost", "Budget pacing over threshold",
                f"Forecast month-end spend ${forecast:,.0f} is {pct:.0f}% of the ${budget:,.0f} budget "
                f"(MTD ${mtd:,.0f} over {days_elapsed:.0f} days).",
                f"{pct:.0f}% of budget", f">= {t['budget_pacing_warn_pct']:.0f}%",
                "Identify the top warehouse/user driving spend; right-size or add a resource monitor.",
            ))

    today = _num(metrics.get("today_spend_usd"))
    avg7 = _num(metrics.get("avg_daily_spend_usd_7d"))
    if avg7 > 0 and today > 0:
        spike = (today - avg7) / avg7 * 100.0
        if spike >= t["daily_spend_spike_pct"]:
            alerts.append(Alert(
                "High", PROACTIVE, "Cost", "Daily spend spike",
                f"Today's spend ${today:,.0f} is {spike:.0f}% above the 7-day average ${avg7:,.0f}.",
                f"+{spike:.0f}% vs 7d avg", f">= {t['daily_spend_spike_pct']:.0f}%",
                "Open Cost by Warehouse/User for the spike window and confirm intended workload.",
            ))

    cur_tb = _num(metrics.get("storage_tb_current"))
    prior_tb = _num(metrics.get("storage_tb_prior"))
    if prior_tb > 0:
        growth = (cur_tb - prior_tb) / prior_tb * 100.0
        if growth >= t["storage_growth_warn_pct"]:
            alerts.append(Alert(
                "Medium", PROACTIVE, "Cost", "Storage growth",
                f"Storage grew {growth:.0f}% ({prior_tb:.1f} TB to {cur_tb:.1f} TB).",
                f"+{growth:.0f}%", f">= {t['storage_growth_warn_pct']:.0f}%",
                "Check largest databases and retention/time-travel settings.",
            ))

    failed_tasks = _num(metrics.get("failed_task_runs"))
    if failed_tasks >= t["failed_task_runs_warn"]:
        alerts.append(Alert(
            "Critical" if failed_tasks >= 5 else "High", REACTIVE, "Tasks", "Failed task runs",
            f"{int(failed_tasks)} task run(s) failed in the window.",
            f"{int(failed_tasks)} failed", f">= {int(t['failed_task_runs_warn'])}",
            "Open Task Graphs, find the failing root task, and inspect its error message.",
        ))

    fqr = _num(metrics.get("failed_query_rate_pct"))
    if fqr >= t["failed_query_rate_warn_pct"]:
        alerts.append(Alert(
            "High", REACTIVE, "Performance", "Elevated query failure rate",
            f"{fqr:.1f}% of queries failed in the window.",
            f"{fqr:.1f}%", f">= {t['failed_query_rate_warn_pct']:.1f}%",
            "Inspect failing query patterns and warehouse/permission errors.",
        ))

    queued = _num(metrics.get("queued_queries"))
    if queued >= t["queued_queries_warn"]:
        alerts.append(Alert(
            "Medium", REACTIVE, "Performance", "Query queueing",
            f"{int(queued)} queries are queued — possible warehouse undersizing.",
            f"{int(queued)} queued", f">= {int(t['queued_queries_warn'])}",
            "Increase warehouse size/clusters or stagger the workload.",
        ))

    spill = _num(metrics.get("remote_spill_gb"))
    if spill >= t["remote_spill_gb_warn"]:
        alerts.append(Alert(
            "Medium", REACTIVE, "Performance", "Remote spill",
            f"{spill:.0f} GB spilled to remote storage — memory pressure.",
            f"{spill:.0f} GB", f">= {t['remote_spill_gb_warn']:.0f} GB",
            "Review heavy joins/sorts; size up the warehouse for those queries.",
        ))

    fl = _num(metrics.get("failed_logins"))
    if fl >= t["failed_logins_warn"]:
        alerts.append(Alert(
            "High", REACTIVE, "Security", "Failed login spike",
            f"{int(fl)} failed login(s) in the window.",
            f"{int(fl)} failed", f">= {int(t['failed_logins_warn'])}",
            "Review source IPs and users; confirm no credential-stuffing or misconfig.",
        ))

    no_mfa = _num(metrics.get("users_without_mfa"))
    if no_mfa >= t["users_without_mfa_warn"]:
        alerts.append(Alert(
            "High", REACTIVE, "Security", "Users without MFA",
            f"{int(no_mfa)} enabled password user(s) lack MFA.",
            f"{int(no_mfa)} user(s)", f">= {int(t['users_without_mfa_warn'])}",
            "Enforce MFA / SSO for these users; disable unused accounts.",
        ))

    grants = _num(metrics.get("new_grants"))
    if grants >= t["new_grants_warn"]:
        alerts.append(Alert(
            "Medium", REACTIVE, "Security", "High grant volume",
            f"{int(grants)} privilege grant(s) in the window — review for least privilege.",
            f"{int(grants)} grants", f">= {int(t['new_grants_warn'])}",
            "Review the grant log for unexpected privilege escalations.",
        ))

    alerts.sort(key=lambda a: (_SEVERITY_RANK.get(a.severity, 9), a.domain))
    return alerts


def summarize(alerts: list[Alert]) -> dict:
    out = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "total": len(alerts)}
    for a in alerts:
        out[a.severity] = out.get(a.severity, 0) + 1
    return out


def build_alert_object_sql(
    name: str,
    warehouse: str,
    schedule_minutes: int,
    condition_sql: str,
    message: str,
    recipients: str | None = None,
    integration: str | None = None,
) -> str:
    integration = integration or config.NOTIFICATION_INTEGRATION
    recipients = recipients or config.DEFAULT_ALERT_RECIPIENTS
    safe_name = "".join(c if (c.isalnum() or c == "_") else "_" for c in name.upper())
    while "__" in safe_name:
        safe_name = safe_name.replace("__", "_")
    safe_name = safe_name.strip("_") or "SNOWMONITOR_ALERT"
    return f"""-- Real server-side alert. Requires a configured email notification integration.
CREATE OR REPLACE ALERT {safe_name}
  WAREHOUSE = {warehouse}
  SCHEDULE = '{int(schedule_minutes)} MINUTE'
  IF (EXISTS (
{condition_sql.strip()}
  ))
  THEN CALL SYSTEM$SEND_EMAIL(
    '{integration}',
    '{recipients}',
    'SnowMonitor alert: {name}',
    '{message.replace("'", "''")}'
  );

ALTER ALERT {safe_name} RESUME;
"""
