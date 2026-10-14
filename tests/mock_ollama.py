#!/usr/bin/env python3
"""Tiny fake Ollama /api/chat server so the LLM code path can be tested offline."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(n))
        text = body["messages"][-1]["content"].lower()
        cat = "Software" if "hosting" in text else "Travel" if "taxi" in text else "Hardware" if "keyboard" in text else "Meals"
        out = {"vendor": text.splitlines()[0].title(), "date": "2026-01-01", "total": 1.0,
               "currency": "LKR", "category": cat}
        payload = json.dumps({"message": {"role": "assistant", "content": json.dumps(out)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", 11555), H).serve_forever()
