"""Pure task-health logic — SLA status and consecutive-failure counting.

Kept Streamlit/Snowflake-free and fully tested. The Tasks page feeds these the
numbers it pulls from TASK_HISTORY.
"""

from __future__ import annotations


def consecutive_failures(states_recent_first: list[str]) -> int:
    """Count leading FAILED runs (states ordered most-recent first).

    Stops at the first non-FAILED state. SKIPPED/CANCELLED do not count as a
    success either, but they break a failure streak (the task isn't actively
    erroring), so only a literal 'FAILED' continues the streak.
    """
    n = 0
    for s in states_recent_first:
        if str(s or "").upper() == "FAILED":
            n += 1
        else:
            break
    return n


def sla_status(
    minutes_since_last: float,
    expected_interval_min: float,
    late_multiple: float = 1.25,
    stale_multiple: float = 2.0,
) -> str:
    """Classify freshness from how long since the last run vs the expected cadence.

    On time   : within late_multiple x cadence
    Late       : within stale_multiple x cadence
    Stale      : beyond that (pipeline likely stopped)
    Unknown    : no usable cadence (one-off / on-demand task)
    """
    try:
        since = float(minutes_since_last)
        interval = float(expected_interval_min)
    except (TypeError, ValueError):
        return "Unknown"
    if interval <= 0:
        return "Unknown"
    if since <= interval * late_multiple:
        return "On time"
    if since <= interval * stale_multiple:
        return "Late"
    return "Stale"


def sla_summary(rows: list[dict]) -> dict:
    """Count tasks by SLA status. rows need MINUTES_SINCE_LAST + EXPECTED_INTERVAL_MIN."""
    out = {"On time": 0, "Late": 0, "Stale": 0, "Unknown": 0}
    for r in rows:
        out[sla_status(r.get("MINUTES_SINCE_LAST"), r.get("EXPECTED_INTERVAL_MIN"))] += 1
    return out
