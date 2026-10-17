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
- **The AGENTS.md traps become regression tests plus `docs/TRAPS.md`; AGENTS.md is retired.**
  Each trap is a bug that shipped; a test keeps it fixed, prose does not.
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
