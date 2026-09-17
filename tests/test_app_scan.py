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
