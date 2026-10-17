# ArmTrace: import pipeline

ArmTrace makes Italian arms export data queryable for journalists, activists and local administrators. This file covers only the import pipeline: official reports in, a validated SQL database out. Do not build the query server, the LLM layer or any interface here.

## Source and goal

- Input: the official yearly reports on arms exports published under Law 185/1990 (PDF). Official sources only.
- Output: every data table in the reports as rows in a SQL database, each row traceable to where it came from.
- Layout changes from year to year. Extraction must survive that without code edits per edition.

## Commands

- Install: `pip install --user -r requirements.txt` (`python3 -m venv` fails on this host: no `ensurepip`)
- Download sources: `bash In/download_185.sh In In/manifest.tsv` (checks page counts against the manifest; exits 1 on any problem). Skill: `download-sources`
- Test: `python3 -m pytest tests/`
- Lint: TBD
- Extract one edition: `python3 Reports2PDFTables_2025.py` (both volumes; writes `Out/PDF/`, `Out/TRASH/`, `Out/manifest-2025.json`)
- Table list of a year: `python3 Reports2PDFTables_2025.py --dry-run --no-trash --tables Out/tables-2025.tsv` (~10 s with a warm page cache)
- Cut one table: `python3 Reports2PDFTables_2025.py --only MEF/UE` (repeatable; key = `authority/[article/]code`, as in the list)
- Review report, then delete the PNGs of PASS tables: `python3 tools/review_report.py 2025` (`--keep-png` to keep them)
- Fast test (table list only): `python3 -m pytest tests/test_table_list.py`; full test (all PDFs, ~1 min warm) `python3 -m pytest tests/`
- Validate: `python3 Reports2PDFTables_2025.py --manifest Out/manifest-2025.jsonl`, then `python3 VerifyTables.py --year 2025 --manifest Out/manifest-2025.jsonl` (writes `Out/VERIFY/`; `--manifest` is step 1's JSON, not `In/manifest.tsv`)
- Debug into a scratch tree with `--out <dir>`, so the last run's `Out/` is not overwritten.

## Requirements

- Keep each original report file untouched in `In/<year>/`. Generated output goes in `Out/PDF/<authority>/[<lawcode>/]<TableCode><Year>V<Volume>P<PageNumber>.PDF` and is never hand-edited. `TableCode` is the printed code (`UE`, `F1`), not the table's title. `PageNumber` is the page number printed on the document at the table's first page, not the PDF page index (2025 F1: `MAE/F12025V1P1035.PDF`).
- Extract a list of existing tables of each year, looking into every volumes for that year. For each table, report if existing start-end page number, authority, table code. Tables can be listed in "indice", or in other summaries leading the tables. Some listed tables cannot exist. Tables can exist even if ot explicitly listed in tables, expecially in introduciton of volume 1. 
- Build the year's table list once (`Out/tables-<year>.tsv`). Using subagents, cut every table into a single PDF with the deterministic splitter, one table per subagent, and check it as soon as it is cut. Subagents report failures with page evidence; one agent fixes the splitting code, rebuilds the list and re-sends only the rows that changed. Do not run years in parallel.
- Using subagents, validate every extraction: the file name corresponds to the table code, authority, law code and printed start page it refers to. The page count of the PDF corresponds to the number of pages that the table says it has. The table code in captions, footer or header corresponds to the file name. 
- SQL extraction reads the original volumes in `In/` through the table list (`Out/tables-<year>.tsv`), never the split PDFs: those drop the tagged table structure, page labels, bookmarks and metadata of the source. The split PDFs are for review, and each carries its provenance (source file, SHA-256, pages) in its metadata.
- Tables that fail validation after 5 review cycles are explicitly asked for human review. Never drop or silently correct a value. Never invent one.
- Layout differences between editions live in a per-edition code. A new edition should share mostly of the codebase, plus specific code needed for that year. 
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

## Things Claude gets wrong

Add a line here each time a mistake happens twice.
