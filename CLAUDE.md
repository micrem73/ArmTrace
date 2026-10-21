# ArmTrace: import pipeline

ArmTrace makes Italian arms export data queryable for journalists, activists and local administrators. This file covers only the import pipeline: official reports in, a validated SQL database out. Do not build the query server, the LLM layer or any interface here.

## Source and goal

- Input: the official yearly reports on arms exports published under Law 185/1990 (PDF). Official sources only.
- Output: every data table in the reports as rows in a SQL database, each row traceable to where it came from.
- Layout changes from year to year. Extraction must survive that without code edits per edition.

## Commands

- Install: `pip install --user -r requirements.txt` (`python3 -m venv` fails on this host: no `ensurepip`)
- Download sources: `bash In/download_185.sh In In/manifest.tsv` (checks page counts against the manifest; exits 1 on any problem). Skill: `download-sources`
- **Run a year:** `python3 run_year.py 2025` — step 1 (tables, trash), verifier, publish (`tablesindex`, `review.html`), final page check. Stops at the first failure; the page check must pass.
- Table list only (~10 s warm): `python3 Reports2PDFTables.py 2025 --dry-run --no-trash` → `Out/.work/2025/tables.tsv`
- Cut one table: `python3 Reports2PDFTables.py 2025 --only MEF/UE` (repeatable; key = `authority/[article/]code`)
- Check one table (subagent): `.claude/agents/table-checker.md`; renders with `python3 tools/review_pages.py 2025 <key>`, writes `Out/.work/2025/checks/<key>/verdict.json`
- After a code change or a round of checks: `python3 tools/recheck.py 2025` (what each table needs next: check, recheck, second-check, fix, human), then republish: `python3 tools/build_index.py 2025` (also deletes the PNGs of PASS tables; `--keep-png` to keep)
- **Done = `make check`** (ruff, then the full test suite; exits non-zero on any failure). Healthy output: `All checks passed!`, then `N passed in ~50s` with no `failed` or `error` (about 1 min warm). The first run after editing `lib/pageio.py` or upgrading pypdf rebuilds the page cache, which takes much longer.
- Fast loop: `make test-fast` (traps, recheck, table list); lint only: `make lint` (`ruff.toml`: correctness rules only)
- The golden tests need the sources: a test that says `sources for <year>: missing …` means download them, not a regression.
- A `code` FAIL is fixed test-first: add the failing assertion (trap test or golden row), then change the code, not the test. Regenerate `tests/golden/` (`python3 tests/snapshot.py <year>`) only for tables re-checked after the change.
- Checker eval: `make eval` (`tools/eval_checker.py 2025`: 26 cases, about $4 and 3 min). It runs the table-checker on known-good and deliberately broken cuts (`tests/evals/checker-2025.tsv`). Run it after changing `.claude/agents/table-checker.md`, the `check-year` skill or a convention, after a model change, and before a new year. A wrong cut that gets PASS is a failure. Add a case for every miss a person finds.
- Hooks (`.claude/hooks/guard.py`): Edit/Write is blocked on `In/<year>/` and on `Out/` outside `Out/.work/`. Each edited `.py` file is linted.
- Debug into a scratch tree with `--out <dir>`, so the last run's `Out/` is not overwritten.
- SQL extraction (Phase 3, pilot): `python3 CatalogueTables.py --year 2025` (table shapes, read in the sources → `Out/.work/CATALOG/`), `python3 tools/catalog_diff.py 2024 2025`, then `python3 ExtractTables.py 2025 [--only KEY]` → `Out/2025/armtrace-2025.sqlite` + `.sql` (exits 1 on a FAIL check or a refused table). Agreed columns: `lib/data/columns.tsv`; printed summaries: `lib/data/totals.tsv`. Review page: `python3 tools/extract_review.py 2025` → `Out/2025/extract-review.html`. Per table (import-year loop): `python3 tools/import_queue.py 2025 [--extract] [--render KEY]` → one folder per table in `Out/.work/2025/import/`, actions in `import-queue.tsv`; `ExtractTables.py 2025 --only KEY --out DIR` extracts one table alone, `--merge DIR...` assembles the year (same dump as a full run). Storage: facts only, printed figures in `printed_figure`, the printed table rebuilt by `lib/views.py` (`from_db`, `render`; review page shows it); summaries checked against their detail tables at `--merge` (`lib/data/views.tsv`). MAE A1 links each item to its amount (`importo`, type `link` in `columns.tsv`: `JOIN ON a.row_ord = i.importo`) and each row to its authorisation (`autorizzazione`, type `unit`: the dated line's `row_ord`, derived, not printed). The loop: skill `import-year`, one `table-importer` subagent per table (`.claude/agents/table-importer.md`); score it with `python3 tools/eval_importer.py 2025` (`tests/evals/importer-2025.tsv`; run after changing the agent, the skill or a convention).

## Output (`lib/layout.py`)

- `Out/<year>/PDF/…` tables · `Out/<year>/TRASH/<volume>_TRASH.PDF` unclaimed pages
- `Out/<year>/review.html` — the one page a person opens
- `Out/<year>/tablesindex.sqlite` (+ `tablesindex.tsv`, `tablesindex-trash.tsv`, same rows as text) — the handover to SQL extraction
- `Out/.work/` — cache, manifest, table list, trash runs, verifier report, checker verdicts. Regenerable; never read by people.

## Requirements

- Keep each original report file untouched in `In/<year>/`. Generated output goes in `Out/PDF/<authority>/[<lawcode>/]<TableCode><Year>V<Volume>P<PageNumber>.PDF` and is never hand-edited. `TableCode` is the printed code (`UE`, `F1`), not the table's title. `PageNumber` is the page number printed on the document at the table's first page, not the PDF page index (2025 F1: `MAE/F12025V1P1035.PDF`).
- Extract a list of existing tables of each year, looking into every volumes for that year. For each table, report if existing start-end page number, authority, table code. Tables can be listed in "indice", or in other summaries leading the tables. Some listed tables cannot exist. Tables can exist even if ot explicitly listed in tables, expecially in introduciton of volume 1. 
- Build the year's table list once (`Out/tables-<year>.tsv`). Using subagents, cut every table into a single PDF with the deterministic splitter, one table per subagent, and check it as soon as it is cut. Subagents report failures with page evidence; one agent fixes the splitting code, rebuilds the list and re-sends only the rows that changed. Do not run years in parallel.
- Using subagents, validate every extraction: the file name corresponds to the table code, authority, law code and printed start page it refers to. The page count of the PDF corresponds to the number of pages that the table says it has. The table code in captions, footer or header corresponds to the file name. 
- SQL extraction reads the original volumes in `In/` through `Out/<year>/tablesindex.sqlite`, never the split PDFs: those drop the tagged table structure, page labels, bookmarks and metadata of the source. The split PDFs are for review, and each carries its provenance (source file, SHA-256, pages) in its metadata.
- Every part of every source page ends up in exactly one place: a table file, a table's cut-out part, or the trash. Never in two, never in none. The page check enforces it at the end of every extraction.
- Escalation is by kind of failure, not by count (`tools/recheck.py`). The checker states the kind of every FAIL/UNSURE: a **convention** question (what counts as part of a table, where it is filed, where it ends) goes to the human at once; a **code** defect is fixed, and goes to the human if it still fails after 2 fixes; a **checker** doubt gets a second, independent checker, and goes to the human if the two disagree. Tables that had passed and change after a fix are re-checked and listed for the human before the commit. Items for the human are in `Out/<year>/HUMAN-REVIEW.md` and on `review.html`. Never drop or silently correct a value. Never invent one.
- Layout differences between editions live in a per-edition code. A new edition should share mostly of the codebase, plus specific code needed for that year. The engine is `lib/engine.py`; an edition is `editions/y<year>.py` (`VOLUME`, plus only the functions `engine.OVERRIDES` lists). A fix that holds for every year goes in the engine, and the golden tests of the verified years say whether it broke one.
- The run is reproducible: same input and configuration give the same PDF files.



## Order of work

1. One pilot edition first - 2025. 
2. Write the table list extractor for the pilot
3. Write the table extractor for the pilot
4. Add validation.
5. Stop and ask for a review: show the validation report next to the original pages.
7. Only then add further editions for subsequent years (decreasing, till 2016). 

## Where the work stands (2026-10-17)

Decisions and their reasons are in `DECISIONS.md`; measurements live beside the code; history is in git.

- 2025: verified clean, 156 tables (150 at tag `2025-clean`, plus the six MEF relation exhibits `MEF/REL/*`, 2026-10-18).
- 2024: verified clean, 159 tables, MEF relation exhibits included (2026-10-18).
- Glyph recovery (garbled MAE fonts, 2020–2025): built. SQL extraction reads MAE text through `lib/glyphs.py` (`page_text`, `decode_spans`). A new edition: run `tools/build_glyphmap.py` and commit the map; an outline it cannot name stays undecoded and goes to a person (`lib/data/glyph-decisions.tsv`).
- Open defects, each needs an engine change and a re-run of every verified year:
  - 2016 vol. II: the bare `TAB. M` series (278 pages) is not detected.
  - Volumes with no legible stamp (2019 vol. II, 2023 vol. III, family 3) get no trailing-page check; they need another witness.
  - 2016 and 2018 indices: ministry rows lack page numbers, so the index is rejected.
  - `MEF/II` key collision in 2020 vol. II: in 2024 it was a false Dogane `TAB.` match (`MALTAB.M.A.`), fixed; 2020 not yet re-run.
  - 2021: `TOMO_II.pdf` and `VOLUME_II.pdf` both claim volume II (844 and 1070 pp).
- SQL extraction (Phase 3 of `plan.md`) started on 2025 only (2026-10-19): catalogue 2024+2025 from the sources, one SQL table per printed table code. Pilot extracted (MAE/A1, MAE/A2, MAE/T2, MEF/EE, MEF/II, DIFESA/4, DOG/A1C2/M, DOG/A1C2/M1): every printed total and subtotal matches, 11 within 2 cents of rounding; A2 agrees with A1 operator by operator. Review stop B passed, committed. To do, in order:
  1. Two-level headers (a label over several columns): T10, T11, T13, T20, T23, Difesa 3B.
  2. MAE summary tables printed without a ruled grid: B4, D, G1, G2, I, F1.
  3. The rest of 2025's 156 tables: agree each table's columns in `columns.tsv` (and its printed summaries in `totals.tsv`), extract, fix by kind of failure.
  4. Store the A2 = A1 per-operator comparison as a check (cross-table witness).
  5. Review stop C: totals report for the year, a sample of tables against their source pages.
- Unsupported, to be listed rather than split: 2001–2010 (no text layer), 2014 vol. I and 2015 (one table found).

## Ask, do not decide
- Language, tooling and database engine, if not already chosen.

If you must proceed, pick the simplest reversible option and record it with the reason in `DECISIONS.md`.

## Conventions

- Keep dependencies few. Justify each new one in the commit message.
- Work in small steps and stop for review at the end of each numbered step above that asks for it.
- Keep it simple! Minimize produced files, do not duplicate informations.

## Things Claude gets wrong

Add a line here each time a mistake happens twice.
