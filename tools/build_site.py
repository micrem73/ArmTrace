#!/usr/bin/env python3
"""Build the public explorer: web/ plus one database, into site/.

    python3 tools/build_site.py [--root DIR] [--out site]

Reads, never writes, what the pipeline published under <root>/Out/:

    Out/<year>/armtrace-<year>.sqlite   rows as printed (SQL extraction)
    Out/<year>/tablesindex.sqlite       every table found (step 1)

and <root>/In/manifest.tsv for the official URL of each source volume. Writes

    site/                 a copy of web/, files addressed by content (stamp)
    site/armtrace.sqlite  the rows of every year, plus
        catalogo          one row per table found, with the SQL table holding it
        fonte             one row per source volume: official URL, SHA-256

A row is traced to its page by (source_file, pdf_page) -> fonte.url#page=N; the
page builds the link, so no column is added to the printed rows. Same inputs
give a byte-identical database: fixed order, no timestamps, VACUUM. Decided
2026-10-19 (DECISIONS.md).
"""
import argparse
import hashlib
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import views  # noqa: E402
from lib.layout import index_sqlite, out_dir  # noqa: E402

WEB = ROOT / "web"
DB_NAME = "armtrace.sqlite"
DATA_DB = re.compile(r"armtrace-(\d{4})\.sqlite$")

SCHEMA = """
CREATE TABLE catalogo (
    year INTEGER, key TEXT, authority TEXT, article TEXT, code TEXT,
    status TEXT, pages INTEGER, printed TEXT, check_verdict TEXT, notes TEXT,
    sql_table TEXT,         -- NULL: found, not yet extracted
    PRIMARY KEY (year, key)
);
CREATE TABLE fonte (
    year INTEGER, file TEXT, volume TEXT, pages INTEGER, url TEXT, sha256 TEXT,
    PRIMARY KEY (year, file)
);
"""


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def data_dbs(root):
    """{year: path} of every extracted year, oldest first."""
    found = {}
    for path in sorted(out_dir(root).glob("*/armtrace-*.sqlite")):
        m = DATA_DB.search(path.name)
        if m and path.parent.name == m.group(1):
            found[int(m.group(1))] = path
    return found


def manifest(root):
    """{(year, file): (volume, pages, url)} from In/manifest.tsv."""
    rows = {}
    with open(Path(root, "In", "manifest.tsv"), newline="") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            year, _leg, _num, vol, file, pages, url = line.rstrip("\n").split("\t")[:7]
            rows[(int(year), file)] = (vol, int(pages), url)
    return rows


def tables_of(db, schema="main"):
    return [name for (name,) in db.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' ORDER BY name")]


def columns(db, table, schema="main"):
    return [r[1] for r in db.execute(f'PRAGMA {schema}.table_info("{table}")')]


def order_by(cols):
    """Rows in printed order where the table says it, else by every column."""
    keys = [c for c in ("year", "source_file", "pdf_page", "row_ord", "table_key")
            if c in cols]
    return ", ".join(f'"{c}"' for c in (keys or cols))


def copy_rows(dst, year, path):
    dst.execute("ATTACH DATABASE ? AS src", (f"file:{path}?mode=ro",))
    try:
        for table in tables_of(dst, "src"):
            have = columns(dst, table)
            cols = columns(dst, table, "src")
            if not have:
                (sql,) = dst.execute(
                    "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone()
                dst.execute(sql)
            elif have != cols:
                # A column renamed or added in another year needs a migration
                # in the extractor (DECISIONS.md 2026-10-19), not a guess here.
                sys.exit(f"{table}: {year} has columns {cols}, earlier years {have}")
            names = ", ".join(f'"{c}"' for c in cols)
            # `company` and `bank` are the same lists in every year (lib/data).
            insert = "INSERT OR IGNORE" if table in ("company", "bank") else "INSERT"
            dst.execute(f'{insert} INTO "{table}" ({names}) SELECT {names} '
                        f'FROM src."{table}" ORDER BY {order_by(cols)}')
        dst.commit()
    finally:
        dst.rollback()  # no-op after commit; else DETACH fails and hides the refusal
        dst.execute("DETACH DATABASE src")


def add_catalogue(dst, root, years):
    held = dict(dst.execute(
        "SELECT DISTINCT year || '|' || table_key, sql_table FROM column_def"))
    for year in years:
        path = index_sqlite(year, root)
        if not path.exists():
            sys.exit(f"{path}: missing; run python3 run_year.py {year}")
        with ro(path) as src:
            rows = src.execute(
                "SELECT year, key, authority, article, code, status, pages, printed,"
                " check_verdict, notes FROM tables ORDER BY key").fetchall()
        dst.executemany(
            "INSERT INTO catalogo VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(int(r[0]), *r[1:], held.get(f"{int(r[0])}|{r[1]}")) for r in rows])
    missing = set(held) - {f"{y}|{k}" for y, k in dst.execute(
        "SELECT year, key FROM catalogo")}
    if missing:
        sys.exit(f"extracted but not in the table list: {sorted(missing)}")


def add_sources(dst, root):
    official = manifest(root)
    rows = dst.execute("SELECT year, file, sha256 FROM source_file "
                       "ORDER BY year, file").fetchall()
    for year, file, sha in rows:
        if (year, file) not in official:
            sys.exit(f"{year} {file}: not in In/manifest.tsv, no official URL")
        vol, pages, url = official[(year, file)]
        dst.execute("INSERT INTO fonte VALUES (?,?,?,?,?,?)",
                    (year, file, vol, pages, url, sha))
    dst.commit()


def build(root, out):
    years = data_dbs(root)
    if not years:
        sys.exit(f"no Out/<year>/armtrace-<year>.sqlite under {Path(root).resolve()}")
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out)
    db_path = out / DB_NAME
    dst = sqlite3.connect(db_path)
    dst.executescript(SCHEMA)
    for year, path in years.items():
        copy_rows(dst, year, path)
    add_catalogue(dst, root, years)
    add_sources(dst, root)
    views.add_register(dst)
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    stamp(out)
    return db_path, sorted(years)


def stamp(out):
    """Address the database, app.js and style.css by content (`name?v=<hash>`).

    Pages lets browsers reuse a file for 10 minutes; a page that kept an older
    database or script after a publish showed columns that no longer exist.
    A new address on every change makes the browser fetch the new copy.
    """
    def version(name):
        return hashlib.sha256((out / name).read_bytes()).hexdigest()[:12]

    def point(name, ref, ver):
        path = out / name
        text = path.read_text()
        if f'"{ref}"' not in text:
            sys.exit(f"web/{name}: no \"{ref}\" to version")
        path.write_text(text.replace(f'"{ref}"', f'"{ref}?v={ver}"'))

    point("app.js", DB_NAME, version(DB_NAME))   # before app.js is hashed below
    for ref in (DB_NAME, "style.css", "app.js"):
        point("index.html", ref, version(ref))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="directory holding Out/ and In/")
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    db_path, years = build(args.root, args.out)
    with ro(db_path) as db:
        n = db.execute("SELECT count(*) FROM catalogo WHERE sql_table IS NOT NULL"
                       ).fetchone()[0]
        total = db.execute("SELECT count(*) FROM catalogo").fetchone()[0]
    size = db_path.stat().st_size / 1e6
    print(f"{db_path}: years {', '.join(map(str, years))}; "
          f"{n} of {total} tables extracted; {size:.1f} MB")


if __name__ == "__main__":
    main()
