"""Tests for per-entity anomaly detection."""

import sys
import unittest
from pathlib import Path

import pandas as pd

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import anomaly  # noqa: E402


def _series(entity, values, start="2026-06-01"):
    dates = pd.date_range(start, periods=len(values))
    return pd.DataFrame({"E": entity, "D": dates, "V": values})


class DetectTests(unittest.TestCase):
    def test_flags_clear_spike(self):
        # Steady ~100, then jumps to 500.
        df = _series("WH1", [100, 105, 95, 100, 102, 98, 500])
        out = anomaly.detect_anomalies(df, "E", "V", "D", z_threshold=2.5, min_baseline_days=5, min_abs=10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["entity"], "WH1")
        self.assertGreater(out[0]["z"], 2.5)

    def test_no_anomaly_on_steady(self):
        df = _series("WH1", [100, 101, 99, 100, 102, 98, 101])
        self.assertEqual(anomaly.detect_anomalies(df, "E", "V", "D", min_abs=10), [])

    def test_respects_min_abs_floor(self):
        # A relative spike but tiny absolute values -> ignored.
        df = _series("WH1", [1, 1, 1, 1, 1, 1, 5])
        self.assertEqual(anomaly.detect_anomalies(df, "E", "V", "D", min_abs=50), [])

    def test_requires_min_baseline(self):
        df = _series("WH1", [100, 500])  # only 1 baseline day
        self.assertEqual(anomaly.detect_anomalies(df, "E", "V", "D", min_baseline_days=5, min_abs=10), [])

    def test_flat_baseline_step_up(self):
        # Constant 100 then 200 (std ~ 0) -> flagged via 1.5x rule.
        df = _series("WH1", [100, 100, 100, 100, 100, 100, 200])
        out = anomaly.detect_anomalies(df, "E", "V", "D", min_abs=10)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["z"])  # flat baseline -> no z

    def test_multiple_entities_sorted_by_z(self):
        df = pd.concat([
            _series("WH1", [100, 100, 100, 100, 100, 100, 300]),
            _series("WH2", [100, 100, 100, 100, 100, 100, 900]),
        ])
        out = anomaly.detect_anomalies(df, "E", "V", "D", min_abs=10)
        self.assertEqual([a["entity"] for a in out][0], "WH2")  # bigger spike first

    def test_empty_and_missing_columns(self):
        self.assertEqual(anomaly.detect_anomalies(pd.DataFrame(), "E", "V", "D"), [])
        self.assertEqual(anomaly.detect_anomalies(_series("X", [1, 2, 3]), "NOPE", "V", "D"), [])


class ToAlertsTests(unittest.TestCase):
    def test_converts_to_alerts(self):
        df = _series("WH1", [100, 100, 100, 100, 100, 100, 800])
        out = anomaly.detect_anomalies(df, "E", "V", "D", min_abs=10)
        alerts = anomaly.to_alerts(out, "Cost", "Warehouse spend")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "Proactive")
        self.assertIn("WH1", alerts[0].title)


if __name__ == "__main__":
    unittest.main()
