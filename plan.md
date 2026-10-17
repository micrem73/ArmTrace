# Plan

Implementation plan for ArmTrace, for use with Claude Code. Read `intent.md` first, then `specs.md`. Requirement IDs (E1, D2, M4...) refer to `specs.md`.

## Ground rules

Standing rules for the import pipeline (table list, table PDFs, validation, human review, decisions) live in `CLAUDE.md` and apply to Phases 0 to 2. Later phases need their own standing rules when they start. This file only says what to build and in what order.

- Work one phase at a time. Finish it, run its checks, and stop for review before starting the next.
- If the work departs from this plan, update `plan.md` in the same commit.
- Where the work stands is kept in `CLAUDE.md` ("Where the work stands"), not here.

## Repository layout

```
ArmTrace/
  In/<year>/          # original report PDFs, untouched (E1); manifest.tsv, download_185.sh
  Out/<year>/         # PDF/ tables, TRASH/, review.html, tablesindex.sqlite (E2, E3)
  Out/.work/          # cache, table list, verdicts: regenerable, never read by people
  lib/                # shared code: engine.py (step-1 engine), pageio, glyphs, ...
  lib/data/           # versioned data files (glyph map, glyph decisions)
  editions/           # y<year>.py: what one edition differs in (E6)
  tools/              # recheck, build_index, page_check, review_pages, build_glyphmap
  tests/              # traps, golden rows per verified year, evals
  .claude/agents/     # table-checker subagent (E4)
  .claude/skills/     # check-year, download-sources
  Reports2PDFTables.py, run_year.py, VerifyTables.py, CatalogueTables.py
  CLAUDE.md  DECISIONS.md  README.md  intent.md  specs.md  plan.md
  # later: db/, mcp_server/, llm/, eval/ for Phases 3-6
```

Adjust if the language or tooling chosen makes another layout more natural, and note the change in `DECISIONS.md`.

## Phase 0: Setup (done)

Python 3.10, ruff, pytest; `make check` is the one "done" command. Licence still open (step 4).

1. Initialise the repository with the layout above, a README that points to `intent.md`, `specs.md` and this file, and a contribution guide stub (O3).
2. Set up the language toolchain, linting, formatting and a test runner. **[decide]** language and runner if not already chosen.
3. Create `DECISIONS.md` with the open **[decide]** items from `specs.md` listed as unresolved.
4. Add a licence placeholder and ask before choosing one (O1, O2).
5. Fill in the Commands section of `CLAUDE.md` with the real install, test, lint, extract and validate commands.

**Done when:** a fresh clone installs, lints and runs an empty test suite with one documented command each.

## Phase 1: Import pipeline, pilot 2025 (E1-E5, E7, E9) (done: tag `2025-clean`)

1. Place the 2025 report files in `In/2025/` and record where they were obtained (E1).
2. Write the table list extractor. Across every volume of the year, list each table with start and end page, authority and table name, using the index and any other summary. Mark tables that are listed but cannot be found, and tables that exist but are not listed, especially in the introduction of volume 1 (E2).
3. Write the table extractor. Using subagents, extract every table into a single PDF at `Out/<authority>/[<lawcode>/]<TableCode><Year>V<Volume>P<PageNumber>` (E3).
4. Add validation using subagents: file name against table code, authority, law code and printed start page; PDF page count against the pages the table states; table code in captions, footer or header against the file name (E4). Failures escalate by kind: convention questions to the human at once, code defects after 2 failed fixes, checker doubts when a second checker disagrees (E5). Nothing is dropped, silently corrected or invented.
5. Check that a re-run on the same input gives the same PDF files (E7), and that every part of every page lands in exactly one output (E9).
6. Stop and review with the maintainer: show the validation report next to the original pages.

**Done when:** the 2025 table list exists, every table that exists has its PDF, validation passes or the remaining failures are listed for human review, a re-run reproduces the same files, and the maintainer has reviewed the report against the original pages.

## Phase 2: Earlier years, 2024 to 2016 (E6, E8) (in progress)

1. Add one year at a time in decreasing order. Shared code first (`lib/engine.py`), then only the code specific to that year (`editions/y<year>.py`, overriding only the functions `engine.OVERRIDES` lists) (E6). The golden tests of the verified years say whether a shared change broke one.
2. Note every layout change that needed year-specific code. Reduce those over time.
3. Run the same validation and human review loop for each year.

**Done when:** every year from 2016 to 2025 has its table list, its table PDFs and a validation report, and the maintainer has reviewed each report.

## Phase 3: SQL extraction and database (D)

The route is decided (DECISIONS.md): the extractor reads the original volumes in `In/`, finding each table through `Out/<year>/tablesindex.sqlite`, never the split PDFs. SQLite, with a plain `.sql` dump as the deliverable.

1. Catalogue the table shapes of every verified year (`CatalogueTables.py`: rotation, rules, headers, record grammar), then diff them across years.
2. Only then write the schema: one lossless cell table (`stg_cell`) plus curated projections; each row traceable to source file, volume, printed page and table key (D1, D2). Version it with migrations.
3. Write the extractor with its verification: printed totals against the sum of the rows, row counts, text coverage against the source page (D7). Values parsed exactly (D4). Anything that cannot be read with confidence is recorded as unknown with a reason, not guessed.
4. MAE text goes through `lib/glyphs.py`; undecoded glyphs are carried flagged (D8).
5. Compare reading tools last, against the ground truth the verification gives.
6. Normalise recurring entities (companies, recipient countries, banks, authorities), keeping the original spelling (D3). Record uncertain matches as uncertain. **[decide]** matching policy, ask before going beyond exact matches.
7. Write the data dictionary (D5) and add data release versioning (D6).

**Done when:** the tables of the verified years load into the database, each row points to its source page, the printed totals match, and the maintainer has checked a sample against the source pages.

## Phase 4: MCP server (M)

1. Implement a read-only MCP server over the database (M1, M2).
2. Expose the schema and data dictionary as context (M3).
3. Provide a query tool that accepts a single SELECT, rejects anything else, enforces time and size limits, and returns results with the executed query and the source PDF names for the returned rows (M4-M6).
4. Add tests for rejected statements (writes, multiple statements, comments hiding statements), limits and source references in the output.
5. Query logging: implement locally, keep it off by default until the retention policy is decided (M7). **[decide]**

**Done when:** an MCP client can list the schema, run a valid query and receive rows with their sources, and every unsafe statement in the test set is rejected.

## Phase 5: LLM layer (L)

1. Write the system prompt and query loop: question to SQL, execution through the MCP server, presentation of results (L1).
2. Require every answer to include the SQL, the source references and the scope (years, editions, filters) (L3, L4).
3. If the database cannot answer, the answer says so (L2). No interpretation or causal claims (L6).
4. Handle ambiguity by stating the interpretation used or asking (L5).
5. Keep the provider behind a thin interface so the model can be swapped (L7). **[decide]** provider and hosting.

**Done when:** a set of sample questions returns answers that satisfy L1-L6, checked by hand against the data.

## Phase 6: Evaluation (Q)

1. With the maintainer and domain collaborators, build the question set: natural-language question, correct SQL, correct result (Q1). Start with 30 to 50 questions covering simple lookups, aggregations across years, joins through entities, and deliberately ambiguous questions.
2. Build a runner that executes the LLM layer on the set and reports: correct, wrong result, wrong interpretation, refusal (Q2).
3. Build the extraction check against a manually reviewed table sample per edition, including tables sent to human review (Q3).
4. Wire the question set so it runs on any change to model, prompt or schema (Q4).

**Done when:** one command produces an error report per release, and the baseline is recorded. **[decide]** the acceptable error rate, do not set it yourself.

## Phase 7: Interface (I)

1. Build a minimal interface: question box, answer, visible SQL, source references (I1, I2).
2. Let the user edit and re-run the query (I3).
3. CSV export with sources (I4).
4. Italian first (I5). **[decide]** English from the start.
5. Local entry points (province, municipality, company location) only if the reports contain that granularity (I6). Check the data first and report what exists.

**Done when:** a non-technical user can ask a question, see where the answer comes from, and export it, tested with at least one real user from the target groups.

## Phase 8: Visual outputs (V)

Do not start before the maintainer confirms scope. **[decide]**

1. Visual outputs on the same database and the same source rules (V1, V2).
2. Document the methods (V3).

## Standing checks (every phase)

- Tests pass and linting is clean.
- Every generated table PDF follows the naming scheme in E3.
- No new **[decide]** item resolved without a recorded decision.
- Docs updated: README commands, `DECISIONS.md`, and the data dictionary once it exists.

## Questions for the maintainer

Answered: Python, SQLite (DECISIONS.md); sources 2001–2025 come from the official URLs in `In/manifest.tsv`; rows come from the original volumes through `tablesindex.sqlite`.

1. Which domain collaborators can review the validation reports and help build the question set?
2. Licence for the code and for the data (O1, O2).
