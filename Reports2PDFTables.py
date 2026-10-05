#!/usr/bin/env python3
"""
Reports2PDFTables — split a Law 185/1990 annual report volume into one PDF
per table.

    reports_185_1990/<anno>/<relazione>.pdf
        ->  Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF

The year is read from the reports_185_1990/<anno>/ path, so one script serves
every reporting year. With many years present, --year is required.

Usage:
    ./Reports2PDFTables.py --year 2024 --volume both
    ./Reports2PDFTables.py --year 2023 --volume 2
    ./Reports2PDFTables.py --report reports_185_1990/2023/2023_LXVII_n2_VOLUME_II.pdf
    ./Reports2PDFTables.py --all                 # every text-bearing volume
    ./Reports2PDFTables.py --year 2023 --dry-run # report only, write nothing

Requires: pypdf (pip install -r requirements.txt)

--------------------------------------------------------------------------
WHERE A TABLE BELONGS
--------------------------------------------------------------------------
The output is partitioned by the ministry that produced the table, because a
table code is only unique within (authority, article): in 2025 the MAE prints
"TAB M1" for Intermediazioni per Operatore and the Dogane print "TAB. M1" for
Esportazioni Definitive, and the previous code-only layout let one overwrite the
other. See lib/ontology.py for the markers and the article tokens.

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

A fourth, unnumbered kind sits outside all three: the DIFESA annessi, whose
running header is "MINISTERO DELLA DIFESA - Annesso 3A" and which carries no
table code at all. It is detected as style 5.

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

from lib import indice, ontology

REPORTS_DIR = "reports_185_1990"
OUT_DIR = "Out"

# Earliest reporting year in scope. Everything before this is ignored by --all.
#
# Not a quality judgement about the older volumes so much as a decision to stop
# carrying them: 2001-2010 are scanned images with no text layer, 2013 has no
# index, and 2012 has an index whose ministry rows carry no page numbers, so
# none of them can be placed by the method this pipeline now uses. Leaving them
# in the output would mean reporting "0 tables" as though it were a finding.
FIRST_YEAR = 2016

# Estensione dei PDF per-table. reports_185_1990/ contiene i PDF di origine in
# minuscolo, ma i PDF per-tabella vengono scritti in maiuscolo: su Linux i due
# casi non si equivalgono e IndividualTables2SQL.py cerca questo nome esatto.
PDF_EXT = ".PDF"

# Per-table PDFs are written as
# Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF, the uppercase extension
# included. IndividualTables2SQL.py walks the same tree with PDF_EXT there;
# change both together or step 2 finds nothing, since globbing is case-sensitive
# on Linux.


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

# art. 27 codes, families 1 and 3: one to three capitals plus up to two digits.
#
# Two digits, not one. The Dogane MG and MT series run to eighteen -- MG1..MG18
# under art. 11 comma 5-bis, MT1/MT7/MT13 under art. 10 quater -- and with a
# single-digit tail "TAB. MG10" matched nothing at all: the group takes MG1, the
# word boundary after it fails against the following 0, and the nine MG tables
# from MG10 to MG18 plus MT13 were invisible to every detector. Two digits keeps
# the year separable, since no code ends in four.
CODE_TAIL = r"[A-Z]{1,3}\d{0,2}"
CODE_RE = re.compile(rf"^{CODE_TAIL}$")

# "TAB A1", "Tabella E", "TAB. MG1". Two traps, both hit in development:
#   - a word boundary after TAB is required, or "TABLES" parses as TAB + "LES"
#     and "TABLET" as TAB + "LET";
#   - the separator must NOT span a newline. These PDFs break lines mid-word,
#     so prose such as "una tabella (F\nG) ed un grafico (GF\n)" otherwise
#     reads as a table code. A real code never has its keyword split from it.
INLINE_CODE = re.compile(rf"\b(?i:TAB|TABELLA)\b\.?[^\S\n]+({CODE_TAIL})\b")

# Bookmark titles come in two shapes needing two patterns:
#   "03_2023_TAB_A1", "2025 TAB A1 (EXP per Operatore)"   separated
#   "tabellaAA_2025", "tabellaFG_2025 (1)"                glued
# Neither can use \b: underscore is a word character, so "TAB_A1" has no word
# boundary around "TAB". Negative letter lookarounds instead.
TITLE_RE = re.compile(
    rf"(?:(?<![A-Za-z])TAB[ ._]+|tabella)({CODE_TAIL})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# "2 401 TAB A1  Esportazione definitiva per operatori   469"
INDEX_ROW = re.compile(
    rf"^\s*\d+\s+(\d+)\s+TAB\.?\s+({CODE_TAIL})\s+(.+?)\s+(\d+)\s*$"
)

# A line that is exactly "Tabella AA" (2025 vol. II, 2012 index listing).
LEADING_CODE = re.compile(rf"^(?i:Tabella)\.?\s+({CODE_TAIL})$")

# "All.1 - OPERAZIONI A LICENZA" (the cover) and "All.1: OPERAZIONI A LICENZA"
# (the summary). Both list the allegato's own tables -- "Tab. M - M1 - M2" -- so
# the header scan reads them as the starts of tables M, M1, M2, N, N1, N2, O, O1,
# O2, P, P1, P2 twelve times over, and the real tables are then given a span
# starting inside a summary. They are index pages and are skipped as such.
#
# Matched against the page with whitespace squeezed out, because pypdf shreds
# these pages into fragments -- "All.1: OPE" arrives as "Al" / "l.1: OPE" / "RAZIONI
# A" -- so no line ever contains the header. Only the eight cover and summary
# pages match: a table page carries "TAB. <code>", never "All.<n>".
ALLEGATO_HEAD = re.compile(r"All\.\d+[-:]")
_WS = re.compile(r"\s+")


def is_allegato_page(text):
    """True for a Dogane allegato cover or summary page."""
    return bool(ALLEGATO_HEAD.search(_WS.sub("", text)))


def is_blank(lines):
    """True for a separator page: no text, or the Italian "PAGINA BIANCA"."""
    if not lines:
        return True
    return len(lines) == 1 and "BIANCA" in lines[0].upper()


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

# "MINISTERO DELLA DIFESA - Annesso 3A". These tables carry no table code, so
# the annesso is both the code and the last path segment. The letter must stay
# adjacent and uppercase, otherwise "Annesso 4 TABELLA RIASSUNTIVA" is read as
# annesso "4 T" and splits one table into two.
ANNEXO_RE = re.compile(
    r"(?i:MINISTERO\s+DELLA\s+DIFESA)\s*[-–—]?\s*(?i:Annesso)\s*([0-9]+[A-Z]?)"
)

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

# ==========================================================================
# the printed page number, and what it is for
# ==========================================================================
#
# A footer page number: up to four digits framed by the three dashes these
# documents use, on either side or one side only ("– 1103 –", "- 1103-",
# "1103 -"). Dashes strictly outside the digits, so a numeric range inside a
# table cell ("27 - 15") is not mistaken for one. Roman numerals do not match,
# which is what keeps the index page of 2025 ("– III –") out of the sequence.
PAGE_NUMBER_RE = re.compile(r"^\s*[–—-]?\s*(\d{1,4})\s*[–—-]?\s*$")

# The last few pages of a table are allowed to look different -- 2025 F1 ends
# on its "Totale autorizzazioni" sheet, printed 1104 -- and are still F1. Hence
# a tolerance rather than a strict run of identical headers.
TAIL_PAGES = 2


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
    style 5  the DIFESA annesso, "MINISTERO DELLA DIFESA - Annesso 3A"
    """
    # Style 5: checked first and without a vocabulary, because these tables sit
    # outside all three code families and print no table code at all -- the
    # annesso number is the only handle, and it repeats on every page of the
    # run. A code-shaped reading of the surrounding title would otherwise be
    # invented from "TABELLA RIEPILOGATIVA ...".
    m = ANNEXO_RE.search(text)
    if m:
        return m.group(1).upper(), 5

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


def scan_headers(reader, vocabulary, family, stamps=None, provenance=None,
                 pasted=None, facts=None):
    """{page: (code, style)}, the provisional pages, and corruption tallies.

    Detections come back per page rather than already grouped by code, because
    the grouping key is (authority, article, code) and the authority is not known
    until the whole volume has been read: one code appears in two ministries,
    and "TAB. N" twice in the Dogane under two different articles. Grouping
    happens in build_manifest, once the provenance gaps have been filled.

    `stamps`, when given, is filled with {page: "Pagina N di X"} for the pages
    that carry one, and `provenance` with {page: (authority, article)}. The pass
    reads every page of the volume anyway and read_page() costs no more than the
    plain extraction it replaces, so this is the only free place to collect them.

    `pasted`, when given, collects the pages belonging to a Gazzetta Ufficiale
    excerpt bound into the volume. They hold no tables and are skipped; see
    indice.is_pasted() for why the header is enough.

    `facts`, when given, is filled with the three per-page properties the
    cross-volume stitch needs -- blank, printed numbers, header fingerprint.
    They are read from the same `lines` this pass already built, so they cost
    nothing, and they are recorded before the index/blank early-exits below: a
    page can be numbered content while carrying no table code at all, which is
    exactly the leading region of a volume that continues the previous one.
    """
    page_codes, weak_pages = {}, set()
    unassigned, garbled, ciphered, foreign = [], [], [], []
    sink = [] if pasted is None else pasted
    for i, page in enumerate(reader.pages):
        page_no = i + 1
        try:
            text, stamp = read_page(page)
        except Exception:
            unassigned.append(page_no)
            continue
        if indice.is_pasted(text):
            foreign.append(page_no)
            sink.append(page_no)
            continue
        if stamps is not None and stamp:
            stamps[page_no] = stamp
        if is_garbled(text):
            garbled.append(page_no)
        elif is_ciphered(text):
            ciphered.append(page_no)
        elif provenance is not None:
            authority, article, _ann = ontology.classify_page(text)
            if authority:
                provenance[page_no] = (authority, article)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if facts is not None:
            facts["blank"][page_no] = is_blank(lines)
            facts["numbers"][page_no] = printed_numbers(lines)
            facts["fingerprints"][page_no] = header_fingerprint(lines)
        if not lines or is_index_page(lines):
            continue
        # An allegato cover or summary lists its own tables by name; treating
        # that listing as table starts invents a dozen tables and truncates the
        # real ones.
        if is_allegato_page(text):
            continue

        code, style = page_code(lines, text, vocabulary, family)
        if not code:
            unassigned.append(page_no)
            continue

        page_codes[page_no] = (code, style)
        # Style 1 also matches summary pages: 2023 vol. II p8 carries a bare
        # "UE" under the same header and would steal the start of the real
        # Tabella UE at p543. A banner-backed page clears the flag, so the
        # table is provisional unless at least one of its pages is backed.
        if style == 1 and "ELENCO TABELLE" not in text:
            weak_pages.add(page_no)
    return page_codes, weak_pages, unassigned, garbled, ciphered, foreign


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


def provenance_from_indice(reader, sibling=None):
    """{page: authority} from the volume's own INDICE, or (None, reason).

    The INDICE states which ministry produced which folio range. That is the
    document's own account of its structure, so it beats inferring ownership from
    a running header: no guess about which string is exclusive to whom, and no
    inheritance across pages that print no marker. Cross-checked against the
    header markers on 2025 vol. II (966 pages) and 2023 vol. II (576 pages):
    agreement on every page where both speak.

    It cannot be the only source, for two reasons that are properties of the
    archive rather than of this parser:

      * it is absent. There is no index of any kind -- not INDICE, not SOMMARIO,
        not INDEX -- in 2013, 2015 vol. I, 2017 (both volumes), 2019 (both
        volumes), 2021 tom. II, 2021 vol. II, 2024 (both volumes) or 2003, 2005,
        2007. Those include the highest-yield volumes in the archive (2021 gives
        31 + 27 + 8 tables, 2019 gives 23 + 10), so an index-only pipeline would
        leave around a hundred tables unplaced.
      * it carries no article. The index bounds ministries, but the Dogane's four
        allegati -- art. 1 comma 2, art. 1 commi 8/9, art. 11 comma 5-bis, art.
        10 quater -- all sit inside one ministry block, so the article can only
        come from the table's own page.

    Hence this returns the authority where it can be had and the caller falls
    back to the markers where it cannot. `validate()` rejects a parse that is
    wrong rather than absent, which is the failure mode that matters: on 2018
    vol. I the parser reads the "Volume I" heading as a ministry and produces
    "MEF block@1", out of document order and entirely plausible.
    """
    offset, coverage = indice.folio_map(reader)
    _page, blocks = indice.find_indice(reader)
    if blocks is None and sibling is not None:
        # The index normally prints in the first volume and covers the whole
        # document, so a second volume has to borrow its sibling's.
        try:
            sib = PdfReader(sibling, strict=False)
            _page, blocks = indice.find_indice(sib)
        except Exception:
            blocks = None
    ok, reason = indice.validate(blocks, offset, coverage, len(reader.pages))
    if not ok:
        return None, reason
    ranges = indice.authority_ranges(blocks, offset)
    if not ranges:
        return None, "INDICE names no ministry that produces tables"
    return ({page: indice.authority_for_page(ranges, page, offset)
             for page in range(1, len(reader.pages) + 1)},
            f"INDICE, {len(ranges)} ministry block(s), folio=page{offset:+d}, "
            f"{coverage:.0%} legible")


def fill_provenance(raw, page_count):
    """{page: (authority, article)}, filling the gaps from the neighbours.

    Not every page of a ministry's block carries the marker that identifies it.
    In 2025 vol. II only 27 of the 126 MAE pages print the export band, so most
    MAE table pages have to inherit. Sections are contiguous, which is what makes
    that safe.

    Inheritance stops at a real boundary. For each maximal run of unplaced pages
    the placement immediately before and immediately after are compared: if they
    agree the run takes that placement, and if they differ the run is a genuine
    section edge that the page furniture cannot resolve, so it is left unplaced
    rather than guessed into one of the two. Blindly forward-filling would hand
    a whole ministry to whichever ministry happened to precede it.
    """
    resolved = {}
    page = 1
    while page <= page_count:
        if page in raw:
            resolved[page] = raw[page]
            page += 1
            continue
        run_end = page
        while run_end + 1 <= page_count and (run_end + 1) not in raw:
            run_end += 1
        before = resolved.get(page - 1)
        after = raw.get(run_end + 1)
        if before and (before == after or after is None):
            placement = before
        elif after and before is None:
            placement = after
        else:
            placement = None          # a boundary between two authorities
        if placement:
            for p in range(page, run_end + 1):
                resolved[p] = placement
        page = run_end + 1
    return resolved


def resolve_placement(pages_seen, provenance, articles=None):
    """(authority, article) for one table.

    The authority is a majority over the table's own pages: a table's pages are
    all in one ministry, so the ministry owning the most of them owns the table.

    The article is taken from the pages that name one -- the Dogane qualifier
    travels with every page of a table -- and is ignored for MAE and MEF, whose
    tables are not subdivided. It is deliberately *not* inherited from the
    ministry: one ministry block holds all four Dogane allegati, so a block-level
    answer would be useless.
    """
    tally = {}
    for page in pages_seen:
        authority = provenance.get(page)
        if authority:
            tally[authority] = tally.get(authority, 0) + 1
    if not tally:
        return (ontology.UNKNOWN, None)
    # max() over a key list sorted by name, so a tie is broken the same way on
    # every run and the manifest is reproducible.
    authority = max(sorted(tally), key=lambda name: tally[name])

    article = None
    if articles:
        seen = {}
        for page in pages_seen:
            candidate = articles.get(page)
            if candidate:
                seen[candidate] = seen.get(candidate, 0) + 1
        if seen:
            article = max(sorted(seen), key=lambda name: seen[name])
    if article and authority not in (ontology.DOG, ontology.DIFESA):
        # Only the Dogane and the Difesa subdivide by article. Any other
        # authority naming one is a stray mention -- an MEF narrative page
        # discussing art. 11 comma 5-bis must not gain a path level -- so the
        # article is dropped rather than invented.
        article = None
    return (authority, article)


def build_manifest(reader, vocabulary, counts, sibling=None):
    """Union every detector into a manifest keyed by (authority, article, code).

    The code alone is not a key. The Dogane print "TAB. N" twice in one volume,
    once under art. 1 comma 2 and once under art. 1 commi 8/9, and the MAE print
    "TAB M1" for a different table from the Dogane "TAB. M1". So the key is the
    triple, and the path is derived from it by ontology.relative_path().

    Placement has two sources. The volume's own INDICE is preferred, because it
    states the ministry ranges outright; the page furniture is the fallback for
    the volumes that have no index. Either way the *article* comes from the
    table's own pages, since the index does not subdivide the Dogane.

    Returns (manifest, facts). `facts` is the per-page blank/number/fingerprint
    map the cross-volume stitch reads; see scan_headers.
    """
    embedded = scan_bookmarks(reader)
    family = detect_family(reader, vocabulary)
    stamps = {}
    raw_prov = {}
    pasted = []
    facts = {"blank": {}, "numbers": {}, "fingerprints": {}}
    page_codes, weak_pages, unassigned, garbled, ciphered, foreign = scan_headers(
        reader, vocabulary, family, stamps, raw_prov, pasted, facts
    )

    from_indice, source = provenance_from_indice(reader, sibling)
    if from_indice:
        provenance = {page: authority for page, authority in from_indice.items()
                      if authority}
    else:
        # fill_provenance yields (authority, article) pairs; only the authority
        # is wanted here, since the article comes from `articles` below and must
        # not be inherited from a neighbouring ministry.
        provenance = {page: pair[0] for page, pair
                      in fill_provenance(raw_prov, len(reader.pages)).items()}
    articles = {page: pair[1] for page, pair in raw_prov.items() if pair[1]}

    # Group the per-page detections into tables. This happens here rather than
    # inside scan_headers because the key needs the page's provenance, which is
    # only complete once the gaps have been filled.
    groups = {}
    for page, (code, style) in page_codes.items():
        authority = provenance.get(page, ontology.UNKNOWN)
        article = articles.get(page)
        tkey = (authority, article, code)
        entry = groups.setdefault(tkey, {"pages": [], "style": style})
        entry["pages"].append(page)
        if style == 1 and page not in weak_pages:
            weak_pages.add(page)

    # A single-page hit is a prose cross-reference, not a table -- but only for
    # style 3, which cannot tell a running header from "come da elencazione
    # della tabella KK1". Styles 1, 2, 4 and 5 are positional or an explicit
    # running header and can legitimately fire on a one-page table; filtering
    # them too cost real tables (2020 vol. II went 41 -> 23).
    #
    # MIN_RUN is waived for the Dogane. The false positives it guards against
    # came from prose inside the MEF art. 27 narrative, and the Dogane tables
    # print "TAB. <code>" in the running header of a data page -- but only on the
    # first page of the short ones. Of the 18 MG tables of art. 11 comma 5-bis,
    # nine run to a single page, and applying the threshold there dropped them
    # while the span of the surviving MG8 swallowed the rest.
    tables, singletons = {}, []
    for tkey, entry in groups.items():
        run = len(entry["pages"])
        min_run = 1 if tkey[0] == ontology.DOG else MIN_RUN
        if entry["style"] == 3 and run < min_run:
            singletons.append(tkey[2])
            continue
        tables[tkey] = {
            "start": min(entry["pages"]), "source": "header", "run": run,
            "style": entry["style"],
            "provisional": any(p in weak_pages for p in entry["pages"]),
            "seen": entry["pages"],
        }

    for code, page in body_starts(reader, vocabulary).items():
        authority, article = resolve_placement([page], provenance, articles)
        tables.setdefault((authority, article, code), {
            "start": page, "source": "title", "provisional": False,
            "seen": [page],
        })

    conflicts = []
    for code, page in embedded.items():
        authority = provenance.get(page, ontology.UNKNOWN)
        article = articles.get(page)
        tkey = (authority, article, code)
        if tkey not in tables:
            # The bookmark names a code the page scan placed elsewhere, usually
            # because the bookmarked page carries no marker of its own. Fall
            # back to the latest-starting table of that code at or before the
            # page, which is the one whose run can contain it.
            before = [c for c, i in tables.items()
                      if c[2] == code and i["start"] <= page]
            if before:
                tkey = max(before, key=lambda c: tables[c]["start"])
        if tkey in tables and tables[tkey]["start"] != page:
            conflicts.append({
                "code": code, "embedded": page,
                "header": tables[tkey]["start"],
                "run": tables[tkey].get("run", 1),
                "resolved_from": "singleton" if code in singletons else "header",
            })
        # the bookmark is human-authored, so it always wins
        tables[tkey] = {
            "start": page, "source": "embedded", "provisional": False,
            "seen": tables.get(tkey, {}).get("seen", [page]),
        }

    # Ends are derived from the next start: every table run is contiguous.
    # Verified 28/28 codes on 2019 vol. I with zero interruptions.
    ordered = sorted(tables.items(), key=lambda kv: kv[1]["start"])
    last_page = len(reader.pages)
    trimmed = {}
    for idx, (tkey, info) in enumerate(ordered):
        code = tkey[2]
        # Placement first: it decides the path, and the per-table log lines
        # below name the table by that path. The majority runs over the pages a
        # detector actually saw, not over the derived span, which reaches to the
        # next table's start and would let a neighbour that inherited into the
        # tail outvote the table itself.
        info["authority"], info["article"] = resolve_placement(
            info.get("seen") or [info["start"]], provenance, articles)
        info.pop("seen", None)
        where = "/".join(filter(None, [info["authority"], info["article"], code]))

        end = ordered[idx + 1][1]["start"] - 1 if idx + 1 < len(ordered) else last_page
        info["end"] = max(info["start"], end)
        info["end"], dropped, note = trim_trailing(info["start"], info["end"],
                                                   stamps)
        if dropped:
            trimmed[where] = dropped
        if note:
            info["stamp_note"] = note
        info["pages"] = info["end"] - info["start"] + 1
        if code in counts:
            info["index_pages"] = counts[code]
            info["index_agrees"] = counts[code] == info["pages"]

    # The last table of the volume reaches the last physical page, which is
    # usually a "PAGINA BIANCA" leaf belonging to nothing. Trim it off, and
    # redo the index cross-check that the trim just invalidated.
    #
    # Only the final table is touched: every other end is derived from the next
    # table's start, so a blank leaf inside a run is already excluded. This has
    # to run after the stamp trim above, which may already have moved the end,
    # and after the derived ends are known so that "last" means last.
    if ordered:
        last_code, last_info = ordered[-1]
        trim_trailing_blanks(last_info, facts)
        if last_code[2] in counts:
            last_info["index_agrees"] = counts[last_code[2]] == last_info["pages"]

    # Re-key now that the authority is settled: two keys that looked distinct
    # while both were UNKNOWN can name the same table once placed, and a
    # collision here would silently drop one of them -- the exact failure the
    # authority level exists to prevent, so it is reported instead.
    final, collisions = {}, []
    for tkey, info in ordered:
        new_key = (info["authority"], info["article"], tkey[2])
        if new_key in final:
            collisions.append({
                "key": list(new_key),
                "kept_start": final[new_key]["start"],
                "dropped_start": info["start"],
            })
            continue
        final[new_key] = info

    return {
        "family": family,
        "page_count": last_page,
        "provenance_source": source,
        "pasted_pages": sorted(foreign),
        "pasted_ranges": [list(r) for r in indice.page_ranges(foreign)],
        "vocabulary": sorted(vocabulary),
        "index_counts": counts,
        "tables": final,
        "conflicts": conflicts,
        "key_collisions": collisions,
        "singletons": sorted(set(singletons)),
        "unplaced_tables": sorted(k[2] for k in final
                                  if k[0] == ontology.UNKNOWN),
        "unassigned_pages": len(unassigned),
        "garbled_pages": len(garbled),
        "ciphered_pages": len(ciphered),
        "stamped_pages": len(stamps),
        "trimmed": trimmed,
    }, facts


# ==========================================================================
# tables that cross a volume boundary
# ==========================================================================

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


def runs_to_the_end(volume, info):
    """True if a span reaches the last numbered page of its own volume."""
    end = last_numbered_page(volume["facts"])
    return bool(end and info["end"] >= end)


def continuation_pages(prev_manifest, prev_facts, nxt_manifest, nxt_facts):
    """Pages of `nxt` that carry on a table cut at the end of `prev`.

    Returns (tkey, first_page, last_page) or None, where `tkey` is the
    (authority, article, code) key of the table that runs off the end.

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
    tkey, info = max(tables.items(), key=lambda kv: kv[1]["start"])
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
    return tkey, first, last


def stitch(prev_manifest, prev_facts, nxt_manifest, nxt_facts,
           prev_name, nxt_name):
    """Record, on `nxt_manifest`, the pages it inherits from `prev_manifest`.

    Volumes of a year are consecutive parts of one document, and a table may
    straddle the join: 2025 Tabella F1 is printed pages 1035-1042 in volume I
    and 1043-1104 in volume II, seventy pages in all, its margin carrying
    "Tabella F1 / Pagina 70 di 70" on the last one.

    Because the split is per volume, the volume holding the tail used to write
    its file over the one holding the head, and only the tail survived. Both
    halves now go into a single Out/PDF/<authority>/[<article>/]<code><year>.PDF.

    The tail pages are keyed to the head's (authority, article, code): a
    continuation inherits its placement, since it is the same table, and the
    Dogane annex codes show why -- the same code string can name two different
    tables in one volume, so the tail must be filed with the head's authority
    and not with whatever ministry happens to own the next volume's first
    pages.
    """
    found = continuation_pages(prev_manifest, prev_facts, nxt_manifest, nxt_facts)
    if not found:
        return None
    tkey, start, end = found
    head = prev_manifest["tables"][tkey]
    nxt_manifest.setdefault("continued", {})[tkey] = {
        "from_volume": prev_name,
        "from_start": head["start"],
        "from_end": head["end"],
        "start": start,
        "end": end,
        "pages": end - start + 1,
        "total_pages": head["pages"] + (end - start + 1),
    }
    return tkey


# How much a detection is trusted when one table code turns up in two volumes
# of the same year and only one copy can be kept. The bookmark tree is
# human-authored, a run of header hits is the detector's own evidence, and a
# title repeat is the weakest of the three.
SOURCE_RANK = {"embedded": 3, "header": 2, "title": 1}


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

    The key is the full (authority, article, code), so two tables that merely
    share a code string in different ministries are never candidates for one
    another -- that is the whole point of the authority level, and keying on
    the bare code would undo it.

    The losers are marked `kept = False` for the writer to skip.
    """
    by_key = {}
    for idx, volume in enumerate(volumes):
        for tkey in volume["manifest"]["tables"]:
            by_key.setdefault(tkey, []).append(idx)

    for tkey, holders in by_key.items():
        if len(holders) < 2:
            continue
        copies = [(i, volumes[i]["manifest"]["tables"][tkey]) for i in holders]
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
            volumes[idx]["manifest"].setdefault("dropped", {})[tkey] = {
                "volume": volumes[idx]["label"],
                "start": info["start"],
                "end": info["end"],
                "pages": info["pages"],
                "kept_from": volumes[best[0]]["label"],
                "reason": "stesso codice in più volumi, non uno spezzamento",
            }


# ==========================================================================
# output
# ==========================================================================

def split_pdf(readers, manifests, out_root, year):
    """Write one PDF per table under the ministry/article tree.

        <out_root>/PDF/MAE/A12023.PDF
        <out_root>/PDF/MEF/AA2023.PDF
        <out_root>/PDF/DOG/A1C2/N12023.PDF
        <out_root>/PDF/DIFESA/A2C6/3A2023.PDF

    The authority comes from the ministry that printed the table, not from the
    volume it came from, so Out/PDF/MEF/AA2023.PDF holds Tabella AA from
    whichever volume carried it. The year is a filename suffix rather than a
    directory level, so one folder per table holds one file per reporting year.

    `readers` and `manifests` are parallel sequences, one entry per volume of
    the year. A table is written once, from the volume that detected it; when
    it continues into the next volume, that volume's manifest carries the
    leading pages under "continued", and they are appended here so both halves
    land in the same file. Previously each volume wrote its own file over the
    same name, and whichever ran last won: 2025 F1 lost the 8 pages of vol. I.

    Segments are keyed by the full (authority, article, code), so a code that
    two ministries both print becomes two files rather than one overwriting the
    other -- which is the failure the authority level was introduced to fix. A
    copy settled away by resolve_repeats is skipped: one table, one file.
    """
    plan = {}
    for reader, manifest in zip(readers, manifests):
        for tkey, info in manifest["tables"].items():
            if info.get("kept") is False:
                continue
            plan.setdefault(tkey, []).append((reader, info["start"], info["end"]))
        for tkey, cont in manifest.get("continued", {}).items():
            plan.setdefault(tkey, []).append((reader, cont["start"], cont["end"]))

    written = []
    for (authority, article, code), segments in plan.items():
        writer = PdfWriter()
        for reader, start, end in segments:
            for p in range(start, end + 1):
                writer.add_page(reader.pages[p - 1])
        rel = ontology.relative_path(authority, article, code, year, PDF_EXT)
        path = Path(out_root, OUT_DIR, "PDF", rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            writer.write(fh)
        written.append(str(path))
    return written


def report(manifest, label):
    n = len(manifest["tables"])
    print(f"{label:46} family={manifest['family']} "
          f"pages={manifest['page_count']:5} tables={n:3} "
          f"{'OK' if n else 'NO-TABLES'}")

    by_authority = {}
    for (authority, article, code) in manifest["tables"]:
        by_authority.setdefault((authority, article), []).append(code)
    for (authority, article), codes in sorted(by_authority.items(),
                                              key=lambda kv: (kv[0][0],
                                                              kv[0][1] or "")):
        label_txt = ontology.article_label(authority, article)
        suffix = f"  {label_txt}" if label_txt else ""
        print(f"    {authority:8} {'/'.join(filter(None, [authority, article])):14}"
              f" {len(codes):3} tables  {' '.join(sorted(codes))}{suffix}")

    if manifest["unplaced_tables"]:
        print(f"    UNPLACED  : {len(manifest['unplaced_tables'])} tables with "
              f"no ministry marker: {' '.join(manifest['unplaced_tables'])}")
    print(f"    ministry  : {manifest.get('provenance_source', '-')}")
    if manifest.get("pasted_ranges"):
        spans = ", ".join(f"p{a}-{b}" if a != b else f"p{a}"
                          for a, b in manifest["pasted_ranges"])
        print(f"    pasted-in: {len(manifest['pasted_pages'])} Gazzetta "
              f"UFFICIALe pages, no tables, skipped ({spans})")
    if manifest["key_collisions"]:
        print(f"    COLLISION : {len(manifest['key_collisions'])} tables dropped, "
              f"two detections landed on one (authority, article, code):")
        for c in manifest["key_collisions"][:6]:
            # article is None for MAE and MEF, so the parts are filtered rather
            # than joined blindly -- a blind join raised here and aborted the run.
            where = "/".join(str(part) for part in c["key"] if part)
            print(f"        {where:22} kept p{c['kept_start']}, "
                  f"dropped p{c['dropped_start']}")
    if manifest["vocabulary"]:
        print(f"    index      : {len(manifest['vocabulary'])} codes "
              f"{' '.join(manifest['vocabulary'])}")
    if manifest["conflicts"]:
        print(f"    conflicts  : {len(manifest['conflicts'])}, resolved to bookmark")
        for c in manifest["conflicts"][:6]:
            print(f"        {c['code']:5} bookmark p{c['embedded']:<5} "
                  f"header p{c['header']:<5} run={c['run']} "
                  f"({c['resolved_from']})")
    prov = [k[2] for k, i in manifest["tables"].items() if i["provisional"]]
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
    notes = [(k[2], i["stamp_note"]) for k, i in manifest["tables"].items()
             if i.get("stamp_note")]
    for code, note in notes[:6]:
        print(f"    stamp note : {code:5} {note}")
    checked = [i for i in manifest["tables"].values() if "index_agrees" in i]
    if checked:
        agree = sum(1 for i in checked if i["index_agrees"])
        mism = [(k[2], i["pages"], i["index_pages"]) for k, i in
                manifest["tables"].items() if "index_agrees" in i
                and not i["index_agrees"]]
        print(f"    index check: {agree}/{len(checked)} spans agree with the "
              f"index page counts, {len(mism)} mismatch {mism[:4]}")
    for tkey, cont in manifest.get("continued", {}).items():
        where = "/".join(str(part) for part in tkey if part)
        print(f"    continued  : {where} carries on from "
              f"{cont['from_volume']} p{cont['from_end']}, here "
              f"p{cont['start']}-{cont['end']} ({cont['pages']} pages) "
              f"-> one PDF of {cont['total_pages']}")
    dropped = manifest.get("dropped", {})
    if dropped:
        print(f"    duplicate  : {len(dropped)} table(s) found in another "
              f"volume of the same year and not a split; kept copy wins")
        for tkey, info in list(dropped.items())[:8]:
            where = "/".join(str(part) for part in tkey if part)
            print(f"        {where:22} p{info['start']}-{info['end']} "
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
    if not m:
        raise ValueError(
            f"Anno non trovato in '{report_path}': il percorso deve essere "
            f"'{REPORTS_DIR}/<anno>/<file>.pdf'"
        )
    return m.group(1)


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
            if int(folder.name) < FIRST_YEAR:
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


def sibling_index(report_path, base=""):
    """The volume of the same year that carries the INDICE, or None.

    The index prints once, in the first volume, and lists the page ranges of the
    whole document including the later volumes -- confirmed for 2016, 2018, 2020,
    2022, 2023 and 2025, where volume I and volume II report identical ranges.
    So a volume without its own index borrows its first sibling's.
    """
    folder = Path(report_path).parent
    if parse_volume(Path(report_path).stem) == 1:
        return None                      # already the first volume
    candidates = [p for p in sorted(folder.glob("*.pdf"))
                  if parse_volume(p.stem) == 1]
    if len(candidates) == 1:
        return str(candidates[0])
    # Two files claim volume 1 (2021 has both TOMO_I and VOLUME_I); prefer the
    # one that actually parses, and let validation decide.
    return str(candidates[0]) if candidates else None


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
    n_volumes = sum(len(v) for v in jobs.values())
    print("=" * 70)
    print(f"Reports2PDFTables — {n_volumes} volume/i, {len(jobs)} anno/i")
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
            # Guarded per volume, for the same reason the report is: one
            # unreadable volume must cost that volume, not the remaining
            # twenty-one.
            try:
                manifest, facts = build_manifest(reader, vocabulary, counts,
                                                sibling=sibling_index(report))
            except Exception as exc:
                print(f"{label}: elaborazione fallita: "
                      f"{type(exc).__name__}: {exc}\n")
                del reader
                continue
            volumes.append({"label": label, "path": report, "reader": reader,
                            "manifest": manifest, "facts": facts})

        # The joins are claimed before the reports are printed, so the
        # "continued" line a reader sees is the one the writer acted on.
        if year == "?":
            if len(volumes) > 1:
                print("    (anno non determinato: nessun join fra i volumi)")
        else:
            for idx in range(len(volumes) - 1):
                stitch(volumes[idx]["manifest"], volumes[idx]["facts"],
                       volumes[idx + 1]["manifest"], volumes[idx + 1]["facts"],
                       volumes[idx]["label"], volumes[idx + 1]["label"])

        resolve_repeats(volumes)

        for volume in volumes:
            manifest = volume["manifest"]
            total += len(manifest["tables"])

            # The artefacts are written before the summary, and the summary is
            # guarded. A formatting bug in a human-readable report once raised
            # a TypeError here and took the run with it: volumes 21 to 43 were
            # never attempted, and the volume that raised lost both its manifest
            # row and its PDFs. Nothing cosmetic may sit upstream of the data.
            if args.manifest:
                target = Path(args.manifest)
                target.parent.mkdir(parents=True, exist_ok=True)
                # json has no tuple keys, so the tables are emitted as a list of
                # records carrying the three key parts as fields. The key stays
                # a tuple in memory because it is what makes a code unique.
                serialisable = dict(manifest)
                serialisable["tables"] = [
                    {"authority": authority, "article": article, "code": code,
                     **info}
                    for (authority, article, code), info
                    in manifest["tables"].items()
                ]
                serialisable.pop("continued", None)
                serialisable["continued"] = [
                    {"authority": k[0], "article": k[1], "code": k[2], **info}
                    for k, info in manifest.get("continued", {}).items()
                ]
                serialisable.pop("dropped", None)
                serialisable["dropped"] = [
                    {"authority": k[0], "article": k[1], "code": k[2], **info}
                    for k, info in manifest.get("dropped", {}).items()
                ]
                with open(target, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"report": volume["path"], "year": year,
                                        **serialisable},
                                        ensure_ascii=False) + "\n")

            try:
                report_manifest(manifest, f"{volume['label']} (y={year})")
            except Exception as exc:
                print(f"    REPORT FAILED (artefacts are safe): "
                      f"{type(exc).__name__}: {exc}\n")

        if not args.dry_run and any(v["manifest"]["tables"] for v in volumes):
            out_root = args.out or args.base or "."
            written = split_pdf([v["reader"] for v in volumes],
                                [v["manifest"] for v in volumes], out_root, year)
            print(f"    wrote      : {len(written)} PDF in "
                  f"{Path(out_root, OUT_DIR, 'PDF')}/<authority>/"
                  f"[<articolo>/]<tabella>{year}.PDF")
        print()
        del volumes

    print("=" * 70)
    print(f"Totale: {total} tabelle in {n_volumes} volume/i")
    if args.dry_run:
        print("(dry-run: nessun PDF scritto)")


def report_manifest(manifest, label):
    report(manifest, label)


if __name__ == "__main__":
    main()