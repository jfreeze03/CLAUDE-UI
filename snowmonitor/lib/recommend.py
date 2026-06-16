"""Recommendations engine — the "so what" layer.

Turns raw signals into a single ranked list of actions, each with an *estimated*
monthly $ saving and the exact SQL to apply it. This is what makes the tool stand
out: not more tables, but a prioritized "do these things, save this much" list.

Two halves:
  - SQL builders gather signals from ACCOUNT_USAGE (idle warehouses, bloated
    time-travel, expensive repeated queries).
  - Pure functions (fully tested) convert signal rows into ranked Recommendations.
Savings are conservative estimates and labeled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import config
from . import formulas

AU = "SNOWFLAKE.ACCOUNT_USAGE"

# Conservative "recoverable fraction" assumptions (tunable).
IDLE_RECOVERABLE = 0.50           # half of idle warehouse cost is realistically recoverable
TIME_TRAVEL_RECOVERABLE = 0.70    # of time-travel storage beyond 1 day
REPEAT_RECOVERABLE = 0.50         # of repeated-query compute via caching/materialization


@dataclass(frozen=True)
class Recommendation:
    category: str            # Warehouse | Storage | Query
    severity: str            # High | Medium | Low (by $ impact)
    title: str
    detail: str
    monthly_savings_usd: float
    fix_sql: str

    def as_row(self) -> dict:
        return asdict(self)


def _sev(saving: float) -> str:
    return "High" if saving >= 500 else "Medium" if saving >= 100 else "Low"


def _num(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# Signal SQL
# --------------------------------------------------------------------------

def idle_warehouse_signal_sql(days: int) -> str:
    """Per-warehouse idle %: billed warehouse-hours vs actual query execution time."""
    win = f"DATEADD('day', -{max(1, int(days))}, CURRENT_TIMESTAMP())"
    return f"""
    WITH wh AS (
        SELECT warehouse_name, SUM(COALESCE(credits_used,0)) AS credits,
               COUNT(DISTINCT DATE_TRUNC('hour', start_time)) AS billed_hours
        FROM {AU}.WAREHOUSE_METERING_HISTORY
        WHERE start_time >= {win} AND warehouse_name IS NOT NULL
        GROUP BY warehouse_name
    ),
    ex AS (
        SELECT warehouse_name, SUM(execution_time)/1000.0 AS exec_seconds
        FROM {AU}.QUERY_HISTORY
        WHERE start_time >= {win} AND warehouse_name IS NOT NULL AND execution_time > 0
        GROUP BY warehouse_name
    )
    SELECT wh.warehouse_name AS WAREHOUSE,
           ROUND(wh.credits * {config.CREDIT_PRICE_USD}, 2) AS WINDOW_COST_USD,
           ROUND(100 * (1 - LEAST(1, COALESCE(ex.exec_seconds,0) / NULLIF(wh.billed_hours*3600.0, 0))), 1) AS IDLE_PCT
    FROM wh LEFT JOIN ex ON wh.warehouse_name = ex.warehouse_name
    WHERE wh.credits > 0
    ORDER BY WINDOW_COST_USD DESC
    """


def time_travel_signal_sql(min_tb: float = 0.1) -> str:
    """Databases with large time-travel storage (retention reduction candidates)."""
    return f"""
    SELECT table_catalog AS DATABASE,
           ROUND(SUM(time_travel_bytes)/POWER(1024,4), 3) AS TIME_TRAVEL_TB,
           ROUND(SUM(active_bytes)/POWER(1024,4), 3) AS ACTIVE_TB
    FROM {AU}.TABLE_STORAGE_METRICS
    WHERE deleted = FALSE AND table_catalog IS NOT NULL
    GROUP BY table_catalog
    HAVING TIME_TRAVEL_TB >= {float(min_tb)}
    ORDER BY TIME_TRAVEL_TB DESC
    LIMIT 50
    """


def repeated_query_signal_sql(days: int, min_runs: int = 50) -> str:
    """Frequently-repeated queries by total execution time (caching candidates)."""
    win = f"DATEADD('day', -{max(1, int(days))}, CURRENT_TIMESTAMP())"
    return f"""
    SELECT query_parameterized_hash AS QUERY_HASH,
           COUNT(*) AS RUNS,
           ROUND(SUM(execution_time)/1000.0/3600.0, 2) AS TOTAL_EXEC_HOURS,
           ANY_VALUE(LEFT(query_text, 120)) AS SAMPLE_QUERY
    FROM {AU}.QUERY_HISTORY
    WHERE start_time >= {win} AND execution_time > 0 AND query_parameterized_hash IS NOT NULL
    GROUP BY query_parameterized_hash
    HAVING RUNS >= {int(min_runs)}
    ORDER BY TOTAL_EXEC_HOURS DESC
    LIMIT 50
    """


# --------------------------------------------------------------------------
# Pure engine — signal rows -> Recommendations
# --------------------------------------------------------------------------

def idle_warehouse_recs(rows: list[dict], window_days: int, idle_threshold_pct: float = 40.0) -> list[Recommendation]:
    """rows: [{WAREHOUSE, WINDOW_COST_USD, IDLE_PCT}]."""
    out = []
    factor = 30.0 / max(1.0, float(window_days))  # window cost -> monthly
    for r in rows:
        idle = _num(r.get("IDLE_PCT"))
        cost = _num(r.get("WINDOW_COST_USD"))
        if idle < idle_threshold_pct or cost <= 0:
            continue
        monthly = cost * factor
        saving = round(monthly * (idle / 100.0) * IDLE_RECOVERABLE, 2)
        if saving <= 0:
            continue
        wh = r.get("WAREHOUSE", "?")
        out.append(Recommendation(
            "Warehouse", _sev(saving), f"Idle warehouse: {wh}",
            f"{wh} is ~{idle:.0f}% idle (~${monthly:,.0f}/mo run-rate). Lower AUTO_SUSPEND or "
            f"consolidate workloads. Est. recoverable ~${saving:,.0f}/mo (conservative).",
            saving,
            f"ALTER WAREHOUSE {wh} SET AUTO_SUSPEND = 60;  -- seconds; verify workload tolerance",
        ))
    return out


def time_travel_recs(rows: list[dict], current_retention_days: int = 1) -> list[Recommendation]:
    """rows: [{DATABASE, TIME_TRAVEL_TB, ACTIVE_TB}]."""
    out = []
    rate = config.STORAGE_COST_PER_TB_USD
    for r in rows:
        tt = _num(r.get("TIME_TRAVEL_TB"))
        if tt <= 0:
            continue
        saving = round(tt * rate * TIME_TRAVEL_RECOVERABLE, 2)
        if saving <= 0:
            continue
        db = r.get("DATABASE", "?")
        out.append(Recommendation(
            "Storage", _sev(saving), f"Time-travel bloat: {db}",
            f"{db} holds ~{tt:.1f} TB of time-travel storage (~${tt*rate:,.0f}/mo). If business "
            f"rules allow, reduce DATA_RETENTION_TIME_IN_DAYS. Est. recoverable ~${saving:,.0f}/mo.",
            saving,
            f"ALTER DATABASE {db} SET DATA_RETENTION_TIME_IN_DAYS = 1;  -- confirm recovery needs first",
        ))
    return out


def repeated_query_recs(rows: list[dict]) -> list[Recommendation]:
    """rows: [{QUERY_HASH, RUNS, TOTAL_EXEC_HOURS, SAMPLE}]."""
    out = []
    # Cost per compute-hour ~ credits/hr * rate; assume ~1 credit/hr baseline (conservative floor).
    for r in rows:
        runs = int(_num(r.get("RUNS")))
        hours = _num(r.get("TOTAL_EXEC_HOURS"))
        if runs < 50 or hours <= 0:
            continue
        approx_cost = hours * config.CREDIT_PRICE_USD  # ~1 credit/exec-hour, conservative
        saving = round(approx_cost * REPEAT_RECOVERABLE, 2)
        if saving <= 0:
            continue
        sample = str(r.get("SAMPLE_QUERY", "")).strip()
        out.append(Recommendation(
            "Query", _sev(saving), f"Repeated heavy query ({runs:,} runs)",
            f"A query pattern ran {runs:,} times for ~{hours:.1f} exec-hours. Consider a result "
            f"cache, materialized view, or dynamic table. Sample: {sample[:90]}",
            saving,
            "-- Investigate this pattern for a materialized view / dynamic table:\n"
            f"-- {sample[:110]}",
        ))
    return out


# Warehouse sizing thresholds (tunable).
SIZING_QUEUE_SECONDS = 600.0      # cumulative queue seconds in window => undersized
SIZING_SPILL_GB = 10.0            # remote spill GB in window => memory pressure => undersized
SIZING_MIN_MONTHLY_COST = 100.0   # only suggest downsize above this monthly cost
DOWNSIZE_RECOVERABLE = 0.40       # downsize one step ~halves credits; conservative
CLUSTERING_MIN_MONTHLY = 50.0
CLUSTERING_RECOVERABLE = 0.50


def warehouse_sizing_recs(rows: list[dict], window_days: int) -> list[Recommendation]:
    """Up/down-size calls from warehouse efficiency rows.

    rows: [{WAREHOUSE, COST_USD, QUEUE_SECONDS, REMOTE_SPILL_GB, QUERIES, CREDITS_PER_EXEC_HOUR}].
    Upsize = clear evidence of undersizing (queueing or remote spill) — a performance/
    reliability fix (no direct $ saving). Downsize = no queue + no spill + meaningful
    cost — a conservative savings candidate to verify against query latency.
    """
    out = []
    factor = 30.0 / max(1.0, float(window_days))
    for r in rows:
        wh = r.get("WAREHOUSE", "?")
        cost = _num(r.get("COST_USD"))
        monthly = cost * factor
        queue = _num(r.get("QUEUE_SECONDS"))
        spill = _num(r.get("REMOTE_SPILL_GB"))

        if spill >= SIZING_SPILL_GB or queue >= SIZING_QUEUE_SECONDS:
            why = []
            if spill >= SIZING_SPILL_GB:
                why.append(f"{spill:.0f} GB remote spill (memory pressure)")
            if queue >= SIZING_QUEUE_SECONDS:
                why.append(f"{queue:.0f}s total queue time (concurrency pressure)")
            sev = "High" if spill >= SIZING_SPILL_GB else "Medium"
            fix = (f"ALTER WAREHOUSE {wh} SET WAREHOUSE_SIZE = '<one size larger>';"
                   if spill >= SIZING_SPILL_GB else
                   f"ALTER WAREHOUSE {wh} SET MAX_CLUSTER_COUNT = <n>;  -- multi-cluster for concurrency, or size up")
            out.append(Recommendation(
                "Warehouse", sev, f"Upsize: {wh}",
                f"{wh} shows " + " and ".join(why) + f" (~${monthly:,.0f}/mo). Upsizing reduces spill/queueing; "
                "a larger warehouse that finishes faster often costs the same or less.",
                0.0, fix,
            ))
        elif queue < 30 and spill < 1 and monthly >= SIZING_MIN_MONTHLY_COST:
            saving = round(monthly * DOWNSIZE_RECOVERABLE, 2)
            out.append(Recommendation(
                "Warehouse", _sev(saving), f"Downsize candidate: {wh}",
                f"{wh} runs with no queueing and no spill at ~${monthly:,.0f}/mo. If its queries tolerate a "
                f"smaller size, downsizing one step could save ~${saving:,.0f}/mo. Verify query latency first.",
                saving,
                f"ALTER WAREHOUSE {wh} SET WAREHOUSE_SIZE = '<one size smaller>';  -- verify latency stays acceptable",
            ))
    return out


def clustering_recs(rows: list[dict], window_days: int) -> list[Recommendation]:
    """Inefficient automatic-clustering tables.

    rows: [{TABLE_NAME, CLUSTERING_COST_USD, TB_RECLUSTERED, COST_PER_TB_RECLUSTERED}].
    High monthly clustering cost (esp. with high recluster churn) suggests a poor
    cluster key or heavy DML — review the key or suspend reclustering.
    """
    out = []
    factor = 30.0 / max(1.0, float(window_days))
    for r in rows:
        tbl = r.get("TABLE_NAME", "?")
        monthly = _num(r.get("CLUSTERING_COST_USD")) * factor
        if monthly < CLUSTERING_MIN_MONTHLY:
            continue
        tb = _num(r.get("TB_RECLUSTERED"))
        saving = round(monthly * CLUSTERING_RECOVERABLE, 2)
        out.append(Recommendation(
            "Clustering", _sev(saving), f"Review clustering: {tbl}",
            f"Automatic clustering on {tbl} costs ~${monthly:,.0f}/mo (reclustered {tb:.1f} TB in window). "
            "High churn suggests a poor cluster key or heavy DML. Review the cluster key or suspend reclustering "
            f"if the query benefit doesn't justify it. Est. recoverable ~${saving:,.0f}/mo.",
            saving,
            f"-- Review the cluster key first; to stop reclustering:\nALTER TABLE {tbl} SUSPEND RECLUSTER;",
        ))
    return out


def rank(*rec_lists: list[Recommendation]) -> list[Recommendation]:
    """Merge and rank recommendations by estimated monthly saving."""
    merged: list[Recommendation] = []
    for lst in rec_lists:
        merged.extend(lst)
    merged.sort(key=lambda r: r.monthly_savings_usd, reverse=True)
    return merged


def total_savings(recs: list[Recommendation]) -> float:
    return round(sum(r.monthly_savings_usd for r in recs), 2)
