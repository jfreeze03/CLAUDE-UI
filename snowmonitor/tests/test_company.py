"""Tests for company segregation — the heart of the tool.

Verifies the Python classifier and that the SQL generators emit the right literal
predicates. ALFA must be the default catch-all; Trexis must win when both match;
no-context rows must be Unclassified (never silently ALFA).
"""

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
        # Has context, not Trexis -> ALFA.
        self.assertEqual(company.classify_company(database="ALFA_EDW_PROD"), "ALFA")
        self.assertEqual(company.classify_company(warehouse="SOME_SHARED_WH"), "ALFA")
        self.assertEqual(company.classify_company(user="ANALYST_1"), "ALFA")

    def test_default_company_is_alfa(self):
        self.assertEqual(config.DEFAULT_COMPANY, "ALFA")

    def test_trexis_wins_when_both_match(self):
        # Trexis warehouse with an ALFA database must resolve to Trexis.
        self.assertEqual(
            company.classify_company(warehouse="WH_TRXS_LOAD", database="ALFA_EDW_PROD"),
            "Trexis",
        )

    def test_no_context_is_unclassified_not_alfa(self):
        self.assertEqual(company.classify_company(), config.UNCLASSIFIED_LABEL)
        self.assertEqual(company.classify_company(warehouse="", database=None, user=""), config.UNCLASSIFIED_LABEL)


class EnvironmentTests(unittest.TestCase):
    def test_alfa_prod_and_dev(self):
        self.assertEqual(company.classify_environment("ALFA_EDW_PROD"), "PROD")
        self.assertEqual(company.classify_environment("ALFA_EDW_MGM"), "PROD")
        self.assertEqual(company.classify_environment("ALFA_SANDBOX"), "DEV")

    def test_trexis_prod_and_dev(self):
        self.assertEqual(company.classify_environment("EDW_TRXS_PRD"), "PROD")
        self.assertEqual(company.classify_environment("EDW_TRXS_DEV"), "DEV")
        self.assertEqual(company.classify_environment("STAGE_SIT"), "DEV")

    def test_unknown(self):
        self.assertEqual(company.classify_environment(""), "UNKNOWN")
        self.assertEqual(company.classify_environment("RANDOM_DB"), "UNKNOWN")


class SqlGenerationTests(unittest.TestCase):
    def test_case_sql_has_all_three_labels(self):
        sql = company.company_case_sql()
        self.assertIn("'Trexis'", sql)
        self.assertIn("'ALFA'", sql)
        self.assertIn("'Unclassified'", sql)
        self.assertIn("AS COMPANY", sql)

    def test_trexis_predicate_uses_literal_matchers(self):
        sql = company.trexis_predicate_sql()
        self.assertIn("STARTSWITH(UPPER(database_name), 'TRXS_')", sql)
        self.assertIn("CONTAINS(UPPER(database_name), '_TRXS_')", sql)
        self.assertIn("WH_TRXS_LOAD", sql)
        # No LIKE wildcards — literal matching only.
        self.assertNotIn("LIKE", sql.upper())

    def test_scope_sql_alfa_excludes_trexis_and_requires_context(self):
        sql = company.company_scope_sql("ALFA")
        self.assertIn("NOT", sql)
        self.assertIn("IS NOT NULL", sql)

    def test_scope_sql_all_is_empty(self):
        self.assertEqual(company.company_scope_sql("ALL"), "")
        self.assertEqual(company.company_scope_sql(""), "")

    def test_scope_sql_trexis_is_predicate(self):
        sql = company.company_scope_sql("Trexis")
        self.assertTrue(sql.startswith("AND "))
        self.assertIn("WH_TRXS_LOAD", sql)

    def test_environment_case_sql(self):
        sql = company.environment_case_sql()
        self.assertIn("'PROD'", sql)
        self.assertIn("'DEV'", sql)
        self.assertIn("ENDSWITH(UPPER(database_name), '_PRD')", sql)


if __name__ == "__main__":
    unittest.main()
