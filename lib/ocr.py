"""Scanned pages read by OCR: the register of companies (Difesa A5/2) only.

DECISIONS.md 2026-10-27 allows OCR for this one table, every value flagged and
checked against the image. A page of an edition whose register is a scan, or a
scan under a noisy text layer (`layer` in `lib/data/register-pages.tsv`), is
rendered at `DPI`, turned upright when the map lists it as printed sideways
(both turns are tried, the one tesseract is surer of is kept), and read by
tesseract. Its words are kept in git under `Out/ocr/<year>/<settings>/
<volume>.p<pdf page>.tsv` (tesseract's TSV, after a `# rotate <deg>` line):
slow to make, and what a reviewer compares with the image.

A ruled grid (the `grid` layouts, 1991-2015) reads badly as it stands: the
rules come back as `|` and a header's words go missing. Its rules are found
in the image first (`rules`: runs of dark pixels at least `RULE_MIN` long),
kept as `# rule h|v <position> <from> <to> <thickness>` lines (pixels), and
whited out before tesseract reads the page, in `GRID_PSM` (one block of
text); `get_drawings` gives them back as the page's lines. Then each cell of
a grid (between two long rules, walls crossing the row) is read again on its
own: read with its neighbours, a two-line header label is lost (2015 p1019's
`DATA CANC.` came back `GANO`; alone, right). The cell's words replace the
page's there (TSV `block_num` from `CELL_BLOCK`).

Where the volume's own OCR layer reads better than tesseract does here (`layer`
`ocrlayer-kept` in the page map: 2012, a 350 dpi scan whose 7 pt rows tesseract
garbles), its words are taken instead, the rules still found in the image
(`LAYER_SETTINGS`: the file keeps the rules only). Such a word's confidence is
`LAYER` (none given).

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
# A grid with its rules whited out: psm 6 reads 2015 p1018's header and rows
# whole, psm 4 loses its first header line.
GRID_PSM = 6
GRID_SETTINGS = f"tess4.1.1-ita-psm{GRID_PSM}-{DPI}dpi-unruled"
RULE_MIN = {"h": 0.4 * DPI, "v": 0.25 * DPI}    # pixels: no glyph runs that long
DARK = 140
BRIDGE = 4
CELL_BLOCK = 1000
LAYER = -1.0
LAYER_SETTINGS = f"layer-rules-{DPI}dpi"
FONT = "ocr:"


def reads(year, key):
    """True where `key`'s pages of `year` are read by OCR."""
    pages = register_pages.load().get(int(year))
    return key == KEY and pages is not None and pages.layer in ("scan", "ocrlayer",
                                                                "ocrlayer-kept")


def tsv_path(year, volume, pdf_page, root=".", grid=False, layer=False):
    settings = LAYER_SETTINGS if layer else GRID_SETTINGS if grid else SETTINGS
    return Path(root, layout.OUT, "ocr", str(year), settings,
                f"{Path(volume).stem}.p{pdf_page}.tsv")


def _tesseract(gray, psm):
    """tesseract's TSV of a grey image (numpy), and its mean word confidence."""
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp, "page.png")
        h, w = gray.shape
        pymupdf.Pixmap(pymupdf.csGRAY, w, h, gray.tobytes(), False).save(png)
        out = subprocess.run(["tesseract", str(png), "-", "-l", "ita", "--psm", str(psm),
                              "tsv"], capture_output=True, text=True, check=True).stdout
    words = _words(out)
    confs = [w["conf"] for w in words]
    # Its sure words, then its mean confidence: on a sparse page (2002 p143, an empty
    # grid) a turn reading noise was, on average, surer than the right one.
    sure = sum(1 for w in words if w["conf"] >= 80 and len(w["text"]) >= 3)
    return out, (sure, statistics.mean(confs) if confs else 0.0)


def _render(page, rotation):
    """The page in grey at `DPI`, turned `rotation` degrees, as a numpy array."""
    import numpy as np
    pix = page.get_pixmap(matrix=pymupdf.Matrix(DPI / 72, DPI / 72).prerotate(rotation),
                          colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.stride)[:, :pix.width].copy()


def _runs(row, min_len):
    """[(start, end)] of the True runs of a 1-D bool array at least `min_len` long."""
    import numpy as np
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    keep = ends - starts >= min_len
    return list(zip(starts[keep].tolist(), ends[keep].tolist()))


def _lines(dark, min_len, gap=8):
    """The rules along a dark mask's rows: [(position, from, to, thickness)].
    A run continues one of the rows just above that it overlaps (or nearly:
    `gap`), so a rule a few pixels thick, or a scan's slightly slanted one, is
    one rule."""
    found, open_ = [], []          # [first row, last row, from, to]
    for y in range(dark.shape[0]):
        nxt = []
        for a, b in _runs(dark[y], min_len):
            hit = next((o for o in open_ if o[1] >= y - 2 and a <= o[3] + gap
                        and b >= o[2] - gap), None)
            if hit:
                open_.remove(hit)
                nxt.append([hit[0], y, min(hit[2], a), max(hit[3], b)])
            else:
                nxt.append([y, y, a, b])
        for o in open_:
            (nxt if o[1] >= y - 2 else found).append(o)
        open_ = nxt
    return [((p0 + p1) / 2, a, b, p1 - p0 + 1) for p0, p1, a, b in found + open_]


def rules(gray):
    """{"h": [...], "v": [...]}: the page's rules, as `_lines` gives them. A wall
    the scan broke into dashes (2012 p68: white gaps of a few pixels all along
    it) is bridged first: a light pixel with dark ones within `BRIDGE` above and
    below it, in its column, is dark for the walls (never grown past an end)."""
    import numpy as np
    dark = gray < DARK
    above, below = np.zeros_like(dark), np.zeros_like(dark)
    for e in range(1, BRIDGE + 1):
        above[e:] |= dark[:-e]
        below[:-e] |= dark[e:]
    return {"h": _lines(dark, RULE_MIN["h"]), "v": _lines((dark | (above & below)).T, RULE_MIN["v"])}


def _unruled(gray, found):
    """The image with its rules whited out (a pixel either side)."""
    clean = gray.copy()
    for pos, a, b, t in found["h"]:
        top = max(0, int(pos - t / 2) - 1)
        clean[top:top + t + 3, a:b] = 255
    for pos, a, b, t in found["v"]:
        left = max(0, int(pos - t / 2) - 1)
        clean[a:b, left:left + t + 3] = 255
    return clean


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


def _cells(found):
    """[(x0, y0, x1, y1)] (pixels): every cell of the page's grids, a row being
    two long rules with at least two walls crossing it (a full-width row,
    `Motivazione : ...`, is one cell between the frame's walls)."""
    # A rule the scan broke in pieces (2013 p580) is one: pieces within 3 px of
    # height, from the first to the last.
    merged = []
    for pos, a, b, _t in sorted(found["h"]):
        if merged and pos - merged[-1][0] <= 3:
            merged[-1] = [merged[-1][0], min(merged[-1][1], a), max(merged[-1][2], b)]
        else:
            merged.append([pos, a, b])
    longest = max((b - a for _p, a, b in merged), default=0)
    levels = []
    for pos, a, b in merged:
        if b - a >= 0.6 * longest and (not levels or pos - levels[-1] > 12):
            levels.append(pos)
    out = []
    for top, bot in zip(levels, levels[1:]):
        mid = (top + bot) / 2
        walls = sorted({round(pos) for pos, a, b, _t in found["v"] if a <= mid <= b})
        out += [(x0, top, x1, bot) for x0, x1 in zip(walls, walls[1:]) if x1 - x0 > 12]
    return out


def _cell_words(clean, cells):
    """tesseract's TSV rows (level 5) of each cell read alone, in page pixels."""
    import numpy as np
    rows, pad = [], 20
    for i, (x0, y0, x1, y1) in enumerate(cells):
        crop = clean[int(y0) + 3:int(y1) - 2, int(x0) + 3:int(x1) - 2]
        if crop.size == 0 or crop.min() >= DARK:
            continue        # an empty cell
        out, _conf = _tesseract(np.pad(crop, pad, constant_values=255), GRID_PSM)
        for w in _words(out):
            left, top, right, bottom = w["box"]
            dx, dy = int(x0) + 3 - pad, int(y0) + 3 - pad
            _block, par, line = w["line"]
            rows.append("\t".join(str(v) for v in (
                5, 1, CELL_BLOCK + i, par, line, 0, left + dx, top + dy, right - left,
                bottom - top, w["conf"], w["text"])))
    return rows


def _with_cells(out, clean, found):
    """The page's TSV, the words inside a grid's cells replaced by each cell's own."""
    cells = _cells(found)
    if not cells:
        return out
    lines = out.splitlines()

    def in_cell(line):
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5":
            return False
        cx, cy = int(f[6]) + int(f[8]) / 2, int(f[7]) + int(f[9]) / 2
        return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in cells)

    return "\n".join([lines[0]] + [ln for ln in lines[1:] if not in_cell(ln)]
                     + _cell_words(clean, cells)) + "\n"


def read(doc, year, pdf_page, sideways, root=".", grid=False, layer=False):
    """(rotation, TSV text) of one page: the saved reading if there is one,
    else tesseract's, saved first (written aside and moved into place, so an
    interrupted run leaves no half page). `rotation`: pymupdf's, degrees.
    `layer`: the rules only, the words being the volume's own."""
    path = tsv_path(year, doc.name, pdf_page, root, grid, layer)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        return int(text.split("\n", 1)[0].split()[-1]), text
    if layer and sideways:
        raise ValueError(f"p{pdf_page}: a page printed sideways keeps no own layer here")
    tries = []
    # A page marked sideways may carry its own turn already (2001-2003: /Rotate 90,
    # upright as rendered): not turning it is tried too.
    for rotation in ((90, 270, 0) if sideways else (0,)):
        gray = _render(doc[pdf_page - 1], rotation)
        found = rules(gray) if grid else {"h": [], "v": []}
        if layer:
            tries.append((0, 0, 0, gray, found, ""))
            break
        out, conf = _tesseract(_unruled(gray, found) if grid else gray,
                               GRID_PSM if grid else PSM)
        conf = conf if sideways else 0      # how sure: only to choose a turn
        tries.append((conf, -rotation, rotation, gray, found, out))  # a tie: the first turn
    *_, rotation, gray, found, out = max(tries, key=lambda t: t[:2])
    if grid and not layer:
        out = _with_cells(out, _unruled(gray, found), found)
    kept = "".join(f"# rule {d} {pos:.1f} {a} {b} {t}\n"
                   for d in ("h", "v") for pos, a, b, t in sorted(found[d]))
    text = f"# rotate {rotation}\n{kept}{out}"
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

    def __init__(self, doc, year, pdf_page, printed_page, sideways=False, root=".", grid=False,
                 layer=False):
        self.doc = doc
        self.page = doc[pdf_page - 1]
        self.pdf_page = pdf_page
        self.printed_page = printed_page
        self.rotation, text = read(doc, year, pdf_page, sideways, root, grid, layer)
        k = DPI / 72
        self.words = ([{"text": t, "box": (x0 * k, y0 * k, x1 * k, y1 * k), "conf": LAYER,
                        "line": (b, ln, 0)}
                       for x0, y0, x1, y1, t, b, ln, _w in self.page.get_text("words")]
                      if layer else _words(text))
        self.rules = [ln.split()[2:] for ln in text.splitlines() if ln.startswith("# rule ")]
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
        """The rules found in the image, as pymupdf's stroked lines, in points."""
        k = 72 / DPI
        out = []
        for d, pos, a, b, t in self.rules:
            pos, a, b, t = float(pos) * k, int(a) * k, int(b) * k, int(t) * k
            p, q = ((pymupdf.Point(a, pos), pymupdf.Point(b, pos)) if d == "h"
                    else (pymupdf.Point(pos, a), pymupdf.Point(pos, b)))
            out.append({"type": "s", "items": [("l", p, q)], "width": t, "fill": None,
                        "color": (0, 0, 0), "rect": pymupdf.Rect(p, q)})
        return out

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


def note(confs, settings):
    """A row's flag: how OCR read it, and the least sure word."""
    if min(confs) == LAYER:
        return "ocr: the volume's own text layer, rules found in the image"
    return f"ocr: read by {settings}, least sure word {min(confs):.0f}%"
