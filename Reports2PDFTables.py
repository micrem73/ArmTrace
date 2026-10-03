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
"""

import argparse
import glob
import json
import os
import re
import sys
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


def scan_headers(reader, vocabulary, family):
    """{code: first page}, {code: [pages]}, and corruption tallies."""
    starts, hits = {}, {}
    unassigned, garbled, ciphered = [], [], []
    weak, styles = {}, {}
    for i, page in enumerate(reader.pages):
        page_no = i + 1
        try:
            text = page.extract_text() or ""
        except Exception:
            unassigned.append(page_no)
            continue
        if is_garbled(text):
            garbled.append(page_no)
        elif is_ciphered(text):
            ciphered.append(page_no)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
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
    return starts, hits, weak, styles, unassigned, garbled, ciphered


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
    header, hits, weak, styles, unassigned, garbled, ciphered = scan_headers(
        reader, vocabulary, family
    )

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
    for idx, (code, info) in enumerate(ordered):
        end = ordered[idx + 1][1]["start"] - 1 if idx + 1 < len(ordered) else last_page
        info["end"] = max(info["start"], end)
        info["pages"] = info["end"] - info["start"] + 1
        if code in counts:
            info["index_pages"] = counts[code]
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
    }


# ==========================================================================
# output
# ==========================================================================

def split_pdf(reader, manifest, out_root, table, year):
    """Write one PDF per table under <out_root>/PDF/<tabella>/<tabella><anno>.PDF.

    The directory is named for the table CODE, not the source volume, so
    Out/PDF/AA/AA2023.PDF holds Tabella AA from whichever volume carried it.
    The year is a filename suffix rather than a directory level, so one
    folder per table holds one file per reporting year.
    """
    written = []
    for code, info in manifest["tables"].items():
        writer = PdfWriter()
        for p in range(info["start"], info["end"] + 1):
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
    checked = [i for i in manifest["tables"].values() if "index_agrees" in i]
    if checked:
        agree = sum(1 for i in checked if i["index_agrees"])
        mism = [(c, i["pages"], i["index_pages"]) for c, i in
                manifest["tables"].items() if "index_agrees" in i
                and not i["index_agrees"]]
        print(f"    index check: {agree}/{len(checked)} spans agree with the "
              f"index page counts, {len(mism)} mismatch {mism[:4]}")


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

    jobs = collect_jobs(args)
    print("=" * 70)
    print(f"Reports2PDFTables — {len(jobs)} volume/i")
    print("=" * 70)

    total = 0
    for report, year in jobs:
        label = Path(report).stem
        try:
            reader = PdfReader(report, strict=False)
        except Exception as exc:
            print(f"{label}: apertura fallita: {exc}\n")
            continue
        vocabulary, counts = read_index(reader)
        manifest = build_manifest(reader, vocabulary, counts)
        report_manifest(manifest, f"{label} (y={year})")
        total += len(manifest["tables"])

        if args.manifest:
            target = Path(args.manifest)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"report": report, "year": year,
                                    **manifest}, ensure_ascii=False) + "\n")

        if not args.dry_run and manifest["tables"]:
            out_root = args.out or args.base or "."
            written = split_pdf(reader, manifest, out_root, label, year)
            print(f"    wrote      : {len(written)} PDF in "
                  f"{Path(out_root, OUT_DIR, 'PDF')}/<tabella>/<tabella>{year}.PDF")
        print()
        del reader

    print("=" * 70)
    print(f"Totale: {total} tabelle in {len(jobs)} volume/i")
    if args.dry_run:
        print("(dry-run: nessun PDF scritto)")


def report_manifest(manifest, label):
    report(manifest, label)


if __name__ == "__main__":
    main()