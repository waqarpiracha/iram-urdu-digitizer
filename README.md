# Iram Urdu Digitizer (v1)

Photos/scans of Urdu syllabus pages → computer reads them → Iram reviews
and corrects → formatted RTL Word (.docx) download.

## Honest limits (tell Iram these upfront)

- **Printed** Urdu (Naskh-style, like textbooks) reads moderately well.
- **Handwritten Nastaliq: the computer mostly cannot read it.** Those pages
  come back empty with a note saying "please type this page yourself."
- The app **never guesses**: words the OCR is unsure about are highlighted
  **yellow** in the review UI and in the Word file. Words Iram corrects lose
  the highlight automatically.
- Tesseract's Urdu model is old; a better engine can be swapped in later —
  see `ocr_engine.py` (change the ONE `ENGINE = ...` line in `server.py`).

## Run locally

```bash
cd ~/workspace/iram-urdu-digitizer
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# tesseract system binary needed: sudo apt-get install tesseract-ocr tesseract-ocr-urd
.venv/bin/python server.py   # http://localhost:5000
```

## Flow

1. Upload JPG/PNG photos or PDFs (PDF pages render at 200 dpi).
2. "Start reading" → tesseract-urd OCR, word-level confidences.
3. Review: image left, editable Urdu text right; unsure-word chips listed.
4. "Make Word file" → .docx, RTL, yellow highlights on still-unverified
   unsure words, verification note at the top.

## Deploy (Render, same account as the other apps — NOT deployed yet)

Push to GitHub → Render → New Web Service → Public Git repo →
Runtime Docker, Plan Free, Health check path `/`.
