"""Shape tests for cost-intelligence SQL builders."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import cost_intel  # noqa: E402


class ServiceTests(unittest.TestCase):
    def test_service_cost(self):
        sql = cost_intel.service_cost_sql(7)
        self.assertIn("METERING_DAILY_HISTORY", sql)
        self.assertIn("service_type", sql)
        self.assertIn("3.68", sql)

    def test_service_daily(self):
        self.assertIn("METERING_DAILY_HISTORY", cost_intel.service_daily_sql(7))


class CortexTests(unittest.TestCase):
    def test_cortex_functions(self):
        sql = cost_intel.cortex_functions_cost_sql(7)
        self.assertIn("CORTEX_FUNCTIONS_USAGE_HISTORY", sql)
        self.assertIn("token_credits", sql)
        self.assertIn("2.2", sql)  # AI rate

    def test_cortex_code(self):
        sql = cost_intel.cortex_code_cost_sql(7)
        self.assertIn("CORTEX_CODE_CLI_USAGE_HISTORY", sql)
        self.assertIn("user_name", sql)


class ChargebackTests(unittest.TestCase):
    def test_chargeback_has_company_and_dim(self):
        sql = cost_intel.chargeback_sql(7, "Database")
        self.assertIn("COMPANY", sql)
        self.assertIn("QUERY_HISTORY", sql)
        self.assertIn("'Trexis'", sql)   # company_case included
        self.assertIn("database_name", sql)
        self.assertIn("exec_ms / NULLIF(q.hour_total_ms, 0)", sql)


class StorageDetailTests(unittest.TestCase):
    def test_storage_detail(self):
        sql = cost_intel.storage_detail_sql("ALFA")
        self.assertIn("TABLE_STORAGE_METRICS", sql)
        self.assertIn("active_bytes", sql)
        self.assertIn("time_travel_bytes", sql)
        self.assertIn("failsafe_bytes", sql)


class VarianceTests(unittest.TestCase):
    def test_variance_compares_windows(self):
        sql = cost_intel.cost_variance_sql(7, "ALFA")
        self.assertIn("DELTA_USD", sql)
        self.assertIn("PCT_CHANGE", sql)
        self.assertIn("-14,", sql)   # prior window = 2*7 days
        self.assertIn("FULL OUTER JOIN", sql)


class EfficiencyTests(unittest.TestCase):
    def test_efficiency_summary(self):
        sql = cost_intel.efficiency_summary_sql(7, "ALFA")
        self.assertIn("COST_PER_QUERY_USD", sql)
        self.assertIn("COST_PER_TB_USD", sql)
        self.assertIn("percentage_scanned_from_cache", sql)
        self.assertIn("FAILED_QUERY_WASTE_USD", sql)

    def test_warehouse_efficiency(self):
        sql = cost_intel.warehouse_efficiency_sql(7, "ALFA")
        self.assertIn("CREDITS_PER_EXEC_HOUR", sql)
        self.assertIn("QUEUE_SECONDS", sql)
        self.assertIn("REMOTE_SPILL_GB", sql)


if __name__ == "__main__":
    unittest.main()
