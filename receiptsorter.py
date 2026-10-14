#!/usr/bin/env python3
"""ReceiptSorter: sort receipts and invoices locally with open-weight models.

Everything runs on your machine. Receipts (txt, pdf, photos) are read by models
served by Ollama, and the results go to a CSV plus a tidy folder structure.
No cloud API involved.

  Text / PDF receipts -> a text model   (default qwen2.5:7b-instruct)
  Photos of receipts  -> a vision model (default qwen2.5vl:7b), no OCR needed

Core uses only the Python standard library. Optional helpers:
  pdftotext (poppler)  -> PDF text
  Pillow               -> shrinks big phone photos so the vision model is faster
  pytesseract + tesseract -> OCR fallback for photos when no vision model is installed
"""
import argparse
import base64
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_CURRENCY = "LKR"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
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

RULES = (
    "Return JSON only. vendor is the business name. date must be YYYY-MM-DD. "
    "total is the final amount payable (e.g. TOTAL BILL or grand total), never the cash handed over or the change. "
    f"currency is a 3-letter code; if none is shown use {DEFAULT_CURRENCY}. "
    f"category must be one of: {', '.join(CATEGORIES)}."
)
PROMPT = "You read receipt/invoice text and extract fields. " + RULES
VISION_PROMPT = "This is a photo of a receipt or invoice. Read it and extract fields. " + RULES


# ---------------------------------------------------------------- reading input
def read_text(path: Path) -> str:
    """Plain text for txt/pdf, OCR text for photos (needs pytesseract). Never raises."""
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return path.read_text(errors="ignore")
        if ext == ".pdf":
            if shutil.which("pdftotext"):
                return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                      capture_output=True, text=True).stdout
            return ""
        if ext in IMAGE_EXTS:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(path))
    except Exception:
        return ""
    return ""


def shrink_image(data: bytes, max_side: int = 1600) -> bytes:
    """Fix phone-photo rotation and downscale so a CPU vision model stays quick."""
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if max(im.size) <= max_side and im.format in ("JPEG", "PNG"):
            return data
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return data


# ---------------------------------------------------------------- talking to Ollama
def _chat(host: str, body: dict, timeout: int):
    req = urllib.request.Request(f"{host}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(json.load(r)["message"]["content"])


def ask_llm(text: str, host: str, model: str, timeout: int = 120):
    return _chat(host, {
        "model": model, "stream": False, "format": SCHEMA, "options": {"temperature": 0},
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": text[:6000]}],
    }, timeout)


def ask_vision(image: bytes, host: str, model: str, timeout: int = 300):
    b64 = base64.b64encode(shrink_image(image)).decode()
    return _chat(host, {
        "model": model, "stream": False, "format": SCHEMA, "options": {"temperature": 0},
        "messages": [{"role": "user", "content": VISION_PROMPT, "images": [b64]}],
    }, timeout)


def ollama_up(host: str) -> bool:
    try:
        urllib.request.urlopen(f"{host}/api/tags", timeout=2).close()
        return True
    except Exception:
        return False


def explain_error(e: Exception, model: str) -> str:
    """Turn a failed model call into something a non-technical user can act on."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = json.loads(e.read()).get("error", "")
        except Exception:
            detail = ""
        if e.code == 404 or "not found" in detail.lower():
            return f"The model '{model}' isn't installed. Run: ollama pull {model}"
        return f"Ollama returned an error ({e.code}). {detail[:120]}".strip()
    if isinstance(e, urllib.error.URLError):
        return "Couldn't reach Ollama. Is it running?"
    if isinstance(e, TimeoutError):
        return f"'{model}' took too long. A smaller model may be faster on this computer."
    return f"The model gave an unusable answer ({e.__class__.__name__})."


# ---------------------------------------------------------------- basic rules (no model)
MONEY = re.compile(r"\b(?:grand total|total bill|total|amount due|balance due)\b[^\d\n]{0,40}(\d[\d,]*\.?\d*)", re.I)
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
    try:
        total = float(amounts[-1].replace(",", "")) if amounts else 0.0
    except ValueError:
        total = 0.0
    c = CURR.search(text)
    if not c:
        currency = DEFAULT_CURRENCY
    elif c.group(0) == "$":
        currency = "USD"
    else:
        currency = c.group(0).upper().rstrip(".").replace("RS", "LKR")
    low = text.lower()
    category = next((cat for cat, kws in KEYWORDS.items() if any(k in low for k in kws)), "Other")
    return {"vendor": vendor, "date": date, "total": total, "currency": currency, "category": category}


def normalize(fields: dict) -> dict:
    out = {k: fields.get(k, "") for k in ("vendor", "date", "total", "currency", "category")}
    if out["category"] not in CATEGORIES:
        out["category"] = "Other"
    try:
        out["total"] = round(float(out["total"] or 0), 2)
    except (TypeError, ValueError):
        out["total"] = 0.0
    return out


# ---------------------------------------------------------------- one entry point for CLI and web
def extract_document(path: Path, host: str, model: str, vision_model: str, use_llm: bool) -> dict:
    """Return {"fields", "source", "note"} on success, or {"error"} if nothing could be read."""
    ext, note = path.suffix.lower(), ""
    is_photo = ext in IMAGE_EXTS

    if is_photo and use_llm:  # the model looks at the photo directly
        try:
            return {"fields": normalize(ask_vision(path.read_bytes(), host, vision_model)),
                    "source": vision_model, "note": ""}
        except Exception as e:
            note = explain_error(e, vision_model)

    text = read_text(path)
    if not text.strip():
        if is_photo:
            tip = note or f"No vision model was used. Run: ollama pull {vision_model}"
            return {"error": f"Couldn't read this photo. {tip} (Or install Tesseract for basic OCR.)"}
        if ext == ".pdf":
            return {"error": "Couldn't read this PDF. It may be a scan; try a photo of it instead, or install poppler (pdftotext)."}
        return {"error": "No readable text in this file."}

    fields, source = None, "rules"
    if use_llm:
        try:
            fields, source = ask_llm(text, host, model), model
        except Exception as e:
            note = note or explain_error(e, model)
    if fields is None:
        fields = fallback_extract(text)
    return {"fields": normalize(fields), "source": source, "note": note}


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:30] or "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, default=Path("sorted"))
    ap.add_argument("--model", default=DEFAULT_MODEL, help="text model for txt/pdf receipts")
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL, help="vision model for photos")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--no-llm", action="store_true", help="basic rules only, no model")
    ap.add_argument("--move-files", action="store_true", help="copy originals into category folders")
    a = ap.parse_args()

    use_llm = not a.no_llm
    if use_llm and not ollama_up(a.host):
        print(f"! Ollama isn't reachable at {a.host}; using basic rules instead.", file=sys.stderr)
        use_llm = False

    files = sorted(p for p in a.folder.iterdir() if p.is_file())
    rows, seen_notes = [], set()
    a.out.mkdir(parents=True, exist_ok=True)

    for p in files:
        r = extract_document(p, a.host, a.model, a.vision_model, use_llm)
        if "error" in r:
            print(f"- {p.name}: SKIPPED. {r['error']}")
            continue
        if r["note"] and r["note"] not in seen_notes:
            seen_notes.add(r["note"])
            print(f"  ! {r['note']}", file=sys.stderr)
        d = {**r["fields"], "file": p.name, "source": r["source"]}
        rows.append(d)
        print(f"- {p.name}: {d['vendor']} | {d['date']} | {d['total']} {d['currency']} | {d['category']}")
        if a.move_files:
            dest = a.out / d["category"]
            dest.mkdir(exist_ok=True)
            shutil.copy2(p, dest / f"{d['date']}_{safe(d['vendor'])}_{d['total']}{p.suffix}")

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