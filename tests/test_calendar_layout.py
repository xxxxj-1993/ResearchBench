# -*- coding: utf-8 -*-
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CalendarLayoutTests(unittest.TestCase):
    def test_calendar_tracks_can_shrink_without_header_drift(self):
        path = os.path.join(ROOT, "ui", "_template.html")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn(".cal-dow{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))", source)
        self.assertIn(".cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))", source)
        self.assertIn(".cal-cell{min-height:104px", source)
        self.assertIn("min-width:0;padding:6px 6px 8px", source)
        self.assertNotIn(".cal-grid{display:grid;grid-template-columns:repeat(7,1fr)", source)


if __name__ == "__main__":
    unittest.main()
