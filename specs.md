# Specs

Derived from `intent.md`. Requirements are numbered so they can be referenced in issues and tests. Items marked **[decide]** are not settled by the intent and need a decision.

## 1. Scope

ArmTrace ingests the official Italian arms export reports published under Law 185/1990, stores them as structured data, and lets users query them in natural language through an LLM connected to the database via MCP. Out of scope: other countries, non-official sources, editorial interpretation of the data.

## 2. Architecture

```
Official reports (PDF), one folder per year (In/)
        |
  [1] Table list: which tables exist, per year (Out/<year>/tablesindex.sqlite)
        |
  [2] Split: one PDF per table, for review (Out/<year>/PDF/), plus trash
        |
  [3] Validation (subagents): escalation by kind of failure, then human review
        |
  [4] SQL extraction: reads the original volumes in In/ through tablesindex.sqlite
        |
  [5] SQL database (SQLite, plus a plain .sql dump)
        |
  [6] MCP server (read-only query tools)
        |
  [7] LLM layer: question -> SQL -> results -> presentation
        |
  [8] Interface: answers, visible queries, source references
        |
  [9] Visual outputs (later phase)
```
## 3. Import pipeline

Covers stages [1] to [3]. Initial coverage: 2025 first, then each earlier year down to 2016.

- **E1.** Keep each original report file untouched in `In/<year>/`.
- **E2.** For each year, list the tables that exist, looking into every volume of that year, once per year (`Out/<year>/tablesindex.sqlite`). For each table record the start and end page, the authority and the printed table code. Sources for the list are the index and any other summary that precedes the tables. Some listed tables do not exist. Some tables exist without being listed, especially in the introduction of volume 1. The list has to account for both cases.
- **E3.** Cut every table into a single PDF with the deterministic splitter, written to `Out/<year>/PDF/<authority>/[<lawcode>/]<TableCode><Year>V<Volume>P<PageNumber>.PDF`; subagents check each one as soon as it is cut. `TableCode` is the printed code (`UE`, `F1`), not the title; `PageNumber` is the page number printed at the table's first page, not the PDF page index. Generated files are never hand-edited. The split PDFs are for review: each carries its provenance (source file, SHA-256, pages) in its metadata.
- **E4.** Using subagents, validate every extraction: the PDF name matches the table code, the authority, the law code and the printed start page it refers to; the number of pages of the PDF matches the number of pages the table states it has; the table name in captions, footer or header matches the file name.
- **E5.** Escalation is by kind of failure, not by count. A convention question (what counts as part of a table, where it is filed, where it ends) goes to human review at once; a code defect is fixed, and goes to human review if it still fails after 2 fixes; a checker doubt gets a second, independent checker, and goes to human review if the two disagree. Never drop or silently correct a value. Never invent one.
- **E6.** Layout differences between editions live in per-edition code. A new edition shares most of the codebase and adds only the code specific to that year.
- **E7.** The run is reproducible: the same input and configuration give the same PDF files.
- **E8.** Work order: pilot on 2025, then validation review by the maintainer, then each earlier year in decreasing order down to 2016. Years are not run in parallel.
- **E9.** Every part of every source page ends up in exactly one place: a table file, a table's cut-out part, or the trash. A page check enforces it at the end of every run.

## 4. Database

Stages [4] and [5]. SQL extraction reads the original volumes in `In/`, locating each table through `Out/<year>/tablesindex.sqlite`, never the split PDFs: those drop the tagged table structure, page labels, bookmarks and metadata of the source. Order of work: catalogue the table shapes across years, diff them, then the schema, then the extractor with its verification; the choice of reading tool comes last (DECISIONS.md).

- **D1.** SQLite, with a plain `.sql` dump as the deliverable; documented, versioned schema. Decided 2026-10-19: one table per printed table code, as printed (printed text kept beside each parsed value), plus curated projections later.
- **D2.** Every row remains traceable to its source: report file, volume, printed page and table key.
- **D3.** Entities that appear across years (companies, recipient countries, banks, authorities) are normalised, with the original spelling kept. **[decide]** How aggressive normalisation should be and how to record uncertain matches.
- **D4.** Monetary values keep the original currency and unit as published, parsed exactly (Italian convention, decimal, never floating point); a printed dash is empty, never zero. **[decide]** Whether to add inflation-adjusted or converted values.
- **D5.** The schema and a data dictionary are published with the code.
- **D6.** Data releases are versioned, so a published answer can be reproduced against the version it used.
- **D7.** The tables' own printed totals are the extraction's check: the sum of the rows must match the printed total.
- **D8.** Text printed in fonts with no character map is recovered glyph by glyph, only where the glyph's identity is certain. A glyph that cannot be named stays undecoded and is carried into the database flagged.

## 5. MCP server

- **M1.** Exposes the database to the LLM through MCP.
- **M2.** Read-only. No tool can modify data.
- **M3.** Provides the schema and data dictionary to the model as context.
- **M4.** Accepts SQL and returns results together with the executed query and provenance for the returned rows.
- **M5.** Enforces limits on query time and result size.
- **M6.** Rejects statements other than a single SELECT.
- **M7.** Logs queries for debugging and evaluation. **[decide]** Retention and privacy policy for user queries.

## 6. LLM layer

- **L1.** The model translates the user's question into SQL, runs it through the MCP server, and presents the result.
- **L2.** The model does not answer from its own knowledge. If the database cannot answer, it says so.
- **L3.** Every answer shows the generated SQL and the source references for the figures it uses.
- **L4.** Answers state their scope: which years, which report editions, which filters were applied.
- **L5.** When a question is ambiguous (for example, "exports to Saudi Arabia" could mean authorised or delivered), the system states the interpretation it used or asks.
- **L6.** The model does not add interpretation, causal claims or judgments to the data it returns.
- **L7.** Model and provider are swappable. **[decide]** Hosted or self-hosted model, and which one.

## 7. Interface

- **I1.** Usable by non-technical people without knowing SQL.
- **I2.** The generated query and the sources are visible but do not obstruct the answer.
- **I3.** The user can edit and re-run the query. This keeps the LLM an interface and not an authority.
- **I4.** Results can be exported (CSV) with their provenance.
- **I5.** Primary language is Italian. **[decide]** Whether English is supported from the start.
- **I6.** Local-scale entry points: queries by province, municipality or company location. This depends on whether the reports contain this granularity. **[decide]**

## 8. Visual outputs (later phase)

- **V1.** Visual and artistic representations built on the same database and the same provenance rules.
- **V2.** Every figure shown must remain traceable to its source rows.
- **V3.** Methods and choices documented so others can reuse them. **[decide]** Scope and format of this phase.

## 9. Quality and evaluation

- **Q1.** A test set of questions with known correct SQL and results, built with domain collaborators.
- **Q2.** Track how often generated SQL is wrong or answers a different question than asked, per release. **[decide]** Acceptable error rate.
- **Q3.** Extraction and validation are checked against a manually reviewed sample of tables per edition, including the tables sent to human review.
- **Q4.** Any change to the model, prompt or schema re-runs the test set.

## 10. Open source

- **O1.** Code, schema, per-edition extraction code and documentation are open source. **[decide]** Licence.
- **O2.** The processed dataset is published with the same openness as the source allows. **[decide]** Data licence, after checking the terms of the original reports.
- **O3.** Contribution guide, and documentation on running the whole pipeline from scratch.

## 11. Constraints

- The source layout changes over time (E6).
- Natural-language-to-SQL can produce queries that look right and are wrong, so L3, I3 and Q1–Q4 are mandatory, not optional.
- Limited, project-based funding. Hosting and model costs need to stay low. **[decide]**

## 12. Milestones

**[decide]** Order and timing. A reasonable sequence, following the dependencies above:

1. Table list, table PDFs and validation for the 2025 pilot, then each earlier year down to 2016 (E).
2. SQL extraction from the original volumes and the database (D).
3. MCP server and basic query loop with visible SQL (M, L).
4. Test set and evaluation (Q).
5. Interface for non-technical users (I).
6. Visual outputs (V).
