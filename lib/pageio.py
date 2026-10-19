"""Reading one page: its text, the running stamp, the printed folio, its corruption.

Split out of Reports2PDFTables.py on 2026-10-11. These know what a page SAYS and never
what it means -- no ministry, no code vocabulary, no year. That separation is what lets
a year change how tables are found without touching how pages are read, and it is why
VerifyTables.py can import from here and still audit a year that has forked its
detectors."""


import re

from lib.patterns import (
    ALLEGATO_HEAD, ANNEX_HEADER, APPENDIX_CODE, APPENDIX_SUFFIX, COVER_CODE, COVER_KEYWORD, ELENCO_HEAD, ELENCO_MIN_CODES,
    ELENCO_ROW, EXHIBIT_CLOSED, MAX_TRAILING_PAGES, MEF_EXHIBIT, MEF_EXHIBIT_TITLES, PAGE_NUMBER_RE,
    PUA_RE, REL_CONTINUED, REL_HEADING, REL_HEADING_SCAN, REL_PREFIX, REL_SALDI, STAMP_BAND, STAMP_NUM, STAMP_SIZE, STAMP_WORD,
    SUBSET_SPACE, TITLE_RE, _WS,
)


# The running header every page of the printed act carries, which is not part
# of any table: the two chambers, the legislature line and the folio.
# "LEGISLA TURA": pypdf splits the word on 2023 vol. III's covers, and the line
# it leaves behind hid 13 Dogane covers from is_table_cover.
RUNNING_HEADER = re.compile(r"Senato della Repubblica|Camera dei Deputati|LEGISLA\s*TURA")
COVER_MAX_LINES = 8
COVER_MAX_CHARS = 160


def is_table_cover(text, code):
    """True when the page is `code`'s own cover sheet, not a page of data.

    The Dogane open every table with a page that names it and holds nothing
    else -- "Riesportazione (RE) / TAB. M - APPENDICE", then the data stamped
    "Pag. 1 di 17". Once the header is stripped such a page is a few short
    lines ending in the table's own TAB. caption. pypdf shreds the caption
    ("T" / "AB. M - APPENDICE"), so the test squeezes whitespace out first.
    """
    body = [ln.strip() for ln in text.split("\n") if ln.strip()]
    body = [ln for ln in body
            if not RUNNING_HEADER.search(ln) and not PAGE_NUMBER_RE.match(ln)]
    if not body or len(body) > COVER_MAX_LINES:
        return False
    if sum(len(ln) for ln in body) > COVER_MAX_CHARS:
        return False
    squeezed = _WS.sub("", "".join(body)).upper()
    return squeezed.endswith(_tab_caption(code))


def _tab_caption(code):
    """'M' -> 'TAB.M', 'MAPPENDICE' -> 'TAB.M-APPENDICE', whitespace squeezed."""
    if code.endswith(APPENDIX_SUFFIX) and code != APPENDIX_SUFFIX:
        return f"TAB.{code[:-len(APPENDIX_SUFFIX)]}-{APPENDIX_SUFFIX}".upper()
    return f"TAB.{code}".upper()


def page_names_table(text, code):
    """True when the page prints `code`'s own TAB. caption on a data page.

    Not a prefix match: TAB.M must not be read off TAB.M1 or TAB.M-APPENDICE.
    The Dogane summary tables ("Riepilogo per operatore") print no caption on
    their data pages at all -- only the cover names them.
    """
    # Whitespace allowed between any two characters (pypdf shreds the caption),
    # but not squeezed away: the caption must end where a word ends.
    spaced = r"\s*".join(re.escape(ch) for ch in _tab_caption(code))
    tail = r"(?![A-Z0-9])"
    if not code.endswith(APPENDIX_SUFFIX):
        tail += r"(?!\s*-\s*" + r"\s*".join(APPENDIX_SUFFIX) + ")"
    return re.search(spaced + tail, text.upper()) is not None


# A citation of an article of L. 185/90, in the spellings the Difesa annexes
# print: "art. 5. Legge n.185/90", "Art.2 co.6 L. 185", "ART.2 comma 6 L.185",
# "L. 185/90 art_2co6". The law number must sit within a short reach of the
# article, so a stray "art." in prose does not count.
ARTICLE_CITE = re.compile(
    r"art[\s._]*(\d+)(?:[\s._]*(?:comma|co|c)[\s._]*(\d+))?[^\n]{0,25}?185"
    r"|185\s*/\s*90[^\n]{0,15}?art[\s._]*(\d+)(?:[\s._]*(?:comma|co|c)[\s._]*(\d+))?",
    re.IGNORECASE)


def cited_article(text):
    """'A5', 'A2C6' for the first L. 185/90 article the text cites, else None."""
    m = ARTICLE_CITE.search(text)
    if not m:
        return None
    article = m.group(1) or m.group(3)
    comma = m.group(2) or m.group(4)
    return f"A{article}" + (f"C{comma}" if comma else "")


def is_allegato_page(text):
    """True for a Dogane allegato cover or summary page."""
    return bool(ALLEGATO_HEAD.search(_WS.sub("", text)))



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
    together here would hide exactly that confusion. So the rule is: one extra
    capital, nothing else.

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



def cover_code(lines):
    """The code a Dogane cover prints under its cut keyword, or None
    (patterns.COVER_KEYWORD)."""
    for j, line in enumerate(lines):
        if not COVER_KEYWORD.search(line) or j + 1 >= len(lines):
            continue
        m = COVER_CODE.match(lines[j + 1].strip())
        if m:
            suffix = APPENDIX_SUFFIX if "APPENDICE" in lines[j + 1].upper() \
                else ""
            return f"{m.group(1).upper()}{suffix}"
    return None



def no_text_layer(lines):
    """True for a scanned page: text, but only the act's own furniture.

    2023 vol. I prints the Difesa section as images; the act's running header
    and folio are the only text on them ("XIX LEGISLA TURA ... DOC. LXVII"),
    so a reader that counts text as legible would call them 100% readable.
    """
    if not lines:
        return False
    return all(RUNNING_HEADER.search(ln) or PAGE_NUMBER_RE.match(ln)
               or "LXVII" in ln for ln in lines)



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



def rel_code(number):
    """'8.1' -> 'T81', '23' -> 'T23'. See the note above on why not '8.1'.

    A MEF exhibit prints a code, not a number ('UE'), and keeps it."""
    if not number[:1].isdigit():
        return number
    return REL_PREFIX + number.replace(".", "")


def relation_exhibits(text, open_code):
    """([(kind, code)], open_code) for the MEF relation exhibits on one page.

    `open_code` is the exhibit the previous page left unclosed (no grand total
    after its caption). Amounts printed before this page's first caption are
    its continuation, recorded as (REL_CONTINUED, code) so the writer cuts that
    band too. Returns the exhibit this page leaves open, for the next page.
    """
    joined = "".join(text.split("\n"))
    coded = list(MEF_EXHIBIT.finditer(joined))
    starts = [(m.start(), m.group(1) or REL_SALDI) for m in coded]
    # A bare title opens an exhibit unless "Tabella <code> - " just printed it.
    if not ANNEX_HEADER.search(text):
        for pattern, code in MEF_EXHIBIT_TITLES:
            for t in pattern.finditer(joined):
                if not any(0 <= t.start() - m.end() <= 2 for m in coded):
                    starts.append((t.start(), code))
        starts.sort()
    head = joined[:starts[0][0]] if starts else joined
    found = []
    if open_code and "€" in head:
        found.append((REL_CONTINUED, open_code))
        if EXHIBIT_CLOSED.search(head):
            open_code = None
    elif not starts:
        return [], None
    found += [("Tabella", code) for _pos, code in starts]
    if starts:
        tail = joined[starts[-1][0]:]
        open_code = None if EXHIBIT_CLOSED.search(tail) else starts[-1][1]
    return found, open_code



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
    is the same shape as the Dogane appendix defect (appendix_code), where
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



def _cached(page):
    """The lib.pagecache entry for `page`, or None when its reader has none."""
    ref = getattr(page, "indirect_reference", None)
    cache = getattr(getattr(ref, "pdf", None), "_page_cache", None)
    if not cache:
        return None
    entry = cache.get((ref.idnum, ref.generation))
    if entry is not None and entry["error"]:
        raise RuntimeError(entry["error"])
    return entry


def page_text(page):
    """page.extract_text(), from the page cache when one is attached."""
    entry = _cached(page)
    if entry is not None:
        return entry["text"]
    return page.extract_text() or ""


def read_page(page):
    """(text, stamp) for one page, from a single extraction pass.

    scan_headers needs the page text anyway and the visitor costs nothing
    measurable against it (90.6s against 91.8s for 2025 vol. I, 1048 pages).
    """
    entry = _cached(page)
    if entry is not None:
        return entry["text"], (tuple(entry["stamp"]) if entry["stamp"] else None)
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
