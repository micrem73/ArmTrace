---
name: check-year
description: Run the table-checking loop for one year of Law 185/1990 reports - work through Out/.work/<year>/queue.tsv by dispatching one table-checker subagent per table (8 at a time), then recheck and republish, and stop with what needs a code fix or a human decision. Use after `run_year.py <year>`, after any code change to the splitter, or when the user asks to check, re-check or validate a year's tables.
---

# Check a year

The splitter is deterministic; this loop is where the tables are judged. You (the main
agent) dispatch checkers and read their results. **Checkers never edit code, and you
never decide a convention question yourself** (CLAUDE.md, "Escalation is by kind").

## 1. Know what is queued

```bash
python3 tools/recheck.py <year>
```

It prints counts and writes `Out/.work/<year>/queue.tsv` (`key action reason`). Actions:

| action | what you do |
|---|---|
| `check`, `recheck` | dispatch a checker (step 2) |
| `second-check` | dispatch a checker in second-opinion mode (step 2) |
| `fix` | nothing yet: collect for step 4 |
| `human` | nothing: collect for step 4 |
| `done` | nothing |

If it lists tables **changed after a PASS**, note them: the user sees that list before
any commit.

## 2. Dispatch checkers, 8 at a time

```bash
awk -F'\t' 'NR>1 && ($2=="check"||$2=="recheck"||$2=="second-check") {print $1"\t"$2}' \
    Out/.work/<year>/queue.tsv
```

For each line, launch one subagent with the Agent tool, `run_in_background` implicit:

- `subagent_type: "table-checker"` (defined in `.claude/agents/table-checker.md`); if
  that type is not available in this session, use `general-purpose` and start the
  prompt with "Read /home/mic/ArmTrace/.claude/agents/table-checker.md and follow it
  exactly."
- prompt: `Year: <year>. Table key: <key>.` plus, for `second-check`: `You are the
  second opinion: write verdict-second.json and do not read verdict.json.` Ask for the
  one closing line only.

Keep **8 running**: start 8, and start the next queued table each time one reports.
A checker that stops on a usage limit is resumed with SendMessage, not restarted.
Do not report or predict results before they arrive.

## 3. Close the round

When every dispatched checker has reported:

```bash
python3 tools/recheck.py <year>      # records the verdicts, decides the next action
python3 tools/build_index.py <year>  # republish tablesindex + review.html, prune PASS PNGs
```

If the second recheck queues new `check`/`second-check` work (a checker asked for a
second opinion), run step 2 again for those only.

## 4. Stop and report

Report to the user, briefly:

- counts per action;
- every `fix`: key and cause - the code defects for the main agent to fix next, one at
  a time, each followed by `run_year.py <year>` and this skill again (only changed
  tables are re-checked);
- every `human`: key and question, from `Out/<year>/HUMAN-REVIEW.md` - ask the user,
  do not decide;
- tables that changed after a PASS, if any;
- the link `Out/<year>/review.html`.

Do not commit while `human` items are open or while changed-after-PASS tables have not
been re-checked.
