"""Tests for spend forecasting + budget burndown."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import forecast  # noqa: E402


class ProjectionTests(unittest.TestCase):
    def test_linear_runrate(self):
        # $10k over 10 days of a 30-day month -> $30k projection
        p = forecast.month_end_projection(10000, 10, 30)
        self.assertEqual(p["projection"], 30000.0)
        self.assertEqual(p["run_rate_daily"], 1000.0)
        self.assertEqual(p["days_remaining"], 20)
        self.assertLess(p["low"], p["projection"])
        self.assertGreater(p["high"], p["projection"])

    def test_band_widens_with_variability(self):
        steady = forecast.month_end_projection(10000, 10, 30, daily_spends=[1000] * 10)
        spiky = forecast.month_end_projection(10000, 10, 30, daily_spends=[200, 50, 3000, 100, 2500, 80, 3000, 90, 700, 280])
        self.assertGreater(spiky["band_pct"], steady["band_pct"])

    def test_handles_zero_day(self):
        p = forecast.month_end_projection(0, 0, 30)
        self.assertEqual(p["projection"], 0.0)


class BudgetTests(unittest.TestCase):
    def test_over_budget(self):
        b = forecast.budget_status(60000, 50000, 20000, 10, 30)
        self.assertEqual(b["state"], "Over budget")
        self.assertEqual(b["projected_overage"], 10000.0)
        self.assertGreaterEqual(b["pct_of_budget"], 100)

    def test_on_track(self):
        b = forecast.budget_status(40000, 50000, 13000, 10, 30)
        self.assertEqual(b["state"], "On track")
        self.assertEqual(b["projected_overage"], 0.0)

    def test_at_risk(self):
        b = forecast.budget_status(47000, 50000, 16000, 10, 30)
        self.assertEqual(b["state"], "At risk")

    def test_no_budget(self):
        b = forecast.budget_status(40000, 0, 10000, 10, 30)
        self.assertFalse(b["has_budget"])


class BurndownTests(unittest.TestCase):
    def test_series(self):
        s = forecast.burndown_series([1000, 1000, 1000], 30000, 30)
        self.assertEqual(len(s), 3)
        self.assertEqual(s[0]["CUMULATIVE_ACTUAL"], 1000.0)
        self.assertEqual(s[2]["CUMULATIVE_ACTUAL"], 3000.0)
        self.assertEqual(s[0]["BUDGET_LINE"], 1000.0)  # 30000/30 * 1


if __name__ == "__main__":
    unittest.main()
