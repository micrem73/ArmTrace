"""The year's table list is the plan subagents cut from; it must not move unseen.

Fast (detection only, from the page cache): run it after every detector change.
A diff here is the list of tables to re-cut and re-check.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).parent / "golden"


@pytest.mark.parametrize(
    "year", sorted(p.name.split("-")[0] for p in GOLDEN.glob("*-tables.tsv")))
def test_table_list_unchanged(year, sources, tmp_path):
    out = tmp_path / "tables.tsv"
    subprocess.run(
        [sys.executable, str(ROOT / "Reports2PDFTables.py"), year,
         "--dry-run", "--no-trash", "--tables", str(out)],
        cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    assert out.read_text() == (GOLDEN / f"{year}-tables.tsv").read_text()
