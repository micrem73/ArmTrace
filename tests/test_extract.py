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


def stored(year, result, key):
    """The table as `ExtractTables.py` stores it, in an in-memory database."""
    import sqlite3

    import ExtractTables as E
    spec = extract.specs()[key]
    db = sqlite3.connect(":memory:")
    db.executescript(E.SHARED)
    name = extract.sql_name(key)
    E.create(db, name, spec)
    rows, _ = E.records(year, result, spec)
    db.executemany(f"INSERT INTO {name} VALUES ({','.join('?' * len(rows[0]))})", rows)
    for i, label in enumerate(result.labels):
        col = spec.printed[min(i, len(spec.printed) - 1)]
        db.execute("INSERT INTO column_def VALUES (?,?,?,?,?,?,?,?)",
                   (name, key, int(year), i, col.key, col.type, label, col.sum_sql()))
    db.executemany("INSERT INTO printed_figure VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   E.figures(year, key, result)[0])
    return db


@pytest.mark.parametrize("year", ["2025"])
@pytest.mark.parametrize("key,volume,first,last,printed,clip", [
    ("MEF/II", VOL2, 393, 395, 1431, None),
    ("MAE/A1", VOL1, 184, 186, 180, None),
    ("MAE/T2", VOL1, 27, 27, 23, [0.0, 295.84, 595.28, 513.41]),     # the year window
    ("DOG/A1C2/M1", VOL2, 858, 867, 1896, None),
])
def test_the_database_alone_rebuilds_the_printed_table(year, sources, key, volume, first,
                                                        last, printed, clip):
    """What a user asking for a table gets: the facts and printed figures, read
    back from the database, rebuild the table exactly as from the pages, and
    the facts table holds no subtotal or total to double-count."""
    from lib import views
    r = read(volume, first, last, printed, key, clip=clip)
    spec = extract.specs()[key]
    db = stored(year, r, key)
    cols = [d[1] for d in db.execute(f"PRAGMA table_info({extract.sql_name(key)})")]
    assert "row_kind" not in cols
    from_pages = views.render(*views.split(r), spec)
    from_db = views.render(*views.from_db(db, key, year), spec)
    assert from_db == from_pages


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
        got = db.execute(f"SELECT round(total({expr}), 2) FROM mae_a1").fetchone()[0]
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


def printed_sequence(result):
    """The table as printed: (kind, cells) per row, a group heading once per
    group (MAE A1 repeats it at the top of each page: page furniture)."""
    out, last = [], None
    for x in result.rows:
        if x["kind"] == "group":
            if x["group"] != last:
                out.append(("group", [x["group"]]))
            last = x["group"]
            continue
        hidden = set(x.get("carried", ())) | set(x.get("copied", ()))
        cells = [("" if i in hidden else c) for i, c in enumerate(x["cells"])]
        out.append((x["kind"], cells))
    return out


def rebuilt_sequence(result, key):
    from lib import views
    facts, figures, summary = views.split(result)
    return [(r["kind"], r["cells"]) for r in views.render(facts, figures, summary,
                                                          extract.specs()[key])
            if r["kind"] != "summary"]


@pytest.mark.parametrize("year", ["2025"])
@pytest.mark.parametrize("key,volume,first,last,printed", [
    ("DOG/A1C2/M1", VOL2, 858, 867, 1896),     # rows, then a total in a value column
    ("MEF/II", VOL2, 393, 395, 1431),          # Ditta on a company's first line, `<Ditta> Totale`
    ("MAE/A1", VOL1, 184, 186, 180),           # operator headings, unlabelled subtotals
])
def test_the_printed_table_is_rebuilt_from_its_facts(year, sources, key, volume, first,
                                                     last, printed):
    """The data rows are the facts; headings, subtotals and totals are layout.
    Rebuilt from the facts and the printed figures, the table is the printed
    one, row by row and cell by cell (maintainer, 2026-10-20)."""
    r = read(volume, first, last, printed, key)
    assert rebuilt_sequence(r, key) == printed_sequence(r)


def test_a_summary_agrees_with_the_table_it_summarises():
    """MAE A2 restates A1 per operator; DOG M1 restates M (maintainer: "when a
    corresponding table exists, data should match"). Checked on the year's
    database; an M1 name may cover several lines, or be cut short in print."""
    import sqlite3
    path = ROOT / "Out" / "2025" / "armtrace-2025.sqlite"
    if not path.exists():
        pytest.skip("no assembled 2025 database: ExtractTables.py 2025 --merge ...")
    from lib import views
    got = views.cross_checks(sqlite3.connect(path), "2025")
    a2 = [c for c in got if c[1] == "MAE/A2"]
    assert len(a2) == 3 * 159 and all(c[5] == "PASS" for c in a2)
    m1 = [c for c in got if c[1] == "DOG/A1C2/M1"]
    assert len(m1) == 190 and all(c[5] == "PASS" for c in m1)
    # M1 prints `CPM ELETTRONICA SRL` twice: its two lines add up to M's one name.
    assert any(c[2].endswith("(CPM ELETTRONICA SRL)") for c in m1)


@pytest.mark.parametrize("year", ["2025"])
def test_two_full_stops_in_a_row_are_printed_not_overprinted(year, sources):
    """MEF II prints `AVIO S.p.A..` and `FERRETTI S.p..A.` (each stop its own
    width on); the overprinted-bold rule (MAE B4, a copy 0.19 pt off) dropped
    the second stop, and the carried name then disagreed with its printed
    subtotal. Found by the importer eval."""
    r = read(VOL2, 393, 398, 1431, "MEF/II")
    names = {x["cells"][0] for x in r.rows if x["kind"] == "data"}
    assert {"AVIO S.p.A..", "FERRETTI S.p..A."} <= names
    assert not [i for i in r.issues if i[1] == "carry"]


def links(result):
    """[((qta, description start), its amount's `ammontare`)] of each item."""
    data = [x for x in result.rows if x["kind"] == "data"]
    by_ord = {x["row_ord"]: x for x in data}
    return [((extract._cell(x, "qta"), extract._cell(x, "materiale_oggetto_del_contratto")[:16]),
             extract._cell(by_ord[x["link"]], "ammontare") if x.get("link") else None)
            for x in data if extract._cell(x, "qta")]


@pytest.mark.parametrize("year", ["2025"])
def test_mae_a1_amounts_cover_their_items_as_the_maintainer_reads_them(year, sources):
    """A1 prints an authorisation's amounts on the left and its items on the
    right, not always side by side (maintainer, 2026-10-21, matching page).
    A.C.S.A. p66: one amount covers the items on its line and under it, up to
    the next amount. CERATIZIT p114: four amounts stacked one line apart, four
    items three lines tall: as many amounts as items, the k-th goes with the
    k-th. A line with an amount and no quantity is the item above, wrapped."""
    acsa = dict(links(read(VOL1, 70, 70, 66, "MAE/A1")))
    assert acsa[("3.550", "P/N 10951089 - F")] == "887.145,00"
    assert acsa[("3.550", "P/N 10951110 - F")] == "887.145,00"
    assert acsa[("20.000", "P/N 0000139-1 PA")] == "2.844.000,00"
    assert acsa[("30.000", "P/N SK-KV-5723-1")] == "2.844.000,00"
    r = read(VOL1, 117, 118, 113, "MAE/A1")
    got = [(q, a) for (q, d), a in links(r) if d.startswith("UTENSILE") and
           a in ("10.656,22", "562,20", "295,17", "1.261,47")]
    assert got == [("300", "10.656,22"), ("2", "562,20"), ("2", "295,17"), ("20", "1.261,47")]
    data = [x for x in r.rows if x["kind"] == "data"]
    assert not [x for x in data if not extract._cell(x, "qta")
                and extract._cell(x, "materiale_oggetto_del_contratto")]
    # The amount keeps its own side: `EUR` stays, so its EURO subtotal adds up.
    assert extract._cell(next(x for x in data if extract._cell(x, "ammontare") == "562,20"),
                         "valuta") == "EUR"
    assert ("subtotal p118 euro (CERATIZIT COMO SPA)", "PASS") in [
        (c[0], c[3]) for c in extract.checks(r, extract.specs()["MAE/A1"])]
    item = next(x for x in data if extract._cell(x, "ammontare") == "10.656,22")
    assert extract._cell(item, "materiale_oggetto_del_contratto").endswith("5,56MM")
    # Stored: every amount once, and each item joins its amount.
    db = stored(year, r, "MAE/A1")
    joined = db.execute(
        "SELECT i.qta_raw, a.ammontare_raw FROM mae_a1 i JOIN mae_a1 a "
        "ON a.year = i.year AND a.row_ord = i.importo "
        "WHERE i.operatore = 'CERATIZIT COMO SPA' AND i.pdf_page = 118 ORDER BY i.row_ord").fetchall()
    assert joined == [("300", "10.656,22"), ("2", "562,20"), ("2", "295,17"), ("20", "1.261,47")]
    # One authorisation, four amounts: every line of it names its dated line.
    units = db.execute(
        "SELECT i.autorizzazione, count(*), count(DISTINCT i.importo), o.data_raw FROM mae_a1 i "
        "JOIN mae_a1 a ON a.year = i.year AND a.row_ord = i.importo "
        "JOIN mae_a1 o ON o.year = i.year AND o.row_ord = i.autorizzazione "
        "WHERE i.operatore = 'CERATIZIT COMO SPA' AND a.ammontare_raw IN "
        "('10.656,22', '562,20', '295,17', '1.261,47') GROUP BY 1").fetchall()
    assert len(units) == 1 and units[0][1:3] == (4, 4) and units[0][3]
    assert not db.execute("SELECT 1 FROM mae_a1 WHERE autorizzazione IS NULL").fetchone()
    acsa = stored(year, read(VOL1, 70, 70, 66, "MAE/A1"), "MAE/A1")
    assert acsa.execute("SELECT count(DISTINCT autorizzazione) FROM mae_a1 "
                        "WHERE operatore LIKE 'A.C.S.A.%'").fetchone() == (2,)


@pytest.mark.parametrize("year", ["2025"])
def test_mae_a1_two_amounts_one_item_and_an_empty_amount(year, sources):
    """The seven authorisations the matching rule could not decide (maintainer,
    2026-10-21). One item, two amount boxes (FABARM p142, the second on a line
    of its own): "two rows, one per amount, keeping the same item description
    for both"; the quantity is not repeated, so it is not counted twice.
    LEONARDO p260 prints a dated item with empty amount boxes: "a new row with
    an amount of 0", stored as 0 with nothing printed (`_raw` NULL)."""
    r = read(VOL1, 146, 146, 142, "MAE/A1")
    data = [x for x in r.rows if x["kind"] == "data"]
    item = next(x for x in data if extract._cell(x, "ammontare") == "20.925,00")
    second = next(x for x in data if extract._cell(x, "ammontare") == "5.000,00")
    for col in ("materiale_oggetto_del_contratto", "tipo", "cat"):
        assert extract._cell(second, col) == extract._cell(item, col) != ""
    assert extract._cell(second, "qta") == ""
    assert (item["link"], second["link"]) == (item["row_ord"], second["row_ord"])
    assert not [i for i in r.issues if i[1] == "link"]
    db = stored(year, r, "MAE/A1")
    flags, = db.execute("SELECT flags FROM mae_a1 WHERE ammontare_raw = '5.000,00'").fetchone()
    assert f"copied from row {item['row_ord']}" in flags
    # As printed, the second line shows its amount alone.
    assert rebuilt_line(r, "5.000,00") == ["EUR", "5.000,00", "", "", "", "", "", "", "", ""]

    r = read(VOL1, 264, 264, 260, "MAE/A1")
    assert not [i for i in r.issues if i[1] == "link"]
    db = stored(year, r, "MAE/A1")
    got = db.execute("SELECT ammontare, ammontare_raw, importo = row_ord, flags FROM mae_a1 "
                     "WHERE materiale_oggetto_del_contratto LIKE '%COVER ACCESS'").fetchone()
    assert got[:3] == (0, None, 1) and got[3].startswith("ammontare 0: amount boxes printed empty")


def rebuilt_line(result, amount):
    """The cells of the rebuilt line printing `amount`, from the database."""
    from lib import views
    key = "MAE/A1"
    db = stored("2025", result, key)
    rows = views.render(*views.from_db(db, key, "2025"), extract.specs()[key])
    return next(x["cells"] for x in rows if x["kind"] == "data" and amount in x["cells"])
