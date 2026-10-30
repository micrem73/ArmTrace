"""Scanned pages read by OCR: the register of companies (Difesa A5/2) only.

DECISIONS.md 2026-10-27 allows OCR for this one table, every value flagged and
checked against the image. A page of an edition whose register is a scan, or a
scan under a noisy text layer (`layer` in `lib/data/register-pages.tsv`), is
rendered at `DPI`, turned upright when the map lists it as printed sideways
(both turns are tried, the one tesseract is surer of is kept), and read by
tesseract. Its words are kept in git under `Out/ocr/<year>/<SETTINGS>/
<volume>.p<pdf page>.tsv` (tesseract's TSV, after one `# rotate <deg>` line):
slow to make, and what a reviewer compares with the image.

An `OcrPage` stands in for a `source.SourcePage`: `get_texttrace` gives one
span per word, upright, in points of the turned image, so `lib/geometry` and
`extract.read_list` read it as they read a text layer. The span's font is
`ocr:<confidence>`, the word's tesseract confidence (0-100), so a reader can
flag what it took from OCR. Nothing read is corrected here.
"""
import csv
import io
import os
import statistics
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from lib import layout, register_pages

KEY = "DIFESA/A5/2"
DPI = 300
# psm 4 (one column of lines of varying size): on 2018 p729 psm 6 read 00771
# as 00774 and ran words together (`ROSASPA`); psm 4 read the page right.
PSM = 4
SETTINGS = f"tess4.1.1-ita-psm{PSM}-{DPI}dpi"
FONT = "ocr:"


def reads(year, key):
    """True where `key`'s pages of `year` are read by OCR."""
    pages = register_pages.load().get(int(year))
    return key == KEY and pages is not None and pages.layer in ("scan", "ocrlayer")


def tsv_path(year, volume, pdf_page, root="."):
    return Path(root, layout.OUT, "ocr", str(year), SETTINGS,
                f"{Path(volume).stem}.p{pdf_page}.tsv")


def _tesseract(image):
    """tesseract's TSV of a PIL image, and its mean word confidence."""
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp, "page.png")
        image.save(png)
        out = subprocess.run(["tesseract", str(png), "-", "-l", "ita", "--psm", str(PSM),
                              "tsv"], capture_output=True, text=True, check=True).stdout
    confs = [w["conf"] for w in _words(out)]
    return out, (statistics.mean(confs) if confs else 0.0)


def _words(text):
    """tesseract's TSV rows that are words: text, box in pixels, confidence."""
    rows = csv.DictReader(io.StringIO("".join(ln for ln in text.splitlines(True)
                                               if not ln.startswith("#"))),
                          delimiter="\t", quoting=csv.QUOTE_NONE)
    out = []
    for r in rows:
        if r["level"] != "5" or not (r.get("text") or "").strip() or float(r["conf"]) < 0:
            continue
        left, top, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
        out.append({"text": r["text"].strip(), "box": (left, top, left + w, top + h),
                    "conf": float(r["conf"]),
                    "line": (r["block_num"], r["par_num"], r["line_num"])})
    return out


def read(doc, year, pdf_page, sideways, root="."):
    """(rotation, TSV text) of one page: the saved reading if there is one,
    else tesseract's, saved first (written aside and moved into place, so an
    interrupted run leaves no half page)."""
    path = tsv_path(year, doc.name, pdf_page, root)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        return int(text.split("\n", 1)[0].split()[-1]), text
    from PIL import Image
    pix = doc[pdf_page - 1].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    tries = []
    for rotation in ((90, 270) if sideways else (0,)):
        out, conf = _tesseract(image.rotate(rotation, expand=True) if rotation else image)
        tries.append((conf, -rotation, rotation, out))     # a tie keeps the first turn
    *_, rotation, out = max(tries)
    text = f"# rotate {rotation}\n{out}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False, suffix=".part") as fh:
        fh.write(text)
    os.replace(fh.name, path)
    return rotation, text


class OcrPage:
    """A scanned source page, read by OCR, in place of a SourcePage."""

    clip = None
    direction = None

    def __init__(self, doc, year, pdf_page, printed_page, sideways=False, root="."):
        self.doc = doc
        self.page = doc[pdf_page - 1]
        self.pdf_page = pdf_page
        self.printed_page = printed_page
        self.rotation, text = read(doc, year, pdf_page, sideways, root)
        self.words = _words(text)
        r = self.page.rect
        w, h = (r.height, r.width) if self.rotation in (90, 270) else (r.width, r.height)
        self.rect = pymupdf.Rect(0, 0, w, h)

    def get_texttrace(self, across=False):
        if across:
            return []
        k = 72 / DPI
        spans = []
        for wd in self.words:
            x0, y0, x1, y1 = (v * k for v in wd["box"])
            step = (x1 - x0) / len(wd["text"])
            chars = [(ord(c), 0, (x0 + i * step, y1), (x0 + i * step, y0, x0 + (i + 1) * step, y1))
                     for i, c in enumerate(wd["text"])]
            spans.append({"dir": (1.0, 0.0), "font": f"{FONT}{wd['conf']:.0f}", "size": y1 - y0,
                          "flags": 0, "bbox": (x0, y0, x1, y1), "chars": chars})
        return spans

    def get_drawings(self):
        return []

    def get_text(self, option="text", **kwargs):
        if option != "text":
            raise ValueError(f"OcrPage.get_text: only 'text', not {option!r}")
        lines = {}
        for wd in self.words:
            lines.setdefault(wd["line"], []).append(wd["text"])
        return "\n".join(" ".join(ws) for ws in lines.values())


def confidence(word):
    """A `geometry.Word`'s OCR confidence, or None if it was not read by OCR."""
    return float(word.font[len(FONT):]) if word.font.startswith(FONT) else None
