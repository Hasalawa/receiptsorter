import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import webapp  # noqa: E402

PORT = 18765


class WebAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = webapp.make_server(PORT, "http://127.0.0.1:1", "test-model")
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{PORT}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, path, data=None, headers=None):
        r = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read()

    def test_index_served(self):
        status, body = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn(b"ReceiptSorter", body)

    def test_status_reports_offline_model(self):
        _, body = self.req("/api/status")
        st = json.loads(body)
        self.assertFalse(st["online"])
        self.assertEqual(st["default_model"], "test-model")

    def test_extract_and_zip_roundtrip(self):
        text = (ROOT / "samples" / "hosting.txt").read_bytes()
        _, body = self.req("/api/extract", text, {"X-Filename": "hosting.txt", "X-Use-Llm": "0"})
        row = json.loads(body)
        self.assertEqual(row["category"], "Software")
        self.assertEqual(row["total"], 8500.0)
        row["category"] = "Office"  # user edit in the UI
        _, z = self.req("/api/zip", json.dumps({"rows": [row]}).encode())
        names = zipfile.ZipFile(io.BytesIO(z)).namelist()
        self.assertIn("receipts.csv", names)
        self.assertTrue(any(n.startswith("Office/") for n in names))

    def test_extract_empty_file_gives_error(self):
        _, body = self.req("/api/extract", b"", {"X-Filename": "blank.txt"})
        self.assertIn("error", json.loads(body))

    def test_foreign_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("/api/status", headers={"Host": "evil.example"})
        self.assertEqual(cm.exception.code, 403)

    def test_foreign_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("/api/reset", b"", {"Origin": "http://evil.example"})
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
