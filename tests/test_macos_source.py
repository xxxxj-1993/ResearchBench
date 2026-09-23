# -*- coding: utf-8 -*-
import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MacOSSourceTests(unittest.TestCase):
    def test_spec_parses_as_python(self):
        ast.parse((ROOT / "ResearchBench-macOS.spec").read_text(encoding="utf-8"))

    def test_build_script_has_unix_line_endings_and_required_steps(self):
        raw = (ROOT / "macos" / "build_macos.sh").read_bytes()
        self.assertNotIn(b"\r\n", raw)
        text = raw.decode("utf-8")
        for command in ("sips -s format png", "iconutil -c icns", "PyInstaller",
                        "codesign", "hdiutil create"):
            self.assertIn(command, text)

    def test_workflow_targets_macos(self):
        text = (ROOT / ".github" / "workflows" / "build-macos.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: macos-14", text)
        self.assertIn("macos/build_macos.sh", text)
        self.assertIn("ResearchBench-v2.4.5-macOS.dmg", text)


if __name__ == "__main__":
    unittest.main()
