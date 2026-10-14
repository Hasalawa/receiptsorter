import csv
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import receiptsorter as rs  # noqa: E402

SAMPLES = ROOT / "samples"


class FakeOllama(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        out = {"vendor": "Fake Vendor", "date": "2026-01-01", "total": 12.5,
               "currency": "USD", "category": "Software"}
        payload = json.dumps({"message": {"content": json.dumps(out)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


class RegexTests(unittest.TestCase):
    def test_hosting(self):
        d = rs.fallback_extract((SAMPLES / "hosting.txt").read_text())
        self.assertEqual(d["date"], "2026-09-03")
        self.assertEqual(d["total"], 8500.0)
        self.assertEqual(d["category"], "Software")

    def test_dd_mm_yyyy_date(self):
        d = rs.fallback_extract((SAMPLES / "taxi.txt").read_text())
        self.assertEqual(d["date"], "2026-09-12")
        self.assertEqual(d["category"], "Travel")

    def test_unknown_text(self):
        d = rs.fallback_extract("hello")
        self.assertEqual(d["category"], "Other")
        self.assertEqual(d["total"], 0.0)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "receiptsorter.py"), *args],
                              capture_output=True, text=True)

    def test_no_llm_creates_csv_and_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli(str(SAMPLES), "--no-llm", "--out", tmp, "--move-files")
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = list(csv.DictReader(open(Path(tmp) / "receipts.csv")))
            self.assertEqual(len(rows), 4)
            self.assertTrue((Path(tmp) / "Hardware").is_dir())

    def test_llm_unreachable_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli(str(SAMPLES), "--host", "http://127.0.0.1:1", "--out", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = list(csv.DictReader(open(Path(tmp) / "receipts.csv")))
            self.assertTrue(all(x["source"] == "regex" for x in rows))


class LlmPathTests(unittest.TestCase):
    def test_ask_llm_parses_structured_json(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            data = rs.ask_llm("anything", f"http://127.0.0.1:{srv.server_port}", "m")
        finally:
            srv.shutdown()
        self.assertEqual(data["vendor"], "Fake Vendor")
        self.assertEqual(data["total"], 12.5)


if __name__ == "__main__":
    unittest.main()
