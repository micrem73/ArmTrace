#!/usr/bin/env python3
"""Score the table importer on rows whose right answer is known.

    python3 tools/eval_importer.py 2025 [--jobs 4] [--only CASE] [--model M] [--keep]

The importer's verdict decides whether a table's rows go into the year's
database, and nothing else tests it: an edit to .claude/agents/table-importer.md
or a new model could pass rows with a line missing. So each case in
tests/evals/importer-<year>.tsv takes a table's correctly extracted rows
(`tools/import_queue.py <year> --extract` first) and, unless its mutation is
`none`, breaks them on the table's first page the way a reading defect would
(see the TSV for the mutations).

Each case runs in its own throwaway copy of the repo: the code and .claude/ as
they are now, In/ linked, the table index, and only that case's table folder.
ExtractTables.py there is a stub, so the importer judges the rows it is given.
The real agent runs headless (`claude --agent table-importer -p`).

A FAIL case is caught by FAIL or UNSURE; a PASS case must get PASS. Writes
Out/.work/<year>/eval-importer/results.tsv and each verdict beside it. Exit 1
when broken rows got PASS. Costs tokens and minutes, so it is not part of
`make check`: run it after a change to the importer, the import-year skill or a
convention, after a model change, and before a new year.
"""
import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import extract, layout  # noqa: E402

TIMEOUT = 1200
STUB = '''#!/usr/bin/env python3
print("eval: the table is already extracted; judge the rows you are given")
'''
PICK = 2        # which data row of the first page a mutation hits (0-based)


def read_tsv(path):
    with open(path, encoding="utf-8") as fh:
        lines = [line for line in fh if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def target_row(db, name, need=None):
    """A data row of the table's first page (with `need` printed), as a dict."""
    first = db.execute(f"SELECT min(pdf_page) FROM {name}").fetchone()[0]
    where = f" AND {need}_raw IS NOT NULL" if need and _has(db, name, f"{need}_raw") \
        else f" AND {need} IS NOT NULL AND {need}!=''" if need else ""
    cur = db.execute(f"SELECT rowid, * FROM {name} WHERE pdf_page=? AND row_kind='data'"
                     f"{where} ORDER BY row_ord", (first,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not rows:
        raise SystemExit(f"{name}: no data row on page {first} with {need}")
    return rows[min(PICK, len(rows) - 1)], rows


def _has(db, name, col):
    return any(r[1] == col for r in db.execute(f"PRAGMA table_info({name})"))


def misread(text):
    """A plausible misreading: one digit changed, or one word replaced."""
    for i, ch in enumerate(text):
        if ch.isdigit():
            return text[:i] + str((int(ch) + 3) % 10) + text[i + 1:]
    words = text.split()
    return " ".join(words[:-1] + ["GERMANIA" if words[-1] != "GERMANIA" else "FRANCIA"])


def mutate(db_path, key, mutation):
    if mutation == "none":
        return
    name = extract.sql_name(key)
    kind, _, arg = mutation.partition("=")
    db = sqlite3.connect(db_path)
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({name})")]
    if kind == "label":
        col, text = arg.split(":", 1)
        db.execute("UPDATE column_def SET label_it=? WHERE table_key=? AND col=?",
                   (text, key, col))
    elif kind == "drop-row":
        row, _ = target_row(db, name)
        db.execute(f"DELETE FROM {name} WHERE rowid=?", (row["rowid"],))
    elif kind == "kind":
        row, _ = target_row(db, name)
        db.execute(f"UPDATE {name} SET row_kind=? WHERE rowid=?", (arg, row["rowid"]))
    elif kind == "wrong-value":
        row, _ = target_row(db, name, arg)
        if f"{arg}_raw" in cols:
            raw = misread(row[f"{arg}_raw"])
            value = extract.parse("amount", raw)[0]
            db.execute(f"UPDATE {name} SET {arg}=?, {arg}_raw=? WHERE rowid=?",
                       (str(value) if value is not None else None, raw, row["rowid"]))
        else:
            db.execute(f"UPDATE {name} SET {arg}=? WHERE rowid=?",
                       (misread(row[arg]), row["rowid"]))
    elif kind == "swap-cells":
        a, b = arg.split(",")
        row, _ = target_row(db, name, a)
        pairs = [(a, b)] + ([(f"{a}_raw", f"{b}_raw")] if f"{a}_raw" in cols else [])
        for x, y in pairs:
            db.execute(f"UPDATE {name} SET {x}=?, {y}=? WHERE rowid=?",
                       (row[y], row[x], row["rowid"]))
    elif kind == "split-row":
        row, _ = target_row(db, name, arg)
        moved = [c for c in (arg, f"{arg}_raw") if c in cols]
        db.execute(f"UPDATE {name} SET {', '.join(c + '=NULL' for c in moved)} "
                   "WHERE rowid=?", (row["rowid"],))
        new = {c: None for c in cols}
        new.update({c: row[c] for c in ("year", "source_file", "pdf_page", "printed_page")
                    if c in cols})
        new.update({c: row[c] for c in moved}, row_kind="data", row_ord=row["row_ord"] + 0.5)
        if "operatore" in cols:
            new["operatore"] = row["operatore"]
        db.execute(f"INSERT INTO {name} ({', '.join(cols)}) VALUES "
                   f"({', '.join('?' * len(cols))})", [new[c] for c in cols])
    elif kind == "merge-rows":
        row, rows = target_row(db, name)
        nxt = rows[rows.index(row) + 1] if rows.index(row) + 1 < len(rows) else rows[0]
        for c in cols:
            if c.endswith("_raw") or c in ("year", "source_file", "pdf_page", "printed_page",
                                          "row_ord", "row_kind", "flags"):
                continue
            if isinstance(row[c], str) and isinstance(nxt[c], str) and not c + "_raw" in cols:
                db.execute(f"UPDATE {name} SET {c}=? WHERE rowid=?",
                           (f"{row[c]} {nxt[c]}", row["rowid"]))
        db.execute(f"DELETE FROM {name} WHERE rowid=?", (nxt["rowid"],))
    else:
        raise SystemExit(f"unknown mutation {mutation!r}")
    db.commit()
    db.close()


def make_tree(year, case, work):
    tree = work / case["case"]
    shutil.copytree(ROOT, tree, symlinks=True, ignore=shutil.ignore_patterns(
        ".git", "Out", "In", "__pycache__", ".pytest_cache"))
    (tree / "In").symlink_to(ROOT / "In")
    (tree / "ExtractTables.py").write_text(STUB)
    index = layout.index_sqlite(year, tree)
    index.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(layout.index_sqlite(year, ROOT), index)
    src = layout.import_dir(year, ROOT) / layout.key_dir(case["key"])
    dst = layout.import_dir(year, tree) / layout.key_dir(case["key"])
    dst.mkdir(parents=True)
    for name in (f"armtrace-{year}.sqlite", f"armtrace-{year}.sql"):
        shutil.copyfile(src / name, dst / name)
    mutate(dst / f"armtrace-{year}.sqlite", case["key"], case["mutation"])
    return tree, dst


def run_case(year, case, work, model):
    tree, folder = make_tree(year, case, work)
    prompt = f"Year: {year}. Table key: {case['key']}. Mode: check."
    cmd = ["claude", "-p", prompt, "--agent", "table-importer",
           "--allowedTools", "Bash,Read,Write", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    result = {"seconds": "", "cost": ""}
    try:
        done = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                              timeout=TIMEOUT)
        info = json.loads(done.stdout or "{}")
        result["seconds"] = round(info.get("duration_ms", 0) / 1000)
        result["cost"] = round(info.get("total_cost_usd", 0), 3)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    try:
        verdict = json.loads((folder / "verdict.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        verdict = {}
    return case, verdict, result


def outcome(expect, got):
    if got not in ("PASS", "FAIL", "UNSURE"):
        return "error"
    if expect == "PASS":
        return "ok" if got == "PASS" else "false-alarm"
    return "MISSED" if got == "PASS" else "ok"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("year")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", action="append", default=[], help="case id")
    parser.add_argument("--model", default="")
    parser.add_argument("--keep", action="store_true",
                        help="keep the scratch trees (path printed)")
    args = parser.parse_args()

    cases = read_tsv(ROOT / "tests" / "evals" / f"importer-{args.year}.tsv")
    if args.only:
        cases = [c for c in cases if c["case"] in args.only]
    missing = [c["key"] for c in cases if not (
        layout.import_dir(args.year, ROOT) / layout.key_dir(c["key"])
        / f"armtrace-{args.year}.sqlite").exists()]
    if missing:
        raise SystemExit(f"not extracted: {' '.join(sorted(set(missing)))}; run "
                         f"python3 tools/import_queue.py {args.year} --extract")
    work = Path(tempfile.mkdtemp(prefix=f"armtrace-eval-importer-{args.year}-"))
    out = layout.work_year(args.year, ROOT) / "eval-importer"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    results = []
    try:
        print(f"{len(cases)} case(s), {args.jobs} at a time, in {work}", flush=True)
        with ThreadPoolExecutor(args.jobs) as pool:
            futures = [pool.submit(run_case, args.year, c, work, args.model) for c in cases]
            for future in futures:
                case, verdict, cost = future.result()
                got = verdict.get("verdict", "none")
                failed = ",".join(k for k, v in verdict.get("checks", {}).items()
                                  if v.get("result") in ("fail", "unreadable"))
                row = {"case": case["case"], "key": case["key"],
                       "mutation": case["mutation"], "expect": case["expect"], "got": got,
                       "kind": verdict.get("kind") or "", "checks": failed,
                       "outcome": outcome(case["expect"], got), **cost}
                results.append(row)
                (out / f"{case['case']}.json").write_text(
                    json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  {row['outcome']:<11} {row['case']:<14} {row['expect']} -> "
                      f"{got} {row['kind']} {failed}", flush=True)
    finally:
        if args.keep:
            print(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    agent = ROOT / ".claude" / "agents" / "table-importer.md"
    with open(out / "results.tsv", "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# table-importer.md sha1 "
                 f"{hashlib.sha1(agent.read_bytes()).hexdigest()[:12]}"
                 f" model {args.model or 'default'}\n")
        writer = csv.DictWriter(fh, fieldnames=list(results[0]), delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)

    count = {k: sum(r["outcome"] == k for r in results)
             for k in ("ok", "MISSED", "false-alarm", "error")}
    wrong = [r for r in results if r["expect"] == "FAIL"]
    cost = sum(r["cost"] for r in results if r["cost"] != "")
    print(f"caught {sum(r['outcome'] == 'ok' for r in wrong)}/{len(wrong)} broken tables; "
          f"missed {count['MISSED']}; false alarms {count['false-alarm']}/"
          f"{len(results) - len(wrong)}; errors {count['error']}; "
          f"cost ${cost:.2f} -> {out / 'results.tsv'}")
    return 1 if count["MISSED"] or count["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
