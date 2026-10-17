# Plan

Implementation plan for ArmTrace, for use with Claude Code. Read `intent.md` first, then `specs.md`. Requirement IDs (E1, D2, M4...) refer to `specs.md`.

## Ground rules

Standing rules for the import pipeline (table list, table PDFs, validation, human review, decisions) live in `CLAUDE.md` and apply to Phases 0 to 2. Later phases need their own standing rules when they start. This file only says what to build and in what order.

- Work one phase at a time. Finish it, run its checks, and stop for review before starting the next.
- If the work departs from this plan, update `plan.md` in the same commit.

## Repository layout

```
armtrace/
  In/               # original report PDFs, untouched, one folder per year (E1)
  Out/              # pipeline output: one PDF per table, not hand-edited (E3)
                    #   <authority>/[<lawcode>/]<TableCode><Year>V<Volume>P<PageNumber>
  extraction/       # shared pipeline code plus per-year code (E6)
  .claude/agents/   # subagents for table extraction and validation (E3, E4)
  db/               # schema, migrations, data dictionary (D1, D5)
  mcp_server/       # read-only MCP server (M1-M7)
  llm/              # prompts, query loop, guardrails (L1-L7)
  eval/             # question set with known SQL and results (Q1-Q4)
  docs/
  CLAUDE.md
  intent.md
  specs.md
  plan.md
  DECISIONS.md
```

Adjust if the language or tooling chosen makes another layout more natural, and note the change in `DECISIONS.md`.

## Phase 0: Setup

1. Initialise the repository with the layout above, a README that points to `intent.md`, `specs.md` and this file, and a contribution guide stub (O3).
2. Set up the language toolchain, linting, formatting and a test runner. **[decide]** language and runner if not already chosen.
3. Create `DECISIONS.md` with the open **[decide]** items from `specs.md` listed as unresolved.
4. Add a licence placeholder and ask before choosing one (O1, O2).
5. Fill in the Commands section of `CLAUDE.md` with the real install, test, lint, extract and validate commands.

**Done when:** a fresh clone installs, lints and runs an empty test suite with one documented command each.

## Phase 1: Import pipeline, pilot 2025 (E1-E5, E7)

1. Place the 2025 report files in `In/2025/` and record where they were obtained (E1).
2. Write the table list extractor. Across every volume of the year, list each table with start and end page, authority and table name, using the index and any other summary. Mark tables that are listed but cannot be found, and tables that exist but are not listed, especially in the introduction of volume 1 (E2).
3. Write the table extractor. Using subagents, extract every table into a single PDF at `Out/<authority>/[<lawcode>/]<TableCode><Year>V<Volume>P<PageNumber>` (E3).
4. Add validation using subagents: file name against table name, authority and law code; PDF page count against the pages the table states; table name in captions, footer or header against the file name (E4). Tables that still fail after 5 review cycles go to human review. Nothing is dropped, silently corrected or invented (E5).
5. Check that a re-run on the same input gives the same PDF files (E7).
6. Stop and review with the maintainer: show the validation report next to the original pages.

**Done when:** the 2025 table list exists, every table that exists has its PDF, validation passes or the remaining failures are listed for human review, a re-run reproduces the same files, and the maintainer has reviewed the report against the original pages.

## Phase 2: Earlier years, 2024 to 2016 (E6, E8)

1. Add one year at a time in decreasing order. Shared code first, then only the code specific to that year (E6).
2. Note every layout change that needed year-specific code. Reduce those over time.
3. Run the same validation and human review loop for each year.

**Done when:** every year from 2016 to 2025 has its table list, its table PDFs and a validation report, and the maintainer has reviewed each report.

## Phase 3: Table PDFs to database (D)

**[decide]** The route from validated table PDFs to rows is not defined yet. Settle it with the maintainer before writing code, and record the decision in `DECISIONS.md`.

1. Design the first schema from the table types found in Phases 1 and 2. Each row must remain traceable to its table PDF by file name (D1, D2). Version the schema with migrations. **[decide]** database engine.
2. Load the validated tables. Anything that cannot be read with confidence is flagged, not guessed.
3. Normalise recurring entities (companies, recipient countries, banks, authorities), keeping the original spelling (D3). Record uncertain matches as uncertain. **[decide]** matching policy, ask before going beyond exact matches.
4. Write the data dictionary (D5) and add data release versioning (D6).

**Done when:** the validated tables load into the database, each row points to its source PDF, and the maintainer has checked a sample against the source pages.

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

## Questions for the maintainer before starting

1. Language and stack preferences?
2. Are the report PDFs for 2016 to 2025 already collected, and where do they come from?
3. Which domain collaborators can review the validation reports and help build the question set?
4. How should validated table PDFs become rows in the database?
