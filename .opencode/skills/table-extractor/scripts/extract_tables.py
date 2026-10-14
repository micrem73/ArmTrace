#!/usr/bin/env python3
"""
extract_tables.py -- extract every table from a PDF, alongside an honest
record of which pages could be read at all.

The two halves matter as much as each other. A page whose text layer is
missing, garbled, or ciphered is not a page without a table, and the usual
failure is to report "no tables found" with total confidence. So this script
classifies every page first, and every table it emits carries the state of
the page it came from.

Depends only on pymupdf (+ numpy/pandas for the CSV convenience). camelot is
optional and used solely as a cross-check on ruled pages.

Usage:
    python extract_tables.py document.pdf --out ./out
    python extract_tables.py document.pdf --pages 1-5,9 --method ruled
    python extract_tables.py document.pdf --camelot
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
import tempfile
from collections import defaultdict, deque

try:
    import pymupdf
except ImportError:  # pragma: no cover
    sys.exit("pymupdf is required:  pip install pymupdf")


VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Preflight: can this page be read at all?
# --------------------------------------------------------------------------

# Private Use Area ranges. A subset font with no ToUnicode maps every glyph to
# a PUA codepoint, so the text reads as boxes rather than as the document's
# language.
PUA_RANGES = (
    (0xE000, 0xF8FF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
)


def _is_pua(ch: str) -> bool:
    o = ord(ch)
    for lo, hi in PUA_RANGES:
        if lo <= o <= hi:
            return True
    return False


def analyse_text(text, fonts, images):
    """Classify a page's text layer. Pure function, so it is directly testable.

    `fonts` is a list of font-descriptor strings. Returns the state, the
    reason, and the evidence behind it -- the label is a convenience, the
    evidence is the part worth reading.

    Deliberately judged on what the extractor actually returned, not on what
    the font table implies. An earlier version inferred "ciphered" from an
    /Identity-H font plus low character diversity, and on a real 1000-page
    volume that marked 133 perfectly readable pages as unreadable and skipped
    them, losing their tables. A working ToUnicode CMap decodes /Identity-H
    perfectly well, and the only evidence that matters is whether the output
    is text.
    """
    body = [c for c in text if not c.isspace()]
    n = len(body)
    pua = sum(1 for c in body if _is_pua(c))
    pua_ratio = (pua / n) if n else 0.0
    unique_ratio = (len(set(body)) / n) if n else 1.0
    identity = any("Identity-H" in f for f in fonts)

    if n == 0:
        state = "no-text-layer" if images else "empty"
        reason = (
            "no extractable characters, but raster images are present"
            if images
            else "no extractable characters and no images"
        )
    elif pua_ratio > 0.30:
        # A PUA-heavy page is either a subset font without ToUnicode or an
        # Identity-H encoded one. The font table tells them apart, but the
        # action is the same either way, so this is the only test that
        # matters: did the codes come out as anything but text.
        state = "ciphered" if identity else "garbled"
        reason = f"{pua_ratio:.0%} of characters are Private Use Area codepoints"
    elif unique_ratio < 0.02 and n > 500 and pua:
        # One codepoint per character means two pages of one document agree
        # byte for byte. The PUA guard is what keeps this off hex dumps and
        # on genuine subset-font damage: a page of base64 or hex is just as
        # repetitive and is perfectly readable, and calling it unreadable
        # drops its tables for no reason.
        state = "garbled"
        reason = f"only {len(set(body))} distinct characters across {n}"
    else:
        state = "text"
        reason = "text layer reads as ordinary text"

    return {
        "state": state,
        "reason": reason,
        "chars": n,
        "pua_ratio": round(pua_ratio, 4),
        "unique_ratio": round(unique_ratio, 4),
        "identity_h_font": identity,
        "fonts": sorted(set(fonts)),
        "images": images,
    }


def classify_page(page, index: int) -> dict:
    try:
        text = page.get_text("text")
    except Exception as exc:  # a corrupt content stream
        return {
            "page": index,
            "state": "unreadable",
            "reason": f"text extraction raised {type(exc).__name__}: {exc}",
            "chars": 0,
            "pua_ratio": None,
            "unique_ratio": None,
            "fonts": [],
            "images": 0,
        }

    fonts = []
    try:
        for f in page.get_fonts():
            fonts.append(" ".join(str(x) for x in f))
    except Exception:
        pass
    try:
        images = len(page.get_images())
    except Exception:
        images = 0

    rec = analyse_text(text, fonts, images)
    rec["page"] = index
    try:
        rec["page_height"] = round(page.rect.height, 1)
    except Exception:
        rec["page_height"] = None
    return rec


READABLE = {"text"}
DEGRADED = {"garbled", "ciphered"}
DEAD = {"no-text-layer", "empty", "unreadable"}


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------


def cluster_positions(rules, tol=2.5):
    """Collapse near-coincident rule positions into a mean position.

    `rules` is a list of (position, span) pairs, where span is how far the
    rule extends along the other axis.
    """
    if not rules:
        return []
    rules = sorted(rules, key=lambda t: t[0])
    groups = [[rules[0]]]
    for r in rules[1:]:
        if r[0] - groups[-1][-1][0] <= tol:
            groups[-1].append(r)
        else:
            groups.append([r])
    return [
        (
            sum(r[0] for r in g) / len(g),
            min(r[1] for r in g),
            max(r[2] for r in g),
        )
        for g in groups
    ]


def lines_from_words(words, tol_ratio=0.7):
    """Group words into visual lines by vertical centre.

    Deliberately not using the producer's block/line numbers: those are
    interleaved in documents that mix portrait and rotated content, and the
    geometric answer is the one a reader would draw.
    """
    if not words:
        return []
    heights = [w[3] - w[1] for w in words if w[3] > w[1]]
    tol = max(2.0, statistics.median(heights) * tol_ratio) if heights else 3.0

    ordered = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
    lines = []
    cur = [ordered[0]]
    cur_cy = (ordered[0][1] + ordered[0][3]) / 2.0
    for w in ordered[1:]:
        cy = (w[1] + w[3]) / 2.0
        if cy - cur_cy <= tol:
            cur.append(w)
            cur_cy = (cur_cy * (len(cur) - 1) + cy) / len(cur)
        else:
            lines.append(cur)
            cur = [w]
            cur_cy = cy
    lines.append(cur)

    out = []
    for ws in lines:
        ws.sort(key=lambda w: w[0])
        out.append(
            {
                "words": ws,
                "cy": (ws[0][1] + ws[0][3]) / 2.0,
                "x0": ws[0][0],
                "x1": ws[-1][2],
                "y0": ws[0][1],
                "y1": ws[-1][3],
            }
        )
    return out


# --------------------------------------------------------------------------
# Ruled extraction -- tables drawn with visible borders
# --------------------------------------------------------------------------

# A rule thinner than this is a line, not a box. Two-point is the usual
# minimum; anything above three is a filled cell.
MAX_RULE_THICKNESS = 3.5
MIN_RULE_LENGTH = 18.0


def rule_positions(page):
    """Collect horizontal and vertical rules from the page's vector art.

    Both `l` (line) and `re`/`qu` (rectangle/quad) items count. Generators
    that emit rules as one-point filled rectangles produce no line segments
    at all, so a detector that reads only `l` items reports a ruled table as
    a page with no tables -- which is indistinguishable, downstream, from a
    page that genuinely has none.
    """
    xs, ys = [], []
    try:
        drawings = page.get_drawings()
    except Exception:
        return xs, ys

    for d in drawings:
        for item in d.get("items", ()):
            kind = item[0]
            try:
                if kind == "l":
                    p1, p2 = item[1], item[2]
                    dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
                    if dy <= MAX_RULE_THICKNESS and dx >= MIN_RULE_LENGTH:
                        ys.append((p1.y, p1.x, p2.x))
                    elif dx <= MAX_RULE_THICKNESS and dy >= MIN_RULE_LENGTH:
                        xs.append((p1.x, p1.y, p2.y))
                elif kind == "re":
                    r = item[1]
                    if (
                        r.height <= MAX_RULE_THICKNESS
                        and r.width >= MIN_RULE_LENGTH
                    ):
                        ys.append((r.y0, r.x0, r.x1))
                    elif (
                        r.width <= MAX_RULE_THICKNESS
                        and r.height >= MIN_RULE_LENGTH
                    ):
                        xs.append((r.x0, r.y0, r.y1))
                elif kind == "qu":
                    r = item[1].rect
                    if (
                        r.height <= MAX_RULE_THICKNESS
                        and r.width >= MIN_RULE_LENGTH
                    ):
                        ys.append((r.y0, r.x0, r.x1))
                    elif (
                        r.width <= MAX_RULE_THICKNESS
                        and r.height >= MIN_RULE_LENGTH
                    ):
                        xs.append((r.x0, r.y0, r.y1))
            except (AttributeError, IndexError, TypeError):
                continue
    return xs, ys


def _covered(positions, a0, a1, b0, b1, tol=2.5):
    """True if rules sit at both `a0` and `a1` and both span [b0, b1].

    `positions` are rules on the a-axis, each a (pos, lo, hi) triple giving
    the extent they cover on the b-axis. Both axes have to be checked, and
    each check has to be made against the b-range -- comparing a b-extent
    against an a-coordinate silently passes or fails for reasons that have
    nothing to do with the table.
    """
    if not positions:
        return False
    hit = set()
    for pos, lo, hi in positions:
        if lo > b0 + tol or hi < b1 - tol:
            continue
        if abs(pos - a0) <= tol:
            hit.add(0)
        if abs(pos - a1) <= tol:
            hit.add(1)
    return hit == {0, 1}


def extract_ruled(page, words, min_rows, min_cols, page_no, notes=None):
    xs, ys = rule_positions(page)
    xs = cluster_positions(xs)
    ys = cluster_positions(ys)
    if len(xs) < 2 or len(ys) < 2:
        return []

    lines = lines_from_words(words)

    # Place every word in the grid cell that contains its centre.
    cells = {}
    dropped = []
    for w in words:
        cx, cy = (w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0
        ci = _bucket(xs, cx)
        cj = _bucket(ys, cy)
        if ci is None or cj is None:
            dropped.append((cx, cy))
            continue
        cells.setdefault((ci, cj), []).append(w[4])

    # Text just outside the rules is a different signal from a heading two
    # inches above the table. Only the near misses matter: that is what a
    # missing border looks like, and it costs a real column or row.
    gx0, gx1 = xs[0][0], xs[-1][0]
    gy0, gy1 = ys[0][0], ys[-1][0]
    heights = [w[3] - w[1] for w in words if w[3] > w[1]]
    tol = 2.0 * (statistics.median(heights) if heights else 10.0)
    adjacent = sum(
        1
        for cx, cy in dropped
        if (gx0 - tol <= cx <= gx1 + tol) and (gy0 - tol <= cy <= gy1 + tol)
    )

    # A cell with no text is still part of the table if it is fully enclosed:
    # that is how an empty form, or a table with a blank cell, is captured.
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            if (i, j) in cells:
                continue
            x0, x1 = xs[i][0], xs[i + 1][0]
            y0, y1 = ys[j][0], ys[j + 1][0]
            if _covered(xs, x0, x1, y0, y1) and _covered(ys, y0, y1, x0, x1):
                cells[(i, j)] = []

    occupied = set(cells)
    seen = set()
    tables = []

    for start in sorted(occupied):
        if start in seen:
            continue
        comp = []
        queue = deque([start])
        seen.add(start)
        while queue:
            node = queue.popleft()
            comp.append(node)
            ci, cj = node
            for nb in (
                (ci - 1, cj),
                (ci + 1, cj),
                (ci, cj - 1),
                (ci, cj + 1),
            ):
                if nb in occupied and nb not in seen:
                    seen.add(nb)
                    queue.append(nb)

        rows = sorted({c[1] for c in comp})
        cols = sorted({c[0] for c in comp})
        if len(rows) < min_rows or len(cols) < min_cols:
            continue

        r0, r1 = rows[0], rows[-1]
        c0, c1 = cols[0], cols[-1]
        grid = []
        for j in range(r0, r1 + 1):
            row = []
            for i in range(c0, c1 + 1):
                vals = cells.get((i, j))
                row.append(" ".join(vals).strip() if vals else "")
            grid.append(row)

        ok, why = looks_tabular(grid)
        if not ok:
            if notes is not None:
                notes.append(
                    f"page {page_no}: rejected a {len(grid)}x{len(cols)} ruled "
                    f"block ({why})"
                )
            continue

        y_top = ys[r0][0]
        y_bot = ys[r1][0]
        x_left = xs[c0][0]
        x_right = xs[c1][0]
        # A ruled block two columns wide is indistinguishable from a running
        # head once its text varies page to page, and page position does not
        # separate them either -- real tables sit at the top of the page as
        # often as furniture does. Rather than drop them or pass them off as
        # equal, they are tagged so the caller can filter with --min-cols.
        wide = len(cols) >= 3
        table_warnings = []
        if not wide:
            table_warnings.append(
                "two-column ruled block: this shape is also what running "
                "headers and footers look like, so treat it as unconfirmed"
            )
        if adjacent:
            table_warnings.append(
                f"{adjacent} text item(s) sit immediately outside the ruled "
                "grid and were dropped; a border may be missing, so this table "
                "may be missing a column or a row"
            )
        tables.append(
            {
                "method": "ruled",
                "bbox": [round(x_left, 1), round(y_top, 1), round(x_right, 1), round(y_bot, 1)],
                "rows": grid,
                "page": page_no,
                "confidence": "high" if wide else "low",
                "warnings": table_warnings,
            }
        )
    return tables


def _bucket(positions, value):
    """Index of the band containing `value`, or None."""
    if len(positions) < 2:
        return None
    for i in range(len(positions) - 1):
        if positions[i][0] <= value < positions[i + 1][0]:
            return i
    return None


# --------------------------------------------------------------------------
# Whitespace extraction -- tables with no borders at all
# --------------------------------------------------------------------------


def column_ranges(lines, min_persist, min_gap=6.0):
    """Find column separators as vertical gutters that persist across lines.

    Persistence is the whole point. A one-line gap is just a wide space; a
    gutter that is empty on a large fraction of the lines is a column
    boundary. Requiring it is what stops this method from inventing columns
    out of ordinary prose spacing.
    """
    per_line = [[] for _ in lines]
    for idx, line in enumerate(lines):
        ws = line["words"]
        for a, b in zip(ws, ws[1:]):
            if b[0] - a[2] >= min_gap:
                per_line[idx].append((a[2], b[0]))

    flat = sorted((g, i) for i, gs in enumerate(per_line) for g in gs)
    bands = []
    for g, i in flat:
        if bands and g[0] <= bands[-1]["x1"] + 2.0:
            bands[-1]["x1"] = max(bands[-1]["x1"], g[1])
            bands[-1]["lines"].add(i)
        else:
            bands.append({"x0": g[0], "x1": g[1], "lines": {i}})

    kept = [b for b in bands if len(b["lines"]) >= min_persist]
    if not kept:
        return []

    left = min(l["x0"] for l in lines)
    right = max(l["x1"] for l in lines)
    seps = [(b["x0"] + b["x1"]) / 2.0 for b in kept]
    edges = [left] + seps + [right]
    return list(zip(edges[:-1], edges[1:]))


# A real table has short cells. Prose split by word-wrap has long ones, and a
# two-column block of running text is indistinguishable from a table to any
# amount of whitespace analysis -- it is only distinguishable by looking at
# what ended up in the cells.
PROSE_CELL_CHARS = 120

# A real table fills its columns. A paragraph split across two detected
# columns leaves most cells empty or ragged.
MIN_FULL_ROWS = 0.5


def looks_tabular(grid):
    """Is this a table, or a block of running text that happened to line up?

    Three or more columns is accepted outright: a real grid that wide is
    almost never a paragraph, and real wide tables are the ones worth having.
    Two columns is where the confusion lives, because a paragraph wrapping at
    a fixed width and a two-column table produce identical geometry -- and so
    do the running headers and footers that sit on every page of a report,
    which is the single largest source of junk candidates in practice.

    So narrow grids have to earn their place, and the reason for a rejection
    is reported rather than swallowed.
    """
    n_cols = max((len(r) for r in grid), default=0)
    if n_cols == 0:
        return False, "no columns"
    if n_cols >= 3:
        return True, f"{n_cols} columns"

    full = sum(1 for r in grid if sum(1 for c in r if c.strip()) == n_cols)
    if len(grid) and full / len(grid) < MIN_FULL_ROWS:
        return False, (
            f"only {full}/{len(grid)} rows fill all {n_cols} columns"
        )
    lengths = [len(c) for r in grid for c in r if c.strip()]
    median_len = statistics.median(lengths) if lengths else 0
    if median_len > PROSE_CELL_CHARS:
        return False, (
            f"median cell is {median_len:.0f} characters, which reads as "
            "prose rather than a table"
        )
    tails = [r[1].strip() for r in grid[1:] if len(r) > 1 and r[1].strip()]
    if tails:
        low = sum(1 for t in tails if t[0].islower())
        if low / len(tails) > 0.6:
            return False, (
                f"{low}/{len(tails)} right-hand cells start lower case, "
                "which is prose wrapping at a fixed width"
            )
    return True, f"{full}/{len(grid)} full rows, median cell {median_len:.0f} chars"


def extract_whitespace(page, words, min_rows, min_cols, page_no, notes=None):
    lines = lines_from_words(words)
    if len(lines) < min_rows:
        return []

    min_persist = max(3, int(0.25 * len(lines)))
    ranges = column_ranges(lines, min_persist)
    if len(ranges) < min_cols:
        return []

    heights = [l["y1"] - l["y0"] for l in lines if l["y1"] > l["y0"]]
    line_h = statistics.median(heights) if heights else 12.0
    break_gap = line_h * 2.5

    assigned = []
    for line in lines:
        buckets = [[] for _ in ranges]
        spanning = 0
        for w in line["words"]:
            cx = (w[0] + w[2]) / 2.0
            idx = next(
                (i for i, (a, b) in enumerate(ranges) if a <= cx < b), None
            )
            if idx is None:
                continue
            touched = sum(1 for a, b in ranges if w[0] < b and w[2] > a)
            if touched > 1:
                spanning += 1
            buckets[idx].append(w[4])
        assigned.append((line, buckets, spanning))

    tables = []
    run = []
    prev_cy = None

    def flush():
        if len(run) >= min_rows:
            grid = [[" ".join(c).strip() for c in buckets] for _, buckets, _ in run]
            if any(any(row) for row in grid):
                ok, why = looks_tabular(grid)
                if not ok:
                    if notes is not None:
                        notes.append(
                            f"page {page_no}: rejected a {len(grid)}-row block as "
                            f"prose, not a table ({why})"
                        )
                    run.clear()
                    return
                span_warn = any(sp > 0 for _, _, sp in run)
                first, last = run[0][0], run[-1][0]
                tables.append(
                    {
                        "method": "whitespace",
                        "bbox": [
                            round(first["x0"], 1),
                            round(first["y0"], 1),
                            round(last["x1"], 1),
                            round(last["y1"], 1),
                        ],
                        "rows": grid,
                        "page": page_no,
                        "confidence": "high",
                        "warnings": (
                            ["some cells contain text spanning several columns"]
                            if span_warn
                            else []
                        ),
                    }
                )
        run.clear()

    for line, buckets, sp in assigned:
        filled = sum(1 for b in buckets if b)
        if filled < min_cols:
            flush()
            prev_cy = None
            continue
        if prev_cy is not None and (line["cy"] - prev_cy) > break_gap:
            flush()
        run.append((line, buckets, sp))
        prev_cy = line["cy"]
    flush()
    return tables


# --------------------------------------------------------------------------
# Page-level driver
# --------------------------------------------------------------------------


def extract_page(page, page_no, args, warnings, words, derotated=0):
    """Run detection on one page. Each strategy is guarded independently, so a
    failure in one costs that strategy and not the page."""
    method = args.method
    tables = []
    if method in ("ruled", "both"):
        try:
            tables = extract_ruled(
                page, words, args.min_rows, args.min_cols, page_no, warnings
            )
        except Exception as exc:
            warnings.append(f"page {page_no}: ruled detection failed ({exc})")
    if not tables and method in ("both", "whitespace"):
        try:
            tables = extract_whitespace(
                page, words, args.min_rows, args.min_cols, page_no, warnings
            )
        except Exception as exc:
            warnings.append(f"page {page_no}: whitespace detection failed ({exc})")
    for t in tables:
        t["derotated"] = derotated
    return tables


def orientation_score(words):
    """Median word width / median word height.

    Above 1.0 means the text runs horizontally. Used in preference to
    `page.rotation` because the common real case -- a scanned or re-laid-out
    page -- has the rotation baked into the content stream, where /Rotate
    reads 0 and every geometry-based detector silently transposes the table.
    """
    ws = [w[2] - w[0] for w in words if w[2] > w[0]]
    hs = [w[3] - w[1] for w in words if w[3] > w[1]]
    if not ws or not hs:
        return 1.0
    return statistics.median(ws) / statistics.median(hs)


def dominant_dir(page):
    """The page's dominant writing direction, as a (dx, dy) tuple.

    This is the signal that survives a rotated page. `page.rotation` reads 0
    when the turn was baked into the content stream, and word bounding boxes
    look identical whether text runs left-to-right or right-to-left, so
    neither can tell a correct page from one turned by 180 degrees.
    """
    try:
        d = page.get_text("dict")
    except Exception:
        return None
    counts = {}
    for block in d.get("blocks", ()):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            v = line.get("dir")
            if not v:
                continue
            key = (round(v[0], 2), round(v[1], 2))
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def is_upright(page, words):
    """True when text runs along a horizontal baseline.

    Left-to-right and right-to-left both qualify: both are normal layouts.
    Text running straight up or down the page is what a rotated page looks
    like. The word-shape fallback is only for pages that report no direction
    at all.
    """
    d = dominant_dir(page)
    if d is not None:
        return abs(d[0]) >= abs(d[1])
    if len(words) < 8:
        return True
    return orientation_score(words) >= 0.95


def derive_upright(doc, index, angle):
    """Return (new_doc, page) with the source page's content rotated.

    Embedding through show_pdf_page keeps the text and the vector art in one
    consistent coordinate space, so words and rules stay aligned after the
    turn. Rotating the coordinates by hand is where this goes wrong.
    """
    src = doc[index]
    r = src.rect
    out = pymupdf.open()
    if angle in (90, 270):
        page = out.new_page(width=r.height, height=r.width)
    else:
        page = out.new_page(width=r.width, height=r.height)
    page.show_pdf_page(page.rect, doc, index, rotate=angle)
    return out, page


def extract_with_derotation(doc, index, args, warnings, page_no):
    """Extract from a page, turning it upright first if its content needs it.

    A page turned 90 and a page turned 270 both end up with horizontal text and
    nearly identical shapes, so "does it look upright" cannot choose between
    them -- a shape check picks the mirrored one roughly half the time. The
    tie is broken on writing direction instead: left-to-right is preferred,
    because that is what the overwhelming majority of documents are.

    An genuinely right-to-left page is still extracted rather than turned
    twice; it simply keeps its own direction, which is the correct outcome.
    See references/troubleshooting.md for the caveat.
    """
    page = doc[index]
    try:
        words = page.get_text("words")
    except Exception as exc:
        warnings.append(f"page {page_no}: word extraction failed ({exc})")
        return []

    if is_upright(page, words):
        return extract_page(page, page_no, args, warnings, words, 0)

    candidates = []
    for angle in (90, 270):
        try:
            _tmp, tpage = derive_upright(doc, index, angle)
            twords = tpage.get_text("words")
        except Exception as exc:
            warnings.append(f"page {page_no}: rotation by {angle} failed ({exc})")
            continue
        if not is_upright(tpage, twords):
            continue
        d = dominant_dir(tpage) or (0.0, 0.0)
        got = extract_page(tpage, page_no, args, warnings, twords, angle)
        candidates.append((d[0] > 0, len(got), angle, got))

    if candidates:
        ltr = [c for c in candidates if c[0]]
        best = max(ltr or candidates, key=lambda c: c[1])
        if best[2] is not None:
            warnings.append(
                f"page {page_no}: content was rotated {best[2]} degrees "
                "(/Rotate reads 0 -- the rotation lives in the content stream)"
            )
        return best[3]

    warnings.append(
        f"page {page_no}: text runs vertically and could not be turned "
        "upright; any table here is likely missed"
    )
    return []


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def table_id(tbl):
    raw = json.dumps(
        [
            tbl["method"],
            tbl["page_start"],
            [round(v, 1) for v in tbl["bbox"]],
            tbl["n_rows"],
            tbl["n_cols"],
        ],
        sort_keys=True,
    )
    return "t_" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def trim_empty_edges(grid):
    """Drop trailing (never interior) all-empty rows and columns.

    A blank row in the middle of a grid is structure -- a spacer in a form --
    and is kept. A blank row at the end is an artefact of a page where the
    ruled region continues past the last piece of text, which is exactly what
    the top half of a page-spanning table looks like.
    """
    rows = list(grid)
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    width = max(
        (max((i for i, c in enumerate(r) if c.strip()), default=-1) + 1)
        for r in rows
    )
    return [r[:width] for r in rows]


def finalise(tables, pages):
    for t in tables:
        grid = trim_empty_edges(t["rows"])
        t["rows"] = grid
        t["page_start"] = t.pop("page")
        t.setdefault("page_end", t["page_start"])
        t.setdefault("stitched", False)
        t["n_rows"] = len(grid)
        t["n_cols"] = max((len(r) for r in grid), default=0)
        t["header"] = grid[0] if grid else []
        t["rows_data"] = grid[1:] if len(grid) > 1 else []
        t["page_text_state"] = pages.get(t["page_start"], {}).get("state", "unknown")
        t["page_height"] = pages.get(t["page_start"], {}).get("page_height")
        t["id"] = table_id(t)
    return tables


def furniture_signature(t):
    """A content fingerprint for a small block, stable across pages.

    Page numbers and dot leaders change from page to page, so pure digits and
    bare punctuation are dropped; what is left is the furniture itself.
    """
    toks = []
    for row in t["rows"]:
        for cell in row:
            for w in cell.lower().split():
                w = w.strip(".,;:·…-–—()[]/\\")
                if not w or w.isdigit():
                    continue
                toks.append(w)
    return " ".join(sorted(toks))


# Running heads, feet and watermarks repeat on most pages of a report. Nothing
# about a single instance looks wrong, which is why they survive every
# per-table test; the giveaway is only visible across the whole document.
FURNITURE_MIN_PAGES = 8
FURNITURE_MAX_ROWS = 8


def mark_furniture(tables, min_pages=FURNITURE_MIN_PAGES, max_rows=FURNITURE_MAX_ROWS):
    """Separate recurring page furniture from real tables.

    On a 1048-page parliamentary volume this is the single largest source of
    junk candidates: hundreds of blocks made of the running header and footer.
    Each looks like a tidy little two-column table in isolation.

    The size cap keeps the rule honest. A thirty-row table that legitimately
    recurs -- a schedule reprinted in each annex -- is not furniture, and a
    real table spanning many pages is one record, not many.
    """
    by_sig = defaultdict(set)
    for t in tables:
        by_sig[furniture_signature(t)].add(t["page_start"])

    kept, dropped = [], {}
    for t in tables:
        pages = by_sig[furniture_signature(t)]
        if len(pages) >= min_pages and t["n_rows"] <= max_rows:
            dropped.setdefault(furniture_signature(t), []).append(t)
            continue
        kept.append(t)
    return kept, dropped


def is_continuation(prev, nxt):
    """Does `nxt` look like the rest of `prev` rather than a separate table?

    Matching headers, column counts and page adjacency are all necessary but
    not sufficient: the same table printed twice on facing pages matches on
    every one of them, and merging those two silently concatenates unrelated
    data. The thing that actually distinguishes a continuation is geometry --
    a continuing table runs off the bottom edge of its page and resumes at
    the top of the next, while two independent tables sit wherever they sit.
    """
    ph = prev.get("page_height") or nxt.get("page_height")
    if not ph:
        return False
    prev_bottom = prev["bbox"][3] / ph
    next_top = nxt["bbox"][1] / (nxt.get("page_height") or ph)
    return prev_bottom >= 0.70 and next_top <= 0.30


def stitch(tables, warnings):
    """Merge a table continuing across a page break into one record.

    Deliberately conservative. A weak match here produces a table that looks
    fine and is wrong, which is the most expensive kind of output this script
    can emit.
    """
    if not tables:
        return tables
    by_page = {}
    for t in tables:
        by_page.setdefault(t["page_start"], []).append(t)

    merged = []
    consumed = set()
    for t in tables:
        if t["id"] in consumed:
            continue
        current = t
        page = t["page_start"]
        while True:
            nxt = None
            for cand in by_page.get(page + 1, []):
                if cand["id"] in consumed or cand["method"] != current["method"]:
                    continue
                if cand["n_cols"] != current["n_cols"]:
                    continue
                if cand["header"] != current["header"]:
                    continue
                if not is_continuation(current, cand):
                    continue
                nxt = cand
                break
            if nxt is None:
                break
            consumed.add(nxt["id"])
            grid = (current["rows"] + nxt["rows_data"])
            current["rows"] = grid
            current["rows_data"] = current["rows_data"] + nxt["rows_data"]
            current["n_rows"] = len(grid)
            current["bbox"][3] = nxt["bbox"][3]
            current["page_end"] = nxt["page_end"]
            current["stitched"] = True
            page += 1
        if current.get("stitched"):
            warnings.append(
                f"{current['id']}: stitched across pages "
                f"{t['page_start']}-{page}"
            )
        merged.append(current)
    return merged


def camelot_crosscheck(doc, per_page, warnings):
    """Run camelot on the pages pymupdf called ruled, and compare counts.

    A disagreement is not proof that either is wrong -- the two disagree about
    what counts as a table. It is a pointer at the page most worth looking at.
    """
    try:
        import camelot  # noqa: F401
    except ImportError:
        warnings.append("camelot not installed; cross-check skipped")
        return {}

    report = {}
    for pno in sorted(per_page):
        if not any(t["method"] == "ruled" for t in per_page[pno]):
            continue
        ours = sum(1 for t in per_page[pno] if t["method"] == "ruled")
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = os.path.join(td, "page.pdf")
                one = pymupdf.open()
                src = doc[pno - 1]
                np_ = one.new_page(width=src.rect.width, height=src.rect.height)
                np_.show_pdf_page(np_.rect, doc, pno - 1)
                one.save(tmp)
                one.close()
                theirs = len(camelot.read_pdf(tmp, pages="1", flavor="lattice"))
            report[str(pno)] = {"pymupdf": ours, "camelot": theirs}
            if theirs != ours:
                warnings.append(
                    f"page {pno}: pymupdf found {ours} ruled table(s), camelot "
                    f"found {theirs} -- review this page"
                )
        except Exception as exc:
            report[str(pno)] = {"pymupdf": ours, "error": str(exc)}
            warnings.append(f"page {pno}: camelot cross-check failed ({exc})")
    return report


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def write_outputs(out_dir, tables, pages, warnings, args, crosscheck):
    os.makedirs(out_dir, exist_ok=True)

    preflight = {
        "source": os.path.abspath(args.pdf),
        "pages": [pages[k] for k in sorted(pages)],
        "generated_by": f"table-extractor/{VERSION}",
    }
    with open(os.path.join(out_dir, "preflight.json"), "w", encoding="utf-8") as fh:
        json.dump(preflight, fh, indent=2, ensure_ascii=False)

    # Human-readable summary is printed before the machine-readable payload,
    # so a failure between the two costs the JSON and not the part a person
    # was going to read.
    counts = {}
    for p in pages.values():
        counts[p["state"]] = counts.get(p["state"], 0) + 1
    print("\n=== page readability ===")
    for state, n in sorted(counts.items()):
        print(f"  {state:>16}  {n}")
    unreadable = sorted(
        k for k, p in pages.items() if p["state"] in (DEAD | DEGRADED)
    )
    if unreadable:
        print(
            f"  {len(unreadable)} page(s) could not be read as text; "
            "tables on those pages are missing, not absent."
        )

    by_method = {}
    for t in tables:
        by_method[t["method"]] = by_method.get(t["method"], 0) + 1
    print("\n=== tables ===")
    for m, n in sorted(by_method.items()):
        print(f"  {m:>16}  {n}")
    print(f"  {'total':>16}  {len(tables)}")

    conf = {}
    for t in tables:
        conf[t.get("confidence", "high")] = conf.get(t.get("confidence", "high"), 0) + 1
    if conf.get("low"):
        print(
            f"  {conf['low']} of them are two-column blocks, which is also "
            "the shape of a running header; re-run with --min-cols 3 to keep "
            "only the confident ones"
        )

    if crosscheck:
        print("\n=== camelot cross-check ===")
        for pno, res in sorted(crosscheck.items(), key=lambda kv: int(kv[0])):
            if "error" in res:
                print(f"  page {pno}: error {res['error']}")
            else:
                print(
                    f"  page {pno}: pymupdf {res['pymupdf']} / camelot {res['camelot']}"
                )

    if warnings:
        print("\n=== warnings ===")
        for w in warnings:
            print(f"  ! {w}")

    # Per-table notes belong in the log a person reads, not only in the JSON.
    noted = [t for t in tables if t.get("warnings")]
    if noted:
        print("\n=== table notes ===")
        for t in noted:
            for w in t["warnings"]:
                print(f"  ! {t['id']} (page {t['page_start']}): {w}")

    if not args.json_only:
        csv_path = os.path.join(out_dir, "tables.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "table_id",
                    "page_start",
                    "page_end",
                    "method",
                    "page_text_state",
                    "row",
                    "col",
                    "header",
                    "value",
                ]
            )
            for t in tables:
                for r, row in enumerate(t["rows_data"]):
                    for c, val in enumerate(row):
                        if not val:
                            continue
                        hdr = t["header"][c] if c < len(t["header"]) else ""
                        w.writerow(
                            [
                                t["id"],
                                t["page_start"],
                                t["page_end"],
                                t["method"],
                                t["page_text_state"],
                                r,
                                c,
                                hdr,
                                val,
                            ]
                        )
        print(f"\nwrote {csv_path}")

    json_path = os.path.join(out_dir, "tables.json")
    payload = {
        "source": os.path.abspath(args.pdf),
        "generated_by": f"table-extractor/{VERSION}",
        "params": {
            "method": args.method,
            "min_rows": args.min_rows,
            "min_cols": args.min_cols,
            "stitch": args.stitch,
        },
        "preflight": [pages[k] for k in sorted(pages)],
        "warnings": warnings,
        "camelot_crosscheck": crosscheck,
        "tables": tables,
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"wrote {json_path}")
    return json_path


# --------------------------------------------------------------------------


def parse_pages(spec, total):
    if not spec:
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in out if 1 <= p <= total]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Extract every table from a PDF, with a readability record."
    )
    ap.add_argument("pdf")
    ap.add_argument("--out", default="./tables-out", help="output directory")
    ap.add_argument("--pages", help="e.g. 1-5,9 (1-indexed, inclusive)")
    ap.add_argument(
        "--method",
        default="ruled",
        choices=["ruled", "both", "whitespace"],
        help="ruled: tables with drawn borders only (default, high precision). "
        "both: ruled, falling back to whitespace per page. "
        "whitespace: borderless tables only",
    )
    ap.add_argument(
        "--min-rows",
        type=int,
        default=3,
        help="rows needed to call a block a table (default 3: a header plus "
        "two data rows). Lower it to catch two-row tables at the cost of "
        "more false positives",
    )
    ap.add_argument("--min-cols", type=int, default=2)
    ap.add_argument(
        "--no-stitch", dest="stitch", action="store_false",
        help="do not merge tables continuing across a page break",
    )
    ap.add_argument(
        "--camelot", action="store_true",
        help="cross-check ruled pages with camelot lattice",
    )
    ap.add_argument("--json-only", action="store_true", help="skip tables.csv")
    ap.set_defaults(stitch=True)
    args = ap.parse_args(argv)

    warnings = []
    try:
        doc = pymupdf.open(args.pdf)
    except Exception as exc:
        sys.exit(f"cannot open {args.pdf}: {exc}")

    if doc.needs_pass:
        sys.exit("this PDF is encrypted; remove the password first")

    total = doc.page_count
    wanted = parse_pages(args.pages, total)
    print(f"{args.pdf}: {total} page(s), analysing {len(wanted)}")

    pages = {}
    tables = []
    for pno in wanted:
        page = doc[pno - 1]
        rec = classify_page(page, pno - 1)
        rec["page_number"] = pno
        pages[pno] = rec

        if rec["state"] not in READABLE:
            warnings.append(
                f"page {pno}: {rec['state']} ({rec['reason']}) -- "
                "any table here is missing, not absent"
            )
            continue
        tables.extend(
            extract_with_derotation(doc, pno - 1, args, warnings, pno)
        )

    tables = finalise(tables, pages)
    tables, furniture = mark_furniture(tables)
    if furniture:
        worst = max(furniture.values(), key=len)
        warnings.append(
            f"{sum(len(v) for v in furniture.values())} block(s) on "
            f"{len(furniture)} distinct recurring signature(s) were treated as "
            "page furniture (running heads, feet, watermarks) rather than "
            f"tables; the largest appears on {len(worst)} pages"
        )
    if args.stitch:
        tables = stitch(tables, warnings)

    crosscheck = {}
    if args.camelot:
        per_page = {}
        for t in tables:
            per_page.setdefault(t["page_start"], []).append(t)
        crosscheck = camelot_crosscheck(doc, per_page, warnings)

    write_outputs(args.out, tables, pages, warnings, args, crosscheck)
    return 0


if __name__ == "__main__":
    sys.exit(main())
