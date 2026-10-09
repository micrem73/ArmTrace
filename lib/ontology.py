#!/usr/bin/env python3
"""
Ontology of the Law 185/1990 annual relations: which ministry produced a
table, under which article of the law, and therefore where the table belongs
in the Out/ tree.

    Out/PDF/<authority>/[<article>/]<TableName><Year>.PDF
    Out/CATALOG/catalog.sqlite          the shapes of those tables, per year

This module owns the vocabulary and the path shape. It does *not* decide which
ministry a given table belongs to: that comes from the volume's own INDICE
where it has one (lib/indice.py, 11 of the 22 in-scope volumes) and from
classify_page() below where it does not -- 2017, 2019 and all three 2021
volumes. What always comes from here is the *article*, because the INDICE bounds
ministries but does not subdivide the Dogane, whose four allegati share a single
block.

Why the authority level is not cosmetic
---------------------------------------
The archive reuses the same table code for different tables in different
ministries. In 2025 the MAE prints "TAB M1 (Intermediazioni per Operatore)"
and the Dogane print "TAB. M1" for Esportazioni Definitive; likewise N1, N2,
O1, O2, P1, P2. Keyed on the code alone, the second silently overwrites the
first, and that is what the previous flat layout did. A code is therefore only
unique within (authority, article), and the output path has to carry both.

The four authorities
--------------------
Read off the INDICE of the volumes, cross-checked against the running header
of every table page:

    MAE      Ministero degli Affari Esteri e della Cooperazione
             Internazionale -- Unità Autorizzazioni Materiali Armamento.
             Tables A1..P2, per operator and per country, per operation type.
    MEF      Ministero dell'Economia e delle Finanze -- Dipartimento del
             Tesoro, Direzione V. Every table is art. 27 (credit-institution
             reporting), so the family 1 and 3 codes AA, BB, UE, GF, NN, OO,
             PP, LGP, ... all live here.
    DOG      Agenzia delle Dogane e dei Monopoli, which sits inside the MEF
             part of the INDICE but is reported separately. This is the only
             authority whose tables are keyed by article.
    DIFESA   Ministero della Difesa -- Segretariato Generale / DNA. Its tables
             are "annessi" under art. 2 comma 6 (maintenance and training
             services). The INDICE gives DIFESA no "Tabelle" line, so these are
             easy to overlook; they exist in every recent volume.

Articles
--------
Only DOG and DIFESA subdivide by article, and the subdivision is printed on
the page, not merely implied by position:

    DOG     the four allegati each carry a qualifier line in the running
            header above the table title:

              (no qualifier)                            OPERAZIONI A LICENZA
                                                          art. 1 comma 2
              Programmi Intergovernativi                 PROGRAMMI DI COPRODUZIONE
                                                          INTERGOVERNATIVA
                                                          art. 1 commi 8 lett. a)
                                                          e 9 lett. a)
              Licenze Globali di Progetto                OPERAZIONI A LICENZA GLOBALE
                                                          DI PROGETTO
                                                          art. 11 comma 5-bis
              Autorizzazioni Globali di Trasferimento    OPERAZIONI AD AUTORIZZAZIONE
                                                          GLOBALE DI TRASFERIMENTO
                                                          art. 10 quater

            The qualifier travels with every page of the table, so the article
            is read off the table itself rather than inferred from where the
            table sits. That matters because the four covers are printed
            consecutively and only then the tables follow, so positional
            inheritance would be wrong; and because art. 1 comma 2 and
            art. 1 commi 8/9 share the code letters M, N, O, P, N1, O1 ...

    DIFESA  every annesso is art. 2 comma 6; the annesso number (2, 3A, 3B,
            3C, 4) is the table name, so the path is
            DIFESA/A2C6/3A<Year>.PDF with no extra level.

Token spelling
--------------
Article directories keep the differentiators the law actually uses. "A10"
alone cannot say quater from bis or quinquies -- all three appear in Law
185/1990, and art. 10 quinquies is cited in the Dogane footnote of both 2020
and 2025 -- so the token spells it out. Likewise comma 5 and comma 5-bis are
different authorisations and get different tokens.
"""

import re

# The four authorities that produce tables.
MAE = "MAE"
MEF = "MEF"
DOG = "DOG"
DIFESA = "DIFESA"

# Used when a page carries no authority marker at all and no neighbouring page
# does either. Kept explicit rather than silently dropped, so a gap shows up in
# the coverage report instead of landing in a plausible-looking folder.
UNKNOWN = "UNKNOWN"

AUTHORITIES = (MAE, MEF, DOG, DIFESA)

# --------------------------------------------------------------------------
# page-local markers
#
# These were read off the running header of every section in pypdf reading
# order, which is NOT the order pdftotext -layout reports and is not the visual
# top-to-bottom order either: the export band is rotated, so its text lands at a
# varying position in the content stream. What each ministry actually prints:
#
#   MAE      MAECI -UAMA - CENTRO INFORMATICO Pagina 11 di 11 TAB M1
#   MEF      Tabella AA / Dipartimento del Tesoro Direzione V - Ufficio VIII
#            / ELENCO TABELLE / Operazioni disciplinate dall'art. 27 ...
#   DOG      [qualifier] / Tipo di operazione: ... / TAB. M
#   DIFESA   MINISTERO DELLA DIFESA - Annesso 3A
#
# MAE writes "TAB" and DOG writes "TAB.", MEF spells it out as "Tabella", and
# DIFESA prints no code at all -- so the code's own typography is a reliable
# discriminator, which matters because MAE pages do not always carry the export
# band and MEF pages carry the ELENCO banner only on the first page of a table.
# --------------------------------------------------------------------------

# DIFESA prints authority and annesso on one line. No IGNORECASE on the annesso
# group: "Annesso 4 TABELLA RIASSUNTIVA" must yield "4", not "4 T", so the
# letter is uppercase and adjacent to the digits.
DIFESA_RE = re.compile(
    r"(?i:MINISTERO\s+DELLA\s+DIFESA)\s*[-–—]?\s*(?i:Annesso)\s*([0-9]+[A-Z]?)"
)

# Dogane. The period in "TAB." is the discriminator against MAE's "TAB"; the
# code may sit a little way along the line from the keyword, because the export
# band is rotated and lands between them.
#
# The keyword is spelled out letter by letter with \s* between them, for the
# reason recorded on MEF_BANNER_SQUEEZED: pypdf breaks these pages wherever the
# font was subsetted, so "TAB. M" arrives as 'T' / 'AB. M' on 13 pages of 2025
# vol. II and classify_page() returned None for all of them. That was survivable
# while the pages were anonymous interior -- fill_provenance() gave them the
# ministry from their neighbours -- but the M appendix cover at p743 is the
# FIRST page of a table, and its missing article split the table in two: one
# file keyed (DOG, None, MAPPENDICE) holding a single page, and another keyed
# (DOG, A1C2, MAPPENDICE) holding the other 17. Only the pages carrying an
# article take part in grouping, so a single unclassifiable opening page is
# enough to create a table of its own.
#
# The tolerance is confined to the keyword itself. The separator before the code
# still cannot span a newline, and the period is still required, so MAE's "TAB"
# and a prose "TAB." are unaffected.
DOG_CODE_RE = re.compile(r"T\s*A\s*B\s*\.[^\n]{0,40}?\b([A-Z]{1,3}\d{0,2})\b")
DOG_QUALIFIER = (
    (re.compile(r"Autorizzazioni\s+Globali\s+di\s+Trasferimento", re.IGNORECASE),
     "A10QUATER"),
    (re.compile(r"Licenze\s+Globali\s+di\s+Progetto", re.IGNORECASE),
     "A11C5BIS"),
    (re.compile(r"Programmi\s+(?:di\s+Coproduzione\s+)?Intergovernativi",
                re.IGNORECASE),
     "A1C89"),
)
# art. 1 comma 2 carries no qualifier: it is the individual-licence allegato and
# the qualifier slot is blank on those pages.
DOG_DEFAULT_ARTICLE = "A1C2"

# MEF: the spelled-out code on a line of its own, its own office, or the art. 27
# banner. The anchored form is what keeps "Tabella dei codici delle valute" out.
MEF_CODE_RE = re.compile(r"^\s*Tabella\s+([A-Z]{1,3}\d{0,2})\s*$", re.MULTILINE)
MEF_ALT_RE = re.compile(
    r"Dipartimento\s+del\s+Tesoro\s+Direzione\s+V|Operazioni\s+disciplinate\s+dall"
    r"'\s*art\.\s*27\b",
    re.IGNORECASE,
)

# The same banner, matched with whitespace squeezed out, because pypdf shreds
# these pages: 2025 vol. II prints the office line as "Dip" / "artimento del
# Tesoro Direzione V - Uffici" / "o VIII" and the law line as "Operazio" / "ni
# disciplinate dall'art. 27", so every word MEF_ALT_RE is built from is broken
# across two lines and neither alternative matches. Only the MEF prints this
# banner, and it prints it on every page of its relation and of its annex, so it
# is what marks a page as belonging to the art. 27 section.
MEF_BANNER_SQUEEZED = ("DipartimentodelTesoroDirezioneV", "art.27")


def is_mef_section(text):
    """True when a page carries the MEF art. 27 section banner.

    Read against the page with whitespace squeezed out, for the reason recorded
    on MEF_BANNER_SQUEEZED: the words are split across lines by the extractor,
    not by the typesetting.
    """
    squeezed = re.sub(r"\s+", "", text)
    return any(marker in squeezed for marker in MEF_BANNER_SQUEEZED)

# MAE. Two forms, both checked last because neither is exclusive on its own:
#
#   export band   "MAECI -UAMA - CENTRO INFORMATICO Pagina 11 di 11 TAB M1".
#                 Requiring "CENTRO INFORMATICO" is what makes it safe. The
#                 shorter "MAECI-UAMA" also occurs in prose -- the 2025 Difesa
#                 relation says its companies file "comunicazione al MAECI-UAMA"
#                 -- and on those two pages the shorter form fires and the tight
#                 one does not, which put a Difesa page under MAE. Measured over
#                 2025 vol. II: 25 MAE pages, 2 Difesa, 0 elsewhere.
#   office name   "Unita' Autorizzazioni Materiali Armamento" / "Unità ...".
#                 Needed for volumes whose MAE pages carry no export band at all.
#
# The separator never spans a newline, for the reason documented on INLINE_CODE:
# p202 of 2025 vol. II breaks "MAECI-" and "UAMA" across lines.
MAE_RE = re.compile(
    r"MAECI[^\S\n]*[-–][^\S\n]*UAMA[^\S\n]*-[^\S\n]*CENTRO[^\S\n]+INFORMATICO"
    r"|Unit[ae]\w*\s+Autorizzazioni\s+Materiali\s+Armamento",
    re.IGNORECASE,
)
# MAE pages without the export band still print the code as "TAB <code>" with
# no period. Restricted to the family 2 vocabulary so that prose such as
# "tabella AA" spelled out, or "TABELLE", cannot reach it.
MAE_CODE_RE = re.compile(r"\bTAB\s+(?![.\w])([A-Z]{1,2}\d{1,2})\b")

# Every DIFESA annesso is art. 2 comma 6 (maintenance and training services).
DIFESA_ARTICLE = "A2C6"

SAFE_CODE_RE = re.compile(r"[^A-Z0-9]+")


def classify_page(text):
    """(authority, article, annesso) for one page, or (None, None, None).

    `text` is the page's extracted text.

    The ministry's own office and export band are trusted wherever they appear
    on the page, including in the body: those strings are printed only by the
    ministry itself. The narrative *does* name other ministries -- the 2020 MEF
    relation opens by praising the "Unità Autorizzazioni Materiali Armamento
    del Ministero degli Affari Esteri" -- so the authority is decided by which
    office is speaking, and only when no office speaks does the code's
    typography break the tie.

    Order is by exclusivity, measured over 2025 vol. II rather than guessed:

    marker                          MAE   MEF   DOG   DIFESA
    TAB. <code>                       0     0   526       0
    Dipartimento del Tesoro / art.27 0    35     0       0
    MAECI -UAMA - CENTRO INFORMATICO 25     0     0       0

MAE goes last because it is the only one of the four that leaks: its office is
named in other ministries' prose, so it is asked only once the three exclusive
markers have declined. DIFESA goes first because its running header names both
the ministry and the annesso and nothing else prints it.
    """
    difesa = DIFESA_RE.search(text)
    if difesa:
        return DIFESA, DIFESA_ARTICLE, difesa.group(1).upper()

    if DOG_CODE_RE.search(text):
        for pattern, article in DOG_QUALIFIER:
            if pattern.search(text):
                return DOG, article, None
        return DOG, DOG_DEFAULT_ARTICLE, None

    if MEF_CODE_RE.search(text) or MEF_ALT_RE.search(text):
        return MEF, None, None

    if MAE_RE.search(text) or MAE_CODE_RE.search(text):
        return MAE, None, None

    return None, None, None


def safe_code(code):
    """Filesystem-safe table name.

    The archive's codes are already safe (A1, AA, MG13, UE, LGP), and the one
    that is not is the Dogane appendix -- which is now real: step 1 reads
    "TAB. M - APPENDICE" as the table MAPPENDICE (see APPENDIX_CODE there), so
    this is what keeps that word from turning into a path segment.

    Note the code is *glued* rather than space-separated, "MAPPENDICE" and not
    "M APPENDICE", and that is load-bearing rather than cosmetic. The verifier
    reads the code back out of the filename stem, so the manifest and the
    filename have to hold the same string; a space here would be stripped on the
    way to disk and the two would then disagree.
    """
    cleaned = SAFE_CODE_RE.sub("", str(code).upper())
    return cleaned or "TAB"


def relative_path(authority, article, code, year, volume, page, extension):
    """Out-relative path for one table.

        MAE/A1<year>V1P70.PDF
        MEF/AA<year>V2P273.PDF
        DOG/A1C2/N<year>V2P470.PDF
        DIFESA/A2C6/3A<year>V2P236.PDF

    MAE and MEF carry no article directory because the reports do not subdivide
    them by article; for DIFESA the annesso is the table name, so it lands in
    the code position rather than adding a level of its own.

    The trailing V<volume>P<page> is where the table came from, so that a person
    browsing Out/PDF/ can open a file and know which volume and which printed
    page it was cut from without consulting anything. It is not decoration: it is
    also what makes the filename parseable. The stem used to be split on "the
    last four digits are the year", which is ambiguous -- MG102025 is MG10 of
    2025, and nothing in the string says so. Anchoring on an explicit V/P tail
    removes the ambiguity, because the tail is unmistakable and what precedes it
    is fixed-length.

    `volume` is 1-based in document order within the reporting year, and `page`
    is the first page of the table's FIRST segment. A table that straddles the
    volume join (2025 Tabella F1 runs p1039-1046 of vol. I and p5-66 of vol. II)
    therefore names vol. I, which is where the table starts and where its
    bookmark, index row and first data page all point.
    """
    parts = [authority]
    if article:
        parts.append(article)
    parts.append(f"{safe_code(code)}{year}V{int(volume)}P{int(page)}{extension}")
    return "/".join(parts)


# The stem, anchored. See relative_path() for why the tail is there.
STEM_RE = re.compile(
    r"^(?P<code>.+?)(?P<year>\d{4})V(?P<volume>\d+)P(?P<page>\d+)$")


def parse_stem(stem):
    """{code, year, volume, page} from a filename stem, or None.

    THE place a stem is taken apart. There used to be three: this one,
    VerifyTables.check_layout's "does it start with the code and is the rest the
    year", and CatalogueTables' own copy of the trailing-four-digits regex. They
    disagreed by construction, and adding a V/P tail to the filename would have
    made all three fail at once -- the verifier would read the code as
    "A12025V1P70" and step 2 would fall back to the --year argument without
    saying so. One implementation, called from all of them.

    A stem without the tail is None, not a guess. This is a clean break: the
    verifier can no longer audit output written before the tail existed, and
    pretending otherwise by parsing both shapes would let a stale tree look
    valid.
    """
    m = STEM_RE.match(stem)
    if not m:
        return None
    return {
        "code": m.group("code"),
        "year": int(m.group("year")),
        "volume": int(m.group("volume")),
        "page": int(m.group("page")),
    }


def article_label(authority, article):
    """Human label for the log."""
    if not article:
        return ""
    if authority == DOG:
        return {
            "A1C2": "art. 1 comma 2 -- operazioni a licenza",
            "A1C89": "art. 1 commi 8 lett. a) e 9 lett. a) -- coproduzione",
            "A11C5BIS": "art. 11 comma 5-bis -- licenze globali di progetto",
            "A10QUATER": "art. 10 quater -- autorizzazioni globali di trasferimento",
        }.get(article, article)
    if authority == DIFESA:
        return "art. 2 comma 6 -- manutenzione e addestramento"
    return article