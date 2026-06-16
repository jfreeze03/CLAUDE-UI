"""Tests for cost/credit formulas and rate sourcing."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import config  # noqa: E402
from lib import formulas  # noqa: E402


class RateTests(unittest.TestCase):
    def test_rates_match_config(self):
        self.assertEqual(formulas.CREDIT_PRICE_USD, config.CREDIT_PRICE_USD)
        self.assertEqual(formulas.AI_CREDIT_PRICE_USD, config.AI_CREDIT_PRICE_USD)
        self.assertEqual(formulas.STORAGE_COST_PER_TB_USD, config.STORAGE_COST_PER_TB_USD)

    def test_known_rates(self):
        self.assertEqual(config.CREDIT_PRICE_USD, 3.68)
        self.assertEqual(config.AI_CREDIT_PRICE_USD, 2.20)
        self.assertEqual(config.STORAGE_COST_PER_TB_USD, 23.00)


class MathTests(unittest.TestCase):
    def test_warehouse_cost(self):
        self.assertEqual(formulas.warehouse_cost_usd(10), 36.80)
        self.assertEqual(formulas.warehouse_cost_usd(None), 0.0)
        self.assertEqual(formulas.warehouse_cost_usd(10, rate_usd=4.0), 40.0)

    def test_cortex_cost(self):
        self.assertEqual(formulas.cortex_ai_cost_usd(10), 22.0)

    def test_storage_cost(self):
        self.assertEqual(formulas.storage_cost_usd(2), 46.0)

    def test_allocation(self):
        self.assertEqual(formulas.allocate_credits(100, 250, 1000), 25.0)
        self.assertEqual(formulas.allocate_credits(100, 250, 0), 0.0)
        self.assertEqual(formulas.allocate_credits(100, 5000, 1000), 100.0)
        self.assertEqual(formulas.allocate_credits(100, -50, 1000), 0.0)

    def test_sql_fragments_null_safe(self):
        self.assertIn("COALESCE", formulas.SQL_TOTAL_CREDITS)
        self.assertIn("COALESCE(credits_used_compute, credits_used", formulas.SQL_COMPUTE_CREDITS)

    def test_cost_sql_embeds_rate(self):
        sql = formulas.cost_sql(formulas.SQL_TOTAL_CREDITS)
        self.assertIn("3.68", sql)
        self.assertIn("AS COST_USD", sql)


if __name__ == "__main__":
    unittest.main()
