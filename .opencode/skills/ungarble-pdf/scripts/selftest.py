#!/usr/bin/env python3
"""
selftest.py for ungarble-pdf.

Everything here runs without the tesseract binary: the OCR call is stubbed so
that the parts which are actually ours -- the legibility rule, the redaction,
and the geometry of the text written back -- are tested rather than assumed.
Checks that need the real binary report themselves as skipped.

    python selftest.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pymupdf

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ocr_repair", os.path.join(HERE, "ocr_repair.py"))
et = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(et)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- legibility
print("legibility rule")

check("plain text is text", et.legibility("North 120 South 98")[0] == "text")

check("empty page is empty", et.legibility("  \n\t ")[0] == "empty")

pua_page = "".join(chr(0xE000 + (i % 11)) for i in range(300))
state, reason, ratio = et.legibility(pua_page)
check("Private Use Area page is unreadable", state == "unreadable", state)
check("the ratio is reported as evidence", ratio > 0.9, str(ratio))
check("the reason names the cause", "Private Use" in reason, reason)

# The regression this pins. An earlier version inferred unreadability from the
# font table, and on a real 1000-page volume that discarded 133 readable pages.
repetitive = "Presidenza del Consiglio dei Ministri " * 20
check(
    "readable text is never called unreadable, however repetitive",
    et.legibility(repetitive)[0] == "text",
    et.legibility(repetitive)[0],
)
check(
    "PUA page is unreadable even when it is highly repetitive",
    et.legibility("".join(chr(0xE000 + (i % 3)) for i in range(400)))[0]
    == "unreadable",
)

# ------------------------------------------------------------ language parse
print("\nlanguage list parsing")
check("no tesseract is handled, not crashed", isinstance(et.tesseract_langs(), set))

# ------------------------------------------------------------ page synthesis
print("\npage repair")
doc = pymupdf.open()
page = doc.new_page(width=400, height=300)
# a grid of lines, like a ruled table
for x in (20, 190, 360):
    page.draw_line(pymupdf.Point(x, 20), pymupdf.Point(x, 280), width=0.8)
for y in (20, 100, 180, 280):
    page.draw_line(pymupdf.Point(20, y), pymupdf.Point(360, y), width=0.8)
# text that will stand in for the broken layer
page.insert_text((30, 60), "BROKEN", fontsize=12)
for r in range(3):
    for c in range(3):
        page.insert_text((30 + c * 170, 140 + r * 80), f"R{r}C{c}", fontsize=10)
tmp = "/tmp/_ungarble_selftest.pdf"
doc.save(tmp)
doc.close()

src = pymupdf.open(tmp)
sp = src[0]
before_draw = len(sp.get_drawings())
before_text = len(sp.get_text("text").strip())
check("fixture has drawable rules", before_draw > 0, str(before_draw))
check("fixture has text to remove", before_text > 0, str(before_text))

# simulate OCR: a 3x3 grid of words inside the rules
dpi = 72
words = [
    {"text": f"W{r}{c}", "x": 30 + c * 120, "y": 45 + r * 80,
     "w": 60, "h": 12, "conf": 90.0}
    for r in range(3) for c in range(3)
]

new = pymupdf.open()
np_ = new.new_page(width=sp.rect.width, height=sp.rect.height)
np_.show_pdf_page(np_.rect, src, 0)
np_.add_redact_annot(np_.rect)
np_.apply_redactions(
    images=pymupdf.PDF_REDACT_IMAGE_NONE,
    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
    text=pymupdf.PDF_REDACT_TEXT_REMOVE,
)
# Checked before overlaying: after the overlay there is text again, but it is
# the new text, and that is the next assertion's job.
check("the broken text was removed", np_.get_text("text").strip() == "",
      repr(np_.get_text("text")[:40]))
check("the rules survived redaction", len(np_.get_drawings()) == before_draw,
      f"{len(np_.get_drawings())} vs {before_draw}")

written, dropped = et.overlay_text(np_, words, dpi, min_conf=30)
check("every word was written", written == 9 and dropped == 0,
      f"written={written} dropped={dropped}")

got = np_.get_text("words")
check("all nine words extract", len(got) == 9, str(len(got)))
rows = {}
for w in got:
    rows.setdefault(round(w[1]), []).append(w[4])
check("geometry preserved: 3 rows of 3",
      len(rows) == 3 and all(len(v) == 3 for v in rows.values()),
      str({k: len(v) for k, v in rows.items()}))
check("no PUA leaked into the new text",
      not any(et._is_pua(c) for w in got for c in w[4]))

# low-confidence words are dropped, not guessed at
words_low = [dict(w, conf=5.0) for w in words]
n_before = len(np_.get_text("words"))
w2, d2 = et.overlay_text(np_, words_low, dpi, min_conf=30)
check("low-confidence words are dropped", w2 == 0 and d2 == 9,
      f"written={w2} dropped={d2}")

# a readable page must survive the pass untouched
readable = pymupdf.open()
rp = readable.new_page(width=300, height=200)
rp.insert_text((20, 40), "Already readable text", fontsize=11)
rtmp = "/tmp/_ungarble_readable.pdf"
readable.save(rtmp)
readable.close()
rdoc = pymupdf.open(rtmp)
check("a readable page is classified text, not a target",
      et.legibility(rdoc[0].get_text("text"))[0] == "text")

os.remove(tmp)
os.remove(rtmp)

# ------------------------------------------------------------- derotation
print("\nderotation")
probe = pymupdf.open()
pp = probe.new_page(width=400, height=300)
pp.insert_text((100, 200), "MARKER", fontsize=12)
orig_box = pp.get_text("words")[0][:4]
probe.save("/tmp/_ungarble_probe.pdf")
probe.close()
pdoc = pymupdf.open("/tmp/_ungarble_probe.pdf")

for angle in (90, 270):
    d, q = et.derive_upright(pdoc, 0, angle)
    up = q.get_text("words")[0][:4]
    back = et.unrotate_box(up[0], up[1], up[2], up[3], angle, 400, 300)
    err = max(abs(back[i] - orig_box[i]) for i in range(4))
    check(
        f"unrotate_box is exact for {angle} degrees",
        err < 0.01,
        f"error {err:.3f} pt, got {[round(v,2) for v in back]} "
        f"want {[round(v,2) for v in orig_box]}",
    )

# a page whose content is turned must be detected as turned
tdoc = pymupdf.open()
tsrc = tdoc.new_page(width=300, height=400)
tsrc.insert_text((40, 200), "TURNED", fontsize=12)
tdoc.save("/tmp/_ungarble_turned_src.pdf")
tdoc.close()
host = pymupdf.open()
tp = host.new_page(width=400, height=300)
ts = pymupdf.open("/tmp/_ungarble_turned_src.pdf")
tp.show_pdf_page(tp.rect, ts, 0, rotate=90)
host.save("/tmp/_ungarble_turned.pdf")
host.close()
chosen = et.choose_rotation(pymupdf.open("/tmp/_ungarble_turned.pdf"), 0)
check("a turned page is detected as turned", chosen in (90, 270), str(chosen))

for f in ("/tmp/_ungarble_probe.pdf", "/tmp/_ungarble_turned_src.pdf",
          "/tmp/_ungarble_turned.pdf"):
    try:
        os.remove(f)
    except OSError:
        pass

# ------------------------------------------------------------------ binary
print("\ntesseract binary")
if et.have_tesseract():
    langs = et.tesseract_langs()
    check("installed languages parse to bare codes",
          all("(" not in l and " " not in l for l in langs), str(sorted(langs)))
    check("an Italian pack is available", "ita" in langs, str(sorted(langs)))
else:
    print("  SKIP  tesseract not installed "
          "(sudo apt install tesseract-ocr tesseract-ocr-ita)")

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("failed: " + ", ".join(failed))
sys.exit(1 if failed else 0)
