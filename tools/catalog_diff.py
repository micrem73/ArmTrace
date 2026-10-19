#!/usr/bin/env python3
"""What changed in the tables' shapes between two catalogued years.

    python3 tools/catalog_diff.py 2024 2025
        -> Out/.work/CATALOG/diff-2024-2025.md

Reads only `Out/.work/CATALOG/catalog.sqlite` (CatalogueTables.py). A table is
matched on (authority, article, code). For each one it reports: present in one
year only, grammar changed, and the column signature side by side, so that a
renamed column shows as a rename (same position, different label) and not as a
loss plus a gain. A year printed in a label is compared relative to the
edition (`Anno 2025` in 2025 and `Anno 2024` in 2024 are both `Anno Y`): the
relation exhibits name their columns after the last five years, and the
same layout would otherwise differ in every edition. This is the input to the column keys of the SQL tables
(DECISIONS.md 2026-10-19); it decides nothing itself.
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import layout  # noqa: E402

CATALOG = layout.work_dir() / "CATALOG" / "catalog.sqlite"


def key_of(authority, article, code):
    return "/".join(p for p in (authority, article, code) if p)


def load(db, year):
    tables = {}
    for row in db.execute(
            "SELECT authority, article, code, pages, orientation, grammar,"
            " n_columns, extractable, extract_reason FROM table_year"
            " WHERE year=?", (int(year),)):
        tables[key_of(*row[:3])] = dict(
            zip(("pages", "orientation", "grammar", "n_columns", "extractable",
                 "reason"), row[3:]))
    for authority, article, code, ordinal, label, path in db.execute(
            "SELECT authority, article, code, ordinal, label, header_path"
            " FROM column_def WHERE year=? ORDER BY ordinal", (int(year),)):
        tables[key_of(authority, article, code)].setdefault(
            "columns", []).append(relative(path or label or "", year))
    return tables


YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def relative(label, year):
    """`Anno 2023` in the 2025 edition -> `Anno Y-2`; a 2-digit year in a date
    range (`01/01/25`) is left as printed."""
    def sub(m):
        k = int(year) - int(m.group())
        return "Y" if k == 0 else f"Y{-k:+d}"
    return YEAR.sub(sub, label)


def fold(label):
    return " ".join(label.lower().replace("'", " ").split())


def diff(old_year, new_year):
    db = sqlite3.connect(CATALOG)
    old, new = load(db, old_year), load(db, new_year)
    db.close()
    out = [f"# Table shapes, {old_year} → {new_year}", ""]
    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))
    both = sorted(set(old) & set(new))
    same = [k for k in both
            if old[k]["grammar"] == new[k]["grammar"]
            and [fold(c) for c in old[k].get("columns", [])]
            == [fold(c) for c in new[k].get("columns", [])]]
    out += [f"{len(both)} tables in both years, {len(same)} with the same grammar "
            f"and the same columns; {len(only_old)} only in {old_year}, "
            f"{len(only_new)} only in {new_year}.", ""]
    for title, keys, side in ((f"Only in {old_year}", only_old, old),
                              (f"Only in {new_year}", only_new, new)):
        if keys:
            out += [f"## {title}", ""]
            out += [f"- `{k}` ({side[k]['pages']} pp, {side[k]['grammar']}): "
                    + " | ".join(side[k].get("columns", [])) for k in keys]
            out.append("")
    changed = [k for k in both if k not in same]
    if changed:
        out += ["## Changed", ""]
    for k in changed:
        a, b = old[k], new[k]
        out.append(f"### `{k}`")
        if a["grammar"] != b["grammar"]:
            out.append(f"- grammar: {a['grammar']} → {b['grammar']}")
        ca, cb = a.get("columns", []), b.get("columns", [])
        out.append("")
        out.append(f"| # | {old_year} | {new_year} | |")
        out.append("|---|---|---|---|")
        for i in range(max(len(ca), len(cb))):
            x = ca[i] if i < len(ca) else ""
            y = cb[i] if i < len(cb) else ""
            mark = "" if fold(x) == fold(y) else ("added" if not x else
                                                  "dropped" if not y else
                                                  "differs")
            out.append(f"| {i} | {x} | {y} | {mark} |")
        out.append("")
    path = CATALOG.parent / f"diff-{old_year}-{new_year}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"{len(both)} in both, {len(same)} unchanged, {len(changed)} changed, "
          f"{len(only_old)} only {old_year}, {len(only_new)} only {new_year}")
    print(f"wrote {path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: tools/catalog_diff.py <old year> <new year>")
    diff(sys.argv[1], sys.argv[2])
