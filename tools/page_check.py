#!/usr/bin/env python3
"""Final check of an extraction: every source page lives in exactly one place.

    python3 tools/page_check.py 2025

Reads only what is published -- each table PDF's provenance (its /Subject
JSON) in Out/<year>/PDF/ and the trash runs in Out/<year>/tablesindex-trash.tsv
-- and accounts for every page of every source volume in In/<year>/. A page passes when it is either

  * whole, in exactly one place: one table file, or the trash; or
  * cut: one or more table files each hold a cut-out part of it (provenance
    "cut": true), and at most one leftover part is in the trash.

Anything else fails: a page in no output, a page whole in two places, or a
page both whole somewhere and cut somewhere else. Exit status 1 on failure,
so a run that breaks the rule cannot pass silently. Decided 2026-10-17.
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import layout  # noqa: E402
from lib.patterns import PDF_EXT, REPORTS_DIR  # noqa: E402


def owners(year, out_root=ROOT):
    """{(source file name, pdf page): [(kind, where)]}"""
    found = defaultdict(list)
    for pdf in sorted(layout.pdf_dir(year, out_root).rglob(f"*{year}V*{PDF_EXT}")):
        meta = PdfReader(pdf).metadata or {}
        try:
            record = json.loads(meta.get("/Subject") or "")
        except ValueError:
            found[("?", 0)].append(("no-provenance", pdf.name))
            continue
        for src in record.get("sources", []):
            a, b = src["pdf_pages"]
            kind = "cut" if src.get("cut") else "whole"
            for page in range(a, b + 1):
                found[(src["file"], page)].append((kind, record["table"]))
    trash = layout.index_trash_tsv(year, out_root)
    if trash.exists():
        with open(trash, encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                kind = "leftover" if row["reason"] == "cut-leftover" else "trash"
                for page in range(int(row["pdf_from"]), int(row["pdf_to"]) + 1):
                    found[(row["source_file"], page)].append((kind, "TRASH"))
    return found


def verdict(entries):
    """None when the page obeys the rule, else a short reason."""
    kinds = [kind for kind, _ in entries]
    whole = kinds.count("whole") + kinds.count("trash")
    cut, left = kinds.count("cut"), kinds.count("leftover")
    if not kinds:
        return "in no output"
    if "no-provenance" in kinds:
        return "a file without provenance"
    if whole == 1 and cut == 0 and left == 0:
        return None
    if whole == 0 and cut >= 1 and left <= 1:
        return None
    return "in " + ", ".join(f"{k}:{w}" for k, w in entries)


def main(year):
    found = owners(year)
    problems, pages = [], 0
    for pdf in sorted(Path(ROOT, REPORTS_DIR, str(year)).glob("*.pdf")):
        count = len(PdfReader(str(pdf)).pages)
        pages += count
        for page in range(1, count + 1):
            reason = verdict(found.get((pdf.name, page), []))
            if reason:
                problems.append(f"{pdf.name} p{page}: {reason}")
    stray = [k for k in found if k[1] == 0]
    for key in stray:
        problems.append(f"{found[key]}")
    if problems:
        print(f"PAGE CHECK FAILED for {year}: {len(problems)} problem(s) "
              f"over {pages} source pages")
        for line in problems[:40]:
            print("  " + line)
        return 1
    cut_pages = sum(1 for v in found.values() if any(k == "cut" for k, _ in v))
    print(f"page check {year}: OK - all {pages} source pages in exactly one place "
          f"({cut_pages} of them split into cut parts)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
