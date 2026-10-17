"""
Iram Urdu Digitizer — tiny backend.

Endpoints:
  GET  /            -> the web app (app/index.html)
  POST /upload      -> images (JPG/PNG) + PDFs -> uploads/<YYYY-MM-DD>/
                       (PDF pages are rendered to PNGs at 200 dpi)
  POST /ocr         -> OCR every page -> pages.json (words + confidences)
  GET  /page-image  -> serve one page PNG (?day=&id=) for side-by-side review
  POST /build       -> corrected texts -> output .docx (RTL Urdu,
                       yellow highlights on still-unverified unsure words)
  GET  /download   -> today's finished .docx
  GET  /status      -> day, page count, OCR/docx readiness (JSON)

No auth, no database. Flat files only (pilot scale).
Run:  .venv/bin/python server.py   ->  http://localhost:5000

OCR engine swap: change the ONE line marked ENGINE below to any
OCREngine subclass (see ocr_engine.py).
"""

import os
import glob
import json
from datetime import date
from flask import Flask, request, jsonify, send_from_directory, send_file
from PIL import Image

from ocr_engine import TesseractUrduEngine, LOW_CONF_THRESHOLD

# === ENGINE SWAP POINT: replace with any OCREngine subclass ===
ENGINE = TesseractUrduEngine()
# ==============================================================

BASE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE, "app")
UPLOAD_ROOT = os.path.join(BASE, "uploads")
OUTPUT_ROOT = os.path.join(BASE, "output")
os.makedirs(UPLOAD_ROOT, exist_ok=True)
os.makedirs(OUTPUT_ROOT, exist_ok=True)

app = Flask(__name__, static_folder=APP_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

ALLOWED_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}


def today_str():
    return date.today().isoformat()


def day_dir(day=None):
    d = os.path.join(UPLOAD_ROOT, day or today_str())
    os.makedirs(d, exist_ok=True)
    return d


def pages_manifest(day):
    return os.path.join(day_dir(day), "pages.json")


def output_path(day=None):
    return os.path.join(OUTPUT_ROOT, f"urdu_syllabus_{day or today_str()}.docx")


def safe_name(directory, filename):
    base = os.path.basename(filename) or "upload.png"
    name, ext = os.path.splitext(base)
    cand, i = base, 1
    while os.path.exists(os.path.join(directory, cand)):
        i += 1
        cand = f"{name}_{i}{ext}"
    return cand


def _ext(path):
    return os.path.splitext(path)[1].lower()


def _render_pdf_pages(pdf_path, dest_dir, base_name):
    """Render each PDF page to a PNG. Returns list of page image paths."""
    import pypdfium2 as pdfium
    out = []
    pdf = pdfium.PdfDocument(pdf_path)
    for i, page in enumerate(pdf):
        bitmap = page.render(scale=200 / 72)  # ~200 dpi
        pil = bitmap.to_pil()
        p = os.path.join(dest_dir, f"{base_name}_p{i + 1}.png")
        pil.save(p)
        out.append(p)
    return out


def _strip_punct(w):
    return w.strip("،۔؟!؛:()\"',.?")


def _load_pages(day):
    mp = pages_manifest(day)
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_pages(day, pages):
    with open(pages_manifest(day), "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=1)


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.post("/upload")
def upload():
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"ok": False, "error": "no files received"}), 400
    dest = day_dir()
    saved, skipped, page_ids = [], [], []
    for f in files:
        if not f.filename:
            continue
        if _ext(f.filename) not in ALLOWED_EXTS:
            skipped.append(f.filename)
            continue
        name = safe_name(dest, f.filename)
        path = os.path.join(dest, name)
        f.save(path)
        saved.append(name)
        if _ext(name) == ".pdf":
            try:
                stem = os.path.splitext(name)[0]
                for p in _render_pdf_pages(path, dest, stem):
                    page_ids.append(os.path.basename(p))
            except Exception as e:
                skipped.append(f"{name} (PDF render failed: {e})")
        else:
            page_ids.append(name)
    return jsonify({"ok": True, "saved": saved, "skipped": skipped,
                    "pages": sorted(set(page_ids)), "day": today_str()})


@app.post("/ocr")
def ocr():
    day = (request.get_json(silent=True) or {}).get("day", today_str())
    d = day_dir(day)
    page_files = sorted(
        p for p in glob.glob(os.path.join(d, "*.png"))
        if os.path.isfile(p))
    if not page_files:
        return jsonify({"ok": False,
                        "error": "no pages uploaded for " + day}), 400
    pages = []
    for pf in page_files:
        pid = os.path.basename(pf)
        try:
            img = Image.open(pf)
            res = ENGINE.ocr_image(img)
            words = [{"t": w.text, "c": round(w.conf, 1)} for w in res.words]
            low = sorted({w.text for w in res.low_conf_words})
            pages.append({
                "id": pid, "ok": True,
                "mean_conf": round(res.mean_conf, 1),
                "word_count": len(res.words),
                "low_conf_count": len(low),
                "low_conf_words": low,
                "words": words,
                "text": res.text,
                "notes": res.notes,
            })
        except Exception as e:
            pages.append({"id": pid, "ok": False, "error": str(e),
                          "words": [], "text": "", "notes": []})
    _save_pages(day, pages)
    return jsonify({
        "ok": True, "day": day, "engine": ENGINE.name,
        "pages": [{k: p[k] for k in
                   ("id", "ok", "mean_conf", "word_count",
                    "low_conf_count", "notes") if k in p}
                  for p in pages],
    })


@app.get("/page-image")
def page_image():
    day = request.args.get("day", today_str())
    pid = os.path.basename(request.args.get("id", ""))
    p = os.path.join(day_dir(day), pid)
    if not pid.endswith(".png") or not os.path.isfile(p):
        return jsonify({"ok": False, "error": "page not found"}), 404
    return send_file(p, mimetype="image/png")


@app.get("/page-text")
def page_text():
    """Full OCR words for one page (for the review UI)."""
    day = request.args.get("day", today_str())
    pid = request.args.get("id", "")
    for p in _load_pages(day):
        if p["id"] == pid:
            return jsonify({"ok": True, "page": p})
    return jsonify({"ok": False, "error": "page not found"}), 404


@app.post("/build")
def build():
    from docx_builder import build_docx
    body = request.get_json(silent=True) or {}
    day = body.get("day", today_str())
    texts = body.get("texts", {})  # {page_id: corrected text}
    pages = _load_pages(day)
    if not pages:
        return jsonify({"ok": False,
                        "error": "run OCR first for " + day}), 400
    doc_pages = []
    for p in pages:
        final_text = texts.get(p["id"], p.get("text", ""))
        # Highlight = low-confidence OCR words STILL present verbatim.
        # Words Iram corrected no longer match -> highlight drops. Honest.
        final_words = {_strip_punct(w) for w in final_text.split()}
        hl = [w for w in p.get("low_conf_words", [])
              if _strip_punct(w) in final_words]
        doc_pages.append({
            "title": "",
            "text": final_text,
            "low_conf_words": hl,
        })
    out = output_path(day)
    build_docx(doc_pages, out)
    total_hl = sum(len(p["low_conf_words"]) for p in doc_pages)
    return jsonify({"ok": True, "day": day, "pages": len(doc_pages),
                    "highlighted_words": total_hl,
                    "download": "/download?day=" + day})


@app.get("/download")
def download():
    day = request.args.get("day", today_str())
    out = output_path(day)
    if not os.path.exists(out):
        return jsonify({"ok": False, "error": "not built yet"}), 404
    return send_file(out, as_attachment=True,
                     download_name=f"urdu_syllabus_{day}.docx")


@app.get("/status")
def status():
    day = today_str()
    d = day_dir(day)
    pngs = [p for p in glob.glob(os.path.join(d, "*.png"))
            if os.path.isfile(p)]
    out = output_path(day)
    return jsonify({
        "ok": True, "day": day, "pages": len(pngs),
        "ocr_done": os.path.exists(pages_manifest(day)),
        "ready": os.path.exists(out),
        "low_conf_threshold": LOW_CONF_THRESHOLD,
        "download": "/download?day=" + day if os.path.exists(out) else None,
    })


if __name__ == "__main__":
    print("Iram Urdu Digitizer  |  http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)
