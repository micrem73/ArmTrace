# ArmTrace: import pipeline

ArmTrace makes Italian arms export data queryable for journalists, activists and local administrators. This file covers only the import pipeline: official reports in, a validated SQL database out. Do not build the query server, the LLM layer or any interface here.

## Source and goal

- Input: the official yearly reports on arms exports published under Law 185/1990 (PDF). Official sources only.
- Output: every data table in the reports as rows in a SQL database, each row traceable to where it came from.
- Layout changes from year to year. Extraction must survive that without code edits per edition.

## Commands

Fill these in as they exist. Do not guess them.

- Install: TBD
- Test: TBD
- Lint: TBD
- Extract one edition: TBD
- Validate: TBD

## Requirements

- Keep each original report file untouched in `In/<year>/`. Generated output goes in `Out/<authority>/[<lawcode>/]<TableCode><Year>V<Volume>P<PageNumber>` and is never hand-edited.
- Extract a list of existing tables of each year, looking into every volumes for that year. For each table, report if existing start-end page number, authority, table name. Tables can be listed in "indice", or in other summaries leading the tables. Some listed tables cannot exist. Tables can exist even if ot explicitly listed in tables, expecially in introduciton of volume 1. 
- Using subagents, extract every table into a single PDF per table.
- Using subagents, validate every extraction: the PDF name corresponds to the table name, authority, and law code it refers to. The page number of the PDF corresponds to the number of pages that the table says it has. Table name in captions, footer or header corresponds to the file name. 
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
