"""2024 edition: Doc. LXVII n. 3 (leg. XIX), two volumes (lxvii_3 volume 1 and 2).

What is true of this edition only; everything else is lib/engine.py. The
functions below replace the engine's under the same name (engine.OVERRIDES);
the rest are this edition's own helpers. Ported unchanged from the per-year
copy of commit bb05615, reading pages through the page cache.

  * vol. I: the ELENCO on p70 states an exact page count per table, the only
    witness to the 21 tables whose 335 pages carry no readable code. They are
    placed by walking the listing from a detector-anchored start, and checked
    against the export stamps (place_from_plan, stamp_witness).
  * vol. II: the Dogane's own code space (bare M/N/O/P, MG1-MG18, MT1/7/13) is
    outside the MEF vocabulary; fifteen covers print the code under a cut
    keyword (pageio.cover_code, in the engine); vol. II is family 1, not 3 (detect_family).

The volumes are separate documents whose folios run continuously, and every
2024 table stops before printed 1000: no table straddles the join, so stitch()
must leave `continued` empty. A run that fires it is a defect.
"""

import re

from lib import engine, indice, ontology
from lib.engine import (
    block_at,
    body_starts,
    FAMILY2_CODES,
    fill_provenance,
    header_fingerprint,
    INDEX_ROW,
    INLINE_CODE,
    is_allegato_page,
    is_blank,
    is_ciphered,
    is_garbled,
    is_index_page,
    LEADING_CODE,
    listed_pages,
    listing_codes,
    MIN_RUN,
    normalise_digit_one,
    page_text,
    printed_numbers,
    provenance_from_indice,
    read_page,
    record_boundary,
    rel_code,
    rel_headings,
    relation_exhibits,
    REL_STYLE,
    REL_TABELLA,
    REPRINT_GAP,
    resolve_placement,
    same_table,
    scan_bookmarks,
    trim_trailing,
    trim_trailing_blanks,
)
from lib.patterns import rel_article

VOLUME = "both"


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
            text = page_text(page)
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


# The Dogane covers that cut the code onto the line after the keyword are read
# by the engine (pageio.cover_code, shared with 2023 vol. III).
#
# Styles this year adds. Their own number, because every existing style is a
# shape that could be a law table and this one is not.
DOGANE_STYLE_2024 = 9     # a code from the Dogane's own scheme, below


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
            text = page_text(reader.pages[i])
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


# The shared detector, kept under its own name so the wrapper below can call it.
# A year that shadows page_code must not re-implement it.
_page_code_shared = engine.page_code


def page_code(lines, text, vocabulary, family, dogane=False):
    """The shared detector, plus the two shapes 2024 vol. II needs.

    WHAT THE VOLUME PRINTS THAT THE SHARED DETECTOR REFUSES. 2024 vol. II is
    two ministries in one document: the MEF's art. 27 double-letter annex
    (AA..UE, 35 codes, named by the volume's own ELENCO and by its bookmark
    tree), and the Dogane's own annex, which switches scheme at printed 1707 and
    prints TAB. M / N / O / P, the bare bases of the M1..P2 series, then
    MG1..MG18 and MT1/MT7/MT13 under art. 11 comma 5-bis. None of that is in
    the MEF vocabulary, and style 3's test -- "a code the volume's own listing
    promised" -- throws every one of them away. That is what made the 2026-10-05 baseline's
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
    if not dogane:
        return None, None
    for line in lines:
        for m in INLINE_CODE.finditer(line):
            cand = m.group(1).upper()
            if cand not in FAMILY2_CODES and cand not in vocabulary:
                return cand, DOGANE_STYLE_2024
    return None, None


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
    open_exhibit = None
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
        # The MEF relation's own exhibits (patterns.MEF_EXHIBIT) win over the
        # annex code the page also prints: p78's rows name AA..DD inside the
        # uncoded "Riepilogo saldi", and p79's "Tabella UE" is not the annex UE.
        # Always cut, even alone on a page: the ministry's prose shares them.
        mef, open_exhibit = relation_exhibits(text, open_exhibit)
        if mef:
            code, style = rel_code(mef[0][1]), REL_STYLE
            for _kind, number in mef:
                page_codes.setdefault(page_no, []).append(
                    (rel_code(number), REL_STYLE))
            if len(mef) > 1 and shared_pages is not None:
                shared_pages.append((page_no, mef))
            if rel_pages is not None:
                rel_pages.append((page_no, mef))
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

        if not rel and not mef:
            page_codes.setdefault(page_no, []).append((code, style))
        # Style 1 also matches summary pages: 2023 vol. II p8 carries a bare
        # "UE" under the same header and would steal the start of the real
        # Tabella UE at p543. A banner-backed page clears the flag, so the
        # table is provisional unless at least one of its pages is backed.
        if style == 1 and "ELENCO TABELLE" not in text:
            weak_pages.add(page_no)
    return (page_codes, weak_pages, unassigned, garbled, ciphered, foreign,
            listings, candidates, shared_pages or [], rel_pages or [])


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
            if style == REL_STYLE:
                article = rel_article(code) or article
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
        # A MEF relation exhibit keeps REL: resolve_placement ignores
        # articles for MEF, and without it MEF/UE would be two tables.
        if info.get("style") == REL_STYLE:
            info["article"] = rel_article(code) or info["article"]
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
