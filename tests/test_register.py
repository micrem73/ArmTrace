"""The register of companies (Difesa A5/2) across every edition: the declared
page map (lib/data/register-pages.tsv) and the register-only table index."""
import pytest

from lib import register_pages


def test_every_edition_since_1991_has_register_pages():
    """1990 has no register (set up in February 1991); every later edition
    prints one, and its pages are a range with the rotated pages inside it."""
    rows = register_pages.load()
    assert set(rows) == set(range(1990, 2026))
    assert rows[1990] is None
    for year in range(1991, 2026):
        r = rows[year]
        assert r.pdf_first <= r.pdf_last, year
        assert r.printed_first > 0, year
        assert r.rotated <= set(range(r.pdf_first, r.pdf_last + 1)), year


def test_printed_pages_run_on_from_the_first_folio():
    """2018 prints folio 725 on pdf page 729: the segment carries both."""
    r = register_pages.load()[2018]
    assert (r.pdf_first, r.pdf_last, r.printed_first, r.printed_last) == (729, 731, 725, 727)


def test_a_register_only_year_indexes_one_table():
    """A year with no full run gets a table index holding only DIFESA/A5/2,
    declared, its pages from the map: the SQL side reads it like any other."""
    from tools.register_index import index_rows
    tables, segments = index_rows(1995, sha256=lambda path: "x")
    assert [t["key"] for t in tables] == ["DIFESA/A5/2"]
    assert tables[0]["witness"] == "declared"
    assert tables[0]["printed"] == "119-135"
    assert [(s["source_file"], s["pdf_from"], s["pdf_to"], s["printed_from"],
             s["printed_to"]) for s in segments] == [
        ("1995_LXVII_n2_DOCUMENTO_UNICO.pdf", 119, 135, 119, 135)]


def test_a_year_with_its_own_index_is_left_alone(tmp_path):
    """Where a full run built the table index, that index decides: the
    register tool does not overwrite it."""
    from tools.register_index import build
    index = tmp_path / "Out" / "2025" / "tablesindex.sqlite"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"")
    with pytest.raises(SystemExit, match="already has a table index"):
        build(2025, root=tmp_path)
