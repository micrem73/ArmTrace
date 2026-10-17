#!/usr/bin/env python3
"""
Reports2PDFTables for the 2024 reporting year -- step 1, one PDF per table.

    python3 Reports2PDFTables_2024.py [--out <root>] [--dry-run] [--manifest <path>]

WHAT IS HERE AND WHAT IS NOT. This file holds the DETECTORS -- how a page is turned
into a candidate table code, how spans are built, how two volumes are joined -- and
the driver that runs them. The printed patterns, the page reader, the writer and the
volume plumbing are all in lib/:

    lib/patterns.py   every regex and constant; year-independent
    lib/pageio.py     reading one page; year-independent
    lib/emit.py       manifest -> files, plus the trash; year-independent
    lib/volumes.py    finding and ordering the source PDFs; year-independent
    lib/ontology.py   the ministries, the article tokens, the Out/ path shape
    lib/indice.py     ministry page ranges out of a volume's INDICE

WHY EACH YEAR IS ITS OWN SCRIPT. On 2026-10-11 the per-year scripts stopped
sharing one engine. The objective is tables produced with no errors, not one elegant
program; a detector that is right for 2025 is not right for 2024, and merging them
means re-testing every year on every change. So each year owns its detectors and is
never edited once its year verifies clean.

WHAT MAY CHANGE HERE. Anything above the primitives, and only here: page_code,
page_code_listed, scan_headers, detect_family, build_manifest, resolve_repeats,
stitch, and the volume policy main() pins. A 2016 script will need a new header style;
a 2024 script will need a different detect_family. Neither belongs in the frozen 2025
file, and neither belongs in lib/.

WHAT MUST NOT CHANGE. A primitive in lib/ is wrong for every year at once. To override
one locally -- a year that needs its own MIN_RUN or code shape -- import it here and
shadow it; do not edit lib/ to suit one year.

Both volumes, always -- and for 2024 "both" costs nothing to assert but must be checked:
the volumes are separate documents (lxvii_3 volume 1 and volume 2) whose folios run
continuously and whose tables all stop before printed 1000, so NO 2024 table straddles the
join and stitch() is expected to leave `continued` empty. A run that fires it here is a
defect, not a rescue. 2025 is the year that must be run in one go for the opposite reason:
Tabella F1 is printed 1035-1042 in volume I and 1043-1104 in volume II.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from lib import indice, ontology
from lib.emit import TRASH_DIR, dump_trash, report as render_report, split_pdf, table_plan
from lib.pageio import (
    appendix_code,
    scan_bookmarks,
    header_fingerprint,
    is_allegato_page,
    is_blank,
    is_ciphered,
    is_garbled,
    is_index_page,
    listing_codes,
    normalise_digit_one,
    printed_numbers,
    read_page,
    rel_code,
    rel_headings,
    same_table,
    trim_trailing,
    trim_trailing_blanks,
)
from lib.patterns import (
    ANNEXO_RE,
    APPENDIX_SUFFIX,
    BARE_CODE_MAX_LINE,
    CODE_RE,
    CODE_TAIL,
    ELENCO_TITLE,
    FAMILY2_CODES,
    INLINE_CODE,
    INDEX_ROW,
    LEADING_CODE,
    MIN_RUN,
    OUT_DIR,
    PDF_EXT,
    PROSE_STOPLIST,
    REL_STYLE,
    REL_TABELLA,
    REPRINT_GAP,
    SECTION_OPEN_MAX_LINE,
    SOURCE_RANK,
    TAIL_PAGES,
    TITLE_LINES,
    _WS,
)
from lib.volumes import collect_jobs, group_by_year, sibling_index

YEAR = "2024"
VOLUME = "both"

def block_at(edges, page):
    """Ministry owning a page per the volume's INDICE, or None.

    None where the index does not reach: before its first block, or in a volume
    it only partly covers. A missing boundary is not a boundary, so callers
    treat it as "nothing to enforce here" rather than "belongs to nobody".
    """
    if not edges:
        return None
    return indice.block_for_page(edges, page)



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



# A row of the "Cartella / Pagine / Totale Pagine" listing, read without the
# `$` anchor that loses a wrapped title.
#
# 2024 vol. I's ELENCO (PDF p70, printed 66) prints all of its entries on one
# page, and three of the titles wrap onto a second physical line:
#
#     5 1 TAB B5 Esportazione definitiva programmi di cooperazione elenco per paese
#     di destinazione (estrapolati dalla scheda B1) 616
#
# lib.patterns.INDEX_ROW ends at the line, so B5, B6 and B7 fall out and the
# volume reports a 28-code vocabulary where it prints 31. This is not a second
# spelling of the same trap -- INDEX_ROW cannot express a row that ends on the
# next line, and the witness is the page above.
#
# "TAB" IS OPTIONAL, and that is the second thing this pattern is for. The
# listing's third column is the table's NAME, and only most rows prefix it with
# "TAB": the same page also carries
#
#     1 55 INTRO Considerazioni generali e statistiche 55
#     1  1 LEGENDA Legenda 56
#     7  1 VALORE Esportazione definitiva riepilogo per fasce di valore ... 831
#
# Every one of those occupies pages in the same running total, so a sequence read
# with the prefix required skips a page in the middle and shifts every table
# after it by exactly that page -- which is how F1 came out one page early, on
# p845, a page that is not F1 at all.
ELENCO_ROW_2024 = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(TAB\.?\s+)?([A-Z][A-Z0-9]{0,7})(?![A-Za-z0-9])(.*)$"
)
# The running total that ends the row, wherever the row happens to end.
ELENCO_TOTAL_2024 = re.compile(r"(\d+)\s*$")


def read_index(reader):
    """Harvest {code: page_count} and a placement plan from an ELENCO page.

    Two layouts are recognised: the "Cartella / Pagine / Totale Pagine" table
    of 2020 and 2022, and the plain "Tabella D <title>" listing of 2012.

    Returns (vocabulary, page_counts, plan). `plan` is {"page": the page the
    listing is printed on, "rows": [...]}, one record per listed code in printed
    order -- {code, pages, total} -- and it is empty for every volume whose
    listing is not the two-column-count one. It is kept because that layout says
    more than the vocabulary: the second column is an exact page count for each
    table, which is the only witness to a table whose own pages carry no
    readable code. See place_from_plan.
    """
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        lines = text.split("\n")
        counts, codes, plan = {}, set(), []
        for i, line in enumerate(lines):
            m = INDEX_ROW.match(line)
            if m:
                counts[m.group(2)] = int(m.group(1))
                codes.add(m.group(2))
                # Recorded in the plan as well. INDEX_ROW is this listing's own
                # layout -- "Cartella / Pagine / Totale Pagine", whose two number
                # columns are a page count and a running total -- and a plan of
                # only the rows INDEX_ROW REJECTED would be a plan of the three
                # broken ones, which is no plan at all.
                plan.append({"code": m.group(2), "pages": int(m.group(1)),
                             "total": int(m.group(4)), "tab": True,
                             "title": m.group(3)})
                continue
            m = LEADING_CODE.match(line.strip())
            if m:
                codes.add(normalise_digit_one(m.group(1).upper()))
                continue
            # Tried last, and read only what INDEX_ROW rejected: a row still
            # carrying a code here is one whose title wrapped, so the running
            # total that ends the row is on the NEXT line. Looking ahead is safe
            # precisely because the row did not match above -- if the total were
            # on this line INDEX_ROW would have taken the row and this branch
            # would never see it. No guess about which number in the title is a
            # total, because a title is prose and this is the only shape left.
            # Tried last, and read only what INDEX_ROW rejected. Two shapes reach here, and
            # they end on different lines.
            #
            # A row WITH the "TAB" prefix is one whose title wrapped: INDEX_ROW
            # ends at the line, so it could not have matched a row whose running
            # total is on the next one, and looking ahead is safe precisely for
            # that reason. No guess about which number in the title is a total --
            # a title is prose and a wrapped row is the only shape left.
            #
            # A row WITHOUT it (INTRO, LEGENDA, VALORE) was rejected because the
            # keyword is absent, not because the row wrapped, so its total is the
            # trailing number of its OWN line. Reading ahead there picks up the
            # next row's page count and writes a title that runs on into the row
            # below it, which is how LEGENDA was reported as "Legenda 56 2 497
            # TAB A1 ...".
            w = ELENCO_ROW_2024.match(line)
            if not w:
                continue
            tabbed = bool(w.group(3))
            if tabbed:
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                total = ELENCO_TOTAL_2024.search(nxt)
                title = nxt[:total.start()].strip() if total else ""
                body = w.group(5).strip() + " " + title
            else:
                total = ELENCO_TOTAL_2024.search(w.group(5))
                if not total:
                    continue
                body = w.group(5)[:total.start()].strip()
            code = normalise_digit_one(w.group(4).upper())
            # A row with no "TAB" prefix is an entry in the running total, not
            # necessarily a table: INTRO and LEGENDA are the ministry's front
            # matter. Its length is what matters here, and the flag is what keeps
            # the placement pass from claiming it as one.
            counts.setdefault(code, int(w.group(2)))
            if tabbed:
                codes.add(code)
            plan.append({"code": code, "pages": int(w.group(2)),
                         "total": int(total.group(1)), "tab": tabbed,
                         "title": body.strip()})
        # A real index lists many codes and nothing else. 2012 vol. III and
        # 2014 vol. I surface a single spurious code this way and are rejected
        # by requiring eight.
        if len(codes) >= 8:
            if len(plan) < 8:
                return codes, counts, {"page": None, "rows": []}
            return codes, counts, {"page": page_no, "rows": plan}
    return set(), {}, {"page": None, "rows": []}



# A line the export header cut between the keyword and the code, and the code
# that follows it.
#
# The Dogane prints every table of its annex as a cover page plus data pages,
# and on fifteen of the covers the cover's own last line ends with the keyword
# and the code stands alone on the line after:
#
#     Riepilogo per operatore TAB.          <- p1071
#     M1                                   <- p1071
#
#     Programmi Intergovernativi
#     Reimportazione(RI) TAB.              <- p1120
#     O - APPENDICE                        <- p1120
#
# lib/pageio.appendix_code() already probes a joined pair, but only for the
# APPENDICE shape, and nothing else sees a bare code on the next line at all.
# Those fifteen covers are the ONLY place MG3, MG6, MG8, MG11, MG15 and MG18
# are named, so without this the Dogane's whole art. 11 comma 5-bis series is
# invisible and the table before it swallows the lot.
COVER_KEYWORD_2024 = re.compile(r"(?i:T\s*a\s*b\s*e\s*l\s*l\s*a|TAB)\s*\.\s*$")
COVER_CODE_2024 = re.compile(
    rf"^({CODE_TAIL})(?![A-Za-z0-9])(?:\s*[-–—]\s*APPENDICE)?$")

# Styles this year adds. Their own numbers, because every existing style is a
# shape that could be a law table and neither of these is.
COVER_STYLE_2024 = 8      # the shredded cover above
DOGANE_STYLE_2024 = 9     # a code from the Dogane's own scheme, below


def cover_code(lines):
    """The code a cover page prints under its cut keyword, or None."""
    for j, line in enumerate(lines):
        if not COVER_KEYWORD_2024.search(line) or j + 1 >= len(lines):
            continue
        m = COVER_CODE_2024.match(lines[j + 1].strip())
        if m:
            suffix = APPENDIX_SUFFIX if "APPENDICE" in lines[j + 1].upper() \
                else ""
            return f"{m.group(1).upper()}{suffix}"
    return None



def detect_family(reader, vocabulary):
    """1 = art.27 double-letter, 2 = A1..P2, 3 = art.27 single-letter.

    Informational for reporting, plus it selects which header styles to try.
    Detection does not depend on it being correct.

    2024 vol. II IS FAMILY 1, and the shared test says 3. Its vocabulary as
    harvested is AA..DD2 -- twelve codes, every one of the shape [A-Z]\\d?, four
    of them single letters -- which is exactly the family-3 signature, because
    read_index() stopped at the first page reaching eight codes and returned a
    twelfth of the MEF's thirty-five. The volume prints no family-3 code: the
    whole single-letter space is the Dogane's bare M/N/O/P, which is what the
    shared test mistakes for one. Corrected here rather than in lib/ because
    the correction is this volume's, and it is worth reporting: family selects
    which header styles page_code tries.
    """
    # A vocabulary dominated by A1..P2 codes is family 2. D, E, I, L and M are
    # excluded from the test because they are also art.27 single-letter codes,
    # which a family 1 index can legitimately contain.
    f2 = (vocabulary & FAMILY2_CODES) - {"D", "E", "I", "L", "M"}
    if len(f2) >= 4:
        return 2
    if vocabulary and len(vocabulary) >= 8:
        # Art. 27 shapes, and the two families differ in WHICH of them dominates.
        # The shared test counts [A-Z]\d? -- a single letter with an optional
        # digit -- which drops every double-letter code on the floor and so
        # cannot see the difference at all: AA, AA1, BB, BB1 ... never enter
        # `shaped`. Measured on 2024 vol. II's vocabulary, the shared test
        # counts 4 shaped and 4 singles and answers 3; counting the doubles too
        # gives 12 shaped, 12 doubles, 0 singles, which is family 1.
        art27 = [c for c in vocabulary if re.fullmatch(r"[A-Z]{1,2}\d{0,2}", c)]
        singles = [c for c in art27 if re.fullmatch(r"[A-Z]", c)]
        doubles = [c for c in art27 if re.fullmatch(r"[A-Z]{2}\d{0,2}", c)]
        if len(art27) >= 6 and len(doubles) >= 3 and len(doubles) >= len(singles):
            return 1
        if len(art27) >= 6 and len(singles) >= 4:
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


# The shared detector, kept under its own name so the wrapper below can call it.
# A year that shadows page_code must not re-implement it.
_page_code_shared = page_code


def page_code(lines, text, vocabulary, family, dogane=False):
    """The shared detector, plus the two shapes 2024 vol. II needs.

    WHAT THE VOLUME PRINTS THAT THE SHARED DETECTOR REFUSES. 2024 vol. II is
    two ministries in one document: the MEF's art. 27 double-letter annex
    (AA..UE, 35 codes, named by the volume's own ELENCO and by its bookmark
    tree), and the Dogane's own annex, which switches scheme at printed 1707 and
    prints TAB. M / N / O / P, the bare bases of the M1..P2 series, then
    MG1..MG18 and MT1/MT7/MT13 under art. 11 comma 5-bis. None of that is in
    the MEF vocabulary, and style 3's test -- "a code the volume's own listing
    promised" -- throws every one of them away. That is what made the ROADMAP's
    "five phantoms" out of real tables, and what left P2 running to the end of
    the volume over MG1, MG3, MG4 and MT1.

    So on a page the ontology already attributes to the Dogane, a code outside
    the MEF vocabulary is read instead of discarded. It is NOT taken on trust:
    it comes back as DOGANE_STYLE_2024, and build_manifest then requires it to
    repeat on a second page -- the frequency rule the pipeline already uses to
    tell a running header from a prose mention, and the one that has to be
    applied here because MIN_RUN's Dogane waiver would otherwise let a single
    mention through.

    The cover shape is tried when nothing else found a code, and it needs no
    ministry marker: those fifteen covers classify as NOBODY (classify_page
    returns None on all of them -- a cover prints the operation name and the
    code and nothing else), so a "only on Dogane pages" gate would have skipped
    every one. It does not need one: a line ending in the keyword with a bare
    code standing alone beneath it is the Dogane's cover typesetting, and over
    both 2024 volumes it fires on exactly those fifteen pages and nowhere else
    -- zero times in vol. I, which has no such covers.
    """
    code, style = _page_code_shared(lines, text, vocabulary, family)
    if code:
        return code, style
    code = cover_code(lines)
    if code:
        return code, COVER_STYLE_2024
    if not dogane:
        return None, None
    for line in lines:
        for m in INLINE_CODE.finditer(line):
            cand = m.group(1).upper()
            if cand not in FAMILY2_CODES and cand not in vocabulary:
                return cand, DOGANE_STYLE_2024
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
    # header region, above the body -- 2025 has a bare "I" in
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
    dogane_pages = set()
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
                # 2024: the Dogane's annex prints a code scheme of its own, and
                # page_code has to be told this page belongs to it. The set is
                # filled here, in the same pass that already classifies every
                # page, so it costs nothing.
                if authority == ontology.DOG:
                    dogane_pages.add(page_no)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if facts is not None:
            facts["blank"][page_no] = is_blank(lines)
            # A page of a line or two. Recorded because the last table of vol. II
            # runs to the last physical page and closes on two such leaves: a
            # "PAGINA BIANCA" and a Dogane export-system stamp, "*190670137970*",
            # neither of which names a table.
            facts["thin"][page_no] = len(lines) <= 3
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

        code, style = page_code(lines, text, vocabulary, family,
                              page_no in dogane_pages)
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
            # ...and the page is recorded for cutting only when it carries more
            # than ONE exhibit. A page carrying a single exhibit does not need
            # the cut: the whole page is the table, bar the volume's own running
            # footer -- and cutting it costs more than it buys, because it
            # rewrites the content stream into a 17-byte wrapper around a Form
            # XObject, and that stream is the ONLY witness
            # VerifyTables.check_page_overlap() has that two files do not share
            # a page. With it gone the witness degenerates to the mediabox and
            # two exhibits cut from pages of the same size hash identically:
            # T6 (p35) against T88 (p43-44) and T82 (p39) against T85 (p41)
            # all FAILed on a page they share nothing with.
            if tables_here and len(rel) > 1 and rel_pages is not None:
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



def place_from_plan(tables, plan, provenance, articles):
    """Place the tables no page could be found for, from the index's own counts.

    WHAT THIS IS FOR. 2024 vol. I prints 31 tables and the page scan finds 10 of
    them. The other 21 are not missing because their codes are unknown -- the
    volume's ELENCO on PDF p70 names every one of them -- and not because they
    are absent: they are 335 pages whose text layer was destroyed at PDF
    generation, so no header, no running footer and no title can be read on
    them. The last table the scan did find before the dead stretch, B7, simply
    runs on to the next one it can see, and a 20-page table ships 358 pages.

    THE WITNESS. The ELENCO's second column is an exact page count per table, so
    the listing says how long every table is even when no page of it is legible.
    That is a length, not a position, so a position still has to come from
    somewhere: it comes from a table the scan DID place, by walking the listing
    in printed order and applying the counts. Two independent witnesses are
    therefore required for every table placed here -- the index that says how
    long it is, and the page scan that says where the run begins -- and a table
    is never placed from the index alone.

    WHY THE ANCHOR IS THE PREVIOUS TABLE'S INDEX LENGTH, not its detected end.
    The detected end is derived from the next detected start, which in a dead
    stretch is the next table hundreds of pages away; that derived end is
    precisely what is wrong. The index's own length, applied to the start a
    detector read, is not derived from anything that is missing.

    VERIFIED ON 2024 vol. I, which is where the rule was measured. With A1
    starting at p71 (not p70: p70 is the ELENCO itself, and the scan read the
    listing's own row as a table opening), the counts reproduce every detected
    start exactly -- A2 p568, A4 p578, B6 p631, B7 p634 -- and the 21 unplaced
    tables fill pp 654-991 with not one page left over. The separator leaves the
    scan does show (before N1, before O1, before P1) are detected starts, and
    they win over the arithmetic by construction: a code the scan placed is
    never moved.

    Any slack between the last placed table and the next detected one is
    reported, never absorbed. Pages the index cannot account for are pages
    nobody has claimed, which is what Out/TRASH is for.
    """
    notes = []
    placed = []
    omitted = []
    rows = (plan or {}).get("rows") or []
    # The plan is a witness about one code space. A volume whose listing is a
    # different layout leaves it empty, and a listing naming codes outside the
    # space it was read for is not this detector's to act on. Entries with no
    # "TAB" prefix are excluded from the test on purpose: they are counted for
    # their length and never claimed as tables.
    tabbed = [row for row in rows if row.get("tab")]
    if len(tabbed) < 8:
        return placed, notes, omitted
    if not all(row["code"] in FAMILY2_CODES for row in tabbed):
        return placed, notes, omitted

    # Where the page scan, the outline and the listing put each planned code.
    # A code a detector placed is never moved, so the walk below only fills what
    # is missing.
    page = {}
    for (_authority, _article, code), info in sorted(
            tables.items(), key=lambda kv: kv[1]["start"]):
        for row in tabbed:
            if row["code"] not in page and same_table(code, row["code"]):
                page[row["code"]] = info["start"]
                break
    if not page:
        return placed, notes, omitted
    existing = {key[2] for key in tables}
    known_pages = sorted(page.values())

    # seq[k] is the page the listing's k-th entry starts on. Every entry gets
    # one, tabbed or not, because every entry occupies pages in the running
    # total -- that is the whole reason VALORE is in the sequence at all. An
    # entry a detector placed supplies its own; the rest are counted forward
    # from whichever entry before them does.
    seq = {}
    i = 0
    while i < len(rows):
        row = rows[i]
        if row["code"] in page:
            seq[i] = page[row["code"]]
            i += 1
            continue
        if not row.get("tab"):
            # Counted, never claimed. VALORE is a real one-page table the
            # pipeline has no code for; INTRO and LEGENDA are front matter. The
            # walk cannot tell those apart and does not pretend to: it reports
            # each one, with the page the arithmetic gives it.
            seq[i] = None if not i or seq.get(i - 1) is None \
                else seq[i - 1] + rows[i - 1]["pages"]
            omitted.append({"code": row["code"], "pages": row["pages"],
                            "page": seq[i], "title": row["title"]})
            i += 1
            continue
        # A maximal run of consecutive listed tables that nothing placed.
        j = i
        while j < len(rows) and rows[j].get("tab") \
                and rows[j]["code"] not in page:
            j += 1
        run = rows[i:j]
        if not i or seq.get(i - 1) is None:
            # Nothing before the run anchors it. The head of the sequence is the
            # ministry's front matter, and a position invented here would be
            # invented: reported, not placed.
            omitted.append({"code": " ".join(r["code"] for r in run),
                            "pages": sum(r["pages"] for r in run), "page": None,
                            "title": "nessuna tabella precedente che lo ancori"})
            i = j
            continue
        cursor = seq[i - 1] + rows[i - 1]["pages"]
        below = page[rows[j]["code"]] if j < len(rows) \
            and rows[j]["code"] in page else None
        for k in range(i, j):
            seq[k] = cursor
            cursor += rows[k]["pages"]
        if below is not None and cursor - 1 >= below:
            notes.append({
                "codes": [r["code"] for r in run],
                "from": seq[i], "to": cursor - 1, "next": below,
                "problem": "l'indice dichiara piu' pagine di quante ne restano",
            })
        elif below is not None:
            notes.append({
                "codes": [r["code"] for r in run],
                "from": seq[i], "to": cursor - 1, "next": below,
                "slack": below - cursor,
            })
        i = j

    for k, row in enumerate(rows):
        if not row.get("tab"):
            continue
        code = row["code"]
        if any(same_table(code, seen) for seen in existing):
            continue
        start = seq.get(k)
        if start is None or not known_pages[0] <= start <= known_pages[-1]:
            continue
        authority, article = resolve_placement([start], provenance, articles)
        tables.setdefault((authority, article, code), {
            "start": start, "source": "index", "provisional": False,
            "seen": [start],
        })
        placed.append({"code": code, "start": start,
                       "index_pages": row["pages"], "total": row["total"],
                       "title": row["title"]})
    return placed, notes, omitted



def stamp_witness(tables, lengths, stamps):
    """Check every index-placed table against the export stamps, and report.

    THE POINT. place_from_plan() positions a table by arithmetic: the listing's
    page count, applied to a start some detector read. Arithmetic that is never
    checked is arithmetic that is merely plausible, so every table it placed is
    put against an INDEPENDENT witness -- the "Pagina N di X" stamp each table
    was exported with, which says how long the document really is.

    The digits on a garbled page are Private Use Area characters and cannot be
    read, and no decoding is attempted: the test needs no value. A document's
    pages carry the same TOTAL token and its last page carries that token as its
    own running number, so

      * a table whose last page carries a stamp must end on a page whose
        running number IS its total -- that page is the document's last;
      * a table whose pages carry no stamp at all lies wholly inside an
        unstamped stretch, and its length is the listing's alone.

    Anything else is printed as a disagreement rather than smoothed over. On
    2024 vol. I the stamps agree on all 31: B7 ends on p653 reading "Pagina 20
    di 20" for 20 listed pages, C1 on p791, E on p844, F1 on p920, and C2 and D
    on the five unstamped pages pp 792-796, which is exactly 2 + 3.
    """
    agree, disagree = [], []
    for (authority, article, code), info in sorted(
            tables.items(), key=lambda kv: kv[1]["start"]):
        length = lengths.get(code)
        if not length or info.get("source") != "index":
            continue
        start, end = info["start"], info["end"]
        where = "/".join(filter(None, [authority, article, code]))
        stamp = stamps.get(end)
        if stamp is not None:
            if stamp[1] == stamp[2]:
                agree.append((where, start, end))
            else:
                disagree.append((where, start, end,
                                 f"l'ultima pagina porta il timbro {stamp[1]} di "
                                 f"{stamp[2]}, non la fine del documento"))
        elif any(p in stamps for p in range(start, end + 1)):
            disagree.append((where, start, end,
                             "il timbro sparisce a meta' della tabella"))
        else:
            agree.append((where, start, end))
    return agree, disagree


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



def build_manifest(reader, vocabulary, counts, sibling=None, plan=None):
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
    facts = {"blank": {}, "thin": {}, "numbers": {}, "fingerprints": {}}
    page_codes, weak_pages, unassigned, garbled, ciphered, foreign, listings, \
        candidates, shared_pages, rel_pages = scan_headers(
            reader, vocabulary, family, stamps, raw_prov, pasted, facts,
            shared=[], rel_pages=[])

    # The volume's own listing is not a page of any table. 2024 vol. I prints
    # its ELENCO on p70, and that page names every table in the volume with its
    # length -- so the page scan reads "TAB A1 Esportazione definitiva per
    # operatori" as A1's opening and A1 starts one page early, shipping the
    # listing as its first page. 498 pages against the index's 497.
    #
    # Dropping the page is the whole fix: A1's code is on every one of its own
    # pages, so the run simply begins at p71 and the span the index promised is
    # the span the file gets. The page itself goes to Out/TRASH as the listing,
    # which is what it is.
    index_page = (plan or {}).get("page")
    if index_page and index_page in page_codes:
        del page_codes[index_page]
        unassigned = [p for p in unassigned if p != index_page]
        candidates.pop(index_page, None)
    plan_lengths = {row["code"]: row["pages"]
                    for row in (plan or {}).get("rows") or []
                    if row.get("tab")}

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
        # is wanted here, since the article comes from `articles` below.
        provenance = {page: pair[0] for page, pair
                      in fill_provenance(raw_prov, len(reader.pages)).items()}
    # The article goes through the SAME gap fill the authority does, because it
    # was the only one of the two not being filled and 2024 vol. II showed what
    # that costs. Every Dogane cover page classifies to None and the "riepilogo
    # per operatore" pages carry no article reference at all, so pp 1071-1095 --
    # the M1, N1, O1 and P1 summaries of the art. 1 comma 2 series -- were keyed
    # under (DOG, None) and landed beside the ministry instead of inside the
    # article they belong to. fill_provenance's rule is the right one here: a
    # gap is filled only when the pages before and after it agree, and left
    # alone where they do not, so a real article boundary is never crossed.
    articles = {page: pair[1] for page, pair
               in fill_provenance(raw_prov, len(reader.pages)).items()
               if pair[1]}

    # A cover page carries a table's code and nothing else.
    # ontology.classify_page() returns None on all fifteen of vol. II's Dogane
    # covers -- a cover prints the operation name, the code and the volume
    # footer -- so the ARTICLE was missing and the table split in two keys: the
    # cover under (DOG, None) and the body under (DOG, A11C5BIS). MG3 came out
    # as both DOG/MG3 (p1164) and DOG/A11C5BIS/MG3 (p1165-1172).
    #
    # A page that carries a table's code is not an article of its own. The code
    # says which table the page opens; the table's own body says which article
    # printed it. Adopting the body's article is witnessed rather than inherited
    # from a neighbour: the two pages carry the SAME code, which is what makes
    # them one table, and the look-ahead is three pages so it cannot reach across
    # a boundary into the next document.
    for page, hits in sorted(page_codes.items()):
        if articles.get(page):
            continue
        here = {code for code, _style in hits}
        for ahead in range(page + 1, min(page + 4, len(reader.pages) + 1)):
            if ahead not in page_codes:
                break
            if articles.get(ahead) and here & {c for c, _s in page_codes[ahead]}:
                articles[page] = articles[ahead]
                provenance.setdefault(page, provenance.get(ahead))
                break

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
        # The Dogane's own scheme, read on a page the ontology attributed to the
        # Dogane because the volume's MEF listing never named it. That is a
        # weaker warrant than a listing, so MIN_RUN is applied to it even where
        # the Dogane's waiver lifts it: one page carrying a code is a prose
        # cross-reference, and the frequency rule is what tells the two apart.
        if entry["style"] == DOGANE_STYLE_2024 and run < MIN_RUN:
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

    # The index's own page counts place what no page could: 2024 vol. I has 335
    # pages whose text layer is destroyed, and 21 of its 31 tables live in them.
    # Run after the bookmark merge, so a human-authored outline keeps priority
    # over arithmetic, and before the ends are derived, so placing a table also
    # shortens the table it was swallowing.
    plan_placed, plan_notes, plan_omitted = place_from_plan(
        tables, plan, provenance, articles)

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

        # Fourth boundary, and the one the index is the only witness to: the
        # listing states each table's own length, so a derived end that runs
        # past it is a table swallowing a page the document says belongs to
        # nobody. 2024 vol. I L is 21 pages by the index, so it ends at p990,
        # and p991 is a separator leaf between it and M1 at p992 -- but the
        # derived end is "the page before the next table starts", which is p991.
        #
        # Same discipline as the clamps below: never below `floor`, the last
        # page on which a detector actually read this table's own code. Where the
        # page furniture and the index disagree, the disagreement is reported
        # rather than resolved by deleting pages the table demonstrably has.
        length = plan_lengths.get(code)
        if length is not None:
            declared = info["start"] + length - 1
            if declared < info["end"] and declared >= floor:
                record_boundary(outside, where, declared + 1, None,
                                info["end"] - declared)
                info["end"] = declared

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
        # ...and one step further than it can go. trim_trailing_blanks walks back
        # only while the LAST page is blank, and 2024 vol. II closes on two
        # leaves where only the first is: p1259 is "PAGINA BIANCA" and p1260 is
        # the Dogane's export stamp, "*190670137970*", which is not blank, so
        # the walk stopped at once and MT13 shipped both. The second test is
        # what distinguishes a closing leaf from a table whose own pages happen
        # to carry no code -- a page of one or two lines, naming no table, at the
        # very end of the volume. Applied to the last table only, which is the
        # only one whose end is derived from the physical end of the document.
        dropped = 0
        while last_info["end"] > last_info["start"] \
                and last_info["end"] not in page_codes \
                and (facts["blank"].get(last_info["end"])
                     or facts["thin"].get(last_info["end"])):
            last_info["end"] -= 1
            dropped += 1
        if dropped:
            last_info["pages"] = last_info["end"] - last_info["start"] + 1
            where = "/".join(filter(None, [last_info.get("authority"),
                                          last_info.get("article"),
                                          last_code[2]]))
            record_boundary(outside, where, last_info["end"] + 1, None, dropped)
            trimmed[where] = trimmed.get(where, 0) + dropped
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

    # The listings the reader gets to see are the ones the report prints plus the
    # page the two-column-count layout was read from -- which IS a listing of
    # this volume's tables, and lib/emit.py's trash_reason() reads this key to
    # decide what an unattributed run was. Recorded here rather than in
    # `listings` itself because the page scan's `listings` also drives the
    # reprint-gap test and the ELENCO boundary clamp, and this page is neither a
    # gap between two prints nor a ministry boundary.
    _listings_out = {p: c for p, c in sorted(listings.items())}
    if index_page and index_page not in _listings_out:
        _listings_out[index_page] = sorted(plan_lengths)

    # The independent check on every table place_from_plan() positioned. Run
    # last, over the finished spans, so it tests what will be written.
    stamp_ok, stamp_bad = stamp_witness(final, plan_lengths, stamps)

    return {
        "family": family,
        "page_count": last_page,
        "provenance_source": source,
        "listings": _listings_out,
        "index_page": index_page,
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
        "index_plan": {"codes": len(plan_lengths),
                       "placed": plan_placed, "runs": plan_notes,
                       "omitted": plan_omitted,
                       "stamp_agree": [w for w, _s, _e in stamp_ok],
                       "stamp_disagree": stamp_bad},
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
                        help="processa ogni volume in In/")
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
            vocabulary, counts, plan = read_index(reader)
            # Guarded per volume, for the same reason the report is: one
            # unreadable volume must cost that volume, not the remaining
            # twenty-one.
            try:
                manifest, facts = build_manifest(reader, vocabulary, counts,
                                                sibling=sibling_index(report),
                                                plan=plan)
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
                report_index_plan(manifest)
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
    """The per-volume summary, printed before the writer runs.

    A wrapper only because `report` is shadowed inside main() by the loop variable
    of the same name; the alias above keeps the two apart.
    """
    render_report(manifest, label)

def report_index_plan(manifest):
    """Print the index-placement pass, if this volume ran one.

    lib/emit.py's report is the shared half and knows nothing about a 2024
    detector, so the lines belong here rather than in it. Every table placed this
    way is named with the page it was given and the length the index promised,
    because a placement that is arithmetic rather than observed is the one a
    reader has to be able to check by eye. A disagreement is printed even when
    nothing was placed: a run that does not fit is a finding, not a silence.
    """
    info = manifest.get("index_plan") or {}
    if not info.get("codes"):
        return
    placed = info.get("placed") or []
    runs = info.get("runs") or []
    omitted = info.get("omitted") or []
    if not placed and not runs and not omitted:
        return
    print(f"    ELENCO plan: {info['codes']} rows carry an exact page count; "
          f"{len(placed)} table(s) placed by it")
    for row in placed[:40]:
        print(f"        {row['code']:6} p{row['start']:<5} "
              f"{row['index_pages']:>4}p  tot {row['total']:<5} "
              f"{row['title'][:44]}")
    for run in runs:
        codes = " ".join(run["codes"])
        if "problem" in run:
            print(f"    PLAN ERROR : {codes} p{run['from']}-{run['to']} runs "
                  f"past p{run['next']} -- {run['problem']}")
        elif run["slack"]:
            print(f"    plan slack : {len(run['codes'])} table(s) "
                  f"p{run['from']}-{run['to']}, {run['slack']} page(s) before "
                  f"p{run['next']} that the index does not account for")
    if omitted:
        print(f"    plan omits : {len(omitted)} listing entry(ies) with no "
              f"'TAB' name, counted for their pages and NOT exported:")
        for row in omitted[:12]:
            where = f"p{row['page']}" if row["page"] else "unplaced"
            print(f"        {row['code']:9} {row['pages']:>4}p  {where:9} "
                  f"{row['title'][:40]}")
    agree, bad = info.get("stamp_agree") or [], info.get("stamp_disagree") or []
    if agree or bad:
        print(f"    stamp check: {len(agree)}/{len(agree) + len(bad)} placed "
              f"table(s) confirmed by the 'Pagina N di X' stamp or by lying "
              f"wholly in an unstamped stretch")
        for where, start, end, why in bad[:8]:
            print(f"        STAMP MISMATCH {where} p{start}-{end}: {why}")

def _refuse_foreign_year(argv):
    """This script is pinned to its year; a --year on the command line is an error.

    Silently honouring it would leave the output looking like a different year's
    result, which is the one failure the per-year script exists to prevent.
    """
    for arg in argv:
        if arg.startswith("--year"):
            raise SystemExit(
                f"Reports2PDFTables_{YEAR}.py is pinned to {YEAR}; use the script "
                f"for the year you want, or "
                f"Reports2PDFTables_{YEAR}.py --help."
            )


def cli(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _refuse_foreign_year(argv)
    return main(["--year", YEAR, "--volume", VOLUME] + argv)


if __name__ == "__main__":
    cli()
