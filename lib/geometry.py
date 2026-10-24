#!/usr/bin/env python3
"""
Reading geometry for the per-table PDFs: which way the text runs, where the
rules are, and how a page's glyphs become rows, columns and cells.

Why this module exists
----------------------
The archive does not have one table layout, it has at least four, and none of
them is a plain rectangular grid. Measured on 2025's 110 tables:

    rot90 vs upright   28 / 82      the family-2 MAE tables print their content
                                     rotated 90 degrees inside a portrait A4 page
    ruled vs borderless  hairline rectangles: 0 on all 45 Dogane tables, up to
                                     128 on MAE/C1

Stock extractors lose on both counts, for reasons that are structural rather
than tuning problems:

  * **Rotation is not handled.** A4 portrait, content landscape. Word boxes
    come out 13pt wide and 54pt tall, so anything that assumes rows advance
    with y reads the table sideways. `tabula.read_pdf` has no rotation
    normalisation at all, and `PyMuPDF.Page.find_tables()` returned **zero**
    tables on 2025 A1 page 1 while its text strategy returned a 70x22
    over-segmentation of a 10-column table.
  * **The rules are hairlines drawn as rectangles.** A1's grid is ~82 one-point
    `re` items per page, never a 2-point path, so line-detection strategies find
    nothing to snap to.
  * **Rotation is per content block, not per page.** On A1 page 1 the running
    furniture ("Camera dei Deputati - 66 - Senato della Repubblica", 266 glyphs)
    is *upright* while the table under it (1770 glyphs) is rotated. A page-level
    "is this rotated" flag is therefore wrong by construction; what is needed is
    the dominant direction, with the minority direction accounted for rather
    than ignored.

So this module does four things and nothing else:

  1. `dominant_direction()` -- glyph-count-weighted writing direction, read from
     the content stream rather than guessed from box aspect ratios. The
     aspect-ratio heuristic was measured misfiling MEF GF at 58% "tall", PP at
     60%, NN/OO at 61% and KK1 at 61%, with a clean family-2 majority at 63-90%:
     no threshold separates them, because several tables are genuinely mixed.
  2. `Frame` -- the orthonormal frame implied by a direction: `right` is the
     writing direction and `down` is `right` rotated a quarter turn
     anticlockwise. Every coordinate in this module is *reading space*, in which
     rows advance with `v` and columns with `u`, whatever the page did.
  3. `lines()` / `hairline_census()` -- the vector rule layer, counted in
     reading space, so "does this table have a grid" is a measurement rather
     than an opinion.
  4. `lines_of()` / `row_bands()` / `column_bands()` / `cell_matrix()` --
     glyphs to text lines to bands to cells.

What this module deliberately does not do: decide what a *record* is. A band
that carries an operator's name and a band that carries that operator's subtotal
are both "rows" here. Grouping them into records is `lib/grammar.py`'s job,
because that is a per-ministry question and this module is not allowed to have
opinions about ministries.

The archive is Italian and the tables are printed with Italian typography:
`1.234,56` is one number, `Val. Fini Dog.` is one column heading split over
two printed lines, and `Q.ta'` is a heading with an apostrophe. Nothing here
parses values -- see `ExtractTables.py` for that -- but `LIGATURES` and
`deaccent` exist because header comparison across years needs them.
"""

import math
import re
from collections import Counter, defaultdict


# --------------------------------------------------------------------------
# tolerances
#
# Every threshold below is in points and every one of them was measured rather
# than guessed. They are named so that a run which misreads a table can be
# diagnosed by changing one number rather than by reading the code.
# --------------------------------------------------------------------------

# Two glyph runs belong to the same printed line when their baselines are
# within this distance. A1's body text is 7.5-7.7pt on a 10.2pt leading, so
# adjacent baselines sit ~10pt apart and same-line fragments share a baseline
# exactly; 3pt absorbs the sub-pixel drift pymupdf reports without ever
# merging two lines.
BASELINE_TOL = 3.0

# A gap wider than this multiple of the font size is whitespace between words.
# Proportional faces put real word spaces near 0.25-0.33 em; A1's description
# column sets justified text at ~0.1 em, so the multiplier has to sit below the
# justification gap and above inter-letter tracking.
WORD_GAP_EM = 0.18

# Two words are in the same table cell when their reading-space extents overlap
# by at least this fraction of the narrower one. Overlap rather than midpoint:
# a long wrapped description overruns its own column, and a midpoint test
# would drop those words into the next column.
CELL_OVERLAP = 0.30

# Row bands are cut where the gap to the band below exceeds this multiple of the
# band's own line height. One printed line is one band, which is the right
# primitive for every grammar in the archive: a record that wraps over several
# lines (A1's description column, the Difesa list cells) *should* come back as
# several bands, because deciding that they belong to one record is the job of
# `lib/grammar.py`.
#
# The gap is measured against the *line height*, not against the band so far. An
# earlier version scaled it by the accumulated band height, which grows as the
# band grows, so a tall band eventually swallowed the rest of the page: Difesa
# Annesso 4 came back as one band of 91 words instead of sixty lines.
ROW_GAP_EM = 0.55
ROW_GAP_MIN = 1.5

# A band's cells are separated by at least this multiple of the band's font
# size. Measured on 2025, where the two populations are far apart: a printed
# label arrives as one word run (intra-label advance under 0.2 em) while
# neighbouring columns sit 90-200pt apart at 7-9pt type, i.e. 3-20 em. 1.2 em
# sits in the middle of that gap. The previous absolute 2pt cut failed in the
# opposite direction and split a three-column table into fourteen.
CELL_GAP_EM = 1.2

# A ruled cell edge shorter than this (in points) is a hairline fragment, not a
# cell wall. A1 draws its grid as one-point rectangles.
HAIRLINE_MAX = 3.0

# A rule at least this long is counted as a rule. Anything shorter is an
# underline, a tick or a bullet.
RULE_MIN_LEN = 20.0

# Rule positions closer together than this are the same wall drawn twice. A1
# draws every column edge as a pair of one-point rectangles a point apart, so
# without this its ten columns read as twenty.
RULE_TOL = 2.5

# The shortest rule the grid header accepts. A wall only counts when it spans
# the whole header row, so a short one is safe; 2025 `T2` draws its walls one
# cell high (16 pt).
WALL_MIN_LEN = 4.0

# Header cells are separated by at least this much reading-space white. The
# header of A1 is 10 boxed cells whose labels are 8-14pt apart; its *body* rows
# are separated by ~4pt, which is why the same test cannot be reused for rows.
HEADER_GAP = 2.0

# Characters below this fraction of a page's legible characters are treated as
# a foreign block rather than the page's main content. 2025 A1 page 1 splits
# 1770/266 rotated/upright; the margin furniture of the MEF annexes is a
# similar minority.
FOREIGN_FRACTION = 0.20

# Bezier segments on one page that mean the page is a chart rather than a grid.
# Measured on 2025: MEF NN 2140, OO 984, PP 2010, GF 2424 -- and MAE A1, the
# largest real table in the year at 537 pages, zero.
CHART_CURVES = 200

# Private Use Area, the residue of a subsetted font whose ToUnicode CMap was
# destroyed. See lib/patterns.py (PUA_RE) and lib/pageio.py (is_garbled).
PUA = re.compile(r"[\ue000-\uf8ff]")

LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
})
_ACCENTS = {
    "à": "a", "á": "a", "â": "a", "ä": "a", "è": "e", "é": "e", "ê": "e",
    "ë": "e", "ì": "i", "í": "i", "î": "i", "ï": "i", "ò": "o", "ó": "o",
    "ô": "o", "ö": "o", "ù": "u", "ú": "u", "û": "u", "ü": "u", "ç": "c",
    "À": "A", "Á": "A", "Â": "A", "Ä": "A", "È": "E", "É": "E", "Ê": "E",
    "Ì": "I", "Í": "I", "Î": "I", "Ï": "I", "Ò": "O", "Ó": "O", "Ô": "O",
    "Ö": "O", "Ù": "U", "Ú": "U", "Û": "U", "Ü": "U", "Ç": "C", "'": "",
    "’": "", "`": "", " ": " ", "-": "-",
}


def deaccent(text):
    """Fold an Italian heading to a comparable key.

    Used only for *comparing* headers across years, never for display: the
    printed label is always kept verbatim in the catalog. `Q.ta'` and `Qta'`,
    `Val. Fini Dog.` and `Val Fini Dog` must compare equal, and they do not
    compare equal as codepoints.
    """
    folded = str(text).translate(LIGATURES).lower()
    return "".join(_ACCENTS.get(c, c) for c in folded)


def legible_ratio(text):
    """Share of `text` that is alnum, i.e. not Private Use and not whitespace."""
    if not text:
        return 0.0
    good = sum(1 for c in text if c.isalnum())
    return good / len(text)


def is_garbled(text, threshold=0.15):
    """True when a page's text layer carries more PUA than a real one does.

    The ratio, not the presence, is the test on purpose. 2025 Tabella A1's last
    page is its `Totale Autorizzazioni` sheet with 128 Private Use characters
    among 681 legible ones -- 18.8% against a 15% threshold -- and it *is*
    half subsetted. So "garbled" here means "partly or wholly destroyed", and
    a caller must never read it as "this page is empty".
    """
    if not text:
        return True
    good = sum(1 for c in text if c.isalnum())
    return len(PUA.findall(text)) > threshold * max(1, good)


# --------------------------------------------------------------------------
# 1. which way does the text run
# --------------------------------------------------------------------------


def dominant_direction(page, exclude=None):
    """Glyph-count-weighted writing direction of a page, and its minority.

    `dir` comes from the content stream's text matrix, so it is the truth and
    not a shape heuristic. Returned as

        {"right": (dx, dy), "chars": n, "upright_chars": m, "mixed": bool}

    `upright_chars` counts glyphs written along (1, 0) whatever the dominant
    direction is, which is what identifies the page furniture on a rotated
    table page. Both are kept: a page with 1770 rotated glyphs and 266 upright
    ones is a rotated page *with* furniture, and dropping the second fact would
    let the furniture be read as table content.

    `exclude` drops glyphs whose text matches a predicate before the count, so
    a caller that already knows which band is the margin stamp can ask "which
    way does the *rest* of this page run" without the stamp's own direction
    skewing the answer.
    """
    tally = Counter()
    try:
        spans = page.get_texttrace()
    except Exception:
        spans = []
    for span in spans:
        chars = span.get("chars") or []
        if not chars:
            continue
        if exclude is not None:
            text = "".join(chr(c[0]) for c in chars)
            if exclude(text):
                continue
        dx, dy = span["dir"]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        key = (round(dx / length), round(dy / length))
        tally[key] += len(chars)
    if not tally:
        return {"right": (1.0, 0.0), "chars": 0, "upright_chars": 0,
                "mixed": False, "tally": {}}
    (right, total), = tally.most_common(1)
    upright = tally.get((1, 0), 0)
    foreign = sum(n for d, n in tally.items() if d != right)
    return {
        "right": (float(right[0]), float(right[1])),
        "chars": total,
        "upright_chars": upright,
        "mixed": foreign > FOREIGN_FRACTION * total,
        "tally": {str(k): v for k, v in tally.items()},
    }


def orientation_name(right):
    """`upright`, `rot90`, `rot180`, `rot270` for a writing direction."""
    return {
        (1.0, 0.0): "upright",
        (0.0, 1.0): "rot270",
        (-1.0, 0.0): "rot180",
        (0.0, -1.0): "rot90",
    }.get((round(right[0]), round(right[1])), f"dir{right}")


# --------------------------------------------------------------------------
# 2. the reading frame
# --------------------------------------------------------------------------


class Frame:
    """The orthonormal frame a writing direction implies, in reading space.

    `right` is the writing direction; `down` is `right` rotated a quarter turn
    anticlockwise; `u = p . right` and `v = p . down`. For upright text this is
    the identity, and for A1 -- whose dominant direction is (0, -1) -- it gives
    `u = -y` and `v = x`, which is what the measurements show: the table's
    columns run along decreasing page-y and its rows along increasing page-x,
    with the page furniture, being upright, landing at the largest `v`.

    The quarter-turn choice is not cosmetic. The other rotation puts `down` at
    -x for A1, which reverses the record order of a 537-page table; this one was
    fixed by checking that the printed header row (`Valuta`, `Ammontare`, ...)
    comes out as a single row rather than as columns.
    """

    __slots__ = ("right", "down", "name")

    def __init__(self, right=(1.0, 0.0)):
        self.right = (float(right[0]), float(right[1]))
        rx, ry = self.right
        self.down = (-ry, rx)              # quarter turn anticlockwise
        self.name = orientation_name(self.right)

    @classmethod
    def for_page(cls, page):
        return cls(dominant_direction(page)["right"])

    def u(self, x, y):
        return x * self.right[0] + y * self.right[1]

    def v(self, x, y):
        return x * self.down[0] + y * self.down[1]

    def point(self, x, y):
        return (self.u(x, y), self.v(x, y))

    def size(self, width, height):
        """(width, height) of a page-space rectangle in reading space.

        A portrait page under a rot90 frame becomes a landscape one, so this
        swaps the two; it is what makes a page's `u` extent and `v` extent
        comparable to a table's.
        """
        uw, vh = self.u(width, height), self.v(width, height)
        return (abs(uw), abs(vh))

    def __repr__(self):
        return f"Frame({self.name})"


# --------------------------------------------------------------------------
# 3. the rule layer
# --------------------------------------------------------------------------


def hairline_census(page, frame=None):
    """Count the vector rule layer, in reading space.

    Returns

        {"rects": n, "rules": n, "vertical": n, "horizontal": n,
         "longest": pt, "ruled": bool}

    `ruled` is the measurement that decides which extraction strategy a table
    can possibly use: it is False for all 45 Dogane tables of 2025, whose
    columns have to be recovered from glyph alignment alone. `longest` is the
    single most useful number in the census -- a long rule means a real grid,
    a short one means an underline.
    """
    frame = frame or Frame.for_page(page)
    rects = rules = vertical = horizontal = curves = 0
    longest = 0.0
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for path in drawings:
        for item in path.get("items", ()):
            kind = item[0]
            shape = item[1]
            if kind == "re":
                rects += 1
                w = abs(shape.width)
                h = abs(shape.height)
                if min(w, h) > HAIRLINE_MAX:
                    continue          # a filled box, not a rule
                span = max(w, h)
            elif kind == "l":
                rules += 1
                a, b = item[1], item[2]
                span = math.hypot(b.x - a.x, b.y - a.y)
            elif kind in ("c", "qu"):
                # Bezier curves. Counted and not measured: a chart is drawn with
                # them (2025 MEF NN has 2140 of them on one page against A1's
                # six straight lines), and their presence is what tells a chart
                # from a table when both are ruled and both are rotated.
                curves += 1
                continue
            else:
                continue
            if span < RULE_MIN_LEN:
                continue
            longest = max(longest, span)
            if kind == "l":
                dx, dy = b.x - a.x, b.y - a.y
                vertical += abs(dy) > abs(dx)
                horizontal += abs(dx) >= abs(dy)
    return {
        "rects": rects,
        "rules": rules,
        "vertical": vertical,
        "horizontal": horizontal,
        "curves": curves,
        "longest": round(longest, 1),
        "ruled": longest >= RULE_MIN_LEN,
        # A chart is ruled too -- 2025 MEF `NN` runs a 447pt rule across the page
        # -- so "has rules" does not distinguish one from a table. Curves do:
        # NN carries 2140 Bezier segments where A1 has none, because NN is a
        # pie chart (`Grafico Ripartizione percentuale per istituti di credito`)
        # and A1 is a grid.
        "charted": curves >= CHART_CURVES,
    }


# --------------------------------------------------------------------------
# 4. glyphs -> lines -> bands -> cells
# --------------------------------------------------------------------------


class Word:
    """One word in reading space.

    `u0`/`u1` is the word's extent along the line, `v0`/`v1` its extent across
    it, and `size`, `bold` and `font` come from the span it was cut from, which
    is what lets a heading be told from a value without reading either.
    """

    __slots__ = ("text", "u0", "u1", "v0", "v1", "size", "bold", "font",
                 "foreign")

    def __init__(self, text, u0, u1, v0, v1, size, bold, font, foreign=False):
        self.text = text
        self.u0, self.u1 = u0, u1
        self.v0, self.v1 = v0, v1
        self.size = size
        self.bold = bold
        self.font = font
        self.foreign = foreign

    @property
    def vmid(self):
        return 0.5 * (self.v0 + self.v1)

    @property
    def umid(self):
        return 0.5 * (self.u0 + self.u1)

    def __repr__(self):
        return f"Word({self.text!r}, u=({self.u0:.0f},{self.u1:.0f}), " \
               f"v=({self.v0:.0f},{self.v1:.0f}))"


def rule_boundaries(page, frame=None, tol=RULE_TOL):
    """Column and row separator positions, in reading space.

    Returns `{"u": [...], "v": [...]}`: the `u` positions of separators that run
    along `v` (i.e. they divide columns) and the `v` positions of those that run
    along `u` (they divide rows).

    This is the other source of column geometry besides the header labels, and
    on a ruled table it is the *better* one. A header label is left-aligned
    inside its cell while the data under it is usually right-aligned, so mapping
    values onto label extents misplaces them: 2025 `EE` has
    `Importi Segnalati` printed at u 344.6-379.1 and `Importi Accessori
    Segnalati` at 396.9-451.4, while the values sit at 371.6-394.8 and
    453.5-468.0 -- so the first value of every row that fits it overlaps both
    labels and lands in the wrong column. The rules give the true cell edges:
    6 separators, 5 columns, exactly the printed header.

    Two tolerances matter. Rules are drawn as pairs a point apart (A1's grid
    draws each column wall twice, 1pt apart), so positions are merged within
    `tol`. And a borderless table still returns *some* positions -- MG10's
    running-head underline and its margin stamp box come back as ten -- so the
    caller must check the count against the header before believing any of it.
    """
    frame = frame or Frame.for_page(page)
    upos, vpos = [], []
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for path in drawings:
        for item in path.get("items", ()):
            if item[0] == "re":
                shape = item[1]
                a_u, b_u = frame.u(shape.x0, shape.y0), frame.u(shape.x1, shape.y1)
                a_v, b_v = frame.v(shape.x0, shape.y0), frame.v(shape.x1, shape.y1)
                span_u, span_v = abs(b_u - a_u), abs(b_v - a_v)
                lo_u, lo_v = min(a_u, b_u), min(a_v, b_v)
            elif item[0] == "l":
                p1, p2 = item[1], item[2]
                a_u, b_u = frame.u(p1.x, p1.y), frame.u(p2.x, p2.y)
                a_v, b_v = frame.v(p1.x, p1.y), frame.v(p2.x, p2.y)
                span_u, span_v = abs(b_u - a_u), abs(b_v - a_v)
                lo_u, lo_v = min(a_u, b_u), min(a_v, b_v)
            else:
                continue
            if span_v > RULE_MIN_LEN and span_u <= HAIRLINE_MAX:
                upos.append(lo_u)
            elif span_u > RULE_MIN_LEN and span_v <= HAIRLINE_MAX:
                vpos.append(lo_v)
    return {"u": _merge_positions(upos, tol), "v": _merge_positions(vpos, tol)}


def rule_segments(page, frame=None, min_len=RULE_MIN_LEN):
    """Every rule of the page as a segment in reading space, with its extent.

    Returns `{"walls": [(u, v0, v1)], "floors": [(v, u0, u1)]}`: walls run
    along `v` and divide columns, floors run along `u` and divide rows.
    `rule_boundaries` keeps only positions, so it cannot tell a column wall
    from the running head's underline; the grid header needs the extent.

    A stroked rectangle wider than a hairline is a cell drawn as a box (2025
    `EE` draws its grid that way), so its four edges are rules too; a filled
    box with no stroke is a shaded cell and is not.

    `min_len` is the shortest segment kept. The grid header lowers it: 2025
    `T2` draws its column walls cell by cell, each one row high, under 20 pt.
    """
    frame = frame or Frame.for_page(page)
    walls, floors = [], []

    def add(p1, p2):
        a_u, b_u = frame.u(p1.x, p1.y), frame.u(p2.x, p2.y)
        a_v, b_v = frame.v(p1.x, p1.y), frame.v(p2.x, p2.y)
        if abs(b_v - a_v) > min_len and abs(b_u - a_u) <= HAIRLINE_MAX:
            walls.append(((a_u + b_u) / 2, min(a_v, b_v), max(a_v, b_v)))
        elif abs(b_u - a_u) > min_len and abs(b_v - a_v) <= HAIRLINE_MAX:
            floors.append(((a_v + b_v) / 2, min(a_u, b_u), max(a_u, b_u)))

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for path in drawings:
        stroked = "s" in (path.get("type") or "")
        for item in path.get("items", ()):
            if item[0] == "l":
                add(item[1], item[2])
            elif item[0] == "re":
                r = item[1]
                if min(abs(r.width), abs(r.height)) <= HAIRLINE_MAX:
                    mid_x, mid_y = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                    if abs(r.width) <= HAIRLINE_MAX:
                        add(_P(mid_x, r.y0), _P(mid_x, r.y1))
                    else:
                        add(_P(r.x0, mid_y), _P(r.x1, mid_y))
                elif stroked:
                    for a, b in ((r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)):
                        add(a, b)
    return {"walls": walls, "floors": floors}


class _P:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x, self.y = x, y


def grid_header(page, words, frame=None, tol=RULE_TOL, is_label=None):
    """The column header a ruled table prints, read off its grid, or None.

    The header is the topmost row of the grid: two floors with at least three
    walls running from one to the other (two outer edges, one or more column
    walls). Its columns are the gaps between those walls, and each label is
    every word printed inside its gap, line by line -- so a label set on two
    lines (`Numero di operazioni / svolte`, DOG M1) is one label, and labels
    that run into each other on one line (`Numero di operazioni Stato di
    avanzamento annuale`) are still two, because a wall separates them. The
    printed edges beat the gaps between words (DECISIONS.md, 2026-10-17).

    A title underline, the running head's rule or a caption above the table
    has no walls under it, so it is never taken for the header.

    The columns are those of the first body row when it has more: a table
    whose rows are labelled leaves the header cell over the labels unprinted
    (2025 `T2`: no rule above its row names), so that column has an empty
    label rather than being lost.

    `is_label(texts)` lets the caller refuse a first grid row that is data: a
    continuation page (2025 `EE` p2) prints no header, and its first row is
    then not one. What a value looks like is `lib/grammar`'s business, not
    this module's.

    Returns `{"cells": [{"text", "u0", "u1", "bold"}], "v0", "v1"}`, `v1` being
    the floor under the header: data starts below it.
    """
    frame = frame or Frame.for_page(page)
    segs = rule_segments(page, frame, min_len=WALL_MIN_LEN)
    walls = _join_walls(segs["walls"], tol)
    if len(walls) < 3:
        return None
    levels = join_floors(segs["floors"], tol)
    def walls_across(top, bottom, u_lo, u_hi):
        return sorted(_merge_positions(
            [u for u, v0, v1 in walls
             if v0 <= top + tol and v1 >= bottom - tol and u_lo <= u <= u_hi],
            tol))

    def next_level(i):
        for j in range(i + 1, len(levels)):
            if levels[j][0] - levels[i][0] >= ROW_GAP_MIN:
                return j
        return None

    def _stub(cells, v1):
        """Columns the first body row has beyond the header's edges, unlabelled
        (T13: the breakdown and code columns under no header cell)."""
        m = next((x for x in range(len(levels)) if levels[x][0] - v1 >= ROW_GAP_MIN), None)
        if m is None:
            return cells
        # As wide as the floor under the header: a cell spanning the body rows
        # (T13's rotated breakdown) has no floor of its own between them.
        floor = next((lv for lv in levels if abs(lv[0] - v1) <= tol), levels[m])
        body = walls_across(v1, levels[m][0], floor[1] - tol, floor[2] + tol)
        left = [u for u in body if u < cells[0]["u0"] - tol]
        right = [u for u in body if u > cells[-1]["u1"] + tol]
        def empty(a, b):
            return {"text": "", "over": "", "u0": a, "u1": b, "bold": False}
        return ([empty(a, b) for a, b in zip(left, left[1:] + [cells[0]["u0"]])] + cells
                + [empty(a, b) for a, b in zip([cells[-1]["u1"]] + right, right)])

    for i, (top, lo, hi) in enumerate(levels):
        j = next_level(i)
        if j is None:
            break
        bottom, lo2, hi2 = levels[j]
        u_lo, u_hi = max(lo, lo2) - tol, min(hi, hi2) + tol
        across = walls_across(top, bottom, u_lo, u_hi)
        inside = [w for w in words
                  if top < w.vmid < bottom and u_lo < w.umid < u_hi]
        if len(across) < 3 or not inside:
            continue                # the next floor down closes no grid row
        k = next_level(j)
        spans = None
        if k is not None:
            body = walls_across(bottom, levels[k][0],
                                min(lo2, levels[k][1]) - tol,
                                max(hi2, levels[k][2]) + tol)
            if len(body) > len(across) and body[0] <= across[0] + tol \
                    and body[-1] >= across[-1] - tol:
                spans, across = across, body
        two = None
        if spans is not None:
            # The lower row ends at a floor as wide as the grid: a highlighted
            # column's fill (T10's 2025, yellow) draws floors of its own width.
            under = next((lv for lv in levels[j + 1:] if lv[0] - bottom >= ROW_GAP_MIN
                          and lv[1] <= across[0] + tol and lv[2] >= across[-1] - tol), None)
            two = under and _two_levels(words, _merge_positions(spans, 2 * tol),
                                        _merge_positions(across, 2 * tol),
                                        top, bottom, under[0], is_label)
            if two:
                return {"cells": _stub(two, under[0]), "v0": top, "v1": under[0]}
        if not two and i > 0 and top - levels[i - 1][0] >= ROW_GAP_MIN:
            # A row above whose floor runs only under its wide label (3B's
            # `INCASSO`): this row is the lower one, the labels over it are there.
            prev = levels[i - 1][0]
            up = walls_across(prev, top, across[0] - tol, across[-1] + tol)
            if 3 <= len(up) < len(across) and up[0] <= across[0] + tol \
                    and up[-1] >= across[-1] - tol:
                two = _two_levels(words, _merge_positions(up, 2 * tol),
                                  _merge_positions(across, 2 * tol),
                                  prev, top, bottom, is_label)
                if two:
                    return {"cells": _stub(two, bottom), "v0": prev, "v1": bottom}
        cells = []
        for a, b in zip(across, across[1:]):
            mine = sorted((w for w in inside if a < w.umid < b),
                          key=lambda w: (round(w.v0), w.u0))
            cells.append({"text": " ".join(" ".join(w.text for w in mine).split()),
                          "u0": a, "u1": b,
                          "bold": any(w.bold for w in mine)})
        if is_label is not None and not is_label([c["text"] for c in cells]):
            return None             # the grid starts with data: no header here
        return {"cells": cells, "v0": top, "v1": bottom}
    return None


def _two_levels(words, spans, walls, top, middle, bottom, is_label):
    """A header on two grid rows, or None: a label over several columns
    (`Anno 2022` over `N. | Valore in € | %`, T10; `INCASSO` over its years,
    Difesa 3B), and labels standing over both rows (`Paese`). The upper row's
    walls are `spans`, the lower's `walls`. Each column gets its own label
    (`text`) and the one printed over it (`over`, empty when its label stands
    over both rows). Only when the lower row is a row of labels: else the
    columns under a wide header cell are data (T2 leaves its stub unlabelled)."""
    def text(u0, u1, v0, v1):
        mine = sorted((w for w in words if u0 < w.umid < u1 and v0 < w.vmid < v1),
                      key=lambda w: (round(w.v0), w.u0))
        return " ".join(" ".join(w.text for w in mine).split()), any(w.bold for w in mine)
    cells = []
    for a, b in zip(walls, walls[1:]):
        lo, hi = next(((x, y) for x, y in zip(spans, spans[1:])
                       if x <= a + RULE_TOL and y >= b - RULE_TOL), (a, b))
        if hi - lo <= b - a + RULE_TOL:         # one column wide: its label is all of it
            label, bold = text(a, b, top, bottom)
            cells.append({"text": label, "over": "", "u0": a, "u1": b, "bold": bold,
                          "lower": False})
        else:
            label, bold = text(a, b, middle, bottom)
            over, _ = text(lo, hi, top, middle)
            cells.append({"text": label, "over": over, "u0": a, "u1": b, "bold": bold,
                          "lower": True})
    lower = [c["text"] for c in cells if c.pop("lower")]
    if not lower or (is_label is not None and not is_label(lower)):
        return None
    return cells


def join_floors(floors, tol=RULE_TOL):
    """Collinear floors at one v, as one rule `[v, u0, u1]`: a grid drawn cell
    by cell (2025 `T2`) gives one floor piece per cell."""
    levels = []
    for v, u0, u1 in sorted(floors):
        if levels and v - levels[-1][0] <= tol:
            levels[-1][1] = min(levels[-1][1], u0)
            levels[-1][2] = max(levels[-1][2], u1)
        else:
            levels.append([v, u0, u1])
    return levels


def _join_walls(walls, tol):
    """Collinear wall pieces that touch, joined into one wall: 2025 `T2` draws
    each wall one text line at a time, so no piece spans a two-line row."""
    joined = []
    for u, v0, v1 in sorted(walls, key=lambda w: (round(w[0] / tol), w[1])):
        last = joined[-1] if joined else None
        if last and abs(u - last[0]) <= tol and v0 <= last[2] + tol:
            last[2] = max(last[2], v1)
        else:
            joined.append([u, v0, v1])
    return [tuple(w) for w in joined]


def _merge_positions(values, tol):
    """Collapse near-identical positions, keeping the first of each cluster."""
    merged = []
    for value in sorted(values):
        if not merged or value - merged[-1][-1] > tol:
            merged.append([value])
        else:
            merged[-1].append(value)
    return [round(sum(group) / len(group), 1) for group in merged]


def columns_from_boundaries(positions, u_lo=None, u_hi=None):
    """Turn separator positions into `[{"u0", "u1", "index"}]` cells.

    `u_lo`/`u_hi` clip the span: a ruled table's page carries rules that belong
    to the running head and the margin stamp as well (2025 A1 has one past the
    last column), and those would invent a column the header does not have.
    """
    pos = [p for p in positions
           if u_lo is None or p >= u_lo - RULE_TOL
           if u_hi is None or p <= u_hi + RULE_TOL]
    cells = []
    for i in range(len(pos) - 1):
        cells.append({"u0": pos[i], "u1": pos[i + 1], "index": i})
    return cells


def lines_of(page, frame=None, foreign_dirs=()):
    """Words of a page, grouped into printed lines and returned reading-order.

    Lines come from `get_texttrace` rather than `get_text("words")` for three
    reasons, all of which cost something on this archive: the trace carries the
    font, the size and the writing direction per span, so a heading is
    identifiable as a heading; its glyph boxes are exact, so a one-point rule
    and a glyph edge can be compared without a tolerance guess; and it does not
    shred a rotated page, where `get_text("words")` returns boxes 13pt wide and
    54pt tall that no column logic can use.

    Within a span, words are cut on gaps wider than `WORD_GAP_EM` of the font
    size. That is deliberately below the justification gap of A1's description
    column, which is set at ~0.1em, so a justified description does not come
    back as one enormous "word".

    `foreign_dirs` lists writing directions to keep out of the result: pass the
    page furniture's direction to read only the table, which on a rotated page
    is the difference between A1's data and its `Camera dei Deputati` banner.
    """
    frame = frame or Frame.for_page(page)
    # `foreign_dirs` may be given either as (dx, dy) pairs or as integer ticks.
    # Both are normalised because the frame's own direction comes out as floats
    # from `dominant_direction` and as integers from a caller's literal, and
    # `(0.0, -1.0) != (0, -1)` in a set comparison -- so a caller passing
    # `[(1, 0)]` silently filtered nothing at all and the page furniture was
    # read as table content. 2025 MEF `NN` came back as 279 "foreign" words and
    # 7 real ones, i.e. the filter removed the table and kept the banner.
    foreign = {(round(float(d[0])), round(float(d[1])))
               for d in foreign_dirs}

    # Words are ordered by their position *along the text matrix*, which PyMuPDF
    # reports per span, so a rotated page is emitted in rotated reading order
    # and this function cannot know that a word belongs to a foreign block
    # until the spans have been compared. Everything below therefore sorts by
    # reading-space coordinates, never by extraction order.

    # (direction, baseline-v, span) -> glyph list, then words per span.
    runs = defaultdict(list)
    spans = []
    try:
        trace = page.get_texttrace()
    except Exception:
        trace = []
    for span in trace:
        chars = span.get("chars") or []
        if not chars:
            continue
        dx, dy = span["dir"]
        length = math.hypot(dx, dy) or 1.0
        direction = (round(dx / length), round(dy / length))
        is_foreign = direction in foreign
        bold = "bold" in span.get("font", "").lower()
        # A span's baseline is the v of its glyph origins.
        for ch in chars:
            text, origin, bbox = chr(ch[0]), ch[2], ch[3]
            if not text.strip() and ch[0] == 0:
                continue        # glyph the extractor could not name
            u0, v0 = frame.point(origin[0], origin[1])
            u1, v1 = frame.point(bbox[2], bbox[1])
            runs[(direction, round(v0, 1), id(span))].append(
                (text, min(u0, u1), max(u0, u1), min(v0, v1), max(v0, v1),
                 span.get("size", 0.0), bold, span.get("font", ""), is_foreign)
            )
        spans.append(span)

    words = []
    for (direction, _baseline, _sid), glyphs in runs.items():
        glyphs.sort(key=lambda g: g[1])
        current = []
        for glyph in glyphs:
            # Bold faked by printing the text twice a hair apart (MAE B4
            # `Area GeograficaArea Geografica` in one span, the copy 0.19 pt
            # on): a letter repeated within half its own width is that letter
            # once. A real double letter stands its whole width on: `zz` in
            # `Autorizzazioni`, MEF II's `S.p.A..` (each stop 1.0 pt wide,
            # the second 0.97 pt on).
            prev = current[-1] if current else None
            if (prev and glyph[0] == prev[0] and not glyph[0].isspace()
                    and glyph[1] - prev[1] < 0.5 * (prev[2] - prev[1])):
                continue
            if current:
                gap = glyph[1] - current[-1][2]
                em = current[-1][5] or glyph[5]
                if em and gap > WORD_GAP_EM * em:
                    words.append(_join(current))
                    current = []
            current.append(glyph)
        if current:
            words.append(_join(current))

    # A word printed twice in one place is one word: 2025 T2 overprints
    # `Programmi intergovernativi (%)`; MAE B4 fakes bold with a second copy
    # 0.2 pt off (`Area Geografica` twice).
    kept = []
    for w in sorted(words, key=lambda w: (w.v0, w.u0)):
        if not any(w.text == x.text and abs(w.u0 - x.u0) < 1 and abs(w.v0 - x.v0) < 1
                   for x in kept[-8:]):
            kept.append(w)
    words = kept
    words.sort(key=lambda w: (-w.v1, w.u0))     # top band first, left to right
    return words


def _join(glyphs):
    text = "".join(g[0] for g in glyphs)
    return Word(
        text,
        min(g[1] for g in glyphs), max(g[2] for g in glyphs),
        min(g[3] for g in glyphs), max(g[4] for g in glyphs),
        max(g[5] for g in glyphs),
        any(g[6] for g in glyphs),
        glyphs[0][7],
        glyphs[0][8],
    )


def row_bands(words, gap_em=ROW_GAP_EM):
    """Group words into visual rows: `[band, [Word, ...]]`, top band first.

    `v` grows downward in reading space, so the topmost band is the one with
    the smallest `v0` and the bands come out in reading order already.

    A new band starts when a word lies *entirely* below the current one plus
    `gap_em` of the line's own font size -- not of the band's accumulated
    height. That distinction is the whole function: the accumulated version
    grows its own tolerance as it grows, so the Difesa Annesso 4 table, whose
    cells wrap over ten printed lines each, came back as a single band of 91
    words and every column collapsed into one.
    """
    ordered = sorted(words, key=lambda w: (w.v0, w.u0))
    bands = []
    for word in ordered:
        if bands:
            band = bands[-1]
            line_v = band[0].v0                # this band's own top, not its extent
            em = max(w.size for w in band) or word.size
            gap = max(ROW_GAP_MIN, gap_em * em)
            # Compared against the band's *top line*, never its bottom. A cell
            # whose text wraps over ten lines has a v extent covering the whole
            # record, and a test against that extent absorbs every following
            # line into one band: Difesa Annesso 4, whose `DITTE ITALIANE` cell
            # runs twenty lines deep, came back as four bands for three whole
            # pages with the header buried inside the first data row.
            if word.v0 > line_v + gap:
                bands.append([word])
                continue
            band.append(word)
        else:
            bands.append([word])
    for band in bands:
        band.sort(key=lambda w: w.u0)
    return bands


def column_bands(band, gap_em=CELL_GAP_EM):
    """Split one band into its cells, on reading-space gaps.

    The cut is `gap_em` of the band's font size, not a fixed number of points.
    Measured on 2025: MG10's three columns sit 94pt and 105pt apart at 9.1pt
    type, EE's five sit 15-84pt apart at 4.8pt type, A1's ten sit 13-115pt
    apart at 7.4pt type -- while a printed label arrives as one word run whose
    internal advance is under 0.2em. An absolute 2pt cut therefore split MG10
    into fourteen cells and read EE as six.

    Overlap-merge would *not* work here either: A1's `Materiale oggetto del
    contratto` is wider than its own column and abuts `Tipo`, so an overlap test
    merges the two and returns nine columns for a ten-column table.
    """
    words = sorted(band, key=lambda w: w.u0)
    em = max((w.size for w in words), default=0.0) or 1.0
    gap = gap_em * em
    cells = []
    for word in words:
        if cells and word.u0 - cells[-1][-1].u1 < gap:
            cells[-1].append(word)
        else:
            cells.append([word])
    out = []
    for cell in cells:
        text = " ".join(w.text for w in cell)
        out.append({
            "text": text,
            "u0": min(w.u0 for w in cell),
            "u1": max(w.u1 for w in cell),
            "v0": min(w.v0 for w in cell),
            "v1": max(w.v1 for w in cell),
            "bold": any(w.bold for w in cell),
            "size": max(w.size for w in cell),
            # The words themselves, so a caller that needs to cut a stacked cell
            # further -- a wrapped column name that a cell gap did not separate
            # -- can do so from the geometry rather than re-deriving it.
            "words": cell,
        })
    return out


def cell_matrix(bands, columns):
    """Place each band's words into `columns`, returning one list of cells per band.

    A word goes to the column it overlaps most, so a description that overruns
    its own column still lands in that column rather than being pushed into the
    next one. Unfilled positions come back as "" -- which is how a blank cell
    and a missing cell are told apart later: the archive prints both, and only
    one of them means zero.
    """
    if not columns:
        return [["" for _ in bands] for _ in ()]
    rows = []
    for band in bands:
        cells = [""] * len(columns)
        last = [None] * len(columns)
        for word in _reading_order(band):
            best, best_overlap = None, CELL_OVERLAP
            for i, col in enumerate(columns):
                overlap = min(word.u1, col["u1"]) - max(word.u0, col["u0"])
                if overlap <= 0:
                    continue
                if overlap > best_overlap:
                    best, best_overlap = i, overlap
            if best is None:                       # no overlap: nearest column
                best = min(
                    range(len(columns)),
                    key=lambda i: min(abs(word.u0 - columns[i]["u0"]),
                                      abs(word.u1 - columns[i]["u1"])))
            # Two words that touch on one line are one word, printed in two
            # spans: Difesa 4 `THALES IT` + `ALIA.`, `MBDA ITALIA` + `.`.
            prev = last[best]
            # A word ending in its own printed space (T2 `Paesi `) is not,
            # unless it is a number cut in two: a digit and its space, then a
            # digit starting inside that space (3B `  3 ` + `2.288,00 €`, the
            # page prints 32.288,00 €; `1 ` + `.000.000.000,00 €`). Words do overlap across a real space
            # (DOG M `BENELLI ` + `ARMI`, 0.4 pt): only digits are joined.
            near = (prev is not None and abs(word.v0 - prev.v0) < BASELINE_TOL
                    and word.u0 - prev.u1 <= WORD_GAP_EM * (word.size or prev.size))
            over = (near and prev.text[-1:].isspace() and word.u0 < prev.u1
                    and prev.text.strip()[-1:].isdigit()
                    and re.match(r"[.,]?\d", word.text.strip()) is not None)
            touching = near and (not prev.text[-1:].isspace() or over)
            head = cells[best].rstrip() if over else cells[best]
            cells[best] = (head + ("" if touching else " ") + word.text).strip()
            last[best] = word
        rows.append(cells)
    return rows


def _reading_order(words):
    """Line by line, left to right. A line is the words within `BASELINE_TOL`
    of its first: a raised footnote marker (Difesa 4 `(Fincantieri)¹.`, 0.6 pt
    higher) stays where it is printed, not ahead of its line."""
    lines = []
    for w in sorted(words, key=lambda w: w.v0):
        if lines and w.v0 - lines[-1][0].v0 < BASELINE_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [w for line in lines for w in sorted(line, key=lambda w: w.u0)]


def page_text(page):
    """Whole-page text, for legibility and fingerprinting."""
    try:
        return page.get_text("text") or ""
    except Exception:
        return ""


def page_legibility(page):
    """(text_status, legible_pages_fraction_input) for one page.

    `garbled` is the archive's third corruption class' cousin: a subset font
    whose ToUnicode was destroyed, so the characters are Private Use Area and
    no reader can recover them. `ciphered` is `/Identity-H` with no ToUnicode
    and no /Differences, where the text reads as a substituted alphabet.
    """
    text = page_text(page)
    if not text.strip():
        return "empty", text
    if is_garbled(text):
        return "garbled", text
    if re.search(r"/[A-Z]{2,}[\]\[\^_`]", text) and legible_ratio(text) < 0.5:
        return "ciphered", text
    return "legible", text


def fingerprint(page):
    """The catalog's per-page signature: geometry, legibility, direction.

    Cheap enough to run on every page of every table -- which is the point,
    because a table's legibility is a property of the whole file and a
    three-page sample would quietly overstate it on the 221-page garbled `E`.
    """
    direction = dominant_direction(page)
    text, status = page_legibility(page)
    census = hairline_census(page)
    frame = Frame(direction["right"])
    words = lines_of(page, frame)
    body = [w for w in words if not w.foreign]
    return {
        "orientation": frame.name,
        "direction": direction["right"],
        "mixed_direction": direction["mixed"],
        "upright_chars": direction["upright_chars"],
        "chars": direction["chars"],
        "status": status,
        "pua_ratio": round(
            len(PUA.findall(text)) / max(1, sum(1 for c in text if c.isalnum())), 3),
        "hairlines": census["ruled"],
        "longest_rule": census["longest"],
        "rects": census["rects"],
        "words": len(body),
        "bands": len(row_bands(body)) if body else 0,
    }