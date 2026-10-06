#!/usr/bin/env python3
"""
What a band of cells *means*, and what grammar a whole table speaks.

`lib/geometry.py` turns glyphs into rows and columns and stops there. This
module supplies the two things it deliberately does not:

  1. the shape of a printed value -- an Italian `1.234,56`, a `€ 12.877,00`, a
     `03/03/2025`, a `—`, a code like `016` -- and a lossless parse of it;
  2. the kind of a band, from which a table's *record grammar* follows.

Why the grammar has to be measured rather than assumed
-----------------------------------------------------
2025's 110 tables do not share a layout. Four grammars were confirmed by
reading the pages:

  flat                 MG10 `Denominazione operatore | Quantita | Valore (Euro)`
                        -- one record per operator, three borderless columns, and
                        a printed `Totale complessivo 262.920.093,96`.

  group+subtotal       A1 `Valuta | Ammontare | EURO | Val. Fini Dog. | Data |
                        Q.ta' | Unita' Misura | Materiale oggetto del contratto |
                        Tipo | Cat.` -- the operator name is a band of its own
                        (`A.C.S.A. STEEL FORGINGS S.P.A.`), the group closes on
                        a printed subtotal (`3.700.813,79  0,00`), and one
                        record wraps over several bands because the description
                        column is justified and multi-line.

  repeated-subdimension EE `Numero Autorizzazione | Utilizzatore Finale |
                        Causale | Importi Segnalati | Importi Accessori
                        Segnalati` -- one authorisation number carries several
                        `Causale` rows, each with its own amounts, so the naive
                        reading of "one row = one record" invents authorisations.

  wrapped-list          Difesa `Annesso 4` -- six columns where `PAESI` and
                        `DITTE ITALIANE` hold company and country lists that run
                        over ten printed lines inside a single cell.

A fifth grammar is entirely possible in another year, so `grammar_of` returns
`unknown` rather than forcing a fit, and `band_kind` returns `other` rather than
inventing a kind. The catalog stores the measured features *next to* the label,
so a label can be revised without re-reading a single PDF.

The one thing this module will not do is guess a number. `parse_number` returns
`None` for anything it cannot account for, and the caller keeps the printed text;
a silent `float()` on `1.234,56` yields 1.234, which is wrong by three orders of
magnitude and looks plausible.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------
# printed value shapes
# --------------------------------------------------------------------------

# A number as the archive prints it: optional sign, digits, Italian decimal
# comma, and either a thousands dot or spaced thousands. `1.234,56` and
# `1 234,56` and `1234,56` are the same number; `1,234.56` is the other
# convention and does not occur -- but it is recognised rather than mangled, by
# `parse_number` refusing anything it cannot account for.
NUMBER_RE = re.compile(r"""
    ^\s*
    (?P<neg>[-−–—])?\s*
    (?P<paren>\()?\s*
    (?P<int>\d{1,3}(?:[.\u00a0 ]\d{3})*|\d+)
    (?P<frac>[.,]\d{1,4})?
    \s*(?P<paren2>\))?
    (?P<pct>\s*%)?
    (?P<cur>\s*(?:€|EUR|euro))?
    \s*$
""", re.VERBOSE)

CURRENCY_RE = re.compile(r"^\s*(?:€|EUR|euro)\s*", re.IGNORECASE)
# The currency is printed on *either* side depending on the ministry: MEF
# art. 27 writes `€ 12.877,00` with the sign first, MAE writes `887.145,00`
# with no sign at all and names the unit in the column heading. Both forms are
# stripped before the number is parsed, and `value_shape` reports which was
# printed so the catalog can tell the two ministries apart.
TRAILING_CURRENCY_RE = re.compile(r"\s*(?:€|EUR|euro)\s*$", re.IGNORECASE)
DATE_RE = re.compile(
    r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$")
YEAR_RE = re.compile(r"^\s*(19|20)\d{2}\s*$")
DASHES = {"", "-", "–", "—", "‐", "‑", "−", "_", ".", "n.d.", "N.D.",
          "nd", "ND", "n/a", "N/A", "/", "null", "NULL"}
CODE_RE = re.compile(r"^\s*\d{1,6}\s*$")          # 016, 01, 001 -- leading zeros matter
TOTAL_RE = re.compile(
    r"\b(totale|totali|totale\s+complessivo|totale\s+generale|"
    r"totale\s+autorizzazioni|complessivo|grand\s*total)\b", re.IGNORECASE)

# A column counts as prose-carrying at this multiple of the lower-quartile
# column width. Measured on 2025: A1's description column is 6.3x its quartile
# and `Tipo` is 0.9x; the Difesa `DITTE ITALIANE` is 5.1x and its index column
# `NR.` is 0.2x. 1.6x sits in the empty middle of that distribution.
WIDE_COLUMN_EM = 1.6

SHAPE_EMPTY = "empty"
SHAPE_DASH = "dash"
SHAPE_NUMBER = "number"
SHAPE_CURRENCY = "currency"
SHAPE_DATE = "date"
SHAPE_YEAR = "year"
SHAPE_PERCENT = "percent"
SHAPE_CODE = "code"
SHAPE_TOTAL_LABEL = "total_label"
SHAPE_TEXT = "text"


def value_shape(text):
    """Classify one printed cell. Never raises, never guesses.

    The order matters and encodes the archive's typography: a dash is a dash
    even though it is not a number, `0,00` is a number rather than a code even
    though it is all digits, and a company name is text.
    """
    if text is None:
        return SHAPE_EMPTY
    raw = str(text).strip()
    if raw == "":
        return SHAPE_EMPTY
    if raw in DASHES:
        return SHAPE_DASH
    leading = bool(CURRENCY_RE.match(raw))
    bare = CURRENCY_RE.sub("", TRAILING_CURRENCY_RE.sub("", raw)).strip()
    if TOTAL_RE.search(raw) and not NUMBER_RE.match(bare):
        return SHAPE_TOTAL_LABEL
    if leading and NUMBER_RE.match(bare):
        return SHAPE_CURRENCY
    if DATE_RE.match(raw):
        return SHAPE_DATE
    if YEAR_RE.match(raw):
        return SHAPE_YEAR
    if CODE_RE.match(raw):
        return SHAPE_CODE
    if NUMBER_RE.match(raw):
        return SHAPE_PERCENT if raw.rstrip().endswith("%") else SHAPE_NUMBER
    return SHAPE_TEXT


def parse_number(text):
    """A printed number as a `Decimal`, or None.

    Italian convention throughout: `.` groups thousands and `,` divides. Using
    float() here would turn `1.234,56` into 1.234, so every monetary column in
    the archive would be wrong by 1000x and would still look like a number.
    `Decimal` also keeps `1.234,00` exactly, which matters because the printed
    subtotals are compared against the sum of the rows below them.

    None means "not a number I can account for", and the caller must keep the
    printed text. It is returned for `1,234.56` (the other convention, not
    observed in this archive but reachable from a future year), for a code like
    `016` -- leading zeros are an identity, not a quantity -- and for a dash.
    """
    raw = str(text).strip()
    if raw == "" or raw in DASHES:
        return None
    bare = CURRENCY_RE.sub("", TRAILING_CURRENCY_RE.sub("", raw)).strip()
    m = NUMBER_RE.match(bare)
    if not m:
        return None
    intpart = m.group("int")
    frac = m.group("frac") or ""
    if "," in frac or (frac.startswith(".") and len(frac) - 1 != 3):
        decimal_point = frac[0] if frac else ""
    else:
        decimal_point = ""
    int_digits = re.sub(r"[.\u00a0 ]", "", intpart)
    # Reject the other convention rather than misreading it: 1,234.56 has a
    # comma before the dot, which this archive never prints.
    if "," in int_digits:
        return None
    # A leading zero on a bare 1-3 digit integer is an identity, not a
    # quantity. The archive prints `Tipo` as 01/02/04 and `Cat.` as
    # 001/010/016, and `016` must not become 16 -- it is the join key to the
    # Military List, and 016 != 16 for every query anyone will ask. A larger
    # number keeps its leading zeros as ordinary digits, because `001.234` is
    # one thousand two hundred and thirty-four.
    if "," not in frac and "." not in intpart and len(int_digits) <= 3 \
            and len(int_digits) > 1 and int_digits[0] == "0":
        return None
    frac_digits = frac[1:] if frac else ""
    if decimal_point == "." and len(frac_digits) == 3 and "," not in raw:
        # `1.234` is ambiguous: thousands grouped, or a decimal with 3 places.
        # The archive means thousands when the integer part is 1-3 digits.
        return Decimal(int_digits)
    try:
        value = Decimal(f"{int_digits}.{frac_digits}" if frac_digits
                        else int_digits)
    except InvalidOperation:
        return None
    if m.group("neg") or (m.group("paren") and m.group("paren2")):
        value = -value
    return value


def parse_date(text):
    """`03/03/2025` -> `2025-03-03`, or None.

    Italian day-first order, checked rather than assumed: `02/03/2025` is
    ambiguous across locales and the archive prints dates inside a `Data`
    column whose neighbours are all day-first, so a month>12 reading is the only
    disambiguation available and a day-first reading is taken otherwise.
    """
    m = DATE_RE.match(str(text).strip())
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000 if year < 70 else 1900
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalise_text(text):
    """NFKC, collapsed whitespace, trimmed -- for comparison and for storage.

    The archive prints `Unita'` and `Unita’` and `UNITA'` for the same heading,
    and justified cells carry runs of spaces. The printed string is still kept
    verbatim in `stg_cell.raw_text`; this is only the comparable form.
    """
    if text is None:
        return ""
    folded = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", folded).strip()


# --------------------------------------------------------------------------
# band kinds
# --------------------------------------------------------------------------

KIND_BLANK = "blank"
KIND_HEADER = "header"
KIND_DATA = "data"
KIND_CONTINUATION = "continuation"
KIND_GROUP = "group"
KIND_SUBTOTAL = "subtotal"
KIND_WRAPPED = "wrapped"
KIND_TOTAL = "total"
KIND_OTHER = "other"

# The shapes that make a cell a *measurement*. A band whose filled cells are all
# of these is arithmetic; anything with text in it is not.
MEASURE_SHAPES = {SHAPE_NUMBER, SHAPE_CURRENCY, SHAPE_PERCENT, SHAPE_CODE,
                  SHAPE_DASH, SHAPE_EMPTY, SHAPE_YEAR}
# The shapes that identify a date column, used to tell a data band from a
# subtotal: a real record nearly always carries its date.
DATE_SHAPES = {SHAPE_DATE}


def band_profile(cells):
    """Measured features of one band of cells -- the catalog's raw material.

    Deliberately arithmetic rather than categorical: every count here is
    something a band either has or does not, so a grammar rule can be stated
    over numbers and revised without re-reading the PDFs.
    """
    filled = [(i, c) for i, c in enumerate(cells) if str(c).strip()]
    shapes = [value_shape(c) for _, c in filled]
    numeric = sum(1 for s in shapes
                  if s in (SHAPE_NUMBER, SHAPE_CURRENCY, SHAPE_PERCENT))
    texty = sum(1 for s in shapes if s == SHAPE_TEXT)
    dates = sum(1 for s in shapes if s in DATE_SHAPES)
    total_labels = sum(1 for s in shapes if s == SHAPE_TOTAL_LABEL)
    return {
        "filled": len(filled),
        "numeric": numeric,
        "text": texty,
        "dates": dates,
        "total_labels": total_labels,
        "shapes": shapes,
        "cells": [normalise_text(c) for c in cells],
    }


def band_kind(profile, columns=None, previous=None):
    """What kind of band this is, from its profile and the band above it.

    The rules, in the order they are applied:

      blank          nothing printed
      total          carries a `Totale`-shaped label
      header         every filled cell is bold or the band is the widest on the
                     page and has no date -- see `is_header`
      group          exactly one filled cell, it is text, and it is not numeric:
                     A1's operator name, printed across the table
      subtotal       two or more filled cells, all of them arithmetic, and no
                     date: A1's per-operator `3.700.813,79  0,00`
      continuation   one or two filled cells and they are all in the wide text
                     columns: a wrapped description line belonging to the
                     record above it
      data           anything else
      other          a band with no filled cells that is not blank

    `columns` is needed for the wide-column tests, because "is this the description
    column" is a question about the column, not about the band. Without it a
    continuation is recognised as a single-cell band, which is the same thing on
    A1 and a guess elsewhere.

    `previous` is the kind of the band above. It is what separates a wrapped
    *continuation* from a wrapped *row*: both are wide-column text with no key,
    but a continuation belongs to the record above it and a wrapped row is a
    record of its own. Passing it costs one argument and is the difference
    between the Difesa Annesso 4 body being 113 group bands and being 12
    wrapped rows.
    """
    if profile["filled"] == 0:
        return KIND_BLANK
    if profile["total_labels"]:
        return KIND_TOTAL
    if profile["dates"]:
        return KIND_DATA
    # A band made only of prose, sitting in the wide columns, is a wrapped row
    # or a wrapped continuation -- never a group header. This test has to come
    # *before* the single-cell group test, because on a wrapped-list table every
    # row is a handful of text cells and the single-cell rule then claims all
    # 113 of them: Difesa Annesso 4 has no dates, no amounts and no group
    # headers at all, yet was catalogued as 113 group bands and no data.
    wide_early = _wide_columns(columns) if columns else set()
    if wide_early:
        positions = [i for i, c in enumerate(profile["cells"]) if c]
        if (positions and all(i in wide_early for i in positions)
                and profile["numeric"] == 0 and profile["total_labels"] == 0):
            if profile["dates"] or profile["numeric"]:
                return KIND_CONTINUATION
            return KIND_WRAPPED if previous in (KIND_CONTINUATION,
                                               KIND_WRAPPED) else KIND_DATA
    if profile["filled"] == 1 and profile["text"] == 1:
        return KIND_GROUP
    if profile["filled"] >= 2 and profile["text"] == 0 and profile["numeric"] >= 2:
        return KIND_SUBTOTAL
    wide = _wide_columns(columns) if columns else set()
    if wide:
        positions = [i for i, c in enumerate(profile["cells"]) if c]
        if positions and all(i in wide for i in positions):
            # A band made only of wide-column text is either a wrapped
            # continuation of the record above (A1's justified description, which
            # follows a data band carrying a date) or a whole row of a
            # wrapped-list table (Difesa Annesso 4, whose body is company names
            # in two wide columns and which follows another such row).
            if profile["dates"] or profile["numeric"]:
                return KIND_CONTINUATION
            if previous in (KIND_CONTINUATION, KIND_WRAPPED):
                return KIND_WRAPPED
            return KIND_CONTINUATION
    if profile["numeric"] == 0 and profile["text"] >= 1:
        return KIND_GROUP
    return KIND_DATA


def _wide_columns(columns):
    """Ordinals of the columns carrying prose: those much wider than the rest.

    A1's `Materiale oggetto del contratto` is 94pt against a 15pt median and
    `Tipo` is 14pt; the Difesa `DITTE ITALIANE` is 113pt against 25pt. The test
    is against the **lower quartile**, not the median, because the median of
    four widths is the mean of the middle two and therefore lands on the widest
    column: with Difesa's [30.9, 58.3, 82.8, 96.3] the median is 82.8, nothing
    clears 1.6x it, and the whole table came back with no wide column at all --
    which is what made 113 wrapped rows read as 113 group headers.

    Lower quartile rather than minimum, so one unusually narrow index column
    does not make everything else look wide.
    """
    if not columns:
        return set()
    widths = sorted(c["u1"] - c["u0"] for c in columns)
    if len(widths) < 3:
        return set()
    quartile = widths[int(0.25 * (len(widths) - 1))] or 1.0
    return {i for i, c in enumerate(columns)
            if (c["u1"] - c["u0"]) >= WIDE_COLUMN_EM * quartile}


def is_header(cells, columns):
    """A header band is the one with the most cells, when it is unambiguous.

    Taken as an argument rather than sniffed, because the archive prints
    `Valuta Ammontare EURO ...` once per table and not per page, and a boldness
    test is unreliable here: the A1 header's own font is a subset whose name
    carries no weight hint in some years.
    """
    if not columns:
        return False
    filled = sum(1 for c in cells if str(c).strip())
    return filled >= max(3, 0.6 * len(columns))


# --------------------------------------------------------------------------
# table grammars
# --------------------------------------------------------------------------

GRAMMAR_CHART = "chart"
GRAMMAR_FLAT = "flat"
GRAMMAR_GROUP_SUBTOTAL = "group+subtotal"
GRAMMAR_REPEATED_SUBDIM = "repeated-subdimension"
GRAMMAR_WRAPPED_LIST = "wrapped-list"
GRAMMAR_UNKNOWN = "unknown"

GRAMMAR_LABELS = {
    GRAMMAR_CHART: "a percentage chart, not a grid of cells",
    GRAMMAR_FLAT: "one record per band",
    GRAMMAR_GROUP_SUBTOTAL: "group header bands and per-group subtotals",
    GRAMMAR_REPEATED_SUBDIM: "a repeated dimension inside each record",
    GRAMMAR_WRAPPED_LIST: "cells holding lists wrapped over many lines",
    GRAMMAR_UNKNOWN: "no grammar established",
}


def grammar_of(kinds, columns, repeated=None):
    """Label a table's grammar from the measured band kinds.

    Every clause is over counts, so a threshold can be argued with, and the
    order is significant -- each test is stronger evidence than the one after it.

        unknown                no data bands at all: a cover, an empty table, or a
                               page whose bands are all fragments of a row
        wrapped-list           a third or more of the bands are continuations
                               and there are wide text columns: the Difesa annessi
        group+subtotal         group bands and subtotal bands both present -- A1
        repeated-subdimension  a key column repeats across consecutive data bands
                               while another key varies: EE, one authorisation
                               number carrying several causale rows
        flat                   everything else with data bands: MG10

    `repeated` is `repeated_key_columns`' output. It is consulted *before* the
    group rule because a group band and a repeated key can coexist in the same
    table, and the sub-dimension is then the finer fact of the two: the record
    key is the pair, and reporting only "group" would hide that EE's rows are
    causale-level rather than authorisation-level.
    """
    total = len(kinds) or 1
    data = kinds.count(KIND_DATA)
    groups = kinds.count(KIND_GROUP)
    subtotals = kinds.count(KIND_SUBTOTAL)
    continuations = kinds.count(KIND_CONTINUATION)
    wrapped_rows = kinds.count(KIND_WRAPPED)
    wide = _wide_columns(columns) if columns else set()

    if data == 0:
        # Every band is text with no arithmetic anywhere in the table. That is a
        # wrapped-list table -- Difesa Annesso 4, six columns of programme names
        # and company lists, not one date or euro in 113 bands -- and not a
        # cover, provided the bands do span the columns.
        if wrapped_rows and wide:
            return GRAMMAR_WRAPPED_LIST
        return GRAMMAR_UNKNOWN
    if continuations >= 0.3 * total and wide and data <= 0.5 * total:
        return GRAMMAR_WRAPPED_LIST
    if wrapped_rows >= 0.3 * total and wide:
        return GRAMMAR_WRAPPED_LIST
    if groups and subtotals:
        return GRAMMAR_GROUP_SUBTOTAL
    if repeated:
        # At least one column repeats and at least one varies: a single
        # repeating column with no varying one is a constant, not a dimension.
        if any(r["distinct"] >= 2 for r in repeated):
            return GRAMMAR_REPEATED_SUBDIM
    if groups:
        return GRAMMAR_GROUP_SUBTOTAL
    return GRAMMAR_FLAT


def repeated_key_columns(cells_by_band, columns, kinds):
    """Columns that repeat across consecutive bands -- the record key candidates.

    A column that carries the same text on two neighbouring data bands is a
    dimension of the record above rather than a measure of this one: EE's
    `Numero Autorizzazione` reads 92956 on four consecutive bands, while
    `Importi Segnalati` differs on each. That difference is what tells a
    repeated sub-dimension from a flat record, and it is measured here rather
    than assumed per ministry.
    """
    if not columns or len(columns) < 2:
        return []
    data_idx = [i for i, k in enumerate(kinds) if k == KIND_DATA]
    repeats = []
    for j in range(len(columns)):
        values = [cells_by_band[i][j] for i in data_idx]
        if not values:
            continue
        filled = [v for v in values if v]
        if len(filled) < 2:
            continue
        changes = sum(1 for a, b in zip(filled, filled[1:]) if a != b)
        ratio = changes / max(1, len(filled) - 1)
        if ratio <= 0.34:
            repeats.append({
                "ordinal": j,
                "label": normalise_text(columns[j].get("text", "")),
                "distinct": len(set(filled)),
                "filled": len(filled),
                "repeat_ratio": round(1 - ratio, 3),
            })
    return repeats