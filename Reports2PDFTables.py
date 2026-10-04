#!/usr/bin/env python3
"""
Reports2PDFTables — split a Law 185/1990 annual report volume into one PDF
per table.

    reports_185_1990/<anno>/<relazione>.pdf
        ->  Out/PDF/<tabella>/<tabella><anno>.PDF

The year is read from the reports_185_1990/<anno>/ path, so one script serves
every reporting year. With many years present, --year is required.

Usage:
    ./Reports2PDFTables.py --year 2024 --volume both
    ./Reports2PDFTables.py --year 2023 --volume 2
    ./Reports2PDFTables.py --report reports_185_1990/2023/2023_LXVII_n2_VOLUME_II.pdf
    ./Reports2PDFTables.py --all                 # every text-bearing volume
    ./Reports2PDFTables.py --year 2023 --dry-run # report only, write nothing

Requires: pypdf (pip install -r requirements.txt)

Note: --volume both / --all. A year is processed one volume at a time but its
tables are not confined to a volume: 2025 Tabella F1 runs from printed page
1035 (volume I) to printed page 1104 (volume II). Stitching the volumes of a
year together needs more than one of them in the same run; with a single
volume the split PDF simply stops at the volume boundary and says so.

--------------------------------------------------------------------------
HOW TABLES ARE FOUND
--------------------------------------------------------------------------
A table occupies a contiguous run of pages, so the task reduces to finding
each table's first page; the last page is one before the next table starts.

Three code schemes are in the archive. A volume uses exactly one:

    family 1   art. 27 double-letter   AA AA1 BB BB1 ... UE, plus
               MG1-MG9 MT1 MT7 GF NN OO PP for the MEF/UAMA signalled tables
    family 2   A1 .. P2                31 codes, printed as "TAB A1"
    family 3   art. 27 single-letter   A B D E G J Q (2012 vol. I)

Two independent detectors feed one manifest:

    embedded   the PDF bookmark tree, when the volume carries one
    header     the table code printed in the page header

They are UNIONED, not ranked. Bookmarks are human-authored and win on a page
conflict, but they are not a superset: on 2025 vol. II the outline omits
MG1-MG9 and MT1/MT7, which the header scan finds. Conversely the header scan
invents codes from prose, which is why the bookmark wins where they disagree.

Two more detectors exist only to fill gaps:
    index      an ELENCO page listing codes and their page COUNTS, used to
               cross-check the derived spans
    title      the table title repeated through the body, for family 3 where
               the codes appear only on the index page

--------------------------------------------------------------------------
TABLES THAT CROSS A VOLUME BOUNDARY
--------------------------------------------------------------------------
A year's volumes continue one another, and a table may straddle the join. The
volumes are numbered continuously, so the last table of volume N can run on
into the pages volume N+1 opens with, before that volume's first detected
table. Those pages belong to the same PDF: Out/PDF/F1/F12025.PDF is one
70-page table, not two files fighting over one name.

Since the split is per volume, the volume that wrote the tail of the table
used to overwrite the volume that wrote its head. See stitch().
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    sys.exit("Errore: pypdf non installato. Esegui: pip install -r requirements.txt")

REPORTS_DIR = "reports_185_1990"
OUT_DIR = "Out"

# Per-table PDFs are written as Out/PDF/<tabella>/<tabella><anno>.PDF, the
# uppercase extension included. IndividualTables2SQL.py globs for exactly this
# name (PDF_EXT there); change both together or step 2 finds nothing, since
# globbing is case-sensitive on Linux.


# ==========================================================================
# code vocabularies and patterns
# ==========================================================================

# Family 2 is a closed set of 31 codes, confirmed by the bookmark trees of
# 2021 tom. I, 2023 vol. I and 2025 vol. I, which agree on every code.
FAMILY2_CODES = {
    "A1", "A2", "A3", "A4",
    "B1", "B2", "B3", "B4", "B5", "B6", "B7",
    "C1", "C2", "D", "E",
    "F1", "F2", "G1", "G2", "H1", "H2", "I", "L",
    "M1", "M2", "N1", "N2", "O1", "O2", "P1", "P2",
}

# art. 27 codes, families 1 and 3: one to three capitals plus optional digit.
CODE_RE = re.compile(r"^[A-Z]{1,3}\d?$")

# "TAB A1", "Tabella E", "TAB. MG1". Two traps, both hit in development:
#   - a word boundary after TAB is required, or "TABLES" parses as TAB + "LES"
#     and "TABLET" as TAB + "LET";
#   - the separator must NOT span a newline. These PDFs break lines mid-word,
#     so prose such as "una tabella (F\nG) ed un grafico (GF\n)" otherwise
#     reads as a table code. A real code never has its keyword split from it.
INLINE_CODE = re.compile(r"\b(?i:TAB|TABELLA)\b\.?[^\S\n]+([A-Z]{1,3}\d?)\b")

# Bookmark titles come in two shapes needing two patterns:
#   "03_2023_TAB_A1", "2025 TAB A1 (EXP per Operatore)"   separated
#   "tabellaAA_2025", "tabellaFG_2025 (1)"                glued
# Neither can use \b: underscore is a word character, so "TAB_A1" has no word
# boundary around "TAB". Negative letter lookarounds instead.
TITLE_RE = re.compile(
    r"(?:(?<![A-Za-z])TAB[ ._]+|tabella)([A-Z]{1,3}\d?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# "2 401 TAB A1  Esportazione definitiva per operatori   469"
INDEX_ROW = re.compile(
    r"^\s*\d+\s+(\d+)\s+TAB\.?\s+([A-Z]{1,3}\d?)\s+(.+?)\s+(\d+)\s*$"
)

# A line that is exactly "Tabella AA" (2025 vol. II, 2012 index listing).
LEADING_CODE = re.compile(r"^(?i:Tabella)\.?\s+([A-Z]{1,3}\d?)$")

# Private Use Area: what pypdf emits for a font with no ToUnicode CMap.
PUA_RE = re.compile(r"[-]")

# Italian words that follow "Tabella" in prose ("TABELLA DEI CODICI DELLE
# VALUTE"). Consulted only when a volume has no index page to harvest a real
# vocabulary from. E and I are deliberately absent: both are genuine family 2
# codes, and suppressing them cost 4 tables on 2019 vol. I. Membership in
# FAMILY2_CODES takes precedence over this set.
PROSE_STOPLIST = {
    "DEI", "DEL", "DELLA", "DELLE", "DI", "LA", "LE", "IL", "UN", "UNA",
    "CODICI", "CODICE", "DESCRIZIONE", "VALORI", "RIEPILOGO", "DETTAGLIATO",
}

# A table repeats its code on every page of its run; a prose cross-reference
# does not. Two hits is the threshold -- the genuinely short tables are 1-2
# pages, and a single hit is always ambiguous. Applied to style 3 only; see
# build_manifest for why.
MIN_RUN = 2

# How many pages past the last matching running header a continuation may run
# before it is stopped. A table often closes with a differently shaped page --
# a totals sheet, a chart -- and dropping it loses real figures: 2025 F1 ends
# on printed page 1104, "Totale autorizzazioni", whose margin reads
# "Tabella F1 / Pagina 70 di 70". Kept small, since past this point the pages
# are more likely to be the next table's than this table's.
TAIL_PAGES = 2

# A footer page number: up to four digits framed by the three dashes these
# documents use, on either side or one side only ("– 1103 –", "- 1103-",
# "1103 -"). Dashes strictly outside the digits, so a numeric range inside a
# table cell ("27 - 15") is not mistaken for one. Roman numerals do not match,
# which is what keeps the index page of 2025 ("– III –") out of the sequence.
PAGE_NUMBER_RE = re.compile(r"^\s*[–—-]?\s*(\d{1,4})\s*[–—-]?\s*$")


# ==========================================================================
# the "Pagina N di X" stamp
# ==========================================================================
#
# Each table was exported as a document of its own and then pasted into the
# volume, so every page carries the pagination of the document it came from:
# a stamp in the right margin reads "Pagina N di X" -- page N of X. It is the
# only witness of how long a table really is. The volume folio ("-  1035  -")
# runs continuously across tables and says nothing about their boundaries,
# and "the page before the next table starts" is wrong whenever a document
# brought trailing junk with it.
#
# The stamp is set in the 9pt export header, rotated, in the middle of the
# right margin, between "MAECI - UAMA - CENTRO INFORMATICO" above and the
# table code below. Both the position and the shape are needed: on the text
# alone the shape also matches body rows, and
# "MUNIZIONAMENTO CALIBRO 120 MM , APPOSITAMENTE" (2025 vol. I p70) would
# then have cut A1 from 537 pages to 1.
STAMP_SIZE = (8.5, 10.0)      # the export header, 9pt
STAMP_BAND = (0.30, 0.52)     # ... between these fractions of the page height

# The subsetter copied the Private Use Area codepoint into the glyph name, and
# a space is a glyph like any other, so a space comes back as U+E000. Both
# stamp words keep it ("Pagina ", "di ") and neither number does: on a
# garbled page that is what tells the two numbers from the separator.
SUBSET_SPACE = ""

STAMP_WORD = re.compile(r"(?i)pagina[\s.]*")
STAMP_NUM = re.compile(r"\d+")

# A table that would lose this many trailing pages is a detection failure, not
# trailing junk, and is left alone with a note.
MAX_TRAILING_PAGES = 25


def stamp_tokens(chunk):
    """The "Pagina N di X" stamp carried by one text chunk, or None.

    Returns (word, first, second): the word and the two numbers in the order
    pypdf hands them over. Which of the two numbers is the total is settled
    per table in stamp_total(), because the rotation reverses them from one
    page to the next -- 2025 vol. I p70 reads "Pagina 1 di 537" and p611
    "Pagina 5 5di".

    Two cases, and only the first gives readable numbers:

      read      the word is there in clear, so the digits are picked out of
                the three tokens that follow it.
      garbled   the stamp is set in one of the subsetted fonts with no
                ToUnicode (see is_garbled), so even "Pagina" comes back as
                Private Use Area characters and the numbers cannot be read at
                all. The strings are still stable, though: the subsetter gave
                one PUA codepoint per character, so two pages of the same
                document agree character for character on the total and a page
                from another document does not. Comparing them is enough --
                this is what recovers 2025 E (221 garbled pages) and F1.
    """
    tokens = chunk.split()
    for i, tok in enumerate(tokens):
        if STAMP_WORD.fullmatch(tok):
            numbers = []
            for after in tokens[i + 1:i + 4]:
                numbers += STAMP_NUM.findall(after)
            if len(numbers) == 2:
                return tok, numbers[0], numbers[1]
            return None
    # Garbled: "Pagina ", "di ", then the two numbers, then the table code.
    # The word and the separator are the two runs that end in the space
    # glyph; the numbers are the short ones that do not.
    if (len(tokens) >= 4
            and 5 <= len(tokens[0]) <= 8 and tokens[0].endswith(SUBSET_SPACE)
            and 2 <= len(tokens[1]) <= 3 and tokens[1].endswith(SUBSET_SPACE)
            and SUBSET_SPACE not in tokens[2] and len(tokens[2]) <= 4
            and SUBSET_SPACE not in tokens[3] and len(tokens[3]) <= 4):
        return tokens[0], tokens[2], tokens[3]
    return None


def read_page(page):
    """(text, stamp) for one page, from a single extraction pass.

    scan_headers needs the page text anyway and the visitor costs nothing
    measurable against it (90.6s against 91.8s for 2025 vol. I, 1048 pages).
    """
    found = []
    try:
        height = float(page.mediabox.height)
    except Exception:
        height = 841.0

    def visit(text, cm, tm, font, size):
        chunk = text.strip()
        if not chunk or not size or not STAMP_SIZE[0] <= size <= STAMP_SIZE[1]:
            return
        if not STAMP_BAND[0] * height < tm[5] < STAMP_BAND[1] * height:
            return
        stamp = stamp_tokens(chunk)
        if stamp:
            found.append(stamp)

    return page.extract_text(visitor_text=visit) or "", (found[0] if found else None)


def stamp_total(stamps, start, end):
    """The table's own page total, as (which of the two numbers, its token).

    The total is the number that stays the same from page to page, the page
    number is the one that changes, so the two are told apart by looking at
    every stamped page of the table rather than by trusting the order. With a
    single stamped page there is nothing to compare and None is returned: the
    stamp is not corroborated and nothing is trimmed on its word.

    Reading the numbers is only possible when they are not garbled, so the
    "first page is page 1" gate below is skipped for a garbled stamp. That is
    not a hole in the argument: what the trim compares is the total, and the
    total is read off the first page whatever the digits look like.
    """
    pages = [stamps[p] for p in range(start, end + 1) if p in stamps]
    if len(pages) < 2:
        return None
    if len({s[1] for s in pages}) == 1 and len({s[2] for s in pages}) > 1:
        return 1, stamps[start][1]
    return 2, stamps[start][2]


def trim_trailing(start, end, stamps):
    """Drop the pages a table was pasted with but does not own.

    The header scan cannot see them: 2025 E ends with a separate one-page
    document ("Pagina 1 di 1") and F1 with two blank ones ("PAGINA BIANCA"),
    and neither carries a table code.

    The stamp settles it. If the first page reads "Pagina 1 di X", the final
    page must read "Pagina Y di X"; a different total, or no stamp at all,
    means the page was pasted from elsewhere, and everything after the last
    page that still agrees is dropped. Y is normally X but need not be: F1 is
    the first half of table F, which is split across the two volumes of 2025,
    so it ends at "Pagina 8 di 12" and the totals are only compared, never
    the page numbers.

    Dropping pages here loses nothing from the split. They are still in the
    volume and still inside the next table's span, so they are written with
    that table; this only shortens one output file.

    Returns (end, trimmed, note).
    """
    if start not in stamps:
        return end, 0, ""
    total = stamp_total(stamps, start, end)
    if total is None:
        return end, 0, "stamp on one page only, not corroborated"
    slot, wanted = total
    first = stamps[start][3 - slot]
    if first.isdigit() and first != "1":
        return end, 0, f"first page reads {first} of {wanted}, not 1"

    keep = end
    while keep > start and not (keep in stamps and stamps[keep][slot] == wanted):
        keep -= 1
    if keep == end:
        return end, 0, ""
    if end - keep > MAX_TRAILING_PAGES:
        return end, 0, f"{end - keep} trailing pages, over the cap"

    # A dropped page whose document carries on past this table is not junk: it
    # is the first page of a table no detector found, and trimming it would
    # take those pages out of the split altogether, because no table claims
    # them. Left alone, with a note, for a human to look at.
    dropped = [p for p in range(keep + 1, end + 1) if p in stamps]
    beyond = {stamps[p][slot] for p in stamps if p > end}
    for page in dropped:
        if stamps[page][slot] in beyond:
            return end, 0, f"page {page} starts a table no detector found"
    return keep, end - keep, ""


# ==========================================================================
# text-corruption classes
# ==========================================================================

def is_garbled(text):
    """True if the page text is mostly Private Use Area glyphs.

    These pages come from subset fonts with no ToUnicode CMap. The subsetter
    copied each PUA codepoint into the glyph name -- verified with fontTools,
    the CharStrings of PKETUD+YgnwsfTimesNewRoman,Bold are literally
    ['.notdef', 'uniE019', 'uniE026', ...], i.e. U+E019 renamed. No original
    character information survives, only outlines. Confirmed unrecoverable by
    pdftotext (identical output), pdffonts (uni=no) and fontTools.

    Measured: 2023 vol. I 284pp, 2025 vol. I 356pp, 2018 vol. II 299pp,
    2025 vol. II 141pp, 2020 vol. I 174pp. Digitally rendered, not scanned, so
    OCR is the only route. The split still works: it needs page boundaries.
    """
    if not text:
        return True
    legible = sum(1 for c in text if c.isalnum())
    return len(PUA_RE.findall(text)) > 0.15 * max(1, legible)


def is_ciphered(text):
    """True if the page text is a shifted alphabet rather than real text.

    2022 vol. I from p938 on is emitted by /Identity-H fonts (2-byte CIDs)
    with no ToUnicode AND no /Differences, so pypdf returns a substituted
    alphabet: "/LFHQ]D 2SHUDWRUH" where the header reads "TABELLA N1 PER
    OPERATORE". There is no mapping table anywhere in the PDF, only glyph
    outlines, so this is not reversible either -- worse than the PUA case,
    since the character count is intact and it reads as plausible text.

    Measured: 2017 vol. I 287pp, 2022 vol. I 66pp, 2016 vol. I 28pp,
    2018 vol. II 7pp.
    """
    if not text:
        return False
    ctrl = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    letters = sum(1 for c in text if c.isalpha())
    if letters < 40:
        return False
    if ctrl > 0.05 * letters:
        return True
    vowels = sum(1 for c in text.lower() if c in "aeiou")
    return vowels < 0.08 * letters


def normalise_digit_one(code):
    """Fold a capital I used as a digit one back into "1".

    The 2012 index prints "Tabella DI" and "Tabella Gl" where the real codes
    are D1 and G1. Without this, D and D1 are both detected and the split
    carries a phantom table.
    """
    if len(code) == 2 and code[1] == "I":
        return code[0] + "1"
    return code


# ==========================================================================
# per-page facts used by the cross-volume stitch
# ==========================================================================

def is_blank(lines):
    """True for a separator page: no text, or the Italian "PAGINA BIANCA"."""
    if not lines:
        return True
    return len(lines) == 1 and "BIANCA" in lines[0].upper()


def trim_trailing_blanks(info, facts):
    """Pull a span's end back off the blank leaves closing the volume.

    The last table of a volume runs to the last physical page, which is
    usually a "PAGINA BIANCA" separator: two of them close 2025 vol. I. They
    belong to no table, and leaving them attached also hides the cut that
    continuation_pages looks for. Never trims past the table's own start.
    """
    while info["end"] > info["start"] and facts["blank"].get(info["end"]):
        info["end"] -= 1
    info["pages"] = info["end"] - info["start"] + 1


def printed_numbers(lines):
    """Page numbers printed in the footer, as a SET of candidates.

    This is the one thing readable on a garbled page: the digits are drawn in
    a font whose ToUnicode survived even where the letters were subsetted into
    the Private Use Area, so pypdf returns "– 1103 –" on pages whose text is
    otherwise unreadable. Verified on 2025, where all 70 pages of Tabella F1
    report their number -- the 62 of them in vol. II being garbled beyond
    reading.

    A page can carry more than one. 2021 tom. II prints the volume-local
    number *and* the volume-wide one, so the result is a set and callers test
    the relation they need rather than picking a value. Lines without a dash
    are ignored: a bare "2015" is a year in a title rather than a page
    number, and the 2025 index prints "– III –" in roman numerals.
    """
    found = set()
    for line in lines:
        match = PAGE_NUMBER_RE.match(line)
        if match and any(c in "-–—" for c in line):
            found.add(int(match.group(1)))
    return found


def header_fingerprint(lines):
    """A running-header signature, comparable across volumes.

    Every page of a table repeats the table's title, so the first line of the
    text identifies the group; consecutive different tables differ. On garbled
    pages the letters are PUA, but each PUA codepoint stands for exactly one
    original character, so two pages carrying the same header produce
    byte-identical strings -- including across volumes, which is what the
    continuation check compares. Confirmed on 2025: the F1 pages in vol. I
    (p1040-1046) and in vol. II (p5-64) share one fingerprint, F2's does not.

    Digits are stripped, because a running header usually ends with the page
    number, which changes on every page.
    """
    if not lines:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"\d+", " ", lines[0])).strip()[:80]


# ==========================================================================
# index / vocabulary
# ==========================================================================

def is_index_page(lines):
    """True for ELENCO TABELLE SEGNALAZIONI listing pages.

    The marker is honoured only in the top 12 lines. On 2025 vol. II the table
    pages ALSO print "SEGNALAZIONI" in a trailing header block, so a plain
    substring test discards every table in the file.
    """
    return any("SEGNALAZIONI" in line for line in lines[:12])


def read_index(reader):
    """Harvest {code: page_count} from an ELENCO page, if the volume has one.

    Two layouts are recognised: the "Cartella / Pagine / Totale Pagine" table
    of 2020 and 2022, and the plain "Tabella D <title>" listing of 2012.
    Returns (vocabulary, page_counts).
    """
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        counts, codes = {}, set()
        for line in text.split("\n"):
            m = INDEX_ROW.match(line)
            if m:
                counts[m.group(2)] = int(m.group(1))
                codes.add(m.group(2))
                continue
            m = LEADING_CODE.match(line.strip())
            if m:
                codes.add(normalise_digit_one(m.group(1).upper()))
        # A real index lists many codes and nothing else. 2012 vol. III and
        # 2014 vol. I surface a single spurious code this way and are rejected
        # by requiring eight.
        if len(codes) >= 8:
            return codes, counts
    return set(), {}


def detect_family(reader, vocabulary):
    """1 = art.27 double-letter, 2 = A1..P2, 3 = art.27 single-letter.

    Informational for reporting, plus it selects which header styles to try.
    Detection does not depend on it being correct.
    """
    # A vocabulary dominated by A1..P2 codes is family 2. D, E, I, L and M are
    # excluded from the test because they are also art.27 single-letter codes,
    # which a family 1 index can legitimately contain.
    f2 = (vocabulary & FAMILY2_CODES) - {"D", "E", "I", "L", "M"}
    if len(f2) >= 4:
        return 2
    # A small index of single-letter art.27 codes is family 3 (2012 vol. I).
    # Tested before the sample scan below, which sees "Tabella D" in body
    # pages and would otherwise call it family 2.
    if vocabulary and len(vocabulary) >= 8:
        shaped = [c for c in vocabulary if re.fullmatch(r"[A-Z]\d?", c)]
        singles = [c for c in shaped if re.fullmatch(r"[A-Z]", c)]
        if len(shaped) >= 6 and len(singles) >= 4:
            return 3
    # No usable index: sample pages and look at the code shape.
    letters = digits = 0
    step = max(1, len(reader.pages) // 25)
    for i in range(0, len(reader.pages), step):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            continue
        for m in INLINE_CODE.finditer(text):
            cand = m.group(1)
            if cand in FAMILY2_CODES:
                return 2
            if re.fullmatch(r"[A-Z]", cand):
                letters += 1
            elif re.fullmatch(r"[A-Z]\d", cand):
                digits += 1
    if letters and not digits:
        return 3
    return 1


# ==========================================================================
# detectors
# ==========================================================================

def page_code(lines, text, vocabulary, family):
    """Detect the table code printed on one page. Returns (code, style).

    style 1  bare code on the line after "Operazioni disciplinate" (the MEF
             block header, families 1 and 3 from 2016 vol. II onwards)
    style 2  a line that is exactly "Tabella AA"
    style 3  inline "TAB A1" in a running header (families 2, and 1/3 too)
    style 4  leading "Tabella D" against the index vocabulary (family 3)
    """
    # Style 4: family 3 only, where the index is the sole reliable source
    # because those codes appear on the index page and nowhere in the body.
    if family == 3 and vocabulary:
        for line in lines[:6]:
            m = LEADING_CODE.match(line)
            if m:
                return normalise_digit_one(m.group(1).upper()), 4

    if family != 2:
        for j, line in enumerate(lines[:14]):
            if "Operazioni disciplinate" in line:
                for k in range(j + 1, min(j + 4, len(lines))):
                    if CODE_RE.match(lines[k]):
                        return lines[k], 1
                break

        for line in lines[:8]:
            m = LEADING_CODE.match(line)
            if m:
                return m.group(1).upper(), 2

    # Style 3: deliberately unbounded. A positional window cannot separate a
    # running header from a prose cross-reference -- 8 lines lost 24 of 28
    # tables in 2019 vol. I, 24 lines invented KK1 on 2025 vol. II p263 ("come
    # da elencazione sintetica della tabella KK1"), and every threshold in
    # between was wrong somewhere. Callers disambiguate by frequency instead.
    for line in lines:
        for m in INLINE_CODE.finditer(line):
            cand = m.group(1).upper()
            if cand not in FAMILY2_CODES:
                if family == 2:
                    continue
                if vocabulary and cand not in vocabulary:
                    continue
                if not vocabulary and cand in PROSE_STOPLIST:
                    continue
            return cand, 3
    return None, None


def scan_bookmarks(reader):
    """{code: first page} from the PDF bookmark tree.

    Only 5 of 43 volumes carry usable table bookmarks: 2021 tom. I (31),
    2023 vol. I (31), 2023 vol. II (34), 2025 vol. I (16), 2025 vol. II (50).
    2016 vol. II, 2018 vol. I and 2023 vol. III have outlines but only
    "Pagina vuota" and annex titles.

    Named destinations are NOT usable: 2020 vol. II (522), 2021 vol. II (570)
    and 2023 vol. II (1689) carry JR_PAGE_ANCHOR_* entries, which are
    JasperReports hyperlink anchors, and pypdf resolves most to None.
    """
    found = {}

    def walk(items):
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:
                # container entries ('araba.pdf', '0001.pdf') carry no page
                continue
            m = TITLE_RE.search(getattr(item, "title", "") or "")
            if m and page:
                found.setdefault(m.group(1).upper(), page)

    try:
        outline = reader.outline
    except Exception:
        return found
    if outline:
        walk(outline)
    return found


def scan_headers(reader, vocabulary, family, stamps=None):
    """{code: first page}, {code: [pages]}, per-page facts, corruption tallies.

    `stamps`, when given, is filled with {page: "Pagina N di X"} for the pages
    that carry one, in the same pass. The pass reads every page of the volume
    anyway and read_page() costs no more than the plain extraction it
    replaces, so this is the only free place to collect them.
    """
    starts, hits = {}, {}
    unassigned, garbled, ciphered = [], [], []
    weak, styles = {}, {}
    blank, numbers, fingerprints = {}, {}, {}
    for i, page in enumerate(reader.pages):
        page_no = i + 1
        try:
            text, stamp = read_page(page)
        except Exception:
            unassigned.append(page_no)
            continue
        if stamps is not None and stamp:
            stamps[page_no] = stamp
        if is_garbled(text):
            garbled.append(page_no)
        elif is_ciphered(text):
            ciphered.append(page_no)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        # Recorded before the index/blank early-exits below: the stitch needs
        # to know which pages are numbered content even when they carry no
        # table code at all, which is exactly the leading region of a volume
        # that continues the previous one.
        blank[page_no] = is_blank(lines)
        numbers[page_no] = printed_numbers(lines)
        fingerprints[page_no] = header_fingerprint(lines)
        if not lines or is_index_page(lines):
            continue

        code, style = page_code(lines, text, vocabulary, family)
        if not code:
            unassigned.append(page_no)
            continue

        if code not in hits:
            hits[code] = []
            starts[code] = page_no
            styles[code] = style
            # Style 1 also matches summary pages: 2023 vol. II p8 carries a
            # bare "UE" under the same header and would steal the start of the
            # real Tabella UE at p543. Flag it provisional; a later
            # banner-backed page clears the flag.
            if style == 1 and "ELENCO TABELLE" not in text:
                weak[code] = page_no
        hits[code].append(page_no)
    return (starts, hits, weak, styles, unassigned, garbled, ciphered,
            {"blank": blank, "numbers": numbers, "fingerprints": fingerprints})


def body_starts(reader, vocabulary):
    """First page per table, from the table title printed in the body.

    Last-resort detector. Keys on the title rather than the code because in
    2012 vol. I the single-letter codes appear only on the index page, while
    the title repeats on every page of the table.
    """
    starts = {}
    if not vocabulary:
        return starts
    norm = {normalise_digit_one(c) for c in vocabulary}
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if is_garbled(text) or is_ciphered(text):
            continue
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines or is_index_page(lines):
            continue
        for j, line in enumerate(lines[:6]):
            m = LEADING_CODE.match(line)
            if not m:
                continue
            code = normalise_digit_one(m.group(1).upper())
            nxt = lines[j + 1] if j + 1 < len(lines) else ""
            # Require a title on the next line, so a bare "Tabella" column
            # header inside a table body is not taken for a table start.
            if nxt and not LEADING_CODE.match(nxt) and len(nxt) > 12 \
                    and code in norm:
                starts.setdefault(code, i + 1)
            break
    return starts


def build_manifest(reader, vocabulary, counts):
    """Union every detector into a {code: {start, end, source}} manifest."""
    embedded = scan_bookmarks(reader)
    family = detect_family(reader, vocabulary)
    stamps = {}
    (header, hits, weak, styles, unassigned, garbled, ciphered,
     facts) = scan_headers(reader, vocabulary, family, stamps)

    # A single-page hit is a prose cross-reference, not a table -- but only for
    # style 3, which cannot tell a running header from "come da elencazione
    # della tabella KK1". Styles 1, 2 and 4 are positional and can legitimately
    # fire on a one-page table; filtering them too cost real tables (2020
    # vol. II went 41 -> 23).
    tables, singletons = {}, []
    for code, page in header.items():
        run = len(hits[code])
        if styles.get(code) == 3 and run < MIN_RUN:
            singletons.append(code)
            continue
        tables[code] = {
            "start": page, "source": "header", "run": run,
            "style": styles.get(code), "provisional": code in weak,
        }

    titles = body_starts(reader, vocabulary)
    for code, page in titles.items():
        tables.setdefault(code, {
            "start": page, "source": "title", "provisional": False,
        })

    conflicts = []
    for code, page in embedded.items():
        if code in tables and tables[code]["start"] != page:
            conflicts.append({
                "code": code, "embedded": page,
                "header": tables[code]["start"],
                "run": tables[code].get("run", 1),
                "resolved_from": "singleton" if code in singletons else "header",
            })
        # the bookmark is human-authored, so it always wins
        tables[code] = {"start": page, "source": "embedded", "provisional": False}

    # Ends are derived from the next start: every table run is contiguous.
    # Verified 28/28 codes on 2019 vol. I with zero interruptions.
    ordered = sorted(tables.items(), key=lambda kv: kv[1]["start"])
    last_page = len(reader.pages)
    trimmed = {}
    for idx, (code, info) in enumerate(ordered):
        end = ordered[idx + 1][1]["start"] - 1 if idx + 1 < len(ordered) else last_page
        info["end"] = max(info["start"], end)
        info["end"], dropped, note = trim_trailing(info["start"], info["end"],
                                                   stamps)
        if dropped:
            trimmed[code] = dropped
        if note:
            info["stamp_note"] = note
        info["pages"] = info["end"] - info["start"] + 1
        if code in counts:
            info["index_pages"] = counts[code]
            info["index_agrees"] = counts[code] == info["pages"]

    # The final run otherwise ends on the last physical page, which in most
    # volumes is a blank separator (2025 vol. I: p1047-1048 are "PAGINA
    # BIANCA"). Those pages belong to no table, and leaving them attached
    # would also make the cut that continuation_pages looks for invisible.
    # The index cross-check is redone here, having been computed against the
    # untrimmed span.
    if ordered:
        code, info = ordered[-1]
        trim_trailing_blanks(info, facts)
        if code in counts:
            info["index_agrees"] = counts[code] == info["pages"]

    return {
        "family": family,
        "page_count": last_page,
        "vocabulary": sorted(vocabulary),
        "index_counts": counts,
        "tables": dict(ordered),
        "conflicts": conflicts,
        "singletons": sorted(singletons),
        "unassigned_pages": len(unassigned),
        "garbled_pages": len(garbled),
        "ciphered_pages": len(ciphered),
        "stamped_pages": len(stamps),
        "trimmed": trimmed,
    }, facts


# ==========================================================================
# tables that cross a volume boundary
# ==========================================================================

def stitch(prev_manifest, prev_facts, nxt_manifest, nxt_facts,
           prev_name, nxt_name):
    """Record, on `nxt_manifest`, the pages it inherits from `prev_manifest`.

    Volumes of a year are consecutive parts of one document, and a table may
    straddle the join: 2025 Tabella F1 is printed pages 1035-1042 in volume I
    and 1043-1104 in volume II, seventy pages in all, its margin carrying
    "Tabella F1 / Pagina 70 di 70" on the last one.

    Because the split is per volume, the volume holding the tail used to write
    its file over the one holding the head, and only the tail survived. Both
    halves now go into a single Out/PDF/<code>/<code><year>.PDF.
    """
    found = continuation_pages(prev_manifest, prev_facts, nxt_manifest, nxt_facts)
    if not found:
        return None
    code, start, end = found
    head = prev_manifest["tables"][code]
    nxt_manifest.setdefault("continued", {})[code] = {
        "from_volume": prev_name,
        "from_start": head["start"],
        "from_end": head["end"],
        "start": start,
        "end": end,
        "pages": end - start + 1,
        "total_pages": head["pages"] + (end - start + 1),
    }
    return code


def last_numbered_page(facts):
    """The last page of a volume that carries a printed page number.

    Blanks and the cover/index pages have none, so this is where the real
    content stops: 1046 on 2025 vol. I (1048 physical pages), 1046 on
    2023 vol. I. It is also what "the table runs to the end of the volume"
    means, as opposed to running to the last physical page.
    """
    numbered = [p for p, nums in facts["numbers"].items() if nums]
    return max(numbered) if numbered else None


def first_numbered_page(facts):
    """The first page of a volume that carries a printed page number."""
    numbered = [p for p, nums in facts["numbers"].items() if nums]
    return min(numbered) if numbered else None


# How much a detection is trusted when one table code turns up in two volumes
# of the same year and only one copy can be kept. The bookmark tree is
# human-authored, a run of header hits is the detector's own evidence, and a
# title repeat is the weakest of the three.
SOURCE_RANK = {"embedded": 3, "header": 2, "title": 1}


def runs_to_the_end(volume, info):
    """True if a span reaches the last numbered page of its own volume."""
    end = last_numbered_page(volume["facts"])
    return bool(end and info["end"] >= end)


def halves_join(volumes, earlier, later):
    """True if one detected span continues straight into the next.

    Consecutive means exactly that: the volumes are neighbours, the earlier
    copy runs to the last numbered page of its own volume, and the later one
    starts no later than the first numbered page of its own.
    """
    i, info = earlier
    j, other = later
    if j != i + 1:
        return False
    end = last_numbered_page(volumes[i]["facts"])
    start = first_numbered_page(volumes[j]["facts"])
    return bool(end and start
                and info["end"] >= end and other["start"] <= start)


def resolve_repeats(volumes):
    """Settle a table code that was detected in more than one volume.

    One code means one file, so a repeat has to be resolved rather than
    concatenated. Three cases are real in the archive and only the third is a
    split:

      * **the same table printed twice.** 2021 tom. I and tom. II both carry
        the MAE tables: A1 spans 295 pages in each, down to the page, and the
        opening pages are identical. One copy belongs in the output.
      * **two different tables sharing a code.** 2021 tom. II and VOL. II both
        detect an M1, 8 and 13 pages of different content. Neither is the
        other's continuation and concatenating them would invent a table, so
        the better-attested copy is kept and the other reported.
      * **one table split across the join, both halves readable.** The halves
        are consecutive by construction, and only then are both kept.

    The losers are marked `kept = False` for the writer to skip.
    """
    by_code = {}
    for idx, volume in enumerate(volumes):
        for code in volume["manifest"]["tables"]:
            by_code.setdefault(code, []).append(idx)

    for code, holders in by_code.items():
        if len(holders) < 2:
            continue
        copies = [(i, volumes[i]["manifest"]["tables"][code]) for i in holders]
        if all(halves_join(volumes, a, b) for a, b in zip(copies, copies[1:])):
            continue
        # Keep the best-attested copy. A human-authored bookmark outranks anything
        # the header scan inferred, the same principle the per-volume detectors
        # already follow. Between two header-scan copies, prefer the one that
        # does not run to the end of its volume: that is the shape an
        # over-extension takes (2016 and 2019 vol. I each read a body line as
        # "M1" and swallowed the rest of the volume with it, while vol. II
        # holds the real 10-page M1). Then the index cross-check, then reading
        # order.
        best = max(copies, key=lambda pair: (
            SOURCE_RANK.get(pair[1]["source"], 0),
            not runs_to_the_end(volumes[pair[0]], pair[1]),
            bool(pair[1].get("index_agrees")),
            -pair[0],
        ))
        for idx, info in copies:
            if idx == best[0]:
                info["kept"] = True
                continue
            info["kept"] = False
            volumes[idx]["manifest"].setdefault("dropped", {})[code] = {
                "volume": volumes[idx]["label"],
                "start": info["start"],
                "end": info["end"],
                "pages": info["pages"],
                "kept_from": volumes[best[0]]["label"],
                "reason": "stesso codice in più volumi, non uno spezzamento",
            }


def number_joins(here, there):
    """True if some printed number in `there` follows one in `here` by one.

    Volume N ends at printed page 1042 and volume N+1 opens at 1043, so the
    two volumes are one document and a table cut at the join continues. When
    a volume restarts its numbering (2019: vol. I ends at 824, vol. II opens at
    1) nothing joins and no continuation is claimed.

    A page may offer several candidates -- 2021 tom. II prints the volume
    local number and the volume-wide one -- so any pairing is accepted rather
    than a single guess.
    """
    return any(b == a + 1 for a in here for b in there)


def continuation_pages(prev_manifest, prev_facts, nxt_manifest, nxt_facts):
    """Pages of `nxt` that carry on the table cut at the end of `prev`.

    Returns (code, first_page, last_page) or None.

    A table is treated as cut when it reaches the last numbered page of its
    volume: nothing else claims those pages, so the run is not complete and
    the next volume must be holding the rest. The claim is then made
    conservative, because gluing the wrong pages in is worse than missing a
    join, and the next volume opening on something else is the normal case --
    2023 vol. III starts the Agenzia delle Dogane relations, where vol. II
    ended on Tabella UE. Three tests have to agree:

      * the next volume must open with numbered pages of its own, before its
        first table, so there is somewhere to continue into;
      * those pages must carry the printed numbers the join implies, each the
        next integer of the last (number_joins, then consecutive to the end of
        the run). The volumes are one document, numbered continuously, so a
        run that continues shows it;
      * their running header must be the one the cut table ended on, read
        from the previous volume. This is the test that says "same table" and
        it is what rejects 2023: Dogane pages do not carry the MEF header
        that UE's tail carries.

    The last few pages of a table are allowed to look different -- the
    "Totale autorizzazioni" sheet of 2025 F1, printed 1104, opens with its
    own line and is still F1, its margin reading "Tabella F1 / Pagina 70 di
    70". Hence TAIL_PAGES rather than a strict run of equal headers.
    """
    tables = prev_manifest["tables"]
    if not tables:
        return None
    code, info = max(tables.items(), key=lambda kv: kv[1]["start"])
    prev_last = last_numbered_page(prev_facts)
    if prev_last is None or info["end"] < prev_last:
        return None

    first = first_numbered_page(nxt_facts)
    if first is None or not nxt_manifest["tables"]:
        return None
    nxt_start = min(i["start"] for i in nxt_manifest["tables"].values())
    if first >= nxt_start:
        return None

    # The running header the cut table ends on, read from the previous volume
    # so that a header repeated by an unrelated page cannot pose as the match.
    tail = Counter(
        prev_facts["fingerprints"][p]
        for p in range(max(info["start"], info["end"] - 7), info["end"] + 1)
        if prev_facts["fingerprints"][p]
    ).most_common(1)
    if not tail:
        # No readable header on the tail pages, so the header test cannot
        # vouch for anything and nothing is glued.
        return None
    expected = tail[0][0]

    # Printed numbers, in two steps. First the join: the next volume's first
    # numbered page must be the successor of the previous volume's last one
    # (on 2025, printed 1042 then 1043 -- the two volumes are one document).
    # Then, within the next volume, the numbers must run consecutively, which
    # is what a continuation looks like and what a new section cannot fake: a
    # page whose footer is unreadable ends the run rather than letting it
    # bridge a gap. A page that prints two numbers (2021 tom. II carries the
    # volume-local one alongside) advances if either of them does.
    prev_numbers = prev_facts["numbers"][prev_last]
    if not number_joins(prev_numbers, nxt_facts["numbers"][first]):
        return None

    run, seen = [], None
    for p in range(first, nxt_start):
        numbers = nxt_facts["numbers"].get(p) or set()
        if not numbers or (seen and not numbers & {n + 1 for n in seen}):
            break
        seen = numbers
        run.append(p)
    if not run or nxt_facts["fingerprints"].get(run[0]) != expected:
        return None

    # The run may stop early on a header change; the last TAIL_PAGES pages of
    # it are kept regardless, since a table often ends differently shaped.
    matched = 0
    for idx, p in enumerate(run):
        if nxt_facts["fingerprints"].get(p) != expected:
            break
        matched = idx
    last = run[min(matched + TAIL_PAGES, len(run) - 1)]
    return code, first, last


# ==========================================================================
# output
# ==========================================================================

def split_pdf(readers, manifests, out_root, year):
    """Write one PDF per table under <out_root>/PDF/<tabella>/<tabella><anno>.PDF.

    The directory is named for the table CODE, not the source volume, so
    Out/PDF/AA/AA2023.PDF holds Tabella AA from whichever volume carried it.
    The year is a filename suffix rather than a directory level, so one
    folder per table holds one file per reporting year.

    `readers` and `manifests` are parallel sequences, one entry per volume of
    the year. A table is written once, from the volume that detected it; when
    it continues into the next volume, that volume's manifest carries the
    leading pages under "continued", and they are appended here so both halves
    land in the same file. Previously each volume wrote its own file over the
    same name, and whichever ran last won: 2025 F1 lost the 8 pages of vol. I.
    """
    # code -> list of (reader, first, last), in volume order. A copy of a table
    # resolved away by resolve_repeats is skipped: one code, one file.
    plan = {}
    for reader, manifest in zip(readers, manifests):
        for code, info in manifest["tables"].items():
            if info.get("kept") is False:
                continue
            plan.setdefault(code, []).append((reader, info["start"], info["end"]))
        for code, cont in manifest.get("continued", {}).items():
            plan.setdefault(code, []).append((reader, cont["start"], cont["end"]))

    written = []
    for code, segments in plan.items():
        writer = PdfWriter()
        for reader, start, end in segments:
            for p in range(start, end + 1):
                writer.add_page(reader.pages[p - 1])
        folder = Path(out_root, OUT_DIR, "PDF", code)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{code}{year}.PDF"
        with open(path, "wb") as fh:
            writer.write(fh)
        written.append(str(path))
    return written


def report(manifest, label):
    n = len(manifest["tables"])
    print(f"{label:46} family={manifest['family']} "
          f"pages={manifest['page_count']:5} tables={n:3} "
          f"{'OK' if n else 'NO-TABLES'}")
    print(f"    codes      : {' '.join(manifest['tables']) or '-'}")
    if manifest["vocabulary"]:
        print(f"    index      : {len(manifest['vocabulary'])} codes "
              f"{' '.join(manifest['vocabulary'])}")
    if manifest["conflicts"]:
        print(f"    conflicts  : {len(manifest['conflicts'])}, resolved to bookmark")
        for c in manifest["conflicts"][:6]:
            print(f"        {c['code']:5} bookmark p{c['embedded']:<5} "
                  f"header p{c['header']:<5} run={c['run']} "
                  f"({c['resolved_from']})")
    prov = [c for c, i in manifest["tables"].items() if i["provisional"]]
    if prov:
        print(f"    provisional: {' '.join(prov)}")
    if manifest["garbled_pages"]:
        print(f"    garbled    : {manifest['garbled_pages']} pages, text "
              f"unrecoverable (no ToUnicode; step 2 extracts nothing)")
    if manifest["ciphered_pages"]:
        print(f"    ciphered   : {manifest['ciphered_pages']} pages, text "
              f"unrecoverable (Identity-H, no encoding table)")
    if manifest["stamped_pages"]:
        print(f"    stamp      : 'Pagina N di X' on "
              f"{manifest['stamped_pages']}/{manifest['page_count']} pages")
    if manifest["trimmed"]:
        detail = ", ".join(f"{c} -{n}p" for c, n in
                           sorted(manifest["trimmed"].items()))
        print(f"    stripped   : {len(manifest['trimmed'])} trailing pages "
              f"removed ({detail})")
    notes = [(c, i["stamp_note"]) for c, i in manifest["tables"].items()
             if i.get("stamp_note")]
    for code, note in notes[:6]:
        print(f"    stamp note : {code:5} {note}")
    checked = [i for i in manifest["tables"].values() if "index_agrees" in i]
    if checked:
        agree = sum(1 for i in checked if i["index_agrees"])
        mism = [(c, i["pages"], i["index_pages"]) for c, i in
                manifest["tables"].items() if "index_agrees" in i
                and not i["index_agrees"]]
        print(f"    index check: {agree}/{len(checked)} spans agree with the "
              f"index page counts, {len(mism)} mismatch {mism[:4]}")
    for code, cont in manifest.get("continued", {}).items():
        print(f"    continued  : {code} carries on from {cont['from_volume']} "
              f"p{cont['from_end']}, here p{cont['start']}-{cont['end']} "
              f"({cont['pages']} pages) -> one PDF of {cont['total_pages']}")
    dropped = manifest.get("dropped", {})
    if dropped:
        print(f"    duplicate  : {len(dropped)} code(s) found in another volume "
              f"of the same year and not a split; kept copy wins")
        for code, info in list(dropped.items())[:8]:
            print(f"        {code:5} p{info['start']}-{info['end']} "
                  f"({info['pages']} pp) dropped, kept from {info['kept_from']}")


# ==========================================================================
# CLI
# ==========================================================================

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


def parse_volume(stem):
    """Volume number from a report filename.

        lxvii_3_volume 1_442452.pdf        -> 1   (2024, arabic)
        2019_LXVII_n3_VOLUME_I.pdf         -> 1   (roman)
        2021_LXVII_n5_TOMO_II.pdf          -> 2   (TOMO as VOLUME)
        2001_LXVII_n1_DOCUMENTO_UNICO.pdf  -> 1   (single volume)
    """
    m = re.search(r"(?:volume|tomo)[\s_-]*([IVX]+|\d+)", stem, re.IGNORECASE)
    if m:
        token = m.group(1).upper()
        return int(token) if token.isdigit() else ROMAN.get(token)
    if re.search(r"documento[\s_-]*unico", stem, re.IGNORECASE):
        return 1
    return None


def report_year(report_path):
    """Reporting year from the reports_185_1990/<anno>/ path."""
    m = re.search(rf"{REPORTS_DIR}[/\\](\d{{4}})", str(report_path))
    if m:
        return m.group(1)
    # Outside that layout -- an absolute path, a copy renamed -- fall back to
    # the filename, which the archive prefixes with the reference year because
    # doc numbering restarts each legislature.
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", Path(str(report_path)).stem)
    if m:
        return m.group(1)
    raise ValueError(
        f"Anno non trovato in '{report_path}': il percorso deve essere "
        f"'{REPORTS_DIR}/<anno>/<file>.pdf'"
    )


def detect_year(reports_dir=REPORTS_DIR):
    """If the folder contains exactly one year, use it."""
    years = sorted(
        d.name for d in Path(reports_dir).glob("*")
        if d.is_dir() and d.name.isdigit()
    )
    if len(years) == 1:
        return years[0]
    if not years:
        return None
    raise SystemExit(
        f"Più anni presenti in {reports_dir}/ ({', '.join(years)}): specifica --year"
    )


def find_report(year, volume, reports_dir=REPORTS_DIR):
    """The PDF for the requested volume inside reports_185_1990/<anno>/."""
    folder = Path(reports_dir, year)
    if not folder.is_dir():
        raise SystemExit(f"Cartella non trovata: {folder}")
    wanted = int(volume)
    matches = [p for p in sorted(folder.glob("*.pdf"))
               if parse_volume(p.stem) == wanted]
    if not matches:
        available = ", ".join(p.name for p in sorted(folder.glob("*.pdf"))) or "(nessun PDF)"
        raise SystemExit(
            f"Nessun PDF del volume {volume} in {folder}.\nTrovati: {available}"
        )
    if len(matches) > 1:
        raise SystemExit(
            f"Ambiguo: più PDF del volume {volume} in {folder}: "
            + ", ".join(p.name for p in matches)
            + "\nUsa --report per specificare il file."
        )
    return matches[0]


def collect_jobs(args):
    """Resolve the CLI flags to a list of (report_path, year) jobs."""
    reports_dir = Path(args.base, REPORTS_DIR)
    if args.report:
        return [(r, year_from_path(r)) for r in args.report]
    if args.all:
        jobs = []
        for folder in sorted(reports_dir.glob("*")):
            if not folder.is_dir() or not folder.name.isdigit():
                continue
            for pdf in sorted(folder.glob("*.pdf")):
                jobs.append((str(pdf), folder.name))
        return jobs

    year = args.year or detect_year(str(reports_dir))
    if not year:
        raise SystemExit(
            f"Nessun anno trovato in {reports_dir}/: usa --year"
        )
    volumes = ["1", "2"] if args.volume == "both" else [args.volume]
    return [(str(find_report(year, v, reports_dir)), year)
            for v in volumes]


def year_from_path(path):
    try:
        return report_year(path)
    except ValueError:
        return "?"


def group_by_year(jobs):
    """Regroup (report, year) jobs into {year: [report, ...]}, in volume order.

    The volumes of a year have to meet each other before any of them is
    written, because a table may run from one into the next. Order within the
    year is by parsed volume number, with the filename as the tie-break: that
    is what makes 2021 TOMO I -> TOMO II -> VOLUME II come out in reading
    order despite the colliding volume-2 names.

    A path that yields no year at all still gets a group, so those volumes are
    processed but never stitched: `main` refuses to join a boundary whose two
    halves cannot be attributed to the same year.
    """
    grouped = {}
    for report, year in jobs:
        grouped.setdefault(year, []).append(report)
    for reports in grouped.values():
        reports.sort(key=lambda p: (parse_volume(Path(p).stem) or 0, p))
    return grouped


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Estrae una tabella per PDF dalla relazione L.185/1990.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--year", help="anno di riferimento, es. 2024")
    parser.add_argument("--volume", default="2", choices=["1", "2", "both"],
                        help="quale volume processare (default: 2)")
    parser.add_argument("--report", action="append", default=[],
                        help="percorso esplicito del PDF (ripetibile)")
    parser.add_argument("--all", action="store_true",
                        help="processa ogni volume in reports_185_1990/")
    parser.add_argument("--base", default="", help="radice dei percorsi (default: .)")
    parser.add_argument("--out", default="",
                        help=f"radice di output (default: <base>/{OUT_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="stampa il report senza scrivere i PDF")
    parser.add_argument("--manifest", default="",
                        help="scrivi anche il manifest JSON in questo percorso")
    args = parser.parse_args(argv)

    jobs = group_by_year(collect_jobs(args))
    print("=" * 70)
    print(f"Reports2PDFTables — {sum(len(v) for v in jobs.values())} volume/i, "
          f"{len(jobs)} anno/i")
    print("=" * 70)

    total = 0
    for year, reports in jobs.items():
        # Every volume of the year is opened before any of them is written:
        # a table that runs past a volume boundary is only visible once the
        # next volume's first table is known, and the PDF is written once from
        # both halves. One entry per volume that opened, so the labels stay
        # aligned with their manifests if one of them does not.
        volumes = []
        for report in reports:
            label = Path(report).stem
            try:
                reader = PdfReader(report, strict=False)
            except Exception as exc:
                print(f"{label}: apertura fallita: {exc}\n")
                continue
            vocabulary, counts = read_index(reader)
            manifest, page_facts = build_manifest(reader, vocabulary, counts)
            volumes.append({"label": label, "path": report, "reader": reader,
                            "manifest": manifest, "facts": page_facts})

        for idx in range(len(volumes) - 1):
            if year == "?":
                print("    (anno non determinato: nessun join fra i volumi)")
                break
            stitch(volumes[idx]["manifest"], volumes[idx]["facts"],
                   volumes[idx + 1]["manifest"], volumes[idx + 1]["facts"],
                   volumes[idx]["label"], volumes[idx + 1]["label"])

        resolve_repeats(volumes)

        for volume in volumes:
            report_manifest(volume["manifest"], f"{volume['label']} (y={year})")
            total += len(volume["manifest"]["tables"])

        if args.manifest:
            target = Path(args.manifest)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as fh:
                for volume in volumes:
                    fh.write(json.dumps({"report": volume["path"], "year": year,
                                        **volume["manifest"]},
                                        ensure_ascii=False) + "\n")

        if not args.dry_run and any(v["manifest"]["tables"] for v in volumes):
            out_root = args.out or args.base or "."
            written = split_pdf([v["reader"] for v in volumes],
                                [v["manifest"] for v in volumes], out_root, year)
            print(f"    wrote      : {len(written)} PDF in "
                  f"{Path(out_root, OUT_DIR, 'PDF')}/<tabella>/<tabella>{year}.PDF")
        print()
        del volumes

    print("=" * 70)
    print(f"Totale: {total} tabelle in "
          f"{sum(len(v) for v in jobs.values())} volume/i")
    if args.dry_run:
        print("(dry-run: nessun PDF scritto)")


def report_manifest(manifest, label):
    report(manifest, label)


if __name__ == "__main__":
    main()