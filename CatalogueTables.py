#!/usr/bin/env python3
"""
CatalogueTables -- fingerprint every per-table PDF of one year into a database
of *shapes*, so that a later step can ask what changed between years.

    Out/PDF/<authority>/[<article>/]<table><year>.PDF
        ->  Out/CATALOG/catalog.sqlite
            Out/CATALOG/catalog-<year>.jsonl
            Out/CATALOG/census-<year>.md

This is the step that answers "what changed in the same table, what disappeared,
what appeared, and how did the data inside change". It deliberately does *not*
extract data: it records what each table *is* -- its columns, their labels, their
value shapes, its record grammar, its legibility -- so the diff across years is a
comparison of measurements rather than of text scraped twice.

Why the catalog exists at all, and why it is not just "run tabula and diff the
CSVs"
------------------------------------------------------
Because a table's shape is not stable and not even uniform within a year. Measured
on 2025's 110 tables: 28 print their content rotated 90 degrees, 45 print no
vector rules at all, and the row grain differs by ministry -- MAE groups rows
under an operator and closes each group with a printed subtotal, MEF repeats a
`Causale` sub-dimension inside one authorisation number, the Dogane print one
flat record per operator, and the Difesa print cells holding lists wrapped over
ten lines. A CSV produced per table would encode all four grains as
indistinguishable rectangles, and the year-over-year diff of those CSVs would
report a change in every column of every table.

What is stored per (authority, article, code, year)
---------------------------------------------------
  identity      pages, bytes, source span, index title, and the *witness* for the
                table's existence -- index / bookmark / header-run / garbled /
                missing. The witness is not decoration: the catalog inherits
                step 1's coverage, so a table that "disappeared" in a year must
                be distinguishable from a table step 1 stopped finding. Without
                it the diff would confidently report 21 of 31 tables lost in 2024
                vol I, where the truth is that 335 of 1016 pages are garbled and
                the header scan cannot see them.
  geometry      orientation (from the content stream, not a box heuristic),
                whether the direction is mixed, hairline census, text density
  columns       per ordinal: the label **as printed**, a comparable key, the
                value shapes observed in it, sample values, width, and whether
                the header is a single row or a stacked one
  grammar       flat / group+subtotal / repeated-subdimension / wrapped-list /
                unknown, with the measured features it was decided from
  legibility    legible / garbled / ciphered / empty page counts over the *whole*
                file. Sampled, this would overstate a 221-page table of which
                216 pages are destroyed.
  totals        how many bands carry a `Totale` label, and in which columns

Ordering is deliberate and follows the same shape as the rest of the pipeline:
`lib/geometry.py` knows about glyphs, `lib/grammar.py` knows about printed
values and bands, this script knows about files and ministries, and nothing below
knows about `reports_185_1990/` paths.

Usage:
    ./CatalogueTables.py --year 2025
    ./CatalogueTables.py --year 2025 --manifest Out/manifest-2025.jsonl
    ./CatalogueTables.py --year 2025 --sample 7 --dry-run
    ./CatalogueTables.py --all --out-root Out

Requires: pymupdf (pip install pymupdf). No JRE, no tabula, no OCR.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

try:
    import pymupdf
except ImportError:
    sys.exit("Errore: pymupdf non installato. Esegui: pip install pymupdf")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import geometry as geo            # noqa: E402
from lib import grammar as gra             # noqa: E402
from lib import ontology                   # noqa: E402

PDF_EXT = ".PDF"
CATALOG_DIR = "CATALOG"

# Pages per table read for *geometry*. Geometry is the expensive half and the
# signature has to be stable, not exhaustive, so it is read on a spread of
# pages and then checked for agreement between them. The count is a compromise
# measured on 2025: A1 repeats its header on all 537 pages, while MG10's data is
# on one page of two, so a sample has to include both ends and the middle.
DEFAULT_SAMPLE = 5

# A band's cells must be at least this fraction text-shaped for the band to be
# the header. Without it a wide data row (A1's material description runs to
# 94pt) can outscore the real header on cell count.
HEADER_TEXT_SHARE = 0.5

# A stacked header -- `Anno 2022 / Valore in €` over `Anno 2022 / Quantita` -- is
# only merged into the band below when this much of it sits over the same
# columns. Set generously: a super-header that does not line up would put the
# wrong word on every column of the table.
HEADER_OVERLAP = 0.6

# How far a header label may sit outside the outermost rules before the rules
# are judged not to describe this table at all. A ruled table's edge is a
# fraction of a point outside its labels (2025 EE: 468.9 against 451.4), while
# MG10's stray rules come from a running-head underline well away from the
# table (u -105 against labels ending at -143.9).
RULE_EDGE_SLACK = 20.0

# The running furniture and the margin stamp are not table columns, and on a
# rotated page they sit *outside* the table's reading-space extent, so they
# never reach `cell_matrix`. They still show up in a *header candidate* band,
# which is why 2025 A1 was briefly catalogued as three columns named
# `MAECI - UAMA - CENTRO INFORMATICO`, `Pagina 1 di 537`, `TAB A1`: the stamp is
# printed rotated on a rotated page, so it forms its own valid-looking band.
MARGIN_STAMP_RE = re.compile(
    r"Pagina\s+\d+\s+di\s+\d+|CENTRO\s+INFORMATICO|Camera\s+dei\s+Deputati|"
    r"Senato\s+della\s+Repubblica|LEGISLATURA|DISEGNI\s+DI\s+LEGGE", re.I)
FURNITURE_RE = re.compile(r"^TAB\.?\s|^Annesso\s", re.I)

# How wide a band must be, relative to a candidate header, to count as its data.
# A header's own labels are as wide as its columns, while a wrapped continuation
# is narrower, so 0.6 admits the data and rejects the fragments.
FOLLOW_SHARE = 0.6
FOLLOW_WINDOW = 4

# The longest a header cell may be. Measured against the archive's real column
# names: the longest is `Materiale oggetto del contratto` at 31 characters
# (A1) and `Importi Accessori Segnalati` at 28 (EE). A data cell runs to
# hundreds -- `VITROCISET (oggi LEONARDO), SIRIO PANEL (oggi LEONARDO),
# INTRTEL, REVELLI, SKF INDUSTRIE, ...` -- so 60 is a wide margin that still
# separates the two populations.
HEADER_CELL_MAX_CHARS = 60

# How many printed bands a stacked header may occupy. Two is the measured case
# (DOG N1 wraps every long column name once); three is the ceiling, because a
# four-band group is a title block rather than a header.
STACK_MAX_BANDS = 3

# How many cells a band in a stacked header may have. DOG N1's two header bands
# carry two cells each and the union is four; a data row carries four on its own.
# So a group whose bands are already this wide is not a header.
STACK_MAX_CELLS = 3

# How much of the column-gap threshold the widest gap inside a stacked cell
# must reach before the cell counts as several columns. Below 1.0 a wrapped
# column name keeps its internal line breaks together; at 1.0 the cell splits
# exactly at a column gutter. Measured on 2025 DOG N1, where one 321pt cell
# holds three column names and the gutters inside it are 1.4-1.9 em against a
# word space of 0.2 em.
STACK_SPLIT_SHARE = 1.0

# How close two data cells must start, in points, to be the same column when the
# columns come from the body rather than from the rules. Tight, because the
# archive right-aligns its numbers and left-aligns its text, so a value's `u0`
# moves with its length: `6.418.312,50` and `40,00` are the same column and
# start 30pt apart.
CONSENSUS_TOL = 12.0

# And how many of the sampled bands must place a cell at that position. A
# majority, so a value that shifts once does not become a column.
CONSENSUS_SHARE = 0.6

# How close two identical words must start, in points, to count as one word
# drawn twice. Tight, because a header cell holding `A | A` is two different
# things; loose enough for the sub-point drift between two overlays.
DOUBLE_TOL = 3.0

# How far outside a rescued column a word may start and still belong to the
# label of that column. A label is set inside its cell but a long one overhangs
# the rule that bounds it -- DOG N1's `Utilizzo anni precedenti (Euro)` starts
# 4pt left of its column's left edge -- so the cut has to be forgiving at the
# edges while still separating the words that lie beyond.
LABEL_EDGE_SLACK = 8.0

# Three Private Use characters in a row. The archive's garbled pages carry whole
# words this way (one PUA codepoint per original character), so a run means the
# letters are gone even where the page-wide ratio stays under the legibility
# threshold -- which is exactly 2025 MAE `H1`, whose labels are unreadable
# while its digits still parse.
PUA_RUN_RE = re.compile("[\ue000-\uf8ff]{3}")

SCHEMA = """
PRAGMA journal_mode=WAL;

-- One row per table per year. The primary key is the manifest key, the triple
-- (authority, article, code): a code is unique only inside that scope, so
-- keying on the code alone is what used to make the Dogane overwrite the MAE.
CREATE TABLE IF NOT EXISTS table_year (
    authority      TEXT NOT NULL,
    article        TEXT,
    code           TEXT NOT NULL,
    year           INTEGER NOT NULL,
    path           TEXT,
    pages          INTEGER,
    bytes          INTEGER,
    src_first      INTEGER,          -- first page in the source volume, 1-based
    src_last       INTEGER,
    index_title    TEXT,             -- the title the volume's own index prints
    witness        TEXT,             -- index | bookmark | header-run | garbled
                                    -- | missing  (why the table is believed)
    orientation    TEXT,             -- upright | rot90 | rot180 | rot270 | unknown
    mixed_dir      INTEGER,          -- 1 when the page carries a second direction
    ruled          INTEGER,          -- 1 when a rule of >= 20pt is present
    longest_rule   REAL,
    legible_pages  INTEGER,
    garbled_pages  INTEGER,
    ciphered_pages INTEGER,
    empty_pages    INTEGER,
    words          INTEGER,
    bands          INTEGER,
    n_columns      INTEGER,
    stacked_header INTEGER,
    header_stable  INTEGER,          -- 1 when every sampled page agreed
    grammar        TEXT,
    grammar_detail TEXT,             -- json: the features the label came from
    band_kinds     TEXT,             -- json: histogram of band kinds
    repeated_keys  TEXT,             -- json: columns that repeat across bands
    total_bands    INTEGER,          -- bands carrying a Totale label
    columns        TEXT,             -- json: the column signature
    extractable    INTEGER,          -- 1 when data can actually be read
    extract_reason TEXT,
    fingerprint    TEXT,
    PRIMARY KEY (authority, article, code, year)
);

-- One row per page, for the legibility census. Kept because "garbled on all
-- 221 pages" and "garbled on 216 of 221" are different facts and only the
-- second one can be summarised honestly by a count.
CREATE TABLE IF NOT EXISTS page_stat (
    authority TEXT NOT NULL, article TEXT, code TEXT NOT NULL,
    year INTEGER NOT NULL, page INTEGER NOT NULL,
    orientation TEXT, status TEXT, pua_ratio REAL,
    ruled INTEGER, longest_rule REAL, words INTEGER, bands INTEGER,
    PRIMARY KEY (authority, article, code, year, page)
);

CREATE INDEX IF NOT EXISTS ix_ty_year   ON table_year(year);
CREATE INDEX IF NOT EXISTS ix_ty_grammar ON table_year(grammar);
CREATE INDEX IF NOT EXISTS ix_ps_table  ON page_stat(authority, article, code, year);

-- The column signature as rows, because the cross-year diff is a SQL question
-- and a json blob is not one. `label` is the printed string and `label_key` the
-- comparable form (accent- and apostrophe-folded); keeping both is what lets a
-- rename be reported as a rename rather than as a removal plus an addition.
CREATE TABLE IF NOT EXISTS column_def (
    authority TEXT NOT NULL, article TEXT, code TEXT NOT NULL,
    year INTEGER NOT NULL, ordinal INTEGER NOT NULL,
    label TEXT, label_key TEXT, header_path TEXT,
    role TEXT, width REAL, bold INTEGER,
    shapes TEXT, samples TEXT,
    PRIMARY KEY (authority, article, code, year, ordinal)
);
CREATE INDEX IF NOT EXISTS ix_cd_key ON column_def(label_key);
"""


# --------------------------------------------------------------------------
# reading one page
# --------------------------------------------------------------------------


def page_summary(page):
    """The cheap per-page record: legibility, direction, rule census.

    ~90ms/page measured on 2025 vol I, so a whole year is a few minutes. The
    expensive part of `geo.fingerprint` -- building every word -- is deliberately
    not here: this runs on every page of every table, where the question is
    "can this page yield text at all", not "what is on it".
    """
    direction = geo.dominant_direction(page)
    status, text = geo.page_legibility(page)
    census = geo.hairline_census(page)
    return {
        "orientation": geo.Frame(direction["right"]).name,
        "status": status,
        "pua_ratio": round(
            len(geo.PUA.findall(text))
            / max(1, sum(1 for c in text if c.isalnum())), 3),
        "ruled": 1 if census["ruled"] else 0,
        "longest_rule": census["longest"],
        "chars": direction["chars"],
        "upright_chars": direction["upright_chars"],
    }


def page_geometry(page, direction):
    """The expensive per-page record: bands, columns, cell matrix.

    Returns `None` for a page that cannot yield text, because a band list built
    from Private Use Area characters is a plausible-looking fabrication and must
    never reach the catalog.
    """
    if geo.page_legibility(page)[0] != "legible":
        return None
    # A partly-destroyed page is not usable either. The ratio test passes at 15%
    # of Private Use, and 2025 MAE `H1` clears it while every *label* on the
    # page is a run of PUA codepoints -- so the catalog recorded its columns as
    # ` `, a plausible-looking signature of
    # nothing. The extra test is on the extracted text rather than on the ratio:
    # three Private Use characters in a row mean the letters are gone.
    if PUA_RUN_RE.search(geo.page_text(page)):
        return None
    frame = geo.Frame(direction["right"])
    foreign = [(1.0, 0.0)] if frame.name != "upright" else []
    words = [w for w in geo.lines_of(page, frame, foreign_dirs=foreign)
             if not w.foreign]
    if not words:
        return None
    bands = geo.row_bands(words)
    return {"frame": frame, "bands": bands, "words": words}


def _band_is_doubled(band):
    """True when the same words appear twice at the same reading-space position.

    The archive has pages where a table is drawn twice, exactly overlaid, once
    per text layer. 2025 MAE `O1` is one, and the duplication is invisible in
    the cell text unless the words are compared by position: every cell reads
    `X X` rather than `X`.

    A header name never repeats inside itself, so this is a safe rejection. The
    test is on the *words* rather than on the joined text so that a legitimate
    header containing a repeated word -- `PARTICIPANTI | DITTE ITALIANE |
    PARTECIPANTI` in Difesa Annesso 4, which repeats a word but at two
    different positions -- is not caught by it.
    """
    cells = geo.column_bands(band)
    for cell in cells:
        words = cell.get("words") or []
        if len(words) < 2:
            continue
        for a, b in zip(words, words[1:]):
            if a.text == b.text and abs(a.u0 - b.u0) <= DOUBLE_TOL:
                return True
    return False


def _band_is_headerish(band):
    """Would this band pass the *shape* half of the header tests?

    Split out because `find_header` needs the same three shape tests twice: once
    when scoring candidates, and once to insist that the winner is the first such
    band on the page rather than merely the best one.
    """
    cells = geo.column_bands(band)
    if len(cells) < 3:
        return False
    shapes = [gra.value_shape(c["text"]) for c in cells]
    texty = sum(1 for s in shapes if s in (gra.SHAPE_TEXT, gra.SHAPE_EMPTY))
    if texty < HEADER_TEXT_SHARE * len(cells):
        return False
    if any(s in (gra.SHAPE_NUMBER, gra.SHAPE_CODE, gra.SHAPE_CURRENCY)
           for s in shapes):
        return False
    if any(len(gra.normalise_text(c["text"])) > HEADER_CELL_MAX_CHARS
           for c in cells):
        return False
    return True


def find_header(bands, rules=None):
    """(index, cells) of the band that is this table's column header.

    `rules` is `geo.rule_boundaries`' `u` list for the page. It is what rescues a
    header whose labels run into each other: DOG N1 prints
    `Numero di operazioni | Stato di avanzamento annuale | Utilizzo anni
    precedenti (Euro)` in a 321pt cell whose internal gaps are 0.78-1.13 em,
    all below the 1.2 em column gap, so no gap test can find three columns
    there. The page's rules do -- and when they do, and they fall inside the
    header's own extent, they are used as the cells and the labels are mapped
    onto them. That is the same argument as `resolve_columns`, applied one step
    earlier: the printed *edges* beat the printed *labels*.

    "The band with the most cells" is the obvious rule and it is wrong twice
    over on 2025:

      MG10  every band has three cells -- the header
            `Denominazione operatore | Quantita | Valore (Euro)` and the data
            `ELETTRONICA S.P.A. | 20 | 3.317.854,95` are equally wide -- so the
            count ties and an arbitrary winner gives a data row as the column
            definition;
      Difesa Annesso 4  the running furniture band
            `Camera dei Deputati | Senato della Repubblica | XIX LEGISLATURA |
            DISEGNI DI LEGGE ...` is drawn twice on the page, once per text
            layer, and has more words than any real band.

    So a candidate must satisfy all four:

      >= 3 cells        a two-cell band is a running header, not a column list
      mostly text       a header is words; a data band carries numbers
      short cells       a header names columns, a data row holds a paragraph
      no bare numbers   `2`, `EFA`, `VELIVOLO` are a row of Annesso 4, not names
      followed by data  a band nearly as wide follows within four lines
      first of its kind on the page, not merely the best-looking one

    Ties are broken towards the earliest candidate, because on every page that
    repeats its header the first one is the authoritative printing of it.

    Returns `(None, [])` when nothing qualifies, which is a real answer for a
    cover page and for a page whose header is not on it.
    """
    found = _single_band_header(bands)
    if found is None or found[0] is None:
        found = _stacked_band_header(bands, rules)
    if found is None or found[0] is None:
        return None, []
    index, cells = found
    if rules:
        ruled = _header_by_rules(bands, index, cells, rules)
        if ruled:
            return index, ruled
    return index, cells


def _header_by_rules(bands, index, cells, rules):
    """Re-cut a header band's columns with the page's rules, when they fit.

    Only used when the label-based cut produced fewer columns than the rules do
    inside the header's own extent -- the signature of labels that have run into
    each other. Returns `None` when the rules do not describe this band, so a
    borderless table is never given columns it does not have.
    """
    if not cells:
        return None
    lo = min(c["u0"] for c in cells)
    hi = max(c["u1"] for c in cells)
    ruled = geo.columns_from_boundaries(rules, lo, hi)
    if len(ruled) <= len(cells):
        return None
    for cell in ruled:
        cell["text"] = _label_at(cells, 0.5 * (cell["u0"] + cell["u1"]),
                                 cell["u0"], cell["u1"])
        cell["bold"] = False
        cell["size"] = 0.0
    return ruled


def _single_band_header(bands):
    """The usual case: one printed band carries every column name."""
    counts = [len(geo.column_bands(b)) for b in bands]
    best = None
    for i, band in enumerate(bands[:40]):
        # The header is the *first* qualifying band. Annesso 4 prints no header
        # on its later pages, so every band there is a data band, and scoring
        # them all and taking the best assembled a signature out of rows on
        # different pages.
        if i and _band_is_headerish(bands[i - 1]):
            continue
        # A band whose words are *each* duplicated down the page is not a
        # header. 2025 MAE `O1` prints its table twice overlaid -- `GE AVIO
        # S.R.L. GE AVIO S.R.L. | EX EX | AUSTRIA AUSTRIA | 755.849,61
        # 755.849,61` -- and the doubled band is wider than the real header two
        # bands above it, so the widest-band rule picked it and reported the
        # column names as `GE AVIO S.R.L. GE AVIO S.R.L. | EX EX | ...`.
        # Repeated identical runs at the same position are the signature.
        if _band_is_doubled(band):
            continue
        # The band must not be the running furniture or the margin stamp. Both
        # form valid-looking bands -- on a rotated page the stamp is rotated too
        # -- and both would otherwise read as a column header.
        if any(MARGIN_STAMP_RE.search(w.text) or FURNITURE_RE.search(w.text)
               for w in band):
            continue
        if not _band_is_headerish(band):
            continue
        cells = geo.column_bands(band)
        # The shape tests are deliberately strict and each one cost a wrong
        # signature, so they are named here rather than inline:
        #   no bare numbers   a wrapped-list table's data row is as wide and as
        #                     wordy as its header, so "widest and texty" picks a
        #                     data row -- Annesso 4 was catalogued with the
        #                     column names `2`, `EFA`, `VELIVOLO`, `ITALIA,`
        #   no long cells     Annesso 4's header is `NR. PROGRAMMA | TIPOLOGIA |
        #                     PAESI | DITTE ITALIANE` and its data cells run to
        #                     twenty company names
        if i and _band_is_headerish(bands[i - 1]):
            continue
        # Every cell must carry text. Without this the Difesa Annesso 4 header
        # is missed, because its printed form is
        # `NR. | PROGRAMMA | TIPOLOGIA | PAESI | PARTECIPANTI | DITTE ITALIANE |
        # PARTECIPANTI` -- seven labels, with `PARTECIPANTI` repeated over both
        # of the columns it spans.
        if any(not gra.normalise_text(c["text"]) for c in cells):
            continue
        if not any(counts[j] >= FOLLOW_SHARE * len(cells)
                   for j in range(i + 1, min(i + 1 + FOLLOW_WINDOW, len(counts)))):
            continue
        key = (len(cells), -i)
        if best is None or key > best[0]:
            best = (key, i, cells)
    if best is None:
        return None, []
    return best[1], best[2]


def _stack_columns(group):
    """The columns of a header printed over several bands.

    Two steps, and the first one is the one that matters. A cell in a stacked
    header is *itself* a stack of lines, so `column_bands` returns one cell
    holding several labels run together:

        Numero di operazioni Stato di avanzamento annuale Utilizzo anni
        precedenti (Euro)

    which is three column names in one 321pt cell beside a 95pt cell. Measuring
    only where the lines *end* would see two columns; measuring where they
    *start* sees five starts for four columns, because `(Euro)` sits under
    `Stato di avanzamento annuale` and not under `Utilizzo anni precedenti`.

    So the bands are cut at the widest em gap inside each cell -- which is the
    column gutter, since no intra-column line break is that wide -- and the
    pieces are then merged across bands by overlap, which is what joins
    `Numero di operazioni` to `svolte`.
    """
    pieces = []
    for band in group:
        for cell in geo.column_bands(band):
            for part in _split_stacked_cell(cell):
                pieces.append(part)
    pieces.sort(key=lambda c: c["u0"])
    merged = []
    for part in pieces:
        if merged and min(merged[-1]["u1"], part["u1"]) - max(
                merged[-1]["u0"], part["u0"]) > 0:
            last = merged[-1]
            last["u1"] = max(last["u1"], part["u1"])
            last["text"] = (last["text"] + " " + part["text"]).strip()
            last["size"] = max(last["size"], part["size"])
            last["bold"] = last["bold"] or part["bold"]
        else:
            merged.append(dict(part))
    return merged


def _split_stacked_cell(cell):
    """Split one cell's text at its widest internal em gap.

    `cell["words"]` is the band's words that fell in this cell. The split is at
    the largest gap *relative to the font size*, not the largest gap in points:
    a column gutter is at least `CELL_GAP_EM` wide by construction, while the
    line breaks inside a wrapped name are ordinary word spaces.
    """
    words = cell.get("words") or []
    if len(words) < 2:
        return [cell]
    em = max((w.size for w in words), default=0.0) or 1.0
    gaps = [words[i + 1].u0 - words[i].u1 for i in range(len(words) - 1)]
    widest = max(gaps)
    if widest < geo.CELL_GAP_EM * em * STACK_SPLIT_SHARE:
        return [cell]
    cut = gaps.index(widest)
    out = []
    for group in (words[:cut + 1], words[cut + 1:]):
        out.append({
            "u0": min(w.u0 for w in group), "u1": max(w.u1 for w in group),
            "v0": min(w.v0 for w in group), "v1": max(w.v1 for w in group),
            "text": " ".join(w.text for w in group),
            "bold": any(w.bold for w in group),
            "size": max(w.size for w in group),
        })
    return out


def _stacked_band_header(bands, rules=None):
    """A header printed over two or three bands, found as one.

    Needed because a wrapped column name splits the header band in two, and a
    two-cell band is not a candidate. 2025 DOG `N1` prints

        Denominazione operatore | Numero di operazioni | Stato di avanzamento annuale
        (Euro) | Utilizzo anni precedenti

    with `Numero di operazioni` / `svolte`, `Stato di avanzamento annuale` /
    `(Euro)` and `Utilizzo anni precedenti` / `(Euro)` each broken across two
    lines, so the band is three cells wide and the second band is two. Neither
    alone has three cells, and every header-only test rejects them.

    Consecutive bands are stacked when they all carry short text, when their
    cells are *disjoint* in reading space (the whole point -- a data row
    repeats its leftmost column down the band rather than continuing it), and
    when a band at least as wide follows. The last condition is what keeps a
    title block and a data row from being joined: `Tipo di operazione:
    Temporanea Esportazione (TE) | Riepilogo per operatore` is two cells wide
    and is followed by a three-cell header.
    """
    counts = [len(geo.column_bands(b)) for b in bands]
    for i in range(min(len(bands) - 1, 40)):
        if counts[i] > STACK_MAX_CELLS:
            continue
        group = [bands[i]]
        j = i + 1
        while j < len(bands) and len(group) < STACK_MAX_BANDS:
            if any(MARGIN_STAMP_RE.search(w.text)
                   or FURNITURE_RE.search(w.text) for w in bands[j]):
                break
            # A band this wide is data, so the group ends *here* -- it is not a
            # reason to abandon the candidate. DOG N1's two header bands hold two
            # cells each and the row below them holds four, so growing the group
            # unconditionally and then rejecting it discarded the only header on
            # the page.
            if counts[j] > STACK_MAX_CELLS:
                break
            group.append(bands[j])
            j += 1
        if len(group) < 2:
            continue
        cells = _stack_columns(group)
        rescued = False
        if rules:
            # The rules rescue runs *before* the width test. DOG N1's four
            # column names come back as two label cells -- one holding three
            # names run together, 91 characters -- and every width test rejects
            # that, while the page's own rules say four columns. Tested before
            # the rescue, the header is lost; tested after, it is recovered.
            lo = min(c["u0"] for c in cells)
            hi = max(c["u1"] for c in cells)
            span = _data_span(bands, j, counts)
            if span and span[0] < lo - geo.RULE_TOL:
                lo = min(lo, span[0])
            ruled = geo.columns_from_boundaries(rules, lo, hi)
            data_cells = _consensus_columns(bands, j, counts)
            rescued = False
            # Whichever of the two witnesses saw more columns wins, and the
            # body's consensus is allowed to overrule the rules. That ordering
            # is measured, not preferred: on DOG N1 the rules yield three
            # columns for a four-column table, because the page's leftmost
            # column edge is simply not drawn, while all 89 body rows agree on
            # four positions. A partial drawing is a fact about the drawing; the
            # body is the table.
            if len(data_cells) > len(ruled):
                chosen, rescued = data_cells, True
            elif len(ruled) > len(cells):
                chosen, rescued = ruled, True
            else:
                chosen = cells
            if chosen is not cells:
                for cell in chosen:
                    cell["text"] = _label_at(cells,
                                             0.5 * (cell["u0"] + cell["u1"]),
                                             cell["u0"], cell["u1"])
                    cell["bold"] = False
                    cell["size"] = 0.0
                cells = chosen

        # The width test is on the *labels*, and it runs only when the labels
        # are still the column definition. After the rescue above they have
        # been cut to the individual column names, so a group whose labels were
        # 91 characters because they held three names each is no longer over the
        # limit -- testing before the rescue was what lost DOG N1's header
        # twice, once for being too narrow and once for being too long.
        if len(cells) < 3:
            continue
        texts = [gra.normalise_text(c["text"]) for c in cells]
        if any(not t for t in texts):
            continue
        if rescued and not any(len(t) > HEADER_CELL_MAX_CHARS for t in texts):
            pass                     # already cut to single names by position
        elif any(len(t) > HEADER_CELL_MAX_CHARS for t in texts):
            continue
        if any(gra.value_shape(t) in (gra.SHAPE_NUMBER, gra.SHAPE_CODE,
                                       gra.SHAPE_CURRENCY) for t in texts):
            continue
        if not any(counts[k] >= FOLLOW_SHARE * len(cells)
                   for k in range(j, min(j + FOLLOW_WINDOW, len(counts)))):
            continue
        return i, cells
    return None, []


def _label_at(cells, mid, lo=None, hi=None):
    """The part of a label cell that belongs to the column centred on `mid`.

    Always cut by position, never returned whole. A rescued column is usually
    narrower than the label cell it came from: 2025 DOG N1's second header cell
    is 91 characters and holds three column names
    (`Numero di operazioni Stato di avanzamento annuale Utilizzo anni
    precedenti (Euro)`), and four columns are rescued from it. Returning that
    cell whole for each of them gives four identical 91-character "names", so
    `lo`/`hi` default to the column's own extent and only the words inside it
    are taken.

    Falls back to the nearest cell by midpoint when the column falls in a gap
    between label cells, which happens where a column has no label at all.
    """
    if lo is None or hi is None:
        lo, hi = mid, mid
    best = None
    for cell in cells:
        words = [w for w in (cell.get("words") or [])
                 if w.u1 >= lo - LABEL_EDGE_SLACK
                 and w.u0 <= hi + LABEL_EDGE_SLACK]
        if words:
            words.sort(key=lambda w: w.u0)
            best = " ".join(w.text for w in words)
            break
    if best:
        return best
    for cell in cells:
        if cell["u0"] <= mid <= cell["u1"]:
            return cell["text"]
    return min(cells,
               key=lambda c: abs(0.5 * (c["u0"] + c["u1"]) - mid))["text"]


def _words_within(cell, lo, hi):
    """The words of `cell` whose extent lies inside `[lo, hi]`.

    Falls back to the whole label when the cell has no words of its own, which
    is the case for a column that came from the rules in the first place.
    """
    words = [w for w in (cell.get("words") or [])
             if w.u1 >= lo - LABEL_EDGE_SLACK and w.u0 <= hi + LABEL_EDGE_SLACK]
    if not words:
        return cell["text"]
    words.sort(key=lambda w: w.u0)
    return " ".join(w.text for w in words)


def _consensus_columns(bands, start, counts, window=12):
    """The columns the *data* bands agree on.

    The witness for a table whose vector layer is incomplete. DOG N1 draws
    column rules at -368.6, -249.8 and -130.9 and then jumps straight to -460,
    so the four-column body is bounded by three of its own edges: no rule-based
    cut finds four columns on that page, however the rules are read.

    The body is not missing the information, though -- all 89 of its rows are
    four cells wide at four consistent reading-space positions, because the
    columns are where the *values* sit. So the columns are clustered from the
    data cells themselves: every cell's `u0` is collected, and positions within
    `CONSENSUS_TOL` of each other are one column. Clusters are then required to
    be supported by a majority of the sampled bands, which is what separates a
    real column from a right-aligned value that happens to start elsewhere.

    Returns `[]` rather than a guess when the bands disagree, because a wrong
    column count is worse than an absent one: the catalog records the miss and
    the next table gets a full signature.
    """
    seen = []
    for k in range(start, min(start + window, len(bands))):
        if counts[k] < 3:
            continue
        seen.append(geo.column_bands(bands[k]))
    if len(seen) < 3:
        return []
    positions = []
    for cells in seen:
        for cell in cells:
            positions.append((cell["u0"], cell["u1"]))
    if not positions:
        return []
    median_cells = max(len(c) for c in seen)
    clusters = []
    for u0, u1 in sorted(positions):
        if clusters and abs(u0 - clusters[-1]["u0"]) <= CONSENSUS_TOL:
            cluster = clusters[-1]
            cluster["u0"] = (cluster["u0"] + u0) / 2
            cluster["u1"] = max(cluster["u1"], u1)
            cluster["n"] += 1
        else:
            clusters.append({"u0": u0, "u1": u1, "n": 1})
    need = max(2, int(CONSENSUS_SHARE * len(seen)))
    kept = [c for c in clusters if c["n"] >= need]
    if len(kept) != median_cells or len(kept) < 3:
        return []
    return [{"u0": c["u0"], "u1": c["u1"], "text": "", "bold": False,
             "size": 0.0} for c in kept]


def _data_span(bands, start, counts):
    """The reading-space extent of the data bands following a header.

    Used to check whether the header's own labels reach past its first rule. On
    DOG N1 they do, by 99pt: `Denominazione operatore` is printed left of the
    rule that would have bounded its column, because the header is not aligned
    to the grid the body uses.
    """
    lo = hi = None
    for k in range(start, min(start + FOLLOW_WINDOW, len(bands))):
        if counts[k] < 3:
            continue
        band_lo = min(w.u0 for w in bands[k])
        band_hi = max(w.u1 for w in bands[k])
        lo = band_lo if lo is None else min(lo, band_lo)
        hi = band_hi if hi is None else max(hi, band_hi)
    return (lo, hi) if lo is not None else None


def resolve_columns(page, header_cells, frame=None):
    """The column geometry to trust: the rules if they agree, else the labels.

    A ruled table's cell edges are the ground truth and its header labels are
    not: labels are left-aligned inside the cell while values are usually
    right-aligned, so on 2025 `EE` -- `Importi Segnalati` printed at u
    344.6-379.1, values at 371.6-394.8 -- every narrow value overlaps *both*
    labels and is filed under the wrong column. The rules give the six
    separators of a five-column table outright.

    The rules are only believed when their column count matches the header's
    cell count to within one, because a borderless table still yields
    positions: MG10 has no grid at all, but its running-head underline and its
    margin stamp box come back as ten separators, and taking those at face
    value turned a three-column table into eleven.

    Returns `(cells, source)` with `source` one of `rules`, `header` or
    `header(no rules)`, recorded in the catalog so a reader can see which
    geometry a column list rests on.
    """
    if not header_cells:
        return [], "none"
    u_lo = min(c["u0"] for c in header_cells)
    u_hi = max(c["u1"] for c in header_cells)
    try:
        rules = geo.rule_boundaries(page, frame)
    except Exception:
        rules = {"u": [], "v": []}
    # N separators delimit N+1 columns, and the outermost separators are the
    # table's own left and right edges -- so a five-column table has six
    # separators, and clipping to the labels' extent throws the right-hand edge
    # away. The clip is only applied when the labels themselves sit outside the
    # rules, which is the borderless case where the rules are not to be trusted.
    span_lo = rules["u"][0] if rules["u"] else u_lo
    span_hi = rules["u"][-1] if rules["u"] else u_hi
    if rules["u"] and (u_lo < span_lo - RULE_EDGE_SLACK
                       or u_hi > span_hi + RULE_EDGE_SLACK):
        span_lo, span_hi = u_lo, u_hi
    ruled = geo.columns_from_boundaries(rules["u"], span_lo, span_hi)
    if ruled and len(ruled) == len(header_cells):
        # The rules give edges, so the labels have to be mapped onto them rather
        # than kept at their own extents -- and cut by position as they are
        # mapped, because a label can be wider than the column it names.
        return ([dict(cell,
                      text=_label_at(header_cells,
                                     0.5 * (cell["u0"] + cell["u1"]),
                                     cell["u0"], cell["u1"]),
                      bold=False, size=0.0)
                 for cell in ruled], "rules")
    return header_cells, "header" if rules["u"] else "header(no rules)"


def merge_stacked_header(bands, index, cells, header_cells):
    """Join a super-header row onto the labels of the row below it.

    MEF art. 27 prints `Anno 2022 | Anno 2023` above
    `Valore in € | Quantita | ...`, so the printed column name is a *path*, not a
    string. Getting this wrong is not cosmetic: without the year level, 2022's
    value and 2025's value land in one column and every cross-year diff reports
    that column as changed.

    The label is taken from `header_cells` and the span from `cells`, because
    after `resolve_columns` the two no longer share extents. Merged only when
    the band above covers the same columns, and the result is recorded as
    `Anno 2022 / Valore in €` in `header_path` with the inner label kept
    verbatim.
    """
    if index is None or index == 0 or not cells:
        return cells, 0
    above = bands[index - 1]
    upper = geo.column_bands(above)
    if len(upper) < 2:
        return cells, 0
    upper_shapes = [gra.value_shape(c["text"]) for c in upper]
    if any(s not in (gra.SHAPE_TEXT, gra.SHAPE_EMPTY) for s in upper_shapes):
        return cells, 0
    # Count which columns the band above actually reaches; a super-header that
    # covers only part of the table is a running head, not a header.
    covered_cols = set()
    for sup in upper:
        mid = 0.5 * (sup["u0"] + sup["u1"])
        for i, cell in enumerate(cells):
            if cell["u0"] <= mid <= cell["u1"]:
                covered_cols.add(i)
                break
    if len(covered_cols) < max(2, 0.6 * len(cells)):
        return cells, 0
    for i in sorted(covered_cols):
        sup = None
        best = 0.0
        mid = 0.5 * (cells[i]["u0"] + cells[i]["u1"])
        for candidate in upper:
            lo = min(candidate["u1"], mid) - max(candidate["u0"], mid)
            if lo <= 0:
                continue
            width = candidate["u1"] - candidate["u0"] or 1.0
            if lo / width > best:
                best, sup = lo / width, candidate
        if sup is None:
            continue
        sup_text = gra.normalise_text(sup["text"])
        if sup_text:
            cells[i]["path"] = f"{sup_text} / {cells[i]['text']}"
    return cells, 1


# --------------------------------------------------------------------------
# reading one table
# --------------------------------------------------------------------------


def catalogue_file(pdf_path, year, sample=DEFAULT_SAMPLE):
    """Every catalogued fact about one per-table PDF."""
    identity = read_identity(pdf_path, year)
    doc = pymupdf.open(pdf_path)
    pages = []
    try:
        for page in doc:
            pages.append(page_summary(page))
    finally:
        doc.close()

    status_counts = Counter(p["status"] for p in pages)
    orientations = Counter(p["orientation"] for p in pages)
    sample_pages = choose_sample_pages(len(pages), sample)
    signature, geometry = read_signature(pdf_path, sample_pages)
    # Re-open for the chart census. `read_signature` closes its own handle, and
    # the curve count needs a *drawn* page rather than an extracted one, so it is
    # taken here over a spread of three pages: a table that is charted on one
    # page of six is still a table, and one page would misreport it.
    chart_doc = pymupdf.open(pdf_path)
    try:
        charted = any(
            geo.hairline_census(chart_doc[i])["charted"]
            for i in choose_sample_pages(total_pages_of(pages), 3))
    finally:
        chart_doc.close()

    legible = status_counts.get("legible", 0)
    total_pages = len(pages)
    ruled = sum(p["ruled"] for p in pages)
    orientation, orientation_n = orientations.most_common(1)[0] if pages \
        else ("unknown", 0)
    mixed = any(p["upright_chars"] > geo.FOREIGN_FRACTION * max(1, p["chars"])
                and p["orientation"] != "upright" for p in pages)

    record = {
        **identity,
        "year": int(year),
        "pages": total_pages,
        "bytes": os.path.getsize(pdf_path),
        "charted": 1 if charted else 0,
        "orientation": orientation,
        "orientation_pages": orientation_n,
        "mixed_dir": 1 if mixed else 0,
        "ruled": 1 if ruled else 0,
        "longest_rule": max((p["longest_rule"] for p in pages), default=0.0),
        "legible_pages": legible,
        "garbled_pages": status_counts.get("garbled", 0),
        "ciphered_pages": status_counts.get("ciphered", 0),
        "empty_pages": status_counts.get("empty", 0),
        "page_stats": pages,
    }
    if charted and signature.get("n_columns") == 0:
        signature["grammar"] = gra.GRAMMAR_CHART
    record.update(signature)
    record["geometry"] = geometry
    record.update(extraction_status(record))
    return record


def total_pages_of(pages):
    return len(pages)


def choose_sample_pages(n_pages, sample):
    """Spread the geometry sample over the file, always including both ends.

    Both ends matter: A1's header and group structure are on page 1 and its
    closing subtotal is on page 537, and MG10's only data page is page 2 of 2.
    """
    if n_pages <= 0:
        return []
    if n_pages <= sample:
        return list(range(n_pages))
    idx = {0, n_pages - 1}
    step = (n_pages - 1) / float(sample - 1) if sample > 1 else 1
    for i in range(sample):
        idx.add(int(round(i * step)))
    return sorted(i for i in idx if 0 <= i < n_pages)


def read_signature(pdf_path, page_indices):
    """The column signature, and the features the grammar was decided from.

    Read on several pages and then compared, because a signature taken from one
    page is a guess: `header_stable` records whether every sampled page produced
    the same column labels, and a table where they disagree is marked as such
    rather than being given the first page's answer.
    """
    doc = pymupdf.open(pdf_path)
    per_page = []
    try:
        for i in page_indices:
            page = doc[i]
            direction = geo.dominant_direction(page)
            geom = page_geometry(page, direction)
            if geom is None:
                continue
            bands = geom["bands"]
            try:
                rules = geo.rule_boundaries(page, geom["frame"])["u"]
            except Exception:
                rules = []
            index, header_cells = find_header(bands, rules)
            if not header_cells:
                per_page.append({
                    "page": i, "columns": [], "cells": [], "stacked": 0,
                    "kinds": [], "rows": [], "repeated": [],
                    "total_bands": 0, "words": len(geom["words"]),
                })
                continue
            cells, source = resolve_columns(page, header_cells,
                                           frame=geom["frame"])
            cells, stacked = merge_stacked_header(bands, index, cells,
                                                   header_cells)
            matrix = geo.cell_matrix(bands, cells)
            header_row = matrix[index] if index is not None else None
            kinds, rows, per_col = [], [], defaultdict(Counter)
            previous_kind = None
            for r, row in enumerate(matrix):
                if r == index:
                    continue
                profile = gra.band_profile(row)
                kind = gra.band_kind(profile, cells, previous=previous_kind)
                previous_kind = kind
                kinds.append(kind)
                rows.append(row)
                for c, text in enumerate(row):
                    per_col[c][gra.value_shape(text)] += 1
            repeated = gra.repeated_key_columns(rows, cells, kinds)
            per_page.append({
                "page": i,
                "cells": cells,
                "column_source": source,
                "columns": [
                    {
                        "ordinal": c,
                        "label": gra.normalise_text(cell["text"]),
                        "path": gra.normalise_text(cell.get("path")
                                                   or cell["text"]),
                        "width": round(cell["u1"] - cell["u0"], 1),
                        "bold": 1 if cell["bold"] else 0,
                    }
                    for c, cell in enumerate(cells)
                ],
                "stacked": stacked,
                "header_row": header_row,
                "kinds": kinds,
                "rows": rows,
                "repeated": repeated,
                "total_bands": kinds.count(gra.KIND_TOTAL)
                             + kinds.count(gra.KIND_SUBTOTAL),
                "words": len(geom["words"]),
                "shapes": {str(c): dict(per_col[c]) for c in per_col},
            })
    finally:
        doc.close()

    usable = [p for p in per_page if p["columns"]]
    if not usable:
        return {
            "columns": [], "n_columns": 0, "stacked_header": 0,
            "header_stable": 0, "grammar": gra.GRAMMAR_UNKNOWN,
            "grammar_detail": {"reason": "no header band found on any "
                                         "sampled page"},
            "band_kinds": {}, "repeated_keys": [], "total_bands": 0,
            "bands": sum(p["kinds"].__len__() for p in per_page),
        }, {"sampled_pages": len(per_page)}

    # Pages are compared on their *geometry*, not on their labels. A label can
    # come back empty from a page whose header is drawn in rules but whose text
    # layer is thin, and two such pages are still the same table; conversely two
    # pages can carry the same five labels and a different number of columns
    # between them.
    shapes = [tuple(round(c["u1"] - c["u0"], 1) for c in p["cells"])
              for p in usable]
    modal = Counter(shapes).most_common(1)[0]
    chosen = usable[shapes.index(modal[0])]
    labels = [tuple(c["label"] for c in p["columns"]) for p in usable]
    label_modal = Counter(labels).most_common(1)[0]

    all_kinds = Counter()
    for p in usable:
        all_kinds.update(p["kinds"])
    grammar = gra.grammar_of(list(all_kinds.elements()), chosen["cells"],
                             chosen["repeated"])
    detail = {
        "bands_sampled": sum(len(p["kinds"]) for p in usable),
        "band_kinds": dict(all_kinds),
        "repeated_keys": chosen["repeated"],
        "wide_columns": sorted(gra._wide_columns(chosen["cells"])),
        "column_source": Counter(p["column_source"] for p in usable).most_common(1)[0][0],
        "pages_with_header": len(usable),
        "pages_agreeing": modal[1],
        "pages_disagreeing": len(usable) - modal[1],
        "pages_same_labels": label_modal[1],
    }
    columns = []
    for col in chosen["columns"]:
        shapes = Counter()
        samples = []
        for p in usable:
            if len(p["columns"]) != len(chosen["columns"]):
                continue
            for shape in p["shapes"].get(str(col["ordinal"]), {}).items():
                shapes[shape[0]] += shape[1]
        for row in chosen["rows"]:
            value = row[col["ordinal"]] if col["ordinal"] < len(row) else ""
            if value and len(samples) < 6:
                samples.append(value[:60])
        columns.append({
            **col,
            "shapes": dict(shapes.most_common()),
            "samples": samples,
            "role": column_role(col["label"], shapes),
        })
    return {
        "columns": columns,
        "n_columns": len(columns),
        "stacked_header": max(p["stacked"] for p in usable),
        "header_stable": 1 if len(set(labels)) == 1 and len(set(shapes)) == 1 else 0,
        "grammar": grammar,
        "grammar_detail": detail,
        "band_kinds": dict(all_kinds),
        "repeated_keys": chosen["repeated"],
        "total_bands": sum(p["total_bands"] for p in usable),
        "bands": sum(len(p["kinds"]) for p in usable),
    }, {"sampled_pages": len(per_page), "sampled_with_header": len(usable)}


def column_role(label, shapes):
    """dimension | measure | key | meta, from the label and the observed shapes.

    Only the leading label is consulted, and case is folded, because the
    archive's headings are inconsistent about case and about trailing
    punctuation: `Val. Fini Dog.`, `Val. Fini Dog`, `Valore in €`. A dimension
    is named by a noun (`Paese`, `Operatore`, `Utilizzatore Finale`), a measure
    by a quantity or a currency, and both are confirmed by what the column
    actually holds -- a column headed `Tipo` holding only `01`/`02` is a key,
    not a dimension of free text.
    """
    text = geo.deaccent(label)
    if not text:
        return "meta"
    if any(w in text for w in ("numero", "n.", "codice", "cod.", "autorizzazione")):
        return "key"
    if any(w in text for w in ("valore", "importi", "euro", "€", "quantita",
                               "spedizioni", "movimentazioni", "numero", "prezzo",
                               "amount", "totale")):
        return "measure"
    if shapes and all(s in (gra.SHAPE_CODE, gra.SHAPE_EMPTY) for s in shapes):
        return "key"
    if shapes and sum(shapes.values()) and shapes.get(gra.SHAPE_TEXT, 0) > 0:
        return "dimension"
    return "dimension"


def extraction_status(record):
    """Can this table actually yield data, and if not, why not.

    This is the number the rest of the project is honest or not about. The
    archive carries three corruption classes that no extractor can read -- pages
    with no text layer, garbled subset fonts, and ciphered `/Identity-H` -- and
    they are *inside* 2025, not only in the older years: 2025 `E` is garbled on
    all 221 of its pages. A table that splits correctly and returns nothing is
    not a pipeline failure, and calling it one would put the whole year red for
    a limitation that is in the source.
    """
    pages = record["pages"] or 0
    legible = record["legible_pages"]
    if record["grammar"] == gra.GRAMMAR_UNKNOWN and record["n_columns"] == 0:
        if record.get("charted"):
            # A pie chart has no cells to extract, and saying "no header found"
            # about one is a false alarm: 2025 MEF NN/OO/PP/GF are all
            # `Grafico Ripartizione percentuale`, ruled 447pt across the page
            # and carrying over a thousand Bezier segments each.
            return {"extractable": 0,
                    "extract_reason": "grafico percentuale: nessuna tabella di "
                                      "celle da estrarre"}
        if legible == 0:
            reason = (f"testo illeggibile in tutte le {pages} pagine "
                      f"(font senza ToUnicode: serve OCR)")
        else:
            reason = "nessuna intestazione riconosciuta: pagina di copertina o tabella vuota"
        return {"extractable": 0, "extract_reason": reason}
    if legible == 0:
        return {"extractable": 0,
                "extract_reason": f"testo illeggibile in tutte le {pages} pagine"}
    if legible < pages:
        return {"extractable": 1,
                "extract_reason": f"parziale: {pages - legible}/{pages} pagine illeggibili"}
    return {"extractable": 1, "extract_reason": ""}


def read_identity(pdf_path, year):
    """(authority, article, code, year) read back out of the path step 1 wrote.

    The path is walked upwards from the file rather than indexed by position,
    because `Out/PDF` may or may not be its root and the article level is
    optional: `MAE/A12025.PDF` has no article, `DOG/A1C2/N12025.PDF` has one.
    Reading the last two components as "authority, article" therefore turned
    every MAE and MEF table into `PDF/MAE` with an article of `MAE`, which is
    not a key the primary key can hold -- `article IS 'MAE'` matched nothing and
    the census counted those tables under an authority named `PDF`.

    So: the file's parent is the article when the grandparent is one of the four
    ministries, otherwise the parent *is* the authority.
    """
    path = Path(pdf_path)
    parent = path.parent
    grandparent = parent.parent
    if parent.name in ontology.AUTHORITIES:
        authority, article = parent.name, None
    elif grandparent.name in ontology.AUTHORITIES:
        authority, article = grandparent.name, parent.name
    else:
        authority, article = parent.name or ontology.UNKNOWN, None
    import re as _re
    m = _re.search(r"\d{4}$", path.stem)
    code = path.stem[:m.start()] if m else path.stem
    file_year = m.group(0) if m else str(year)
    return {
        "authority": authority,
        "article": article,
        "code": code.upper(),
        "file_year": int(file_year),
        "path": str(pdf_path),
        "src_first": None,
        "src_last": None,
        "index_title": None,
        "witness": None,
    }


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def open_catalog(path, create=True):
    fresh = create and not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    if fresh:
        conn.executescript(SCHEMA)
        conn.commit()
    return conn


TY_COLUMNS = (
    "authority", "article", "code", "year", "path", "pages", "bytes",
    "src_first", "src_last", "index_title", "witness", "orientation",
    "mixed_dir", "ruled", "longest_rule", "legible_pages", "garbled_pages",
    "ciphered_pages", "empty_pages", "words", "bands", "n_columns",
    "stacked_header", "header_stable", "grammar", "grammar_detail",
    "band_kinds", "repeated_keys", "total_bands", "columns", "extractable",
    "extract_reason", "fingerprint",
)


def write_record(conn, record, year):
    """Upsert one table-year and its columns and page stats."""
    key = (record["authority"], record["article"], record["code"], int(year))
    conn.execute("DELETE FROM table_year WHERE authority=? AND article IS ? "
                 "AND code=? AND year=?", key)
    values = {
        "authority": record["authority"], "article": record["article"],
        "code": record["code"], "year": int(year),
        "path": record["path"], "pages": record["pages"],
        "bytes": record["bytes"], "src_first": record["src_first"],
        "src_last": record["src_last"], "index_title": record["index_title"],
        "witness": record["witness"], "orientation": record["orientation"],
        "mixed_dir": record["mixed_dir"], "ruled": record["ruled"],
        "longest_rule": record["longest_rule"],
        "legible_pages": record["legible_pages"],
        "garbled_pages": record["garbled_pages"],
        "ciphered_pages": record["ciphered_pages"],
        "empty_pages": record["empty_pages"],
        "words": record.get("words", 0), "bands": record.get("bands", 0),
        "n_columns": record["n_columns"],
        "stacked_header": record["stacked_header"],
        "header_stable": record["header_stable"],
        "grammar": record["grammar"],
        "grammar_detail": json.dumps(record["grammar_detail"], ensure_ascii=False),
        "band_kinds": json.dumps(record["band_kinds"], ensure_ascii=False),
        "repeated_keys": json.dumps(record["repeated_keys"], ensure_ascii=False),
        "total_bands": record["total_bands"],
        "columns": json.dumps(record["columns"], ensure_ascii=False),
        "extractable": record["extractable"],
        "extract_reason": record["extract_reason"],
        "fingerprint": fingerprint(record),
    }
    # The statement is built from the column tuple rather than written out, so
    # a field added to the schema and forgotten here is an immediate
    # KeyError instead of a silent mis-insertion into a neighbouring column.
    sql = "INSERT INTO table_year ({}) VALUES ({})".format(
        ", ".join(TY_COLUMNS), ", ".join(["?"] * len(TY_COLUMNS)))
    conn.execute(sql, tuple(values[c] for c in TY_COLUMNS))
    conn.execute("DELETE FROM column_def WHERE authority=? AND article IS ? "
                 "AND code=? AND year=?", key)
    for col in record["columns"]:
        conn.execute(
            "INSERT INTO column_def (authority, article, code, year, ordinal,"
            " label, label_key, header_path, role, width, bold, shapes, samples)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            key + (col["ordinal"], col["label"],
                   geo.deaccent(col["label"]), col.get("path"),
                   col["role"], col["width"], col["bold"],
                   json.dumps(col["shapes"], ensure_ascii=False),
                   json.dumps(col["samples"], ensure_ascii=False)))
    conn.execute("DELETE FROM page_stat WHERE authority=? AND article IS ? "
                 "AND code=? AND year=?", key)
    conn.executemany(
        "INSERT INTO page_stat (authority, article, code, year, page,"
        " orientation, status, pua_ratio, ruled, longest_rule, words, bands)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [key + (i + 1, p["orientation"], p["status"], p["pua_ratio"],
                p["ruled"], p["longest_rule"], 0, 0)
         for i, p in enumerate(record["page_stats"])])
    conn.commit()


def fingerprint(record):
    """Identity of the *input*, so a re-run can tell what moved.

    Size and page count rather than a content hash: the catalog is a
    measurement of shape, and a page-count change is the signal that would
    change a shape. Hashing every page of every table would cost more than the
    rest of the run and answer a question the pipeline does not ask.
    """
    raw = f"{record['path']}|{record['bytes']}|{record['pages']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def attach_manifest(records, manifest_path):
    """Fold step 1's manifest in as the table's *witness*.

    A table the catalog lists is a table somebody can point at: the volume's own
    index names it, the PDF outline lists it, or the header scan saw it repeat.
    Step 1 knows which, and the witness is what separates "the table changed"
    from "we stopped being able to see the table". See Reports2PDFTables.py for
    how the manifest shape changed once and silently disarmed the verifier.
    """
    if not manifest_path or not Path(manifest_path).exists():
        return 0
    tables = {}
    with open(manifest_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            items = entry.get("tables")
            if isinstance(items, dict):          # the pre-ministry-tree shape
                items = list(items.values())
            for item in items or []:
                key = (item.get("authority"), item.get("article"),
                       str(item.get("code", "")).upper())
                tables[key] = item
    seen = 0
    for record in records:
        key = (record["authority"], record["article"], record["code"])
        item = tables.get(key)
        if not item:
            continue
        record["index_title"] = (item.get("title") or item.get("index_title")
                               or item.get("index_title_it"))
        record["src_first"] = (item.get("first_page") or item.get("start")
                               or item.get("src_first"))
        record["src_last"] = (item.get("last_page") or item.get("end")
                              or item.get("src_last"))
        record["witness"] = (item.get("witness") or item.get("source")
                             or item.get("detected_by") or "header-run")
        record["src_pages"] = item.get("pages")
        record["provisional"] = item.get("provisional")
        seen += 1
    return seen


# --------------------------------------------------------------------------
# the census: the human view of one year
# --------------------------------------------------------------------------


def write_census(conn, year, path, elapsed):
    """The one report a person reads: what the year's tables turned out to be.

    Four questions, in the order they matter: how many tables are there and can
    they be read; what grammars do they speak; what columns do they carry; and
    what is not extractable. A table that cannot yield data is listed with its
    reason, because a coverage claim that silently omits the unreadable third of
    the archive is the failure mode this whole step exists to prevent.
    """
    rows = conn.execute(
        "SELECT authority, article, code, pages, orientation, ruled,"
        " legible_pages, garbled_pages, ciphered_pages, empty_pages, n_columns,"
        " stacked_header, header_stable, grammar, total_bands, extractable,"
        " extract_reason, index_title FROM table_year WHERE year=?"
        " ORDER BY authority, article, code", (int(year),)).fetchall()
    # The authority is the first directory below `Out/PDF`. `article IS ?` with
    # a NULL parameter never matches, so a table filed directly under its
    # ministry read back as `PDF/...` and the census grouped them under an
    # authority called `PDF`. Keying the lookup on the same tuple the primary
    # key uses is what keeps the report's counts equal to the table's.
    by_key = {(r[0], r[1], r[2]): r for r in rows}
    extras = {}
    for r in rows:
        extras[r[:3]] = conn.execute(
            "SELECT mixed_dir, grammar, columns FROM table_year WHERE year=?"
            " AND authority=? AND article IS ? AND code=?",
            (int(year), r[0], r[1], r[2])).fetchone()

    out = []
    out.append(f"# Census of the {year} tables\n")
    out.append(f"{len(rows)} tables catalogued in {elapsed:.0f}s.\n")

    extractable = sum(1 for r in rows if r[15])
    pages = sum(r[3] for r in rows)
    legible_pages = sum(r[6] for r in rows)
    out.append("## Coverage\n")
    out.append("| | tables | pages |")
    out.append("|---|---|---|")
    out.append(f"| catalogued | {len(rows)} | {pages} |")
    out.append(f"| data extractable | {extractable} | {legible_pages} |")
    out.append(f"| text destroyed (garbled) | "
               f"{sum(1 for r in rows if not r[15])} | {sum(r[7] for r in rows)} |")
    out.append("")
    out.append("`text destroyed` counts pages, not tables: a 221-page table of "
               "which 216 are garbled is one unreadable table and a partly "
               "readable one is *not* silently promoted to a full one.\n")

    out.append("## Grammars\n")
    out.append("| grammar | tables | authorities |")
    out.append("|---|---|---|")
    by_grammar = defaultdict(list)
    for r in rows:
        by_grammar[r[13]].append(f"{r[0]}{'/' + r[1] if r[1] else ''} {r[2]}")
    for grammar, items in sorted(by_grammar.items(), key=lambda kv: -len(kv[1])):
        authorities = sorted({i.split()[0].split('/')[0] for i in items})
        out.append(f"| `{grammar}` — {gra.GRAMMAR_LABELS.get(grammar,'')} | "
                   f"{len(items)} | {', '.join(authorities)} |")
    out.append("")

    out.append("## Orientation and rules\n")
    out.append("| | tables |")
    out.append("|---|---|")
    for name in sorted({r[4] for r in rows if r[4]}):
        out.append(f"| content runs {name} | "
                   f"{sum(1 for r in rows if r[4] == name)} |")
    out.append(f"| ruled (a rule of >= 20pt present) | "
               f"{sum(1 for r in rows if r[5])} |")
    mixed = sum(1 for r in rows if extras[r[:3]][0])
    if mixed:
        out.append(f"| mixed: table rotated, furniture upright | {mixed} |")
    out.append(f"| stacked (two-row) header | "
               f"{sum(1 for r in rows if r[11])} |")
    stable = sum(1 for r in rows if r[12])
    out.append(f"| header identical on every sampled page | {stable} |")
    out.append("")
    out.append(
        f"A header is usually printed on the table's *first* page only, so "
        f"`identical on every sampled page` being {stable} is the expected "
        f"reading rather than a defect: the catalog records the *modal* "
        f"signature over the pages that carry a header, and the per-table "
        f"agreement is in `grammar_detail.pages_agreeing`. Where a table "
        f"repeats its header the count should be the number of sampled pages, "
        f"and where it does not, the modal signature is the first page's.\n")
    out.append("")

    out.append("## Column signatures\n")
    out.append("| table | authority | pages | cols | header | grammar | totals |")
    out.append("|---|---|---|---|---|---|---|")
    for r in rows:
        cols = conn.execute(
            "SELECT label, header_path FROM column_def WHERE year=? AND"
            " authority=? AND article IS ? AND code=? ORDER BY ordinal",
            (int(year), r[0], r[1], r[2])).fetchall()
        head = "; ".join((p or l or "") for l, p in cols) or "-- no header --"
        out.append(f"| `{r[2]}`{'/' + r[1] if r[1] else ''} | {r[0]} | {r[3]} "
                   f"| {r[10]} | {head[:90]} | {r[13]} | {r[14]} |")
    out.append("")

    unreadable = [r for r in rows if not r[15]]
    if unreadable:
        out.append("## Not extractable\n")
        out.append("| table | authority | pages | reason |")
        out.append("|---|---|---|---|")
        for r in unreadable:
            out.append(f"| `{r[2]}`{'/' + r[1] if r[1] else ''} | {r[0]} "
                       f"| {r[3]} | {r[16]} |")
        out.append("")

    path.write_text("\n".join(out), encoding="utf-8")
    return len(rows)


# --------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fingerprint the per-table PDFs of one year into a "
                    "catalog of table shapes.")
    parser.add_argument("--year", required=True,
                        help="reporting year to catalogue")
    parser.add_argument("--input-dir", default=None,
                        help=f"per-table PDFs (default: Out/PDF)")
    parser.add_argument("--out-root", default=None,
                        help=f"output root (default: Out)")
    parser.add_argument("--catalog", default=None,
                        help="catalog path (default: <out>/{CATALOG_DIR}/catalog.sqlite)")
    parser.add_argument("--manifest", default=None,
                        help="step 1's JSON manifest, for the table's witness")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                        help=f"pages per table read for geometry "
                             f"(default: {DEFAULT_SAMPLE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report only, write nothing")
    args = parser.parse_args(argv)

    year = args.year
    input_dir = Path(args.input_dir) if args.input_dir else Path("Out", "PDF")
    out_root = Path(args.out_root) if args.out_root else Path("Out")
    catalog_dir = out_root / CATALOG_DIR
    catalog_path = Path(args.catalog) if args.catalog \
        else catalog_dir / "catalog.sqlite"

    files = sorted(input_dir.rglob(f"*{year}{PDF_EXT}"))
    print(f"CatalogueTables -- {year}")
    print(f"  input:   {input_dir}/<authority>/[<article>/]<table>{year}{PDF_EXT}")
    print(f"  catalog: {catalog_path}")
    print(f"  {len(files)} file(s) found, geometry sampled on {args.sample} "
          f"page(s) per table\n")

    if not files:
        print(f"No per-table PDFs for {year} under {input_dir}. "
              f"Run Reports2PDFTables.py --year {year} --volume both first.")
        return 1

    started = time.time()
    conn = open_catalog(catalog_path, create=not args.dry_run)
    records, failures = [], []
    for i, path in enumerate(files, 1):
        label = str(path.relative_to(input_dir))
        try:
            record = catalogue_file(str(path), year, args.sample)
        except Exception as exc:                      # one bad file, not the year
            failures.append((label, f"{type(exc).__name__}: {exc}"))
            print(f"  [{i:>3}/{len(files)}] FAIL {label}: {exc}")
            continue
        records.append(record)
        if not args.dry_run:
            write_record(conn, record, year)
        print(f"  [{i:>3}/{len(files)}] {label:<34} "
              f"{record['pages']:>4}pp {record['orientation']:<8} "
              f"{record['n_columns']:>2}col {record['grammar']:<22} "
              f"{'ok' if record['extractable'] else 'UNREADABLE'}")

    attach_manifest(records, args.manifest)
    if not args.dry_run and args.manifest:
        # Re-upserted so the witness and the source span are stored on the row
        # rather than only in memory. The manifest is read *after* the files,
        # so this is a second write per table; at 110 tables and ~2 minutes for
        # the whole year the cost is a few seconds and the alternative is a
        # catalog whose provenance lives nowhere durable.
        for record in records:
            write_record(conn, record, year)

    elapsed = time.time() - started
    grammars = Counter(r["grammar"] for r in records)
    readable = sum(1 for r in records if r["extractable"])
    print(f"\n{'=' * 72}")
    print(f"{len(records)} tables in {elapsed:.0f}s "
          f"({elapsed / max(1, len(records)):.1f}s each)")
    print(f"  extractable      {readable}/{len(records)}")
    print(f"  rotated          {sum(1 for r in records if r['orientation'] != 'upright')}")
    print(f"  ruled            {sum(1 for r in records if r['ruled'])}")
    for grammar, n in grammars.most_common():
        print(f"  {grammar:<24} {n}")
    if failures:
        print(f"  failures         {len(failures)}")
        for label, why in failures:
            print(f"      {label}: {why}")
    print(f"{'=' * 72}")

    if not args.dry_run:
        jsonl = catalog_dir / f"catalog-{year}.jsonl"
        with jsonl.open("w", encoding="utf-8") as handle:
            for record in records:
                flat = {k: v for k, v in record.items() if k != "page_stats"}
                flat["page_stats"] = None       # in the database already
                handle.write(json.dumps(flat, ensure_ascii=False) + "\n")
        census = catalog_dir / f"census-{year}.md"
        write_census(conn, year, census, elapsed)
        print(f"  wrote {catalog_path}")
        print(f"  wrote {jsonl}")
        print(f"  wrote {census}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())