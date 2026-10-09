#!/usr/bin/env python3
"""
Reports2PDFTables — split a Law 185/1990 annual report volume into one PDF
per table.

    reports_185_1990/<anno>/<relazione>.pdf
        ->  Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF
        ->  Out/TRASH/<anno>/<volume>_p<a>-<b>.PDF   (the pages no table owns)

The year is read from the reports_185_1990/<anno>/ path, so one script serves
every reporting year. With many years present, --year is required.

Usage:
    ./Reports2PDFTables.py --year 2024 --volume both
    ./Reports2PDFTables.py --year 2023 --volume 2
    ./Reports2PDFTables.py --report reports_185_1990/2023/2023_LXVII_n2_VOLUME_II.pdf
    ./Reports2PDFTables.py --all                 # every text-bearing volume
    ./Reports2PDFTables.py --year 2023 --dry-run # report only, write nothing
    ./Reports2PDFTables.py --year 2023 --no-trash # report the trash, do not write it

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
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    sys.exit("Errore: pypdf non installato. Esegui: pip install -r requirements.txt")

from lib import indice, ontology


def block_at(edges, page):
    """Ministry owning a page per the volume's INDICE, or None.

    None where the index does not reach: before its first block, or in a volume
    it only partly covers. A missing boundary is not a boundary, so callers
    treat it as "nothing to enforce here" rather than "belongs to nobody".
    """
    if not edges:
        return None
    return indice.block_for_page(edges, page)


# A page that opens another body's section: the ministry's own name, then its
# office, then no table. "DIREZIONE DOGANE / Ufficio controlli dogane / R E L A
# Z I O N E" on 2021 vol. II p586 is the first of four (p586, p594, p601, p607)
# and each opens a Dogane allegato.
#
# Matched on the line ordering rather than on a name, because the names are the
# whole problem: the MEF's own pages open with "Ministero dell'Economia e delle
# Finanze", so a name test would fire on the annex it is meant to end. What
# separates them is that a table page names a code and a section-opening page
# does not, so the caller only consults this where the volume's listing says no
# table may follow.
SECTION_OPEN_MAX_LINE = 3


def is_section_open(lines, promised, banner=False):
    """True for a page that opens a new body's section rather than a table.

    Four conditions, and all four are needed:

      * a body named in the first few lines, where a running header sits. The
        window is small because the archive puts the volume furniture above it.
        The head is matched with its whitespace SQUEEZED, because pypdf shreds
        these names wherever the font was subsetted: 2025 vol. II p457 arrives as
        'DIR' / 'EZIONE DOG' / 'ANE', and a line-by-line test never sees
        "direzione" in it. Same lesson as ontology.is_mef_section, and the same
        reason the word cannot simply be matched per line;
      * NO code on the page -- not one the volume's ELENCO promised, and not one
        of any other shape. A table page always names its table, even when the
        name is the bare "AA" of the MEF annex;
      * no table title either. "Tabella <code> - <title>" is the annex's own
        opening and must not be read as a new section;
      * and NOT the MEF art. 27 banner. Squeezing the head is what makes the name
        test fire, and it fires on the MEF's own inline relation pages too
        (2025 vol. II pp 264, 266, 267 -- 'Mi' / "nistero dell'Economia e delle
        Finanze"), which are tables. The banner is the discriminator, and it is
        already computed for these pages by the caller. It is a coarse test,
        though: the MEF prints that banner on its section cover as well as on its
        table pages, so this witness no longer reports 2025 vol. II p260. Nothing
        is lost by it -- p260 is exactly the MEF block edge in the INDICE
        (edges 194/259/260/457), so the boundary clamp already treats that page as
        a ministry change and the two witnesses agree. What is bought is that
        three table pages are no longer mistaken for section openings, which is
        what would have handed a table a page of somebody else's section.

    Measured over all 1048 pages of 2025 vol. II: fires on 17 pages, the same 17
    the previous version did bar p260, plus p457. p457 is the Dogane relation
    cover -- 'DIR' / 'EZIONE DOG' / 'ANE' / 'Ufficio co' / 'ntrolli dogane' --
    which the previous version could not see at all, and which is the page UE was
    shipping.
    """
    head = lines[:SECTION_OPEN_MAX_LINE]
    squeezed = _WS.sub("", "\n".join(head)).lower()
    if not any(word in squeezed
               for word in ("ministero", "direzione", "agenzia")):
        return None
    for line in lines:
        bare = line.strip().rstrip(".")
        if CODE_RE.match(bare) and any(same_table(bare, c) for c in promised):
            return None
    # No table title either. "Tabella <code> - <title>" is the annex's own
    # opening and must not be read as a new section.
    for line in lines[:SECTION_OPEN_MAX_LINE + 2]:
        if ELENCO_TITLE.match(line) or LEADING_CODE.match(line.strip()):
            return None
    if banner:
        return None
    return _body_label(lines, promised)


def _body_label(lines, promised):
    """The body's name on a section-opening page, for the report to print.

    Read from the raw line when one of them carries the whole word, and from the
    whitespace-squeezed head otherwise -- because on a shredded page NO line
    carries it, and returning None there would discard a page this function has
    already decided is a section opening. That is not hypothetical: it is how
    p457 was lost while the name test above was being loosened to catch it.

    The label is the leading run of capitals in the squeezed head, which is what
    the typesetting puts there: "DIREZIONEDOGANE" from 'DIR' / 'EZIONE DOG' /
    'ANE', "MINISTERODELLADIFESA" from the page that prints it whole. Readable
    rather than faithful, which is all a note in the trash TSV has to be.
    """
    head = lines[:SECTION_OPEN_MAX_LINE]
    for line in head:
        low = line.lower()
        if any(word in low for word in ("direzione", "agenzia", "ministero")):
            return line.strip()
    squeezed = _WS.sub("", "\n".join(head)).lstrip("0123456789 .-")
    run = ""
    for ch in squeezed:
        if not ch.isalpha():
            break
        if ch.islower():
            break
        run += ch
    return run or None


def record_boundary(outside, where, page, authority, count):
    """Note in `outside` that `where` stops at `page`, accumulating the count.

    A table can hit both boundaries at once -- on 2025 vol. II the DIFESA
    annesso 4 is cut by the INDICE at the INTERNO leaf and by the listing further
    on -- and the report wants one line per table naming every witness that
    fired, not one line per witness.
    """
    entry = outside.setdefault(where, {"pages": [], "authority": None,
                                       "total": 0})
    if page not in entry["pages"]:
        entry["pages"].append(page)
    entry["authority"] = entry["authority"] or authority
    entry["total"] += count

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
# casi non si equivalgono e CatalogueTables.py cerca questo nome esatto.
PDF_EXT = ".PDF"

# Per-table PDFs are written as
# Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF, the uppercase extension
# included. CatalogueTables.py walks the same tree with PDF_EXT there; change
# both together or step 2 finds nothing, since globbing is case-sensitive on
# Linux.


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

# "TAB. M - APPENDICE": the Dogane print an appendix as its base code plus this
# word, and the appendix is a DIFFERENT table, not more of the same one. TAB. M
# is "Esportazione Definitiva (EX)" and TAB. M - APPENDICE is "Riesportazione
# (RE)": a different operation type, a different set of rows, and the volume's
# own summary lists it separately ("Riesportazione (RE) M Appendice - M1
# Appendice - M2 Appendice", 2025 vol. II p462). Read as a bare code, INLINE_CODE
# above matches "TAB. M" and hands the appendix to the base table: M came out
# 291pp instead of 273pp, and six of its tables carried a second one inside.
#
# Exactly six codes have one, and the covers say which: only EX and IM have a
# "re-" counterpart, so M, M1, M2, O, O1 and O2 do, and the temporanee (N, P and
# their riepiloghi) do not. Under art. 1 commi 8/9 only the O series has data,
# because the M appendix there is a single row worth EUR 0,00 (p464) and the
# table was never printed.
#
# Two shapes have to be handled, for the reason ELENCO_ROW documents: pypdf
# breaks the keyword wherever the font was subsetted, so the M appendix cover
# arrives as 'Riesportazione (RE)' / 'T' / 'AB. M - APPENDICE'. Losing that one
# page would leave it inside M's span, since a table's end is derived from the
# next table's start.
#
# The word is required rather than inferred from a dash, so the summary line
# "Tab. M - Appendice - Riesportazioni (RE)" is matched as a mention and not as
# a table opening: the dash and the word must be the header's ending, not the
# start of a title.
APPENDIX_CODE = re.compile(
    rf"(?i:T\s*A\s*B\s*\.\s*)({CODE_TAIL})\s*[-–—]\s*APPENDICE\s*$"
)

# The suffix the code carries from then on, and it is deliberately glued to the
# base code rather than separated by a space. The manifest code and the
# filename stem have to be the same string: the verifier reads the code back out
# of the filename (split_stem), so a manifest saying "M APPENDICE" against a
# file named MAPPENDICE2025.PDF would build a vocabulary the manifest cannot
# match and report every appendix file as not carrying its own code.
APPENDIX_SUFFIX = "APPENDICE"

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


# ==========================================================================
# the ELENCO TABELLE SEGNALAZIONI: an index of the tables that follow it
# ==========================================================================
#
# The MEF prints, ahead of its own table annex, a list of the tables that
# follow: "Tabella AA  Esportazioni definitive per Istituti di Credito",
# "Tabella FG  Finanziamenti/Garanzie per Istituti di Credito", and so on, then
# an ELENCO GRAFICI for the four chart pages. On 2025 vol. II it is pages
# 270-272 and it names all 35 codes of the annex -- which is the volume's own
# statement of what its table section contains, and the only one: none of these
# codes appears in the INDICE, and the four charts appear in no outline.
#
# It is read as an index, and therefore in the order printed, because that is
# what it is. That gives three things no other witness offers:
#
#   * the vocabulary of the annex, harvested exactly, so a code that only the
#     running header names is corroborated rather than guessed;
#   * the codes expected *after* the index, so a table the header scan misses
#     and no outline names is still known to exist;
#   * the boundary of the annex itself, which is what stops the last table
#     before it from running on into the Dogane annex that follows.
#
# Three ways the text arrives, all of them handled here:
#
#   * the heading is shredded: "ELENC" / "O TABELLE SEGNALAZIONI" on 2025,
#     "ELENCO TABELLE" whole on older volumes, so the marker is matched with
#     whitespace squeezed out;
#   * so is the word Tabella: "Ta" / "bella FG - Finanziamenti-Garanzie" on the
#     relation pages, "Tabe" / "lla AA" in the index itself. Hence the entry
#     pattern tolerates a break between the keyword and the code, and the
#     volume's own page_break rule is reused rather than reinvented;
#   * the ELENCO continues over more than one page (2025: three, and the
#     previous read stopped at the first eight codes), so a run of pages is read
#     as one list.
#
# What an index page is *not*: a table page. On 2025 vol. II the MEF table pages
# carry "ELENCO TABELLE / SEGNALAZIONI / Operazioni disciplinate dall'art. 27"
# in a trailing header block of their own, so the marker is honoured in the TOP
# lines only, and a page is accepted as a listing only once it has named a
# handful of codes -- the way a real index differs from a page that mentions
# one.

# "Tabella AA", "Tab. M", and the shredded forms pypdf produces from them:
# "Ta" / "bella FG - Finanziamenti-Garanzie", "Tabe" / "lla AA".
#
# The break can fall anywhere inside the keyword, so the keyword is spelled out
# with \s* between its letters rather than matched whole. Whitespace is *not*
# squeezed out, unlike the heading test above: squeezing merges the code into
# the title that follows it ("TabellaAAEsportazioni"), and then no lookahead can
# tell AA from AAE. The separator between keyword and code is therefore
# required, which is what the archive prints.
ELENCO_ROW = re.compile(
    rf"(?i:T\s*a\s*b\s*e\s*l\s*l\s*a|TAB\s*\.\s*)\s+({CODE_TAIL})(?![A-Za-z0-9])"
)

# The heading, whitespace squeezed, so "ELENC"+"O TABELLE" is one word.
ELENCO_HEAD = re.compile(r"ELENCOTABELLE|ELENCOGRAFICI|ELENCODELLETABELLE")

# A table title: "Tabella FG - Finanziamenti-Garanzie per intermediari", shredded
# by pypdf into "Ta" / "bella FG - Finan...". The trailing title is REQUIRED,
# which is what separates a real table opening from the same word in prose --
# "come da elencazione sintetica della tabella KK1" carries no dash after the
# code and is not a table start. ELENCO_ROW above deliberately allows no title,
# because inside a listing the title is on the next line.
ELENCO_TITLE = re.compile(
    rf"(?i:T\s*a\s*b\s*e\s*l\s*l\s*a|TAB\s*\.\s*)\s+({CODE_TAIL})(?![A-Za-z0-9])"
    rf"\s*[-–—:]\s*\S"
)

# A real listing names many codes on a page. The MEF index names 12-16; a table
# page whose running header mentions the ELENCO names one.
ELENCO_MIN_CODES = 6

# How far past the MEF section banner a table title may sit. The banner is three
# shredded lines -- "Mi"/"nistero dell'Economia e delle Finanze",
# "Dip"/"artimento del Tesoro Direzione V - Uffici"/"o VIII",
# "Operazio"/"ni disciplinate dall'art. 27" -- so the title lands two or three
# lines below it, never further. Generous, because the point is only to stop the
# search before the table's own body.
TITLE_LINES = 6

# How far below the banner a bare code line may sit and still be this page's
# own stamp. The MEF annex prints it fifth or sixth, under a four-line banner;
# the first table row is below it. Matches BARE_CODE_MAX_LINE in VerifyTables.py,
# which is the same rule reached independently by the audit.
BARE_CODE_MAX_LINE = 8

# Where the ELENCO of a ministry sits, and how far its section runs. A listing
# ends at the next listing of the same ministry, at a ministry boundary, or at
# the end of the volume -- whichever comes first. Bounded, because an
# unterminated section would swallow the Dogane annex into the MEF's.
ELENCO_MAX_PAGES = 400


def listing_codes(text):
    """The table codes named on one ELENCO page, in printed order.

    Empty for a page that is not a listing: the marker has to appear in the top
    lines (the MEF table pages repeat it in a trailing block), and the page has
    to name enough codes to be a list rather than a mention.
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return []
    if not ELENCO_HEAD.search(_WS.sub("", "\n".join(lines[:12])).upper()):
        return []
    found = []
    for i, line in enumerate(lines):
        # A row is "Tabella <code>" and pypdf breaks it wherever the font was
        # subsetted: "Ta" / "bella FG - ..." on a relation page, "Tabe" / "lla
        # AA" inside the index, and on one page of the 2025 listing "lla IA" /
        # "A" -- where the break falls inside the code itself. So each line is
        # joined with the next two and the code read off the join, which covers a
        # break anywhere in "Tabella" or immediately after it.
        #
        # No digit-one fold: the listing is human-authored and prints the code as
        # it is, and 2025 has both "Tabella II" and "Tabella II1" as distinct
        # tables -- folding maps both onto I1 and neither is recognisable
        # afterwards. same_table() absorbs the shredding instead, at comparison
        # time, where it cannot alter a code.
        probes = [line + " " + " ".join(lines[i + 1:i + 3])]
        if i + 1 < len(lines):
            probes.append(line + " " + lines[i + 1])
        for probe in probes:
            m = ELENCO_ROW.match(probe)
            if m:
                code = m.group(1).upper()
                if code not in found:
                    found.append(code)
                break
    return found if len(found) >= ELENCO_MIN_CODES else []


def _split_code(code):
    """("AA", "1") for "AA1". The code tail is capitals then digits throughout."""
    i = 0
    while i < len(code) and code[i].isalpha():
        i += 1
    return code[:i], code[i:]


def same_table(printed, detected):
    """True if an index reading and a page reading can name the same table.

    The one disagreement in the archive is a *letter* lost to the shredding.
    pypdf breaks 2025 vol. II's "Tabella IAA" into "Tabe" / "lla IA" / "A", so
    the listing yields "IA" and the table page yields "IAA": same letters, same
    digits, one capital missing. IBB is the same case.

    A missing *digit* is deliberately NOT forgiven, because in this archive a
    digit is a different table: "Tabella II" and "Tabella II1" are two tables,
    as are AA and AA1, and every family-2 pair A1/A2, B1..B7. Folding them
    together here would hide exactly the confusion AGENTS.md section 4 warns
    about. So the rule is: one extra capital, nothing else.

    This only widens a comparison. It never decides that a code exists, so
    nothing is created by being generous here.
    """
    if printed == detected:
        return True
    short, long = sorted((printed, detected), key=len)
    if len(long) != len(short) + 1:
        return False
    letters_s, digits_s = _split_code(short)
    letters_l, digits_l = _split_code(long)
    # Same digits, one more capital at the end, and the shorter reading is itself
    # a code the archive uses. "IA"/"IAA" yes. "AA"/"AA1" no, because the digits
    # differ. "FG"/"F" no, because no table in the archive is called F: a lone
    # capital is not a truncated code, it is a different word.
    return (digits_s == digits_l and letters_l.startswith(letters_s)
            and len(letters_s) >= 2)


def appendix_code(lines):
    """The appendix table this page opens, as a code of its own, or None.

    Read from the running header, and from a pair of adjacent lines as well as
    from each line singly, because the Dogane cover page shreds the keyword
    ('T' / 'AB. M - APPENDICE') -- the same two probes listing_codes() and
    page_code_listed() use, for the same reason.

    Unbounded on purpose, like style 3: a positional window cannot separate a
    running header from a prose cross-reference, and the word APPENDICE is a
    strong enough discriminator on its own.
    """
    for i, line in enumerate(lines):
        probes = [line]
        if i + 1 < len(lines):
            probes.append(line + " " + lines[i + 1])
        for probe in probes:
            m = APPENDIX_CODE.search(probe)
            if m:
                return m.group(1).upper() + APPENDIX_SUFFIX
    return None


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


# ==========================================================================
# the ministry relation's OWN numbered tables
# ==========================================================================
#
# A fifth code space, and the only one that is neither a law article nor an
# allegato. Every ministry writes a narrative RELAZIONE before its table annex,
# and that narrative numbers its own exhibits: "Tabella 1", "Tabella 23", with
# dotted sub-tables "Tabella 8.1" .. "8.8" and "14.1"/"14.2". They are real
# tables of real figures -- five years of export values by country, by operator,
# by Military List category -- and until now nothing detected them, so all of
# them sat in Out/TRASH under the "front-matter" label that any run starting at
# page 1 earns. On 2025 vol. I that is 31 tables over pp 25-66.
#
# Nothing else in the pipeline can see them, and the reason is worth stating:
#
#   * they print no law reference. Art. 27 (MEF), art. 1 comma 2 (Dogane) and
#     art. 2 comma 6 (Difesa) are all absent, so there is no article to key on;
#   * they are absent from the INDICE, which gives MAE "Relazione » 11" and
#     "Tabelle » 65" and nothing between -- the relation's exhibits are not
#     indexed, in any year;
#   * they are absent from the bookmark tree, which jumps from
#     "Relazione al Parlamento 2025 intro def" (p15) straight to "LEGENDA-2025"
#     (p69);
#   * their titles are prose cross-reference bait. The narrative names them
#     constantly -- "Nella Tabella 6 è riportato l'elenco dei primi 25 Paesi" --
#     so MIN_RUN and frequency, which is what the annex tables rely on, would
#     fire on the mention rather than the table.
#
# What identifies them is the opposite: the heading is the WHOLE LINE, with
# nothing before or after it. Every one of the 17 prose cross-references in
# 2025 vol. I is rejected by that test, including the two that are hardest --
# "(Tabella 1)." ending a sentence and "Tabella 5 e Grafico 6" naming two
# exhibits at once. Measured: 32 heading lines, 31 distinct numbers, 17 prose
# mentions rejected.
#
# The codes are plain integers, which collide with nothing: family 2 is A1..P2
# and art. 27 is letters and digits. They do collide with the Difesa annesso
# numbers (2, 3A, 4) in shape, but the key is (authority, article, code) and the
# annessi are filed under DIFESA/A2C6, so MAE "2" and DIFESA/A2C6 "2" are two
# different tables in two different directories.
#
# The output code is "T" + the printed number with the dot removed, so 8.1
# becomes T81 and 14.1 becomes T141. It has to be letters-and-digits only:
# ontology.safe_code() strips everything else, so a manifest code of "8.1"
# would be written as "81" and VerifyTables.split_stem() would read "812025"
# back as "81" -- the manifest and the filename would disagree, which is the
# MAPPENDICE trap and the reason that suffix is glued rather than spaced. The
# printed form is kept beside the code in the manifest so the two can be
# cross-checked by eye.

REL_PREFIX = "T"

# "Tabella 8.8 (parte 1 di 2)" and "Grafico 9". The whole line, nothing either
# side. Both kinds because both are band boundaries; see rel_heading.
REL_HEADING = re.compile(
    r"^(Tabella|Grafico)\s+(\d+(?:\.\d+)?)"
    r"(?:\s*\(\s*parte\s+\d+\s+di\s+\d+\s*\))?\s*$", re.IGNORECASE)

REL_TABELLA = "Tabella"
REL_GRAFICO = "Grafico"

# What the output code must look like, for VerifyTables to agree with.
REL_CODE = re.compile(rf"^{REL_PREFIX}\d{{1,3}}$")

# How far down a page the heading may sit and still be the page's own exhibit.
#
# Measured, not guessed, and the measurement is what settled the value. Scanning
# 2025 vol. I pp 15-70 for whole-line headings finds 46 of them, at line indices
# 0 to 27, and 46 is exactly the 31 tables plus the 15 charts the volume prints:
# so the anchored test invents nothing at ANY depth, and the bound below is a
# guard rather than the thing doing the work.
#
# It was 12, and that was wrong by four tables. A page carrying two exhibits puts
# the second one well down the page -- "Tabella 8.4" is line 27 of p40 -- and
# "Tabella 19" is line 12 of p60, one line past a 12-line window. Those four
# (T84, T87, T21, T19) were silently not found, which is the failure mode this
# file is full of: a threshold that is wrong somewhere and says nothing.
REL_HEADING_SCAN = 40

# The style page_code returns for this space. Its own number rather than one of
# the six, because every one of those is a shape that could be a law table, and
# this is not.
REL_STYLE = 7


def rel_code(number):
    """'8.1' -> 'T81', '23' -> 'T23'. See the note above on why not '8.1'."""
    return REL_PREFIX + number.replace(".", "")


def rel_heading(line):
    """(kind, number) for a relation exhibit a line opens, or None.

    The whole line has to be the heading. That is the whole discriminator, and
    it is what keeps "Nella Tabella 6 è riportato..." out.

    Both kinds, and the distinction is load-bearing: a Grafico is a CHART, not a
    table, and none is exported. But it still has to be read here, because it is
    also a band boundary -- on 12 pages of 2025 vol. I a chart shares the page
    with a table (p29 Tabella 3 + Grafico 2, p45 Tabella 9 + Grafico 9, and ten
    more), and a cut drawn only between Tabelle would leave the chart sitting in
    the table's file.
    """
    m = REL_HEADING.match(line.strip())
    return (m.group(1), m.group(2)) if m else None


def rel_headings(lines):
    """Every relation exhibit a page opens, in printed order, as (kind, number).

    A LIST, and deliberately so: three pages of 2025 vol. I open two exhibits
    between them -- p40 prints 8.3 and 8.4 side by side, p42 prints 8.6 and 8.7,
    p60 stacks 18 above 19 -- and a detector that returned one code per page
    would silently drop three tables while looking like it had found them. That
    is the same shape as the Dogane appendix defect in AGENTS.md section 4, where
    losing one unclassifiable opening page was enough to invent a table of one.

    Duplicates collapse, so an exhibit whose heading repeats on a continuation
    page ("Tabella 8.8 (parte 1 di 2)" then "(parte 2 di 2)") is yielded once.
    """
    found = []
    for line in lines[:REL_HEADING_SCAN]:
        item = rel_heading(line)
        if item and item not in found:
            found.append(item)
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

# Two prints of the same table are hundreds of pages apart -- the MEF relation
# prints LGP inline and the annex prints it again 178 pages later -- while the
# interruptions inside one table's run are a handful of pages. 40 sits well clear
# of both: the widest interruption measured is 20 pages (the Dogane M table of
# 2025 vol. II), and the narrowest reprint gap is 173.
REPRINT_GAP = 40

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

    Plus one shape that is deliberately not a style of its own: the Dogane
    appendix "TAB. M - APPENDICE" returns style 3 with a suffixed code.

    Returns a single code because a law table occupies a whole page to itself.
    The one space where that is false -- the ministry relation's own numbered
    exhibits, several of which share a page -- is handled by scan_headers, which
    is the only place that can yield more than one code per page.
    """
    # Style 5: checked first and without a vocabulary, because these tables sit
    # outside all three code families and print no table code at all -- the
    # annesso number is the only handle, and it repeats on every page of the
    # run. A code-shaped reading of the surrounding title would otherwise be
    # invented from "TABELLA RIEPILOGATIVA ...".
    m = ANNEXO_RE.search(text)
    if m:
        return m.group(1).upper(), 5

    # The appendix, ahead of every other style. Style 3 would read the bare base
    # code off the very same line and hand the appendix back to the table it
    # belongs to, which is the defect this shape exists to fix.
    #
    # Deliberately NOT gated on classify_page() saying DOG. The page that
    # carries the appendix's own title has the keyword shredded into 'T' /
    # 'AB. M - APPENDICE', so the ontology cannot classify that page either, and
    # gating on it loses the cover page -- which the end derivation then hands
    # straight back to the base table, since one table's end is the next
    # table's start minus one.
    #
    # Style 3 is returned rather than a new number so that MIN_RUN keeps
    # governing it, which is what carries the one-page appendices: the Dogane
    # waive that threshold, and any other ministry still drops a single-page hit
    # as a prose cross-reference. A style of its own would exempt these pages
    # from the rule everywhere.
    appendix = appendix_code(lines)
    if appendix:
        return appendix, 3

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


def page_code_listed(lines, listed, banner):
    """The code of a table the volume's own ELENCO promised, opening this page.

    A detector of its own rather than another style inside page_code(), because
    the witness is different in kind: the volume lists the table, and the page
    opens it. Neither means much alone -- a code can be listed and never printed,
    and "Tabella" appears in prose -- but together they cannot lie about a table
    that does not exist.

    This is what finds the MEF tables the relation prints inline on 2025 vol. II,
    pp 263-267: UE, LGP, IAA, FG and KK1, each titled "Tabella <code> - <title>"
    and each invisible to every other detector, because pypdf returns the title
    shredded into fragments ("Ta" / "bella FG - Finanziamenti-Garanzie per
    intermediari") so INLINE_CODE never sees the keyword whole, and no outline
    names the inline print. They are the pages the DIFESA annesso was swallowing
    whole, and two of them (p263, p267) style 3 was reading as the wrong table
    from a prose mention further up the page.

    Requires all three of: a code the listing named, the art. 27 banner the MEF
    puts on the page, and a real title after the code, which is what separates
    this from "come da elencazione sintetica della tabella KK1" on p263. No
    listing, no detection -- a code nobody promised cannot be manufactured here.
    """
    if not listed or not banner:
        return None
    # The title follows the banner, so the search starts where the banner does
    # rather than at the top of the page. That is what finds IAA on 2025 vol. II
    # p265, where the page carries the tail of LGP's rows first and the new
    # section block -- banner, title and body -- starts two thirds down: read
    # from the top, the title is invisible; read from the banner, it is the
    # first thing after it.
    start = 0
    for i, line in enumerate(lines):
        # Both halves of the banner are matched, because pypdf cuts between them:
        # "Dip" / "artimento del Tesoro Direzione V", and the law line arrives as
        # "Operazio" / "ni disciplinate dall'art. 27".
        if "disciplinate" in line or "ipartimento" in line:
            start = i
            break
    for i in range(start, min(start + TITLE_LINES + 2, len(lines))):
        line = lines[i]
        probes = [line + " " + " ".join(lines[i + 1:i + 3])]
        if i + 1 < len(lines):
            probes.append(line + " " + lines[i + 1])
        for probe in probes:
            m = ELENCO_TITLE.match(probe)
            if m:
                code = m.group(1).upper()
                if any(same_table(code, known) for known in listed):
                    return code

    # The third shape, and the reason the four MEF charts needed a bookmark tree
    # to be found at all. In the annex the code is printed BARE on its own line
    # -- "AA", then "Esportazioni definitive per Istituti di credito", then
    # "Ta€ella" where the euro sign has been substituted for the letter b of
    # Tabella. So the keyword is unusable and only the code is legible:
    #
    #     Ministero dell'Economia e delle Finanze
    #     Dipartimento del Tesoro Direzione V - Ufficio VIII
    #     ELENCO TABELLE
    #     Operazioni disciplinate dall'art. 27, legge 09/07/1990, n. 185 -Smi
    #     AA                          <- the code, alone
    #     Esportazioni definitive per Istituti di credito - Riepilogo generale
    #     Ta€ella                     <- "Tabella", corrupted
    #
    # Gated three ways, all of which are needed. The code must be one the ELENCO
    # promised; the page must carry the MEF banner; and the code must sit in the
    # header region, above the body -- AGENTS.md section 4 records the bare "I" in
    # a Dogane table list, 108 lines down a page, which is exactly this shape
    # without the position. The first table row below the code is never eligible.
    for i, line in enumerate(lines):
        if i >= start + BARE_CODE_MAX_LINE:
            break
        if not CODE_RE.match(line):
            continue
        code = line.upper()
        if any(same_table(code, known) for known in listed):
            return code
    return None


def listed_pages(candidates, listings):
    """({page: code}, {page: ministry}) from the volume's own ELENCO.

    The first is what it says: the tables the listing promised and a page opens.
    The second is what the same pass finds around them -- the pages where a new
    body's section begins. Both come out of the same held pages, and both need
    the listing: a code is only trusted because the listing promised it, and a
    section only counts as the end of the annex because the listing said what
    that annex contains.

    Run over the held pages once the whole volume has been read, because the
    listing names the tables that follow it while a ministry may print one of
    them inline *before* it. So the listing cannot be applied as the scan goes
    past, and the pages it applies to have to be held -- see the comment on
    `candidates` in scan_headers.

    A code found this way REPLACES whatever style 3 read on the page. Style 3 is
    the weakest witness in the pipeline and this is a stronger one, so where they
    disagree the listing wins: on 2025 vol. II p267 style 3 returns FG, read from
    a cross-reference in the prose above the page's own title, while the page
    opens Tabella KK1 and the listing promises KK1.
    """
    if not listings or not candidates:
        return {}, {}
    promised = set()
    for codes in listings.values():
        promised.update(codes)
    found, sections = {}, {}
    for page in sorted(candidates):
        lines, banner = candidates[page]
        code = page_code_listed(lines, promised, banner)
        if code:
            found[page] = code
            continue
        # Not a table, and not the header of one. If it opens another body's
        # section, that is the end of the annex this listing introduced. The
        # banner rides along because is_section_open needs it: the MEF's own
        # pages open with a ministry name too, and only the art. 27 banner
        # separates them from a real section opening.
        opening = is_section_open(lines, promised, banner)
        if opening:
            sections[page] = opening
    return found, sections


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
                 pasted=None, facts=None, shared=None, rel_pages=None):
    """{page: [(code, style)]}, the provisional pages, and corruption tallies.

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
    listings = {}
    # Pages carrying more than one relation exhibit, and every relation exhibit
    # page at all, so the writer can cut them. Filled only when the caller wants
    # them; both are read by split_pdf, which is the only place that writes.
    shared_pages = [] if shared is not None else None
    rel_pages = [] if rel_pages is not None else None
    # The opening lines of every page no other detector claimed, kept so the
    # ELENCO can be consulted against them afterwards. Necessary because a
    # listing names the tables that FOLLOW it, yet a ministry may also print one
    # of those tables inline in its relation, *before* the listing: the MEF does
    # exactly that on 2025 vol. II, where LGP, IAA, FG and KK1 appear on
    # pp 264-267 and again in the annex on pp 388-442. So the listing cannot be
    # applied as the pages go past; it has to be applied at the end, to pages
    # already seen. Holding the opening lines is what makes that possible without
    # extracting every page of the volume a second time -- which, on a
    # 1048-page volume, costs more than the whole rest of the run.
    candidates = {}
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
        if not lines:
            continue
        # An index of tables, read as one. Tested before is_index_page() because
        # that test is the wrong shape for this: it keys on the word
        # "SEGNALAZIONI", and 2021 tom. II prints its first ELENCO page as
        # "ELENCO TABELLE" without it, so the page falls through to the body
        # scan and the first annex table is detected *inside* the index, then
        # clamped to the index's own page. What separates a listing from a table
        # page is the heading and a pageful of codes, which is what this checks;
        # is_index_page() below still catches the other index layouts, which
        # carry no "Tabella <code>" rows to read.
        named = listing_codes(text)
        if named:
            listings[page_no] = named
            continue
        if is_index_page(lines):
            continue
        # An allegato cover or summary lists its own tables by name; treating
        # that listing as table starts invents a dozen tables and truncates the
        # real ones.
        if is_allegato_page(text):
            continue

        code, style = page_code(lines, text, vocabulary, family)
        # Style 7, the ministry relation's own numbered exhibits, and only where
        # no law table claimed the page. Tried second on purpose: a page carrying
        # both an art. 27 code and a relation exhibit would be ambiguous, and the
        # law table is the one the rest of the pipeline, the ministry tree and the
        # index all agree about, so it wins by default rather than by argument.
        rel = [] if code else rel_headings(lines)
        if rel:
            tables_here = [n for kind, n in rel if kind.lower() == REL_TABELLA.lower()]
            for number in tables_here:
                page_codes.setdefault(page_no, []).append(
                    (rel_code(number), REL_STYLE))
            # Every exhibit on the page, chart included, is a band boundary, so
            # the page is recorded as shared even when only one of them is a
            # table. The writer needs the chart's position to cut it away.
            if len(rel) > 1 and shared_pages is not None:
                shared_pages.append((page_no, rel))
            # ...and the page is recorded for cutting in any case, because the
            # prose the ministry prints around an exhibit is on the same page
            # and a whole-page copy ships it too.
            if tables_here and rel_pages is not None:
                rel_pages.append((page_no, rel))
            if tables_here:
                code, style = rel_code(tables_here[0]), REL_STYLE

        # Held for a second look: either the page claims nothing, or what it
        # claims came from style 3, which is the weakest witness there is -- an
        # unbounded scan of the body that cannot tell a running header from a
        # prose mention. Style 6 is stronger, and it is only available once the
        # volume's ELENCO has been read. What is stored is what style 6 needs and
        # nothing more: the opening lines, and whether the MEF section banner is
        # on the page at all, which is a substring test on the squeezed text and
        # is the cheaper of the two.
        if not code or style == 3:
            candidates[page_no] = (lines, ontology.is_mef_section(text))
        if not code:
            unassigned.append(page_no)
            continue

        if not rel:
            page_codes.setdefault(page_no, []).append((code, style))
        # Style 1 also matches summary pages: 2023 vol. II p8 carries a bare
        # "UE" under the same header and would steal the start of the real
        # Tabella UE at p543. A banner-backed page clears the flag, so the
        # table is provisional unless at least one of its pages is backed.
        if style == 1 and "ELENCO TABELLE" not in text:
            weak_pages.add(page_no)
    return (page_codes, weak_pages, unassigned, garbled, ciphered, foreign,
            listings, candidates, shared_pages or [], rel_pages or [])


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
    """({page: authority}, source, block_edges) from the volume's own INDICE.

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

    `block_edges` is the same parse kept *unfiltered* -- every ministry the
    index names, including those that file no tables -- because it answers a
    different question: where a table's span has to stop. It is None when the
    parse is rejected, and so is every clamp built on it.
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
        return None, reason, None
    ranges = indice.authority_ranges(blocks, offset)
    if not ranges:
        return None, "INDICE names no ministry that produces tables", None
    edges = indice.block_edges(blocks, offset, len(reader.pages))
    return ({page: indice.authority_for_page(ranges, page, offset)
             for page in range(1, len(reader.pages) + 1)},
            f"INDICE, {len(ranges)} ministry block(s), folio=page{offset:+d}, "
            f"{coverage:.0%} legible",
            edges)


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
    page_codes, weak_pages, unassigned, garbled, ciphered, foreign, listings, \
        candidates, shared_pages, rel_pages = scan_headers(
            reader, vocabulary, family, stamps, raw_prov, pasted, facts,
            shared=[], rel_pages=[])

    # The ELENCO consulted over the held pages. Style 6: the volume lists the table
    # and the page opens it, which no header shape can do. Where style 3 had also
    # claimed the page, the listing wins -- see listed_pages().
    listed_codes_found, foreign_sections = listed_pages(candidates, listings)
    for page, code in listed_codes_found.items():
        # Replace, not append: the listing is the stronger witness and the page
        # is holding one table, whatever a weaker test may have also read there.
        page_codes[page] = [(code, 6)]
        unassigned = [p for p in unassigned if p != page]
    listed_found = len(listed_codes_found)

    from_indice, source, edges = provenance_from_indice(reader, sibling)
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
    #
    # A code can be detected on two disjoint runs, and that is a real shape rather
    # than an error: the MEF prints a table once in its relation, inline, and
    # again in the annex its ELENCO introduces. 2025 vol. II does that with UE
    # (p263 and p446), LGP (p264 and p442), IAA (p265 and p388), FG (p266 and
    # p340) and KK1 (p267 and p441), each inline print carrying its own title and
    # section banner. Joining the runs would give one table a span covering every
    # page between them -- most of the relation prose -- so they are kept apart
    # and the table keeps one of them.
    #
    # Which copy it keeps is the volume's to decide, and the volume's own ELENCO
    # is the first witness: it names the table as one of those that follow the
    # listing, so the annex print is the one indexed and the inline print in the
    # relation is given up. Failing that the outline, then the longer run, then
    # the earlier. The prints given up are reported, never dropped silently.
    groups = {}
    for page in sorted(page_codes):
        # Usually one code per page. Two is possible and real: the relation
        # prints 8.3 beside 8.4 on p40, so a page can open more than one table
        # and each gets its own group, its own span and its own output file.
        for code, style in page_codes[page]:
            authority = provenance.get(page, ontology.UNKNOWN)
            article = articles.get(page)
            tkey = (authority, article, code)
            entry = groups.setdefault(tkey, {"pages": [], "style": style,
                                             "runs": []})
            entry["pages"].append(page)
            if entry["runs"] and page == entry["runs"][-1][-1] + 1:
                entry["runs"][-1].append(page)
            else:
                entry["runs"].append([page])
            if style == 1 and page not in weak_pages:
                weak_pages.add(page)

    repeated_prints = []
    split_groups = {}
    for tkey, entry in groups.items():
        runs = entry["runs"]
        # Two kinds of gap separate two prints of one table.
        #
        # A WIDE one, where nothing else explains the silence. A table's pages do
        # not all carry its code -- the Dogane's M runs 250 pages with 20 of them
        # silent -- so a small gap means an interruption, not a second print. The
        # relation print and the annex print of the same MEF table are hundreds of
        # pages apart, which no such interruption reaches; 40 is well clear of the
        # widest interruption measured and well inside the gap it must catch.
        #
        # And one that CONTAINS A LISTING PAGE. The ELENCO is the volume's own
        # index of the tables that follow it, so a run of table pages cannot
        # continue across it: whatever came before is a different thing from
        # whatever comes after. That is the only thing separating two prints ten
        # pages apart in 2021 vol. II, where the MEF relation quotes four saldi
        # rows as bare codes at p6-9 and the annex prints the real Tabella AA at
        # p16, with the listing on p13-15 in between.
        far = []
        for i in range(1, len(runs)):
            gap = range(runs[i - 1][-1] + 1, runs[i][0])
            if runs[i][0] - runs[i - 1][-1] > REPRINT_GAP \
                    or any(p in listings for p in gap):
                far.append(i)
        if not far:
            split_groups[tkey] = entry
            continue
        # Split the run list at each wide gap, then keep the cluster the volume
        # itself points at.
        clusters = [[runs[0]]]
        for i in far:
            clusters.append([runs[i]])
        marked = embedded.get(tkey[2])
        # The annex copy comes first in the ranking, and the reason is the
        # ELENCO: it names this table as one of the tables that follow the
        # listing, so the print after the listing is the copy the volume
        # indexes. The inline print in the relation is a second printing of the
        # same table, and it is the one given up. Only where the volume has no
        # listing does the outline decide, then the longer run, then the earlier.
        promised_after = [p for p in sorted(listings)
                          if any(same_table(tkey[2], c)
                                 for c in listings[p])]
        after = promised_after[-1] if promised_after else None
        kept = max(clusters, key=lambda grp: (
            after is not None and grp[-1][-1] > after,
            marked is not None and any(r[0] <= marked <= r[-1] for r in grp),
            sum(len(r) for r in grp), -grp[0][0]))
        for grp in clusters:
            if grp is kept:
                continue
            pages = [p for r in grp for p in r]
            repeated_prints.append({
                "key": list(tkey), "start": pages[0], "end": pages[-1],
                "pages": len(pages),
                "kept_from": kept[0][0], "kept_to": kept[-1][-1],
                "reason": "stessa tabella stampata due volte nel volume",
            })
        entry["pages"] = [p for r in kept for p in r]
        entry["runs"] = kept
        split_groups[tkey] = entry
    groups = split_groups

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
    # MIN_RUN applies to style 3 only -- and so, automatically, not to style 6:
    # the threshold tests the style, and a table the volume's own ELENCO lists
    # and the page opens is not a prose cross-reference however few pages it
    # repeats its code on. The five MEF tables the 2025 relation prints inline
    # are one page each, so the threshold would reject all five.
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
    trimmed, outside = {}, {}
    for idx, (tkey, info) in enumerate(ordered):
        code = tkey[2]
        # Placement first: it decides the path, and the per-table log lines
        # below name the table by that path. The majority runs over the pages a
        # detector actually saw, not over the derived span, which reaches to the
        # next table's start and would let a neighbour that inherited into the
        # tail outvote the table itself.
        seen = info.get("seen") or [info["start"]]
        info["authority"], info["article"] = resolve_placement(
            seen, provenance, articles)
        info.pop("seen", None)
        where = "/".join(filter(None, [info["authority"], info["article"], code]))
        # The last page on which a detector actually read this table's code, as
        # a floor no clamp may go below.
        floor = max(seen)

        end = ordered[idx + 1][1]["start"] - 1 if idx + 1 < len(ordered) else last_page
        info["end"] = max(info["start"], end)
        # What the derivation alone would have produced. Both clamps report
        # against this, so "how many pages did we take off" stays a single
        # number however many witnesses fired.
        derived = info["end"]

        # The relation's own exhibits are the one case where "the end is where
        # the next table starts" is not merely approximate but simply wrong, and
        # the difference is not one page. These tables sit inside the narrative
        # that cites them, so prose sits between them: Tabella 1 is on p25,
        # Tabella 2 on p27, and p26 is the section 8.1 running text. Next-start
        # minus one would hand Tabella 1 that page and ship a file whose second
        # page is an essay.
        #
        # So the end is the last page on which the exhibit's own heading was read
        # -- which is what `floor` already is -- extended by nothing else. The
        # archive prints exactly one exhibit that runs on, and it declares it:
        # "Tabella 8.8 (parte 1 di 2)" and "(parte 2 di 2)" on pp 43-44, and
        # those two pages both carry the heading, so they group and the floor
        # covers them. Every other exhibit in 2025 vol. I is one page.
        #
        # The derived end is still recorded above, because the clamps below
        # report against it and a table whose floor is past it would otherwise
        # look like it gained pages.
        if info.get("style") == REL_STYLE:
            info["end"] = max(info["start"], floor)

        # Deriving the end from the next start assumes the next table starts
        # where the next table starts. When it does not -- when the page after
        # this table belongs to another ministry's section, which the document's
        # own index states outright -- the derived end reaches into somebody
        # else's pages and a file grows prose it has nothing to do with. The
        # index is a better witness than the next start, so it wins, and the
        # pages it claims are dropped from this table.
        #
        # Never below the last page on which the table's own code was actually
        # read: if the index and the page furniture disagree about a table, the
        # disagreement is left visible in the report rather than resolved by
        # deleting the table's own pages.
        block = block_at(edges, info["start"]) if edges else None
        if block is not None:
            # Scanned forwards from the floor, not backwards from the end: the
            # first page past the table that the index gives to somebody else is
            # the boundary. A backwards scan would find the *last* page of the
            # next ministry's block and report the whole of it as trimmed.
            boundary = next((p for p in range(floor + 1, info["end"] + 1)
                             if block_at(edges, p) != block), None)
            if boundary is not None:
                # The boundary page belongs to the NEXT ministry -- that is what
                # makes it the boundary -- so it is given up with the pages the
                # clamp is discarding, exactly as the two clamps below do
                # (ministry_break - 1, listing_page - 1). Keeping it shipped one
                # page of somebody else's section on the end of this file:
                # 2025 vol. II UE ran to p457, which the index gives to DOG and
                # which prints "DIREZIONE DOGANE / Ufficio controlli dogane",
                # and DIFESA annesso 4 ran to p259, the INTERNO leaf. The count
                # moves with it, so the report says how many pages were dropped.
                record_boundary(outside, where, boundary,
                                block_at(edges, boundary),
                                derived - boundary + 1)
                info["end"] = boundary - 1

        # Third boundary, and the one that catches a volume with no INDICE at all: the
        # ELENCO is only ever printed at the HEAD of an annex, so the section it
        # introduces ends where the next ministry's own opening page begins. The
        # witness is a page whose first lines name a ministry and print neither a
        # table code nor a table title -- the shape of "DIREZIONE DOGANE /
        # Ufficio controlli dogane / R E L A Z I O N E" on 2021 vol. II p586,
        # which is 367 pages of UE table past its last row.
        #
        # Narrow on purpose: the line must sit in the header region and the page
        # must carry no code the listing promised, because the MEF's own pages
        # open with "Ministero dell'Economia e delle Finanze" and are tables.
        ministry_break = next((
            p for p in range(max(floor, info["start"]) + 1, info["end"] + 1)
            if p in foreign_sections), None)
        if ministry_break is not None:
            record_boundary(outside, where, ministry_break,
                            foreign_sections[ministry_break],
                            derived - ministry_break + 1)
            info["end"] = ministry_break - 1

        # Second, independent boundary: the ELENCO TABELLE SEGNALAZIONI. The
        # ministry prints it immediately before its own table annex, naming the
        # tables that follow, so a table detected before a listing page cannot
        # reach past it. On 2025 vol. II the DIFESA annesso 4 detected at p254
        # took the derived end p272, which swallowed the MEF relation, its three
        # inline tables and the entire listing. The listing page is a boundary
        # the document prints itself, which makes this clamp independent of the
        # INDICE: a volume with no INDICE still gets it.
        #
        # Only the FIRST listing page inside the span is a boundary. A listing
        # continues over several pages (2025 vol. II: p270-272), and the later
        # ones are pages of the listing, not a further section starting.
        if listings:
            listing_page = next((p for p in sorted(listings)
                                 if info["start"] <= p <= info["end"]), None)
            if listing_page is not None and listing_page > floor:
                # The listing itself belongs to no table, so it is not a boundary
                # on its own: what the table must not cross is the ministry's
                # change of section, which the listing marks. So the clamp stops
                # the table one page short of the first page of the section that
                # follows, and the pages between are left unassigned rather than
                # attributed to a table they do not belong to.
                record_boundary(outside, where, listing_page, None,
                                derived - listing_page + 1)
                info["end"] = listing_page - 1

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

    # Codes the volume's own ELENCO says exist but no detector found. Reported
    # rather than invented: an index page names the tables that follow it, so a
    # code on one with no table anywhere after it is a detection failure, and
    # where it was named is exactly the information needed to go and look.
    listed_missing = []
    if listings:
        found = {code for _a, _art, code in final}
        for page in sorted(listings):
            for code in listings[page]:
                if not any(same_table(code, seen) for seen in found):
                    listed_missing.append((page, code))

    return {
        "family": family,
        "page_count": last_page,
        "provenance_source": source,
        "listings": {p: c for p, c in sorted(listings.items())},
        "listed_missing": listed_missing,
        "listed_found": listed_found,
        "listed_tables": len({code for hits in page_codes.values()
                              for code, s in hits if s == 6}),
        "shared_pages": [{"page": p,
                          "exhibits": [[k, n] for k, n in rel]}
                         for p, rel in sorted(shared_pages)],
        "rel_pages": [{"page": p,
                       "exhibits": [[k, n] for k, n in rel]}
                      for p, rel in sorted(rel_pages)],
        "repeated_prints": repeated_prints,
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
        "boundary": outside,
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

# The rotation that brings a page's writing direction upright, as the argument
# show_pdf_page() takes it. Not the frame's own name: that is a description of
# where the text points, this is how far to turn the page to fix it.
_FRAME_ROTATE = {"upright": 0, "rot90": 270, "rot180": 180, "rot270": 90}

# Blank border left around a cut exhibit, in points. Generous because the rule
# layer is measured exactly and the text is not: a hairline sits on the table
# edge and the last column of digits butts against it, so a tight crop clips
# the figure.
CUT_MARGIN = 20.0


def _pymupdf():
    """The geometry library, imported lazily.

    Only the pages that carry more than one relation exhibit need it, and only
    when a run actually writes. pypdf does the rest of step 1, so an import at
    module scope would make pymupdf a hard dependency of a detection-only run
    such as --dry-run.
    """
    try:
        import pymupdf
    except ImportError:
        return None
    from lib import geometry
    return pymupdf, geometry


def cut_exhibit_page(volume_path, page_no, keep_code, out_path, manifest,
                     volume_index):
    """Write one relation exhibit off a page that carries more than one.

    2025 vol. I prints two exhibits on a page three times over: 8.3 beside 8.4
    on p40, 8.6 beside 8.7 on p42, and 18 stacked above 19 on p60. At page
    grain they cannot be separated -- one physical page, two tables -- so the
    page is cut along the bands the exhibits themselves print.

    Four things this has to get right, each of which was wrong in a first
    attempt and is recorded because none of them fails loudly:

      * the CUT has to be real, not just a clip. `show_pdf_page(clip=...)` leaves
        the text layer whole, so pypdf still read "Tabella 8.3" out of the 8.4
        file -- both files contained both tables. Only apply_redactions()
        rewrites the content stream and deletes what is outside;
      * the tables are printed ROTATED 90 degrees inside a portrait page, so the
        page is turned upright first. 48 of 2025's tables are like this;
      * the turn has to come from lib/geometry.Frame, which reads the direction
        off the content stream's text matrix, rather than from a guess at the
        page. p40 and p42 come out rot90 and p60 upright, and the band is taken
        along "down the page in reading space", which is the same axis for both;
      * the crop must not be measured from get_text("words"), which splits words
        on the PAGE axes and so under-reports rotated text: it found
        "D'AMERICA" but not the "STATI UNITI" before it, and a crop computed
        from it silently cut the label to "MITI D'AMERICA". The crop is taken
        in the upright frame, where the axes agree with the text.

    `keep_code` is the exported code ("T83"); the exhibit it names is found by
    its printed number ("8.3"), read from the manifest rather than guessed back
    out of the code. A page whose band cannot be isolated returns False rather
    than writing a file that would hold two tables.
    """
    libs = _pymupdf()
    if libs is None:
        return False
    pymupdf, geometry = libs

    document = pymupdf.open(volume_path)
    try:
        source = document[page_no - 1]
        frame = geometry.Frame.for_page(source)
        turn = _FRAME_ROTATE.get(frame.name)
        if turn is None:
            return False
        # Page size after the turn: a quarter turn swaps the two.
        if turn in (90, 270):
            width, height = source.rect.height, source.rect.width
        else:
            width, height = source.rect.width, source.rect.height

        # Draw the page upright. Everything after this is axis-aligned, which is
        # what makes both the band arithmetic and the text extraction reliable.
        work = pymupdf.open()
        canvas = work.new_page(width=width, height=height)
        canvas.show_pdf_page(pymupdf.Rect(0, 0, width, height), document,
                             page_no - 1, rotate=turn)

        wanted = printed_number(keep_code, manifest, volume_index)
        headings = exhibit_headings(canvas)
        numbers = [h[1] for h in headings]
        if wanted is None or wanted not in numbers:
            return False
        index = numbers.index(wanted)

        # A band runs from its OWN heading down to the next heading, and the last
        # band runs to the foot of the page. Starting at the previous heading
        # instead -- which is the obvious thing to write, and what a first
        # attempt did -- keeps the *previous* exhibit's body, because a body sits
        # between its heading and the next one.
        #
        # Starting at ZERO, which is what this did next, is wrong for the same
        # reason one step further out: the ministry prints the narrative that
        # introduces an exhibit onto the same page, above it. p63 carried 17 lines
        # of section 8.3.2 ahead of Tabella 21, and p45 six ahead of Tabella 9.
        # The table is what is wanted, so the band starts at the heading.
        #
        # Charts are boundaries exactly as tables are, so a chart sharing the page
        # is cut away rather than left in the file: 12 pages of 2025 vol. I carry
        # both a table and a chart, and none of the charts is exported.
        lo = headings[index][2] - CUT_MARGIN
        hi = (headings[index + 1][2] - 2.0
              if index + 1 < len(headings) else float(height))
        for band in (pymupdf.Rect(0, 0, width, lo),
                     pymupdf.Rect(0, hi, width, height)):
            if band.is_empty or band.is_infinite:
                continue
            canvas.add_redact_annot(band, fill=False)

        # The volume furniture goes with the prose. On a rotated page it runs
        # vertically while the table runs horizontally, so it sits INSIDE the
        # band's y-range and no horizontal cut can reach it. What separates them
        # is the writing direction, and it separates them cleanly: measured on
        # 2025 vol. I p40, the table is 1689 characters one way and the furniture
        # 262 the other, with nothing in between. The tally survives the rotation
        # -- (0,-1) and (1,0) become (1,0) and (0,1) -- so it can be read on the
        # canvas the band was cut on.
        drop_minority_direction(canvas)

        canvas.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            # The archive draws its grids as one-point rectangles, so a rule is
            # removed when the band TOUCHES it, not only when it covers it: the
            # stricter test leaves 616 orphaned rules and the page reads as an
            # empty grid beside the table.
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE)

        # Crop to what survived, and write it at that size rather than leaving a
        # page of A4 with a table in one corner of it.
        box = None
        for word in canvas.get_text("words"):
            rect = pymupdf.Rect(word[:4])
            box = rect if box is None else box | rect
        for drawing in canvas.get_drawings():
            box = box | drawing["rect"]
        if box is None or box.is_empty:
            return False
        crop = pymupdf.Rect(box.x0 - CUT_MARGIN, box.y0 - CUT_MARGIN,
                            box.x1 + CUT_MARGIN, box.y1 + CUT_MARGIN) & canvas.rect
        out = pymupdf.open()
        page = out.new_page(width=crop.width, height=crop.height)
        page.show_pdf_page(pymupdf.Rect(0, 0, crop.width, crop.height),
                           work, 0, clip=crop)
        out.save(out_path)
        out.close()
        work.close()
        return True
    finally:
        document.close()


def drop_minority_direction(page):
    """Redact everything not written along the page's dominant direction.

    Used to take the volume furniture off a rotated table page. It annotates;
    the caller applies, because a redaction pass rewrites the content stream and
    is worth doing once per page rather than once per span.

    pymupdf is imported here rather than at module scope on purpose: it is only
    needed to CUT a page, and a --dry-run detection pass must not require it.
    """
    libs = _pymupdf()
    if libs is None:
        return
    pymupdf, _geometry = libs
    import math

    tally = Counter()
    spans = []
    for span in page.get_texttrace():
        chars = span.get("chars") or []
        if not chars:
            continue
        dx, dy = span.get("dir", (1.0, 0.0))
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        key = (round(dx / length, 3), round(dy / length, 3))
        tally[key] += len(chars)
        spans.append((key, span.get("bbox")))
    if len(tally) < 2:
        return
    (keep, _total), = tally.most_common(1)
    for key, bbox in spans:
        if key == keep or not bbox:
            continue
        page.add_redact_annot(pymupdf.Rect(bbox), fill=False)


def exhibit_headings(page):
    """[(kind, number, y0, y1)] for each exhibit heading on an UPRIGHT page.

    Read as LINES with the same anchored pattern rel_headings() uses on pypdf
    text, not by looking for a number near the word "Tabella". The adjacency
    guess is wrong on an unrotated page: there the number sits to the RIGHT of
    the keyword on the same baseline, not below it, so a test that looks
    downwards picks up whatever digit happens to be underneath -- on 2025 vol. I
    p45 it read "Grafico 2022" and "Tabella 2025" (the year column headings),
    cut the wrong bands, and shipped a file with no table in it. Matching the
    line is the same discriminator that makes detection safe, applied twice.

    Only valid once the page has been turned, which is why cut_exhibit_page()
    does the turning itself rather than handing a raw volume page here.
    """
    found = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "")
                           for span in line.get("spans", [])).strip()
            m = REL_HEADING.match(text)
            if m:
                x0, y0, _x1, y1 = line["bbox"]
                found.append((m.group(1).capitalize(), m.group(2), y0, y1))
    found.sort(key=lambda heading: heading[2])
    return found


def printed_number(code, manifest, volume_index):
    """The printed number behind an exported code: 'T83' -> '8.3'.

    Not derivable from the code -- 'T83' could be Tabella 83 or Tabella 8.3 and
    nothing in the string says which -- so it is read from the manifest, where
    scan_headers recorded the exhibits as printed. Read from rel_pages, which
    lists every relation exhibit page and not only the shared ones, because
    every one of them is cut now.
    """
    for entry in manifest.get("rel_pages") or []:
        for kind, number in entry["exhibits"]:
            if rel_code(number) == code:
                return number
    return None


def table_plan(readers, manifests):
    """{(authority, article, code): [(volume_index, start, end)]}, the pages
    each exported PDF will contain.

    The single source of truth for "which pages does the output own". Both the
    writer (split_pdf) and the trash dump (dump_trash) read it, so the two
    cannot drift into disagreeing about what was written -- a second,
    independently reconstructed notion of the claimed pages is exactly how a
    coverage report starts lying.

    A copy settled away by resolve_repeats is skipped, so its pages are claimed
    by nobody and land in the trash; that is the point. The leading pages of a
    table that continues into the next volume come from that volume's "continued"
    entry and are attributed to it, so a page is never claimed twice and never
    claimed by the volume it does not physically belong to.

    Volumes are referenced by index rather than by reader object, so a caller
    can hold the reader alive and compare plans without relying on identity.
    """
    plan = {}
    for idx, manifest in enumerate(manifests):
        for tkey, info in manifest["tables"].items():
            if info.get("kept") is False:
                continue
            plan.setdefault(tkey, []).append((idx, info["start"], info["end"]))
        for tkey, cont in manifest.get("continued", {}).items():
            plan.setdefault(tkey, []).append((idx, cont["start"], cont["end"]))
    return plan


def split_pdf(readers, manifests, out_root, year, paths=None):
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

    The plan comes from table_plan(), which dump_trash() reads as well.
    """
    plan = table_plan(readers, manifests)

    # Every relation exhibit is cut, not only the ones sharing a page. An exhibit
    # that has a page to itself is no cleaner for it: the ministry writes the
    # narrative that introduces the exhibit onto the same page, so copying the
    # page whole shipped the prose too -- 17 lines of it ahead of Tabella 21 on
    # p63. Cutting every one of them is the same code and 31 pages instead of 10.
    rel_pages = {}
    for idx, manifest in enumerate(manifests):
        for entry in manifest.get("rel_pages") or []:
            rel_pages.setdefault(idx, set()).add(entry["page"])

    written, cut_pages, cut_failed = [], [], []
    for (authority, article, code), segments in plan.items():
        # Where the table came from, in the filename. The FIRST segment, so a
        # table crossing the volume join names the volume it starts in -- 2025
        # F1 is V1 p1039, not the vol. II half it continues into.
        first_idx, first_start, _first_end = segments[0]
        rel = ontology.relative_path(authority, article, code, year,
                                     first_idx + 1, first_start, PDF_EXT)
        path = Path(out_root, OUT_DIR, "PDF", rel)
        path.parent.mkdir(parents=True, exist_ok=True)

        # A table whose every page needs cutting is written entirely by the cut,
        # because a pypdf writer and a pymupdf page cannot be mixed in one file
        # without going through bytes. That is the common case here: the six
        # exhibits that share a page are the only ones cut, and each is one page.
        cut_pages_here = []
        for idx, start, end in segments:
            if paths is None:
                continue
            for p in range(start, end + 1):
                if p in rel_pages.get(idx, ()):
                    cut_pages_here.append((idx, p))

        if cut_pages_here and paths is not None and len(cut_pages_here) == sum(
                end - start + 1 for _idx, start, end in segments):
            # Every page of this file is a cut page, which is the case for every
            # relation exhibit: they are one page long, and their page is cut so
            # that the prose the ministry prints around them stays out. The cut
            # is written by pymupdf and read back by pypdf, so it goes via a
            # temporary file rather than being merged in memory -- the two
            # libraries do not share a page object.
            merged = PdfWriter()
            ok = True
            scratch = tempfile.mkdtemp(prefix="relazione-cut-")
            try:
                for n, (idx, p) in enumerate(cut_pages_here):
                    tmp = Path(scratch, f"{n}.PDF")
                    if not cut_exhibit_page(paths[idx], p, code, str(tmp),
                                            manifests[idx], idx):
                        ok = False
                        cut_failed.append((code, p))
                        break
                    merged.add_page(PdfReader(str(tmp)).pages[0])
                    cut_pages.append((code, p))
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            if ok:
                with open(path, "wb") as fh:
                    merged.write(fh)
                written.append(str(path))
                continue

        writer = PdfWriter()
        for idx, start, end in segments:
            reader = readers[idx]
            for p in range(start, end + 1):
                if paths is not None and p in rel_pages.get(idx, ()):
                    # A cut page inside a file that also holds whole pages: the
                    # whole pages go in as they are, and the cut page is left out
                    # rather than silently carrying prose. Reported. Reached only
                    # by a multi-page exhibit, since a single-page one takes the
                    # branch above.
                    cut_failed.append((code, p))
                    continue
                writer.add_page(reader.pages[p - 1])
        with open(path, "wb") as fh:
            writer.write(fh)
        written.append(str(path))

    if cut_pages:
        print(f"    cut          : {len(cut_pages)} relation exhibit page(s) cut "
              f"off a shared page")
    if cut_failed:
        print(f"    cut WARNING  : {len(cut_failed)} shared page(s) not exported "
              f"whole: {', '.join(f'{c}@p{p}' for c, p in cut_failed[:6])}")
    return written


# ==========================================================================
# the pages no table owns
# ==========================================================================
#
# What lands here is the complement of table_plan(), not a second detection
# pass. Every page the exported PDFs contain is claimed by exactly one of them,
# so whatever is left over is by definition unattributed -- and the arithmetic is
# a closed system: claimed + trashed == every physical page of the volume, which
# is asserted per volume below rather than hoped for.
#
# It is NOT the same thing as manifest["unassigned_pages"], which counts pages no
# detector read a code on. Almost all of those sit INSIDE a derived span: ends
# come from the next table's start, so a table's pages need not repeat its code.
# And it is not the over-extension check either -- a page a table wrongly
# swallowed (Tabella M holding TAB N) is attributed, so it never appears here.
# That failure belongs to VerifyTables.check_page_overlap().

TRASH_DIR = "TRASH"

# Reason slugs, printed in the report and written to the TSV. An empty note means
# the slug is the whole explanation.
TRASH_REASONS = {
    "front-matter": "copertina, INDICE, frontespizio: precede la prima tabella",
    "volume-tail": "dopo l'ultima tabella: PAGINA BIANCA, chiusura, allegati",
    "elenco": "ELENCO TABELLE SEGNALAZIONI della pagina",
    "gazzetta-pasted": "pagina Gazzetta Ufficiale incollata nel volume",
    "cut-at-boundary": "ritagliata: la tabella si ferma al confine che il volume stampa",
    "trimmed-tail": "pagine finali tolte dalla tabella (stamp o PAGINA BIANCA di un altro documento)",
    "dropped-copy": "copia scartata: stesso codice in piu' volumi, non uno spezzamento",
    "given-up-reprint": "seconda stampa scartata: la stessa tabella stampata due volte nel volume",
    "no-detector": "nessun rilevatore ha rivendicato questa pagina",
}


def claimed_pages(plan, volume_count, page_counts):
    """{volume_index: {page: [table keys claiming it]}}.

    A page claimed twice is possible in principle -- the spans are contiguous by
    construction, so it should not be -- and the caller reports it rather than
    letting it be counted as either attributed or trashed.
    """
    claims = [{} for _ in range(volume_count)]
    for tkey, segments in plan.items():
        for idx, start, end in segments:
            # Clipped to the volume: a span that runs past the last page is a
            # defect to report, not an IndexError to raise at the last volume of
            # the run. See dump_trash(), which prints the overage.
            last = page_counts[idx]
            for p in range(max(1, start), min(end, last) + 1):
                claims[idx].setdefault(p, []).append(tkey)
    return claims


def span_past_end(plan, volume_count, page_counts):
    """{volume_index: [(code, end)]} for spans reaching past their volume.

    build_manifest clamps every end to the page count, so this is empty in
    practice. It exists because claimed_pages() clips defensively, and a silent
    clip would hide the very defect it was protecting against.
    """
    over = {}
    for tkey, segments in plan.items():
        for idx, _start, end in segments:
            if idx < volume_count and end > page_counts[idx]:
                over.setdefault(idx, []).append((tkey[2], end))
    return over


def trash_runs(claimed, page_count):
    """Maximal [start, end] ranges of pages no table claims, in page order."""
    runs, start = [], None
    for p in range(1, page_count + 1):
        if p in claimed:
            if start is not None:
                runs.append((start, p - 1))
                start = None
        elif start is None:
            start = p
    if start is not None:
        runs.append((start, page_count))
    return runs


def trash_reason(manifest, start, end, tables):
    """(slug, note) for one unattributed run.

    Only witnesses the manifest records exactly are asserted. `boundary` and
    `dropped` name real page numbers and `listings`/`pasted_pages` are page
    sets, so a match is a fact. `trimmed` records only a COUNT, so it is
    consulted by adjacency -- the run begins where a trimmed table's own pages
    stop -- and the note names the table rather than claiming a page list.

    Order is most-specific first. A run that satisfies no witness is reported as
    `no-detector`, which is the honest answer and not a failure: a volume
    legitimately holds pages no table owns.
    """
    # Exact page sets. The whole run has to be inside the set: a run that is
    # only partly pasted Gazzetta is a mixed region, and calling it Gazzetta
    # would misdescribe the pages that are not.
    #
    # A PARTIAL overlap is not discarded, it is reported on whichever reason the
    # run earns. 2025 vol. II p194-235 is one run of 42 pages of which 31 are
    # the Gazzetta block: the run opens where MAE/P2 was cut, so that is what it
    # is called, and the Gazzetta pages inside it are named -- otherwise the
    # folder would say "42 pages, no reason" and hide both facts.
    inside = []
    for slug, pages in (("gazzetta-pasted", manifest.get("pasted_pages") or []),
                        ("elenco", manifest.get("listings") or {})):
        known = set(pages)
        if not known:
            continue
        hit = [p for p in range(start, end + 1) if p in known]
        if len(hit) == end - start + 1:
            return slug, ""
        if hit:
            covered = (f"{hit[0]}-{hit[-1]}" if len(hit) > 1 else str(hit[0]))
            inside.append(f"{len(hit)}p {slug} ({covered}) dentro il blocco")

    def note(base=""):
        """The run's own reason, plus whatever known region sits inside it."""
        return "; ".join(filter(None, [base] + inside))

    # Exact spans, one run to one dropped copy or one discarded reprint.
    for tkey, info in (manifest.get("dropped") or {}).items():
        if info["start"] == start and info["end"] == end:
            where = "/".join(filter(None, tkey))
            return "dropped-copy", note(f"{where}, tenuta la copia di "
                                        f"{info['kept_from']}")
    for info in manifest.get("repeated_prints") or []:
        if info["start"] == start and info["end"] == end:
            where = "/".join(str(part) for part in info["key"] if part)
            return "given-up-reprint", note(
                f"{where}, tenuta la stampa "
                f"p{info['kept_from']}-{info['kept_to']}")

    # A run opening exactly on a boundary the volume itself printed is what that
    # clamp gave up. The authority is the one the index named, when it did.
    for key, info in (manifest.get("boundary") or {}).items():
        if start in info["pages"]:
            who = info.get("authority")
            return "cut-at-boundary", note(
                f"{key} fermata a p{start}"
                + (f", il volume assegna p{start} a {who}" if who else ""))

    # Adjacency only: the pages immediately after a table that had its tail
    # stripped off are those stripped pages, when nothing else claims them. The
    # manifest records a COUNT here and no page numbers, so the match is made on
    # position and bounded by the count -- an adjacency match larger than the
    # count recorded is somebody else's pages and is not claimed.
    #
    # The adjacency is tested against the table NAMED BY THE KEY, not against
    # every table in the volume. Testing against all of them made any page that
    # happened to follow any table look like a trimmed tail, and labelled it with
    # whichever key came first in dict order: 2025 vol. I reported eleven pages of
    # relation prose as "MAE/E, -1p finale/i", which is a sentence about a table
    # 800 pages away. It stayed hidden while those pages sat inside derived spans,
    # and surfaced the moment the relation's exhibits stopped claiming them.
    for key, count in (manifest.get("trimmed") or {}).items():
        info = by_path.get(key)
        if info and info["end"] + 1 == start and end - start + 1 <= count:
            return "trimmed-tail", note(f"{key}, -{count}p finale/i")

    if start == 1:
        return "front-matter", note()
    if end == manifest.get("page_count"):
        return "volume-tail", note()
    return "no-detector", note()


def dump_trash(volumes, plan, out_root, year, write=True):
    """Write Out/TRASH/<anno>/<volume>_p<a>-<b>.PDF for every unattributed run.

        Out/TRASH/2025/2025_LXVII_n4_VOLUME_II_p001-066.PDF
        Out/TRASH/2025/2025_LXVII_n4_VOLUME_II.tsv

    One file per contiguous run, because a run is the unit a person triages: the
    pages between two tables, or the block of Gazzetta pages bound into the
    volume, is one thing to look at. The TSV beside them gives every run its page
    range, size and reason, which is what makes the folder greppable rather than
    a pile of PDFs.

    `write=False` prints the accounting and writes nothing, so --dry-run still
    reports the coverage -- which is the number a reader most wants from a run
    that cannot be trusted yet.

    Two things this deliberately does not do. It does not decide that an
    unattributed page is a defect: most are not (cover, INDICE, relation prose,
    Gazzetta pages), so the reason is reported rather than judged. And it does
    not reproduce the verifier's over-extension check -- a page a table wrongly
    swallowed is attributed and never reaches here.

    Guarded per volume, for the reason build_manifest is: one volume must not
    cost the others.
    """
    page_counts = [len(v["reader"].pages) for v in volumes]
    claims = claimed_pages(plan, len(volumes), page_counts)
    overage = span_past_end(plan, len(volumes), page_counts)

    written = []
    for idx, volume in enumerate(volumes):
        # Guarded per volume, for the reason build_manifest is: the trash is a
        # review artefact and must never be the thing that costs a run its PDFs.
        # The per-table PDFs are already on disk by the time this is called.
        try:
            written += dump_volume_trash(idx, volume, claims[idx],
                                         overage.get(idx, []), out_root,
                                         year, write)
        except Exception as exc:
            print(f"    TRASH FAILED for {volume['label']} (artefacts are safe): "
                  f"{type(exc).__name__}: {exc}")
    return written


def dump_volume_trash(idx, volume, claimed, overage, out_root, year, write):
    """One volume's share of dump_trash(). Returns the paths written."""
    manifest = volume["manifest"]
    reader = volume["reader"]
    stem = volume["label"]
    page_count = len(reader.pages)
    written = []

    # A span running past the volume's last page is reported, not clipped
    # silently. It cannot happen from build_manifest, which clamps every end to
    # the page count, so a non-empty list is a real finding.
    if overage:
        print(f"    SPAN PAST END: {len(overage)} span(s) reach past p{page_count}"
              f" in {stem} (clipped when the trash was written): "
              + ", ".join(f"{code} p{end}" for code, end in overage[:6]))

    # The closed system: assert the arithmetic before writing anything, so a
    # mismatch is a reported defect rather than a silently wrong folder.
    twice = sorted(p for p, keys in claimed.items() if len(keys) > 1)
    runs = trash_runs(claimed, page_count)
    trashed = sum(end - start + 1 for start, end in runs)
    covered = len(claimed) + trashed
    if covered != page_count:
        print(f"    TRASH ACCOUNTING FAILED for {stem}: {covered} pages "
              f"accounted for out of {page_count}")
    if twice:
        print(f"    DOUBLE-CLAIMED: {len(twice)} page(s) claimed by two "
              f"tables in {stem} (first p{twice[0]}) -- not trashed")

    print(f"    trash       : {trashed:5} pages unattributed in "
          f"{len(runs)} run(s) of {page_count} "
          f"({trashed / page_count:.0%} of the volume)")
    if runs:
        print(f"                 -> {Path(out_root, OUT_DIR, TRASH_DIR, year)}/"
              f"{stem}_p<a>-<b>{PDF_EXT} + {stem}.tsv")

    rows = []
    for start, end in runs:
        slug, note = trash_reason(manifest, start, end, manifest["tables"])
        rows.append((start, end, end - start + 1, slug, note))

    if not write or not rows:
        return written

    folder = Path(out_root, OUT_DIR, TRASH_DIR, year)
    folder.mkdir(parents=True, exist_ok=True)
    # Clear this volume's own previous trash before writing, so a run that now
    # yields fewer blocks does not leave the old ones behind: a stale file here
    # would be indistinguishable from a current one. Scoped to this volume's stem
    # and to _p*-PDF / .tsv -- never a recursive delete, and never anything in
    # Out/PDF.
    for stale in folder.glob(f"{stem}_p*{PDF_EXT}"):
        stale.unlink()
    for stale in folder.glob(f"{stem}*.tsv"):
        stale.unlink()

    names = []
    for start, end, size, slug, note in rows:
        writer = PdfWriter()
        for p in range(start, end + 1):
            writer.add_page(reader.pages[p - 1])
        # An outline entry per run, so a folder of 14 PDFs opened in a viewer is
        # navigable without reading the filenames.
        try:
            writer.add_outline_item(
                f"p{start}-{end} ({size}pp) {slug}"
                + (f" -- {note}" if note else ""), 0)
        except Exception:
            pass              # an outline is a convenience, never a failure
        path = folder / f"{stem}_p{start:03d}-{end:03d}{PDF_EXT}"
        with open(path, "wb") as fh:
            writer.write(fh)
        written.append(str(path))
        names.append(path.name)

    tsv = folder / f"{stem}.tsv"
    with open(tsv, "w", encoding="utf-8") as fh:
        fh.write("start\tend\tpages\treason\tnote\tfile\n")
        for (start, end, size, slug, note), name in zip(rows, names):
            fh.write(f"{start}\t{end}\t{size}\t{slug}\t{note}\t{name}\n")
    written.append(str(tsv))

    for start, end, size, slug, note in rows[:8]:
        print(f"        p{start}-{end:<6} {size:5}pp  {slug:16}"
              + (f" {note}" if note else ""))
    if len(rows) > 8:
        print(f"        ... {len(rows) - 8} more run(s) in {tsv.name}")
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
    if manifest.get("listings"):
        listed = sum(len(c) for c in manifest["listings"].values())
        pages = ", ".join(f"p{p}({len(c)})" for p, c
                          in manifest["listings"].items())
        print(f"    ELENCO     : {listed} codes named ahead of the annex on "
              f"{len(manifest['listings'])} page(s) {pages}")
    if manifest.get("listed_found"):
        print(f"    ELENCO hit : {manifest['listed_found']} page(s) claimed by "
              f"the volume's own listing alone, covering "
              f"{manifest.get('listed_tables', 0)} table(s) no header or "
              f"outline named")
    if manifest.get("listed_missing"):
        print(f"    LISTED BUT NOT FOUND: {len(manifest['listed_missing'])} "
              f"code(s) the volume's own index promises and no detector found: "
              + " ".join(f"{c}@p{p}" for p, c in manifest["listed_missing"][:12]))
    if manifest.get("repeated_prints"):
        print(f"    reprinted  : {len(manifest['repeated_prints'])} table(s) "
              f"printed twice in the volume; one copy exported")
        for info in manifest["repeated_prints"][:8]:
            where = "/".join(str(part) for part in info["key"] if part)
            print(f"        {where:12} p{info['start']}-{info['end']} "
                  f"({info['pages']}pp) given up, kept p{info['kept_from']}"
                  f"-{info['kept_to']}")
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
    if manifest.get("boundary"):
        print(f"    bounded    : {len(manifest['boundary'])} table(s) stopped at "
              f"a section boundary the volume prints (INDICE or ELENCO)")
        for where, info in list(manifest["boundary"].items())[:8]:
            at = ", ".join(f"p{p}" for p in info["pages"])
            who = f" (p{info['pages'][0]} starts {info['authority']})" \
                if info["authority"] else ""
            print(f"        {where:22} -{info['total']} trailing page(s), "
                  f"stopped before {at}{who}")
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
    parser.add_argument("--no-trash", action="store_true",
                        help="non scrivere Out/TRASH/ (le pagine non attribuite "
                             "restano solo nel report)")
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

        out_root = args.out or args.base or "."
        readers = [v["reader"] for v in volumes]
        paths = [v["path"] for v in volumes]
        manifests = [v["manifest"] for v in volumes]
        plan = table_plan(readers, manifests)
        if not args.dry_run and plan:
            written = split_pdf(readers, manifests, out_root, year, paths)
            print(f"    wrote      : {len(written)} PDF in "
                  f"{Path(out_root, OUT_DIR, 'PDF')}/<authority>/"
                  f"[<articolo>/]<tabella>{year}.PDF")

        # The pages no table owns, written beside them. The accounting is printed
        # on every run -- including --dry-run -- because coverage is the number a
        # reader most wants from a run whose output cannot yet be trusted; only
        # the files are conditional.
        if args.no_trash:
            print("    trash       : skipped (--no-trash)")
        else:
            trashed = dump_trash(volumes, plan, out_root, year,
                                 write=not args.dry_run)
            if trashed and not args.dry_run:
                pdfs = sum(1 for p in trashed if p.endswith(PDF_EXT))
                print(f"    trashed    : {pdfs} PDF + {len(trashed) - pdfs} TSV in "
                      f"{Path(out_root, OUT_DIR, TRASH_DIR, year)}/")
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