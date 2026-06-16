"""Optimization engine — triage inefficient queries and explain the fix.

Two layers:
  - Rule-based (pure, tested): from a query's diagnostic stats (spill, GB scanned,
    cache %, partition pruning, duration) it derives concrete findings + a triage
    score, with no AI cost. This catches the common Snowflake anti-patterns:
    memory spill, full-table scans, poor partition pruning, long runtime.
  - AI (Cortex): an on-demand button sends the query text + stats to
    SNOWFLAKE.CORTEX.COMPLETE for tailored rewrite suggestions ("like cortex code").
    Opt-in per query (costs AI credits); fails gracefully without Cortex access.
"""

from __future__ import annotations

import config

# Thresholds (tunable).
SPILL_GB_WARN = 1.0
SCAN_GB_WARN = 50.0
CACHE_LOW_PCT = 10.0
PRUNING_BAD_PCT = 80.0     # scanned >= this % of partitions => little pruning
LONG_RUNTIME_SEC = 300.0


def _num(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def optimization_findings(stats: dict) -> list[dict]:
    """Return concrete optimization findings for one query's stats.

    stats keys: REMOTE_SPILL_GB, GB_SCANNED, CACHE_PCT, PRUNING_PCT,
                PARTITIONS_TOTAL, DURATION_SEC.
    """
    spill = _num(stats.get("REMOTE_SPILL_GB"))
    gb = _num(stats.get("GB_SCANNED"))
    cache = _num(stats.get("CACHE_PCT"))
    pruning = _num(stats.get("PRUNING_PCT"))
    parts_total = _num(stats.get("PARTITIONS_TOTAL"))
    dur = _num(stats.get("DURATION_SEC"))
    out: list[dict] = []

    if spill >= SPILL_GB_WARN:
        out.append({
            "severity": "High", "issue": "Memory spill to remote storage",
            "guidance": f"Spilled {spill:.1f} GB to remote storage — the warehouse ran out of memory and "
                        "fell back to disk/remote (very slow). Upsize the warehouse for this workload, or "
                        "cut intermediate data: filter earlier, avoid huge sorts/window functions over the "
                        "full set, and project only needed columns.",
        })
    if pruning >= PRUNING_BAD_PCT and parts_total > 1:
        out.append({
            "severity": "High", "issue": "Poor partition pruning",
            "guidance": f"Scanned {pruning:.0f}% of micro-partitions — almost no pruning. Add a selective "
                        "WHERE on the table's clustering key, or cluster the table on the column you filter/"
                        "join on. A full-partition scan is the usual cause of long SP runtimes.",
        })
    if gb >= SCAN_GB_WARN and cache < CACHE_LOW_PCT:
        out.append({
            "severity": "Medium", "issue": "Large cold scan",
            "guidance": f"Scanned {gb:.0f} GB with only {cache:.0f}% from cache. Select fewer columns, filter "
                        "rows earlier in the query, or materialize a narrower/pre-aggregated table the proc reads.",
        })
    if dur >= LONG_RUNTIME_SEC:
        out.append({
            "severity": "Medium", "issue": "Long runtime",
            "guidance": f"Ran {dur:.0f}s. Break the proc into smaller steps, push filters down, and confirm the "
                        "warehouse size matches the work (see the spill/pruning findings first).",
        })
    if not out:
        out.append({
            "severity": "Low", "issue": "No clear anti-pattern",
            "guidance": "No spill, big cold scan, or pruning problem detected. Runtime may be inherent to data "
                        "volume — consider incremental processing or a larger warehouse only if SLA-critical.",
        })
    return out


def triage_score(stats: dict) -> float:
    """Composite 'optimize me first' score. Higher = worse."""
    spill = _num(stats.get("REMOTE_SPILL_GB"))
    gb = _num(stats.get("GB_SCANNED"))
    pruning = _num(stats.get("PRUNING_PCT"))
    dur = _num(stats.get("DURATION_SEC"))
    return round(spill * 10.0 + gb * 0.5 + (pruning / 100.0) * 20.0 + dur * 0.1, 2)


def cortex_optimize_prompt(query_text: str, stats: dict) -> str:
    """Build the prompt sent to Cortex for AI optimization suggestions."""
    spill = _num(stats.get("REMOTE_SPILL_GB"))
    gb = _num(stats.get("GB_SCANNED"))
    pruning = _num(stats.get("PRUNING_PCT"))
    dur = _num(stats.get("DURATION_SEC"))
    q = str(query_text or "")[:3000]
    return (
        "You are a senior Snowflake performance engineer. Analyze this query and give the top 3 most "
        "impactful, concrete optimizations (clustering/pruning, query rewrite, reducing scanned data, "
        "warehouse sizing). Be specific and brief; cite the exact change. "
        f"Stats: scanned {gb:.1f} GB, spilled {spill:.1f} GB to remote, scanned {pruning:.0f}% of "
        f"micro-partitions (high = poor pruning), runtime {dur:.0f}s.\n\nQuery:\n{q}"
    )


def cortex_optimize_sql(query_text: str, stats: dict, model: str | None = None) -> str:
    """SQL that calls Cortex COMPLETE to get AI optimization suggestions.

    Requires Cortex access; the model must be allowed by any CORTEX_MODELS_ALLOWLIST.
    """
    model = model or getattr(config, "CORTEX_OPTIMIZE_MODEL", "llama3.1-70b")
    prompt = cortex_optimize_prompt(query_text, stats).replace("'", "''")
    safe_model = "".join(c for c in str(model) if c.isalnum() or c in "._-")
    return f"SELECT SNOWFLAKE.CORTEX.COMPLETE('{safe_model}', '{prompt}') AS SUGGESTION"
