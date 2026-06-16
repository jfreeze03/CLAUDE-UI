"""Tests for task intelligence — pure logic + SQL shape."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import tasks_logic, tasks_intel  # noqa: E402


class ConsecutiveFailureTests(unittest.TestCase):
    def test_counts_leading_failures(self):
        self.assertEqual(tasks_logic.consecutive_failures(["FAILED", "FAILED", "SUCCEEDED"]), 2)

    def test_stops_at_success(self):
        self.assertEqual(tasks_logic.consecutive_failures(["SUCCEEDED", "FAILED", "FAILED"]), 0)

    def test_skipped_breaks_streak(self):
        self.assertEqual(tasks_logic.consecutive_failures(["FAILED", "SKIPPED", "FAILED"]), 1)

    def test_all_failed(self):
        self.assertEqual(tasks_logic.consecutive_failures(["FAILED", "FAILED", "FAILED"]), 3)

    def test_empty(self):
        self.assertEqual(tasks_logic.consecutive_failures([]), 0)


class SlaStatusTests(unittest.TestCase):
    def test_on_time(self):
        self.assertEqual(tasks_logic.sla_status(60, 60), "On time")      # exactly cadence
        self.assertEqual(tasks_logic.sla_status(70, 60), "On time")      # within 1.25x

    def test_late(self):
        self.assertEqual(tasks_logic.sla_status(90, 60), "Late")         # 1.5x -> Late

    def test_stale(self):
        self.assertEqual(tasks_logic.sla_status(300, 60), "Stale")       # 5x -> Stale

    def test_unknown_without_cadence(self):
        self.assertEqual(tasks_logic.sla_status(1000, 0), "Unknown")
        self.assertEqual(tasks_logic.sla_status(None, None), "Unknown")

    def test_summary(self):
        rows = [
            {"MINUTES_SINCE_LAST": 60, "EXPECTED_INTERVAL_MIN": 60},   # On time
            {"MINUTES_SINCE_LAST": 90, "EXPECTED_INTERVAL_MIN": 60},   # Late
            {"MINUTES_SINCE_LAST": 600, "EXPECTED_INTERVAL_MIN": 60},  # Stale
            {"MINUTES_SINCE_LAST": 10, "EXPECTED_INTERVAL_MIN": 0},    # Unknown
        ]
        s = tasks_logic.sla_summary(rows)
        self.assertEqual(s, {"On time": 1, "Late": 1, "Stale": 1, "Unknown": 1})


class TaskSqlTests(unittest.TestCase):
    def test_sla_sql(self):
        sql = tasks_intel.task_sla_sql(7, "ALFA")
        self.assertIn("TASK_HISTORY", sql)
        self.assertIn("MINUTES_SINCE_LAST", sql)
        self.assertIn("EXPECTED_INTERVAL_MIN", sql)
        self.assertIn("MEDIAN", sql)
        self.assertIn("last_success", sql)

    def test_health_sql(self):
        sql = tasks_intel.task_health_sql(7, "ALFA")
        self.assertIn("SUCCESS_PCT", sql)
        self.assertIn("P95_DURATION_SEC", sql)
        self.assertIn("APPROX_PERCENTILE", sql)

    def test_recent_states_sql(self):
        sql = tasks_intel.recent_task_states_sql(7, "ALFA")
        self.assertIn("QUALIFY ROW_NUMBER()", sql)
        self.assertIn("STATE", sql)

    def test_error_clusters_sql(self):
        sql = tasks_intel.error_clusters_sql(7, "ALFA")
        self.assertIn("error_message", sql)
        self.assertIn("TASKS_AFFECTED", sql)
        self.assertIn("state = 'FAILED'", sql)

    def test_serverless_cost_sql(self):
        sql = tasks_intel.serverless_task_cost_sql(7, "ALFA")
        self.assertIn("SERVERLESS_TASK_HISTORY", sql)
        self.assertIn("COST_USD", sql)

    def test_duration_daily_sql(self):
        sql = tasks_intel.task_duration_daily_sql(7, "ALFA")
        self.assertIn("AVG_DURATION_SEC", sql)
        self.assertIn("USAGE_DATE", sql)


if __name__ == "__main__":
    unittest.main()
