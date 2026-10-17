#!/usr/bin/env python3
"""Decide what each table needs next: nothing, a check, a fix, or a person.

    python3 tools/recheck.py 2025 [--adopt]

Run after every change to the code and after every round of checks. For each
table in Out/.work/<year>/tables.tsv it compares the table file's content now
with the content its verdict judged (checks/<key>/checked.json, written by
tools/review_pages.py), keeps a history of verdicts (checks/<key>/history.jsonl)
and assigns one action:

  check         no verdict yet
  recheck       the content changed since the check; old verdict cleared
  second-check  the checker doubted itself (kind=checker): a second,
                independent checker writes verdict-second.json
  fix           a code defect (kind=code): the main agent fixes the code
  human         a convention question (kind=convention); or a code defect
                still failing after 2 fixes; or two checkers disagree
  done          PASS on the current content

Escalation is by kind, not by count (decided 2026-10-17, DECISIONS.md).
Writes Out/.work/<year>/queue.tsv and, when anything needs a person,
Out/<year>/HUMAN-REVIEW.md. Lists the tables that had PASSED and changed,
so a fix that moves other tables is seen before it is committed.

--adopt records the current content as checked for verdicts that predate
checked.json (once, when introducing this tool on already-checked output).
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import layout  # noqa: E402

MAX_CODE_FIXES = 2


def content_hash(pdf):
    doc = pymupdf.open(pdf)
    return hashlib.sha1("\f".join(p.get_text() for p in doc).encode()).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def history(folder):
    path = folder / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def record(folder, entry):
    """Append a verdict to the table's history once (keyed by content+file)."""
    seen = {(h["content"], h["source"], h["verdict"], h.get("kind"))
            for h in history(folder)}
    if (entry["content"], entry["source"], entry["verdict"], entry.get("kind")) in seen:
        return
    with open(folder / "history.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def decide(folder, current, adopt=False):
    """(action, reason, moved) for one table."""
    checked = read_json(folder / "checked.json")
    first = read_json(folder / "verdict.json")
    second = read_json(folder / "verdict-second.json")
    if first and checked is None and adopt:
        checked = {"content": current}
        (folder / "checked.json").write_text(json.dumps(checked), encoding="utf-8")
    if not first:
        return "check", "no verdict yet", False
    if checked is None or checked.get("content") != current:
        was_pass = first.get("verdict") == "PASS"
        for name in ("verdict.json", "verdict-second.json", "checked.json"):
            (folder / name).unlink(missing_ok=True)
        return "recheck", "content changed since the check", was_pass
    for source, verdict in (("first", first), ("second", second)):
        if verdict:
            record(folder, {"content": current, "source": source,
                            "verdict": verdict.get("verdict"),
                            "kind": verdict.get("kind"),
                            "cause": verdict.get("cause", "")})
    word, kind = first.get("verdict"), first.get("kind") or "code"
    if second:
        if (second.get("verdict") == "PASS") != (word == "PASS"):
            return "human", "the two checkers disagree", False
        word, kind = second.get("verdict"), second.get("kind") or kind
        if kind == "checker":
            return "human", "both checkers unsure", False
    if word == "PASS":
        return "done", "", False
    if kind == "convention":
        return "human", "convention question: " + first.get("cause", ""), False
    if kind == "checker":
        return "second-check", "checker unsure: " + first.get("cause", ""), False
    failed = {h["content"] for h in history(folder)
              if h["verdict"] != "PASS" and (h.get("kind") or "code") == "code"}
    if len(failed) > MAX_CODE_FIXES:
        return "human", f"still failing after {MAX_CODE_FIXES} fixes: " \
                        + first.get("cause", ""), False
    return "fix", first.get("cause", ""), False


def main(year, adopt=False):
    with open(layout.tables_tsv(year, ROOT), encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t") if r["file"]]
    queue, moved = [], []
    for row in rows:
        folder = layout.checks_dir(year, ROOT) / layout.key_dir(row["key"])
        folder.mkdir(parents=True, exist_ok=True)
        current = content_hash(layout.pdf_dir(year, ROOT) / row["file"])
        action, reason, was_pass = decide(folder, current, adopt)
        queue.append((row["key"], action, reason))
        if was_pass:
            moved.append(row["key"])

    target = layout.work_year(year, ROOT) / "queue.tsv"
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("key\taction\treason\n")
        for key, action, reason in queue:
            fh.write(f"{key}\t{action}\t{reason.replace(chr(9), ' ')}\n")

    human = [(k, r) for k, a, r in queue if a == "human"]
    review = layout.year_dir(year, ROOT) / "HUMAN-REVIEW.md"
    if human:
        lines = [f"# Human review {year}", "",
                 "Each table below needs a decision. Its pages and verdicts are in "
                 f"`Out/.work/{year}/checks/<key>/`; it is also listed in review.html.", ""]
        for key, reason in human:
            lines += [f"## {key}", "", reason, ""]
        review.write_text("\n".join(lines), encoding="utf-8")
    else:
        review.unlink(missing_ok=True)

    counts = {}
    for _k, action, _r in queue:
        counts[action] = counts.get(action, 0) + 1
    print(f"recheck {year}: " + ", ".join(f"{a} {n}" for a, n in sorted(counts.items())))
    if moved:
        print(f"  changed after a PASS ({len(moved)}), review before committing: "
              + " ".join(moved))
    for key, action, reason in queue:
        if action not in ("done",):
            print(f"  {action:13} {key}  {reason}"[:200])
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--adopt"]
    if len(args) != 1:
        raise SystemExit(__doc__)
    sys.exit(main(args[0], adopt="--adopt" in sys.argv))
