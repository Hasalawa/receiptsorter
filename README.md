# ReceiptSorter

Sort freelancer receipts and invoices into a clean CSV and category folders, **entirely on your own machine**.
A local open-weight model (via [Ollama](https://ollama.com)) reads each receipt and extracts vendor, date, total, currency and expense category. No cloud API, no client data leaving your laptop.

## Quick start

```bash
# 1. Install Ollama and pull an open-weight model
ollama pull qwen2.5:7b-instruct

# 2. Run it on a folder of receipts (.txt, .pdf, .png, .jpg)
python3 receiptsorter.py ./my_receipts --out sorted --move-files
```

Output: `sorted/receipts.csv`, category folders with renamed copies (`2026-10-01_Tech-Mart_24990.0.pdf`), and a per-category total in the terminal.

## Options

| Flag | Meaning |
|------|---------|
| `--model` | Any Ollama model (default `qwen2.5:7b-instruct`). Swap freely. |
| `--host` | Ollama URL (default `http://localhost:11434`) |
| `--no-llm` | Regex-only mode, no model needed |
| `--move-files` | Copy originals into category folders |

If Ollama is not reachable, the tool automatically falls back to regex extraction, so it always produces something.

## Optional extras

- PDFs: `pdftotext` (poppler-utils)
- Photos/scans: `pip install pytesseract pillow` plus the `tesseract` binary

Core tool uses only the Python standard library.

## Try it without Ollama

```bash
python3 receiptsorter.py samples --no-llm --out out
python3 tests/mock_ollama.py &   # fake server to exercise the LLM code path
python3 receiptsorter.py samples --host http://127.0.0.1:11555 --out out_llm
```

## Why open models

- **Privacy**: invoices contain client names and amounts; they never leave the laptop.
- **Cost**: zero per-receipt cost, so a flat monthly price for freelancers still has margin.
- **Customisable**: swap models, edit the category list, or fine-tune on a user's own vendors.
