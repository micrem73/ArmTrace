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
