---
title: ReceiptSorter: a local-first receipt sorter for freelancers, powered by open-weight models
published: false
tags: devchallenge, hf26challenge
---

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 2: Side Hustle](https://dev.to/challenges/hacktoberfest-week2-2026-10-12)*

## What I Built

**ReceiptSorter** is a small command-line tool that turns a messy folder of receipts and invoices into a clean expense CSV plus tidy category folders. A local open-weight model reads each receipt and extracts the vendor, date, total, currency and expense category.

**Who would pay for it:** freelancers and tiny agencies (designers, developers, translators) who dread tax season. Their invoices contain client names and amounts, so many of them don't want to upload that to a random cloud service. A flat monthly fee, or a one-off setup service where I install it for them, works because the per-receipt cost is zero.

**The problem:** every month-end, they lose hours renaming files, retyping totals into a spreadsheet and guessing categories.

## Demo

There is a local web app too (`python3 webapp.py`): drag receipts in, fix anything the model got wrong, and download a CSV or a ZIP sorted into category folders. It only listens on 127.0.0.1 and has no external assets, so it works offline.

<!-- Add docs/screenshot.png (upload it to the dev.to editor) or a short screen recording here -->

<!-- Add a short screen recording or a deployed link here -->

```
$ python3 receiptsorter.py samples --out sorted --move-files
- cafe.txt: Cafe Ceylon | 2026-10-08 | 4100.0 LKR | Meals
- hosting.txt: Lanka Cloud Hosting (Pvt) Ltd | 2026-09-03 | 8500.0 LKR | Software
- keyboard.txt: Tech Mart Electronics | 2026-10-01 | 24990.0 LKR | Hardware
- taxi.txt: PickMe Rides | 2026-09-12 | 3250.0 LKR | Travel

Wrote sorted/receipts.csv (4 receipts)
  Hardware      24,990.00 LKR
  Meals          4,100.00 LKR
  Software       8,500.00 LKR
  Travel         3,250.00 LKR
```

## Code

<!-- Embed your GitHub repo here, e.g. {% embed https://github.com/YOUR_USERNAME/receiptsorter %} -->

## How I Built It

- **Open-weight model via Ollama:** default `qwen2.5:7b-instruct`, running locally. Ollama's structured output (`format` with a JSON schema) forces the model to return valid JSON with the exact fields and a fixed category enum, so there's no fragile output parsing.
- **Temperature 0** for repeatable results.
- **Text extraction:** plain text directly, PDFs via `pdftotext`, photos via Tesseract OCR.
- **Graceful fallback:** if Ollama isn't running, a regex extractor takes over, so the tool never leaves the user with nothing.
- **Pure standard library core:** a CLI plus a small web UI (`webapp.py` and one HTML file, no frameworks, no CDN), so there is no dependency hell for a non-technical customer.
- **Web UI safety:** the server binds to 127.0.0.1 and rejects requests with a foreign Host or Origin header, so a random website can't talk to it.
- I tested all three paths (regex, LLM, LLM-unreachable fallback) with sample receipts and a mock Ollama server.

## Why Does Open Innovation Matter?

- **Data stays put.** Invoices reveal who a freelancer works for and what they charge. With a local model, nothing leaves the laptop, which is a real selling point and impossible to promise with a hosted API.
- **The economics work.** A closed API charges per call, so a cheap flat subscription would eat the margin. Local inference costs nothing per receipt.
- **Swap and tune.** The model is one flag (`--model`). A user in Sri Lanka with local vendors (PickMe, Daraz, local hosting companies) can get a model fine-tuned on their own receipts, and I'm not locked into anyone's pricing or deprecation schedule.
- **Works offline.** Month-end on a train or with flaky internet is no problem.

## My Agent Session

<!-- Optional: save your session with DevRelay and embed it here -->

## My First User

<!-- Fill in after you show it to a real freelancer: who they were, what they said, did they pay, what they asked for next. Delete this section if you have nothing yet. -->

## Prize Categories

<!-- List the partner categories you're entering, or remove this section. -->
