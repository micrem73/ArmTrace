#!/usr/bin/env python3
"""What each table of a year needs next on its way into SQL.

    python3 tools/import_queue.py 2025 [--extract] [--only KEY]
    python3 tools/import_queue.py 2025 --render KEY

The import-year loop (.claude/skills/import-year): one folder per table in
Out/.work/<year>/import/<key>/, holding the table's own database
(`ExtractTables.py --only KEY --out`), its review page, and the verdicts of the
table-importer agent. Actions, in the order a table meets them:

  columns       no agreed columns in lib/data/columns.tsv: an importer drafts
                them into columns-proposal.tsv
  human         a draft waits for the maintainer; or, as in step 1
                (tools/recheck.py): a convention question, a code defect still
                failing after 2 fixes, two importers disagreeing
  extract       agreed, but not extracted yet: run with --extract
  check         extracted, no verdict yet: an importer judges it
  recheck       the table's rows changed since its verdict; verdict cleared
  second-check  the importer doubted itself
  fix           a code defect: the main agent fixes it, test-first
  done          PASS on the current rows

--extract re-reads every agreed table into its folder first (4 at a time),
after a code fix: a table whose rows changed comes back as `recheck`.
--render KEY writes the table's review page and records which rows it shows
(checked.json): the verdict is about those rows.
Writes Out/.work/<year>/import-queue.tsv. Exit 0.
"""
import argparse
import contextlib
import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from lib import extract, layout, source  # noqa: E402
from recheck import decide  # noqa: E402

PARALLEL = 4


def folder_of(year, key):
    return layout.import_dir(year, ROOT) / layout.key_dir(key)


def content(year, key):
    """The table's rows as extracted now: the hash of its own dump, or None."""
    dump = folder_of(year, key) / f"armtrace-{year}.sql"
    return hashlib.sha1(dump.read_bytes()).hexdigest() if dump.exists() else None


def run_extract(year, keys):
    def one(key):
        out = subprocess.run(
            [sys.executable, str(ROOT / "ExtractTables.py"), year, "--only", key,
             "--out", str(folder_of(year, key))],
            cwd=ROOT, capture_output=True, text=True)
        return key, out.stdout.strip().splitlines()[:1], out.stderr.strip()[-300:]
    with ThreadPoolExecutor(PARALLEL) as pool:
        for key, line, err in pool.map(one, keys):
            print(f"  extracted {line[0].strip() if line else key}"
                  + (f"\n    {err}" if err and not line else ""))


def render(year, key, dpi=110):
    """Pages to look at, as PNGs, and what was read from them, as JSON.

    With agreed columns: the table's first, middle and last page and every
    page a FAIL check points to, beside a review page of its rows (checked.json
    records which rows: the verdict is about those). Without: the same sample
    pages and the header as read off the grid, for drafting the columns."""
    import extract_review as review

    from lib import geometry as geo
    from lib import grammar as gra
    folder = folder_of(year, key)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("p*.png"):
        old.unlink()
    db_path = folder / f"armtrace-{year}.sqlite"
    agreed = key in extract.specs() and db_path.exists()
    pages = set(review.sample_pages(year, key))
    out = {"key": key, "year": year, "folder": str(folder.relative_to(ROOT))}
    if agreed:
        db = sqlite3.connect(db_path)
        name = extract.sql_name(key)
        pages |= review.failing_pages(db, name, key)
        has_rows = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
        out.update({
            "db": str(db_path.relative_to(ROOT)), "sql_table": name if has_rows else None,
            "rows": dict(db.execute(f"SELECT row_kind, count(*) FROM {name} GROUP BY 1")
                         ) if has_rows else {},
            "columns": [dict(zip(("col", "type", "printed_label"), r)) for r in db.execute(
                "SELECT col, type, label_it FROM column_def WHERE table_key=? "
                "ORDER BY ordinal", (key,))],
            "checks": dict(db.execute("SELECT status, count(*) FROM check_result "
                                      "WHERE table_key=? GROUP BY 1", (key,))),
            "not_pass": [list(r) for r in db.execute(
                "SELECT check_name, printed, computed, status FROM check_result "
                "WHERE table_key=? AND status!='PASS'", (key,))],
            "issues": [list(r) for r in db.execute(
                "SELECT pdf_page, kind, detail FROM extract_issue WHERE table_key=?", (key,))],
            "notes": [list(r) for r in db.execute(
                "SELECT pdf_page, text FROM table_note WHERE table_key=?", (key,))],
            "printed_totals": [list(r) for r in db.execute(
                "SELECT pdf_page, label, value_raw FROM printed_total WHERE table_key=?",
                (key,))]})
        with contextlib.redirect_stdout(sys.stderr):     # stdout is the JSON
            review.build(year, {key: sorted(pages)}, folder / "review.html", db_path)
        (folder / "checked.json").write_text(json.dumps({"content": content(year, key)}),
                                             encoding="utf-8")
    images = []
    for sp in source.table_pages(year, key):
        if not agreed and sp.pdf_page == min(pages) and "header" not in out:
            frame, words = extract.page_words(sp)
            h = geo.grid_header(sp, words, frame, is_label=gra.is_label_row)
            out["header"] = [c["text"] for c in h["cells"]] if h else None
        if sp.pdf_page in pages:
            png = folder / f"p{sp.pdf_page}.png"
            sp.page.get_pixmap(dpi=dpi, clip=sp.clip).save(png)
            images.append({"pdf_page": sp.pdf_page, "printed_page": sp.printed_page,
                           "png": str(png.relative_to(ROOT))})
    out["images"] = images
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def action_of(year, key, specs):
    folder = folder_of(year, key)
    folder.mkdir(parents=True, exist_ok=True)
    drafted = _read(folder / "verdict.json") or {}
    if drafted.get("mode") == "columns":
        if key in specs:                # approved: the draft's verdict is spent
            (folder / "verdict.json").unlink()
        elif drafted.get("kind") == "code":
            return "fix", "header misread: " + drafted.get("cause", ""), False
    if key not in specs:
        if (folder / "columns-proposal.tsv").exists():
            return "human", "columns drafted, to approve into lib/data/columns.tsv", False
        return "columns", "no agreed columns", False
    current = content(year, key)
    if current is None:
        return "extract", "not extracted", False
    return decide(folder, current)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("year")
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--only", action="append")
    parser.add_argument("--render", metavar="KEY")
    args = parser.parse_args(argv)
    year = args.year
    if args.render:
        render(year, args.render)
        return 0
    specs = extract.specs()
    keys = args.only or source.table_keys(year)
    if args.extract:
        run_extract(year, [k for k in keys if k in specs])
    queue, moved = [], []
    for key in keys:
        action, reason, was_pass = action_of(year, key, specs)
        queue.append((key, action, reason))
        if was_pass:
            moved.append(key)
    if not args.only:
        target = layout.work_year(year, ROOT) / "import-queue.tsv"
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("key\taction\treason\n")
            for key, action, reason in queue:
                fh.write(f"{key}\t{action}\t{reason.replace(chr(9), ' ')}\n")
    counts = {}
    for _k, action, _r in queue:
        counts[action] = counts.get(action, 0) + 1
    print(f"import {year}: " + ", ".join(f"{a} {n}" for a, n in sorted(counts.items())))
    if moved:
        print(f"  changed after a PASS ({len(moved)}), review before committing: "
              + " ".join(moved))
    for key, action, reason in queue:
        if action not in ("done", "columns"):
            print(f"  {action:13} {key}  {reason}"[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
