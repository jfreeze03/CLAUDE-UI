"""Tests for Tier-1 security detections (pure logic + SQL shape)."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import security_intel as sec  # noqa: E402


class GrantSeverityTests(unittest.TestCase):
    def test_ownership_is_high(self):
        self.assertEqual(sec.grant_severity("OWNERSHIP", "TABLE", "T1"), "High")

    def test_admin_role_grant_is_high(self):
        self.assertEqual(sec.grant_severity("USAGE", "ROLE", "ACCOUNTADMIN"), "High")
        self.assertEqual(sec.grant_severity("USAGE", "ROLE", "securityadmin"), "High")

    def test_ordinary_grant_is_low(self):
        self.assertEqual(sec.grant_severity("USAGE", "WAREHOUSE", "WH_X"), "Low")
        self.assertEqual(sec.grant_severity("SELECT", "TABLE", "CUSTOMERS"), "Low")


class TakeoverSeverityTests(unittest.TestCase):
    def test_success_after_is_high(self):
        self.assertEqual(sec.takeover_severity(10, True), "High")

    def test_no_success_is_medium(self):
        self.assertEqual(sec.takeover_severity(10, False), "Medium")


class AssessTakeoverTests(unittest.TestCase):
    def test_empty_not_fired(self):
        self.assertFalse(sec.assess_takeover([]).get("fired"))

    def test_success_after_is_high_and_explains(self):
        r = sec.assess_takeover([
            {"USER": "ALICE", "FAILED_ATTEMPTS": 14, "FAIL_IPS": 3, "SUCCEEDED_AFTER": True,
             "LAST_ERROR": "INCORRECT_PASSWORD"},
        ])
        self.assertTrue(r["fired"])
        self.assertEqual(r["severity"], "High")
        self.assertEqual(r["compromised"], 1)
        self.assertIn("ALICE", r["alert"].detail)
        self.assertIn("SUCCESS", r["alert"].detail.upper())

    def test_fails_without_success_is_medium(self):
        r = sec.assess_takeover([
            {"USER": "BOB", "FAILED_ATTEMPTS": 8, "FAIL_IPS": 1, "SUCCEEDED_AFTER": False},
        ])
        self.assertTrue(r["fired"])
        self.assertEqual(r["severity"], "Medium")
        self.assertEqual(r["compromised"], 0)


class AssessGrantsTests(unittest.TestCase):
    def test_admin_grant_fires_high(self):
        r = sec.assess_grants([
            {"PRIVILEGE": "USAGE", "OBJECT_TYPE": "ROLE", "OBJECT": "ACCOUNTADMIN",
             "GRANTEE": "DEV1", "GRANTED_BY": "SYSADMIN"},
            {"PRIVILEGE": "SELECT", "OBJECT_TYPE": "TABLE", "OBJECT": "T", "GRANTEE": "R", "GRANTED_BY": "X"},
        ])
        self.assertTrue(r["fired"])
        self.assertEqual(r["severity"], "High")
        self.assertEqual(r["count"], 1)
        self.assertIn("ACCOUNTADMIN", r["alert"].detail)

    def test_only_ordinary_grants_not_fired_but_kept(self):
        r = sec.assess_grants([
            {"PRIVILEGE": "SELECT", "OBJECT_TYPE": "TABLE", "OBJECT": "T", "GRANTEE": "R", "GRANTED_BY": "X"},
        ])
        self.assertFalse(r["fired"])
        self.assertEqual(len(r["all_rows"]), 1)


class AssessOtherTests(unittest.TestCase):
    def test_single_factor_fires_medium(self):
        r = sec.assess_single_factor([{"USER": "C", "LOGINS": 3, "LAST_TIME": "2026-06-17 02:00"}])
        self.assertTrue(r["fired"])
        self.assertEqual(r["severity"], "Medium")

    def test_new_ip_fires_medium(self):
        r = sec.assess_new_ip([{"USER": "D", "NEW_IP": "8.8.8.8", "FIRST_SEEN": "2026-06-17 01:00", "LOGINS": 1}])
        self.assertTrue(r["fired"])
        self.assertEqual(r["severity"], "Medium")
        self.assertIn("8.8.8.8", r["alert"].detail)


class SqlShapeTests(unittest.TestCase):
    def test_takeover_sql(self):
        s = sec.takeover_candidates_sql(7, "ALFA")
        self.assertIn("COUNT_IF(is_success = 'NO')", s)
        self.assertIn("SUCCEEDED_AFTER", s)
        self.assertIn("HAVING COUNT_IF(is_success = 'NO') >=", s)

    def test_single_factor_sql(self):
        s = sec.single_factor_logins_sql(7, "ALFA")
        self.assertIn("first_authentication_factor = 'PASSWORD'", s)
        self.assertIn("COALESCE(second_authentication_factor, '') = ''", s)

    def test_new_ip_sql_is_antijoin(self):
        s = sec.new_ip_logins_sql(7, "ALFA")
        self.assertIn("LEFT JOIN historical", s)
        self.assertIn("WHERE h.client_ip IS NULL", s)

    def test_grants_sql(self):
        s = sec.privilege_grants_sql(7)
        self.assertIn("GRANTS_TO_ROLES", s)


if __name__ == "__main__":
    unittest.main()
