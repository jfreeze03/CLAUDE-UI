"""Tests for Cost page UI navigation behavior."""

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sections import cost  # noqa: E402


class CostUiTests(unittest.TestCase):
    def test_cost_views_have_registered_renderers(self):
        self.assertEqual(tuple(cost._VIEW_RENDERERS.keys()), cost.COST_VIEW_LABELS)

    def test_active_view_invokes_only_selected_renderer(self):
        calls = []
        original = cost._VIEW_RENDERERS.copy()
        try:
            cost._VIEW_RENDERERS.clear()
            cost._VIEW_RENDERERS.update(
                {
                    label: (lambda company, days, use_mart, label=label: calls.append(label))
                    for label in cost.COST_VIEW_LABELS
                }
            )

            cost._render_active_view("Storage", "ALFA", 7, False)

            self.assertEqual(calls, ["Storage"])
        finally:
            cost._VIEW_RENDERERS.clear()
            cost._VIEW_RENDERERS.update(original)


if __name__ == "__main__":
    unittest.main()
