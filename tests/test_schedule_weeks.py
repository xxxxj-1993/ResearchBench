import unittest

import app


class ScheduleWeekParsingTests(unittest.TestCase):
    def test_mixed_single_week_and_range_with_chinese_comma(self):
        parsed = app.parse_class_cell("大学物理 10，12-14周 学海101 张老师")
        self.assertEqual(parsed["weeks"], "10,12-14")

    def test_english_comma_and_common_separators(self):
        cases = {
            "课程A 10,12-14周": "10,12-14",
            "课程A 第10、12～14周": "10,12-14",
            "课程A 10；12至14周": "10,12-14",
            "课程A 10;12—14周": "10,12-14",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(app.parse_class_cell(raw)["weeks"], expected)

    def test_multiple_week_phrases_are_combined(self):
        parsed = app.parse_class_cell("大学物理 理论1-8周 实验10，12-14周")
        self.assertEqual(parsed["weeks"], "1-8,10,12-14")
        self.assertEqual(parsed["note"], "实验")

    def test_schedule_import_keeps_complete_mixed_weeks(self):
        html = (
            "<table><tr><td></td><td>1 2</td></tr>"
            "<tr><td>星期一</td><td>大学物理 10，12-14周 学海101 张</td></tr></table>"
        )
        items, layout, msg = app.parse_schedule_payload(html_text=html)
        self.assertEqual((msg, layout, len(items)), ("", "A", 1))
        self.assertEqual(items[0]["weeks"], "10,12-14")


if __name__ == "__main__":
    unittest.main()
