"""Every regex and constant step 1 reads, and nothing else.

Split out of Reports2PDFTables.py on 2026-10-11, when the per-year scripts stopped
sharing one engine. Nothing here knows a year, a ministry or a volume: it is the
printed vocabulary of the archive, which does not change between 2016 and 2025.

This is the highest-blast-radius file in the repository. A wrong pattern here is wrong
for every year at once, where before it was wrong for one. Every entry carries the
measurement or the failure that produced it -- see AGENTS.md section 5, the trap list,
which is not inferrable from the code."""


import re

from lib import ontology


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


# "MINISTERO DELLA DIFESA - Annesso 3A". These tables carry no table code, so
# the annesso is both the code and the last path segment. The letter must stay
# adjacent and uppercase, otherwise "Annesso 4 TABELLA RIASSUNTIVA" is read as
# annesso "4 T" and splits one table into two.
#
# The pattern itself is ontology.DIFESA_RE. It used to be a second copy here,
# byte for byte, and a third in VerifyTables.py -- three spellings of one
# printed string, which is the shape of bug that only shows up as a count that
# disagrees somewhere else. Ontology owns the code space; the matcher and the
# bare shape (DIFESA_CODE) sit beside each other there.
ANNEXO_RE = ontology.DIFESA_RE


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


# How much a detection is trusted when one table code turns up in two volumes
# of the same year and only one copy can be kept. The bookmark tree is
# human-authored, a run of header hits is the detector's own evidence, and a
# title repeat is the weakest of the three.
SOURCE_RANK = {"embedded": 3, "header": 2, "title": 1}


# ==========================================================================
# CLI
# ==========================================================================

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


# Estensione dei PDF per-table. reports_185_1990/ contiene i PDF di origine in
# minuscolo, ma i PDF per-tabella vengono scritti in maiuscolo: su Linux i due
# casi non si equivalgono e CatalogueTables.py cerca questo nome esatto.
PDF_EXT = ".PDF"


# Earliest reporting year in scope. Everything before this is ignored by --all.
#
# Not a quality judgement about the older volumes so much as a decision to stop
# carrying them: 2001-2010 are scanned images with no text layer, 2013 has no
# index, and 2012 has an index whose ministry rows carry no page numbers, so
# none of them can be placed by the method this pipeline now uses. Leaving them
# in the output would mean reporting "0 tables" as though it were a finding.
FIRST_YEAR = 2016



REPORTS_DIR = "reports_185_1990"


OUT_DIR = "Out"
