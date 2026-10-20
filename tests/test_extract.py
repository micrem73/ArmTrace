"""The pilot tables of 2025 read as printed, and their printed totals agree.

Read from the sources directly (the pages are given here, as the table index
records them), so the test does not need `Out/`. MAE A1 (537 pages, ~1 min)
is left to `ExtractTables.py`; the four here cover the other record shapes.
"""
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from lib import extract
from lib.source import SourcePage

ROOT = Path(__file__).resolve().parent.parent
VOL1, VOL2 = "2025_LXVII_n4_VOLUME_I.pdf", "2025_LXVII_n4_VOLUME_II.pdf"


def read(volume, first, last, printed_first, key, clip=None):
    doc = pymupdf.open(ROOT / "In" / "2025" / volume)
    pages = [SourcePage(doc, p, printed_first + p - first, clip)
             for p in range(first, last + 1)]
    return extract.read_table("2025", key, pages=pages)


def statuses(result, key):
    return sorted(c[3] for c in extract.checks(result, extract.specs()[key]))


@pytest.mark.parametrize("year", ["2025"])
def test_dogane_m1_cover_then_rows_to_its_total(year, sources):
    r = read(VOL2, 858, 867, 1896, "DOG/A1C2/M1")
    assert r.pages == {"cover": 1, "header": 9} and not r.issues
    data = [x for x in r.rows if x["kind"] == "data"]
    assert len(data) == 200
    assert data[0]["cells"] == ["A.C.S.A. STEEL FORGINGS S.P.A.", "8", "738.648,73", "0,00"]
    assert statuses(r, "DOG/A1C2/M1") == ["PASS"]


@pytest.mark.parametrize("year", ["2025"])
def test_mef_ee_continuations_and_credit_notes_add_up(year, sources):
    """41 pages; continuation pages redraw the grid elsewhere; credit notes
    print `-€`. Both printed totals must agree to the cent."""
    r = read(VOL2, 287, 327, 1325, "MEF/EE")
    assert r.pages == {"header": 1, "continuation": 40} and not r.issues
    assert sum(1 for x in r.rows if x["kind"] == "data") == 3981
    assert statuses(r, "MEF/EE") == ["PASS", "PASS"]


@pytest.mark.parametrize("year", ["2025"])
def test_difesa_4_one_record_per_programme_and_its_note(year, sources):
    r = read(VOL2, 254, 258, 1292, "DIFESA/4")
    assert [x["cells"][0] for x in r.rows] == [str(n) for n in range(1, 27)]
    assert r.rows[0]["cells"] == ["1", "ATHENA FIDUS", "SATELLITE KA/EHF", "ITALIA, FRANCIA.",
                                  "TELESPAZIO, THALES ALENIA SPACE ITALIA."]
    assert r.notes and r.notes[0][2].startswith("1   Leonardo SpA") and not r.issues
    # Words in separate spans that touch on one line are one word (found by
    # the importer agent: `THALES IT ALIA.`, `MBDA ITALIA .`).
    cells = " | ".join(c for x in r.rows for c in x["cells"])
    assert "THALES ITALIA" in cells and " ." not in cells


@pytest.mark.parametrize("year", ["2025"])
def test_t2_wrapped_labels_years_and_footnote(year, sources):
    r = read(VOL1, 27, 27, 23, "MAE/T2", clip=[0.0, 295.84, 595.28, 513.41])
    assert [x["cells"][0] for x in r.rows] == [
        "Valore autorizzazioni individuali esportazione (Mrd €)",
        "Esportazioni verso Paesi UE-NATO (%)",     # as printed: no spaces
        "Esportazioni verso Paesi extra UE-NATO (%)",
        "Programmi intergovernativi (%)", "Numero di autorizzazioni",
        "Numero Paesi di destinazione (*)"]
    assert r.rows[0]["cells"][1:] == ["3,65", "3,831", "4,767", "6,451", "7,721"]
    assert list(r.rows[0]["year_of"].values()) == [2021, 2022, 2023, 2024, 2025]
    assert extract.parse("years", "3,831")[0] == Decimal("3.831")
    assert r.notes[0][2].startswith("(*)") and not r.issues


@pytest.mark.parametrize("year", ["2025"])
def test_mae_a1_customs_value_printed_low_stays_on_its_line(year, sources):
    """A1 p181 prints GEM ELETTRONICA's `105.500,00` customs value 3.7 pt
    below the rest of its authorisation, still inside that line's height.
    Read as a line of its own, the authorisation looked customs-free and
    its EURO subtotal came out 105.500 too high."""
    r = read(VOL1, 184, 185, 180, "MAE/A1")
    data = [x for x in r.rows if x["kind"] == "data"]
    line = next(x for x in data if x["cells"][1] == "105.500,00")
    assert line["cells"][3] == "105.500,00"
    assert not [x for x in data if [t for t in x["cells"] if t] == ["105.500,00"]]
    assert ("subtotal p185 euro (GEM ELETTRONICA  S.r.l.)", "PASS") in [
        (c[0], c[3]) for c in extract.checks(r, extract.specs()["MAE/A1"])]


@pytest.mark.parametrize("year", ["2025"])
def test_mef_ii_company_subtotals_and_a_last_page_drawn_in_pieces(year, sources):
    """MEF II closes each company with `<Ditta> Totale`, a subtotal. Its last
    page (PDF 427) redraws the grid narrower, the inner walls in short pieces
    between merged cells: still a continuation, not a summary."""
    pages = [SourcePage(pymupdf.open(ROOT / "In" / "2025" / VOL2), p, pp)
             for p, pp in ((393, 1431), (427, 1465))]
    r = extract.read_table("2025", "MEF/II", pages=pages)
    assert r.pages == {"header": 1, "continuation": 1}
    # Pages 394-426 are skipped here, so p393's last company reaches WASS's
    # lines on p427: the subtotal names another company, and that is reported.
    assert r.issues == [(427, "carry", "ditta 'ASE S.p.A.' carried into a group "
                                       "closed by 'WASS SUBMARINE SYSTEMS SRL Totale'")]
    subtotals = [x for x in r.rows if x["kind"] == "subtotal"]
    assert subtotals[0]["cells"][0] == "A.C.S.A. STEEL FORGINGS S.P.A. Totale"
    # `Ditta` is printed on a company's first line only; every line under it
    # is that company's (maintainer, 2026-10-20): the name is carried down.
    beni = next(x for x in r.rows if x["cells"][3] == "Beni e Servizi non soggetti a 185/90")
    assert beni["cells"][0] == "A.L.A. S.p.A."
    assert all(x["cells"][0] for x in r.rows if x["kind"] == "data")
    got = {c[0]: c[3] for c in extract.checks(r, extract.specs()["MEF/II"])}
    assert got["subtotal p393 totale (A.L.A. S.p.A.)"] == "PASS"
    assert got["subtotal p427 totale (WEGER WALTER G.M.B.H. SRL)"] == "PASS"


@pytest.mark.parametrize("year", ["2025"])
def test_mae_a2_totals_and_the_share_printed_under_them(year, sources):
    r = read(VOL1, 607, 611, 603, "MAE/A2")
    assert sum(1 for x in r.rows if x["kind"] == "data") == 159 and not r.issues
    assert r.notes[0][2] == "(pari a 3,43 %) (pari a 7,48 %)"
    assert statuses(r, "MAE/A2") == ["PASS"] * 4 + ["ROUNDING"]


@pytest.mark.parametrize("year", ["2025"])
def test_tables_extracted_apart_and_merged_give_the_full_run(year, sources, tmp_path):
    """import-year extracts each table into its own folder (`--out`), in
    parallel, then assembles the year (`--merge`): the dump must be the one a
    single run writes, byte for byte."""
    import subprocess
    import sys
    if not (ROOT / "Out" / year / "tablesindex.sqlite").exists():
        pytest.skip(f"no table index for {year}: run_year.py {year}")
    def run(*args):
        subprocess.run([sys.executable, str(ROOT / "ExtractTables.py"), year, *args],
                       cwd=ROOT, check=True, capture_output=True)
    keys = ["MAE/T2", "DIFESA/4"]
    run(*sum((["--only", k] for k in keys), []), "--out", str(tmp_path / "full"))
    for k in keys:
        run("--only", k, "--out", str(tmp_path / k.replace("/", "_")))
    run("--merge", *(str(tmp_path / k.replace("/", "_")) for k in keys),
        "--out", str(tmp_path / "merged"))
    dump = f"armtrace-{year}.sql"
    assert (tmp_path / "merged" / dump).read_bytes() == (tmp_path / "full" / dump).read_bytes()


@pytest.mark.parametrize("year", ["2025"])
def test_the_sum_rule_is_in_the_database_and_agrees_with_the_checks(year, sources):
    """A1's EURO adds up to its printed totals only by its rule (customs-value
    lines count nothing, a blank EURO on an EUR line counts its Ammontare);
    summed as printed it gives a twentieth of the total. A query reads the
    rule from `column_def.sum_sql`: run in SQLite over the data rows it must
    give what the checks compute."""
    import sqlite3

    import ExtractTables as E
    r = read(VOL1, 184, 185, 180, "MAE/A1")
    spec = extract.specs()["MAE/A1"]
    db = sqlite3.connect(":memory:")
    db.executescript(E.SHARED)
    E.create(db, "mae_a1", spec)
    rows, _ = E.records(year, r, spec)
    db.executemany(f"INSERT INTO mae_a1 VALUES ({','.join('?' * len(rows[0]))})", rows)
    for col in (c for c in spec.printed if c.sum):
        expr = col.sum_sql()
        got = db.execute(f"SELECT round(total({expr}), 2) FROM mae_a1 "
                         "WHERE row_kind='data'").fetchone()[0]
        want = sum(extract._effective(x, col, spec) for x in r.rows if x["kind"] == "data")
        assert Decimal(str(got)) == want, (col.key, expr)
    assert extract.specs()["MAE/A1"].printed[2].sum_sql() == (
        "CASE WHEN val_fini_dog_raw IS NOT NULL THEN 0 "
        "ELSE COALESCE(euro, CASE WHEN valuta = 'EUR' THEN ammontare END, 0) END")


@pytest.mark.parametrize("year", ["2025"])
def test_a_wrapped_description_that_looks_like_a_value_stays_in_its_cell(year, sources):
    """DOG M p609 wraps `... HF ANTENNA (CLIN` / `5)`: `5)` is shaped like a
    number, and was read as a row of its own (13 in M, 45 in MAE A1: `5,56`,
    `07282001`). Found by the importer eval. A value in a text column does not
    make a line a record."""
    r = read(VOL2, 609, 609, 1647, "DOG/A1C2/M")
    data = [x for x in r.rows if x["kind"] == "data"]
    assert not [x for x in data if not x["cells"][0] and not x["cells"][1]]
    assert any(x["cells"][4].endswith("HF ANTENNA (CLIN 5)") for x in data)
    assert any(x["cells"][4].endswith("(CLIN 11 & 12)") for x in data)


@pytest.mark.parametrize("year", ["2025"])
def test_mae_a1_summary_labels_whole_with_their_section(year, sources):
    """A1's summary page (p606), found by the importer eval: a label printed
    on three lines is one label; `Totali parziali per tipo valuta:` heads the
    currency lines under it; a percentage is not the amount beside it."""
    doc = pymupdf.open(ROOT / "In" / "2025" / VOL1)
    r = extract.read_table("2025", "MAE/A1",
                           pages=[SourcePage(doc, 70, 66), SourcePage(doc, 606, 602)])
    labels = {label: raw for _p, _pp, label, raw in r.totals}
    assert labels["Di cui relativi a trasformazione in definitive di operazioni "
                  "temporanee relative a Programmi Intergovernativi"] == "0,00"
    assert labels["Di cui relativi a Programmi di Cooperazione / Missioni "
                  "Internazionali (%)"] == "7,48"
    assert labels["Totali parziali per tipo valuta"] == "1.552.753,27"     # no currency
    assert labels["Totali parziali per tipo valuta: USD"] == "373.003.985,50"
