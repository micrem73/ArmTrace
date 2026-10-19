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


@pytest.mark.parametrize("year", ["2025"])
def test_t2_wrapped_labels_years_and_footnote(year, sources):
    r = read(VOL1, 27, 27, 23, "MAE/T2", clip=[0.0, 295.84, 595.28, 513.41])
    assert [x["cells"][0] for x in r.rows] == [
        "Valore autorizzazioni individuali esportazione (Mrd €)",
        "Esportazioni verso Paesi UE - NATO (%)",
        "Esportazioni verso Paesi extra UE - NATO (%)",
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
    assert r.pages == {"header": 1, "continuation": 1} and not r.issues
    subtotals = [x for x in r.rows if x["kind"] == "subtotal"]
    assert subtotals[0]["cells"][0] == "A.C.S.A. STEEL FORGINGS S.P.A. Totale"
    got = {c[0]: c[3] for c in extract.checks(r, extract.specs()["MEF/II"])}
    assert got["subtotal p393 totale (A.L.A. S.p.A.)"] == "PASS"
    assert got["subtotal p427 totale (WEGER WALTER G.M.B.H. SRL)"] == "PASS"


@pytest.mark.parametrize("year", ["2025"])
def test_mae_a2_totals_and_the_share_printed_under_them(year, sources):
    r = read(VOL1, 607, 611, 603, "MAE/A2")
    assert sum(1 for x in r.rows if x["kind"] == "data") == 159 and not r.issues
    assert r.notes[0][2] == "(pari a 3,43 %) (pari a 7,48 %)"
    assert statuses(r, "MAE/A2") == ["PASS"] * 4 + ["ROUNDING"]
