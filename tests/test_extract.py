"""The pilot tables of 2025 read as printed, and their printed totals agree.

Read from the sources directly (the pages are given here, as the table index
records them), so the test does not need `Out/`. MAE A1 (537 pages, ~1 min)
is left to `ExtractTables.py`; the four here cover the other record shapes.
"""
import sqlite3
import sys
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
def test_the_caption_over_the_grid_is_kept_as_printed(year, sources):
    """What a table prints above its header is its caption, one line per
    printed line (maintainer, 2026-10-25): Dogane M's says which operation the
    whole table covers, T3's that its columns are five-year periods."""
    from lib import source

    def caption(key):           # the first page, clipped to the table as the run is
        pages = source.table_pages(year, key)[:1]
        return extract.read_table(year, key, pages=pages).caption
    assert caption("DOG/A1C2/M") == [
        "Tipo di operazione: Esportazione Definitiva (EX) TAB. M"]     # one printed line
    assert caption("DIFESA/4") == [
        "MINISTERO DELLA DIFESA – Annesso 4", "TABELLA RIASSUNTIVA DEI",
        "PROGRAMMI DI COPRODUZIONE INTERNAZIONALE 2025"]
    assert caption("MAE/T2") == ["Tabella 2"]


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
    # The share is printed beside its value, in its box: kept in its text
    # (2026-10-24; a note before, when the grid ended above it).
    total = next(x for x in r.rows if x["kind"] == "total")
    assert "265.139.737,31 (pari a 3,43 %)" in total["cells"]
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
    for i, (label, col) in enumerate(zip(result.labels, result.columns)):
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
    ("DIFESA/A5/2", VOL2, 236, 240, 1274, None),     # a year stored, not printed
    ("MAE/T10", VOL1, 46, 46, 42, None),             # measures under each year
    ("MAE/T13", VOL1, 51, 51, 47, None),             # a rotated label over its block
    ("MAE/T20", VOL1, 62, 62, 58, [0.0, 377.04, 595.28, 669.34]),   # two year blocks
    ("DIFESA/A2C6/3B", VOL2, 244, 251, 1282, None),  # once, rowsum, a total box
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
    for row in from_db + from_pages:        # what a row joins is not printed (A1 link test below)
        row.pop("linked", None)
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
    database, per company id (`id_ditta`), not per name as printed: an M1 name
    may cover several lines, be cut short, or be spelled otherwise than in M."""
    import sqlite3
    path = ROOT / "Out" / "2025" / "armtrace-2025.sqlite"
    if not path.exists():
        pytest.skip("no assembled 2025 database: ExtractTables.py 2025 --merge ...")
    from lib import views
    got = views.cross_checks(sqlite3.connect(path), "2025")
    a2 = [c for c in got if c[1] == "MAE/A2"]
    assert len(a2) == 3 * 159 and all(c[5] == "PASS" for c in a2)
    m1 = [c for c in got if c[1] == "DOG/A1C2/M1"]
    # 158: BERCELLA S.R.L. and BERCELLA SPA are two companies (maintainer, 2026-10-25).
    assert len(m1) == 158 and all(c[5] == "PASS" for c in m1)
    # M1 prints `CPM ELETTRONICA SRL` twice and `C.P.M. ELETTRONICA S.R.L.`: one company.
    cpm = [c[2] for c in m1 if "CPM ELETTRONICA SRL" in c[2]]
    assert len(cpm) == 1 and "C.P.M. ELETTRONICA S.R.L." in cpm[0]


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
        "ON a.year = i.year AND a.row_ord = i.riga_importo "
        "WHERE i.operatore = 'CERATIZIT COMO SPA' AND i.pdf_page = 118 ORDER BY i.row_ord").fetchall()
    assert joined == [("300", "10.656,22"), ("2", "562,20"), ("2", "295,17"), ("20", "1.261,47")]
    # One authorisation, four amounts: every line of it names its dated line.
    units = db.execute(
        "SELECT i.riga_autorizzazione, count(*), count(DISTINCT i.riga_importo), o.data_raw FROM mae_a1 i "
        "JOIN mae_a1 a ON a.year = i.year AND a.row_ord = i.riga_importo "
        "JOIN mae_a1 o ON o.year = i.year AND o.row_ord = i.riga_autorizzazione "
        "WHERE i.operatore = 'CERATIZIT COMO SPA' AND a.ammontare_raw IN "
        "('10.656,22', '562,20', '295,17', '1.261,47') GROUP BY 1").fetchall()
    assert len(units) == 1 and units[0][1:3] == (4, 4) and units[0][3]
    assert not db.execute("SELECT 1 FROM mae_a1 WHERE riga_autorizzazione IS NULL").fetchone()
    # The importer judges the links from the rebuilt rows: each item names its amount and date.
    from lib import views
    rebuilt = views.render(*views.from_db(db, "MAE/A1", year), extract.specs()["MAE/A1"])
    items = [x for x in rebuilt if x["kind"] == "data" and x["group"] == "CERATIZIT COMO SPA"
             and x["pdf_page"] == 118 and x["cells"][5] in ("300", "20")]
    assert [x["linked"] for x in items] == ["→ 10.656,22 · 01/12/2025", "→ 1.261,47 · 01/12/2025"]
    acsa = stored(year, read(VOL1, 70, 70, 66, "MAE/A1"), "MAE/A1")
    assert acsa.execute("SELECT count(DISTINCT riga_autorizzazione) FROM mae_a1 "
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
    got = db.execute("SELECT ammontare, ammontare_raw, riga_importo = row_ord, flags FROM mae_a1 "
                     "WHERE materiale_oggetto_del_contratto LIKE '%COVER ACCESS'").fetchone()
    assert got[:3] == (0, None, 1) and got[3].startswith("ammontare 0: amount boxes printed empty")


def rebuilt_line(result, amount):
    """The cells of the rebuilt line printing `amount`, from the database."""
    from lib import views
    key = "MAE/A1"
    db = stored("2025", result, key)
    rows = views.render(*views.from_db(db, key, "2025"), extract.specs()[key])
    return next(x["cells"] for x in rows if x["kind"] == "data" and amount in x["cells"])


@pytest.mark.parametrize("year", ["2025"])
def test_importer_eval_relink_moves_one_item_to_another_amount(year, sources, tmp_path):
    """The eval's `relink` mutation: one item of the first page joins the wrong
    amount (a misplaced unit boundary), and nothing else changes."""
    sys.path.insert(0, str(ROOT / "tools"))
    import eval_importer
    db = stored(year, read(VOL1, 70, 70, 66, "MAE/A1"), "MAE/A1")
    path = tmp_path / "a1.sqlite"
    db.commit()                 # an open transaction makes backup() wait forever
    copy = sqlite3.connect(path)
    db.backup(copy)
    copy.close()
    cols = "row_ord, riga_importo, riga_autorizzazione"
    before = db.execute(f"SELECT {cols} FROM mae_a1 ORDER BY row_ord").fetchall()
    eval_importer.mutate(path, "MAE/A1", "relink")
    after = sqlite3.connect(path).execute(f"SELECT {cols} FROM mae_a1 ORDER BY row_ord").fetchall()
    changed = [(b, a) for b, a in zip(before, after) if b != a]
    assert len(changed) == 1
    (_, importo, _), (_, moved, unit) = changed[0]
    amounts = {r[0] for r in db.execute("SELECT row_ord FROM mae_a1 WHERE ammontare_raw IS NOT NULL")}
    assert moved != importo and moved in amounts
    assert unit == dict((r[0], r[2]) for r in before)[moved]


@pytest.mark.parametrize("year", ["2025"])
def test_columns_render_reads_the_header_past_a_cover_page(year, sources):
    """Drafting columns, the importer gets the header off the first page that
    prints one: Dogane N1's first page is its cover (p871), the grid is on p872."""
    sys.path.insert(0, str(ROOT / "tools"))
    import import_queue
    out = import_queue.render(year, "DOG/A1C2/N1", dpi=20)
    assert out["header"] == ["Denominazione operatore", "Numero di operazioni svolte",
                             "Stato di avanzamento annuale (Euro)",
                             "Utilizzo anni precedenti (Euro)"]


@pytest.mark.parametrize("year", ["2025"])
def test_a_company_name_is_stored_with_its_id_or_null_and_an_issue(year, sources, tmp_path,
                                                                  monkeypatch):
    """`id_ditta` is the id `lib/data/company-names.tsv` gives the printed name;
    a name with none is NULL and one issue per name, never guessed."""
    import ExtractTables as E
    from lib import companies
    monkeypatch.setattr(companies, "LIST", tmp_path / "companies.tsv")
    monkeypatch.setattr(companies, "NAMES", tmp_path / "company-names.tsv")
    companies.write({1: "A.C.S.A. STEEL FORGINGS S.P.A."},
                    {"A.C.S.A. STEEL FORGINGS S.P.A.": (1, "spelling")})
    key = "DOG/A1C2/M1"
    db = stored(year, read(VOL2, 858, 867, 1896, key), key)
    ids = dict(db.execute("SELECT denominazione_operatore, id_ditta FROM dog_a1c2_m1"))
    assert ids["A.C.S.A. STEEL FORGINGS S.P.A."] == 1
    assert sum(1 for v in ids.values() if v is None) == len(ids) - 1
    _, issues = E.records(year, read(VOL2, 858, 858, 1896, key), extract.specs()[key])
    named = [d for _p, kind, d in issues if kind == "company"]
    assert len(named) == len(set(named)) and all("denominazione_operatore" in d for d in named)


@pytest.mark.parametrize("year", ["2025"])
def test_difesa_a5_2_register_lists_by_place_not_text_order(year, sources):
    """Two numbered lists, no grid, after a page of prose (maintainer, 2026-10-22).
    Each value is placed by where it stands: on p1278 the text layer gives
    GEMELLI's number and date after the last reason."""
    key = "DIFESA/A5/2"
    r = read(VOL2, 236, 240, 1274, key)
    rows = {x["cells"][1]: dict(zip([c.key for c in x["order"]], x["cells"]),
                                implied=x.get("implied", {}))
            for x in r.rows if x["kind"] == "data"}
    assert len(rows) == 95 and not r.issues
    assert rows["SITAEL S.p.A."]["numero_iscrizione"] == "00977"
    assert rows["SITAEL S.p.A."]["implied"] == {"data_iscrizione": "2025"}
    assert rows["SOCIETà INGEGNERIA SUPPORTO LOGISTICO INTEGRATO S.r.l. In sigla SISLI S.r.l."][
        "numero_iscrizione"] == "01035"
    gemelli = rows["GEMELLI S.r.l."]
    assert (gemelli["numero_iscrizione"], gemelli["data_cancellazione"]) == ("00832", "07/11/2025")
    assert gemelli["motivazione"] == "la Società è stata incorporata dalla LOGIC S.p.A.."
    assert rows["CANTIERE NAVALE VITTORIA S.p.A."]["numero_iscrizione"] == "00437"
    # p1276 prints `S.r.l` bold and `.` regular, touching: one word (importer, 2026-10-22).
    assert rows["GESTIONE SILO S.r.l."]["numero_iscrizione"] == "00866"
    assert rows["ESPLODENTI SABINO S.p.A."]["numero_iscrizione"] == "00126"
    assert not rows["ESPLODENTI SABINO S.p.A."]["data_iscrizione"]
    assert statuses(r, key) == ["PASS", "PASS"]


@pytest.mark.parametrize("year", ["2025"])
def test_a_two_level_header_gives_each_column_its_label_and_the_one_over_it(year, sources):
    """T10 prints `Anno 2022` over `N. | Valore in € | %`, and `Paese` over both
    rows; Annesso 3B prints `INCASSO` over its years (maintainer, 2026-10-24)."""
    from lib import geometry as geo
    from lib import grammar as gra

    def header(volume, pdf_page):
        doc = pymupdf.open(ROOT / "In" / "2025" / volume)
        sp = SourcePage(doc, pdf_page, 0, None)
        frame, words = extract.page_words(sp)
        h = geo.grid_header(sp, words, frame, is_label=gra.is_label_row)
        return [(c.get("over", ""), c["text"]) for c in h["cells"]]
    t10 = header(VOL1, 46)
    assert t10[:4] == [("", "Paese"), ("Anno 2022", "N."), ("Anno 2022", "Valore in €"),
                       ("Anno 2022", "%")]
    assert len(t10) == 13 and t10[-1] == ("Anno 2025", "%")
    b3 = header(VOL2, 244)
    assert b3[3:8] == [("INCASSO", "2022"), ("INCASSO", "2023"), ("INCASSO", "2024"),
                       ("INCASSO", "2025"), ("INCASSO", "Nel quadriennio (2022-2025)")]


@pytest.mark.parametrize("year", ["2025"])
def test_year_windows_with_several_measures_add_up_year_by_year(year, sources):
    """T10 and T11: `Anno <year>` over its measures; one row per item and year
    (maintainer, 2026-10-24); each year's printed total against its rows."""
    import ExtractTables as E
    t10 = read(VOL1, 46, 46, 42, "MAE/T10")
    data = [x for x in t10.rows if x["kind"] == "data"]
    assert len(data) == 22 and data[0]["cells"][:4] == ["GERMANIA", "115", "149.076.248,68",
                                                         "52,26%"]
    assert statuses(t10, "MAE/T10") == ["PASS"] * 8
    rows, _ = E.records(year, t10, extract.specs()["MAE/T10"])
    turchia = [r for r in rows if "TURCHIA" in r]
    assert len(turchia) == 1 and 2023 in turchia[0]       # printed for 2023 only
    t11 = read(VOL1, 47, 47, 43, "MAE/T11")
    assert len([x for x in t11.rows if x["kind"] == "data"]) == 21
    assert statuses(t11, "MAE/T11") == ["PASS"] * 7 + ["ROUNDING"]


@pytest.mark.parametrize("year", ["2025"])
def test_two_breakdowns_of_one_total_each_checked_on_their_own(year, sources):
    """T13 and T23 print `Categoria (Military List)` and `Tipologia` rotated,
    each in a cell spanning its block (maintainer, 2026-10-24). T13: both add up
    to TOTALE, but its `Numero` total is 1 off in 2022, 2023 and 2025 (A1's
    quantities give the rows' sum: the print is off). T23: the categories add up
    to MATERIALI, the type block to TOTALE."""
    from collections import Counter
    t13 = read(VOL1, 51, 51, 47, "MAE/T13")
    blocks = Counter(x["cells"][0] for x in t13.rows if x["kind"] == "data")
    assert blocks == {"Categoria (Military List)": 22, "Tipologia": 4}
    failed = [c[0] for c in extract.checks(t13, extract.specs()["MAE/T13"]) if c[3] == "FAIL"]
    assert len(failed) == 5 and all(" numero " in c for c in failed)
    t23 = read(VOL1, 66, 66, 62, "MAE/T23")
    assert Counter(statuses(t23, "MAE/T23")) == {"PASS": 13, "ROUNDING": 3}


@pytest.mark.parametrize("year", ["2025"])
def test_t20_ten_countries_are_facts_its_summary_lines_figures(year, sources):
    """T20 prints 2025's top ten and their 2024 values, then `Primi 10 Paesi`,
    `Altri`, `TOTALE` (maintainer, 2026-10-24: figures, not facts). The labels
    stand outside their box, right-aligned; the gap between the year blocks is
    no column. 2024's `Primi 10 Paesi` is 2024's own top ten: UNCHECKED."""
    from collections import Counter
    r = read(VOL1, 62, 62, 58, "MAE/T20", clip=[0.0, 377.04, 595.28, 669.34])   # T21 below
    kinds = Counter(x["kind"] for x in r.rows)
    assert kinds == {"data": 10, "line": 2, "total": 1} and not r.issues
    assert [x["cells"][0] for x in r.rows[-3:]] == ["Primi 10 Paesi", "Altri", "TOTALE"]
    assert Counter(statuses(r, "MAE/T20")) == {"PASS": 5, "ROUNDING": 1, "UNCHECKED": 6}


@pytest.mark.parametrize("year", ["2025"])
def test_annesso_3b_incasso_by_year_importo_once_quadriennio_checked(year, sources):
    """Annesso 3B (maintainer, 2026-10-24): one row per authorisation and
    year; Importo and Residuo on the edition's year only; `Nel quadriennio` a
    printed figure, checked row by row; `TOTALE INCASSO`, a box of its own
    under the last page's grid, checked year by year. The print numbers 180
    twice and skips 42-53 and 365: kept as printed."""
    from collections import Counter
    r = read(VOL2, 244, 251, 1282, "DIFESA/A2C6/3B")
    data = [x for x in r.rows if x["kind"] == "data"]
    assert len(data) == 488 and not r.issues
    assert r.rows[-1]["kind"] == "total" and r.rows[-1]["cells"][0] == "TOTALE INCASSO"
    # `  3 ` + `2.288,00 €`, the second over the first's space: one number.
    assert next(x for x in data if x["cells"][1] == "20191277")["cells"][2] == "32.288,00 €"
    assert Counter(statuses(r, "DIFESA/A2C6/3B")) == {"PASS": 493}
    db = stored(year, r, "DIFESA/A2C6/3B")
    cols = [c[1] for c in db.execute("PRAGMA table_info(difesa_a2c6_3b)")]
    assert "incasso_quadriennio" not in cols
    assert db.execute("SELECT count(*) FROM difesa_a2c6_3b").fetchone()[0] == 488 * 4
    assert db.execute("SELECT anno, importo_autorizzato, incasso_raw FROM difesa_a2c6_3b "
                      "WHERE rif_istanza='20180256' ORDER BY anno").fetchall() == [
        (2022, None, "0,00 €"), (2023, None, "18.335,00 €"), (2024, None, "31.360,00 €"),
        (2025, 1500000, "0,00 €")]
    assert db.execute("SELECT count(*) FROM printed_figure WHERE kind='rowsum'"
                      ).fetchone()[0] == 488


@pytest.mark.parametrize("year", ["2025"])
@pytest.mark.parametrize("key,volume,first,last,printed,note", [
    ("MAE/B4", VOL1, 684, 691, 680, "(*) Esportazioni"),     # a header box with no inner wall
    ("MAE/D", VOL1, 814, 816, 810, "LE AUTORIZZAZIONI"),     # a note where a heading would be
    ("MAE/G1", VOL2, 72, 74, 1110, "LE AUTORIZZAZIONI"),     # a note under the table
    ("MAE/G2", VOL2, 75, 78, 1113, "LE AUTORIZZAZIONI"),     # a summary page, two values a line
    ("MAE/I", VOL2, 114, 116, 1152, "LE AUTORIZZAZIONI"),    # OCEANIA opens its page
])
def test_mae_summaries_by_area_read_whole(year, sources, key, volume, first, last, printed,
                                          note):
    """B4, D, G1, G2, I: an area heading in a labelled column, unlabelled area
    totals (a count and an amount), and a closing box whose `di cui` and
    `Totale ai Fini Doganali` lines no column holds (UNCHECKED, kept). Every
    total is checked; none fails, nothing is outside the table."""
    from collections import Counter
    r = read(volume, first, last, printed, key)
    assert not r.issues and any(n[2].startswith(note) for n in r.notes)
    got = Counter(statuses(r, key))
    assert not got["FAIL"] and got["PASS"] and got["UNCHECKED"]
    groups = [x["group"] for x in r.rows if x["kind"] == "group"]
    assert key == "MAE/G1" or groups[-1] == "OCEANIA"
    # The closing box's `di cui` lines are shares of its totals, of no area: `line`s
    # with no group (importer, 2026-10-24: D filed them under OCEANIA).
    shares = [x for x in r.rows if x["cells"][0].startswith("di cui")]
    assert all(x["kind"] == "line" and x["group"] is None for x in shares)
    # G2 prints them on its summary page: labelled lines, no group either.
    assert shares or any(t[2].startswith("di cui") for t in r.totals)


@pytest.mark.parametrize("year", ["2025"])
def test_f1_header_split_by_its_labels_and_its_summary_page(year, sources):
    """MAE F1 prints `Ammontare` and `EURO` in one header box (the wall between
    them runs round the body's amounts only): split by the agreed labels. Its
    last page, boxed, is its summary, not rows of the grid."""
    a, b = (pymupdf.open(ROOT / "In" / "2025" / v) for v in (VOL1, VOL2))
    pages = [SourcePage(a, p, 1035 + p - 1039) for p in (1039, 1040)] + [SourcePage(b, 66, 1104)]
    r = extract.read_table(year, "MAE/F1", pages=pages)
    assert r.labels[1:3] == ["Ammontare", "EURO"] and r.pages == {"header": 2, "summary": 1}
    aris = next(x for x in r.rows if x["kind"] == "subtotal")
    assert aris["cells"][2:4] == ["4.940.860,63", "0,00"]          # EURO, Valore Fini Dog.
    assert ("Totale Autorizzazioni", "556") in [(t[2], t[3]) for t in r.totals]


@pytest.mark.parametrize("year", ["2025"])
def test_a_list_of_companies_stores_their_ids_in_printed_order(year, sources, tmp_path,
                                                                monkeypatch):
    """Difesa 4's `DITTE ITALIANE PARTECIPANTI` (maintainer, 2026-10-24): the ids
    in printed order, null for a name with no id yet (one issue), a word that
    names no company (id 0) left out, a name standing for two (`34+177`) both."""
    import json

    import ExtractTables as E
    from lib import companies
    monkeypatch.setattr(companies, "LIST", tmp_path / "companies.tsv")
    monkeypatch.setattr(companies, "NAMES", tmp_path / "company-names.tsv")
    companies.write({1: "LEONARDO S.p.A.", 2: "ERA ELECTRONIC SYSTEMS S.r.l.", 3: "CONSORZIO SIGEN"},
                    {"LEONARDO": (1, "maintainer"), "ERA ELECTRONIC SYSTEM": (2, "maintainer"),
                     "Altre aziende italiane partecipanti": (0, "maintainer; not a company"),
                     "CONSORZIO SIGEN LEONARDO": ((3, 1), "maintainer; a comma left out")})
    key = "DIFESA/4"
    r = read(VOL2, 254, 258, 1292, key)
    db = stored(year, r, key)
    (ids,) = db.execute("SELECT id_ditte FROM difesa_4 WHERE ditte_italiane_partecipanti "
                        "LIKE '%Altre aziende%'").fetchone()
    ids = json.loads(ids)
    assert ids[:2] == [1, 2] and len(ids) == 9 and ids.count(None) == 7
    (ids,) = db.execute("SELECT id_ditte FROM difesa_4 WHERE programma = 'ORIZZONTE'").fetchone()
    assert json.loads(ids)[:2] == [3, 1]        # two companies, one printed name
    _, issues = E.records(year, r, extract.specs()[key])
    named = [d for _p, kind, d in issues if kind == "company"]
    assert len(named) == len(set(named)) and not any("Altre aziende" in d for d in named)


@pytest.mark.parametrize("year", ["2025"])
def test_a_short_operator_heading_in_the_margin_is_read(year, sources):
    """MAE F1 p17 (vol. II): `LEE Srl` heads its operator from the margin left
    of `Valuta`, its middle half a point outside the column; read as the
    middle, it was dropped and 40 rows went to LARIMART (importer, 2026-10-24).
    A word beside the grid is the grid's or an issue, never lost."""
    doc = pymupdf.open(ROOT / "In" / "2025" / VOL2)
    r = extract.read_table(year, "MAE/F1", pages=[SourcePage(doc, 16, 1054),
                                                   SourcePage(doc, 17, 1055)])
    groups = [x["group"] for x in r.rows if x["kind"] == "group"]
    assert groups[-2:] == ["LARIMART S.p.A.", "LEE Srl"]
    ugello = next(x for x in r.rows if x["cells"][7].startswith("UGELLO"))
    assert ugello["group"] == "LEE Srl"
