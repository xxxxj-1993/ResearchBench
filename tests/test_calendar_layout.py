from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CalendarLayoutTests(unittest.TestCase):
    def test_month_cells_share_grid_row_height(self):
        css = (ROOT / 'ui/_template.html').read_text(encoding='utf-8')
        self.assertIn('align-items:stretch', css)
        self.assertIn('height:100%;box-sizing:border-box', css)


if __name__ == '__main__':
    unittest.main()
