"""Tests for guarded control-action SQL generation + validation."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import controls  # noqa: E402


class IdentifierTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(controls.safe_identifier("wh_trxs_load"), "WH_TRXS_LOAD")

    def test_qualified(self):
        self.assertEqual(controls.safe_identifier("SNOWFLAKE.CORTEX_USER", allow_qualified=True), "SNOWFLAKE.CORTEX_USER")

    def test_rejects_injection(self):
        for bad in ["wh; DROP TABLE x", "wh'", "wh--", "1bad", "", "a b"]:
            with self.assertRaises(ValueError):
                controls.safe_identifier(bad)


class WarehouseTimeoutTests(unittest.TestCase):
    def test_set_with_rollback(self):
        a = controls.set_warehouse_timeout_action("MY_WH", 600, 120, 3600, 0)
        self.assertIn("ALTER WAREHOUSE MY_WH SET STATEMENT_TIMEOUT_IN_SECONDS = 600", a.sql)
        self.assertIn("STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 120", a.sql)
        # Rollback restores prior values.
        self.assertIn("STATEMENT_TIMEOUT_IN_SECONDS = 3600", a.rollback_sql)
        self.assertIn("STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 0", a.rollback_sql)

    def test_statement_only(self):
        a = controls.set_warehouse_timeout_action("WH", 300, None, 600, None)
        self.assertIn("STATEMENT_TIMEOUT_IN_SECONDS = 300", a.sql)
        self.assertNotIn("QUEUED", a.sql)

    def test_rejects_out_of_bounds(self):
        with self.assertRaises(ValueError):
            controls.set_warehouse_timeout_action("WH", 999999, None, None, None)
        with self.assertRaises(ValueError):
            controls.set_warehouse_timeout_action("WH", -1, None, None, None)

    def test_rejects_bad_warehouse(self):
        with self.assertRaises(ValueError):
            controls.set_warehouse_timeout_action("WH; DROP", 300, None, None, None)

    def test_no_prior_values_safe_rollback(self):
        a = controls.set_warehouse_timeout_action("WH", 300, None, None, None)
        self.assertIn("rollback manually", a.rollback_sql.lower())

    def test_current_sql(self):
        self.assertIn("SHOW PARAMETERS", controls.warehouse_timeout_current_sql("WH"))
        self.assertIn("IN WAREHOUSE WH", controls.warehouse_timeout_current_sql("WH"))


class CortexTests(unittest.TestCase):
    def test_grant_access_and_rollback(self):
        a = controls.cortex_access_action("GRANT", "ANALYST_ROLE")
        self.assertIn("GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE ANALYST_ROLE", a.sql)
        self.assertIn("REVOKE DATABASE ROLE SNOWFLAKE.CORTEX_USER FROM ROLE ANALYST_ROLE", a.rollback_sql)

    def test_revoke_access_and_rollback(self):
        a = controls.cortex_access_action("REVOKE", "ANALYST_ROLE")
        self.assertIn("REVOKE DATABASE ROLE", a.sql)
        self.assertIn("GRANT DATABASE ROLE", a.rollback_sql)

    def test_invalid_action(self):
        with self.assertRaises(ValueError):
            controls.cortex_access_action("DELETE", "R")

    def test_model_allowlist_restrict(self):
        a = controls.cortex_model_allowlist_action(["mistral-large2", "llama3.1-8b"])
        self.assertIn("CORTEX_MODELS_ALLOWLIST = 'mistral-large2,llama3.1-8b'", a.sql)
        self.assertIn("UNSET CORTEX_MODELS_ALLOWLIST", a.rollback_sql)

    def test_model_allowlist_reset(self):
        a = controls.cortex_model_allowlist_action(None)
        self.assertIn("UNSET CORTEX_MODELS_ALLOWLIST", a.sql)

    def test_model_allowlist_rejects_injection(self):
        with self.assertRaises(ValueError):
            controls.cortex_model_allowlist_action(["ok-model", "bad'; DROP"])


class AuditTests(unittest.TestCase):
    def test_audit_insert(self):
        a = controls.cortex_access_action("GRANT", "R")
        sql = controls.audit_insert_sql("jdoe", a)
        self.assertIn("INSERT INTO", sql)
        self.assertIn("'jdoe'", sql)
        self.assertIn("ACTION_AUDIT", sql)


if __name__ == "__main__":
    unittest.main()
