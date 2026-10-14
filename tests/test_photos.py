"""Photo receipts: a local vision model reads them directly, with clear errors when it can't."""
import base64
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import receiptsorter as rs  # noqa: E402
import webapp  # noqa: E402

FIELDS = {"vendor": "Dumindu Hotels", "date": "2026-10-04", "total": 3000, "currency": "LKR", "category": "Other"}
PHOTO = b"\xff\xd8not-a-real-jpeg"  # bytes only need to round-trip, not decode


def fake_ollama(mode="ok"):
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            if mode == "missing":
                payload = json.dumps({"error": f"model '{body['model']}' not found"}).encode()
                self.send_response(404)
            else:
                payload = json.dumps({"message": {"content": json.dumps(FIELDS)}}).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}", seen


class PhotoTests(unittest.TestCase):
    def photo(self, td):
        p = Path(td) / "IMG_1.jpg"
        p.write_bytes(PHOTO)
        return p

    def test_vision_model_receives_the_image(self):
        srv, host, seen = fake_ollama()
        try:
            with tempfile.TemporaryDirectory() as td:
                r = rs.extract_document(self.photo(td), host, "text-m", "vision-m", True)
        finally:
            srv.shutdown()
        self.assertEqual(r["source"], "vision-m")
        self.assertEqual(r["fields"]["total"], 3000.0)
        msg = seen[0]["messages"][-1]
        self.assertEqual(seen[0]["model"], "vision-m")
        self.assertEqual(base64.b64decode(msg["images"][0]), PHOTO)

    def test_missing_vision_model_gives_pull_command(self):
        srv, host, _ = fake_ollama("missing")
        try:
            with tempfile.TemporaryDirectory() as td:
                r = rs.extract_document(self.photo(td), host, "text-m", "vision-m", True)
        finally:
            srv.shutdown()
        self.assertIn("error", r)
        self.assertIn("ollama pull vision-m", r["error"])

    def test_photo_without_any_reader_explains_what_to_install(self):
        with tempfile.TemporaryDirectory() as td:
            r = rs.extract_document(self.photo(td), "http://127.0.0.1:1", "t", "vision-m", False)
        self.assertIn("error", r)
        self.assertIn("ollama pull vision-m", r["error"])

    def test_total_regex_handles_wide_spacing_and_ignores_cash(self):
        text = "Hotel\nDATE: 2026-10-04\nSubtotal 500.00\nTOTAL BILL        3,000.00\nCASH 5,000.00\nCHANGE 2,000.00"
        self.assertEqual(rs.fallback_extract(text)["total"], 3000.0)


class WebPhotoTests(unittest.TestCase):
    def test_web_extract_uses_vision_header(self):
        srv, host, seen = fake_ollama()
        web = webapp.make_server(18766, host, "text-m", "default-vision")
        threading.Thread(target=web.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:18766/api/extract", data=PHOTO,
                headers={"X-Filename": "bill.jpg", "X-Vision-Model": "my-vl"})
            row = json.load(urllib.request.urlopen(req))
        finally:
            web.shutdown(); web.server_close(); srv.shutdown()
        self.assertEqual(row["source"], "my-vl")
        self.assertEqual(row["vendor"], "Dumindu Hotels")
        self.assertEqual(seen[0]["model"], "my-vl")


if __name__ == "__main__":
    unittest.main()
