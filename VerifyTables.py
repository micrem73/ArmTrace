#!/usr/bin/env python3
"""
VerifyTables — check that the per-table PDFs under Out/PDF really are one
table each, complete, correctly named, and accounted for by the indices the
reports print.

    Out/PDF/<tabella>/<tabella><anno>.PDF   ->   a verdict per file

This is the audit step. It reads what step 1 produced and what the source
volumes say about it, and it reaches its own conclusions: where a span in
step 1's manifest disagrees with the file on disk, the file and the volume
win, because they are the artefacts. The manifest is one witness among
several, not the reference.

Four questions per file, each with its own witness:

    one table      the "Pagina N di X" stamp is the only thing in the archive
                   that knows a table's real length, because each table was
                   exported as a document of its own and pasted into the
                   volume. Stamp total == page count means the file is
                   exactly that document. Where the stamp is garbled the
                   numbers cannot be read, but they can still be COMPARED:
                   the subsetter gave one Private Use codepoint per
                   character, so two pages of the same document agree
                   character for character on the total and a page from
                   another document does not.

    complete       first page is page 1 of that document, the total never
                   changes, no page in the middle lacks the stamp, and the
                   index page count agrees where the index carries one.

    nothing else   no blank separator, no ELENCO page, no running-header
                   page, no page belonging to a different table at either
                   end of the file.

    correctly      the code printed on the file's own pages is the code in
    named          the filename, and the title the index gives that code
                   appears on the first page.

Then, per year and volume, the indices themselves:

    general        the INDICE in the front matter: which authority's tables
                   start on which printed page.
    per authority  the ELENCO TABELLE SEGNALAZIONI inside each authority's
                   section: every table code it lists, with its title.
    bookmarks      the PDF outline, where the volume has a usable one.

Every code any of those three lists must have a file. Every file must have a
code at least one of them lists -- reported as a warning, not a failure,
because five volumes carry no usable index at all.

And the backbone check: rebuild each volume's coverage and require every
physical page to be attributed to exactly one table, or to one of an explicit
list of non-table classes. A page claimed twice is a failure; a page claimed
by nobody is a warning. That single invariant is what catches a table that
ran into its neighbour, a trailing page pasted along with a document, and a
gap where a table no detector found should be.

Usage:
    ./VerifyTables.py --year 2025
    ./VerifyTables.py --from-year 2016 --to-year 2025
    ./VerifyTables.py --all
    ./VerifyTables.py --year 2025 --dry-run        # verdicts only, no JSON
    ./VerifyTables.py --year 2025 --refresh         # ignore the cache
    ./VerifyTables.py --year 2025 --sample 3        # first/middle/last pages

Requires: pypdf (pip install -r requirements.txt). poppler-utils' pdftotext is
used for the index harvest because it is roughly five times quicker than
pypdf over a whole volume; when it is missing the harvest falls back to pypdf
and says so.

Note: this script never writes inside Out/PDF or Out/CSV. Everything it
produces goes to Out/VERIFY/.
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit("Errore: pypdf non installato. Esegui: pip install -r requirements.txt")

# The detectors, the stamp reader and the corruption classifiers are imported
# rather than reimplemented: every one of them encodes a trap that shipped
# (see AGENTS.md section 4) and a second copy would be a second thing to get
# wrong.
from Reports2PDFTables import (
    CODE_RE,
    INDEX_ROW,
    INLINE_CODE,
    LEADING_CODE,
    MIN_RUN,
    appendix_code,
    STAMP_BAND,
    STAMP_NUM,
    STAMP_SIZE,
    detect_family,
    is_blank,
    is_ciphered,
    is_garbled,
    normalise_digit_one,
    printed_numbers,
    scan_bookmarks,
    stamp_total,
    stamp_tokens,
)

REPORTS_DIR = "reports_185_1990"
OUT_DIR = "Out"
PDF_EXT = ".PDF"

# Where the report of each verdict goes. Kept out of PDF/ and CSV/ so that
# nothing this script writes can be mistaken for pipeline output.
VERIFY_SUBDIR = "VERIFY"

# How many leading pages of a volume are searched for the general INDICE.
# Verified on 2025 (page 3) and 2016 (page 3); twenty is generous and costs
# nothing next to a whole-volume scan.
INDICE_WINDOW = 20

# How far into a file the search for the table's own code looks before
# declaring the file unattested. A table's code repeats on its pages; the
# first page of an export is the one place it is guaranteed to appear.
FIRST_CODE_LOOKUP = 3

# A title from the index is matched against the first page's text by token
# overlap. Low enough for a title the printer rewrapped or abbreviated
# ("Esportazioni definitive per Paesi - Riepilogo"), high enough not to pass
# on a shared word like "Riepilogo".
TITLE_OVERLAP = 0.6

# There is deliberately no minimum fraction here any more. See check_uniqueness:
# a low ratio is the ordinary shape of a real table, not a finding, so the
# threshold only ever produced warnings on correct files.

# Below this share of readable pages, the code checks are skipped rather than
# failed: a file whose text layer was destroyed cannot be asked which table it
# is. Measured against the pages that carry anything at all, so a file that is
# mostly blank separators is not mistaken for a file that is mostly unreadable.
READABLE_MIN_RATIO = 0.1

# Above this share of readable pages, a missing code is a defect; below it, the
# code is probably printed in a font whose mapping was destroyed and the file
# gets a warning instead. 2025 H1 sits at 23/32 readable and still shows its
# code nowhere, because the code lives in the garbled header font.
CODE_READABLE_FOR_FAIL = 0.9

# Per-page classes. Everything that is not `content` is a candidate for the
# leading/trailing checks.
CONTENT = "content"
BLANK = "blank"
INDICE = "indice"
HEADER_ONLY = "header_only"
UNREADABLE = "unreadable"

# The "Pagina N di X" stamp, widened from step 1's `pagina`. The archive also
# prints "Pag." and, in the English-language headings, "Page N of X". The
# garbled branch is not reimplemented: stamp_tokens() is delegated to, since
# its Private Use Area handling is the delicate part.
STAMP_WORD = re.compile(r"(?i)(?:pagina|pag|page)[\s.]*")

# Step 1's INLINE_CODE ends in ([A-Z]{1,3}\d?), which allows at most ONE digit
# after the letters, and its trailing \b then does the rest of the work. That
# combination silently fails on every two-digit code: for "TAB. MT13" the
# greedy [A-Z]{1,3} takes "MT", \d? takes "1", and \b cannot hold between "1"
# and "3"; every shorter reading fails it too, so the whole match is abandoned.
#
#   'TAB. MT7'  -> ['MT7']
#   'TAB. MT13' -> []
#
# The archive really does contain such codes. Tabella MT7 holds a second table
# on its third page whose header reads "TAB. MT13", and the Dogane annexes of
# 2025 name MG13 through MG18 in prose -- all invisible to the shared pattern.
#
# These are the verifier's own patterns rather than edits to the generator's,
# which is left alone here on purpose. A negative lookahead replaces the \b so
# that the digit count cannot decide whether the code is readable.
CODE_TOKEN = r"[A-Z]{1,3}\d{0,2}"

VERIFIER_INLINE = re.compile(
    rf"\b(?i:TAB|TABELLA)\b\.?[^\S\n]+({CODE_TOKEN})(?![A-Za-z0-9])")
VERIFIER_LEADING = re.compile(rf"^(?i:Tabella)\.?\s+({CODE_TOKEN})$")
VERIFIER_CODE = re.compile(rf"^{CODE_TOKEN}$")

# The Dogane appendix: "TAB. M - APPENDICE" is its own table, not more of M.
# Step 1 splits on it and writes MAPPENDICE2025.PDF (see APPENDIX_CODE there),
# so the verifier has to read the same shape -- and read it BEFORE
# VERIFIER_INLINE, which matches the bare "TAB. M" on the very same line and
# would report every appendix file as holding a foreign table.
#
# The suffix is glued to the code, so the code-shape check has to accept it too:
# layout.code_shape compares against this, and volume_vocabulary() admits a code
# only if it matches. Both were written before the split existed.
VERIFIER_APPENDIX = re.compile(
    rf"(?i:T\s*A\s*B\s*\.\s*)({CODE_TOKEN})\s*[-–—]\s*APPENDICE\s*$")
VERIFIER_APPENDIX_CODE = re.compile(rf"^{CODE_TOKEN}APPENDICE$")

# "Tabella" with nothing after it on its own line: the rotated margin stamp,
# which pypdf delivers as two lines -- the keyword on one, the code on the
# next. That is how the 2025 MEF charts print themselves.
BARE_KEYWORD = re.compile(r"(?i)tabella|tab\.|tab")

# --------------------------------------------------------------------------
# the DIFESA annessi, which are not art. 27 codes at all
# --------------------------------------------------------------------------
#
# The Difesa files its tables under art. 2 comma 6 as "annessi" and prints no
# table code anywhere: the running header is
# "MINISTERO DELLA DIFESA - Annesso 3A" and the annesso number *is* the table
# name. So the code space is a different one -- digits, optionally followed by a
# capital (2, 3A, 3B, 3C, 4) -- and neither the code-shape check nor the
# code-on-the-page check could see any of it. All five Difesa files of 2025 were
# reported FAIL on that basis, and every one of them was a correct file.
#
# The ministry tree is what made them visible to the verifier at all: under the
# older flat layout these tables collided with the art. 27 codes on the bare
# code, so none of them had ever been written out.
ANNEXO = re.compile(
    r"(?i:MINISTERO\s+DELLA\s+DIFESA)\s*[-–—]?\s*(?i:Annesso)\s*([0-9]+[A-Z]?)")
ANNEXO_CODE = re.compile(r"^[0-9]+[A-Z]?$")

# How far down a page a bare code line may sit and still be read as the page's
# own stamp. The 2025 charts print theirs in the fourth line; a table cell
# that happens to read as a code sits much further down.
BARE_CODE_MAX_LINE = 8

# A page that carries nothing but the running header and the folio: the
# section's opening page, and the shape a stray front page takes. Four lines
# is the observed floor for a real page with content in it.
HEADER_ONLY_LINES = 4

# Step 1's own cap on how much a table may lose to a trim. A file with more
# unstamped pages at its tail than this is a detection failure, not a pasted
# document, and is reported at that size rather than silently excused.
MAX_TRAILING_PAGES = 25


# ==========================================================================
# verdicts
# ==========================================================================

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


class Checks:
    """The verdict of every check run against one file.

    FAIL is reserved for something demonstrably wrong. WARN means the check
    could not be made here -- no stamp on this volume, no index, garbled
    text -- and is never allowed to fail a run on its own, because a warning
    that turns the whole archive red is a warning nobody reads.
    """

    def __init__(self):
        self.items = []

    def add(self, ident, verdict, detail=""):
        self.items.append({"id": ident, "verdict": verdict, "detail": detail})
        return verdict

    def ok(self, ident, detail=""):
        return self.add(ident, PASS, detail)

    def bad(self, ident, detail):
        return self.add(ident, FAIL, detail)

    def maybe(self, ident, detail):
        return self.add(ident, WARN, detail)

    def skip(self, ident, detail):
        return self.add(ident, SKIP, detail)

    @property
    def verdict(self):
        """The file's verdict: the worst thing found, or SKIP if nothing was.

        SKIP is for a file where *no* check could be made at all -- a
        one-page chart with no stamp to read, on a volume that prints none.
        It is deliberately not "any check was skipped": the naming check runs
        on every file and always passes, so that reading would report SKIP for
        all 81 and the PASS column would be permanently zero, which is the
        opposite of what a verdict column is for.
        """
        if any(c["verdict"] == FAIL for c in self.items):
            return FAIL
        if any(c["verdict"] == WARN for c in self.items):
            return WARN
        if all(c["verdict"] == SKIP for c in self.items):
            return SKIP
        return PASS

    def failing(self):
        return [c for c in self.items if c["verdict"] == FAIL]


def brief(text, limit=110):
    """One-line rendering of a detail string, for the console."""
    flat = re.sub(r"\s+", " ", str(text)).strip()
    return flat if len(flat) <= limit else flat[:limit - 3] + "..."


def spans(pages):
    """[3,4,5,9] -> "3-5, 9", so a page list is readable in a report."""
    pages = sorted(pages)
    if not pages:
        return "-"
    out, start, prev = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append((start, prev))
        start = prev = p
    out.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


# ==========================================================================
# the stamp, widened
# ==========================================================================

def stamp_of(chunk):
    """The (word, first, second) triple of a stamp chunk, or None.

    Same contract as step 1's stamp_tokens(), with the word widened to
    "Pag." and "Page N of X". Delegated for the garbled case, where the
    shape of the Private Use Area runs is the only signal there is.
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
    return stamp_tokens(chunk)


# ==========================================================================
# one pass per page
# ==========================================================================

def page_fingerprint(page):
    """A content hash of one page, for spotting it in two files at once.

    Two output files that share a page must share its content stream, and
    both went through the same PdfWriter, so the bytes are comparable between
    split files even where they are not comparable with the source volume.
    """
    try:
        contents = page.get_contents()
        data = contents.get_data() if contents is not None else b""
    except Exception:
        data = b""
    try:
        box = [round(float(v), 2) for v in page.mediabox]
    except Exception:
        box = []
    digest = hashlib.sha1(data)
    digest.update(repr(box).encode())
    return digest.hexdigest()


def read_page_stamped(page):
    """(full text, stamp chunk as printed) from a single extraction pass.

    Step 1's read_page() already gets both out of one visit, because
    extract_text(visitor_text=...) returns the whole text *and* calls the
    visitor for every chunk. This is the same thing with one addition: the
    matching chunk is kept as text, so a failure can quote the stamp the way
    it is printed -- "Pagina 5 5di" -- instead of describing it. Extracting
    the page a second time just to recover one string would double the cost
    of the whole run.
    """
    try:
        height = float(page.mediabox.height)
    except Exception:
        height = 841.0
    found = []

    def visit(text, cm, tm, font, size):
        chunk = (text or "").strip()
        if not chunk or not size or not STAMP_SIZE[0] <= size <= STAMP_SIZE[1]:
            return
        if not STAMP_BAND[0] * height < tm[5] < STAMP_BAND[1] * height:
            return
        if stamp_of(chunk):
            found.append(chunk)

    return page.extract_text(visitor_text=visit) or "", \
        (found[0] if found else None)


def read_facts(page, fingerprint=True):
    """Everything the checks need from one page, in one extraction pass.

    read_page() already returns the full text and the stamp from a single
    visit; this adds the content hash and keeps the stamp as raw text so a
    failure can be quoted rather than described. Doing it in one pass is the
    difference between a verification run over the archive and one that takes
    three times as long, which on one core is the difference between twenty
    minutes and an hour.
    """
    text, raw = read_page_stamped(page)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return {
        "text": text,
        "lines": lines,
        "stamp": stamp_of(raw) if raw else None,
        "stamp_raw": raw,
        "garbled": is_garbled(text),
        "ciphered": is_ciphered(text),
        "blank": is_blank(lines),
        "folio": sorted(printed_numbers(lines)),
        "fingerprint": page_fingerprint(page) if fingerprint else None,
    }


def looks_like_index_page(facts, top=12, min_codes=2):
    """True for a page that is an index OF TABLES, not a page that mentions one.

    Deliberately not is_index_page(). That test asks whether "SEGNALAZIONI"
    appears in the top lines, and it is right for step 1's purpose -- skipping
    index pages while hunting for table starts -- but as a *classifier* it is
    wrong twice over. The 2025 MEF table pages carry SEGNALAZIONI in a running
    header of their own, so three ordinary data pages of Tabella P2 came back
    "indice" and were reported as trailing junk that does not exist. And the
    MEF chart pages print "ELENCO TABELLE" in that same running header while
    being a single table, so trusting the marker condemns NN, OO, PP and GF.

    What actually separates an index from a page that mentions tables is how
    many it names. The 2025 MEF ELENCO lists fifteen codes on one page; a
    chart page names one, its own. So the count decides, and the markers only
    have to corroborate.
    """
    lines = facts["lines"]
    head = " ".join(lines[:top]).upper()
    named = set()
    for line in lines[:top]:
        for m in INLINE_CODE.finditer(line):
            named.add(m.group(1).upper())
        m = LEADING_CODE.match(line)
        if m:
            named.add(m.group(1).upper())
    if len(named) >= min_codes:
        return True
    return any(marker in head for marker in ELENCO_MARKERS) and len(named) >= 1


def classify_page(facts, lines_limit=HEADER_ONLY_LINES):
    """Which of the non-content classes a page belongs to."""
    if facts["blank"]:
        return BLANK
    if looks_like_index_page(facts):
        return INDICE
    if facts["garbled"] or facts["ciphered"]:
        return UNREADABLE
    if len(facts["lines"]) <= lines_limit and not facts["folio"]:
        return HEADER_ONLY
    return CONTENT


# ==========================================================================
# the code printed on a page
# ==========================================================================

def fold(code, vocabulary):
    """The code as this volume knows it.

    normalise_digit_one() exists for one reason: the 2012 index prints a
    capital I where a digit one belongs, listing "Tabella DI" and "Tabella Gl"
    where the real codes are D1 and G1. Applied without a guard it destroys
    genuine codes, because the 2025 art. 27 scheme has *both* "Tabella II" and
    "Tabella II1" as distinct tables -- folding maps II and II1 onto the same
    I1, after which neither is recognisable. Verified: II's own first page
    reads "Tabella II", and the fold turned it into a code no index lists.

    So the code as printed wins whenever the volume knows it, and the fold is
    only a fallback for a volume that needs it.
    """
    raw = code.upper()
    if raw in vocabulary:
        return raw
    folded = normalise_digit_one(raw)
    return folded if folded in vocabulary else raw


def page_code(lines, vocabulary):
    """The table code printed on a page, if it is one this volume knows.

    Deliberately not page_code() from step 1. That one is family-gated -- on
    a family-2 volume it will only accept the 31 A1..P2 codes -- and the 2025
    volumes are mixed: one volume carries the MAE A1..P2 detail tables and
    the MEF art. 27 tables AA..UE and the Dogane annexes M..P at the same
    time. A verifier that refused to see a code because of the volume's family
    label would be unable to report the tables it exists to report.

    What it will not do is invent one: a candidate has to be in the
    volume's vocabulary, and that vocabulary is built from codes that repeat
    (see volume_vocabulary), not from a whitelist of shapes.

    Three shapes, tried strongest first. "Tabella AA" alone on its line is
    the plain case. "Tabella" alone on its line with the code alone on the
    next one is the rotated margin stamp, which pypdf hands over as two
    separate lines -- that is how the 2025 MEF charts print themselves, and
    neither LEADING_CODE nor INLINE_CODE can see it. A bare code on its own
    line is the last resort, and is only safe because the vocabulary is
    already restricted to codes that repeat somewhere in the volume.
    """
    # A Difesa annesso carries no art. 27 code anywhere on the page, so it is
    # read first and from its own marker: "MINISTERO DELLA DIFESA - Annesso 3A".
    # Without this the page reads as having no code at all, and
    # uniqueness.own_code_absent then fails a file that is nothing but table.
    m = ANNEXO.search("\n".join(lines))
    if m:
        return m.group(1).upper()

    # The appendix, first of the code shapes. Step 1's own appendix_code(), not
    # a second copy of it: the pattern encodes the shredding trap, and a second
    # implementation would be a second thing to get wrong.
    appendix = appendix_code(lines)
    if appendix:
        return appendix

    for line in lines:
        m = VERIFIER_LEADING.match(line)
        if m:
            cand = fold(m.group(1), vocabulary)
            if cand in vocabulary:
                return cand

    for i, line in enumerate(lines):
        if not BARE_KEYWORD.fullmatch(line):
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        cand = fold(nxt, vocabulary)
        if VERIFIER_CODE.match(nxt) and cand in vocabulary:
            return cand

    for line in lines:
        for m in VERIFIER_INLINE.finditer(line):
            cand = fold(m.group(1), vocabulary)
            if cand in vocabulary:
                return cand

    # A bare code, but only in the header region. The margin stamp sits at the
    # top of the page; a bare capital in the body is a table cell, and 2025
    # has one that reads as a code -- the "I" in a list of MG-table numbers
    # inside the Dogane annexes, 108 lines down a page. The positional gate
    # here is not the discovery gate AGENTS.md warns about: the code is
    # already known to the volume, and the position only decides whether a
    # known code is being *confirmed* as this page's own.
    for line in lines[:BARE_CODE_MAX_LINE]:
        cand = fold(line.upper(), vocabulary)
        if VERIFIER_CODE.match(line) and cand in vocabulary:
            return cand
    return None


def volume_vocabulary(hits, index_codes, bookmark_codes, manifest_codes):
    """Every table code the volume shows any sign of.

    Deliberately not restricted to the codes that repeat. The frequency rule
    in AGENTS.md -- a real table repeats its code, a prose mention appears
    once -- is a rule about *concluding* that something is a table, and it is
    applied where that conclusion is drawn, in check_uniqueness. Applying it
    here instead would mean a code seen twice anywhere in the volume is
    admitted and everything else is invisible, which loses exactly the
    evidence an audit is for: 2025 Tabella MT7 carries "TAB. MT13" on two
    pages, MT13 appears nowhere else, and a frequency-filtered vocabulary
    would never let the verifier mention it.

    So the vocabulary answers "what codes exist here", and the repetition
    count decides how loudly each one is reported.
    """
    vocab = {c for c in hits if VERIFIER_CODE.match(c) or
             VERIFIER_APPENDIX_CODE.match(c)}
    vocab |= set(index_codes)
    vocab |= set(bookmark_codes)
    vocab |= set(manifest_codes)
    return vocab


# ==========================================================================
# per-file checks
# ==========================================================================

def check_layout(path, code, year, known_years, checks, authority="",
                 article=""):
    """The naming and layout invariant.

    Under the flat shape the folder is named for the code and must be, because
    that folder *is* the table's identity. Under the ministry tree the code is
    carried by the filename alone and the folders above it are the authority
    and, optionally, the article, so the folder-name rule does not apply and
    asserting it would fail every file the moment the writer gained a level.
    What holds for both is that the filename is <code><anno>.PDF and that the
    year is a reporting year.
    """
    folder = path.parent.name
    stem = path.stem
    if not authority and folder != code:
        checks.bad("layout.folder_name",
                   f"cartella '{folder}' != codice '{code}'")
    if not stem.startswith(code):
        checks.bad("layout.file_stem",
                   f"nome file '{stem}' non comincia con il codice '{code}'")
    suffix = stem[len(code):] if stem.startswith(code) else ""
    if suffix != year:
        checks.bad("layout.year_suffix",
                   f"suffisso '{suffix}' != anno '{year}'")
    if known_years and year not in known_years:
        checks.bad("layout.known_year",
                   f"anno '{year}' non presente in manifest.tsv")
    # Three code spaces, not one. art. 27 families print AA/AA1/MG13/UE, the
    # family-2 ministry prints A1/P2, and the Difesa annexes are numbered 2, 3A,
    # 4. Judging a Difesa annesso against the art. 27 shape fails a correct file,
    # which is what happened to all five of them in 2025.
    if not (CODE_RE.match(code) or VERIFIER_CODE.match(code)
            or ANNEXO_CODE.match(code) or VERIFIER_APPENDIX_CODE.match(code)):
        checks.bad("layout.code_shape",
                   f"codice '{code}' non ha forma art. 27, ne di annesso Difesa, "
                   f"ne di appendice Dogane")
    if not any(c["verdict"] == FAIL for c in checks.items):
        where = "/".join(filter(None, [authority, article])) or "flat"
        checks.ok("layout", f"{where}/{stem}{PDF_EXT}")


def check_uniqueness(pages, code, checks):
    """One file, one table.

    Two independent witnesses, because either alone has a blind spot. The
    stamp knows the file's true length but says nothing when the volume
    carries no stamp. The per-page code knows what is on each page but cannot
    read a garbled one.
    """
    hits = Counter()
    where = defaultdict(list)
    for no, facts in pages.items():
        found = page_code(facts["lines"], facts["_vocab"])
        if found:
            hits[found] += 1
            where[found].append(no)
        facts["_code"] = found

    own = hits.get(code, 0)
    legible = [f for f in pages.values() if not f["blank"]]
    readable = [f for f in legible
                if not f["garbled"] and not f["ciphered"]]
    unreadable = len(pages) - len(readable)

    # On a file whose text layer was destroyed at PDF generation time the
    # code cannot be read at all, and saying "the code E is absent" would be
    # reporting the verifier's own blindness as a defect. Those files are
    # skipped here and judged on the stamp instead, which needs the numbers
    # only compared, never read -- 2025 Tabella E is garbled on all 221 of
    # its pages and still says exactly whether it is one document.
    if not legible or len(readable) < READABLE_MIN_RATIO * len(legible):
        checks.skip("uniqueness.code_run",
                    f"testo illeggibile su {unreadable}/{len(pages)} pagine: "
                    f"il codice non e' leggibile")
        return {"hits": dict(hits), "where": {c: where[c] for c in hits},
                "own": own, "readable": len(readable), "legible": len(legible)}

    # A code other than ours. Repeating for MIN_RUN pages, it is a second table
    # pasted into the file and a failure. Seen once, it is reported as a
    # mention instead of dropped: that is the difference between Tabella M,
    # whose tail of 21 pages prints "TAB. N" and is named exactly, and
    # Tabella MT7, whose third and fourth pages print "TAB. MT13" and which
    # would otherwise look clean. One hit cannot be dismissed -- it may be a
    # one-page table -- but it must not be asserted as one either.
    repeated = {c: n for c, n in hits.items() if c != code and n >= MIN_RUN}
    mentioned = {c: n for c, n in hits.items() if c != code and n < MIN_RUN}
    if repeated:
        detail = "; ".join(
            f"TAB {c} su pp {spans(where[c])} ({n}pp)" for c, n in
            sorted(repeated.items(), key=lambda kv: -kv[1]))
        checks.bad("uniqueness.foreign_table", detail)
    else:
        checks.ok("uniqueness.foreign_table", "nessun codice estraneo ripetuto")

    if mentioned:
        detail = "; ".join(
            f"TAB {c} su pp {spans(where[c])}" for c, n in
            sorted(mentioned.items(), key=lambda kv: -kv[1]))
        checks.maybe("uniqueness.foreign_table_mention",
                     f"codici estranei nominati una volta sola: {detail}")

    if own == 0:
        # "The code is not on any page" and "the page that carries the code
        # cannot be read" are different findings, and only the first is a
        # defect. 2025 Tabella H1 prints its code in a subsetted font: 9 of
        # its 32 pages come back as Private Use Area and the readable ones
        # carry the code nowhere legible either. Calling that a failure would
        # be the verifier reporting its own blindness, so a file whose text
        # layer is largely destroyed is a warning.
        share = len(readable) / max(1, len(legible))
        if share >= CODE_READABLE_FOR_FAIL:
            checks.bad("uniqueness.own_code_absent",
                       f"il codice '{code}' non compare su nessuna pagina, "
                       f"e le pagine sono leggibili ({share:.0%})")
        else:
            checks.maybe("uniqueness.own_code_absent",
                         f"il codice '{code}' non compare su nessuna pagina "
                         f"leggibile: {unreadable}/{len(pages)} illeggibili")
    else:
        # A low ratio is NOT reported, and deliberately so. A table printing its
        # code on its first page and a plain running header after that is the
        # ordinary shape of a real table, not a finding: 41 pages of Tabella EE
        # carry "EE" once, and the nine Dogane appendices carry theirs on the
        # cover alone. The ratio cannot separate that from a prose mention --
        # 1/41 and 1/6 are the same shape -- so it was never going to decide
        # anything, and gating it on an attestation only produced a WARN on 21
        # correct files in 2025.
        #
        # What is left still catches a file holding the wrong table:
        # foreign_table above, for another code repeated over MIN_RUN pages, and
        # own_code_absent above, for this file's own code being absent from every
        # legible page. The count is kept in the detail because it is the
        # cheapest thing in the report for a reader to judge by eye.
        checks.ok("uniqueness.own_code_run",
                  f"'{code}' su {own}/{len(legible)} pagine")

    return {"hits": dict(hits), "where": {c: where[c] for c in hits},
            "own": own, "readable": len(readable), "legible": len(legible)}


def check_stamp(pages, checks):
    """The "Pagina N di X" witness: one document, of a known length.

    The stamp is the only thing in the archive that records a table's real
    length, because each table was exported as a document of its own and
    pasted into the volume afterwards. The volume folio runs continuously
    across tables and says nothing about where one ends.

    The total is settled by stamp_total(), never by position: the rotation
    reverses the two numbers from page to page (2025 vol. I p70 reads
    "Pagina 1 di 537" and p611 "Pagina 5 5di"), so which of the two is the
    total is decided by watching which one does not change.
    """
    stamps = {no: f["stamp"] for no, f in pages.items() if f["stamp"]}
    if not stamps:
        checks.skip("completeness.stamp",
                    "nessun 'Pagina N di X' su questa tabella: "
                    "il volume non porta il timbro")
        return {"pages_with_stamp": 0, "total": None, "readable": False,
                "agrees": None, "coverage": 0.0}

    first, last = min(pages), max(pages)
    slot, wanted = stamp_total(stamps, first, last)
    readable = wanted.isdigit()
    agrees = len({s[slot] for s in stamps.values()}) == 1
    coverage = len(stamps) / max(1, len(pages))

    result = {
        "pages_with_stamp": len(stamps),
        "total": wanted,
        "readable": readable,
        "agrees": agrees,
        "coverage": round(coverage, 4),
    }

    if not agrees:
        seen = sorted({s[slot] for s in stamps.values()})
        checks.bad("uniqueness.stamp_total_varies",
                   f"il totale cambia dentro il file: {seen} "
                   f"-> piu' documenti in un file solo")
    else:
        checks.ok("uniqueness.stamp_total_varies", f"totale unico: {wanted}")

    if readable:
        total = int(wanted)
        if total == len(pages):
            checks.ok("completeness.stamp_total",
                      f"'Pagina 1 di {total}', file di {total} pagine")
        elif total < len(pages):
            checks.bad("completeness.stamp_total",
                       f"il timbro dichiara {total} pagine, il file ne ha "
                       f"{len(pages)}: {len(pages) - total} pagine di troppo")
        else:
            checks.bad("completeness.stamp_total",
                       f"il timbro dichiara {total} pagine, il file ne ha "
                       f"{len(pages)}: mancano {total - len(pages)} pagine")
        opening = stamps[first][3 - slot]
        if opening.isdigit() and opening != "1":
            checks.maybe("completeness.first_page_is_one",
                         f"la prima pagina dichiara '{opening}', non 1")
        else:
            checks.ok("completeness.first_page_is_one", "")
    else:
        # Garbled: the digits are Private Use Area characters, so the total
        # cannot be read, only compared. It was compared above; what is left
        # is that the count itself is unverifiable here.
        checks.maybe("completeness.stamp_total",
                     f"totale non leggibile (font senza ToUnicode), "
                     f"ma stabile su {len(stamps)} pagine")

    missing = [no for no in range(first, last + 1) if no not in stamps]
    if not missing:
        checks.ok("completeness.stamp_coverage",
                  f"timbro su {len(stamps)}/{len(pages)} pagine")
    else:
        head = [m for m in missing if m <= first + 2]
        tail = [m for m in missing if m >= last - 2]
        where = []
        if tail:
            where.append(f"in coda pp {spans(tail)}")
        if head:
            where.append(f"in testa pp {spans(head)}")
        if not where:
            where.append(f"a interno pp {spans(missing)}")
        if tail and not head and len(tail) <= MAX_TRAILING_PAGES:
            checks.bad("completeness.stamp_coverage",
                       f"{len(missing)} pagine senza timbro {' e '.join(where)}: "
                       f"pagine incollate da un altro documento")
        elif head:
            checks.bad("completeness.stamp_coverage",
                       f"{len(missing)} pagine senza timbro {' e '.join(where)}")
        else:
            checks.maybe("completeness.stamp_coverage",
                         f"{len(missing)} pagine senza timbro "
                         f"{' e '.join(where)}")

    return result


def check_edges(pages, checks):
    """Nothing but the table: no blank, no index, no foreign page at the ends.

    A table often closes on a differently shaped page -- a totals sheet, a
    chart -- and dropping those loses real figures, so a page with content is
    never a violation. What is a violation is a page with no content of its
    own: the "PAGINA BIANCA" separators that close a volume, an ELENCO page,
    or the running-header-only page a section opens with.

    The distinction that matters is hard versus soft. A blank separator or an
    index page pasted in from the volume is a failure. A running-header page
    may legitimately open a section, and a differently shaped tail page may
    legitimately close the table, so those are warnings -- and the count is
    still reported, because "three pages at the end that are not the table" is
    worth knowing about even when it is not an error.
    """
    classes = {no: classify_page(f) for no, f in pages.items()}
    with_content = [no for no in sorted(classes) if classes[no] == CONTENT]
    if not with_content:
        # Every page unreadable means the check cannot see, not that the file
        # is empty. Reporting that as a failure would make the verifier
        # condemn the one class of table it is least able to judge.
        dark = sum(1 for k in classes.values() if k == UNREADABLE)
        if dark:
            checks.skip("edges.no_content_page",
                        f"testo illeggibile su {dark}/{len(classes)} pagine: "
                        f"i bordi del file non sono distinguibili")
        else:
            checks.bad("edges.no_content_page",
                       "nessuna pagina con contenuto di tabella")
        return classes

    first, last = with_content[0], with_content[-1]
    # An unreadable page is not leading junk and not trailing junk. It is a
    # page whose text layer was destroyed, which says nothing about where the
    # table begins or ends -- counting 221 garbled pages of 2025 Tabella E as
    # "leading junk" would bury the one real finding, which is the single
    # legible page at the end that carries a different document's stamp.
    dark = [no for no in sorted(classes) if classes[no] == UNREADABLE]
    lead = [(no, classes[no]) for no in sorted(classes)
            if no < first and classes[no] not in (CONTENT, UNREADABLE)]
    trail = [(no, classes[no]) for no in sorted(classes)
             if no > last and classes[no] not in (CONTENT, UNREADABLE)]
    interior = [no for no in sorted(classes)
                if first < no < last and classes[no] not in (CONTENT, UNREADABLE)]

    if dark:
        checks.maybe("edges.unreadable_pages",
                     f"pp {spans(dark)} a leggibilita' parziale o nulla: "
                     f"su quelle pagine i bordi del file non sono verificabili")

    def verdict_for(entries, ident, label):
        if not entries:
            checks.ok(ident, f"nessuna pagina {label}")
            return
        detail = ", ".join(f"p{no} {kind}" for no, kind in entries)
        if any(kind in (BLANK, INDICE) for _, kind in entries):
            checks.bad(ident, detail)
        else:
            checks.maybe(ident, detail)

    verdict_for(lead, "edges.leading_junk", "preliminare")
    verdict_for(trail, "edges.trailing_junk", "finale")

    if interior:
        checks.maybe("edges.interior_noncontent",
                     f"pp {spans(interior)} senza un codice proprio "
                     f"(possibile foglio di totali)")
    elif not dark:
        checks.ok("edges.interior_noncontent", "")

    return classes


def check_folio(pages, checks):
    """Printed page numbers must run consecutively.

    This is where the volume joins show up, and it is why the check reports
    both readings instead of choosing. 2021 tom. II prints the volume-local
    number next to the volume-wide one, so a page offers several candidates
    and any pairing may be the intended one. 2019 restarts its numbering at
    the volume boundary. 2025 F1 runs from printed 1035 in volume I to 1104
    in volume II. None of those is an error and none may be reported as one.
    """
    numbered = [no for no in sorted(pages) if pages[no]["folio"]]
    if len(numbered) < 2:
        checks.skip("completeness.folio",
                    f"{len(numbered)} pagine con numero stampato")
        return

    breaks = []
    for prev, cur in zip(numbered, numbered[1:]):
        a, b = set(pages[prev]["folio"]), set(pages[cur]["folio"])
        if b & {n + 1 for n in a}:
            continue
        breaks.append((prev, cur, sorted(a), sorted(b)))

    if not breaks:
        checks.ok("completeness.folio",
                  f"numeri consecutivi su {len(numbered)} pagine")
        return

    if len(breaks) == 1:
        prev, cur, a, b = breaks[0]
        detail = f"p{prev} {a} -> p{cur} {b}"
        checks.maybe("completeness.folio",
                     f"interruzione: {detail} (riavvio di numerazione o "
                     f" confine di volume)")
    else:
        detail = "; ".join(f"p{p} {x} -> p{c} {y}" for p, c, x, y in breaks[:3])
        checks.maybe("completeness.folio",
                     f"{len(breaks)} interruzioni: {detail}")


def check_title(pages, entry, checks):
    """The index's title for this code, found on the file's first page.

    A cheap but sharp check: the index is human-authored and gives every code
    a title, so a file named AA whose first page carries AA1's title is
    misfiled even though AA appears somewhere inside it.
    """
    if not entry or not entry.get("title"):
        checks.skip("identity.title", "nessun titolo nell'indice")
        return
    title = re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", entry["title"].lower())
    if not title:
        checks.skip("identity.title", "titolo non confrontabile")
        return
    head = " ".join(pages[no]["text"] for no in sorted(pages)
                    [:FIRST_CODE_LOOKUP + 2]).lower()
    head_tokens = set(re.findall(r"[a-zà-ÿ0-9]{3,}", head))
    if not head_tokens:
        checks.skip("identity.title", "testo della prima pagina illeggibile")
        return
    overlap = sum(1 for t in set(title) if t in head_tokens) / len(set(title))
    if overlap >= TITLE_OVERLAP:
        checks.ok("identity.title",
                  f"titolo dall'indice presente in prima pagina "
                  f"({overlap:.0%})")
    else:
        checks.bad("identity.title",
                   f"il titolo dell'indice non compare in prima pagina "
                   f"({overlap:.0%}): atteso '{entry['title']}'")


def verify_file(path, code, year, vocab, entry, known_years,
                fingerprint=True, sample=0, attested=False, authority="",
                article=""):
    """Every per-file check, and the evidence they ran on."""
    checks = Checks()
    check_layout(path, code, year, known_years, checks,
                 authority=authority, article=article)

    try:
        reader = PdfReader(str(path), strict=False)
        total = len(reader.pages)
    except Exception as exc:
        checks.bad("read", f"apertura fallita: {exc}")
        return {"path": str(path), "code": code, "year": year,
                "pages": 0, "checks": checks.items,
                "verdict": checks.verdict}

    wanted = range(1, total + 1)
    if sample:
        picked = sorted({1, total, total // 2 + 1} |
                        set(list(wanted)[::max(1, total // sample)]))
        wanted = picked

    pages, read_errors = {}, []
    for no in wanted:
        try:
            facts = read_facts(reader.pages[no - 1], fingerprint)
        except Exception as exc:
            # Recorded and failed on, never quietly turned into a blank page.
            # Swallowing this is what turned a missing import into 81 files
            # reported as "no content page" instead of 81 crashes.
            read_errors.append(f"p{no}: {type(exc).__name__}: {exc}")
            facts = {"text": "", "lines": [], "stamp": None,
                     "stamp_raw": None,
                     "garbled": False, "ciphered": False, "blank": False,
                     "folio": [], "fingerprint": None,
                     "read_error": str(exc)}
        facts["_vocab"] = vocab
        pages[no] = facts

    if read_errors:
        checks.bad("read.page_error",
                   f"{len(read_errors)} pagine non leggibili: "
                   f"{brief(read_errors[0])}")

    codes = check_uniqueness(pages, code, checks)
    stamp = check_stamp(pages, checks)
    classes = check_edges(pages, checks)
    check_folio(pages, checks)
    check_title(pages, entry, checks)

    garbled = sum(1 for f in pages.values() if f["garbled"])
    ciphered = sum(1 for f in pages.values()
                   if f["ciphered"] and not f["garbled"])

    return {
        "path": str(path),
        "code": code,
        "year": year,
        "authority": authority,
        "article": article,
        "pages": total,
        "pages_read": len(pages),
        "sampled": bool(sample),
        "codes": {c: {"hits": codes["hits"][c],
                      "where": spans(codes["where"][c])}
                  for c in codes["hits"]},
        "own_code_pages": codes["hits"].get(code, 0),
        "attested": attested,
        # Kept so that the year-level overlap check can compare files against
        # each other after the per-file pass is over.
        "fingerprints": {str(no): f["fingerprint"]
                         for no, f in pages.items() if f["fingerprint"]},
        "page_classes": {str(no): kind for no, kind in sorted(classes.items())},
        "stamp": stamp,
        "legibility": {"pages": total, "garbled": garbled,
                       "ciphered": ciphered},
        "title_expected": (entry or {}).get("title"),
        "checks": checks.items,
        "verdict": checks.verdict,
    }


# ==========================================================================
# indices
# ==========================================================================

# "PRESIDENZA DEL CONSIGLIO DEI MINISTRI . . . . . Pag. 1"
# "Ministero degli affari esteri . . . » 1"     the "»" means "same as above"
INDICE_ROW = re.compile(
    r"^(?P<name>[A-Za-zÀ-ÿ][^.]*?)\s*\.{2,}\s*(?:Pag\.?\s*)?(?P<ditto>»)?\s*"
    r"(?P<page>\d{1,4})\s*$"
)
VOLUME_SPLIT = re.compile(r"^\s*Volume\s+(I{1,3}V?|IV)\s*$", re.IGNORECASE)

# "Tabella AA" on a line of its own, or "Tab. AA - Appendice - Riesportazioni"
ELENCO_CODE = re.compile(
    r"^(?:Tabella|Tab\.?)\s+(?P<code>[A-Z]{1,3}\d?)\b\s*(?P<rest>.*)$"
)

# The markers that open an index of tables. SEGNALAZIONI is honoured only in
# the top lines: on 2025 vol. II the table pages print it in a trailing
# header block too, and a plain substring test throws away the whole volume.
ELENCO_MARKERS = ("ELENCO TABELLE", "ELENCO GRAFICI", "ELENCO DELLE TABELLE")


def page_texts(pdf, first=None, last=None):
    """{page: text} for a volume, by whichever extractor is available.

    pdftotext is roughly five times quicker than pypdf over a whole volume
    and its output is identical for this purpose, so it is preferred for the
    index harvest -- the one pass that has to look at every page. pypdf is
    the fallback, and which one ran is reported so a slow run is explained
    rather than mysterious.
    """
    tool = shutil.which("pdftotext")
    if tool:
        # The trailing "-" is load-bearing. Given only an input file,
        # pdftotext writes <input>.txt next to the PDF and prints nothing on
        # stdout, so capture_output=True yields an empty string, the page
        # dictionary collapses to a single blank page, and every harvest over
        # the volume silently returns nothing. It looks like a working run:
        # the exit status is 0 and the fallback is never reached.
        cmd = [tool, "-layout", "-enc", "UTF-8"]
        if first:
            cmd += ["-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        cmd += [str(pdf), "-"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=900)
            body = out.stdout.decode("utf-8", "replace")
            if out.returncode == 0 and body.strip():
                offset = (first or 1) - 1
                pages = dict(enumerate(body.split("\f"), start=offset + 1))
                return pages, "pdftotext"
        except Exception:
            pass
    reader = PdfReader(str(pdf), strict=False)
    last = last or len(reader.pages)
    return ({i + 1: (reader.pages[i].extract_text() or "")
             for i in range((first or 1) - 1, min(last, len(reader.pages)))},
            "pypdf")


def harvest_general_indice(texts, window=INDICE_WINDOW):
    """The volume's own table of contents.

    Verified on 2025 (page 3, both volumes) and 2016 (page 3). Every entry is
    an authority with the printed page it starts on, and the "Tabelle" rows
    under it are where that authority's tables begin -- which is the only
    place the archive states the authority a table belongs to. The entries
    are split on "Volume I" / "Volume II" because several volumes restart
    their numbering, and a page number means nothing without knowing which
    numbering it is in.
    """
    best = []
    for page in sorted(texts)[:window]:
        lines = [ln.rstrip() for ln in texts[page].split("\n") if ln.strip()]
        if not any(re.search(r"\bINDICE\b", ln) for ln in lines):
            continue
        entries, volume = [], None
        for raw in lines:
            stripped = raw.strip()
            m = VOLUME_SPLIT.match(stripped)
            if m:
                volume = m.group(1).upper()
                continue
            m = INDICE_ROW.match(stripped)
            if not m:
                continue
            name = re.sub(r"\s+", " ", m.group("name")).strip()
            if len(name) < 3:
                continue
            entries.append({
                "authority": name,
                "page": int(m.group("page")),
                "ditto": bool(m.group("ditto")),
                "volume": volume,
                "kind": classify_indice_entry(name),
            })
        if len(entries) > len(best):
            best = entries
    return best


def classify_indice_entry(name):
    """'Tabelle', 'Relazione', an authority heading, or a numbered section.

    The archive nests three levels under one another and only the first is an
    authority, so the level has to be read off the name rather than assumed.
    """
    low = name.strip().lower()
    if low.startswith("tabelle"):
        return "tables"
    if low.startswith("relazione") or low.startswith("considerazioni") \
            or low.startswith("attivit") or low.startswith("annessi"):
        return "relation"
    if re.match(r"^\d+(\.\d+)*\b", low):
        return "section"
    if name.isupper() or low.startswith("ministero") \
            or low.startswith("presidenza") or low.startswith("agenzia"):
        return "authority"
    return "other"


def harvest_elenco(texts):
    """Every index of tables printed inside a volume.

    Two layouts are read. The 2025 one lists a code and its title on
    alternating lines under an ELENCO TABELLE / ELENCO GRAFICI heading and
    carries no page numbers. The 2020 and 2022 one carries a row count and a
    start page per code, which is the only place the archive states how long
    a table is, independently of the stamp.

    The marker is looked for in the top lines only, for the reason recorded
    in AGENTS.md section 4: on 2025 vol. II the table pages themselves print
    SEGNALAZIONI in a trailing header block.
    """
    found = defaultdict(list)
    for page in sorted(texts):
        lines = [ln.strip() for ln in texts[page].split("\n") if ln.strip()]
        head = " ".join(lines[:12]).upper()
        if not any(marker in head for marker in ELENCO_MARKERS):
            continue
        for i, line in enumerate(lines):
            m = ELENCO_CODE.match(line)
            if not m:
                continue
            # No digit-one fold here. The index is human-authored and prints
            # the code as it is, and 2025 uses I as a letter: "Tabella II" and
            # "Tabella II1" are two different tables. Folding them onto I1
            # loses one of them and invents a code the index never mentions.
            code = m.group("code").upper()
            title = m.group("rest").strip()
            if not title:
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if nxt and not ELENCO_CODE.match(nxt):
                    title = nxt
            found[code].append({
                "code": code,
                "title": re.sub(r"\s+", " ", title).strip(),
                "page": page,
            })
    return {code: rows[0] for code, rows in found.items()}


def harvest_index_rows(texts):
    """The 2020/2022 ELENCO layout: a row count and a start page per code.

    "2 401  TAB A1  Esportazione definitiva per operatori  469". When a
    volume offers this, its page counts are a witness independent of the
    stamp, and a table whose span disagrees with both is wrong.
    """
    counts = {}
    for page in sorted(texts):
        for line in texts[page].split("\n"):
            m = INDEX_ROW.match(line)
            if m:
                counts[normalise_digit_one(m.group(2).upper())] = {
                    "pages": int(m.group(1)),
                    "at_page": int(m.group(4)),
                    "title": m.group(3).strip(),
                    "printed_on": page,
                }
    return counts


# ==========================================================================
# provenance: every page of every volume, accounted for
# ==========================================================================

NON_TABLE_CLASSES = {
    "cover": "cover, PAGINA BIANCA and the volume's front matter",
    "indice": "the INDICE and the ELENCO TABELLE pages",
    "blank": "PAGINA BIANCA separators",
    "relation": "the prose relation of an authority",
    "signature": "the closing page of a relation",
}


def check_provenance(volumes, files):
    """Every physical page claimed by exactly one table, or by none.

    The step 1 manifest is the source of the spans here. Where it is missing
    this check is skipped rather than guessed at: reconstructing spans by
    matching fingerprints against the volume would cost a full pass per
    volume and would reproduce the very detector whose output is under
    examination.

    A page claimed twice is a failure -- two files ship the same page. A page
    claimed by nobody is a warning, because a volume legitimately contains
    pages no table owns, and the point is to list them rather than to say
    the split is broken.
    """
    claims = defaultdict(list)
    cont_pages = 0
    for volume in volumes:
        manifest = volume.get("manifest")
        if not manifest:
            return None, "nessun manifest.json: le spanse non sono verificabili"
        for info in manifest_tables(manifest):
            code = info.get("code")
            if not code or info.get("kept") is False:
                continue
            for p in range(info["start"], info["end"] + 1):
                claims[(volume["report"], p)].append(
                    (code, info.get("source"), info.get("pages")))
        # The leading pages of a table cut at the volume join. They ARE in the
        # exported PDF -- split_pdf appends this segment to the same writer --
        # so claiming them is not a favour, it is the same fact the tables loop
        # above already states. Without this the check calls 62 real pages of
        # 2025 F1 unattributed. `kept` is absent here and that is correct: the
        # record exists only when the segment was actually written.
        for cont in manifest_continued(manifest):
            code = cont.get("code")
            if not code:
                continue
            cont_pages += cont.get("pages") or (cont["end"] - cont["start"] + 1)
            for p in range(cont["start"], cont["end"] + 1):
                claims[(volume["report"], p)].append(
                    (code, "continued", cont.get("total_pages")))

    duplicated, unattributed = [], []
    for volume in volumes:
        pages = volume["pages"]
        manifest = volume.get("manifest")
        tables = {}
        if manifest:
            tables = {i["code"]: i for i in manifest_tables(manifest)
                      if i.get("code") and i.get("kept") is not False}
            # Same reason as the claims loop: p5 of a continued table is that
            # table's first page in this volume, which is what these flags mean.
            # Namespaced so a code in both lists does not overwrite.
            for cont in manifest_continued(manifest):
                if cont.get("code"):
                    tables.setdefault(f"{cont['code']}@continued", cont)
        starts = {i["start"] for i in tables.values()}
        ends = {i["end"] for i in tables.values()}
        for p in range(1, pages + 1):
            holders = claims.get((volume["report"], p), [])
            if len(holders) > 1:
                duplicated.append({
                    "report": Path(volume["report"]).name, "page": p,
                    "claimed_by": [h[0] for h in holders],
                })
            elif not holders:
                unattributed.append({
                    "report": Path(volume["report"]).name, "page": p,
                    "at_table_start": p in starts,
                    "at_table_end": p in ends,
                })

    return {
        "volumes": len(volumes),
        "pages": sum(v["pages"] for v in volumes),
        "claimed_pages": sum(len(v) for v in claims.values()),
        "continued_pages": cont_pages,
        "duplicated_pages": duplicated,
        "unattributed_pages": unattributed,
        "non_table_classes": NON_TABLE_CLASSES,
    }, ""


def check_page_overlap(results, sampled=False):
    """The same page shipped in two different files.

    The manifest cannot catch this, and it is worth being explicit about why.
    Every table's end is derived from the next table's start, so spans are
    contiguous by construction and can never overlap -- asking the manifest
    whether two files share a page always answers "no" whether or not they
    do. The content hash is the only witness that does not inherit step 1's
    assumption, which is why 2025 Tabella M holding 21 pages of Tabella N is
    invisible to the provenance check and obvious here.

    Both files went through the same PdfWriter, so a page that was copied into
    two of them has the same content stream in both.
    """
    seen = defaultdict(list)
    for result in results:
        for page, digest in (result.get("fingerprints") or {}).items():
            seen[digest].append((result["code"], int(page)))

    groups = defaultdict(lambda: defaultdict(list))
    for holders in seen.values():
        if len({code for code, _ in holders}) > 1:
            key = tuple(sorted({code for code, _ in holders}))
            for code, page in holders:
                groups[key][code].append(page)

    # One message per affected file, naming every partner and the pages on
    # both sides. Grouped by the digest set rather than by file, because a
    # file's own bucket cannot say where the partner's pages are.
    per_file = defaultdict(list)
    shared_pages = 0
    for key, by_code in groups.items():
        shared_pages += sum(len(p) for p in by_code.values()) // 2
        for code, pages in by_code.items():
            for other, other_pages in sorted(by_code.items()):
                if other != code:
                    per_file[code].append((other, pages, other_pages))

    for result in results:
        partners = per_file.get(result["code"])
        if not partners:
            continue
        detail = "; ".join(
            f"pp {spans(pages)} anche in {other} pp {spans(other_pages)}"
            for other, pages, other_pages in partners)
        note = detail + (" (letture parziali: --sample)" if sampled else "")
        result["checks"].append({"id": "uniqueness.page_overlap",
                                 "verdict": FAIL, "detail": note})
        if result["verdict"] != FAIL:
            result["verdict"] = FAIL

    return {"files": sorted(per_file), "shared_pages": shared_pages,
            "sampled": sampled}


# ==========================================================================
# volume vocabulary and index reconciliation
# ==========================================================================

def reconcile(files, per_authority, bookmarks):
    """The three index witnesses against the files on disk.

    index -> files is a failure: the reports name a table and there is no
    file for it, which is a table the pipeline lost.

    files -> index is only a warning. Five of the volumes carry no usable
    index at all, and the Dogane annexes of 2025 are announced in prose, so
    a file with no index entry is unusual rather than wrong.

    Order is checked as well, and it is the check that catches a scrambled
    split: an index that reads AA, AA1, AA2, BB while the bookmark tree puts
    AA2 before AA1 means the files are not in the order the report prints
    them, even though every code is present and every file is individually
    sound.
    """
    listed = set(per_authority) | set(bookmarks)
    have = {f["code"] for f in files}
    missing = sorted(listed - have)
    unattested = sorted(have - listed)

    # The index's own order, restricted to the codes a file exists for,
    # against the order the volume actually puts them in. A conflict here
    # means the files are not in the order the report prints them, which is
    # a scrambled split that every per-file check would pass.
    printed = [c for c in per_authority if c in have]
    placed = sorted((bookmarks[c], c) for c in printed if c in bookmarks)
    conflicts = [f"{ca} p{pa} / {cb} p{pb}"
                 for (pa, ca), (pb, cb) in zip(placed, placed[1:])
                 if ca != cb and pa > pb]

    return {
        "indexed_codes": len(listed),
        "files": len(have),
        "missing_files": missing,
        "unattested_files": unattested,
        "printed_order_checked": len(placed),
        "index_order_conflicts": conflicts,
        "index_vs_bookmark": sorted(set(per_authority) ^ set(bookmarks)),
    }


# ==========================================================================
# the run
# ==========================================================================

def split_stem(stem):
    """'AA2025' -> ('AA', '2025'). No archive code ends in four digits."""
    m = re.search(r"\d{4}$", stem)
    if m is None:
        return stem, None
    return stem[:m.start()], m.group(0)


def find_reports(reports_dir, years):
    """{year: [(path, label), ...]} for the requested years, in volume order."""
    from Reports2PDFTables import parse_volume

    root = Path(reports_dir)
    found = {}
    for year in years:
        folder = root / year
        if not folder.is_dir():
            continue
        entries = sorted(folder.glob("*.pdf"))
        found[year] = sorted(
            ((p, p.stem) for p in entries),
            key=lambda pair: (parse_volume(pair[0].stem) or 0, pair[0].name))
    return found


def load_manifest(path):
    """Step 1's JSONL manifest, keyed by report path, last write winning.

    A run appends, so a file re-verified after a re-split has two rows; the
    later one is the current answer.
    """
    if not path or not Path(path).exists():
        return {}, ""
    latest = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        report = row.get("report")
        if not report:
            continue
        latest[report] = row
        latest[Path(report).stem] = row
        latest[str(Path(report).name)] = row
    return latest, ""


def cache_key(path, args_fingerprint):
    try:
        st = os.stat(path)
    except OSError:
        return None
    raw = f"{path}|{st.st_size}|{int(st.st_mtime)}|{args_fingerprint}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class Cache:
    """Per-file results, keyed on the file and on what was asked of it.

    Verifying 2016-2025 is nineteen thousand pages of extraction. Re-running
    it to see whether a detector change helped is the whole point, so the
    answer for an unchanged file is remembered. The key includes the options
    that change the answer, or a --sample run would poison a full one.
    """

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def load(self, key):
        if not key:
            return None
        path = self.folder / f"{key}.json"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                self.hits += 1
                return json.load(fh)
        except Exception:
            return None

    def store(self, key, value):
        if not key:
            return
        try:
            with open(self.folder / f"{key}.json", "w", encoding="utf-8") as fh:
                json.dump(value, fh, ensure_ascii=False)
            self.misses += 1
        except Exception:
            pass


def split_out_path(root, path):
    """(authority, article, code, year) for one exported table.

    Two shapes are in play, and which one is in use is not something to
    assume. Step 1 wrote Out/PDF/<code>/<code><anno>.PDF. It now writes
    Out/PDF/<authority>/[<article>/]<code><anno>.PDF, where the article level
    is present only for tables filed under one. Both are read here rather than
    one being called correct, because the verifier's job is to audit whatever
    is on disk, and picking a shape in advance would make it report a layout
    error on every file the moment the writer changed.

    The code always comes from the filename stem; the leading directories are
    authority and article, in that order, and are recorded rather than
    checked.
    """
    rel = path.relative_to(root)
    code, year = split_stem(path.stem)
    parts = list(rel.parts[:-1])

    # The flat shape's one directory IS the code, which is what tells the two
    # apart without guessing from the folder name: a ministry like "D" and a
    # table code "D" would otherwise be indistinguishable, and the filename is
    # the only authority either shape agrees on.
    if len(parts) == 1 and parts[0] == code:
        return "", "", code, year
    if not parts:
        return "", "", code, year
    return parts[0], "/".join(parts[1:]), code, year


def collect_out_files(out_root):
    """[{code, year, path, authority, article}] for every per-table PDF.

    Recursive, because the ministry tree is three levels deep for article-
    filed tables and two for the rest, and a fixed-depth glob would silently
    skip whichever shape it was not written for.
    """
    root = Path(out_root, OUT_DIR, "PDF")
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.rglob(f"*{PDF_EXT}")):
        if not path.is_file():
            continue
        authority, article, code, year = split_out_path(root, path)
        found.append({"code": code, "year": year, "path": path,
                      "authority": authority, "article": article})
    return found


def find_stray(out_root):
    """Files in Out/PDF that are not a recognisable per-table PDF.

    Both shapes are accepted -- Out/PDF/<code>/<code><anno>.PDF and
    Out/PDF/<authority>/[<article>/]<code><anno>.PDF -- so what remains is a
    loose file, a wrong extension, or a name whose stem carries no year. Worth
    listing by name rather than counting, because "there is a stray file" is
    only actionable if it says which one.
    """
    root = Path(out_root, OUT_DIR, "PDF")
    if not root.is_dir():
        return []
    strays = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix != PDF_EXT:
            strays.append(str(path.relative_to(root)))
            continue
        authority, article, code, year = split_out_path(root, path)
        if not code or not year:
            strays.append(str(path.relative_to(root)))
    return strays


# ==========================================================================
# console report
# ==========================================================================

MARK = {PASS: "ok  ", FAIL: "FAIL", WARN: "warn", SKIP: "skip"}

# The footer of every report. Kept as data rather than prose in the printing
# code so that a check id appearing in a log can always be looked up here, and
# so that adding a check without documenting it is a visible omission.
CHECK_LEGEND = [
    ("layout.folder_name / file_stem / year_suffix / known_year / code_shape",
     "cartella, nome file, suffisso anno e forma del codice coerenti"),
    ("layout", "riepilogo: nessuna delle precedenti e' fallita"),
    ("uniqueness.stamp_total_varies",
     "il totale del timbro e' lo stesso su tutte le pagine"),
    ("uniqueness.foreign_table",
     "nessun codice diverso dal proprio ripetuto per >= MIN_RUN pagine"),
    ("uniqueness.own_code_run",
     "il codice proprio compare su almeno una pagina"),
    ("uniqueness.own_code_absent",
     "il codice proprio non compare: fallisce solo se le pagine sono leggibili"),
    ("uniqueness.code_run",
     "testo troppo distrutto per leggere un codice (SKIP)"),
    ("uniqueness.page_overlap",
     "una pagina di questo file e' identica a una pagina di un altro file"),
    ("completeness.stamp",
     "nessun 'Pagina N di X' disponibile: il volume non porta il timbro (SKIP)"),
    ("completeness.stamp_total",
     "le pagine del file sono tante quante il totale dichiarato dal timbro"),
    ("completeness.stamp_coverage",
     "ogni pagina porta il timbro: un buco indica pagine incollate da fuori"),
    ("completeness.first_page_is_one",
     "la prima pagina dichiara la pagina 1 del documento"),
    ("completeness.folio",
     "i numeri di pagina stampati corrono consecutivi"),
    ("edges.leading_junk",
     "prima della prima pagina di contenuto non c'e' nulla"),
    ("edges.trailing_junk",
     "dopo l'ultima pagina di contenuto non c'e' nulla"),
    ("edges.interior_noncontent",
     "nessun buco interno (un foglio di totali e' ammesso)"),
    ("edges.unreadable_pages",
     "quanta parte del file non e' giudicabile"),
    ("edges.no_content_page",
     "esiste almeno una pagina con contenuto di tabella"),
    ("identity.title",
     "il titolo che l'indice dà a quel codice compare in prima pagina"),
    ("read / read.page_error",
     "il file e tutte le sue pagine sono stati letti"),
]

LEGEND_GROUPS = [
    ("provenienza", "ogni pagina del volume appartiene a una tabella sola, o "
                    "a nessuna; una pagina attribuita due volte e' un errore"),
    ("indice", "ogni codice elencato ha un file, ogni file ha un codice "
               "elencato, e l'ordine stampato coincide con quello del volume"),
]


def check_legend_text():
    """The check reference, as the text that closes every log.

    A verdict id in a log is only actionable if the reader can find out what
    it asserted, and the log is the artefact that gets read weeks later from a
    terminal scrollback. So the legend travels with the report rather than
    living only in the source.
    """
    lines = ["-" * 78,
             "LEGENDA DEI CONTROLLI  (PASS ok / FAIL errore / "
             "WARN non verificabile / SKIP nessuna witness)",
             "-" * 78]
    for ident, meaning in CHECK_LEGEND:
        lines.append(f"  {ident:<62} {meaning}")
    lines.append("")
    for ident, meaning in LEGEND_GROUPS:
        lines.append(f"  {ident + ':':<62} {meaning}")
    lines.append("-" * 78)
    return "\n".join(lines) + "\n"


def print_check_legend():
    sys.stdout.write(check_legend_text())


# ==========================================================================
# the HTML report: one file a person can click through
# ==========================================================================
#
# The text log is the record and the JSON is the machine-readable form; neither
# is any good for the question a person actually has, which is "show me the file
# with the problem". This writes a third view of the SAME payload, in the same
# pass, so the three cannot describe different runs.
#
# Three constraints, all of them learned the hard way:
#
#   * Self-contained. No CDN, no webfont, no build step. It has to open from a
#     file:// URL on a machine with no network, which is where a person actually
#     is when reading an audit.
#   * Links are RELATIVE, via os.path.relpath from the report's own directory.
#     An absolute path breaks the moment the tree is moved or the report is
#     mailed; a relative one keeps working. It also means the report is written
#     where the log is, so it links to the PDFs through the same Out/ tree.
#   * Links, not an embedded <iframe>. Inlining 110 PDFs would make a
#     multi-hundred-megabyte file and browsers refuse local iframes anyway; a
#     plain href hands off to whatever PDF viewer the system has.
#
# Verdict is always written as a word as well as shown in colour, so the report
# stays readable for a colour-blind reader and in a monochrome print.

VERDICT_ORDER = {FAIL: 0, WARN: 1, SKIP: 2, PASS: 3}

# Colours are chosen for contrast in both light and dark rather than for
# saturation. --v-* are the per-verdict accents; the rest is a neutral ramp.
HTML_CSS = """
:root{
  --bg:#fff; --fg:#1a1a1a; --muted:#5a5f66; --line:#d8dce0; --head:#f2f4f6;
  --fail:#b3261e; --warn:#8a5a00; --skip:#4b5563; --pass:#3f6b45;
  --failbg:#fdecea; --warnbg:#fdf3e2; --skipbg:#f1f3f5; --passbg:#eef5ef;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#14171a; --fg:#e6e8ea; --muted:#9aa2ab; --line:#333a40; --head:#1d2126;
    --fail:#f2b8b5; --warn:#e8c07a; --skip:#aab3bd; --pass:#a8d5b0;
    --failbg:#2a1614; --warnbg:#2a2317; --skipbg:#1f2429; --passbg:#16211a;
  }
}
*{box-sizing:border-box}
body{margin:0;padding:1.2rem 1.4rem 4rem;background:var(--bg);color:var(--fg);
  font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
h1{font-size:1.25rem;margin:0 0 .2rem}
.sub{color:var(--muted);margin:0 0 1rem}
h2{font-size:1rem;margin:2rem 0 .5rem;padding-bottom:.3rem;
  border-bottom:1px solid var(--line)}
h3{font-size:.9rem;margin:1.2rem 0 .4rem;color:var(--muted);
  text-transform:uppercase;letter-spacing:.04em}
a{color:inherit}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--line);
  vertical-align:top}
th{background:var(--head);position:sticky;top:0;cursor:pointer;
  user-select:none;white-space:nowrap}
th:hover{outline:1px solid var(--muted)}
th .arrow{color:var(--muted);font-size:.7em;margin-left:.2rem}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.row{cursor:pointer}
tr.row:hover{background:var(--head)}
tr.v-FAIL td.vcell{color:var(--fail);font-weight:600}
tr.v-WARN td.vcell{color:var(--warn);font-weight:600}
tr.v-SKIP td.vcell{color:var(--skip)}
tr.v-PASS td.vcell{color:var(--pass)}
tr.v-PASS td.code,tr.v-PASS td.fname{opacity:.62}
tr.hidden{display:none}
/* An expander row starts hidden and is revealed by its parent being visible. */
tr.checks td{padding-top:0;border-bottom:1px solid var(--line)}
details>summary{cursor:pointer;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"\\25B8";display:inline-block;width:1rem;
  color:var(--muted)}
details[open]>summary::before{content:"\\25BE"}
.dot{display:inline-block;width:.62rem;height:.62rem;border-radius:50%;
  vertical-align:middle;margin-right:.4rem}
.checks{background:var(--head);border-radius:4px;padding:.5rem .7rem;
  margin:.2rem 0 .6rem}
.chk{display:flex;gap:.5rem;padding:.1rem 0;font-size:12.5px}
.chk .id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:nowrap;color:var(--muted)}
.chk .vd{flex:0 0 3.4rem;font-weight:600}
.chk .vd.PASS{color:var(--pass)} .chk .vd.WARN{color:var(--warn)}
.chk .vd.FAIL{color:var(--fail)} .chk .vd.SKIP{color:var(--skip)}
.chips{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;
  margin:.6rem 0}
.chip{border:1px solid var(--line);background:var(--bg);color:var(--fg);
  border-radius:999px;padding:.2rem .7rem;font:inherit;font-size:12.5px;
  cursor:pointer}
.chip[aria-pressed="true"]{background:var(--head);font-weight:600}
.chip .n{color:var(--muted);margin-left:.3rem}
.chip.v-FAIL[aria-pressed="true"]{background:var(--failbg);color:var(--fail)}
.chip.v-WARN[aria-pressed="true"]{background:var(--warnbg);color:var(--warn)}
.chip.v-PASS[aria-pressed="true"]{background:var(--passbg);color:var(--pass)}
#q{border:1px solid var(--line);background:var(--bg);color:var(--fg);
  border-radius:4px;padding:.35rem .6rem;font:inherit;min-width:19rem}
.meta{color:var(--muted);font-size:12.5px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.legend{font-size:12.5px}
.legend td:first-child{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:nowrap;color:var(--muted)}
ul.codes{margin:.3rem 0;padding-left:1.2rem}
ul.codes li{display:inline-block;margin:.1rem .4rem .1rem 0}
.pill{display:inline-block;border-radius:3px;padding:0 .4rem;font-size:12px}
.note{border-left:3px solid var(--line);padding:.3rem .7rem;margin:.6rem 0;
  color:var(--muted);font-size:12.5px}
"""

HTML_JS = """
const rows=[...document.querySelectorAll('tr.row')];
const q=document.getElementById('q');
const chips=[...document.querySelectorAll('.chip[data-v]')];
const VERD={FAIL:0,WARN:1,SKIP:2,PASS:3};
let on=new Set(chips.map(c=>c.dataset.v));
function apply(){
  const s=q.value.trim().toLowerCase();
  let n=0;
  for(const r of rows){
    const show=on.has(r.dataset.v)&&(!s||r.dataset.s.includes(s));
    r.classList.toggle('hidden',!show);
    // The expander row is a SIBLING, so it has no verdict of its own and is
    // not in `rows`. Hiding the parent alone would leave it stranded on screen
    // below a filtered-out row, which looks like a rendering bug.
    const d=r.nextElementSibling;
    if(d&&d.classList.contains('checks'))d.classList.toggle('hidden',!show);
    if(show)n++;
  }
  document.getElementById('shown').textContent=n;
}
q.addEventListener('input',apply);
for(const c of chips){
  c.addEventListener('click',()=>{
    if(on.has(c.dataset.v))on.delete(c.dataset.v);else on.add(c.dataset.v);
    c.setAttribute('aria-pressed',on.has(c.dataset.v));
    apply();
  });
}
for(const r of rows){
  r.addEventListener('click',ev=>{
    if(ev.target.tagName==='A')return;
    const d=r.nextElementSibling;
    if(d&&d.classList.contains('checks')){
      d.classList.remove('hidden');
      d.querySelector('details').open=!d.querySelector('details').open;
    }
  });
}
let dir=1;
const KEY={vcell:'v',code:'code',pages:'pages',auth:'auth',own:'own'};
for(const th of document.querySelectorAll('th[data-k]')){
  th.addEventListener('click',()=>{
    const k=KEY[th.dataset.k]||th.dataset.k;
    const num=(k==='pages'||k==='own');
    dir=-dir;
    // Sort each expander row WITH its parent, or a re-sort tears the table
    // apart: the panel would stay behind while its file moved.
    const pairs=rows.map(r=>[r,r.nextElementSibling]);
    pairs.sort((a,b)=>{
      const ra=a[0],rb=b[0];
      let x=ra.dataset[k],y=rb.dataset[k];
      if(num){x=parseFloat(x)||0;y=parseFloat(y)||0;return (x-y)*dir;}
      if(k==='v')return (VERD[ra.dataset.v]-VERD[rb.dataset.v])*dir;
      return x.localeCompare(y)*dir;
    });
    const tb=th.closest('tbody');
    for(const [r,d] of pairs){tb.appendChild(r);if(d)tb.appendChild(d);}
    for(const o of document.querySelectorAll('th .arrow'))o.remove();
    th.insertAdjacentHTML('beforeend','<span class="arrow">\\u25BC</span>');
  });
}
apply();
"""


def _esc(text):
    """HTML-escape, quotes included, for attribute and text positions."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def write_html_report(payload, path):
    """Write the clickable report for one year. Returns the path, or None.

    Reads only `payload`, so it cannot disagree with the log or the JSON: they
    are all rendered from the same dict in the same pass.

    `path` is where the report lands; the PDF links are computed relative to
    *its* directory, which is why it is written next to the log rather than
    somewhere convenient.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_report(payload, path), encoding="utf-8")
    except Exception as exc:
        # A report that cannot be written must not take the run down: the log
        # and the JSON are already on disk and they are the record. Same
        # reasoning as "write the artefacts before the summary" in step 1.
        print(f"  ⚠️  report HTML non scritto: {type(exc).__name__}: {exc}")
        return None
    return path


def html_report(payload, path):
    """The whole report as one self-contained HTML document.

    Split out from write_html_report() so the rendering is a pure function of
    its inputs: same payload and same destination, same bytes, no I/O, nothing
    to go stale.

    `path` is needed, not just the payload: the PDF links are relative to where
    the report itself sits, which is not out_root. The report lands in
    Out/VERIFY/ and the tables in Out/PDF/, so the honest link is
    `../PDF/MEF/UE2025.PDF`. Computed against out_root instead it came out as
    `Out/PDF/...` and every one of the 110 links was dead -- which is the worst
    failure this report can have, because the whole point of it is that a click
    opens a file.
    """
    year = payload.get("year", "?")
    summary = payload.get("summary") or {}
    verdicts = summary.get("verdicts") or {}
    files = payload.get("files") or []
    here = Path(path).resolve().parent

    # FAIL first, then WARN: the sort order is the report's argument about what
    # matters, so it is the default and not a preference.
    ordered = sorted(files, key=lambda f: (VERDICT_ORDER.get(f.get("verdict"), 9),
                                           str(f.get("code"))))

    out = [
        "<!DOCTYPE html>",
        '<html lang="it"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>VerifyTables — {year}</title>",
        f"<style>{HTML_CSS}</style></head><body>",
        f"<h1>VerifyTables — {year}</h1>",
        f'<p class="sub">{summary.get("files", len(files))} file, '
        f'{summary.get("pages", 0)} pagine &middot; generato da '
        f'{_esc(payload.get("verifier", ""))}</p>',
    ]

    counts = " ".join(
        f'<span class="pill v-{v}" style="background:var(--{v.lower()}bg);'
        f'color:var(--{v.lower()})">{v} {verdicts.get(v, 0)}</span>'
        for v in (FAIL, WARN, SKIP, PASS))
    out.append(f'<p class="meta">{counts}</p>')

    # ---- filters -------------------------------------------------------
    out.append('<div class="chips">')
    for v in (FAIL, WARN, SKIP, PASS):
        out.append(
            f'<button class="chip v-{v}" data-v="{v}" aria-pressed="true">'
            f'{v}<span class="n">{verdicts.get(v, 0)}</span></button>')
    out.append('<input id="q" type="search" '
               'placeholder="filtra per codice, percorso o controllo '
               '(es. MG, foreign_table)">'
               '<span class="meta">&nbsp;<span id="shown">0</span> '
               'righe visibili</span></div>')

    # ---- the table -----------------------------------------------------
    out.append("<table><thead><tr>"
               '<th data-k="vcell" class="num">verdetto</th>'
               '<th data-k="code">codice</th>'
               '<th data-k="pages" class="num">pagine</th>'
               '<th data-k="auth">ministero</th>'
               '<th data-k="own" class="num">codice proprio</th>'
               "<th>file</th><th class=\"num\">controlli</th>"
               "</tr></thead><tbody>")

    for f in ordered:
        verdict = f.get("verdict") or PASS
        code = f.get("code") or "?"
        pages = f.get("pages") or 0
        own = f.get("own_code_pages")
        own_txt = f"{own}/{pages}" if isinstance(own, int) and pages else "—"
        path = f.get("path") or ""
        # Relative, so the report survives being moved or mailed. An absolute
        # path would break the moment the tree was copied anywhere else.
        try:
            rel = os.path.relpath(Path(path).resolve(), here) if path else ""
        except (ValueError, OSError):
            rel = path
        where = "/".join(p for p in (f.get("authority"), f.get("article"))
                         if p) or "—"

        checks = f.get("checks") or []
        flagged = [c for c in checks if c.get("verdict") in (FAIL, WARN, SKIP)]

        # The search haystack: what a person would type to find this row.
        hay = " ".join([str(code), rel, str(f.get("authority") or ""),
                        str(f.get("article") or "")]
                       + [str(c.get("id")) for c in checks]).lower()

        out.append(
            f'<tr class="row v-{verdict}" data-v="{verdict}" '
            f'data-code="{_esc(code)}" data-pages="{pages}" '
            f'data-auth="{_esc(f.get("authority") or "")}" '
            f'data-own="{own if isinstance(own, int) else -1}" '
            f'data-s="{_esc(hay)}">'
            f'<td class="vcell"><span class="dot" style="background:'
            f'var(--{verdict.lower()})"></span>{verdict}</td>'
            f'<td class="code"><code>{_esc(code)}</code></td>'
            f'<td class="num">{pages}</td>'
            f'<td>{_esc(where)}</td>'
            f'<td class="num">{own_txt}</td>'
            f'<td class="fname">'
            + (f'<a href="{_esc(rel)}">{_esc(rel)}</a>' if rel else "—")
            + "</td>"
            f'<td class="num">{len(flagged) or ""}</td></tr>')

        # One expander row per file, carrying every check -- including the PASS
        # ones, so a reader can see what was actually asserted and not only what
        # went wrong.
        if checks:
            rows_html = []
            for c in checks:
                v = c.get("verdict") or PASS
                rows_html.append(
                    f'<div class="chk"><span class="vd {v}">{v}</span>'
                    f'<span class="id">{_esc(c.get("id"))}</span>'
                    f'<span>{_esc(c.get("detail"))}</span></div>')
            extra = []
            # Rendered as sentences, not as reprs of dicts. "leggibilità:
            # {'pages': 3, 'garbled': 0}" is a debugging artefact, not a report,
            # and the stamp is the most-read field in the panel.
            stamp = f.get("stamp") or {}
            if stamp.get("pages_with_stamp"):
                extra.append(
                    f"timbro su {stamp['pages_with_stamp']} pagine, "
                    f"totale {'leggibile' if stamp.get('readable') else 'non leggibile'}"
                    f"{', concordante' if stamp.get('agrees') else ''}")
            if f.get("attested"):
                extra.append("codice attestato da un indice o da un segnalibro")
            leg = f.get("legibility") or {}
            if leg:
                bits = [f"{leg.get('pages', 0)} pagine"]
                if leg.get("garbled"):
                    bits.append(f"{leg['garbled']} illeggibili (no ToUnicode)")
                if leg.get("ciphered"):
                    bits.append(f"{leg['ciphered']} cifrate (Identity-H)")
                extra.append("leggibilità: " + ", ".join(bits))
            if f.get("title_expected"):
                extra.append(f"titolo atteso: {_esc(f['title_expected'])}")
            tail = (f'<div class="meta">{" &middot; ".join(extra)}</div>'
                    if extra else "")
            out.append(f'<tr class="checks hidden"><td colspan="7">'
                       f'<details><summary>{_esc(code)} — '
                       f'{len(checks)} controlli</summary>'
                       f'<div class="checks">{"".join(rows_html)}</div>'
                       f"{tail}</details></td></tr>")

    out.append("</tbody></table>")

    # ---- what the numbers do not say ------------------------------------
    prov = summary.get("provenance") or {}
    if prov:
        out.append("<h2>provenienza</h2>")
        out.append(
            f'<p class="meta">{prov.get("volumes", 0)} volumi, '
            f'{prov.get("pages", 0)} pagine, '
            f'{prov.get("claimed_pages", 0)} attribuite a una tabella, '
            f'{len(prov.get("unattributed_pages") or [])} non attribuite, '
            f'{len(prov.get("duplicated_pages") or [])} duplicate.</p>')
        note = summary.get("provenance_note")
        if note:
            out.append(f'<div class="note">{_esc(note)}</div>')
        classes = prov.get("non_table_classes") or {}
        if classes:
            out.append('<h3>pagine non tabellari</h3><ul class="codes">')
            out.extend(f"<li>{_esc(k)}: <b>{v}</b></li>"
                       for k, v in sorted(classes.items()))
            out.append("</ul>")

    index = summary.get("index") or {}
    if index:
        out.append("<h2>indice</h2>")
        out.append(
            f'<p class="meta">{index.get("indexed_codes", 0)} codici elencati '
            f'su {index.get("files", 0)} file.</p>')
        if index.get("missing_files"):
            out.append("<h3>elencati e senza file</h3><ul class=\"codes\">")
            out.extend(f"<li><code>{_esc(c)}</code></li>"
                       for c in index["missing_files"][:80])
            out.append("</ul>")
        # Atteso, non un difetto: i Dogane e i Difesa non hanno indice. Serve
        # elencarlo perche' il numero e' impressionante, e chiamarlo problema
        # sarebbe falso. Vedi AGENTS.md sezione 6.
        if index.get("unattested_files"):
            codes = index["unattested_files"]
            out.append(
                f'<div class="note">{len(codes)} file senza voce in un indice '
                f'&mdash; <b>atteso</b>: i suoi codici sono in nessun indice e '
                f'nessun segnalibro, perch&eacute; le loro allegati sono '
                f'annunciati in prosa (gli MG/MT dei Dogane) e il DIFESA non ha '
                f'una riga "Tabelle" nell\'INDICE. Non &egrave; un controllo in '
                f'fallimento per questi.</div>')
            out.append(f'<p class="meta">{" ".join(_esc(c) for c in codes)}</p>')

    stray = summary.get("stray_files") or []
    if stray:
        out.append("<h2>file estranei</h2><ul class=\"codes\">")
        out.extend(f"<li><code>{_esc(s)}</code></li>" for s in stray[:80])
        out.append("</ul>")

    # ---- legend ---------------------------------------------------------
    out.append("<h2>legenda dei controlli</h2>")
    out.append('<table class="legend"><tbody>')
    for ident, meaning in CHECK_LEGEND:
        out.append(f"<tr><td>{_esc(ident)}</td><td>{_esc(meaning)}</td></tr>")
    for ident, meaning in LEGEND_GROUPS:
        out.append(f"<tr><td>{_esc(ident)}:</td><td>{_esc(meaning)}</td></tr>")
    out.append("</tbody></table>")

    out.append(f"<script>{HTML_JS}</script></body></html>\n")
    return "\n".join(out)


def locate_span(volumes, code):
    """Where step 1 says a table lives, as (label, start, end, pages, source).

    Read from the manifest rather than recomputed, so the table states what
    the pipeline *believed* and the detail below states what the file turned
    out to be. When the two disagree, that disagreement is the finding.
    """
    for volume in volumes:
        manifest = volume.get("manifest") or {}
        for info in manifest_tables(manifest):
            if info.get("code") != code:
                continue
            return (Path(volume["report"]).stem, info["start"], info["end"],
                    info.get("pages"), info.get("source"))
        for cont in manifest_continued(manifest):
            if cont.get("code") != code:
                continue
            return (Path(volume["report"]).stem, cont["start"], cont["end"],
                    cont.get("total_pages"), "continued")
    return None


def manifest_tables(manifest):
    """Step 1's table records, whichever shape the manifest was written in.

    Two shapes exist and the verifier has to read both. Step 1 wrote `tables` as
    a mapping of code to record until the ministry tree landed, and writes a
    LIST of records since -- json has no tuple keys, so the (authority, article,
    code) triple became three fields on each record. Calling .get() on the list
    raises AttributeError and set() on it raises TypeError, so a verifier written
    against the mapping silently loses every attestation the moment step 1
    changes: no manifest, no "this code is named by the reports", and a file that
    is perfectly real is reported FAIL for it.
    """
    tables = (manifest or {}).get("tables")
    if isinstance(tables, dict):
        return [{**info, "code": code} if isinstance(info, dict) else info
                for code, info in tables.items()]
    if isinstance(tables, list):
        return [t for t in tables if isinstance(t, dict)]
    return []


def manifest_continued(manifest):
    """Step 1's continuation records, whichever shape the manifest used.

    The sibling of manifest_tables(), for the same reason. `continued` was a
    mapping keyed by the (authority, article, code) triple until the ministry
    tree, and is a LIST of records since -- json has no tuple keys. Iterating a
    mapping yields its KEYS, so `for cont in manifest["continued"]` silently
    iterates tuples and `cont.get(...)` raises AttributeError, which is the
    identical disarm the tables shape change caused.

    One difference from manifest_tables(), and it matters: the old `tables` key
    was a bare code string, so the key could be copied into "code" as it stands.
    The old `continued` key is the whole triple, so copying it verbatim would
    leave every record keyed on a tuple and matching nothing. Split it.
    """
    cont = (manifest or {}).get("continued")
    if isinstance(cont, dict):
        out = []
        for key, info in cont.items():
            if not isinstance(info, dict):
                out.append(info)
                continue
            if isinstance(key, tuple):
                authority, article, code = (list(key) + [None] * 3)[:3]
                info = {"authority": authority, "article": article,
                        "code": code, **info}
            else:
                info = {"code": key, **info}
            out.append(info)
        return out
    if isinstance(cont, list):
        return [c for c in cont if isinstance(c, dict)]
    return []


def build_findings(results, volumes):
    """One row per failing file: what it is, where it came from, why it failed.

    Built before the detail so the log opens with the short answer. A reader
    who wants to know what to open finds it on line one; a reader who wants
    the evidence keeps reading.
    """
    rows = []
    for result in results:
        if result["verdict"] != FAIL:
            continue
        span = locate_span(volumes, result["code"])
        expected = span[3] if span else None
        reason = next((c["detail"] for c in result["checks"]
                       if c["verdict"] == FAIL), "")
        rows.append({
            "code": result["code"],
            "file": result["path"],
            "pages": result["pages"],
            "expected_pages": expected,
            "source": (f"{span[0]} p{span[1]}-{span[2]}"
                       if span else "sconosciuta"),
            "source_kind": span[4] if span else None,
            "reason": brief(reason, 120),
            "checks": [c["id"] for c in result["checks"]
                       if c["verdict"] == FAIL],
        })
    return rows


def print_findings_table(findings, out_root):
    """The short answer, at the top of the log."""
    if not findings:
        print("  nessuna tabella da controllare: nessun file in FAIL")
        print()
        return
    print(f"  DA CONTROLLARE — {len(findings)} file in FAIL")
    print(f"  {'code':<6}{'file':<26}{'sorgente':<34}{'pp':>11}  controlli")
    for row in findings:
        try:
            shown = str(Path(row["file"]).relative_to(out_root))
        except ValueError:
            shown = row["file"]
        if row["expected_pages"]:
            extent = f"{row['pages']}/{row['expected_pages']}"
        else:
            extent = str(row["pages"])
        flag = "  <-- " if (row["expected_pages"]
                            and row["pages"] != row["expected_pages"]) else "      "
        print(f"  {row['code']:<6}{shown:<26}{row['source']:<34}{extent:>11}"
              f"{flag}{', '.join(row['checks'])}")
        print(f"         {row['reason']}")
    print()


def report_year(year, payload):
    summary = payload["summary"]
    print("=" * 78)
    print(f"{year} — {summary['files']} file, {summary['pages']} pagine, "
          f"{MARK[PASS]}{summary['verdicts'][PASS]} "
          f"{MARK[FAIL]}{summary['verdicts'][FAIL]} "
          f"{MARK[WARN]}{summary['verdicts'][WARN]} "
          f"{MARK[SKIP]}{summary['verdicts'][SKIP]}")
    print("=" * 78)

    print_findings_table(payload["summary"].get("findings", []),
                         Path(payload["out_root"]))

    for volume in payload["volumes"]:
        print(f"  {volume['label']}  {volume['pages']}pp  "
              f"family={volume.get('family', '?')}  "
              f"timbro {volume.get('stamped_pages', 0)}pp  "
              f"indice: {len(volume.get('elenco', {}))} codici")
        general = volume.get("general_indice") or []
        authorities = [e for e in general if e["kind"] == "authority"]
        tables = [e for e in general if e["kind"] == "tables"]
        if authorities or tables:
            for entry in authorities:
                print(f"      {entry['authority'][:52]:52} p{entry['page']}")
            for entry in tables:
                print(f"        -> tabelle{'':<42} p{entry['page']}")

    strays = payload["summary"].get("stray_files")
    if strays:
        print(f"  file fuori layout in Out/PDF: {len(strays)}")
        for rel in strays[:10]:
            print(f"      {rel}")

    bad = [f for f in payload["files"] if f["verdict"] in (FAIL, WARN)]
    if not bad:
        print("  nessun problema")
    for entry in bad:
        print(f"  {MARK[entry['verdict']]} {entry['code']:<5} "
              f"{entry['pages']:>4}pp  "
              f"{Path(entry['path']).parent.name}/")
        for check in entry["checks"]:
            if check["verdict"] in (FAIL, WARN):
                print(f"         {check['verdict']:<4} {check['id']:<38} "
                      f"{brief(check['detail'])}")

    prov = payload["summary"].get("provenance")
    if prov:
        print(f"  provenienza: {prov['claimed_pages']}/{prov['pages']} pagine "
              f"attribuite, {len(prov['duplicated_pages'])} duplicate, "
              f"{len(prov['unattributed_pages'])} non attribuite")
        if prov.get("continued_pages"):
            print(f"      di cui {prov['continued_pages']} pagine di tabelle "
                  f"proseguite dal volume precedente (stitch)")
        for dup in prov["duplicated_pages"][:8]:
            print(f"      doppia p{dup['page']} {dup['report']}: "
                  f"{', '.join(dup['claimed_by'])}")
        for miss in prov["unattributed_pages"][:8]:
            edge = " (inizio tabella)" if miss["at_table_start"] else \
                   " (fine tabella)" if miss["at_table_end"] else ""
            print(f"      non attribuita {miss['report']} p{miss['page']}{edge}")
        if len(prov["unattributed_pages"]) > 8:
            print(f"      ... altre "
                  f"{len(prov['unattributed_pages']) - 8} pagine")
    elif payload["summary"].get("provenance_note"):
        print(f"  provenienza: {payload['summary']['provenance_note']}")

    overlap = payload["summary"].get("page_overlap") or {}
    if overlap.get("files"):
        print(f"  pagine condivise fra file: {len(overlap['files'])} file "
              f"({overlap['shared_pages']} pagine identiche"
              f"{', letture parziali' if overlap.get('sampled') else ''})")
        for code in overlap["files"][:8]:
            print(f"      {code}")

    index = payload["summary"].get("index")
    if index:
        print(f"  indice: {index['indexed_codes']} codici elencati, "
              f"{index['files']} file")
        if index["missing_files"]:
            print(f"      elencati e senza file: "
                  f"{' '.join(index['missing_files'])}")
        if index["unattested_files"]:
            print(f"      file senza voce in indice: "
                  f"{' '.join(index['unattested_files'])}")
        if index["index_order_conflicts"]:
            print(f"      ordine divergente ({len(index['index_order_conflicts'])}): "
                  f"{'; '.join(index['index_order_conflicts'][:4])}")
        elif index["printed_order_checked"]:
            print(f"      ordine: {index['printed_order_checked']} codici "
                  f"nell'ordine stampato")
        if index["index_vs_bookmark"]:
            print(f"      indice vs segnalibri: "
                  f"{' '.join(index['index_vs_bookmark'])}")
    print()


# ==========================================================================
# CLI
# ==========================================================================

class Tee(io.TextIOBase):
    """Write to the terminal and to a buffer at the same time.

    The report is assembled from many print() calls spread across the run, and
    rewriting them all to take a writer would obscure the code for no gain.
    Redirecting stdout into this for the length of a year's report captures
    exactly what the reader saw, which is the point: the saved log and the
    terminal output must not be able to drift apart.
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verifica i PDF per-tabella prodotti da Reports2PDFTables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base", default="", help="radice dei percorsi")
    parser.add_argument("--out-root", default="",
                        help=f"radice di Out/ (default: <base>/{OUT_DIR})")
    parser.add_argument("--reports-dir", default="",
                        help=f"cartella dei rapporti (default: <base>/"
                             f"{REPORTS_DIR})")
    parser.add_argument("--year", action="append", default=[],
                        help="anno da verificare, ripetibile")
    parser.add_argument("--from-year", default="", help="primo anno")
    parser.add_argument("--to-year", default="", help="ultimo anno")
    parser.add_argument("--all", action="store_true",
                        help="verifica ogni anno presente")
    parser.add_argument("--manifest", default="",
                        help="manifest JSON di step 1, per la provenienza")
    parser.add_argument("--no-manifest-scan", action="store_true",
                        help="non leggere i rapporti per l'indice")
    parser.add_argument("--json", default="",
                        help="percorso del JSON (default: "
                             "<out>/VERIFY/verify-<anno>.json)")
    parser.add_argument("--log", default="",
                        help="percorso del log (default: "
                             "<out>/VERIFY/verify-<anno>.log)")
    parser.add_argument("--html", default="",
                        help="percorso del report HTML cliccabile (default: "
                             "<out>/VERIFY/verify-<anno>.html)")
    parser.add_argument("--no-html", action="store_true",
                        help="non scrivere il report HTML")
    parser.add_argument("--sample", type=int, default=0,
                        help="leggi solo N pagine per file (rapido, meno "
                             "completo)")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignora i risultati memorizzati")
    parser.add_argument("--no-fingerprint", action="store_true",
                        help="non calcolare l'hash del contenuto delle pagine")
    parser.add_argument("--dry-run", action="store_true",
                        help="stampa i verdetti, non scrivere il JSON")
    args = parser.parse_args(argv)

    base = Path(args.base) if args.base else Path(".")
    out_root = Path(args.out_root) if args.out_root else base
    reports_dir = Path(args.reports_dir) if args.reports_dir \
        else base / REPORTS_DIR

    if args.all:
        years = sorted(d.name for d in Path(reports_dir).glob("*")
                       if d.is_dir() and d.name.isdigit())
    elif args.year or args.from_year or args.to_year:
        if args.year:
            years = list(dict.fromkeys(args.year))
        else:
            first = int(args.from_year or 2001)
            last = int(args.to_year or 2099)
            years = [str(y) for y in range(first, last + 1)]
    else:
        years = []

    present = sorted(d.name for d in Path(reports_dir).glob("*")
                     if d.is_dir() and d.name.isdigit())
    years = [y for y in years if y in present] or years
    if not years:
        print(f"Nessun anno in {reports_dir}/. Usa --year, --from-year/--to-year "
              f"o --all.")
        return 2

    tables = collect_out_files(out_root)
    if not tables:
        print(f"Nessun PDF per-tabella in {Path(out_root, OUT_DIR, 'PDF')}/. "
              f"Esegui prima Reports2PDFTables.")
        return 2

    # The years manifest.tsv knows about, so a file whose year suffix is not a
    # reporting year at all is caught as a naming error rather than quietly
    # verified. Falls back to the directories on disk when the manifest is
    # missing or malformed.
    known_years = {row[0] for row in read_manifest_years(reports_dir)}
    if not known_years:
        known_years = set(present)

    manifests, manifest_note = {}, ""
    if args.manifest:
        manifests, manifest_note = load_manifest(args.manifest)
        if not manifests:
            manifest_note = (f"manifest '{args.manifest}' non trovato o vuoto: "
                             f"la provenienza non sara' verificata")

    cache = Cache(Path(out_root, OUT_DIR, VERIFY_SUBDIR, ".cache"))
    fingerprint_tag = f"s{args.sample}f{0 if args.no_fingerprint else 1}"

    # One log per year, beside its JSON: Out/VERIFY/verify-<anno>.log. Same
    # gate as the JSON -- --dry-run writes nothing at all, so a read-only run
    # leaves no trace to be mistaken for a result.
    verify_root = Path(out_root, OUT_DIR, VERIFY_SUBDIR)
    logs_written = []

    print("=" * 78)
    print(f"VerifyTables — {len(years)} anno/i, {len(tables)} file in "
          f"{Path(out_root, OUT_DIR, 'PDF')}")
    print(f"  rapporti: {reports_dir}")
    print("  verdetto: PASS/FAIL/WARN/SKIP — WARN non fallisce un run")
    print("=" * 78)

    reports = find_reports(reports_dir, years) if not args.no_manifest_scan \
        else {}
    totals = Counter()
    payloads = []

    for year in years:
        volumes = []
        per_authority = {}
        bookmark_codes = {}
        manifest_codes = set()

        for pdf, label in reports.get(year, []):
            texts, engine = page_texts(pdf)
            manifest = manifests.get(str(pdf)) or manifests.get(label)
            reader = None
            try:
                reader = PdfReader(str(pdf), strict=False)
                pages = len(reader.pages)
            except Exception as exc:
                print(f"  {label}: apertura fallita: {exc}")
                continue

            general = harvest_general_indice(texts)
            elenco = harvest_elenco(texts)
            counts = harvest_index_rows(texts)
            for code, info in counts.items():
                entry = elenco.setdefault(code, {"code": code, "title": ""})
                entry.setdefault("pages", info["pages"])
                entry["index_pages"] = info["pages"]
                entry["index_at_page"] = info["at_page"]
                if not entry.get("title"):
                    entry["title"] = info["title"]
            marks = scan_bookmarks(reader)
            family = detect_family(reader, set(elenco) | set(marks)) \
                if (elenco or marks) else None

            # A volume's own code census, from the whole volume. Frequency
            # decides: a code repeated on MIN_RUN pages is a table, one seen
            # once is a mention. This is what keeps the 2025 Dogane annexes,
            # which no index lists, from being invisible.
            hits = Counter()
            for page in sorted(texts):
                lines = [ln.strip() for ln in texts[page].split("\n")
                         if ln.strip()]
                # The appendix first, so its code is counted as itself rather
                # than as the base code every one of its pages also carries.
                appendix = appendix_code(lines)
                if appendix:
                    hits[appendix] += 1
                    continue
                for line in lines:
                    for m in VERIFIER_INLINE.finditer(line):
                        cand = m.group(1).upper()
                        if VERIFIER_CODE.match(cand):
                            hits[cand] += 1
                    m = VERIFIER_LEADING.match(line.strip())
                    if m:
                        hits[m.group(1).upper()] += 1

            # manifest_tables() rather than the raw field: step 1 writes a list
            # of records since the ministry tree, and set() on a list of dicts
            # raises TypeError -- which is what kept every manifest code out of
            # the vocabulary, and every attested table from being attested.
            m_tables = manifest_tables(manifest)
            manifest_codes |= {t["code"] for t in m_tables if t.get("code")}
            vocab = volume_vocabulary(hits, set(elenco), set(marks),
                                      manifest_codes)

            volumes.append({
                "label": label,
                "report": str(pdf),
                "pages": pages,
                "family": family,
                "extractor": engine,
                "general_indice": general,
                "elenco": elenco,
                "bookmarks": marks,
                "vocabulary": sorted(vocab),
                "code_hits": {c: n for c, n in hits.most_common(40)},
                "manifest": manifest,
                "index_pages": counts,
            })
            per_authority.update(elenco)
            bookmark_codes.update(marks)
            if reader is not None:
                reader.close()

        year_files = [t for t in tables if t["year"] == year]
        results = []
        for item in year_files:
            key = None if args.no_cache else cache_key(
                item["path"], f"{fingerprint_tag}|{','.join(sorted(vocab_of(volumes, item['code'])))}")
            cached = cache.load(key)
            if cached:
                cached["from_cache"] = True
                results.append(cached)
                continue
            entry = per_authority.get(item["code"])
            # Attested = somebody other than this file says the table exists: an
            # index of tables, a human-authored bookmark, or step 1's own
            # manifest. It is what lets a table whose code is printed only on
            # its first page pass the ratio check; see check_uniqueness for why
            # the ratio alone cannot decide it.
            #
            # The manifest counts, and has to. The Difesa annessi are in no
            # index and in no bookmark tree -- the INDICE gives DIFESA no
            # "Tabelle" line at all -- so without the manifest as a witness
            # Annesso 3B, which prints its header on 1 page of 8, is reported as
            # not being a table. Step 1 reading the same header on the same page
            # is weaker evidence than a printed index, but it is not nothing, and
            # the alternative is failing files that are correct.
            attested = (bool(entry) or item["code"] in bookmark_codes
                       or item["code"] in manifest_codes)
            result = verify_file(
                item["path"], item["code"], item["year"],
                vocab_of(volumes, item["code"]), entry, known_years,
                fingerprint=not args.no_fingerprint, sample=args.sample,
                attested=attested, authority=item.get("authority", ""),
                article=item.get("article", ""))
            cache.store(key, result)
            results.append(result)

        provenance, prov_note = check_provenance(volumes, results)
        overlap = check_page_overlap(results, sampled=bool(args.sample))
        index = reconcile(results, per_authority, bookmark_codes)
        findings = build_findings(results, volumes)

        verdicts = Counter(f["verdict"] for f in results)
        failed = sorted(f["code"] for f in results if f["verdict"] == FAIL)
        payload = {
            "year": year,
            "out_root": str(out_root),
            "reports_dir": str(reports_dir),
            "verifier": "VerifyTables.py",
            "summary": {
                "files": len(results),
                "pages": sum(f["pages"] for f in results),
                "verdicts": {PASS: verdicts[PASS], FAIL: verdicts[FAIL],
                             WARN: verdicts[WARN], SKIP: verdicts[SKIP]},
                "failed_codes": failed,
                "findings": findings,
                "stray_files": find_stray(out_root),
                "provenance": provenance,
                "provenance_note": prov_note or manifest_note,
                "page_overlap": overlap,
                "index": index,
            },
            "volumes": [{k: v for k, v in vol.items() if k != "manifest"}
                        for vol in volumes],
            "files": results,
        }
        payloads.append(payload)
        for v in verdicts.elements():
            totals[v] += 1

        # Everything this year prints is captured as it is printed, so the
        # saved log is the terminal output rather than a second rendering of
        # it that could drift.
        buffer = io.StringIO()

        # The log is written FIRST, from what has been captured so far, and
        # rewritten at the end with the summary appended. Order matters and it
        # used to be the wrong way round: the JSON was written first and the log
        # last, so a run that died between them -- piped into `head`, which
        # closes the pipe and raises BrokenPipeError on the next print --
        # left a JSON with no log beside it. The log is the artefact a person
        # actually reads, so it is the one that must not be the casualty.
        log_path = Path(args.log) if args.log else Path(
            verify_root, f"verify-{year}.log")
        html_path = (Path(args.html) if args.html else
                     Path(verify_root, f"verify-{year}.html"))
        write_html = not args.no_html

        def flush_log(extra=""):
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(buffer.getvalue() + extra
                                + check_legend_text(), encoding="utf-8")

        if not args.dry_run:
            flush_log()

        with contextlib.redirect_stdout(Tee(sys.stdout, buffer)):
            if not args.dry_run:
                target = Path(args.json) if args.json else Path(
                    verify_root, f"verify-{year}.json")
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=1)
                print(f"  -> {target}")

            report_year(year, payload)

            if not args.dry_run:
                # The paths are printed into the log itself, so the log names all
                # of its own outputs and a reader is never left guessing where
                # another one went.
                print(f"  -> {log_path}")
                if write_html:
                    print(f"  -> {html_path}")
                flush_log()

        if not args.dry_run:
            logs_written.append(log_path)
            print(f"  -> {log_path}")

        if write_html and not args.dry_run:
            # After the log, and outside the redirect: the HTML is a third view
            # of a payload that is already on disk, so it can never be the thing
            # that takes the run down. write_html_report() swallows its own
            # errors for the same reason.
            written = write_html_report(payload, html_path)
            if written:
                logs_written.append(written)
                print(f"  -> {written}")

    print("=" * 78)
    print(f"Totale: {sum(totals.values())} file  "
          f"{MARK[PASS]}{totals[PASS]}  {MARK[FAIL]}{totals[FAIL]}  "
          f"{MARK[WARN]}{totals[WARN]}  {MARK[SKIP]}{totals[SKIP]}")
    print(f"Cache: {cache.hits} riutilizzati, {cache.misses} calcolati")
    print_check_legend()
    return 1 if totals[FAIL] else 0


def vocab_of(volumes, code):
    """The union of every volume's vocabulary, for files with no manifest.

    Deliberately permissive: the code census is only used to stop the
    verifier inventing a code out of prose, and a code that one volume knows
    is not thereby wrong in another.
    """
    union = set()
    for volume in volumes:
        union |= set(volume.get("vocabulary", []))
    return union


def read_manifest_years(reports_dir):
    """The years manifest.tsv knows about, so a bad suffix can be caught."""
    path = Path(reports_dir, "manifest.tsv")
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[-1].startswith("http"):
            rows.append(parts)
    return rows


if __name__ == "__main__":
    sys.exit(main())
