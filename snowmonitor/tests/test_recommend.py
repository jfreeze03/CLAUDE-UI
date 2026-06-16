"""Tests for the recommendations engine."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import recommend  # noqa: E402


class IdleWarehouseTests(unittest.TestCase):
    def test_flags_idle_expensive(self):
        rows = [{"WAREHOUSE": "BIG_WH", "WINDOW_COST_USD": 700, "IDLE_PCT": 80}]
        recs = recommend.idle_warehouse_recs(rows, window_days=7)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].category, "Warehouse")
        self.assertGreater(recs[0].monthly_savings_usd, 0)
        self.assertIn("AUTO_SUSPEND", recs[0].fix_sql)

    def test_skips_busy_or_cheap(self):
        self.assertEqual(recommend.idle_warehouse_recs(
            [{"WAREHOUSE": "W", "WINDOW_COST_USD": 700, "IDLE_PCT": 10}], 7), [])
        self.assertEqual(recommend.idle_warehouse_recs(
            [{"WAREHOUSE": "W", "WINDOW_COST_USD": 0, "IDLE_PCT": 90}], 7), [])

    def test_monthly_extrapolation(self):
        # 7-day window cost 700 -> ~3000/mo; 80% idle * 0.5 recoverable -> > 1000
        recs = recommend.idle_warehouse_recs([{"WAREHOUSE": "W", "WINDOW_COST_USD": 700, "IDLE_PCT": 80}], 7)
        self.assertGreater(recs[0].monthly_savings_usd, 800)
        self.assertEqual(recs[0].severity, "High")


class TimeTravelTests(unittest.TestCase):
    def test_flags_bloat(self):
        recs = recommend.time_travel_recs([{"DATABASE": "DB1", "TIME_TRAVEL_TB": 10, "ACTIVE_TB": 5}])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].category, "Storage")
        self.assertIn("DATA_RETENTION_TIME_IN_DAYS", recs[0].fix_sql)

    def test_skips_zero(self):
        self.assertEqual(recommend.time_travel_recs([{"DATABASE": "D", "TIME_TRAVEL_TB": 0}]), [])


class RepeatedQueryTests(unittest.TestCase):
    def test_flags_repeated(self):
        recs = recommend.repeated_query_recs(
            [{"QUERY_HASH": "h", "RUNS": 500, "TOTAL_EXEC_HOURS": 40, "SAMPLE": "SELECT * FROM big"}])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].category, "Query")

    def test_skips_infrequent(self):
        self.assertEqual(recommend.repeated_query_recs(
            [{"QUERY_HASH": "h", "RUNS": 10, "TOTAL_EXEC_HOURS": 40}]), [])


class SizingTests(unittest.TestCase):
    def test_upsize_on_spill(self):
        recs = recommend.warehouse_sizing_recs(
            [{"WAREHOUSE": "WH", "COST_USD": 700, "QUEUE_SECONDS": 0, "REMOTE_SPILL_GB": 50}], 7)
        self.assertEqual(len(recs), 1)
        self.assertIn("Upsize", recs[0].title)
        self.assertEqual(recs[0].severity, "High")
        self.assertEqual(recs[0].monthly_savings_usd, 0.0)  # performance, not $ saving
        self.assertIn("WAREHOUSE_SIZE", recs[0].fix_sql)

    def test_upsize_on_queue(self):
        recs = recommend.warehouse_sizing_recs(
            [{"WAREHOUSE": "WH", "COST_USD": 700, "QUEUE_SECONDS": 1200, "REMOTE_SPILL_GB": 0}], 7)
        self.assertIn("Upsize", recs[0].title)

    def test_downsize_candidate(self):
        recs = recommend.warehouse_sizing_recs(
            [{"WAREHOUSE": "WH", "COST_USD": 700, "QUEUE_SECONDS": 0, "REMOTE_SPILL_GB": 0}], 7)
        self.assertEqual(len(recs), 1)
        self.assertIn("Downsize", recs[0].title)
        self.assertGreater(recs[0].monthly_savings_usd, 0)

    def test_no_rec_when_cheap_and_balanced(self):
        self.assertEqual(recommend.warehouse_sizing_recs(
            [{"WAREHOUSE": "WH", "COST_USD": 5, "QUEUE_SECONDS": 0, "REMOTE_SPILL_GB": 0}], 7), [])


class ClusteringTests(unittest.TestCase):
    def test_flags_expensive_clustering(self):
        recs = recommend.clustering_recs(
            [{"TABLE_NAME": "DB.S.T", "CLUSTERING_COST_USD": 400, "TB_RECLUSTERED": 12}], 7)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].category, "Clustering")
        self.assertIn("SUSPEND RECLUSTER", recs[0].fix_sql)
        self.assertIn("DB.S.T", recs[0].title)

    def test_skips_cheap_clustering(self):
        self.assertEqual(recommend.clustering_recs(
            [{"TABLE_NAME": "T", "CLUSTERING_COST_USD": 2, "TB_RECLUSTERED": 0.1}], 7), [])


class RankTests(unittest.TestCase):
    def test_rank_and_total(self):
        idle = recommend.idle_warehouse_recs([{"WAREHOUSE": "W", "WINDOW_COST_USD": 700, "IDLE_PCT": 80}], 7)
        tt = recommend.time_travel_recs([{"DATABASE": "DB1", "TIME_TRAVEL_TB": 10}])
        ranked = recommend.rank(idle, tt)
        self.assertEqual(len(ranked), 2)
        # sorted descending by savings
        self.assertGreaterEqual(ranked[0].monthly_savings_usd, ranked[1].monthly_savings_usd)
        self.assertAlmostEqual(recommend.total_savings(ranked),
                               round(sum(r.monthly_savings_usd for r in ranked), 2))


if __name__ == "__main__":
    unittest.main()
