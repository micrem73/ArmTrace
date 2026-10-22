# Decisions

One line of decision, one line of reason. Newest last.

## 2026-10-16 — switch to Claude, CLAUDE.md is the contract

- **Language: Python 3.10; PDF libraries pypdf (split, verify) and pymupdf (catalog, page rendering).**
  Already in use; changing them would invalidate the 2025 verification for no gain.
- **Database: SQLite** (`Out/CATALOG/catalog.sqlite` already exists), with a plain `.sql` dump as the deliverable.
  Zero-server, one file, reproducible; the dump keeps the result engine-neutral.
- **Sources live in `In/<year>/`, downloaded fresh from the URLs in the manifest, never edited.**
  The old `reports_185_1990/2024/` copies had been re-saved by macOS Preview (2026-01-28,
  "Quartz PDFContext, AppendMode"), so they were no longer the official files.
- **One shared engine plus a small per-edition module, not one full script per year.**
  The per-year forks (1.7k–2.3k lines each) existed to stop a shared change from breaking a
  verified year; a golden regression test per verified year does that without the copies.
- **Output path: `Out/PDF/<authority>/[<lawcode>/]<TableCode><Year>V<StartVolume>P<StartPrintedPage>.PDF`.**
  The start volume and printed page make a table findable in the source without opening the manifest.
- **Validation by Claude Code subagents reading rendered page images; no LLM call inside the pipeline.**
  Images can be read on pages whose text layer is garbled (~498 pages in 2025), which text checks cannot.
  Python stays deterministic and reproducible.
- **AGENTS.md is retired; its traps live as comments beside the code they protect, and as tests.**
  Each trap is a bug that shipped; a test keeps it fixed, prose does not. A separate trap list drifts.
- **OCR is out of scope.** Pages with no usable text are the ceiling, not a defect.
  Narrowed 2026-10-17: glyph recovery (below) is not OCR and is in scope.
- **Table list is built once per year; subagents cut and check one table each; one agent fixes the code.**
  Boundaries depend on the whole volume (listings, joins, reprints), so a table cannot be cut in
  isolation; parallel code edits would collide. After a fix the list is rebuilt from cached page text
  and diffed, and only changed rows are re-cut and re-checked. 5 failed cycles → `HUMAN-REVIEW.md`.
  Years are not run in parallel.

## 2026-10-16 — filename carries code and printed page

- **The filename's `P` number is the page printed on the document, not the PDF index.**
  It is the number a reader finds in the paper report. Computed as PDF index + the volume's folio
  offset (`indice.folio_map`, the mode of folio − index; 2025: vol. I −4, vol. II +1038), stored as
  `folio_offset` in the manifest. A volume with no legible folio falls back to the PDF index, and
  the table list notes it.
- **The filename carries the table code, never its title.** Titles are not needed in the name or the list.

## 2026-10-16 — pilot of the check loop

- **A table's own cover sheet is not part of its file.** The Dogane open each table with a page
  naming it ("Riesportazione (RE) / TAB. M - APPENDICE") and no data; the file holds only the pages
  the table numbers, so its page count equals its `Pag. N di X` stamp. The cover goes to TRASH.
  Detected by `pageio.is_table_cover()` after detection (`drop_covers`), so `appendix_code()` still
  reads it. **Exception: the cover stays when it is the only page naming the table** — the Dogane
  summaries ("Riepilogo per operatore/paese", e.g. `M1`, `O2APPENDICE`) print no caption on their
  data pages, and a file must be identifiable from its own pages. 2025: 48 covers, 30 dropped, 18 kept.
- **Checker results distinguish `absent` from `unreadable`.** MEF, Difesa annexes and relation
  exhibits print no page stamp, and Dogane pages no ministry heading; a value the document does not
  print is not doubt about the cut. Only `unreadable` (printed but illegible) makes a verdict UNSURE.

## 2026-10-16 — full 2025 check, review round 1

- **A Difesa annex is filed under the article its own pages cite** (`pageio.cited_article`, step
  `difesa_articles`). 2025: Annesso 2 → `DIFESA/A5/2` (Registro Nazionale delle Imprese, art. 5),
  3A–3C → `A2C6`, Annesso 4 cites none → `DIFESA/4`, no article level rather than a guessed one.
  The old rule filed every annex under `A2C6`.
- **An exhibit cut off a shared page holds the exhibit only.** Its band starts 2 pt above its own
  heading (the 20 pt margin took the last row of 8.6 into 8.7), and a ruled exhibit ends at its last
  rule (Tabella 12 carried the paragraph introducing Tabella 13). Prose between exhibits belongs to
  neither.
- **Vol. I printed p. 1034 (the one-page "VALORE" summary before F1) stays untabled**, by review.

## 2026-10-17 — what the split PDFs keep, and where SQL comes from

- **SQL extraction reads the source volumes in `In/`, through `Out/tables-<year>.tsv`; the split PDFs
  are a review view only.** A split keeps each page's content and its embedded fonts, but drops what
  lives at document level: the tagged structure (2025 vol. I: 36 `Table` elements, 755 `TR`, 4712
  `TD`, the publisher's own cell grid), page labels, bookmarks and document metadata. The exhibit
  crops drop marked content too. Reading the sources loses none of it.
- **Printed page numbers come from the PDF's /PageLabels first**, the folio-offset guess second, the
  PDF index last. For 2025 the labels agree with the folio offset on all 150 tables.
  *Amended 2026-10-17:* a volume's labels are dropped when most of its numeric labels contradict
  the folio offset. 2024 vol. II restarts its labels at 1 while the paper continues vol. I's
  numbering (PDF p55: label 51, printed 1061), which named all 89 vol. II tables 1010 pages low.
- **A full run removes table PDFs it did not write** (`--only` keeps the rest), so `Out/<year>/PDF`
  does not depend on earlier runs: the 2024 rename above left 91 stale files beside the new ones.
- **The verifier matches a file to its index row and manifest span on authority and article, not
  the code alone**: 2024 MAE and Dogane both print N2, O1, P2. `identity.title` reads garbled MAE
  pages through `lib/glyphs`, compares words on 6 letters (2024 MAE prints "ESPORTAZIONI
  DEFINITIVE" for "Esportazione definitiva"), and when a page prints no title only the stamp
  `Pagina 1 di <pages> TAB <code>` is accepted, as SKIP, never PASS.
- **Every split PDF carries its provenance** in its document info: /Title = table key, /Subject =
  JSON with each source file, its SHA-256, PDF and printed page range, and the bookmark titles
  pointing into the table. No dates, so output stays byte-reproducible.
- **A note printed under a table's grid belongs to the table** (opens with `(*)`, `*`, `(1)`,
  `Fonte`, `Nota`, `N.B.`, within 14 pt). Prose after a table still does not.
- **Unattributed pages go into one trash PDF per volume**, `Out/TRASH/<year>/<volume>_TRASH.PDF`, with a
  bookmark per run and provenance in its metadata; `<volume>.tsv` maps each run (source pages,
  reason) to its pages in that file. 2025: vol. I 43 pages / 15 runs, vol. II 104 pages / 34 runs.
- **Every part of every source page lives in exactly one output**: a table file, a table's cut-out
  part, or the trash. The leftovers of cut exhibit pages (prose, charts, running header) go to the
  volume's trash file as `cut-leftover` rows. `tools/page_check.py <year>` proves it from what is on
  disk (provenance + trash index) and runs as the final step of every full extraction; a failure
  fails the run. 2025: 2096 pages, 29 of them split into cut parts.

## 2026-10-17 — output layout

- **`Out/<year>/` holds what people and the extractor use; `Out/.work/` everything else** (`lib/layout.py`).
  Per year: the table PDFs, one trash PDF per volume, `review.html` (the one page to open), and
  `tablesindex.sqlite` with `tablesindex.tsv` / `tablesindex-trash.tsv` (same rows as text, for grep,
  diff and tests). `tablesindex` replaces the table list, the checker summary, the trash TSVs and
  the verifier's HTML as things to read; its `segments` table is what the SQL extractor queries to
  find a table in the source volumes. `tagged_pages` counts source pages with a tagged Table.
- **`run_year.py <year>`** runs step 1, the verifier, the publish step and the page check, stopping
  at the first failure. The page check reads only what is published.
- The page cache keeps one entry per volume; superseded entries are deleted.

## 2026-10-17 — escalation by kind, not by count

- **Replaces "5 review cycles, then a person".** In 2025 every failure resolved in 1–2 rounds, and most
  were convention questions that only a decision settles; extra cycles would have meant either the
  agent deciding them or wasted rounds, and a high cycle count invites fitting the code to the
  checker (the T2 footnote loss was such a side effect). Now the checker gives each FAIL/UNSURE a
  kind: `convention` → the human at once; `code` → fixed, the human after 2 failed fixes; `checker`
  → a second independent checker, the human if they disagree. `tools/recheck.py` keeps per-table
  history, re-queues tables whose content changed, lists tables that changed after a PASS, and writes
  `Out/<year>/HUMAN-REVIEW.md`.

## 2026-10-17 — one "done" command, hooks behind the hard rules

- **`make check` is "done": ruff (correctness rules only, `ruff.toml`), then the full test suite.**
  One command that exits non-zero gives an agent the same verdict a person gets. Ruff is a single
  dev-only binary; style rules stay off so the lint never asks for churn.
- **The golden tests fail with "sources missing" when `In/<year>/` is absent or the wrong size.**
  Sources are not in git, so a fresh worktree has none; a bare subprocess error read like a regression.
- **`In/<year>/` and `Out/` (outside `Out/.work/`) are protected by a PreToolUse hook, not only by prose.**
  A rule that must hold without exception needs a block, not just a reminder. Bash writes are not
  covered: the scripts that own those trees use Bash.

## 2026-10-17 — one engine, one small file per edition

- **`lib/engine.py` is the step-1 engine for every year; `editions/y<year>.py` holds what one edition
  differs in (`VOLUME`, and its own version of a function only if `engine.OVERRIDES` lists it).**
  The per-year copies made each fix local to its year: the cover and Difesa-article rules of 2025
  review round 1 never reached 2024. The template and the 2024 copy are deleted; 2024 restarts as an
  edition file. One entry point: `python3 Reports2PDFTables.py <year>`. A trap test fails when an
  edition redefines an engine function that `OVERRIDES` does not list.
- **The table checker has its own eval: known-good and deliberately broken cuts, scored by `make eval`.**
  Its verdicts decide what each table needs next, and nothing tested them: a prompt edit or a new
  model could pass a short cut unseen. 2025 kept no FAIL verdicts, so the broken cases are made by
  mutating good cuts (a page dropped or added, wrong code, printed page, article, authority) in a
  throwaway copy of the repo, where step 1 is a stub so the checker judges the cut it is given.
  Costs tokens, so it is run on change, not in `make check`.

## 2026-10-17 — garbled pages: recover the glyphs, never guess

- **The garbled pages are a per-font substitution, not damage.** Their fonts carry no /Encoding
  and no /ToUnicode; codes run 1..N in the order of the original codes, glyphs are named `uniE000…`, so text
  extraction returns Private Use Area code points. The outlines are intact and the substitution is
  constant within a font object. Same signature in 2020, 2023, 2024 and 2025 vol. I and 2025
  vol. II, under four different final producers, so it comes from the MAE annex as supplied, not
  from the volume's assembly. 2025: 17 MAE tables wholly, 3 partly unreadable (497 pp); 2024
  vol. I: 335 pp, vol. II none.
- **Recover the text by identifying each glyph outline, not by reading the page image.**
- **Rule: no guess.** A glyph is decoded only when its identity is certain: its outline matches
  exactly one character of the reference font, and nothing else in the reference font has that
  outline. Anything else (`l`/`I`/`1`, `O`/`0`, a shape matching nothing) stays undecoded and is
  carried as such into SQL, flagged; no context, dictionary or statistics may resolve it. A wrong
  character is worse than a missing one. The glyph map is a versioned data file, so a run is
  reproducible and every decoded character traces to an outline and a match.
- **An empty `uniE000` is the space (decided 2026-10-17).** Shape cannot name a glyph with no
  outline. In 220 of the 288 garbled fonts `uniE000` is empty, has that face's space width (Times
  250, Arial 278, Courier 600, MS Sans Serif 278: 220 of 220) and the lowest code in the font's
  order (Mac Roman, which all 288 follow). The other 68 subsets have no space and an outline at
  `uniE000`, decoded by shape like any glyph. The one decode by width and order, not by shape.
- **Human review of the glyphs shape cannot settle (2026-10-17, page "Garbled Glyph Proofs").**
  Lookalikes: an outline Windows shares between a Latin letter and a Greek/Cyrillic/Armenian twin
  is the Latin letter, because these fonts' one-byte Mac Roman code space holds only that one;
  confirmed by a person on all 12 outlines (1,219 occurrences). Open glyphs named by a person
  from the rendered glyph and its line: 2023 `b`, `_`, `€`, `p` (×2); 2025 `b`, `m`. The 2025
  Times `uniE000` is the Windows `.notdef` box, identical point for point: no character, stored
  flagged, never mapped to one. The 2025 Times `uniE001` (empty, width 250, first after the box)
  is the space, accepted by the user on the space rule's evidence.
- **Built (2026-10-17).** `tools/build_glyphmap.py` writes `lib/data/glyphmap.tsv` (348
  distinct outlines: 319 by the archive, 315 of them confirmed by Windows, 10 by Windows
  alone, 19 by a person); `lib/glyphs.py` decodes with it. 2020, 2023–2025: 1,658,207
  garbled characters, all decoded but the three 2025 `.notdef` boxes; the 1,400 hand-classified
  occurrences agree. Two cases found while building: a font repaired by a later tool (2023
  vol. I, callas) whose CMap names every glyph but sends one hyphen to U+E000, decoded
  only where the CMap sends the code to the glyph's own name; and a Wingdings font
  (2020), whose PUA code points are its real symbols, left as printed. The Windows files
  are inputs to the builder only and are not committed.
- **Encoding errors printed in the source are kept as printed.** Six sequences (45 occurrences,
  2023–2025, MAE tables): `Â°`, and `Â€™ Â€“ Â„¢ Â€Œ Â€` from UTF-8 read as cp1252 and then
  upper-cased. A person confirmed each matches the page. The suggested reading is a hint only.

## 2026-10-17 — what was kept from ROADMAP.md and READING-TABLES.md (both retired)

Measurements and thresholds stay beside the code that uses them (`lib/geometry.py`,
`lib/grammar.py`, `CatalogueTables.py`); history is in git. What remains are decisions.

- **Order after step 1: catalog, then diff, then schema, then extractor with verification;
  the tool bake-off last.** A schema written before a second year is catalogued encodes one
  year's layout as the ontology; choosing a reader before ground truth exists picks it for its
  documentation.
- **Step 2 measures shapes before it extracts (`CatalogueTables.py`); tabula-based CSV export
  is deleted.** It could not rotate (48 tables of 2025 print rotated content), could not see
  one-point rules, and had no record grammar (MAE: group header, wrapped record, subtotal).
- **Rotation is per content block, never per page; never inferred from box aspect ratios.**
  One page carries a rotated table and an upright running banner.
- **Printed edges beat printed labels; the body is the witness when the drawing is
  incomplete.** Labels are left-aligned and values right-aligned, so labels misplace values.
- **A stacked header is one header; a column name is a path** (`Anno 2023 / Valore in €`),
  or two years' values land in one column.
- **Printed values are parsed to `Decimal`, Italian convention, or `None`; never `float()`.**
  `1.234,56` → 1234.56; `—` is NULL, never zero; `016` is a Military List category, an
  identity, not 16; `1,234.56` is refused. Dash versus zero is a column-level policy.
- **`unknown` is a real grammar, preferred to a fit**, recorded with a reason.
- **The tables print their own totals; they are the extraction's oracle.** Per additive
  column, the sum of the rows against the printed total, plus row counts against the index
  page counts and text coverage against the source page; FAIL at zero.
- **Schema (decided, not built): one lossless EAV `stg_cell`** (authority, article, code,
  year, page, row_ord, col_ord, row_kind, col_key, raw_text, num_value, text_value, unit,
  is_total) plus curated projections from a lineage map. `DECIMAL`, units in `column_def`,
  `snake_case` names with the printed Italian in `column_def.label_it`; deterministic output:
  fixed order, explicit columns, 2-dp decimals, LF, UTF-8 without BOM, `BEGIN`/`COMMIT` per
  file, `--replace` opt-in for `DROP`.
- **No coverage claim without the extractable fraction beside the total.**
- **Glyph recovery, as measured (2026-10-17); built in `lib/glyphs.py`.** The garbled fonts are
  Windows 10/11 TrueType (versions 6.90–7.01; Times New Roman regular and bold, Arial Bold,
  Courier New Bold, Microsoft Sans Serif) converted to Type 1 by a fixed formula: each
  quadratic segment P0–Q–P2 becomes the cubic c1 = Q, c2 = P2 + (Q − P2)/3, at 1000/2048
  scale, glyphs numbered in Mac Roman order. Every decoded glyph with a non-negative left
  side bearing is identical to Windows (10,989 of 10,989); glyphs reaching left of the origin
  are shifted in x by the converter (+1 or +4 units, Times `p` non-uniformly). Archive copies
  decode 99.3% of the 11,535 distinct garbled glyphs; Windows files and human review settle
  the rest (above). PyMuPDF truncates span font names to 24 characters: resolve fonts per
  page, not by name.

## 2026-10-18 — 2024 check round: two stamp shapes, by review

- **A clipped stamp total is read from the page numbers.** 2024 MEF prints `N / 13` on its
  132- and 135-page tables (EE, GG): the total is cut to two digits. `page_count` passes when
  the numbers run without a gap to the file's last page and that page closes the table
  (`Totale Complessivo`). The two checkers had disagreed on EE; the human decided.
- **A totals-only overflow page belongs to its table.** 2024 MEF HH prints `17 / 17` on p1443
  and again on p1444, which holds only the `Totale Complessivo` row: the file keeps it and has
  one page more than the stamp. Dropping it would lose the grand total.
- **The Dogane `TAB.` marker needs a word boundary.** "MALTA" beside "B.M.A." extracts as
  `MALTAB.M.A.` (2024 vol. II p465), which filed four MEF pages under DOG art. 1 comma 2 and
  split Tabella II: 1 page kept, 136 trashed.
- **The MEF relation's own exhibits are tables, filed `MEF/REL/<code>`.** The relation prints
  six exhibits before the annex (2024 vol. II p78-82, 2025 p263-267): the uncoded "Riepilogo
  saldi esportazioni ed importazioni" (code `SALDI`, the one code the document does not
  print), UE, LGP, IAA, FG, KK1. They were discarded as a "second print" and trashed, but only
  LGP and IAA copy their annex tables; FG, KK1, UE and two SALDI rows disagree with them (2024
  FG: €4,380,567,163.68 in the relation, €4,315,708,982.69 in the annex). Both are kept, never
  reconciled. `REL` sits in the lawcode slot so MEF/REL/UE and MEF/UE stay two tables. By
  review: extract all six; filing and the SALDI code were the reversible default, not asked.
- **Same-name notes go into `tablesindex.sqlite` (`tables.notes`), on both sides.** Each REL
  exhibit says "copy of MEF/X" (every amount found in the annex table, at least half of its
  amounts), "summary of MEF/X, matches" (a few amounts, all found there), or "differs from
  MEF/X" with both grand totals; each SALDI row is compared with its table's grand total.
  Grand totals are read by position on the cut files (pypdf scrambles the total row; rotated
  tables keep a row on one x), and two totals agree when one row holds the other (the
  relation prints only the grand total where the annex prints one per column). 2025: all six
  agree; 2024: FG, KK1, UE and SALDI rows AA and CC differ.
- **No table in the trash.** The page check reads every trash page and fails on one that
  prints a table marker (TAB. code, MEF caption, "Tabella N", "Pagina N di M") AND table
  content (3+ euro amounts or more lines than a cover). Dogane covers pass: marker, no content.
- **A totals row printed just under a grid belongs to the table** (`Totali`, within 24 pt of
  the last rule; 2024 p78).

## 2026-10-19 — 2023: three volumes, scanned Difesa annexes, bare exhibit titles

- **An edition may read every volume of its year** (`VOLUME = "all"`). 2023 is three
  volumes (MAE/Difesa/Interno, MEF, Dogane) whose folios run on; `both` reads only I and II.
- **Scanned pages are declared, not detected.** 2023 vol. I prints the Difesa section
  (p879-944) and the Interno page (p945) as images with no text layer. By review: the
  annexes are cut from a page map in `editions/y2023.py` (`declared_tables`, an engine
  override), read off the scans and filed as in 2025: Annesso 2 → `DIFESA/A5/2`, 3A-3C →
  `A2C6` (the scans print "art. 2 comma 6"), Annesso 4 → `DIFESA/4`. Their `source` is
  `declared`. A declared range overlapping a detected table stops the run. SQL extraction
  will need OCR for them. The Interno page is not a table (trash), as in 2025.
- **The Dogane cut-keyword cover is read by the engine** (`pageio.cover_code`, moved from
  `editions/y2024.py`): "Riepilogo per operatore TAB." / "M1" names M1-P1, whose data pages
  print no code. The same cover shape now classifies as Dogane (`ontology.DOG_CODE_RE`), so
  its article is kept (2023 vol. III p389 was filed `DOG/MAPPENDICE`).
- **Dogane pages do not vote on the code family.** A volume with no index is sampled for
  code shapes; the Dogane's M1-P2 made 2023 vol. III (Dogane only) family 2, whose style 3
  drops bare M, MG and MT. Dogane pages are now skipped in that sample.
- **A bare relation-exhibit title names its exhibit.** 2023 prints four MEF relation
  exhibits without `Tabella <code> -`; the code is the one 2024 and 2025 print before the
  same title (UE, LGP, IAA, FG). Annex pages, which repeat two titles, carry the running
  header "ELENCO TABELLE" and read no title. Reversible default, like `SALDI`.
- **The verifier reads what step 1 reads.** A page whose only text is the act's running
  header and folio is a scan (`pageio.no_text_layer`) and counts as unreadable, like a
  garbled one: the code check is a warning there, not a failure. The cut-keyword cover
  names its code to the verifier too, and an exhibit named by a title passes on its title,
  as SALDI already did.
- **The relation cutter knows the bare titles** (`emit.exhibit_headings`): before this the
  four 2023 exhibits were written as files of 0 pages.
- **Check round 1 (2023): two code defects, fixed test-first.** "SEGUE Tabella 8.8" heads
  a continuation page (vol. I p41): `REL_HEADING` reads it, and T88 keeps its total row.
  pypdf splits the act's header into "XIX  LEGISLA TURA" on vol. III's covers; unfiltered,
  it hid 13 Dogane covers (A1C89/O, MG7-MG18) from `is_table_cover`, and they were kept.
  `RUNNING_HEADER` now reads `LEGISLA\s*TURA`. 2024 and 2025 snapshots unchanged.

## 2026-10-19 — SQL extraction starts on 2025, tables stored as printed

- **Phase 3 starts on 2025 before Phase 2 ends; 2024 is catalogued too, for the diff only.**
  Both years are verified, so their shapes are measured on settled cuts; the column keys come
  from two years, not one, and no 2024 rows are extracted yet. By the maintainer.
- **Rows are stored as printed: one SQL table per printed table code, shared across years,
  replacing the `stg_cell` EAV design.** Each row carries year, source file, printed page, row
  order and row kind (data, group, subtotal, total); each value column keeps its printed text
  beside the parsed value. Simpler and directly queryable; the cost is that a column renamed or
  added in another year needs a migration. By the maintainer.
- **The extractor and the catalogue read the source volumes through one reader (`lib/source.py`).**
  A cut-out table is found on its source page by the crop the splitter used, stored in
  `tablesindex.segments`, so the two never disagree on what belongs to a table.
- **A ruled table's columns come from its printed grid** (`geometry.grid_header`): each column
  is the gap between two walls, each label everything printed in it. The gaps between words
  merged DOG M1's three labels into one and missed T2's header; 2025: 31 more tables get a
  header, none lost. Unruled tables keep the label reader.
- **Tables whose columns are years are stored one row per printed row and year**
  (`voce, anno, valore`), not with a column per printed year. T2 prints 2021–2025 in 2025 and
  2020–2024 in 2024: a column named `2021` would mean a different position in each edition.
  By the maintainer, for the 2025 pilot; it applies to the relation exhibits that print a year
  window (T2–T23).
- **A column renamed between editions is one SQL column; the printed label is kept per year**
  in `column_def.label_it`. 2024 EE `Importo Segnalazioni` and 2025 `Importi Segnalati` are
  `importi_segnalati`. By the maintainer. The catalogue diff compares year labels relative to
  the edition (`Anno Y-1`).

## 2026-10-19 — reading the pilot tables (ExtractTables.py, lib/extract.py)

- **A page is a header, continuation, summary or cover page.** A continuation page's grid
  stands elsewhere (EE p2: 10.9 pt right) or with other widths (Difesa 4 p255): its columns
  are its own walls, in order, when they are as many as the header's. A page before the first
  header is the table's cover and may hold no amount (DOG M1 p858). A page with neither is
  the table's printed summary (A1 p606), kept as `printed_total`, the extraction's oracle.
- **One table, one reading frame.** A page holding only a header and one operator name has
  fewer rotated characters than its upright running head (A1 p147); read in its own dominant
  direction the table came out letter by letter.
- **Records: a box between two rules is one record only where the column walls run through
  it and at most one of its lines carries a value** (T2's wrapped labels, Difesa's lists,
  2024 EE's wrapped `Causale`). Elsewhere each line is a record; a line with no value heads
  a group when it is one run of text starting in the first column (A1 operator), else it
  continues the record above (A1's wrapped description; a page that opens on one continues
  the previous page's record).
- **A subtotal prints at least two amounts and nothing else.** A1 boxes EURO and Val. Fini
  Dog. under each operator, even at 0,00; an authorisation line printing only its customs
  value was read as a subtotal and cut its operator in two (24 lines in 2025).
- **The body ends where the grid's walls end, or, where they stop at the header (A1), at the
  last line that is not the page stamp.** Every other line under the table is a note (it
  opens with a note mark: `(*)`, `1   `) or an issue; a printed amount outside the body is
  always an issue. This check found 4,103 A1 lines lost to a mid-page rule taken for the
  table's end.
- **A sign before the currency is read (`-€ 142.942,72`).** EE's credit notes counted zero
  and the column summed 9.711.703,48 over its printed total.
- **A value printed inside the height of the line above, in columns that line leaves empty,
  belongs to that line.** A1 sets some customs (and EURO) values 3-4 pt low (p181 GEM
  ELETTRONICA `105.500,00`); read as lines of their own, ~25 authorisations looked
  customs-free and entered the EURO subtotals. This, not a convention, was the 9 A1 EURO
  failures. Found by reviewing the smallest failing group with the maintainer.
- **How MAE A1 builds its EURO totals (rule of the check, confirmed by the data).** A line with
  a customs value does not count; a blank EURO on an EUR line counts as its Ammontare. With
  that, every EURO and Val. Fini Dog. subtotal matches (10 within 2 cents of conversion
  rounding), and so do the EUR, USD and CHF totals and the 2.576 authorisations. The rule is
  only the check's; no value is changed.
- **A1's grand EURO total is checked against the printed subtotals** (maintainer, 2026-10-19).
  `7.721.114.868,99` equals the printed subtotals added up exactly; the rows give 0,05 more,
  the sum of the subtotals' cent roundings. Each subtotal is checked against its rows, so
  the chain from rows to total is still whole (`totals.tsv`: `sum euro over subtotal`).
- **Pilot widened to MAE A2, MEF II, DOG M** (maintainer, 2026-10-19). What they taught:
  a header may name a total (`Totale`, `Incidenza % su totale`): only a value refuses a
  header row; `<group> Totale` with an amount is a subtotal (MEF II); a continuation page
  may draw its inner walls in short pieces (MEF II p427). A2 agrees with A1 for each of its
  159 operators (authorisations, EURO, customs value): a cross-table witness for later.
- **A year is imported by an agent loop, as step 1 checks its cuts** (maintainer, 2026-10-19).
  `import-year` skill: one `table-importer` subagent per table drafts its columns or judges
  its rows against the rendered pages; it never edits code or the agreed columns. The main
  agent alone fixes code, test-first; escalation reuses `tools/recheck.py`'s rules. Each
  table is extracted alone (`ExtractTables.py --out`) and the year assembled (`--merge`),
  byte-identical to a single run. Why an agent at all: half the tables print no total, and
  the first two trial runs found what the checks could not (Difesa 4's stray spaces, MAE B4's
  doubled header letters). Scored by `tools/eval_importer.py` on broken copies of the pilot.
- **Two words touching on one line are one word.** The source sets some words in two spans
  (Difesa 4 `THALES IT` + `ALIA.`, `MBDA ITALIA` + `.`); joined with a space, 9 cells read
  `IT ALIA`, `S.R.L . .`. A word that ends in its own printed space (T2 `Paesi `) is not
  touching. T2 prints `UE-NATO`: the pilot test had asserted the spaces the old join invented.
- **Open, for the maintainer: a word broken across two lines without a hyphen.** Difesa 4
  prints `HYPERTAC/SMITHSINTE` / `RCONNECT` in a wrapped cell. Joining the lines with a space
  (now) splits the word; joining without one would merge every pair of real words at a line
  end. Kept as printed with a space until a convention is agreed.
- **The sum rule is stored with the data** (`column_def.sum_sql`, 2026-10-19). The interface
  worktree summed A1's `euro` as printed and got 385 M against a printed 7,72 bn: the rule
  lived only in `columns.tsv` and the checks. Each summed column now carries the SQL a data
  row adds (`CASE WHEN val_fini_dog_raw IS NOT NULL THEN 0 ELSE COALESCE(euro, CASE WHEN
  valuta = 'EUR' THEN ammontare END, 0) END`), generated from the same `columns.tsv` line the
  checks read, and a test runs it in SQLite against the checks' sum.
- **The importer eval found two defects the checks could not** (first run: 10/10 broken
  tables caught, 2 of 8 correct ones failed, both rightly). A value-shaped last line of a
  wrapped description (`CAL.` / `5,56`, `(CLIN` / `5)`) was a row of its own: 45 in A1, 13 in
  DOG M; only a value in a non-text column makes a line a record. A1's summary page cut a
  three-line label to its first line, dropped `Totali parziali per tipo valuta:` and filed the
  percentages under the amounts' labels: a label runs until its `:`, a line with no value
  after a closed label heads the lines under it, a value before `%` is `<label> (%)`.
- **A text printed twice a hair apart is printed once** (MAE B4 fakes bold so: `AArreeaa`,
  or `Area Geografica` twice, by whether the 0.2 pt shift splits the run). A repeated glyph is
  the same glyph only within half its own width: two real full stops (`S.p.A..`) stand a whole
  width apart. Both forms are
  dropped in `geometry.lines_of`, so the catalogue and the extractor read the same text.
- **MEF II's `Ditta` is carried down** (maintainer, 2026-10-20: "table II is a visualisation,
  not raw data"). The company is printed on its first line and in its `<Ditta> Totale`; every
  line between is that company's. New column type `carry` (`columns.tsv`): the name fills the
  lines under it up to the subtotal, which must name the same company, or an issue is recorded
  and nothing is overwritten. 2025: all 3.991 lines named, no issue. (The 2 issues first
  reported, `AVIO S.p.A.` vs `AVIO S.p.A.. Totale`, were mine: the overprinted-bold rule
  dropped the second of two full stops. The importer eval caught it; the rule now compares a
  repeated glyph with its own width, and M too reads `AVIO S.P.A..` as printed.)
- **Tables are stored as facts; the printed table is rebuilt from them** (maintainer,
  2026-10-20: "tables in reports are often visualisations of raw data; a total is generated,
  not a row"). Each SQL table holds its data rows only; a group printed once is a column on
  every row (`group`, `carry`). Every figure the table prints that is not a fact (subtotal,
  total, summary line) is in `printed_figure`, at the place printed; `view_def` says how the
  table is laid out. `lib/views.py` rebuilds the printed table from the database alone
  (`from_db`, `render`): headings, carried names on their first row, subtotals and totals
  showing the **printed** figure, flagged with the facts' sum where they differ (maintainer's
  choice: the official figure is the printed one). The rebuild is the import's check: for the
  8 pilot tables it reproduces the print row for row (29.000 rows, no difference), and a
  plain `SUM` is right (MEF II `totale` = its `Totale Complessivo` to the cent). Why not
  store the printed rows: a user summing a column counted subtotals and totals too, and the
  official rounding (A1's total is the sum of rounded subtotals) cannot be regenerated.
- **A summary table agrees with the table it summarises** (`lib/data/views.tsv`, checked when
  the year is assembled). MAE A2 = A1 for all 159 operators (authorisations; EURO and customs
  value equal to A1's *printed* subtotals, rounding included). DOG M1 = M in euro for all 200
  M1 lines (190 names). A first comparison of mine said M1 disagreed: I had merged names M1
  keeps apart, and misread `AVIO S.P.A..` (above).
  What does not rebuild M1 from M: M1 splits some names over two lines (`CPM ELETTRONICA SRL`
  twice) by a key M does not print, counts operations (17.806) where M prints lines (6.245),
  and adds `Utilizzo anni precedenti` once per authorisation while M repeats it on each line.
  So both are stored, and their agreement is a check, not a rebuild.
- **MAE A1: which amount goes with which item** (maintainer, 2026-10-21, on the A1 matching
  page: A.C.S.A. p66 and CERATIZIT p114). An authorisation (from a line printing a date, within
  an operator) prints amounts on the left and items on the right, not always side by side. As
  many amounts as items: the k-th goes with the k-th (CERATIZIT: four amounts stacked one line
  apart beside four items three lines tall). Fewer: "one amount covers every item in line and
  under it, until the next amount" (A.C.S.A.). A line printing an amount and no quantity is
  the item above, wrapped: its Materiale moves to that item, its Valuta stays with the amount
  (moving it too broke the EUR rule of the EURO sum). Stored as column `riga_importo` (type `link`,
  `columns.tsv`): on each item, the `row_ord` of the row whose amount covers it; every amount
  is still stored once, so sums are unchanged (every A1 check as before). 2025: 2.316
  authorisations with one amount, 82 with as many amounts as items, 171 with fewer; 164
  wrapped descriptions back on their items. The 7 the rule left open went back to the page
  (same day). One item with two amount boxes, the second on a line of its own (FABARM p142,
  LEONARDO p278 p302, MBDA p340 p346, RING MILL p391): "two rows, one per amount, keeping the
  same item description for both"; the second row gets the item's Unita' Misura, Materiale,
  Tipo and Cat, not its quantity (counted once), flagged `copied from row N`, and the
  rebuilt view leaves them off that line as printed. LEONARDO p260 dates an item whose amount
  boxes are empty: "a new row with an amount of 0", stored `ammontare` 0 with `_raw` NULL,
  flagged. 2025: every one of 7.946 items linked, no issue.
  Each row also names its authorisation (maintainer, same day, asked by the interface: rows
  filtered by operator gave no clue how they group): column `riga_autorizzazione` (type `unit`),
  the `row_ord` of the dated line that opens it. A1 prints no authorisation number, so it is
  an id derived from the print, valid within one year's table, not an official reference.
  2025: 2.576 authorisations; no amount spans two, none spans two operators.
  Both named `riga_*` (maintainer, same day): a row of this table, not a value of the report,
  and not to be joined with the authorisation code MEF prints (`numero_autorizzazione`).
- **Nested rows keep an id** (maintainer, 2026-10-21, general rule): when a table's rows nest
  (several lines under one printed unit), the import stores on every row the id of its unit,
  a `riga_*` column (`row_ord` of the line that opens it), so the grouping the print shows by
  layout is not lost when the table becomes rows. The unit and where it ends are agreed per
  table, like any column. First: MAE A1 (`riga_autorizzazione`, `riga_importo`).
  Nested means the layout shows the grouping (something printed once covering the lines under
  it); such a table always gets the id. A table that prints every line complete is flat, even
  when lines share an authorisation the print does not show (Dogane M): no id is invented.
- **Dogane M: flat, no authorisation id** (maintainer, 2026-10-21, on the M authorisations page:
  M is not a nested table).
  M prints operator, currency and the authorisation's amount on every line, no authorisation
  number, lines sorted by material: 891 of 2.895 operator+currency+amount groups are not on
  consecutive lines, and in 71 the year's progress plus Utilizzo exceeds the amount (two
  authorisations of the same amount: FIOCCHI 120,00, four lines adding up to 240,00). The
  maintainer: "Tab M does not easily allow to connect to the source authorisation ... Store
  no id." `Utilizzo anni precedenti` is "the amount already exported in previous year reports
  for the same authorisation (or authorisation sub-amount)": a figure of the authorisation,
  repeated on its lines, never added up across lines (it has no `sum` rule).

- **Companies: ArmTrace's own id** (maintainer, 2026-10-21/22; design in plan.md Phase 3
  step 6). `lib/data/companies.tsv` (`id`, `name`; DB table `company`) and
  `company-names.tsv` (each printed spelling, its id, `how`). A table naming a company gets
  `id_ditta` (type `company` in `columns.tsv`): MAE A1/A2, MEF II, Dogane M/M1. The printed
  name stays as printed; a name with no id is NULL with an issue. `tools/companies.py`
  matches only spelling by itself (`lib/companies.fold`: case, accents, punctuation, spacing,
  a legal form dotted or written out); a company with no candidate gets a new id; a cluster
  of candidates (another legal form, a cut name, all of one name's words in another, a near
  spelling) goes to the maintainer (`--same`, `--apart`). 2025: 340 printed names, 192
  companies, 25 clusters decided by the maintainer. Not the same company: TEKNE / TEKNEL,
  PRAVISANI S.P.A. / DOTT. MARIANO PRAVISANI & C., FINCANTIERI / FINCANTIERI NEXTECH,
  LEONARDO / LEONARDO CAE ADVANCED JET TRAINING; AVIO S.P.A. / GE AVIO S.R.L. and THALES
  ALENIA SPACE ITALIA / THALES ITALIA on Claude's knowledge of the companies (Avio's aero
  business sold to GE in 2013; Thales Alenia Space is the Thales-Leonardo space venture),
  the maintainer agreeing. The same company under another legal form (BERCELLA SPA /
  S.R.L., SECONDO MONA, UMBRIA AEROSPACE SYSTEMS) is one id.
  These decisions are open to a witness: when Difesa A5/2 (the national register of
  companies, with register numbers) is imported, any register number under two ids, or id
  under two register numbers, is reported to the maintainer (maintainer, 2026-10-22).
  Every decision, a merge or a split, stays open: when a later table shows otherwise it is
  reported, never changed by itself. So is a table grouped by company (MAE A1's operator,
  MEF II's `Ditta`) printing one id as two groups: 2025, MEF II prints BERCELLA S.R.L. /
  BERCELLA SPA and FINCANTIERI S.P.A. / FINCANTIERI CANTIERI NAVALI ITALIANI as separate
  groups (a bank may report a company under an old and a new name: a doubt, not a proof).
- **Difesa A5/2: the register of companies, by event** (maintainer, 2026-10-22). Each printed
  line is a row of its year, traceable to its page: `numero_iscrizione`, `denominazione`, one
  of `data_iscrizione` / `data_sospensione` / `data_cancellazione` (the list it is printed in:
  `event <WORD>` in columns.tsv; with no date printed, the edition's year, flagged),
  `motivazione`, `id_ditta`. No list column and no item number (maintainer: "if a company is
  in this table, it means in some year it was inscribed"). The register across years, one
  row per company, is the view `registro_imprese` (name, id and reason printed last), built
  in the year database and again on the site, where the years meet: so no row holds two
  years and no year's database is edited by another. Read by `lib/extract.read_list`: no
  grid, each value known by its shape and placed by position (the text layer of p1278 gives
  numbers after the last reason); the count in each list heading is checked, a gap in the
  numbering is an issue. 2025: 95 lines, 2024: 65 (one suspension), all counts PASS.
- **Summaries checked per company id** (2026-10-22): MAE A2 against A1 and Dogane M1 against
  M are grouped by `id_ditta` (`lib/data/views.tsv`), no longer by the name as printed with
  its cut-name and same-letters fallbacks (`views._match`, removed): a company's lines add up
  whatever their spelling. 2025: 477 + 157 PASS (667 per printed name before: M1's 190
  names are 157 companies).
- **The register number identifies a company first** (maintainer, 2026-10-22): a name
  printed beside a register number that already has an id takes it before any name
  matching (`how` = `register <number>`); name matching only covers what no number settles.

## 2026-10-19 — 2022: shifted fonts read back, export stamp as witness, Dogane outside the MEF vocabulary

- **Scanned Difesa annexes are declared, as in 2023** (`editions/y2022.py`): vol. I
  p999-1017, Annesso 2 → `DIFESA/A5/2`, 3A-3C → `A2C6`, 4 → `DIFESA/4`. Annesso 1 is the
  Gazzetta Ufficiale's Elenco Materiali, pasted in (p958-998), no table. The Interno
  section is a cover and a blank page (trash).
- **A shifted font is read back, not given up** (`pageio.decipher`). The pages
  `is_ciphered` flags on 2022 vol. I (p938-950: MAE N1-P2) are Identity-H fonts whose glyph
  index is the character code less 29 (`patterns.CIPHER_SHIFT`); shifted back they read
  exactly ("TAB N1", amounts, country names). The earlier note called them unrecoverable.
  Only shifted lines of a flagged page are touched. Other years flagged (2016-2018) are not
  yet checked for the same shift.
- **The MAE export stamp names its table** ("MAECI - UAMA - CENTRO INFORMATICO Pagina 1 di
  1 TAB M2", `patterns.STAMP_LINE`, style 9). A stamp is no prose mention, so MIN_RUN does
  not apply: 2022 B5 and M2 are one page each and had been dropped (2023 found them by
  bookmark; 2022 has none). A table's trailing page whose stamp names another report is not
  the table's (`pageio.trim_foreign_stamp`): E no longer takes the VALORE summary (p798),
  which stays untabled as in 2025.
- **On a Dogane page the vocabulary does not filter style 3.** 2022 vol. II's MEF ELENCO
  prints "Tabella AA" alone on a line, so `read_index` takes it for the volume's index, and
  its 12 codes rejected every Dogane "TAB. M". Dogane codes are their own space (as for the
  family vote, 2023). `read_index` itself is unchanged, because the 2012 index has the same
  shape.
- **A caption glued to the keyword still names the code**: "operatoreTAB. M1" (vol. II
  p927, p938, p941), in `INLINE_CODE` and `ontology.DOG_CODE_RE`. Only a lowercase letter
  then exact `TAB.`; the uppercase join of 2024 ("MALTAB.M.A.") still reads nothing.
- **FG's 2022 relation title** says "per istituti di credito" where 2023 says "per
  intermediari"; both name FG.
- **The printer's barcode leaf is blank** (`pageio.is_blank`): vol. II ends "PAGINA
  BIANCA", then a page with only "*190670036670*"; MT13 had kept both.
- 2023, 2024 and 2025 snapshots unchanged by all of the above.
- **Check round 1 (2022): one code defect, fixed test-first.** LGP's last rows and total
  open vol. II p9, above IAA, in a font whose euro sign pypdf reads as "Φ"; the
  continuation test looked for "€" only, so the exhibit stopped at p8. A head holding the
  open exhibit's grand total now continues it too (`pageio.relation_exhibits`). 2023-2025
  snapshots unchanged.
- **2025 table list: eight "singleton V2" notes gone, pages unchanged.** MAE M2, N2, O2 are
  one-page tables their stamp names; style 9 keeps them, so they are no longer a discarded
  one-page hit, and the note (which also leaked onto the Dogane M2/N2/O2 of the same bare
  code) is no longer written. `tests/golden/2025-tables.tsv` regenerated for the notes
  column only; the 2025 snapshot is byte-identical.

## 2026-10-19 — public explorer: static page, database in the browser

- **The explorer is one static page that runs SQLite in the browser (sql.js 1.10.3), no server.**
  Read-only by construction (`PRAGMA query_only` on the visitor's own copy), free to host, nothing
  to guard. By the maintainer: simple, effective, free.
- **Hosting: GitHub Pages; the repository is public.** Cloudflare Pages is the same thing if that
  changes.
- **The whole file is downloaded for now (pilot: 4.1 MB); switch the loader to HTTP range reads
  (`sql.js-httpvfs`, chunked) before it passes about 30 MB.** A full year is expected at 30–80 MB
  and GitHub refuses files over 100 MB. Only `start()` in `web/app.js` changes.
- **Italian only; no LLM in v1.** Curated questions plus an editable SQL editor show what the
  database allows without a model that can be wrong; the MCP/LLM layer keeps its own phase.
- **Rows link to the official PDF on camera.it (`#page=<pdf_page>`), never to a copy.** The
  manifest has the URL of every volume; hosting the PDFs would add a second, unofficial source.
- **Printed text is shown, parsed values only sort and compute.** A column with a `_raw` sibling
  shows the `_raw` text; a dash stays a dash.
- **13 curated questions on the pilot, each checked against the report.** Values read on their
  source pages (T2 p23, A2 p605, M1 p1901, II p1436); the per-country sums of MEF EE equal the
  92 country totals printed in Tabella GG, and the per-company sums of II its 157 printed
  subtotals: the country and company printed once per group are carried to the rows that follow.
- **MAE A1's EURO column is not additive as stored.** It is blank on EUR licences (the value is
  `ammontare`) and excluded where a customs value is printed: the extractor's rule
  (`columns.tsv`: `sum else ammontare@valuta=EUR unless val_fini_dog`) is not in the database, so
  `sum(euro)` gives €385 M of a printed €7.72 bn. The page warns on A1 and the questions write the
  rule in SQL; the database should carry it.
- **Published from a local build to the `gh-pages` branch, one commit with no history**
  (`tools/publish_site.sh`). CI cannot build it: the sources and `Out/` are not in git. The code
  history stays on the code branches; the published data is reproducible from them.

## 2026-10-21 — explorer: two views, per-column filters

- **Only Tabelle and SQL remain; the Domande and Metodo views are removed** (`web/domande.json`
  with them). By the maintainer. The checks made for the questions stay recorded above.
- **Every column header sorts and filters**, in the table list and in each table: text is
  searched as shown; on numbers `>1000`, `<=5`, `=0`, `10-20`, Italian or plain notation.
- **A table page shows its rows only: no printed totals, no notes.** By the maintainer.
- **A column printed under several labels (T2: one per year) is headed by its SQL name.**
- **Built on the regenerated 2025 database** (`sql` branch, 2026-10-20): data rows only, no
  `row_kind`; headings, subtotals and totals live in `printed_figure`, the layout in `view_def`.
- **"Come stampata": the table rebuilt as printed, in the browser.** A switch on the table page
  swaps the rows for the printed layout: group headings, a name carried on its group's first row
  only, subtotals, totals and summary lines from `printed_figure`, a band at each page linked to
  the PDF. `printedRows` in `web/app.js` ports `render` in the extractor's `lib/views.py`; the
  database stores nothing twice. Checked row for row against `render` on the 8 pilot tables
  (23,202 rows, identical). A change to `render` or to `view_def`'s keys changes `printedRows`
  too. By the maintainer: a switch, not a separate view.

## 2026-10-22 Explorer: files addressed by content, no stale publish

- **The build addresses the database, `app.js` and `style.css` by content** (`name?v=<12 hex of
  SHA-256>`, `build_site.stamp`). Pages lets browsers reuse a file for 10 minutes; after a
  publish, a browser kept the older database and showed A1 without `riga_autorizzazione`.
- **`publish_site.sh` refuses a `site/` not built from the current `web/`** (stamps taken out
  before comparing). A `site/` left from 19 October went out labelled with that day's HEAD.
- **The rows view hides the import's link columns** (`view_def.link.col`, `.unit_col`: A1
  `riga_importo`, `riga_autorizzazione`); they are not printed. CSV and SQL keep them. By the
  maintainer.
