"""Tests for the proactive/reactive alert engine."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import alerts  # noqa: E402


class EvaluateTests(unittest.TestCase):
    def test_no_alerts_on_healthy_metrics(self):
        healthy = {
            "mtd_spend_usd": 1000, "days_elapsed": 10, "days_in_month": 30,
            "today_spend_usd": 100, "avg_daily_spend_usd_7d": 100,
            "storage_tb_current": 10, "storage_tb_prior": 10,
            "failed_task_runs": 0, "failed_query_rate_pct": 0.5, "queued_queries": 0,
            "remote_spill_gb": 0, "failed_logins": 0, "users_without_mfa": 0, "new_grants": 0,
        }
        self.assertEqual(alerts.evaluate(healthy), [])

    def test_budget_pacing_is_proactive(self):
        # MTD 20k over 10 days -> forecast 60k -> 120% of 50k budget.
        m = {"mtd_spend_usd": 20000, "days_elapsed": 10, "days_in_month": 30}
        result = alerts.evaluate(m)
        pacing = [a for a in result if a.title == "Budget pacing over threshold"]
        self.assertEqual(len(pacing), 1)
        self.assertEqual(pacing[0].kind, alerts.PROACTIVE)
        self.assertEqual(pacing[0].severity, "High")

    def test_failed_tasks_reactive_and_critical_when_many(self):
        one = alerts.evaluate({"failed_task_runs": 1})
        many = alerts.evaluate({"failed_task_runs": 6})
        self.assertEqual([a for a in one if a.domain == "Tasks"][0].kind, alerts.REACTIVE)
        self.assertEqual([a for a in one if a.domain == "Tasks"][0].severity, "High")
        self.assertEqual([a for a in many if a.domain == "Tasks"][0].severity, "Critical")

    def test_spend_spike_threshold(self):
        below = alerts.evaluate({"today_spend_usd": 120, "avg_daily_spend_usd_7d": 100})  # +20%
        above = alerts.evaluate({"today_spend_usd": 200, "avg_daily_spend_usd_7d": 100})  # +100%
        self.assertEqual([a for a in below if a.title == "Daily spend spike"], [])
        self.assertEqual(len([a for a in above if a.title == "Daily spend spike"]), 1)

    def test_security_mfa_and_logins(self):
        result = alerts.evaluate({"users_without_mfa": 3, "failed_logins": 50})
        domains = {a.domain for a in result}
        self.assertIn("Security", domains)
        titles = {a.title for a in result}
        self.assertIn("Users without MFA", titles)
        self.assertIn("Failed login spike", titles)

    def test_sorted_by_severity(self):
        result = alerts.evaluate({"failed_task_runs": 6, "queued_queries": 100})  # Critical + Medium
        self.assertEqual(result[0].severity, "Critical")

    def test_summarize_counts(self):
        result = alerts.evaluate({"failed_task_runs": 6, "queued_queries": 100, "users_without_mfa": 2})
        s = alerts.summarize(result)
        self.assertEqual(s["total"], len(result))
        self.assertGreaterEqual(s["Critical"], 1)


class AlertObjectSqlTests(unittest.TestCase):
    def test_alert_object_sql_shape(self):
        sql = alerts.build_alert_object_sql(
            name="Failed Tasks",
            warehouse="MONITOR_WH",
            schedule_minutes=15,
            condition_sql="SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY WHERE state='FAILED'",
            message="Failed tasks detected",
        )
        self.assertIn("CREATE OR REPLACE ALERT FAILED_TASKS", sql)
        self.assertIn("SCHEDULE = '15 MINUTE'", sql)
        self.assertIn("SYSTEM$SEND_EMAIL", sql)
        self.assertIn("RESUME", sql)


if __name__ == "__main__":
    unittest.main()
