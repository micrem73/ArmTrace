#!/usr/bin/env python3
"""The table index of an edition read for its register of companies only.

    python3 tools/register_index.py 2018        one year
    python3 tools/register_index.py --all       every year that needs it

The editions before 2021 have no full run (no split, no trash, no page check),
but their Difesa Annesso 2 is wanted for the register across years
(DECISIONS.md 2026-10-27). This writes `Out/<year>/tablesindex.sqlite` and
`tablesindex.tsv` holding one table, DIFESA/A5/2, its pages declared in
`lib/data/register-pages.tsv`: the SQL side (`lib/source.table_pages`,
`ExtractTables.py --only DIFESA/A5/2`) reads it like any other index. No split
PDF is written; the review renders the source pages.

A year that already has a table index (a full run) is left alone: there that
index decides. The page check is not claimed for these years.
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import layout, register_pages  # noqa: E402
from lib.patterns import REPORTS_DIR  # noqa: E402
from tools.build_index import TABLE_COLUMNS, write_sqlite, write_tsv  # noqa: E402

KEY = "DIFESA/A5/2"
NOTE = "register only (lib/data/register-pages.tsv): no full run, no page check"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def index_rows(year, root=ROOT, sha256=_sha256):
    """(tables, segments) of the register-only index of `year`."""
    r = register_pages.load()[int(year)]
    if r is None:
        raise SystemExit(f"{year}: no register printed (lib/data/register-pages.tsv)")
    year = str(year)
    printed = f"{r.printed_first}-{r.printed_last}"
    segment = {"year": year, "key": KEY, "seq": 1, "source_file": r.volume,
               "sha256": sha256(Path(root, REPORTS_DIR, year, r.volume)),
               "pdf_from": r.pdf_first, "pdf_to": r.pdf_last,
               "printed_from": r.printed_first, "printed_to": r.printed_last,
               "cut": 0, "clip": None}
    table = {"year": year, "key": KEY, "authority": "DIFESA", "article": "A5",
             "code": "2", "status": "ok", "witness": "declared", "file": "",
             "pages": r.pdf_last - r.pdf_first + 1, "printed": printed,
             "bookmarks": "", "tagged_pages": 0, "verify_verdict": "",
             "verify_findings": "", "check_verdict": "", "check_cause": "",
             "action": "", "notes": NOTE,
             "sources": f"{r.volume}:{r.pdf_first}-{r.pdf_last}"}
    return [table], [segment]


def build(year, root=ROOT):
    if layout.index_sqlite(year, root).exists():
        raise SystemExit(f"{year} already has a table index "
                         f"({layout.index_sqlite(year, root)}): it decides")
    tables, segments = index_rows(year, root)
    layout.year_dir(year, root).mkdir(parents=True, exist_ok=True)
    write_sqlite(str(year), tables, segments, [], root)
    write_tsv(layout.index_tsv(year, root), TABLE_COLUMNS, tables)
    return tables


def main(argv):
    if argv == ["--all"]:
        years = [y for y, r in sorted(register_pages.load().items())
                 if r and not layout.index_sqlite(y, ROOT).exists()]
    elif len(argv) == 1 and argv[0].isdigit():
        years = [int(argv[0])]
    else:
        raise SystemExit(__doc__)
    for year in years:
        t = build(year)[0]
        print(f"Out/{year}/tablesindex.sqlite: {t['key']} {t['sources']} "
              f"(printed {t['printed']})")


if __name__ == "__main__":
    main(sys.argv[1:])
