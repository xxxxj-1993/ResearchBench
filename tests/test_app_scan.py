# -*- coding: utf-8 -*-
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.request import Request, urlopen

import app


class AppScanTests(unittest.TestCase):
    def test_macos_appdata_uses_application_support(self):
        with mock.patch.object(app, "IS_MAC", True), \
                mock.patch("app.os.path.expanduser", side_effect=lambda p: p.replace("~", "/Users/tester", 1)), \
                mock.patch("app.os.makedirs") as makedirs:
            self.assertEqual("/Users/tester/Library/Application Support/ResearchWorkbench",
                             app.appdata_dir())
        makedirs.assert_called_once_with(
            "/Users/tester/Library/Application Support/ResearchWorkbench", exist_ok=True)

    def test_macos_scan_returns_app_bundles(self):
        def fake_glob(pattern, recursive=False):
            return ["/Applications/Zotero.app"] if "Zotero.app" in pattern else []

        with mock.patch("app.glob.glob", side_effect=fake_glob), \
                mock.patch("app.os.path.isdir", side_effect=lambda p: p == "/Applications/Zotero.app"):
            found = app.mac_default_apps()
        self.assertEqual(1, len(found))
        self.assertEqual(("Zotero", "app", "/Applications/Zotero.app"),
                         (found[0]["name"], found[0]["kind"], found[0]["target"]))

    def test_macos_launch_uses_open_app(self):
        with mock.patch.object(app, "IS_MAC", True), \
                mock.patch("app.os.path.isdir", return_value=True), \
                mock.patch("app.os.path.isfile", return_value=False), \
                mock.patch("app.subprocess.Popen") as popen:
            popen.return_value.pid = 1
            ok, _ = app.launch_app("/Applications/Zotero.app")
        self.assertTrue(ok)
        self.assertEqual(["open", "/Applications/Zotero.app"], popen.call_args.args[0])

    def test_macos_reveal_uses_finder(self):
        with mock.patch.object(app, "IS_MAC", True), \
                mock.patch("app.os.path.exists", return_value=True), \
                mock.patch("app.os.path.isfile", return_value=True), \
                mock.patch("app.subprocess.Popen") as popen:
            ok, _ = app.reveal_in_explorer("/Users/tester/paper.pdf")
        self.assertTrue(ok)
        self.assertEqual(["open", "-R", "/Users/tester/paper.pdf"], popen.call_args.args[0])

    def test_macos_picker_can_choose_application_bundle(self):
        done = mock.Mock(returncode=0, stdout="/Applications/Zotero.app\n")
        with mock.patch.object(app, "IS_MAC", True), \
                mock.patch("app.subprocess.run", return_value=done) as run:
            ok, path = app.native_pick("app")
        self.assertTrue(ok)
        self.assertEqual("/Applications/Zotero.app", path)
        self.assertIn("choose application", run.call_args.args[0][-1])

    def test_fresh_load_does_not_scan_implicitly(self):
        with tempfile.TemporaryDirectory() as td:
            data_file = os.path.join(td, "data.json")
            old_launch = os.path.join(td, "launch.json")
            with mock.patch.object(app, "DATA_FILE", data_file), \
                    mock.patch.object(app, "OLD_LAUNCH", old_launch), \
                    mock.patch.object(app, "default_apps") as scan:
                db = app.load_db()
            scan.assert_not_called()
            self.assertEqual(["桌面"], [x["name"] for x in db["shortcuts"]])

    def test_first_manual_scan_adds_and_second_scan_reports_existing(self):
        fresh = [{"id": "x", "category": "文献写作", "name": "Zotero",
                  "kind": "app", "target": r"C:\Program Files\Zotero\zotero.exe", "note": ""}]
        db = {"shortcuts": []}
        first = app.merge_scanned_shortcuts(db, fresh)
        second = app.merge_scanned_shortcuts(db, fresh)
        self.assertEqual(1, first["foundApps"])
        self.assertEqual(1, first["addedApps"])
        self.assertEqual(1, len(first["added"]))
        self.assertEqual(1, second["foundApps"])
        self.assertEqual(0, second["addedApps"])
        self.assertEqual([], second["added"])
        self.assertEqual(1, len(db["shortcuts"]))

    def test_empty_scan_is_distinct_from_all_existing(self):
        result = app.merge_scanned_shortcuts({"shortcuts": []}, [])
        self.assertEqual(0, result["foundApps"])
        self.assertEqual(0, result["total"])

    def test_registry_path_accepts_display_icon_and_uninstall_string(self):
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "Research App.exe")
            with open(exe, "wb") as f:
                f.write(b"MZ")
            self.assertEqual(os.path.normpath(td), app._registry_path('"%s",0' % exe))
            self.assertEqual(os.path.normpath(td), app._registry_path('"%s" /uninstall' % exe))

    def test_rescan_http_endpoint_reports_added_then_existing(self):
        fresh = [{"id": "x", "category": "文献写作", "name": "Zotero",
                  "kind": "app", "target": r"C:\Program Files\Zotero\zotero.exe", "note": ""}]
        old_db = app.STATE.get("db")
        app.STATE["db"] = {"shortcuts": []}
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:%d/api/rescan" % server.server_address[1]
        try:
            with mock.patch.object(app, "default_apps", return_value=fresh), \
                    mock.patch.object(app, "save_db"):
                def post():
                    req = Request(url, data=b"{}", headers={"Content-Type": "application/json"})
                    return json.loads(urlopen(req, timeout=3).read().decode("utf-8"))

                first = post()
                second = post()
        finally:
            server.shutdown()
            server.server_close()
            app.STATE["db"] = old_db
        self.assertEqual((1, 1), (first["foundApps"], first["addedApps"]))
        self.assertEqual((1, 0), (second["foundApps"], second["addedApps"]))


if __name__ == "__main__":
    unittest.main()
