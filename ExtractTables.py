#!/usr/bin/env python3
"""ExtractTables -- the tables of one year, read in the source volumes, into SQL.

    python3 ExtractTables.py 2025                  every table with agreed columns
    python3 ExtractTables.py 2025 --only MEF/EE    one table (repeatable)

        ->  Out/<year>/armtrace-<year>.sqlite
            Out/<year>/armtrace-<year>.sql        the deliverable: a plain dump

One SQL table per printed table, as printed (DECISIONS.md 2026-10-19): the
columns agreed in `lib/data/columns.tsv`, each value beside its printed text
(`<col>_raw`), and on every row its year, source file, PDF and printed page,
order and kind (data, group, subtotal, total). A table that prints a year
window (T2) has one row per printed row and year.

Shared tables:
    source_file    each source volume and its SHA-256
    column_def     each SQL column, its type, and the label printed in that year
    printed_total  totals a table prints outside its grid (MAE A1's summary page)
    table_note     notes printed under a table
    extract_issue  what could not be read with confidence, with its reason
    check_result   every printed total and subtotal against the rows (D7)

Amounts are parsed exactly (Decimal) and written to the dump as exact decimal
literals; SQLite stores them as REAL, so a SUM inside SQLite is a float sum.
The printed text is always in `<col>_raw`. Exit status 1 when a table is
refused, or a check FAILs: the run says so, it does not hide it.
"""
import argparse
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import extract, layout, source  # noqa: E402

SQL_TYPE = {"text": "TEXT", "code": "TEXT", "group": "TEXT", "date": "TEXT",
            "amount": "DECIMAL", "number": "DECIMAL"}
META = ["year INTEGER", "source_file TEXT", "pdf_page INTEGER", "printed_page INTEGER",
        "row_ord INTEGER", "row_kind TEXT"]

SHARED = """
CREATE TABLE source_file (year INTEGER, file TEXT, sha256 TEXT, PRIMARY KEY (year, file));
CREATE TABLE column_def (sql_table TEXT, table_key TEXT, year INTEGER, ordinal INTEGER,
    col TEXT, type TEXT, label_it TEXT, PRIMARY KEY (sql_table, year, col));
CREATE TABLE printed_total (year INTEGER, table_key TEXT, pdf_page INTEGER,
    printed_page INTEGER, label TEXT, value DECIMAL, value_raw TEXT);
CREATE TABLE table_note (year INTEGER, table_key TEXT, pdf_page INTEGER,
    printed_page INTEGER, text TEXT);
CREATE TABLE extract_issue (year INTEGER, table_key TEXT, pdf_page INTEGER,
    kind TEXT, detail TEXT);
CREATE TABLE check_result (year INTEGER, table_key TEXT, check_name TEXT,
    printed TEXT, computed DECIMAL, status TEXT);
"""


def columns_of(spec):
    """[(sql column, type)] of one table, in printed order."""
    out = [(spec.group.key, "group")] if spec.group else []
    for col in spec.printed:
        if col.type == "years":
            out += [("anno", "year"), (col.key, "amount")]
        else:
            out.append((col.key, col.type))
    return out


def create(db, name, spec):
    cols = list(META)
    for col, kind in columns_of(spec):
        if kind == "year":
            cols.append(f"{col} INTEGER")
        elif kind in ("amount", "number"):
            cols += [f"{col} DECIMAL", f"{col}_raw TEXT"]
        elif kind == "date":
            cols += [f"{col} TEXT", f"{col}_raw TEXT"]
        else:
            cols.append(f"{col} TEXT")
    cols.append("flags TEXT")
    db.execute(f"CREATE TABLE {name} ({', '.join(cols)})")


def records(year, result, spec):
    """The SQL rows of one table, and the issues its values raise."""
    rows, issues = [], []
    for r in result.rows:
        meta = [int(year), r["source_file"], r["pdf_page"], r["printed_page"],
                r["row_ord"], r["kind"]]
        values, flags = {}, []
        if spec.group:
            values[spec.group.key] = r["group"]
        years = []
        for i, col in enumerate(r["order"]):
            raw = r["cells"][i] if r["kind"] != "group" else ""
            if col.type == "years":
                years.append((r["year_of"][i], raw))
                continue
            value, problem = extract.parse(col.type, raw)
            if r["kind"] == "total" and extract.TOTAL_LEAD.match(raw):
                problem = None      # the total's label, printed in a value column (DOG M1)
            if problem:
                flags.append(f"{col.key}: {problem}")
                issues.append((r["pdf_page"], problem, f"{col.key} {raw!r}"))
            if col.type in ("amount", "number", "date"):
                values[col.key] = str(value) if value is not None else None
                values[f"{col.key}_raw"] = raw or None
            else:
                values[col.key] = value
        flag = "; ".join(flags) or None
        if years:
            for anno, raw in years:
                value, problem = extract.parse("years", raw)
                if problem:
                    issues.append((r["pdf_page"], problem, f"{anno} {raw!r}"))
                rows.append(meta + [values.get(c) for c, k in columns_of(spec)
                                    if k not in ("year", "amount")]
                            + [anno, str(value) if value is not None else None,
                               raw or None, flag])
        else:
            out = list(meta)
            for col, kind in columns_of(spec):
                out.append(values.get(col))
                if kind in ("amount", "number", "date"):
                    out.append(values.get(f"{col}_raw"))
            rows.append(out + [flag])
    return rows, issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("year")
    parser.add_argument("--only", action="append", default=None)
    args = parser.parse_args(argv)
    year = args.year
    specs = extract.specs()
    keys = args.only or [k for k in source.table_keys(year) if k in specs]

    out_dir = layout.year_dir(year)
    path = out_dir / f"armtrace-{year}.sqlite"
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    if args.only and path.exists():
        # `--only` replaces those tables and keeps the rest, as in step 1.
        shutil.copyfile(path, tmp)
        db = sqlite3.connect(tmp)
        for key in keys:
            db.execute(f"DROP TABLE IF EXISTS {extract.sql_name(key)}")
            for table in ("column_def", "printed_total", "table_note",
                          "extract_issue", "check_result"):
                db.execute(f"DELETE FROM {table} WHERE table_key=?", (key,))
        db.execute("DELETE FROM source_file")
    else:
        db = sqlite3.connect(tmp)
        db.executescript(SHARED)
    files, failed, refused = {}, 0, []
    for key in keys:
        spec = specs[key]
        name = extract.sql_name(key)
        try:
            result = extract.read_table(year, key, spec)
        except extract.Refused as exc:
            refused.append((key, str(exc)))
            db.execute("INSERT INTO extract_issue VALUES (?,?,?,?,?)",
                       (int(year), key, None, "refused", str(exc)))
            print(f"  {key:<14} REFUSED {exc}")
            continue
        create(db, name, spec)
        rows, issues = records(year, result, spec)
        width = len(rows[0]) if rows else 0
        db.executemany(f"INSERT INTO {name} VALUES ({','.join('?' * width)})", rows)
        for seg in source.segments(year, key):
            files[seg["source_file"]] = seg["sha256"]
        for i, label in enumerate(result.labels):
            col = [c for c in spec.printed][min(i, len(spec.printed) - 1)]
            db.execute("INSERT OR IGNORE INTO column_def VALUES (?,?,?,?,?,?,?)",
                       (name, key, int(year), i, col.key, col.type, label))
        db.executemany("INSERT INTO printed_total VALUES (?,?,?,?,?,?,?)",
                       [(int(year), key, p, pp, label,
                         str(v) if (v := extract.parse("amount", raw)[0]) is not None else None,
                         raw) for p, pp, label, raw in result.totals])
        db.executemany("INSERT INTO table_note VALUES (?,?,?,?,?)",
                       [(int(year), key, p, pp, text) for p, pp, text in result.notes])
        db.executemany("INSERT INTO extract_issue VALUES (?,?,?,?,?)",
                       [(int(year), key, p, kind, detail)
                        for p, kind, detail in result.issues + issues])
        results = extract.checks(result, spec)
        db.executemany("INSERT INTO check_result VALUES (?,?,?,?,?,?)",
                       [(int(year), key, name_, printed,
                         str(c) if c is not None else None, status)
                        for name_, printed, c, status in results])
        tally = {s: sum(1 for x in results if x[3] == s)
                 for s in ("PASS", "ROUNDING", "FAIL", "UNCHECKED")}
        failed += tally["FAIL"]
        print(f"  {key:<14} {len(rows):>6} rows  pages {result.pages}  "
              f"issues {len(result.issues) + len(issues)}  checks "
              + " ".join(f"{s} {n}" for s, n in tally.items() if n))
    for (key,) in db.execute("SELECT DISTINCT table_key FROM column_def").fetchall():
        for seg in source.segments(year, key):
            files[seg["source_file"]] = seg["sha256"]
    for name, sha in sorted(files.items()):
        db.execute("INSERT INTO source_file VALUES (?,?,?)", (int(year), name, sha))
    db.commit()
    dump = out_dir / f"armtrace-{year}.sql"
    with open(dump, "w", encoding="utf-8", newline="\n") as fh:
        for line in db.iterdump():
            fh.write(line + "\n")
    db.close()
    tmp.replace(path)
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()[:12]
    print(f"  wrote {path}\n  wrote {dump} (sha256 {digest})")
    return 1 if failed or refused else 0


if __name__ == "__main__":
    sys.exit(main())
