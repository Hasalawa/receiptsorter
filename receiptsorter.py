#!/usr/bin/env python3
"""ReceiptSorter: sort receipts and invoices locally with an open-weight model.

Everything runs on your machine. Text is extracted from receipts (txt, pdf, images),
a local model served by Ollama pulls out vendor / date / total / category, and the
results go to a CSV plus a tidy renamed folder structure. No cloud API involved.

Core uses only the Python standard library. Optional helpers:
  pdftotext (poppler)  -> PDF text
  pytesseract + Pillow -> image OCR
  Ollama               -> the local LLM (falls back to regex if unreachable)
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

CATEGORIES = ["Software", "Travel", "Meals", "Office", "Hardware", "Utilities", "Marketing", "Other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "total": {"type": "number"},
        "currency": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
    },
    "required": ["vendor", "date", "total", "currency", "category"],
}

PROMPT = (
    "You read receipt/invoice text and extract fields. Return JSON only. "
    "date must be YYYY-MM-DD. total is the final amount paid as a number. "
    "currency is a 3-letter code (LKR, USD, ...). "
    f"category must be one of: {', '.join(CATEGORIES)}."
)


def read_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return path.read_text(errors="ignore")
    if ext == ".pdf":
        if shutil.which("pdftotext"):
            return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                  capture_output=True, text=True).stdout
        return ""
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(path))
        except ImportError:
            print(f"  ! OCR deps missing for {path.name} (pip install pytesseract pillow)", file=sys.stderr)
    return ""


def ask_llm(text: str, host: str, model: str, timeout: int = 120):
    body = {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": text[:6000]},
        ],
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(f"{host}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        content = json.load(r)["message"]["content"]
    return json.loads(content)


MONEY = re.compile(r"(?:total|amount due|grand total|balance)\D{0,15}([\d,]+\.?\d*)", re.I)
DATE = re.compile(r"(\d{4}-\d{2}-\d{2})|(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
CURR = re.compile(r"\b(LKR|USD|EUR|GBP|INR|Rs\.?)\b|\$", re.I)
KEYWORDS = {
    "Software": ["subscription", "hosting", "license", "saas", "domain", "cloud"],
    "Travel": ["uber", "pickme", "flight", "hotel", "taxi", "train", "fuel"],
    "Meals": ["restaurant", "cafe", "coffee", "lunch", "dinner"],
    "Office": ["stationery", "paper", "printer", "desk"],
    "Hardware": ["laptop", "keyboard", "monitor", "ssd", "cable"],
    "Utilities": ["electricity", "internet", "broadband", "water", "mobile bill"],
    "Marketing": ["ads", "advert", "promotion", "facebook", "google ads"],
}


def fallback_extract(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    vendor = lines[0][:60] if lines else "Unknown"
    m = DATE.search(text)
    date = "unknown"
    if m:
        date = m.group(1) or f"{m.group(4)}-{int(m.group(3)):02d}-{int(m.group(2)):02d}"
    amounts = MONEY.findall(text)
    total = float(amounts[-1].replace(",", "")) if amounts else 0.0
    c = CURR.search(text)
    currency = "USD" if c and c.group(0) == "$" else (c.group(0).upper().rstrip(".").replace("RS", "LKR") if c else "LKR")
    low = text.lower()
    category = next((cat for cat, kws in KEYWORDS.items() if any(k in low for k in kws)), "Other")
    return {"vendor": vendor, "date": date, "total": total, "currency": currency, "category": category}


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:30] or "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, default=Path("sorted"))
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--no-llm", action="store_true", help="regex only, no model")
    ap.add_argument("--move-files", action="store_true", help="copy originals into category folders")
    a = ap.parse_args()

    files = sorted(p for p in a.folder.iterdir() if p.is_file())
    rows, use_llm = [], not a.no_llm
    a.out.mkdir(parents=True, exist_ok=True)

    for p in files:
        text = read_text(p)
        if not text.strip():
            print(f"- {p.name}: no text found, skipped")
            continue
        data, source = None, "regex"
        if use_llm:
            try:
                data, source = ask_llm(text, a.host, a.model), a.model
            except Exception as e:
                print(f"  ! LLM unavailable ({e.__class__.__name__}), using regex fallback", file=sys.stderr)
                use_llm = False
        if data is None:
            data = fallback_extract(text)
        if data.get("category") not in CATEGORIES:
            data["category"] = "Other"
        data.update(file=p.name, source=source)
        rows.append(data)
        print(f"- {p.name}: {data['vendor']} | {data['date']} | {data['total']} {data['currency']} | {data['category']}")
        if a.move_files:
            dest = a.out / data["category"]
            dest.mkdir(exist_ok=True)
            shutil.copy2(p, dest / f"{data['date']}_{safe(data['vendor'])}_{data['total']}{p.suffix}")

    csv_path = a.out / "receipts.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "vendor", "date", "total", "currency", "category", "source"])
        w.writeheader()
        w.writerows(rows)

    totals = defaultdict(float)
    for r in rows:
        totals[(r["category"], r["currency"])] += float(r["total"])
    print(f"\nWrote {csv_path} ({len(rows)} receipts)")
    for (cat, cur), t in sorted(totals.items()):
        print(f"  {cat:<10} {t:>12,.2f} {cur}")


if __name__ == "__main__":
    main()
