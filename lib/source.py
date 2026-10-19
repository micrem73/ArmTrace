"""The source volumes as the SQL side reads them.

One table's pages are found through `Out/<year>/tablesindex.sqlite`
(`segments`), in the original volumes under `In/<year>/`, never in the split
PDFs: those drop the tagged structure, page labels and bookmarks of the source
(DECISIONS.md 2026-10-17). The catalogue and the extractor both read through
here, so they cannot disagree on what belongs to a table.

A `SourcePage` stands in for a pymupdf page wherever `lib/geometry` reads one
(`get_texttrace`, `get_drawings`, `get_text`). It does two things a raw page
does not:

  * on a cut page (a relation exhibit sharing its page with prose or another
    exhibit) it keeps only what lies inside the table's clip, the band the
    splitter cut, stored in source coordinates (`segments.clip`), and, as the
    splitter does, only text written along the page's dominant direction: the
    volume's running header runs across a rotated exhibit inside its band;
  * it decodes the garbled MAE fonts through `lib/glyphs`. A glyph the map
    cannot name stays as printed, a Private Use Area code point, so it is
    carried flagged and never guessed (`glyphs.undecoded`).
"""
import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

import pymupdf

from lib import glyphs, layout

REPORTS_DIR = "In"


class SourcePage:
    """A source page, clipped and decoded, in place of a pymupdf page."""

    def __init__(self, doc, pdf_page, printed_page, clip=None):
        self.doc = doc
        self.page = doc[pdf_page - 1]
        self.pdf_page = pdf_page
        self.printed_page = printed_page
        self.clip = pymupdf.Rect(clip) if clip else None
        self.maps = glyphs.page_maps(doc, self.page)
        self.direction = self._dominant() if self.clip else None

    @property
    def rect(self):
        return self.clip or self.page.rect

    def _inside(self, rect):
        if self.clip is None:
            return True
        r = pymupdf.Rect(rect)
        return self.clip.contains(pymupdf.Point((r.x0 + r.x1) / 2,
                                                (r.y0 + r.y1) / 2))

    def _dominant(self):
        """The writing direction most characters follow, as emit's
        drop_minority_direction counts it."""
        tally = Counter()
        for span in self.page.get_texttrace():
            tally[_direction(span.get("dir", (1.0, 0.0)))] += len(span.get("chars") or ())
        return tally.most_common(1)[0][0] if tally else None

    def _along(self, direction):
        return self.direction is None or _direction(direction) == self.direction

    def get_texttrace(self):
        spans = []
        for span in self.page.get_texttrace():
            if not self._along(span.get("dir", (1.0, 0.0))):
                continue
            fmap = self.maps.get(span.get("font"))
            chars = []
            for ch in span.get("chars") or ():
                if not self._inside(ch[3]):
                    continue
                if fmap:
                    text = fmap.get(chr(ch[0]))
                    if text and len(text) == 1:
                        ch = (ord(text),) + tuple(ch[1:])
                chars.append(ch)
            if chars:
                spans.append({**span, "chars": chars})
        return spans

    def get_drawings(self):
        return [d for d in self.page.get_drawings() if self._inside(d["rect"])]

    def get_text(self, option="text", **kwargs):
        if option != "text":
            raise ValueError(f"SourcePage.get_text: only 'text', not {option!r}")
        d = self.page.get_text("dict", clip=self.clip, **kwargs)
        d = glyphs.decode_spans(self.doc, self.page, rawdict=d)
        for b in d.get("blocks", []):
            b["lines"] = [ln for ln in b.get("lines", []) if self._along(ln["dir"])]
        return "\n".join("".join(s["text"] for s in ln["spans"])
                         for b in d.get("blocks", []) for ln in b.get("lines", []))


def _direction(d):
    length = math.hypot(d[0], d[1]) or 1.0
    return (round(d[0] / length, 3), round(d[1] / length, 3))


def segments(year, key, root="."):
    """The rows of `tablesindex.segments` for one table, in order."""
    db = sqlite3.connect(layout.index_sqlite(year, root))
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(
            "SELECT * FROM segments WHERE year=? AND key=? ORDER BY seq",
            (str(year), key))]
    finally:
        db.close()


def table_keys(year, root="."):
    """Every table of the year that has pages, in index order."""
    db = sqlite3.connect(layout.index_sqlite(year, root))
    try:
        return [k for (k,) in db.execute(
            "SELECT DISTINCT key FROM segments WHERE year=? ORDER BY rowid",
            (str(year),))]
    finally:
        db.close()


_DOCS = {}


def open_volume(year, name, sha256, root="."):
    """The source volume, opened once, refused if its bytes are not the ones
    the table index was built from."""
    path = Path(root, REPORTS_DIR, str(year), name)
    if path not in _DOCS:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sha256:
            raise ValueError(f"{path}: SHA-256 {digest[:12]}… is not the "
                             f"{sha256[:12]}… the table index was built from")
        _DOCS[path] = pymupdf.open(path)
    return _DOCS[path]


def table_pages(year, key, root="."):
    """Every source page of table `key`, as SourcePage, in reading order."""
    pages = []
    for seg in segments(year, key, root):
        doc = open_volume(year, seg["source_file"], seg["sha256"], root)
        clips = json.loads(seg["clip"]) if seg.get("clip") else {}
        for p in range(seg["pdf_from"], seg["pdf_to"] + 1):
            printed = (seg["printed_from"] + p - seg["pdf_from"]
                       if seg["printed_from"] is not None else None)
            if seg["cut"] and str(p) not in clips:
                raise ValueError(f"{key}: cut page {p} has no clip; "
                                 f"rebuild the table index")
            pages.append(SourcePage(doc, p, printed, clips.get(str(p))))
    return pages
