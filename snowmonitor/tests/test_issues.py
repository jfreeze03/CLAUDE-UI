"""Tests for the unified issue feed ranking/counting (pure)."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from lib import issues  # noqa: E402
from lib.alerts import Alert, REACTIVE  # noqa: E402


def _a(sev, domain, title):
    return Alert(sev, REACTIVE, domain, title, "why", "v", "t", "act")


class RankTests(unittest.TestCase):
    def test_orders_by_severity_then_domain(self):
        items = [_a("Medium", "Cost", "b"), _a("Critical", "Security", "a"),
                 _a("High", "Tasks", "c"), _a("Medium", "Cost", "a")]
        ranked = issues.rank(items)
        self.assertEqual([r.severity for r in ranked], ["Critical", "High", "Medium", "Medium"])
        # within Medium/Cost, title 'a' before 'b'
        meds = [r.title for r in ranked if r.severity == "Medium"]
        self.assertEqual(meds, ["a", "b"])

    def test_counts(self):
        items = [_a("Critical", "X", "1"), _a("High", "Y", "2"), _a("High", "Z", "3")]
        c = issues.counts(items)
        self.assertEqual(c["Critical"], 1)
        self.assertEqual(c["High"], 2)
        self.assertEqual(c["total"], 3)

    def test_empty(self):
        self.assertEqual(issues.rank([]), [])
        self.assertEqual(issues.counts([])["total"], 0)


if __name__ == "__main__":
    unittest.main()
