"""Where every output lives. The one place that knows the Out/ tree.

    Out/
      <year>/                          what a person or the SQL extractor uses
        PDF/<authority>/[<article>/]<code><year>V<vol>P<page>.PDF
        TRASH/<volume>_TRASH.PDF
        tablesindex.sqlite             handover to SQL extraction
        tablesindex.tsv                the same tables, as text
        tablesindex-trash.tsv          the same trash runs, as text
        review.html                    the one page a person opens
      .work/                           regenerable, never read by people
        cache/                         page text, keyed by source bytes
        verify/                        VerifyTables reports and cache
        <year>/manifest.jsonl          detector internals
        <year>/tables.tsv              step 1's table list
        <year>/trash.tsv               step 1's trash runs
        <year>/checks/<key>/           checker images and verdict.json

Decided 2026-10-17 (DECISIONS.md). Paths are built from an out root -- the
directory that contains Out/ -- so a scratch run with --out <dir> keeps the
same shape under <dir>/Out/.
"""
from pathlib import Path

OUT = "Out"
WORK = ".work"


def out_dir(root="."):
    return Path(root, OUT)


def year_dir(year, root="."):
    return Path(root, OUT, str(year))


def pdf_dir(year, root="."):
    return year_dir(year, root) / "PDF"


def trash_dir(year, root="."):
    return year_dir(year, root) / "TRASH"


def index_sqlite(year, root="."):
    return year_dir(year, root) / "tablesindex.sqlite"


def index_tsv(year, root="."):
    return year_dir(year, root) / "tablesindex.tsv"


def index_trash_tsv(year, root="."):
    return year_dir(year, root) / "tablesindex-trash.tsv"


def review_html(year, root="."):
    return year_dir(year, root) / "review.html"


def work_dir(root="."):
    return Path(root, OUT, WORK)


def cache_dir(root="."):
    return work_dir(root) / "cache"


def verify_dir(root="."):
    return work_dir(root) / "verify"


def work_year(year, root="."):
    return work_dir(root) / str(year)


def manifest_path(year, root="."):
    return work_year(year, root) / "manifest.jsonl"


def tables_tsv(year, root="."):
    return work_year(year, root) / "tables.tsv"


def trash_tsv(year, root="."):
    return work_year(year, root) / "trash.tsv"


def checks_dir(year, root="."):
    return work_year(year, root) / "checks"


def import_dir(year, root="."):
    """One folder per table: its own database, rendered pages, verdicts."""
    return work_year(year, root) / "import"


def key_dir(key):
    """'DOG/A1C2/N' -> 'DOG_A1C2_N', the folder name of one table's checks."""
    return key.replace("/", "_")


def year_pdf_roots(root="."):
    """[(year, Out/<year>/PDF)] for every year present."""
    base = out_dir(root)
    if not base.is_dir():
        return []
    return [(d.name, d / "PDF") for d in sorted(base.iterdir())
            if d.is_dir() and d.name.isdigit() and (d / "PDF").is_dir()]
