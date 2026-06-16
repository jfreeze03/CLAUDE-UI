"""Per-entity anomaly detection (baselines for proactive alerts).

Fixed thresholds miss the warehouse that quietly doubled while the account total
looked flat. This compares each entity's latest value to its own trailing baseline
(mean + std dev) and flags statistically unusual jumps. Pure pandas — fully tested.
"""

from __future__ import annotations

import pandas as pd

import config


def detect_anomalies(
    df: pd.DataFrame,
    entity_col: str,
    value_col: str,
    date_col: str,
    z_threshold: float | None = None,
    min_baseline_days: int | None = None,
    min_abs: float | None = None,
) -> list[dict]:
    """Return per-entity anomalies where the latest value spikes above its baseline.

    For each entity: baseline = all-but-latest daily values. Flag the latest when
      - it has at least `min_baseline_days` of history, and
      - it is >= `min_abs` (ignore trivial dollar/credit amounts), and
      - z = (latest - mean) / std >= `z_threshold`
        (or, for a flat baseline with std~0, latest >= 1.5x the baseline mean).
    """
    z_threshold = config.ANOMALY_Z_THRESHOLD if z_threshold is None else z_threshold
    min_baseline_days = config.ANOMALY_MIN_BASELINE_DAYS if min_baseline_days is None else min_baseline_days
    min_abs = config.ANOMALY_MIN_ABS_USD if min_abs is None else min_abs

    if df is None or df.empty:
        return []
    for col in (entity_col, value_col, date_col):
        if col not in df.columns:
            return []

    work = df[[entity_col, date_col, value_col]].copy()
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce").fillna(0.0)
    work = work.sort_values(date_col)

    anomalies: list[dict] = []
    for entity, grp in work.groupby(entity_col):
        values = grp[value_col].tolist()
        if len(values) < min_baseline_days + 1:
            continue
        latest = float(values[-1])
        baseline = pd.Series(values[:-1], dtype="float64")
        mean = float(baseline.mean())
        std = float(baseline.std(ddof=0))
        if latest < min_abs:
            continue

        is_anom = False
        z = None
        if std > 1e-9:
            z = (latest - mean) / std
            is_anom = z >= z_threshold
        else:
            # Flat baseline: flag a clear step-up only.
            is_anom = mean > 0 and latest >= mean * 1.5

        if is_anom:
            anomalies.append({
                "entity": str(entity),
                "latest": round(latest, 2),
                "baseline_mean": round(mean, 2),
                "baseline_std": round(std, 2),
                "z": round(z, 2) if z is not None else None,
                "pct_above_mean": round((latest - mean) / mean * 100, 1) if mean > 0 else None,
                "baseline_days": len(baseline),
            })

    # Rank by absolute latest value (biggest spike first) — works whether or not
    # a z-score exists (flat baselines have none). z is shown in the detail.
    anomalies.sort(key=lambda a: a["latest"], reverse=True)
    return anomalies


def to_alerts(anomalies: list[dict], domain: str, what: str, unit: str = "$"):
    """Convert anomalies into Alert objects for the alert feed (proactive)."""
    from . import alerts as engine
    out = []
    for a in anomalies:
        pct = f"+{a['pct_above_mean']:.0f}%" if a.get("pct_above_mean") is not None else "step-up"
        zlabel = f"z={a['z']}" if a.get("z") is not None else "flat baseline"
        out.append(engine.Alert(
            "High" if (a.get("z") or 0) >= 4 else "Medium",
            engine.PROACTIVE, domain, f"{what} anomaly: {a['entity']}",
            f"{a['entity']} latest {unit}{a['latest']:,.0f} vs baseline {unit}{a['baseline_mean']:,.0f} "
            f"({pct}, {zlabel}, {a['baseline_days']}d baseline).",
            f"{unit}{a['latest']:,.0f}", f"{pct} vs baseline",
            f"Confirm the spike on {a['entity']} is intended workload.",
        ))
    return out
