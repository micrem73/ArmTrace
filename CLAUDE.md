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
- **Done = `make check`** (ruff, then the full test suite; exits non-zero on any failure). Healthy output ends with `All checks passed!` and `30 passed in 54.25s` (about 1 min warm). The first run after editing `lib/pageio.py` or upgrading pypdf rebuilds the page cache, which takes much longer.
- Fast loop: `make test-fast` (traps, recheck, table list); lint only: `make lint` (`ruff.toml`: correctness rules only)
- The golden tests need the sources: a test that says `sources for <year>: missing …` means download them, not a regression.
- A `code` FAIL is fixed test-first: add the failing assertion (trap test or golden row), then change the code, not the test. Regenerate `tests/golden/` (`python3 tests/snapshot.py <year>`) only for tables re-checked after the change.
- Hooks (`.claude/hooks/guard.py`): Edit/Write is blocked on `In/<year>/` and on `Out/` outside `Out/.work/`. Each edited `.py` file is linted.
- Debug into a scratch tree with `--out <dir>`, so the last run's `Out/` is not overwritten.

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

## Ask, do not decide
- Language, tooling and database engine, if not already chosen.

If you must proceed, pick the simplest reversible option and record it with the reason in `DECISIONS.md`.

## Conventions

- Keep dependencies few. Justify each new one in the commit message.
- Work in small steps and stop for review at the end of each numbered step above that asks for it.
- Keep it simple! Minimize produced files, do not duplicate informations.

## Things Claude gets wrong

Add a line here each time a mistake happens twice.
