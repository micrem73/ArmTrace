# Intent

## Purpose

ArmTrace makes Italian arms export data queryable. It turns the official reports published under Law 185/1990 into a structured database that journalists, activists and local administrators can question in plain language, without reading hundreds of pages of tables.

## Problem

Italy publishes data on arms exports, but in a form that is formally public and practically closed: long reports, dense tables, inconsistent layouts from year to year. Anyone who wants to know what was exported, to whom, by whom and through which banks has to rebuild the dataset by hand. Most people who would act on this information do not have the time or the skills to do that.

The effect is that oversight exists on paper only. Institutions are supposed to examine these exports, but conflict rarely emerges, because the material is hard to read and nobody is forced to read it.

## Goals

- Extract each table of the official export reports into its own validated PDF, then into a clean, versioned SQL database.
- Expose that database through an MCP server so an LLM can translate natural-language questions into SQL, run them, and return the results with the underlying query visible.
- Keep every answer traceable to its source report, year and page.
- Stay usable by non-technical people working at a local scale: a newsroom, a campaign, a municipal council.
- Develop visual and artistic ways of showing the data, so that arms exports can be understood as a situated, local matter and not only as national statistics.

## Non-goals

- Not a general arms-trade database. The scope is Italian exports under Law 185/1990.
- Not a source of editorial claims. ArmTrace retrieves and presents data; it does not interpret it for the user.
- Not an LLM that answers from memory. The model only translates questions and presents what the database returns.
- Not a closed or paid service.

## Principles

- **Open by default.** Code, schema and extraction methods are open source and documented.
- **Verifiable over fluent.** A plausible answer without a traceable source is a failure. The generated SQL and the source reference are always shown.
- **Official sources only.** Data comes from published government reports, not from scraped or leaked material.
- **Friction where it matters.** The LLM is an interface, not an authority. The design should keep the user able to check, question and contest what they see.
- **Local scale.** The tool should help people connect disarmament to places they live in and institutions they can reach.

## Constraints

- The source reports are PDFs with layouts that change over time, so the import pipeline needs shared code plus year-specific code, and every extracted table has to be validated.
- Natural-language-to-SQL can return wrong queries that look right; the system needs guardrails and visible queries.
- Open-source, with limited and project-based funding.

## Users and stakeholders

- **Primary users:** investigative journalists, peace and disarmament activists, local administrators.
- **Collaborators and context:** OPAL Brescia, which works on arms exports; Nexa Center at Politecnico di Torino, which supports the research.
- **Adjacent work:** research and art projects on weaponisation and peace, where ArmTrace data can ground practice in evidence.

## Success looks like

- A journalist or council member can ask a question about Italian exports and get a sourced answer in minutes.
- Every figure in an answer can be traced back to a page of an official report.
- The import pipeline can ingest a new yearly report with mostly shared code, plus the code specific to that year.
- Other people reuse the database, the MCP server or the method.
- Visual or artistic outputs built on the data are used in public, not only in research settings.

## Open questions

- How to evaluate the accuracy of generated SQL, and what error rate is acceptable.
- Which visual analytics approaches fit non-expert users best.
- Governance and hosting once the project outgrows a single maintainer.
- Whether reports older than 2016 can be reliably extracted.
- How validated table PDFs become rows in the database.
