"""Shape tests for the company-scoped SQL builders."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import queries  # noqa: E402


class CostQueryTests(unittest.TestCase):
    def test_warehouse_cost_alfa_excludes_trexis(self):
        sql = queries.warehouse_cost_sql(7, "ALFA")
        self.assertIn("WAREHOUSE_METERING_HISTORY", sql)
        self.assertIn("NOT", sql)
        self.assertIn("3.68", sql)
        self.assertIn("COALESCE(credits_used", sql)

    def test_warehouse_cost_trexis_allowlist(self):
        self.assertIn("WH_TRXS_LOAD", queries.warehouse_cost_sql(7, "Trexis"))

    def test_warehouse_cost_all_unscoped(self):
        self.assertNotIn("WH_TRXS_LOAD", queries.warehouse_cost_sql(7, "ALL"))

    def test_cost_by_dimension_allocates(self):
        sql = queries.cost_by_dimension_sql("User", 7, "ALFA")
        self.assertIn("QUERY_HISTORY", sql)
        self.assertIn("exec_ms / NULLIF(q.hour_total_ms, 0)", sql)
        self.assertIn("user_name", sql)

    def test_application_cost_joins_sessions(self):
        sql = queries.application_cost_sql(7, "ALFA")
        self.assertIn("SESSIONS", sql)
        self.assertIn("client_application_name", sql)

    def test_window_clamped(self):
        self.assertIn("-90,", queries.warehouse_cost_sql(9999, "ALFA"))


class TaskQueryTests(unittest.TestCase):
    def test_task_runs_and_graph(self):
        self.assertIn("TASK_HISTORY", queries.task_runs_sql(7, "ALFA"))
        graph = queries.task_graph_sql(7, "ALFA")
        self.assertIn("FAILED_RUNS", graph)
        self.assertIn("root_task_id", graph.lower())


class SecurityQueryTests(unittest.TestCase):
    def test_failed_logins(self):
        sql = queries.failed_logins_sql(7, "ALL")
        self.assertIn("LOGIN_HISTORY", sql)
        self.assertIn("is_success = 'NO'", sql)

    def test_users_without_mfa(self):
        sql = queries.users_without_mfa_sql("ALL")
        self.assertIn("ext_authn_duo", sql)
        self.assertIn("has_password = TRUE", sql)
        self.assertIn("LOGIN_HISTORY", sql)
        self.assertIn("first_authentication_factor", sql)
        self.assertIn("has_rsa_public_key", sql)
        self.assertIn("RISK_BASIS", sql)
        self.assertIn("SAML_2_0", sql)

    def test_recent_grants(self):
        self.assertIn("GRANTS_TO_ROLES", queries.recent_grants_sql(7, "ALL"))


if __name__ == "__main__":
    unittest.main()
