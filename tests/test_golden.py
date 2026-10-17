"""A verified edition must keep producing exactly the tables it was judged on."""
from pathlib import Path

import pytest

from snapshot import snapshot

GOLDEN = Path(__file__).parent / "golden"


@pytest.mark.parametrize(
    "year", sorted(p.stem for p in GOLDEN.glob("*.tsv") if "-" not in p.stem))
def test_edition_unchanged(year, sources):
    expected = (GOLDEN / f"{year}.tsv").read_text()
    assert snapshot(year) == expected
