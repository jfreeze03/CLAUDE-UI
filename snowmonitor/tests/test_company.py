"""Tests for company segregation — the heart of the tool."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import config  # noqa: E402
from lib import company  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_trexis_by_warehouse(self):
        self.assertEqual(company.classify_company(warehouse="WH_TRXS_LOAD"), "Trexis")
        self.assertEqual(company.classify_company(warehouse="wh_trxs_query"), "Trexis")

    def test_trexis_by_db_prefix_and_contains(self):
        self.assertEqual(company.classify_company(database="TRXS_RAW"), "Trexis")
        self.assertEqual(company.classify_company(database="EDW_TRXS_PRD"), "Trexis")

    def test_trexis_by_user_prefix(self):
        self.assertEqual(company.classify_company(user="TRXS_BATCH"), "Trexis")

    def test_alfa_is_default_catch_all(self):
        self.assertEqual(company.classify_company(database="ALFA_EDW_PROD"), "ALFA")
        self.assertEqual(company.classify_company(warehouse="SOME_SHARED_WH"), "ALFA")
        self.assertEqual(company.classify_company(user="ANALYST_1"), "ALFA")

    def test_default_company_is_alfa(self):
        self.assertEqual(config.DEFAULT_COMPANY, "ALFA")

    def test_trexis_wins_when_both_match(self):
        self.assertEqual(company.classify_company(warehouse="WH_TRXS_LOAD", database="ALFA_EDW_PROD"), "Trexis")

    def test_no_context_is_unclassified_not_alfa(self):
        self.assertEqual(company.classify_company(), config.UNCLASSIFIED_LABEL)
        self.assertEqual(company.classify_company(warehouse="", database=None, user=""), config.UNCLASSIFIED_LABEL)


class EnvironmentTests(unittest.TestCase):
    def test_alfa(self):
        self.assertEqual(company.classify_environment("ALFA_EDW_PROD"), "PROD")
        self.assertEqual(company.classify_environment("ALFA_EDW_MGM"), "PROD")
        self.assertEqual(company.classify_environment("ALFA_SANDBOX"), "DEV")

    def test_trexis(self):
        self.assertEqual(company.classify_environment("EDW_TRXS_PRD"), "PROD")
        self.assertEqual(company.classify_environment("EDW_TRXS_DEV"), "DEV")
        self.assertEqual(company.classify_environment("STAGE_SIT"), "DEV")

    def test_unknown(self):
        self.assertEqual(company.classify_environment(""), "UNKNOWN")
        self.assertEqual(company.classify_environment("RANDOM_DB"), "UNKNOWN")


class SqlGenerationTests(unittest.TestCase):
    def test_case_sql_labels(self):
        sql = company.company_case_sql()
        for tok in ["'Trexis'", "'ALFA'", "'Unclassified'", "AS COMPANY"]:
            self.assertIn(tok, sql)

    def test_trexis_predicate_literal(self):
        sql = company.trexis_predicate_sql()
        self.assertIn("STARTSWITH(UPPER(database_name), 'TRXS_')", sql)
        self.assertIn("CONTAINS(UPPER(database_name), '_TRXS_')", sql)
        self.assertIn("WH_TRXS_LOAD", sql)
        self.assertNotIn("LIKE", sql.upper())

    def test_scope_alfa_excludes_trexis(self):
        sql = company.company_scope_sql("ALFA")
        self.assertIn("NOT", sql)
        self.assertIn("IS NOT NULL", sql)

    def test_scope_all_empty(self):
        self.assertEqual(company.company_scope_sql("ALL"), "")
        self.assertEqual(company.company_scope_sql(""), "")

    def test_scope_trexis(self):
        sql = company.company_scope_sql("Trexis")
        self.assertTrue(sql.startswith("AND "))
        self.assertIn("WH_TRXS_LOAD", sql)


if __name__ == "__main__":
    unittest.main()
