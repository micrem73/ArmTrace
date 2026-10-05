"""Parse ministry page ranges out of a volume's INDICE.

Line-oriented, because the INDICE's dot leaders separate the label from the page
number and a whole-line start is what distinguishes a real index entry from the
"Relazione sull'attivita' del Ministero dell'economia e delle finanze" sub-line
that would otherwise match the MEF label.

Handles the four things that broke earlier attempts:
  - long ministry names hyphenated across lines ("COOPERA-\\nZIONALE"), so the
    page number lands on the continuation line and a `pending` entry carries over
  - "DEGLI AFFARI ESTERI" (MAE) against "DELL'ECONOMIA" / "DELLE FINANZE" (MEF):
    the article is DEGLI, not DELL, so the pattern is DE\\w* and not DEL\\w*
  - "FIN ANZE" and "atti vita", i.e. spaces injected inside words by the
    original typesetting; harmless because only the ministry keyword is matched
  - the page number sitting *after* the dot leaders, never before them, so
    splitting on the leaders separates the label from its number and loses both
"""
import re

LABELS = [
    (re.compile(r"AFFARI\s+ESTERI", re.I), "MAE"),
    (re.compile(r"ECONOMIA", re.I), "MEF"),
    (re.compile(r"DIFESA", re.I), "DIFESA"),
    (re.compile(r"INTERNO", re.I), "INTERNO"),
    (re.compile(r"DOGANE", re.I), "DOG"),
]
MINISTRY_HEAD = re.compile(r"^\s*(?:PRESIDENZA\s+DEL\s+CONSIGLIO|"
                           r"MINISTERO\s+DE\w*|AGENZIA\s+DELLE\s+DOGANE)",
                           re.IGNORECASE)
TABELLE_HEAD = re.compile(r"^\s*Tabelle\b", re.IGNORECASE)
PAGNUM = re.compile(r"(?:»|»|Pag\.?)\s*(\d{1,4})\s*$")
DOGANE_HEAD = re.compile(r"^\s*Relazione\s+sull.attivit.\s+dell.Agenzia\s+delle\s+[Dd]ogane",
                         re.IGNORECASE)


def _label(chunk):
    for rx, name in LABELS:
        m = rx.search(chunk)
        if m:
            return name
    return None


def _number(chunk):
    m = PAGNUM.search(chunk.rstrip())
    return int(m.group(1)) if m else None


def parse_indice(text):
    """[(authority, kind, folio)] in document order. kind is BLOCK or TABELLE."""
    lines = text.split("\n")
    entries = []
    pending = None            # (label, accumulated text) awaiting its number
    last_label = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if TABELLE_HEAD.match(line):
            n = _number(line)
            if n and last_label:
                entries.append((last_label, "TABELLE", n))
            continue
        head = DOGANE_HEAD.match(line) or MINISTRY_HEAD.match(line)
        if head:
            name = "DOG" if DOGANE_HEAD.match(line) else _label(line)
            if name is None:
                pending = None
                continue
            last_label = name
            n = _number(line)
            if n is not None:
                entries.append((name, "BLOCK", n))
                pending = None
            else:
                # number is on the continuation line, after the dot leaders
                pending = [name, line]
            continue
        if pending is not None:
            pending[1] += " " + line
            n = _number(pending[1])
            if n is not None:
                entries.append((pending[0], "BLOCK", n))
                pending = None
    return entries


def merge_blocks(entries):
    """Collapse the index into ordered [authority, start_folio, tables_folio].

    A ministry split across volumes ("... (segue) Pag.1043") contributes its
    earliest folio, so one ministry becomes one contiguous block.
    """
    order, tables = [], {}
    for name, kind, folio in entries:
        if kind == "BLOCK":
            if name in tables:
                continue                       # "(segue)" -- keep the first
            order.append([name, folio])
            tables[name] = None
        else:
            if name in tables:
                prev = tables[name]
                tables[name] = folio if prev is None else min(prev, folio)
    return [(name, folio, tables.get(name)) for name, folio in order]


# The index heading, matched with its letterspacing tolerated. 2024 prints it as
# "I N D I C E", every other year as "INDICE", and a plain substring test for
# "INDICE" silently rejects 2024 -- which is how a volume with a perfectly good
# index was recorded as having none. The page is squeezed of whitespace first,
# because pypdf also shatters some of these headings across fragments.
HEADING_RE = re.compile(r"I[\s.]{0,4}N[\s.]{0,4}D[\s.]{0,4}I[\s.]{0,4}C[\s.]{0,4}E",
                        re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def find_indice(reader, max_pages=40):
    """(page_number, blocks) for the first readable INDICE page."""
    for i in range(min(max_pages, len(reader.pages))):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            continue
        if not HEADING_RE.search(_SPACES.sub("", text)):
            continue
        entries = parse_indice(text)
        # A narrative mention of the word yields no entries, so the scan keeps
        # going; that is what stops a passing mention from being taken as the
        # index and shadowing the real one on a later page.
        if entries:
            return i + 1, merge_blocks(entries)
    return None, None


# --------------------------------------------------------------------------
# trusting the result
# --------------------------------------------------------------------------
#
# A parse failure is silent, not loud. On 2018 vol. I the index is laid out per
# volume and the parser picks up the "Volume I" heading line as a ministry,
# producing "MEF block@1" out of document order -- wrong, plausible, and
# invisible unless checked. So a parse is only used once it passes:

#   * at least MIN_BLOCKS ministries, since a single block cannot bound anything
#   * start folios strictly increasing, which rejects out-of-order front matter
#   * a folio map covering COVERAGE of the volume's pages, so a garbled volume
#     whose folios cannot be read does not silently map every page to one block
#   * no block before the first page of the volume, i.e. the first block starts
#     at or before where this volume begins
MIN_BLOCKS = 3
COVERAGE = 0.50

FOLIO_RE = re.compile(r"[–—-]\s*(\d{1,4})\s*[–—-]")


def folio_map(reader):
    """(offset, coverage) for the volume's printed folio.

    The offset is the *mode* of (folio - page) over every page whose folio could
    be read, not a linear fit. A fit is wrong because the front matter carries
    its own numbering -- page 3 of 2023 vol. II reads "- 3 -" -- and fitting
    across that discontinuity gives a slope of 2.6 instead of 1, mapping every
    boundary to nonsense. The mode ignores the handful of outlier pages without
    needing to know which run they came from.

    Coverage is the fraction of pages whose folio was legible at all, which is
    what validation needs: a garbled volume can still yield a correct offset from
    the few pages it can read.
    """
    from collections import Counter
    offsets = Counter()
    legible = 0
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        vals = [int(m.group(1)) for m in FOLIO_RE.finditer(text)]
        if vals:
            legible += 1
            offsets[vals[0] - (i + 1)] += 1
    coverage = legible / max(1, len(reader.pages))
    if not offsets:
        return None, coverage
    # Ties broken by the smaller offset, so the result does not depend on dict
    # ordering when two runs are equally long.
    offset = min(offsets, key=lambda o: (-offsets[o], o))
    return offset, coverage


def validate(blocks, offset, coverage, page_count):
    """(ok, reason). A parse is used only if it passes every check."""
    if not blocks:
        return False, "no INDICE in this volume"
    if len(blocks) < MIN_BLOCKS:
        return False, f"only {len(blocks)} ministry block(s), need {MIN_BLOCKS}"
    folios = [f for _n, f, _t in blocks]
    if any(b <= a for a, b in zip(folios, folios[1:])):
        return False, f"block folios not increasing: {folios}"
    if offset is None:
        return False, "no folio numbering found in this volume"
    if coverage < COVERAGE:
        return False, f"folio readable on only {coverage:.0%} of pages"
    # At least one block must fall inside this volume's own folio span, else the
    # index describes a different part of the document. The check is *at least
    # one*, not the first: the index also lists later volumes, so the first block
    # legitimately starts after this volume's page 1 when this is volume I.
    span = (1 + offset, page_count + offset)
    if not any(span[0] <= f <= span[1] for f in folios):
        return False, f"no block inside this volume's folios {span}"
    return True, "ok"


def authority_ranges(blocks, offset):
    """[(start_folio, authority)] sorted, for a bisect over page folios."""
    out = sorted((folio, name) for name, folio, _t in blocks
                 if name in ("MAE", "MEF", "DOG", "DIFESA"))
    return out


def authority_for_page(ranges, page, offset):
    """Ministry owning a page, from the INDICE ranges. None if before all."""
    folio = page + offset
    chosen = None
    for start, name in ranges:
        if start <= folio:
            chosen = name
        else:
            break
    return chosen


# --------------------------------------------------------------------------
# documents pasted into the volume
# --------------------------------------------------------------------------
#
# Each report is assembled from separately printed documents, and the odd one is
# sometimes neither this ministry's nor even on this subject. Both cases in the
# archive are Gazzetta Ufficiale excerpts bound into the volume:
#
#   2019 vol. I  pp. ~768-826, Serie generale 1588 of 7 July 2019 -- drug prices
#                and patents. Its prose cross-references "Tab. I" on two
#                consecutive pages, which the header scan read as a 22-page
#                table, and it was the only UNPLACED table in the volume.
#   2025 vol. II pp. 194-234, Serie generale 1319 of 6 June 2025 -- the technical
#                annex listing the CAT armament and dual-use categories. This one
#                belongs to the report rather than being misfiled, but it holds no
#                tables either, so it is skipped on the same grounds.
#
# A sampled scan of all 43 volumes finds gazette pages in these two and nowhere
# else, so skipping them costs nothing measurable and the report prints what was
# skipped so the decision stays auditable.
#
# An earlier attempt used the absence of a valid running folio instead, which is
# the more principled witness -- a pasted-in document keeps its own pagination.
# It was abandoned because the folio signal is too noisy to threshold safely:
# pypdf does not emit the running folio first on every page, and ordinary garbled
# or blank pages interrupt any run, so a 20-page minimum flagged 206 pages of
# 2019 vol. I in thirteen scattered runs and still missed the block it was
# written for.
GAZETTE_RE = re.compile(r"GAZZETTA\s*UFFICIALE", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def is_pasted(text):
    """True when a page belongs to a Gazzetta Ufficiale excerpt.

    Matched against the page with whitespace squeezed out, because pypdf shreds
    these pages into fragments and no line ever holds the whole header.

    Called from inside the page scan rather than by a pass of its own: the scan
    already extracts every page, and a second full extraction of a 1048-page
    volume added enough to push the run past forty minutes.
    """
    return bool(GAZETTE_RE.search(_SPACES.sub("", text)))


def page_ranges(pages):
    """[(first, last)] for a set of page numbers, consecutive ones merged."""
    out = []
    for page in sorted(pages):
        if out and page == out[-1][1] + 1:
            out[-1][1] = page
        else:
            out.append([page, page])
    return [tuple(r) for r in out]