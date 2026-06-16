"""Tests for the SP intelligence + optimization engine."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import optimize, sp_intel  # noqa: E402


class ProcParseTests(unittest.TestCase):
    def test_parses_qualified_name(self):
        self.assertEqual(sp_intel.parse_proc_name("CALL DB.SCH.LOAD_CUSTOMERS('x')"), "DB.SCH.LOAD_CUSTOMERS")

    def test_parses_bare_name(self):
        self.assertEqual(sp_intel.parse_proc_name("call my_proc()"), "MY_PROC")

    def test_none_when_not_call(self):
        self.assertIsNone(sp_intel.parse_proc_name("SELECT 1"))


class SpSqlTests(unittest.TestCase):
    def test_performance_sql(self):
        sql = sp_intel.sp_performance_sql(7, "ALFA")
        self.assertIn("query_type = 'CALL'", sql)
        self.assertIn("TOTAL_MINUTES", sql)
        self.assertIn("P95_SEC", sql)
        self.assertIn("REGEXP_SUBSTR", sql)

    def test_degradation_sql(self):
        sql = sp_intel.sp_degradation_sql(7, "ALFA")
        self.assertIn("PCT_CHANGE", sql)
        self.assertIn("PRIOR_AVG_SEC", sql)
        self.assertIn("-14,", sql)  # prior window = 2x

    def test_heavy_query_sql(self):
        sql = sp_intel.heavy_query_sql(7, "ALFA")
        self.assertIn("REMOTE_SPILL_GB", sql)
        self.assertIn("PRUNING_PCT", sql)
        self.assertIn("partitions_scanned", sql)


class FindingsTests(unittest.TestCase):
    def test_spill_is_high(self):
        f = optimize.optimization_findings({"REMOTE_SPILL_GB": 20})
        self.assertEqual(f[0]["severity"], "High")
        self.assertIn("spill", f[0]["issue"].lower())

    def test_poor_pruning(self):
        f = optimize.optimization_findings({"PRUNING_PCT": 95, "PARTITIONS_TOTAL": 1000})
        self.assertTrue(any("pruning" in x["issue"].lower() for x in f))

    def test_cold_scan(self):
        f = optimize.optimization_findings({"GB_SCANNED": 200, "CACHE_PCT": 2})
        self.assertTrue(any("cold scan" in x["issue"].lower() for x in f))

    def test_clean_query(self):
        f = optimize.optimization_findings({"REMOTE_SPILL_GB": 0, "GB_SCANNED": 1, "DURATION_SEC": 5})
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "Low")

    def test_triage_score_orders(self):
        bad = optimize.triage_score({"REMOTE_SPILL_GB": 50, "GB_SCANNED": 500, "PRUNING_PCT": 100, "DURATION_SEC": 600})
        ok = optimize.triage_score({"REMOTE_SPILL_GB": 0, "GB_SCANNED": 5, "PRUNING_PCT": 10, "DURATION_SEC": 20})
        self.assertGreater(bad, ok)


class CortexTests(unittest.TestCase):
    def test_prompt_has_stats_and_query(self):
        p = optimize.cortex_optimize_prompt("SELECT * FROM big", {"GB_SCANNED": 100, "REMOTE_SPILL_GB": 5})
        self.assertIn("Snowflake", p)
        self.assertIn("SELECT * FROM big", p)
        self.assertIn("100", p)

    def test_cortex_sql_escapes_and_calls_complete(self):
        sql = optimize.cortex_optimize_sql("SELECT 'x' FROM t", {"GB_SCANNED": 10})
        self.assertIn("SNOWFLAKE.CORTEX.COMPLETE", sql)
        self.assertIn("''x''", sql)         # single quotes doubled (escaped), no breakout
        self.assertIn("AS SUGGESTION", sql)


if __name__ == "__main__":
    unittest.main()
