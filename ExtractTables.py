#!/usr/bin/env python3
"""Step 4 -- extract the catalogued tables into `stg_cell`, and dump them to SQL.

Phase 4 of [ROADMAP.md](./ROADMAP.md): the record assembler. Step 1 found the
tables and step 2 measured their shapes; this is the step that turns bands into
records and records into rows, and it is the first step that produces *data*
rather than evidence about data.

    ./ExtractTables.py --year 2025
    ./ExtractTables.py --year 2025 --dump          # + Out/SQL/armtrace-2025.sql[.gz]

What it does, per table:

    rotate -> column geometry -> row bands -> record assembly -> typed values
           -> stg_cell -> (with --dump) a deterministic .sql

**It does not re-derive the column frame.** The frame is `column_def`, measured
once per table-year by step 2. Each page's bands are cut by that page's own rules
and then assigned into the catalog's N columns by edge overlap, and the labels
the page prints are compared against the catalog's: a disagreement is a recorded
`column-drift`, never a silent realignment. 537 pages that each resolved their
own columns would be 537 chances to disagree about what column 7 is, and a
realignment is what makes every column change value at once.

**It implements two of the six grammars** -- `flat` and `group+subtotal`, 62 of
2025's 91 extractable tables. The other four are reported as SKIP with the reason
recorded, so "FAIL at zero" in the verifier means the implemented path is right
and the census still shows what carries no rows and why.

**Nothing is lost.** `stg_cell` is EAV: every printed cell of every readable
table-year, with its printed string verbatim in `raw_text`. The curated
projections of ROADMAP Phase 3 are built from this later, in SQL, without
re-parsing a single PDF.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from pathlib import Path

import pymupdf

import lib.geometry as geo
import lib.grammar as gra

try:                                   # the schema has exactly one copy
    from CatalogueTables import CATALOG_DDL, open_catalog
except ImportError:                    # run from another directory
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from CatalogueTables import CATALOG_DDL, open_catalog


# --------------------------------------------------------------------------
# what this step implements
# --------------------------------------------------------------------------

IMPLEMENTED = {gra.GRAMMAR_FLAT, gra.GRAMMAR_GROUP_SUBTOTAL}

#: Printed, and checked against the assembled data.
TOTAL_KINDS = (gra.KIND_SUBTOTAL, gra.KIND_TOTAL)

#: Folded into the record above rather than becoming one of their own. A1's
#: description wraps over three printed lines and all three are the same cell.
MERGE_KINDS = (gra.KIND_CONTINUATION, gra.KIND_WRAPPED)

#: The words a page-number stamp may contain, with every non-letter stripped so
#: `Pag.` and `Pagina` both reduce to `pag`. The order is deliberately NOT
#: matched: the rotation reverses the stamp's tokens page to page -- `Pagina 1 di
#: 537`, `Pagina 5 5di`, `Pagina di 1 221` (AGENTS.md §4) -- so a pattern for the
#: order catches one page of a table and misses the rest. Set membership does not
#: care which order they arrive in.
STAMP_MAX_WORDS = 6
#: The Private Use Area. On a garbled page the subsetter mapped the space glyph
#: to U+E000 as well, so a stamp arrives as `Pagina<PUA>di<PUA>5` and the word
#: separators are not spaces at all. They are stripped before the patterns see
#: the band, which is why the PUA run is removed from the text rather than
#: treated as a word.
PUA_CHARS_RE = re.compile(r"[-]")


class ExtractError(RuntimeError):
    """One table failed. It costs that table, never the run."""


# --------------------------------------------------------------------------
# typed values
# --------------------------------------------------------------------------

def typed_cell(text, unit=None):
    """`(raw, text, num)` for one printed cell.

    `raw` is the string exactly as it was printed, because the printed form is
    evidence -- `0,00` and `—` are different facts and only one of them means
    zero. `text` is the comparable NFKC/collapsed-whitespace form. `num` is a
    `Decimal` or None, and None is a real answer: `parse_number` refuses
    `016` (the Military List category, not the number 16), refuses `1,234.56`
    (the other convention), and refuses anything it cannot read without guessing.
    """
    raw = "" if text is None else str(text)
    norm = gra.normalise_text(raw)
    if not norm:
        return raw, norm, None
    number = gra.parse_number(norm)
    if number is not None:
        return raw, norm, number
    if gra.value_shape(norm) == gra.SHAPE_DATE:
        parsed = gra.parse_date(norm)
        if parsed is not None:
            return raw, parsed, None
    return raw, norm, None


def decimal_literal(value):
    """A DECIMAL literal, never a float and never an exponent.

    `format(Decimal('1234.56'), 'f')` keeps the value; `str(float(...))` would
    not, and `1.234` in a .sql file means one point two three four. Trailing
    zeros beyond what was printed are stripped, so `262920093.9600` is written
    `262920093.96` and two dumps of the same data are byte-identical.
    """
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    quantised = Decimal(value)
    text = format(quantised.normalize(), "f")
    return "0" if text in ("-0", "0E+1", "") else text


# --------------------------------------------------------------------------
# assembling one table into records
# --------------------------------------------------------------------------

def assemble_table(pdf_path, n_columns, grammar, max_pages=None):
    """Read a per-table PDF and return `(cells, detail)`.

    `cells` is a list of tuples ready for `stg_cell`, each already carrying the
    manifest key's first four fields filled in by the caller. `detail` is the
    bookkeeping the verifier's row-count check needs: how many bands of each
    kind were seen, and how many records came out.

    The assembly rules, in application order, for both implemented grammars:

    blank             emit nothing and consume no `row_ord` -- otherwise
                      `row_ord` stops being a stable record identity and the
                      row-count reconciliation loses its meaning
    header            a repeated column header: not data, emitted as nothing
    group             opens a scope. Own `row_ord`, spanning cell with
                      `col_ord` NULL, and it becomes `group_ord` for every
                      record until the next group header. **The scope crosses a
                      page boundary** -- only the next group header or the end of
                      the table closes it, because A1's groups routinely do.
    subtotal / total  closes the scope. Own `row_ord`, `is_total=1`, and it is
                      the verifier's ORACLE, not data: every sum in step 5
                      excludes it, because summing data and subtotals together
                      double-counts and can then be satisfied by a compensating
                      error
    continuation      merges into the record above, into the columns it sits in.
                      Three printed lines of one wrapped description are one
                      cell, not three records
    data              a record. New `row_ord`, `group_ord` = the open scope

    A page with no header band of its own reuses the last page's column frame
    rather than resolving its own -- most tables print the header once and then
    run for hundreds of pages -- and a table whose very first page has no header
    has no frame at all, so its pages are recorded as skipped, not guessed at.
    """
    doc = pymupdf.open(pdf_path)
    rows, detail = [], Counter()
    row_ord = 0
    open_group = None
    group_names = {}             # {row_ord: the text that opened that scope}
    records_in_scope = {}        # {row_ord: data records charged to that scope}
    last = None                  # (row_ord, page_no, group_ord) of the last record
    prev = {}                    # {col_ord: row_ord} of the last record emitted
    cell_at = {}                 # {(row_ord, col_ord): index into rows`
    frame_cols = None
    drift_pages, legible_pages = [], 0
    illegible_pages, no_frame_pages = [], []
    try:
        limit = len(doc) if max_pages is None else min(len(doc), max_pages)
        for page_no in range(1, limit + 1):
            page = doc[page_no - 1]
            direction = geo.dominant_direction(page)
            geom = _page_geometry(page, direction)
            if geom is None:
                illegible_pages.append(page_no)
                continue
            legible_pages += 1

            rules = _rules(page, geom["frame"])
            index, header_cells = _find_header(geom["bands"], rules)
            if header_cells:
                candidate = _resolve(page, geom, header_cells, index)
                # **A header that disagrees with the catalog is not a header.**
                # Measured on A1, where 33 of its 537 pages fail this: on p23 and
                # p25 `find_header` returns a band of description text --
                # `TD-H-200 -  TECNOLOGIA`, `SUBBACQUEE - TEC...` -- and resolves
                # it to *five* columns against the ten the catalog measured. Every
                # value on the page then landed under the wrong label, and the
                # group subtotal 30 pages later came out short.
                #
                # The catalog's count is the authority here, and it is the same
                # argument as everywhere else in this pipeline: the extractor does
                # not re-derive what step 2 already established. A page whose
                # candidate frame disagrees keeps the last good frame instead --
                # exactly the path a page with no header at all takes.
                if not n_columns or len(candidate) == n_columns:
                    cols = frame_cols = candidate
                elif frame_cols is not None:
                    cols = frame_cols
                    drift_pages.append(page_no)
                else:
                    no_frame_pages.append(page_no)
                    continue
            elif frame_cols is not None:
                cols = frame_cols
            else:
                no_frame_pages.append(page_no)
                continue

            bands = geom["bands"]
            matrix = geo.cell_matrix(bands, cols)
            prev_kind = None
            for r, cells in enumerate(matrix):
                if r == index:
                    detail["header"] += 1
                    continue
                profile = gra.band_profile(cells)
                kind = gra.band_kind(profile, cols, previous=prev_kind)

                if kind == gra.KIND_BLANK:
                    detail["band:" + kind] += 1
                    continue
                if kind == gra.KIND_HEADER:
                    detail["band:" + kind] += 1
                    continue
                # The margin stamp is furniture and is dropped before it can be
                # classified. On MG10 the stamp `Pag. 1 di 1` is one cell wide and
                # sits clear of the column frame, so `cell_matrix` files it under
                # the nearest column and it becomes a `group` band -- a phantom
                # record that also opens a phantom group scope for everything
                # after it. Dropping it earlier is cheaper than undoing that.
                #
                # The `band:<kind>` counter is incremented **after** this drop, and
                # that is what makes `row.reconciliation` an identity instead of a
                # guess. Counting furniture under the kind it would have had left
                # the verifier subtracting it from whichever kind happened to be
                # short -- "data: 130 bands but 133 records (of 136 classified)"
                # was that subtraction guessing wrong, on a correct extraction.
                if is_page_stamp(bands[r]) or is_running_furniture(
                        _band_text(bands, r)):
                    detail["furniture"] += 1
                    continue

                # Two bands that are *not* what `band_kind` called them. Both tests
                # run **before** `band:<kind>` is incremented, because that counter
                # is what `row.reconciliation` compares against the records, and a
                # counter that counts a band under a kind the band was then
                # reclassified away from makes a correct extraction look wrong --
                # "data: 7 bands but 6 records" on nine T8x tables, every one of
                # them a year header or a subtotal with no scope to close.
                if _is_year_header(profile):
                    detail["year_header"] += 1
                    prev_kind = kind
                    continue
                if kind in TOTAL_KINDS and kind != gra.KIND_TOTAL and (
                        open_group is None
                        or not records_in_scope.get(open_group)):
                    # A `subtotal` **closes** the group it belongs to, so there is
                    # nothing for it to close here and it is a data row.
                    #
                    # `KIND_TOTAL` is exempt, because that is the band carrying a
                    # `Totale`-shaped *label* -- MG10's `Totale complessivo
                    # 262.920.093,96` -- and that is the one kind of printed total
                    # a table with no groups at all still legitimately has. The
                    # distinction matters: demoting a labelled total would delete
                    # the only oracle MG10 has.
                    #
                    # Measured on T20, where the country is printed on a band of
                    # its own and its figures on the next, so the figures arrive
                    # as a band of eight numbers with no text in it --
                    # indistinguishable from a subtotal -- and 29 of them were
                    # taken for the totals of the country above, giving
                    # `group 7 · Valore in €: assembled 0 vs printed 575718147.76`
                    # on a table that prints no totals at all. And on T84, which
                    # has no groups at all, the last row of numbers was read as a
                    # file total and reported as
                    # `N. autorizzazioni: assembled 152532836754.81 vs printed 192`.
                    detail["total_without_scope"] += 1
                    kind = gra.KIND_DATA
                    profile = gra.band_profile(cells)

                detail["band:" + kind] += 1
                prev_kind = kind

                if kind in MERGE_KINDS:
                    if _merge_into(rows, cell_at, prev, cells, last):
                        detail["merged"] += 1
                    continue

                prev_last = last
                row_ord += 1
                prev = {}
                last = (row_ord, page_no, open_group)
                if kind == gra.KIND_GROUP:
                    text = _band_text(bands, r)
                    if _opens_record(bands[r], cols):
                        if _same_group(text, group_names.get(open_group)):
                            # A group prints its header **on every page it runs
                            # onto**, so the same name opening again is the same
                            # group continuing, not a new one. AEREA S.p.A. spans
                            # pp 1-4 of A1 and prints itself on each; treating
                            # each page's copy as a new scope split one operator
                            # into four, so the p4 subtotal was compared against
                            # 109 rows instead of the whole group and reported
                            # `assembled 0 vs printed 5360673.71` on a table that
                            # is correct.
                            detail["groups_continued"] += 1
                        else:
                            open_group = row_ord
                            group_names[open_group] = text
                            detail["scopes_opened"] += 1
                    else:
                        detail["groups_in_scope"] += 1
                    detail["records:group"] += 1
                    # The header is printed *across* the table, so `cell_matrix`
                    # has already filed its words under several columns and
                    # `cells[0]` is only the first fragment of the name. The
                    # spanning cell's text is the band's own text, joined.
                    #
                    # `group_ord` points at the scope this header *belongs to*:
                    # the open scope, which is itself only when this header is
                    # what opened it. Pointing a continuation at itself would
                    # make p4's copy of AEREA a group of one page, and a
                    # non-opening header at itself would say
                    # `26 NUMERO AFFUSTO SPECIALE...` is an operator.
                    rows.append(_row(row_ord, page_no, None, open_group,
                                     "group_header", None, text))
                    cell_at[(row_ord, None)] = len(rows) - 1
                    if not _opens_record(bands[r], cols):
                        # A header that opened nothing (a repeated column header,
                        # a title line) must not steal the `last` record a
                        # continuation belongs to.
                        last = prev_last
                    continue
                if kind in TOTAL_KINDS:
                    detail["records:total"] += 1
                    # A total band's *label* -- `T otale complessivo` -- is not a
                    # value in any column, and it lands wherever its words sit:
                    # on MG10 pypdf shreds it to `T otale complessivo`, whose `T`
                    # falls inside the Quantità column, so the measure column was
                    # being given the text `T otale complessivo` as a cell. A
                    # cell in a total band that is not a measurement is the
                    # label, and goes in as a spanning cell.
                    label_parts = []
                    for c, text in enumerate(cells):
                        if not str(text).strip():
                            continue
                        shape = gra.value_shape(gra.normalise_text(text))
                        if shape not in gra.MEASURE_SHAPES:
                            label_parts.append(str(text))
                            continue
                        rows.append(_row(row_ord, page_no, c, open_group,
                                         kind, _unit(cols, c), text))
                        cell_at[(row_ord, c)] = len(rows) - 1
                    if label_parts:
                        rows.append(_row(row_ord, page_no, None, open_group,
                                         kind, None, " ".join(label_parts)))
                    prev = {}
                    continue
                detail["records:data"] += 1
                if open_group is not None:
                    records_in_scope[open_group] = \
                        records_in_scope.get(open_group, 0) + 1
                for c, text in enumerate(cells):
                    if not str(text).strip():
                        continue
                    rows.append(_row(row_ord, page_no, c, open_group,
                                     "data", _unit(cols, c), text))
                    cell_at[(row_ord, c)] = len(rows) - 1
                    prev[c] = row_ord
    finally:
        doc.close()

    detail["pages"] = limit
    detail["legible_pages"] = legible_pages
    # Two populations, reported apart because they mean opposite things: a page
    # whose text is destroyed cannot yield cells and never could, while a page
    # with a header but no column frame is usually a cover. Calling both
    # "unreadable" would put MG10's cover page in the same count as `E`'s 221
    # destroyed ones and make the ceiling meaningless.
    detail["illegible_pages"] = illegible_pages[:40]
    detail["illegible_page_count"] = len(illegible_pages)
    detail["no_frame_pages"] = no_frame_pages[:40]
    detail["no_frame_page_count"] = len(no_frame_pages)
    detail["skipped_pages"] = (illegible_pages + no_frame_pages)[:40]
    detail["skipped_page_count"] = len(illegible_pages) + len(no_frame_pages)
    detail.setdefault("scopes_opened", 0)
    detail.setdefault("groups_in_scope", 0)
    detail.setdefault("groups_continued", 0)
    detail["drift_pages"] = drift_pages[:40]
    detail["drift_page_count"] = len(drift_pages)
    detail["row_ord_max"] = row_ord
    detail["rows"] = len(rows)
    return rows, dict(detail)


def _row(row_ord, page_no, col_ord, group_ord, kind, unit, text):
    raw, norm, num = typed_cell(text, unit)
    return (page_no, row_ord, col_ord, group_ord, kind, raw, norm, num, unit,
            1 if kind in TOTAL_KINDS else 0)


#: The running furniture a table stamps on every page of itself. A1 prints
#: `MAECI - UAMA - CENTRO INFORMATICO | Pagina  1 di  537 | TAB A1` as a
#: three-cell band, which `find_header` already rejects for a *column* header --
#: so the catalog's A1 signature is right and its band kinds still contain one
#: `group` band per page. The name is the tell: an operator's name is a company,
#: and this one ends in `CENTRO INFORMATICO`.
FURNITURE_RE = re.compile(
    r"^(?:.*\s)?CENTRO\s+INFORMATICO\b.*$"
    r"|^.*\bTAB\.?\s+[A-Z]{1,3}\d*\s*$", re.I)


#: A four-digit year in 1900-2099. `value_shape` classifies a bare 1900-2099 as
#: `SHAPE_YEAR`, and that is what `_is_year_header` keys on.
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def _is_year_header(profile):
    """True for a band whose cells are all bare years: a column header.

    Measured on DIFESA Annesso 3B, where the summary block prints

        TOTALE INCASSO
        2022   2023   2024   2025        <- the years, as a band of its own
        35.400.025,63   48.404.562,78  …

    and the year band is four numbers in a row, so `band_kind` calls it arithmetic
    and it became four data records. The verifier then reported the totals off by
    **exactly 2022, 2023, 2024 and 2025** — one per year column, which is the
    signature of this bug and is what made it identifiable rather than a mystery
    drift.

    Two or more year cells in one band, and nothing else, is the test. A single
    year in a data row is data; a row of years heading four columns is a header.
    A band that mixes a year with anything else is left alone, because a row
    carrying a date, an amount and a year is a record.
    """
    filled = [c for c in profile["cells"] if str(c).strip()]
    if len(filled) < 2:
        return False
    return all(YEAR_RE.match(gra.normalise_text(c)) for c in filled)


def is_running_furniture(text):
    """True for a band's text that is the table's own page furniture.

    A substring test would be wrong: the archive quotes `Tab. I` in prose, and
    the header of a table legitimately ends in a table code. What is safe is the
    *whole band* being the furniture line, which is what the table prints once
    per page, and the centre name inside it.
    """
    return bool(FURNITURE_RE.match(text.strip()))


#: Exactly the patterns the stamp takes, from AGENTS.md §4. Matched whole-band
#: rather than token-by-token, because `Pag. 1 di 1` and `Pagina di 1 221` are
#: not permutations of one form: the rotation drops words, so the count varies
#: and the order is not reliable. Each is an alternation that allows its digits in
#: either position, which is what the three measured forms need and what a
#: stricter pattern would reject.
STAMP_RES = (
    re.compile(r"^pag(?:ina|\.)?\s*\d+\s*di\s*\d+$", re.I),
    re.compile(r"^pag(?:ina|\.)?\s*di\s*\d+\s*\d*$", re.I),
    re.compile(r"^pag(?:ina|\.)?\s*\d+$", re.I),
    # `Pagina 5 5di`, measured on 2025 vol. I p611. The digits land *after* the
    # word rather than before it, which no permutation of the first two forms
    # covers -- so the "reversed order" is not one shape but two, and both are
    # here. (`Pagina di 1 221`, vol. I p817, is the second.)
    re.compile(r"^pag(?:ina|\.)?\s*[\d\s]*di[\d\s]*$", re.I),
)


def is_page_stamp(band):
    """True for a page-number stamp: the whole band's text is one of `STAMP_RES`.

    The stamp is the reason a rotated table can be catalogued as
    `MAECI - UAMA - CENTRO INFORMATICO | Pagina 1 di 537 | TAB A1` (see
    `find_header`'s "not the stamp" clause), so it has to be recognised here too.

    Matched **whole-band** rather than token-by-token. The first attempt allowed
    any permutation of `{pag, di, <digits>}`, which is right in principle and
    wrong in practice: it needed the words to be *present*, and pypdf does not
    deliver them -- it shreds the stamp, so the band arrives as `Pag.` + `1` +
    `di 1` across three separate words with the count varying page to page. The
    band text is normalised (PUA stripped, whitespace collapsed) and matched
    whole, which is what the three measured forms actually are.
    """
    words = [w.text for w in band]
    if not words or len(words) > STAMP_MAX_WORDS:
        return False
    text = gra.normalise_text(" ".join(words))
    text = PUA_CHARS_RE.sub("", text)
    return any(rx.match(text) for rx in STAMP_RES)


#: How far left of the table's own left edge a band must start to be a group
#: header rather than a row. 8pt, between the measured 19pt of A1's operator
#: names and the 5pt by which the nearest ordinary row clears the same edge.
HEADER_MARGIN = 8.0

#: ... and how wide such a band may be, as a share of the frame's width.
#: A group header names one group in a corner; a data row fills the table.
#: Measured: T20's `STATI UNITI` is 30pt in a 363pt frame (0.08) while its
#: data rows are the full 432pt (1.19) and start 70pt left of the frame, so
#: the left-edge test alone cannot tell them apart.
HEADER_MAX_SHARE = 0.5


def _same_group(a, b):
    """True when two group headers name the same group.

    Compared on the normalised, accent-folded text, because the archive is not
    consistent about either: `AEREA S.p.A.` and `AEREA S.p.A` are the same
    company and A1 prints both. A bare string comparison splits them into two
    operators and every subtotal after the first one fails.

    Whitespace and case only -- the comparison is deliberately not looser than
    that. Two *different* operators with similar names must stay separate,
    because the cost of merging them is a total checked against the wrong rows.
    """
    if not a or not b:
        return False
    return gra.normalise_text(geo.deaccent(a)).casefold() == \
        gra.normalise_text(geo.deaccent(b)).casefold()


def _opens_record(band, cols):
    """True when a `group` band *starts* a group rather than being one row of it.

    Measured on A1, where 46 of 52 `group` bands in six pages were not group
    headers at all. `band_kind` calls a band `group` when it carries one cell of
    text and no arithmetic, and three things qualify on this table:

      * the operator's own name, printed once across the table -- a header;
      * **a material row whose only printable value is its description** --
        `26 NUMERO AFFUSTO SPECIALE LEGGERO PER MITRAGLIATRICE 7.62MM 04 001`,
        which is a data record with empty date, amount and quantity columns;
      * the running furniture line, dropped earlier.

    Only the first may open a scope. A group header is printed *outside* the
    table's own column frame -- it starts left of the leftmost column and spans
    past the rightmost, because it is laid out across the page rather than fitted
    to a cell. A material row is inside the frame, filling one column of it.

    So the test is **where the band's left edge sits relative to the table's own
    left edge**, measured against A1's frame (`-763.5 .. -143.9`, ten columns
    taken from the page's rules):

      A.C.S.A. STEEL FORGINGS S.P.A.   -782.5   19pt LEFT of the first column
      A.T.C. SRL                        -782.5   19pt left, the same
      10 NUMERO (ART. 31) - ARMA ...    -483.6   280pt right -- inside the frame
      USD 887.145,00 856.813,79 ...     -758.8   5pt right -- inside

    A header is laid out **across** the page and therefore starts outside the
    frame; a row is fitted to the frame's columns and never does. The margin is
    8pt, which sits between the measured 19pt of every header and the -758.8 of
    the nearest row that stays inside.

    Overhang on the **right** is deliberately not part of the test: the material
    rows overhang the right edge too, because the last column is narrow and a long
    description runs past it -- band 20 ends at -143.9 against a frame edge of
    -143.9.

    Returning False does not discard the band. It is still emitted as a
    `group_header` row carrying its text, so the EAV keeps every printed cell.
    What it does not do is open a scope, so the records after it stay in the
    operator they belong to instead of being charged to a material description.
    """
    if not cols:
        return True
    left = min(c["u0"] for c in cols)
    right = max(c["u1"] for c in cols)
    u0 = min(w.u0 for w in band)
    u1 = max(w.u1 for w in band)
    if u0 > left - HEADER_MARGIN:
        return False
    # A header is also **narrow**: it names one group in a corner of the table,
    # while a data row spans the frame. Measured on T20, where the frame runs
    # u 93.0–455.9 and every row starts at u 23.4 -- 70pt left of it -- so the
    # left-edge test alone opened a scope on `SVIZZERA  1  596.935.082,53  30,19
    # 57  3  111.267.666,43`, a full data row. The country's real header,
    # `STATI UNITI`, is 30pt wide against a 363pt frame, or 8%.
    return (u1 - u0) <= HEADER_MAX_SHARE * (right - left)


def _band_text(bands, index):
    """A whole band's text, in reading order.

    Needed for the one cell that is not in any column. A1 prints its group header
    across the full width, so `cell_matrix` has already split the operator's name
    across several columns and any single one of them is a fragment.
    """
    if index is None or index >= len(bands):
        return ""
    return " ".join(w.text for w in sorted(bands[index], key=lambda w: w.u0))


def _merge_into(rows, cell_at, prev, cells, last=None):
    """Fold a wrapped line into the record above, by column.

    `last` is `(row_ord, page_no, group_ord)` of the most recent record, needed
    only when the column is empty above and a new cell has to be created for it.

    Merging is per column, not per band, and **a continuation whose column is
    empty on the record above becomes a cell of that record** rather than being
    dropped. The earlier version skipped it, reasoning that a column the record
    left empty was never printed -- and threw away real money. Measured on A1 p64,
    where the record is

        EUR | 400.000,00 |       | 1.000 | ORE | ASSISTENZA PER SISTEMA … | | 03

    and the next line carries only `400.000,00` in `Val. Fini Dog.`. That value is
    printed, it belongs to the record above, and the record above has no cell in
    that column -- so it was skipped, and one group's subtotal came out short by
    **exactly 400.000,00** in two columns at once.

    Dropping it was never the safe choice. The alternative failure -- attaching a
    wrapped line to the wrong record -- is caught by `row.reconciliation` and by
    the printed totals, whereas a silently discarded value is caught by neither
    until someone notices a total that does not add up.
    """
    merged = False
    for c, text in enumerate(cells):
        if not str(text).strip():
            continue
        target = prev.get(c)
        if target is None:
            if last is None:
                continue
            row_ord, page_no, group_ord = last
            # A *second* continuation on the same column of the same record has to
            # join the cell the first one created, not start another: `row_ord`
            # and `col_ord` are the primary key, so appending a duplicate raises
            # `UNIQUE constraint failed` and takes the whole run with it.
            where = cell_at.get((row_ord, c))
            if where is None:
                cell_at[(row_ord, c)] = len(rows)
                rows.append(_row(row_ord, page_no, c, group_ord, "data",
                                 None, text))
            else:
                old = rows[where]
                joined = (old[5] + " " + str(text)).strip()
                _, norm, num = typed_cell(joined)
                rows[where] = old[:5] + (joined, norm, num) + old[8:]
            merged = True
            continue
        where = cell_at.get((target, c))
        if where is None:
            continue
        old = rows[where]
        joined = (old[5] + " " + str(text)).strip()
        _, norm, num = typed_cell(joined)
        rows[where] = old[:5] + (joined, norm, num) + old[8:]
        merged = True
    return merged


def _unit(cols, ordinal):
    """The unit a column carries, from the printed label only.

    The archive prints `(Euro)`, `Quantita`, `in €`. It is recorded on the cell so
    a reader needs no join, and it is never inferred from the value -- a number
    without a unit and a number in euros are different facts and AGENTS-
    TABLE2SQL.md §4 puts units in `column_def` precisely to stop them drifting
    into the data.
    """
    if ordinal is None or ordinal >= len(cols):
        return None
    label = gra.normalise_text(cols[ordinal].get("path") or cols[ordinal].get("text") or "")
    if not label:
        return None
    low = label.lower()
    if "euro" in low or "€" in low or "eur" in low:
        return "EUR"
    if "kg" in low or "chilo" in low:
        return "KG"
    if "percent" in low or "%" in low:
        return "PCT"
    return None


# --------------------------------------------------------------------------
# the page-reading half, reusing step 2's own code so the two cannot disagree
# --------------------------------------------------------------------------

def _page_geometry(page, direction):
    if geo.page_legibility(page)[0] != "legible":
        return None
    import CatalogueTables as cat
    if cat.PUA_RUN_RE.search(geo.page_text(page)):
        return None
    frame = geo.Frame(direction["right"])
    foreign = [(1.0, 0.0)] if frame.name != "upright" else []
    words = [w for w in geo.lines_of(page, frame, foreign_dirs=foreign)
             if not w.foreign]
    if not words:
        return None
    return {"frame": frame, "bands": geo.row_bands(words), "words": words}


def _rules(page, frame):
    try:
        return geo.rule_boundaries(page, frame)["u"]
    except Exception:
        return []


def _find_header(bands, rules):
    import CatalogueTables as cat
    return cat.find_header(bands, rules)


def _resolve(page, geom, header_cells, index):
    """This page's column frame, from the same call the catalog makes.

    `bands` and `index` are passed so `resolve_columns` can reach its third
    witness: on A1 the rules are the margin stamp's box and the labels are
    left-aligned against right-aligned values, so without them the extractor
    silently inherits the wrong frame from step 2. The extractor and the catalog
    must agree on where column 7 is, or the catalog's `col_key` is a lie.
    """
    import CatalogueTables as cat
    cells, _source = cat.resolve_columns(page, header_cells,
                                         frame=geom["frame"],
                                         bands=geom["bands"], index=index)
    cells, _stacked = cat.merge_stacked_header(geom["bands"], index, cells,
                                               header_cells)
    return cells


# --------------------------------------------------------------------------
# the parallel unit: one table
# --------------------------------------------------------------------------

def extract_one(job):
    """Worker. Never raises: one table must cost that table, never the run."""
    identity, pdf_path, n_columns, grammar, max_pages = job
    authority, article, code, year = identity
    started = time.time()
    base = dict(authority=authority, article=article, code=code, year=year)
    if grammar not in IMPLEMENTED:
        return base, [], {
            "status": "skip",
            "reason": f"grammar '{grammar}' not implemented",
            "rows": 0, "seconds": round(time.time() - started, 2),
        }
    if n_columns == 0:
        return base, [], {
            "status": "skip", "reason": "no column frame in the catalog",
            "rows": 0, "seconds": round(time.time() - started, 2),
        }
    try:
        rows, detail = assemble_table(pdf_path, n_columns, grammar, max_pages)
    except Exception as exc:                       # noqa: BLE001 -- by design
        return base, [], {
            "status": "error", "reason": f"{type(exc).__name__}: {exc}",
            "rows": 0, "seconds": round(time.time() - started, 2),
        }
    detail["status"] = "extracted"
    detail["reason"] = ""
    detail["seconds"] = round(time.time() - started, 2)
    return base, rows, detail


def plan_jobs(conn, year, out_root, filters=(), max_pages=None):
    """Every extractable table of `year`, with the catalog's column count.

    The identity is read from `table_year`, not from the file path: the catalog
    already resolved the manifest key -- `(authority, article, code)`, the
    article level optional -- and re-deriving it from a path here is how the
    Dogane-overwrites-MAE class of bug comes back through the side door.
    """
    q = ("SELECT authority, article, code, year, path, n_columns, grammar,"
         "       extractable FROM table_year WHERE year=?")
    jobs = []
    for authority, article, code, yr, path, ncols, grammar, ok in \
            conn.execute(q, (year,)):
        pdf = locate(out_root, path)
        if not pdf or not ok:
            continue
        if filters and code not in filters:
            continue
        jobs.append(((authority, article, code, yr), str(pdf), ncols, grammar,
                     max_pages))
    return jobs


def locate(out_root, path):
    """The file `table_year.path` names, tolerating how it was recorded.

    The catalog stores whatever path step 1 was given, which may already carry
    the `Out/PDF` prefix (`Out/PDF/MAE/A12025.PDF`) or be relative to the PDF root
    (`MAE/A12025.PDF`) depending on how the run was invoked. Joining blindly
    produces `Out/PDF/Out/PDF/...`, finds nothing, and reports "no extractable
    tables" for a year that has 122 of them -- a silent zero.

    Tried in order and the first hit wins, so this cannot pick the wrong file when
    both exist: the fully-qualified reading is the specific one.
    """
    if not path:
        return None
    candidates = [
        Path(path),                                  # already qualified
        Path(out_root) / path,                       # relative to out-root
        Path(out_root) / "PDF" / path,               # relative to the PDF root
        Path(out_root) / "PDF" / Path(path).name,    # last resort: bare name
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def write_rows(conn, base, rows):
    """Replace this table-year's cells, then insert the new ones.

    Deleted by the manifest key, not by a year-wide clear: one re-run must not be
    able to silently empty a table whose extraction failed.

    `col_key` is filled here, by the parent, from `column_def` -- not by the
    worker. The column *ordinal* is a reading-time fact and the column *name* is
    a catalog fact, and joining them in one place means a page that resolves its
    own frame differently cannot invent a label for itself. An ordinal the
    catalog does not have gets NULL and is counted as drift.
    """
    conn.execute(
        "DELETE FROM stg_cell WHERE authority=? AND article IS ? AND code=? "
        "AND year=?", (base["authority"], base["article"], base["code"],
                       base["year"]))
    keys = column_keys(conn, base)
    if not rows:
        return 0
    payload, unmapped = [], set()
    for r in rows:
        col_ord = r[2]
        if col_ord is not None and col_ord not in keys:
            unmapped.add(col_ord)
        payload.append(_sql_row(base, r, keys.get(col_ord)))
    if unmapped:
        print(f"    warning: {base['code']}: column ordinals {sorted(unmapped)} "
              f"are not in the catalog's signature", flush=True)
    conn.executemany(
        "INSERT INTO stg_cell (authority, article, code, year, page, row_ord,"
        " col_ord, group_ord, row_kind, col_key, raw_text, text_value,"
        " num_value, unit, is_total) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        payload)
    return len(rows)


def column_keys(conn, base):
    """`{ordinal: header_path}` for this table-year, from `column_def`."""
    return {
        ordinal: (path or label or "")
        for ordinal, label, path in conn.execute(
            "SELECT ordinal, label, header_path FROM column_def WHERE"
            " authority=? AND article IS ? AND code=? AND year=?",
            (base["authority"], base["article"], base["code"], base["year"]))
    }


def _sql_row(base, r, col_key):
    page_no, row_ord, col_ord, group_ord, kind, raw, norm, num, unit, is_total = r
    return (base["authority"], base["article"], base["code"], base["year"],
            page_no, row_ord, col_ord, group_ord, kind, col_key, raw, norm,
            str(num) if isinstance(num, Decimal) else num, unit, is_total)


def record_detail(conn, base, detail):
    conn.execute(
        "UPDATE table_year SET extract_detail=? WHERE authority=? AND article IS ?"
        " AND code=? AND year=?",
        (json.dumps(detail, sort_keys=True, default=str), base["authority"],
         base["article"], base["code"], base["year"]))


# --------------------------------------------------------------------------
# the deterministic dump
# --------------------------------------------------------------------------

#: A throwaway connection used only to render literals. sqlite's driver offers no
#: public `quote()` on 3.10, but it will quote a bound parameter for us, and that
#: is the authoritative implementation -- the same one it uses to build the SQL it
#: executes, so what is written is what the driver would have written.
_QUOTER = sqlite3.connect(":memory:")


def sql_literal(value):
    """A SQL literal. A dash is data, never a NULL: `0,00` and `—` differ.

    Strings are quoted by handing them to the driver, not by hand. Two real bugs
    came from doing it by hand and **both were found only by loading the finished
    dump into a fresh database**, which is the test this function exists to pass:

      * a value containing `;` ended the statement early -- `column_def.samples`
        holds JSON like `["… TESTO A CASO;", "…"]` and the row was truncated;
      * the writer also appended its own `;` to a statement whose last value had
        been cut at that `;`, producing `);,` and a syntax error.

    So the dump is **loaded before it is believed**, in `check_dump()` below, and
    a dump that does not load is a failure of the run rather than a file somebody
    finds later.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, Decimal)):
        return decimal_literal(value)
    text = str(value)
    if text == "":
        return "NULL"
    # sqlite's `quote()` **truncates at a NUL** rather than escaping it, because
    # the C API takes a length-terminated string. Silently writing half a value is
    # the one thing a lossless store must never do, so the character is refused
    # and reported. No value in the archive contains one -- measured over all
    # 94,358 cells of 2025 and the whole catalog -- so this never fires, and it is
    # here so that it cannot fire *silently* if a future year does.
    if "\x00" in text:
        raise ValueError(
            f"value contains a NUL character, which sqlite would truncate "
            f"silently: {text[:60]!r}")
    return _QUOTER.execute("SELECT quote(?)", (text,)).fetchone()[0]


def dump_rows(conn, table, order, columns):
    """Each row as a parenthesised tuple, with **no** trailing `;`.

    The terminator belongs to the statement, not the row, and the writer adds it
    once after the last row. Having it here as well is what produced `);,` at
    every row boundary and made the whole dump unloadable -- while the row counter
    reported a correct 97,017, because nothing that counted rows would ever notice
    a syntax error.
    """
    cursor = conn.execute(
        "SELECT {} FROM {} ORDER BY {}".format(
            ", ".join(columns), table, ", ".join(order)))
    for row in cursor:
        yield "(" + ", ".join(sql_literal(v) for v in row) + ")"


def write_dump(conn, year, out_dir, replace=False, gzipped=True):
    """Write `Out/SQL/armtrace-<year>.sql` and its `.gz`.

    Deterministic, because a dump is meant to be diffed: fixed table order, rows
    in natural-key order, explicit column lists, 2-dp decimals as DECIMAL
    literals, `BEGIN`/`COMMIT`, LF, and **UTF-8 without BOM** -- a BOM in a
    `.sql` file is a syntax error, which is the opposite of the old CSV default.

    The gzip is written with `mtime=0` so the compressed bytes are reproducible
    too; a `.gz` that embeds a timestamp differs on every run and makes the file
    useless for the comparison it exists to support.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plain = out_dir / f"armtrace-{year}.sql"
    tables = [
        ("table_year",
         ["authority", "article", "code", "year", "path", "pages", "bytes",
          "src_first", "src_last", "index_title", "witness", "orientation",
          "mixed_dir", "ruled", "longest_rule", "legible_pages", "garbled_pages",
          "ciphered_pages", "empty_pages", "words", "bands", "n_columns",
          "stacked_header", "header_stable", "grammar", "grammar_detail",
          "band_kinds", "repeated_keys", "total_bands", "columns",
          "extractable", "extract_reason", "extract_detail", "fingerprint"],
         ["authority", "article", "code", "year"]),
        ("column_def",
         ["authority", "article", "code", "year", "ordinal", "label",
          "label_key", "header_path", "role", "width", "bold", "shapes",
          "samples"],
         ["authority", "article", "code", "year", "ordinal"]),
        ("page_stat",
         ["authority", "article", "code", "year", "page", "orientation",
          "status", "pua_ratio", "ruled", "longest_rule", "words", "bands"],
         ["authority", "article", "code", "year", "page"]),
        ("stg_cell",
         ["authority", "article", "code", "year", "page", "row_ord", "col_ord",
          "group_ord", "row_kind", "col_key", "raw_text", "text_value",
          "num_value", "unit", "is_total"],
         ["authority", "article", "code", "year", "row_ord", "col_ord"]),
    ]
    counts = {}
    written = 0
    with open(plain, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"-- armtrace-{year}.sql -- generated by ExtractTables.py\n")
        fh.write("-- lossless cell store: every printed cell of every readable "
                 "table-year\n")
        fh.write(f"-- year {year}; UTF-8 without BOM; deterministic: fixed "
                 "table and row order\n")
        fh.write("-- load with: sqlite3 archive.db < armtrace-"
                 f"{year}.sql\n")
        if replace:
            fh.write("\n-- --replace was given: drop first.\n")
            for name, _cols, _order in tables:
                fh.write(f"DROP TABLE IF EXISTS {name};\n")
        fh.write("\n")
        fh.write(CATALOG_DDL.strip() + "\n")
        fh.write("\nBEGIN;\n")
        for name, cols, order in tables:
            n = 0
            fh.write(f"\n-- {'-' * 60}\n-- {name}\n-- {'-' * 60}\n")
            fh.write("INSERT INTO {} ({}) VALUES\n".format(name, ", ".join(cols)))
            first = True
            for row in dump_rows(conn, name, order, cols):
                fh.write(("" if first else ",\n") + row)
                first = False
                n += 1
            if first:
                fh.write("-- (no rows)\n")
            else:
                fh.write(";\n")
            counts[name] = n
            written += n
        fh.write("\nCOMMIT;\n")
    gz_path = None
    if gzipped:
        gz_path = plain.with_suffix(".sql.gz")
        with open(plain, "rb") as src, \
                gzip.GzipFile(filename="", mode="wb", fileobj=open(gz_path, "wb"),
                              mtime=0, compresslevel=6) as dst:
            dst.write(src.read())
    loaded = check_dump(plain, counts)
    return plain, gz_path, counts, written, loaded


def check_dump(plain, counts):
    """Load the finished dump into an empty database and compare the row counts.

    **A dump that has not been loaded is not a dump.** Two separate writer bugs --
    an unescaped `;` truncating a row and a doubled statement terminator -- were
    invisible to every other check in this file: the file was written, the gzip
    was produced, the row counter said 97,017. Only loading it found
    `near ",": syntax error` at line 293. The cost of this check is one parse of
    a file just written, which is seconds against a 160 s run, and it is the
    difference between shipping a dump and shipping a file that looks like one.
    """
    fresh = sqlite3.connect(":memory:")
    try:
        with open(plain, "r", encoding="utf-8") as fh:
            fresh.executescript(fh.read())
        for name, expected in counts.items():
            got = fresh.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            if got != expected:
                fresh.close()
                raise ValueError(
                    f"dump for {name} loaded {got} rows but {expected} were "
                    f"written -- the file does not round-trip")
    finally:
        try:
            fresh.close()
        except Exception:
            pass
    return counts


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extract the catalogued tables into stg_cell rows, and "
                    "optionally dump them as deterministic SQL.")
    parser.add_argument("--year", required=True)
    parser.add_argument("--out-root", default="Out")
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--code", action="append", default=[],
                        help="restrict to these table codes (repeatable)")
    parser.add_argument("--jobs", type=int, default=0,
                        help="parallel workers (0 = one per cpu)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="stop after N pages of each table -- the dev loop")
    parser.add_argument("--dump", action="store_true",
                        help="write Out/SQL/armtrace-<year>.sql[.gz] (opt-in: "
                             "never written as a side effect of a run)")
    parser.add_argument("--dump-replace", action="store_true",
                        help="emit DROP TABLE IF EXISTS before the CREATE")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    year = int(args.year)
    out_root = Path(args.out_root)
    catalog = Path(args.catalog) if args.catalog else out_root / "CATALOG" / "catalog.sqlite"
    if not catalog.exists():
        print(f"catalog not found: {catalog}\n"
              f"run CatalogueTables.py --year {year} first", file=sys.stderr)
        return 2

    conn = open_catalog(str(catalog))
    try:
        jobs = plan_jobs(conn, year, out_root, set(args.code), args.max_pages)
        if not jobs:
            print(f"no extractable tables for {year}", file=sys.stderr)
            return 1
        print(f"{len(jobs)} tables queued for {year}", flush=True)
        if args.dry_run:
            for job in jobs:
                print("  {} {}".format("/".join(x or "-" for x in job[0]), job[3]))
            return 0

        workers = args.jobs or min(8, (os.cpu_count() or 2))
        started = time.time()
        done, failures = 0, []
        if workers > 1 and len(jobs) > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                results = pool.map(extract_one, jobs, chunksize=1)
                for base, rows, detail in results:
                    done += 1
                    _absorb(conn, base, rows, detail, failures, done, len(jobs))
                    conn.commit()
        else:
            for job in jobs:
                base, rows, detail = extract_one(job)
                done += 1
                _absorb(conn, base, rows, detail, failures, done, len(jobs))
                conn.commit()
        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM stg_cell WHERE year=?",
                             (year,)).fetchone()[0]
        extracted = conn.execute(
            "SELECT COUNT(*) FROM table_year WHERE year=? AND"
            " json_extract(extract_detail, '$.status')='extracted'",
            (year,)).fetchone()[0]
        print(f"\n{extracted} tables extracted, {total:,} cells for {year}, "
              f"in {time.time() - started:.1f}s")
        if failures:
            print("\nproblems:", file=sys.stderr)
            for line in failures:
                print("  " + line, file=sys.stderr)

        if args.dump:
            plain, gz, counts, written, _ = write_dump(
                conn, year, out_root / "SQL", replace=args.dump_replace)
            print(f"\ndump: {plain} ({plain.stat().st_size:,} bytes)")
            if gz:
                print(f"      {gz} ({gz.stat().st_size:,} bytes)")
            for name, n in counts.items():
                print(f"  {name:<12} {n:>9,} rows")
            print(f"  {'total':<12} {written:>9,} rows")
            print(f"  loaded back into an empty database and compared: OK")
    finally:
        conn.close()
    return 0


def _details(conn, year):
    """Every table's extraction bookkeeping, for a caller that wants it."""
    for authority, article, code, detail in conn.execute(
            "SELECT authority, article, code, extract_detail FROM table_year "
            "WHERE year=?", (year,)):
        yield (authority, article, code), json.loads(detail) if detail else None


def _absorb(conn, base, rows, detail, failures, done, total):
    write_rows(conn, base, rows)
    record_detail(conn, base, detail)
    label = "/".join(x or "-" for x in
                     (base["authority"], base["article"], base["code"]))
    if detail.get("status") == "error":
        failures.append(f"{label}: {detail.get('reason')}")
        print(f"  [{done:>3}/{total}] {label:<28} ERROR {detail.get('reason')}",
              flush=True)
        return
    note = ""
    if detail.get("drift_page_count"):
        note = f"  column-drift {detail['drift_page_count']}pp"
    if detail.get("skipped_page_count"):
        note += f"  unreadable {detail['skipped_page_count']}pp"
    print(f"  [{done:>3}/{total}] {label:<28} "
          f"{detail.get('status','?'):<9} {detail.get('rows',0):>7,} rows"
          f"  {detail.get('seconds',0):>6.1f}s{note}", flush=True)


if __name__ == "__main__":
    sys.exit(main())