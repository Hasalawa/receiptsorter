#!/usr/bin/env python3
"""ReceiptSorter web UI: a tiny local server around receiptsorter.py.

Runs on 127.0.0.1 only. Receipts are processed in memory and by your own
Ollama model; nothing is sent to any outside service. Standard library only.

    python3 webapp.py            # opens http://127.0.0.1:8765
"""
import argparse
import csv
import io
import json
import re
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import receiptsorter as rs

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
SAMPLES = HERE / "samples"
MAX_UPLOAD = 25 * 1024 * 1024
STORE = {}  # upload id -> {"name": str, "data": bytes}
STORE_LOCK = threading.Lock()


def ollama_status(host: str) -> dict:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as r:
            models = [m["name"] for m in json.load(r).get("models", [])]
        return {"online": True, "models": models}
    except Exception:
        return {"online": False, "models": []}


def extract(name: str, data: bytes, host: str, model: str, use_llm: bool) -> dict:
    suffix = Path(name).suffix.lower()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"upload{suffix}"
        path.write_bytes(data)
        text = rs.read_text(path)
    if not text.strip():
        return {"error": "No readable text. PDFs need poppler (pdftotext), photos need tesseract."}

    fields, source, note = None, "rules", ""
    if use_llm:
        try:
            fields, source = rs.ask_llm(text, host, model), model
        except Exception as e:
            note = f"Model unavailable ({e.__class__.__name__}); used basic rules."
    if fields is None:
        fields = rs.fallback_extract(text)
    if fields.get("category") not in rs.CATEGORIES:
        fields["category"] = "Other"
    try:
        fields["total"] = round(float(fields.get("total") or 0), 2)
    except (TypeError, ValueError):
        fields["total"] = 0.0

    uid = uuid.uuid4().hex[:10]
    with STORE_LOCK:
        STORE[uid] = {"name": name, "data": data}
    return {"id": uid, "file": name, "source": source, "note": note,
            **{k: fields.get(k, "") for k in ("vendor", "date", "total", "currency", "category")}}


def build_zip(rows: list) -> bytes:
    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["file", "vendor", "date", "total", "currency", "category"])
        for r in rows:
            w.writerow([r.get("file", ""), r.get("vendor", ""), r.get("date", ""),
                        r.get("total", ""), r.get("currency", ""), r.get("category", "")])
            with STORE_LOCK:
                item = STORE.get(r.get("id"))
            if not item:
                continue
            cat = r.get("category") if r.get("category") in rs.CATEGORIES else "Other"
            ext = Path(item["name"]).suffix.lower()
            base = f"{cat}/{rs.safe(str(r.get('date', 'undated')))}_{rs.safe(str(r.get('vendor', 'unknown')))}_{r.get('total', 0)}"
            arc = base + ext
            n = 2
            while arc in used:
                arc, n = f"{base}-{n}{ext}", n + 1
            used.add(arc)
            z.writestr(arc, item["data"])
        z.writestr("receipts.csv", out.getvalue())
    return buf.getvalue()


def make_server(port: int, ollama_host: str, model: str) -> ThreadingHTTPServer:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ReceiptSorter"

        def log_message(self, *a):
            pass

        # --- helpers -------------------------------------------------
        def _guard(self) -> bool:
            """Reject requests that did not come from this machine's own page."""
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(403, b"Forbidden", "text/plain")
                return False
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlparse(origin).netloc not in allowed_hosts:
                self._send(403, b"Forbidden", "text/plain")
                return False
            return True

        def _send(self, code, body: bytes, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                self._json({"error": "File too large (25 MB max)."}, 413)
                return None
            return self.rfile.read(n)

        # --- routes --------------------------------------------------
        def do_GET(self):
            if not self._guard():
                return
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif path == "/api/status":
                st = ollama_status(ollama_host)
                self._json({**st, "default_model": model, "categories": rs.CATEGORIES})
            elif path == "/api/samples":
                items = [{"name": p.name, "text": p.read_text()} for p in sorted(SAMPLES.glob("*.txt"))]
                self._json(items)
            else:
                self._send(404, b"Not found", "text/plain")

        def do_POST(self):
            if not self._guard():
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/extract":
                data = self._body()
                if data is None:
                    return
                name = urllib.parse.unquote(self.headers.get("X-Filename", "receipt.txt"))
                name = re.sub(r"[\\/]", "_", name)[:120]
                chosen = urllib.parse.unquote(self.headers.get("X-Model", "")) or model
                use_llm = self.headers.get("X-Use-Llm", "1") == "1"
                self._json(extract(name, data, ollama_host, chosen, use_llm))
            elif parsed.path == "/api/zip":
                data = self._body()
                if data is None:
                    return
                try:
                    rows = json.loads(data)["rows"]
                except Exception:
                    self._json({"error": "Bad request."}, 400)
                    return
                self._send(200, build_zip(rows), "application/zip",
                           {"Content-Disposition": 'attachment; filename="sorted-receipts.zip"'})
            elif parsed.path == "/api/reset":
                with STORE_LOCK:
                    STORE.clear()
                self._json({"ok": True})
            else:
                self._send(404, b"Not found", "text/plain")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--ollama", default="http://localhost:11434", help="Ollama URL")
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    a = ap.parse_args()
    srv = make_server(a.port, a.ollama, a.model)
    url = f"http://127.0.0.1:{a.port}"
    print(f"ReceiptSorter is running at {url}  (Ctrl+C to stop)")
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
