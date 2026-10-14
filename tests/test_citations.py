# -*- coding: utf-8 -*-
"""引用次数更新：纯本地单元测试，不访问 OpenAlex。"""
import json
import os
import sys
import unittest
import threading
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["RB_NO_WINDOW"] = "1"

import app  # noqa: E402


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class CitationTests(unittest.TestCase):
    def test_normalize_doi(self):
        self.assertEqual(app.normalize_doi("https://doi.org/10.1000/ABC.12"), "10.1000/abc.12")
        self.assertEqual(app.normalize_doi("doi: 10.5555/Test;"), "10.5555/test")
        self.assertEqual(app.normalize_doi("not a doi"), "")

    def test_openalex_result(self):
        payload = {"id": "https://openalex.org/W123", "doi": "https://doi.org/10.1000/test",
                   "cited_by_count": 37}
        with patch.object(app, "urlopen", return_value=_Response(payload)) as mocked:
            result = app.openalex_citation("10.1000/test")
        self.assertTrue(result["ok"])
        self.assertEqual(result["cites"], 37)
        self.assertEqual(result["openalexId"], "W123")
        self.assertIn("api.openalex.org/works/https://doi.org/10.1000/test", mocked.call_args.args[0].full_url)

    def test_batch_deduplicates_and_isolates_failures(self):
        calls = []

        def fake_fetch(doi):
            calls.append(doi)
            if doi == "10.1000/good":
                return {"ok": True, "cites": 9, "openalexId": "W9"}
            return {"ok": False, "msg": "未收录"}

        result = app.refresh_citation_counts([
            {"id": "a", "doi": "10.1000/good"},
            {"id": "b", "doi": "https://doi.org/10.1000/GOOD"},
            {"id": "c", "doi": "10.1000/missing"},
            {"id": "d", "doi": ""},
        ], fetcher=fake_fetch)

        self.assertEqual(sorted(calls), ["10.1000/good", "10.1000/missing"])
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["failedCount"], 1)
        self.assertEqual(result["skippedCount"], 1)
        self.assertEqual({x["id"] for x in result["results"]}, {"a", "b"})
        self.assertTrue(all(x["cites"] == 9 and x["source"] == "OpenAlex"
                            for x in result["results"]))

    def test_http_endpoint(self):
        reply = {"ok": True, "results": [{"id": "p1", "cites": 12}],
                 "updated": 1, "failedCount": 0, "skippedCount": 0}
        with patch.object(app, "refresh_citation_counts", return_value=reply) as mocked:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps({"items": [{"id": "p1", "doi": "10.1000/test"}]}).encode("utf-8")
                request = urllib.request.Request(
                    "http://127.0.0.1:%d/api/citations/refresh" % httpd.server_port,
                    data=body, headers={"Content-Type": "application/json"})
                result = json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))
            finally:
                httpd.shutdown()
                httpd.server_close()
        self.assertEqual(result["results"][0]["cites"], 12)
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
