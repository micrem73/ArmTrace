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
