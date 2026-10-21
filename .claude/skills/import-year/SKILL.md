---
name: import-year
description: Import one year's tables of Law 185/1990 reports into SQL - work through Out/.work/<year>/import-queue.tsv by dispatching one table-importer subagent per table (8 at a time), fix the reading defects they report test-first, re-extract and re-judge only the tables whose rows changed, then assemble Out/<year>/armtrace-<year>.sqlite and stop with what needs the maintainer. Use when the user asks to import, extract or load a year's tables into SQL, or after a change to lib/extract.py.
---

# Import a year

`ExtractTables.py` reads the rows; `table-importer` subagents judge them, one table each;
you (the main agent) are the only one who fixes code. **Importers never edit code,
`columns.tsv` or `totals.tsv`; you never agree columns or decide a convention yourself**
(CLAUDE.md, "Escalation is by kind").

## 1. Know what is queued

```bash
python3 tools/import_queue.py <year> --extract
```

It re-reads every table with agreed columns into its own folder
(`Out/.work/<year>/import/<key>/`, 4 at a time, a few minutes), then prints counts and
writes `Out/.work/<year>/import-queue.tsv` (`key action reason`):

| action | what you do |
|---|---|
| `columns` | dispatch an importer, mode `columns` (step 2) |
| `check`, `recheck` | dispatch an importer, mode `check` (step 2) |
| `second-check` | dispatch an importer, mode `check`, as second opinion (step 2) |
| `fix` | collect for step 4 |
| `human` | collect for step 5 |
| `extract` | none should remain after `--extract`; if one does, its extraction crashed: a `fix` |
| `done` | nothing |

If it lists tables **changed after a PASS**, note them: the user sees that list before
any commit.

## 2. Dispatch importers, 8 at a time

```bash
awk -F'\t' 'NR>1 && $2 ~ /^(columns|check|recheck|second-check)$/ {print $1"\t"$2}' \
    Out/.work/<year>/import-queue.tsv
```

For each line, launch one subagent with the Agent tool:

- `subagent_type: "table-importer"` (`.claude/agents/table-importer.md`); if that type is
  not available, use `general-purpose` and start the prompt with "Read
  .claude/agents/table-importer.md and follow it exactly."
- prompt: `Year: <year>. Table key: <key>. Mode: columns|check.` plus, for
  `second-check`: `You are the second opinion: write verdict-second.json and do not read
  verdict.json.` Ask for the one closing line only.

Keep **8 running**: start the next queued table each time one reports. Tables of one family
(DOG `MG1`…`MG18`, the `M`/`N`/`O`/`P` series) print the same columns: send one `columns`
importer per family first, and the rest once its draft is approved. A subagent stopped by a
usage limit is resumed with SendMessage, not restarted. Never report or predict a result
before it arrives.

## 3. Close the round

```bash
python3 tools/import_queue.py <year>
```

## 4. Fix code defects, one at a time

For each `fix`, read the verdict's `cause` and `evidence`, look at the page yourself, then:

1. Add the failing assertion first (`tests/test_extract.py` for a table's rows,
   `tests/test_traps.py` for a reading rule), on the smallest pages that show it. Run it:
   it must fail.
2. Change the reader (`lib/extract.py`, `lib/geometry.py`, `lib/grammar.py`) until it
   passes. A rule that holds for one table only is a sign the reading is wrong: say so.
3. `make test-fast`, then `python3 tools/import_queue.py <year> --extract`: every table is
   re-read; those whose rows changed come back `recheck`, PASS tables among them listed.
4. Back to step 2 for the re-queued tables.

A defect still failing after 2 fixes goes to the maintainer (the queue says `human`).

## 5. Stop and report

When nothing is left to dispatch or fix, assemble the year and run the suite:

```bash
python3 ExtractTables.py <year> --merge Out/.work/<year>/import/*/
make check
```

Report to the user, briefly:

- counts per action, and the merge's line (`wrote … sha256 …`);
- every `human`, grouped:
  - **columns drafted**: per table (or family) the proposed lines from
    `columns-proposal.tsv`; on approval, append them to `lib/data/columns.tsv` (and the
    `# totals.tsv` part to `lib/data/totals.tsv`), delete the proposal, and start again
    at step 1;
  - **conventions**: key, question, page;
  - **still failing after 2 fixes**, **importers disagree**: key and both causes;
- the code fixes made, each with its test;
- tables that changed after a PASS, if any.

Do not commit while `human` items are open or changed-after-PASS tables are not re-judged.
