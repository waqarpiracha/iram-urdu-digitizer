"""
Iram Urdu Digitizer — OCR engine layer.

Design: every OCR backend implements OCREngine.ocr_image() and returns an
OCRResult of word-level (text, confidence, bbox) tuples. To swap in a better
Urdu engine later (e.g. a hosted Nastaliq model), write a new OCREngine
subclass and change ONE line in server.py (ENGINE = ...).

v1 backend: tesseract with Urdu language data (tesseract-ocr-urd).
Honest limits: tesseract-urd reads PRINTED Naskh-style Urdu moderately well;
handwritten Nastaliq is mostly unreadable to it. The engine reports
per-word confidences and never invents text — low-confidence words are
flagged for Iram's review, never silently guessed.
"""

import shutil
from dataclasses import dataclass, field

from PIL import Image


# Words below this tesseract confidence (0-100) are "unsure" and get
# highlighted for Iram's review. 60 is deliberately conservative:
# tesseract is often over-confident on Urdu.
LOW_CONF_THRESHOLD = 60


@dataclass
class Word:
    text: str
    conf: float          # 0-100 (tesseract scale); -1 = no text detected here
    bbox: tuple = ()     # (x0, y0, x1, y0) in image pixels


@dataclass
class OCRResult:
    words: list = field(default_factory=list)   # flat, reading order
    lines: list = field(default_factory=list)   # list of list[Word]
    mean_conf: float = 0.0
    engine: str = ""
    notes: list = field(default_factory=list)

    @property
    def low_conf_words(self):
        return [w for w in self.words if 0 <= w.conf < LOW_CONF_THRESHOLD]

    @property
    def text(self):
        return "\n".join(" ".join(w.text for w in line) for line in self.lines)


class OCREngine:
    """Backend contract. ocr_image() must never raise on a bad image —
    return an OCRResult with notes explaining what happened."""

    name = "base"

    def ocr_image(self, image: Image.Image) -> OCRResult:  # noqa: D102
        raise NotImplementedError


class TesseractUrduEngine(OCREngine):
    """tesseract OCR, Urdu language data, word-level confidences."""

    name = "tesseract-urd"

    def __init__(self, lang: str = "urd", psm: int = 6):
        if shutil.which("tesseract") is None:
            raise RuntimeError(
                "tesseract binary not found. Install it with:\n"
                "  sudo apt-get install tesseract-ocr tesseract-ocr-urd\n"
                "(reinstall after any VM replacement — /usr is wiped)")
        self.lang = lang
        # psm 6 = assume a uniform block of text (syllabus page). Good default.
        self.config = f"--psm {psm}"

    def _prepare(self, image: Image.Image) -> Image.Image:
        img = image.convert("L")
        # tesseract wants ~300 dpi; phone photos are fine, small scans are not.
        # Upscale modestly so x-height is readable.
        if max(img.size) < 1600:
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        return img

    def ocr_image(self, image: Image.Image) -> OCRResult:
        import pytesseract

        res = OCRResult(engine=self.name)
        try:
            img = self._prepare(image)
            data = pytesseract.image_to_data(
                img, lang=self.lang, config=self.config,
                output_type=pytesseract.Output.DICT)
        except Exception as e:  # never crash the request on OCR failure
            res.notes.append(f"OCR failed on this page: {e}")
            return res

        n = len(data["text"])
        cur_line, last_line = [], None
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            try:
                conf = float(data["conf"][i])
            except (ValueError, TypeError):
                conf = -1.0
            if not txt or conf < 0:
                continue  # no text here — skip, never invent
            w = Word(text=txt, conf=conf,
                     bbox=(data["left"][i], data["top"][i],
                           data["left"][i] + data["width"][i],
                           data["top"][i] + data["height"][i]))
            res.words.append(w)
            line_no = (data["block_num"][i], data["par_num"][i],
                       data["line_num"][i])
            if line_no != last_line:
                if cur_line:
                    res.lines.append(cur_line)
                cur_line, last_line = [], line_no
            cur_line.append(w)
        if cur_line:
            res.lines.append(cur_line)

        if res.words:
            res.mean_conf = sum(w.conf for w in res.words) / len(res.words)
        else:
            res.notes.append(
                "No readable text found on this page. If it is handwritten "
                "Nastaliq, the computer cannot read it — please type that "
                "page yourself.")
        return res
