"""Tests for the executive digest."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import digest  # noqa: E402


class BuildDigestTests(unittest.TestCase):
    def _digest(self):
        return digest.build_digest(
            company="ALFA", days=7,
            metrics={"mtd_spend_usd": 20000, "failed_task_runs": 2, "failed_logins": 5, "users_without_mfa": 1},
            alert_summary={"total": 4, "Critical": 1, "High": 2, "Medium": 1},
            savings_total=1500,
            projection={"projection": 60000, "low": 52000, "high": 68000, "run_rate_daily": 2000},
            budget_state={"has_budget": True, "pct_of_budget": 120, "state": "Over budget"},
            top_driver=("WH_TRXS_LOAD", 8000),
        )

    def test_contains_key_sections(self):
        d = self._digest()
        self.assertIn("SnowMonitor digest — ALFA", d)
        self.assertIn("Month-to-date", d)
        self.assertIn("Forecast month-end", d)
        self.assertIn("120% of budget", d)
        self.assertIn("Over budget", d)
        self.assertIn("Open alerts", d)
        self.assertIn("1 critical", d)
        self.assertIn("Savings opportunity", d)
        self.assertIn("WH_TRXS_LOAD", d)

    def test_no_budget_omits_budget_line(self):
        d = digest.build_digest("ALFA", 7, {"mtd_spend_usd": 100},
                                {"total": 0}, 0,
                                {"projection": 300, "low": 280, "high": 320, "run_rate_daily": 10},
                                {"has_budget": False})
        self.assertIn("Forecast month-end", d)
        self.assertNotIn("% of budget", d)


class DigestTaskTests(unittest.TestCase):
    def test_task_sql(self):
        sql = digest.digest_task_sql("Weekly Digest", "MONITOR_WH", "1440 MINUTE",
                                     recipients="exec@example.com", integration="EMAIL_INT")
        self.assertIn("CREATE OR REPLACE TASK WEEKLY_DIGEST", sql)
        self.assertIn("SYSTEM$SEND_EMAIL", sql)
        self.assertIn("MART_WAREHOUSE_COST_DAILY", sql)
        self.assertIn("RESUME", sql)


if __name__ == "__main__":
    unittest.main()
