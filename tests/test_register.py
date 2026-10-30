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


@pytest.mark.parametrize("year", ["2018"])
def test_a_scanned_register_page_reads_through_geometry(year, sources):
    """2018 prints the register as a scan under a noisy text layer (`5` for
    item 6, `0076` for 00769). The OCR page stands in for a SourcePage: its
    words come out in printed lines, each carrying tesseract's confidence."""
    import pymupdf

    from lib import extract, ocr
    from lib import geometry as geo
    assert ocr.reads(2018, "DIFESA/A5/2") and not ocr.reads(2025, "DIFESA/A5/2")
    assert not ocr.reads(2018, "DIFESA/4")
    doc = pymupdf.open(f"In/{year}/2018_LXVII_n2_VOLUME_I.pdf")
    page = ocr.OcrPage(doc, 2018, 729, 725)
    _frame, words = extract.page_words(page)
    lines = [" ".join(w.text for w in sorted(b, key=lambda w: w.u0))
             for b in geo.row_bands(words)]
    assert "1 AVIOSPACE SRL 00750" in lines
    assert "20 ROSA SPA 00769" in lines
    assert "LIMITATA 00771" in lines        # item 22's second line, as printed
    # `&` read as `&Z`: kept as read, for the importer to see against the image.
    assert "9 TECNOLOGIE INDUSTRIALI &ZAERONAUTICHE SRL 00758" in lines
    assert "25 POGGIPOLINI SRL 00774" in lines
    assert all(0 <= ocr.confidence(w) <= 100 for w in words)


def _register(year):
    """The rows read from `year`'s register pages, by register number and event,
    with the heading checks."""
    from lib import extract, source
    key = "DIFESA/A5/2"
    r = extract.read_table(str(year), key, pages=source.table_pages(year, key))
    order = [c.key for c in extract.specs()[key].printed]
    rows = [dict(zip(order, x["cells"]), notes=x.get("notes", []))
            for x in r.rows if x["kind"] == "data"]
    checks = sorted(c[3] for c in extract.checks(r, extract.specs()[key]))
    return r, rows, checks


@pytest.mark.parametrize("year, counts", [
    ("2016", (36, 0, 13)),
    ("2017", (40, 0, 13)),
    ("2018", (25, 0, 9)),
    ("2019", (31, 0, 18)),
    ("2020", (21, 0, 15)),      # the heading says 16, the narrative 15: 15 printed
    ("2021", (40, 0, 15)),
    ("2022", (23, 0, 14)),
    ("2023", (37, 2, 15)),
])
def test_every_list_year_meets_its_counts(year, sources, counts):
    """2020-2023 print 2025's list (`SOCIETA' ISCRITTE 2021 (40):`, numbered
    items, `Motivazione:`), as scans read by OCR. 2016-2019 print the count
    over the names (`Società (36)`) and the cancellations in columns: name,
    reason, number, date. Every count is met, every row read by OCR is
    flagged, nothing is left outside an item. A list printed `NESSUNA` has
    no rows."""
    from lib import ocr
    r, rows, checks = _register(year)
    assert not r.issues
    by_event = tuple(sum(1 for x in rows if x[k]) for k in
                     ("data_iscrizione", "data_sospensione", "data_cancellazione"))
    # A row with no date printed carries the edition's year, flagged: count by notes too.
    implied = {k: sum(1 for x in rows if any(n.startswith(k) for n in x["notes"]))
               for k in ("data_iscrizione", "data_sospensione", "data_cancellazione")}
    assert len(rows) == sum(counts), (by_event, implied)
    assert all(any(n.startswith("ocr:") for n in x["notes"]) == ocr.reads(year, "DIFESA/A5/2")
               for x in rows)
    # 2020's CANCELLATE heading counts 16 over 15 items: the print disagrees with itself.
    assert checks == (["FAIL", "PASS"] if year == "2020" else ["PASS"] * len(checks))
    assert all(not x["denominazione"].endswith("Nr.") for x in rows)


@pytest.mark.parametrize("year", ["2021"])
def test_2021_reads_its_cancellations_in_printed_order(year, sources):
    """2021's scan binds the cancellations' second page (items 8-15, p824)
    before the first (the heading and items 1-7, p825); the page map gives the
    reading order. The signature stamp under the last list is no company."""
    _r, rows, _checks = _register(year)
    gone = [x for x in rows if x["data_cancellazione"]]
    assert [x["numero_iscrizione"] for x in gone[:2]] == ["00710", "00770"]
    st_protect = gone[6]
    assert (st_protect["denominazione"], st_protect["numero_iscrizione"]) == (
        "ST PROTECT S.p.A.", "00746")
    assert "item number not read: taken as 7" in st_protect["notes"]
    assert gone[-1]["numero_iscrizione"] == "00766"
    assert rows[39]["denominazione"] == "T. MARIOTTI S.p.A."


@pytest.mark.parametrize("year", ["2020"])
def test_2020_is_read_again_not_from_its_ocr_layer(year, sources):
    """2020's text layer is an old OCR (`PANZERAEVENTI SRL`, `6/08/2020`):
    tesseract reads the page better."""
    _r, rows, _checks = _register(year)
    names = {x["numero_iscrizione"]: x["denominazione"] for x in rows}
    assert names["00808"] == "PANZERA EVENTI SRL"
    assert names["00807"] == "MANUFACTURING PROCESS SPECIFICATION SRL"
    # Item 8's reason goes on at the top of p720, under the page's furniture.
    praxair = next(x for x in rows if x["numero_iscrizione"] == "00707")
    assert praxair["motivazione"].endswith("al RN. e la relativa documentazione.")
    # Printed with a one-digit day.
    assert next(x for x in rows if x["numero_iscrizione"] == "00545")[
        "data_cancellazione"] == "3/09/2020"


@pytest.mark.parametrize("year", ["2016", "2017", "2018", "2019"])
def test_cancellations_in_columns_keep_name_and_reason_apart(year, sources):
    """2016-2019 print each cancellation as a block: the name (over one or more
    lines) beside the reason, the number and date on the right, the reason's
    first line often above the item's number. A value is placed by its column,
    each block one item."""
    _r, rows, _checks = _register(year)
    gone = {x["numero_iscrizione"]: x for x in rows if x["data_cancellazione"]}
    expect = {
        "2016": ("00544", "SELEX ES MUAS SPA", "08/02/2016",
                 "CANCELLAZIONE CHIESTA DALLA SOCIETA' A SEGUITO DI FUSIONE IN \"SELEX ES SPA\""),
        "2017": ("00594", "SELEX ELECTRONIC SYSTEMS S.P.A. (SELEX ES S.P.A. O SES S.P.A.)",
                 "24/07/2017", "CANCELLAZIONE CHIESTA DALLA SOCIETA'"),
        # The scan cuts the year's last digit: kept as read, never completed.
        "2018": ("00740", "NAVALTECH ITALIA SRL", "28/05/201:",
                 "LA SOC. HA CHIESTO LA CANCELLAZIONE PER FALLIMENTO"),
        # `209`: so printed (the maintainer: 14/10/2019, corrections.tsv).
        "2019": ("00509", "FAREM S.R.L.", "14/10/209",
                 "La Soc. nonostante il sollecito scritto, non ha presentato sei mesi prima "
                 "della scadenza del triennio di validità, ai sensi dell’art. 130 del D.P.R. "
                 "15 marzo 2010, n. 90, la domanda di rinnovo dell’iscrizione al R.N.I. e la "
                 "relativa documentazione."),
    }[year]
    row = gone[expect[0]]
    assert (row["denominazione"], row["data_cancellazione"], row["motivazione"]) == expect[1:]
    if year == "2016":
        # Item 8's number stands centred by its two-line name.
        assert gone["00496"]["denominazione"] == "S.I.R.M.I. Soc. Ita Rappr. Montaggi Ind. SRL"
    if year == "2019":
        # A justified reason runs close to the numbers: its last word stays in it.
        assert gone["00552"]["motivazione"].endswith("(iscritta al RNI al n. 265).")
        assert not any(n.startswith("numero_iscrizione") for n in gone["00552"]["notes"])
    if year == "2018":
        # OCR reads GEMELLI's date as a speck: kept as read, for a person to see.
        assert gone["00473"]["data_cancellazione"] == "e"


@pytest.mark.parametrize("year", ["2022"])
def test_a_reason_read_without_its_colon_is_still_the_reason(year, sources):
    """OCR reads 2022's fourth `Motivazione:` as `Motivazione`."""
    _r, rows, _checks = _register(year)
    row = next(x for x in rows if x["numero_iscrizione"] == "00807")
    assert row["denominazione"] == "MANUFACTURING PROCESS SPECIFICATION S.r.l."
    assert row["motivazione"].startswith("la Società non ha versato")


@pytest.mark.parametrize("year", ["2015"])
def test_a_ruled_scan_reads_cell_by_cell(year, sources):
    """2015 prints three ruled grids, scanned. Its rules are found in the image
    and each cell read alone (read with its row, `DATA CANC.` came back `GANO`):
    every agreed column, the reason under each cancellation, and an empty
    suspension table (`//` in every cell) gives no row."""
    r, rows, _checks = _register(year)
    assert not r.issues
    enrolled = [x for x in rows if x["data_iscrizione"] and not x["data_cancellazione"]]
    gone = [x for x in rows if x["data_cancellazione"]]
    assert (len(enrolled), len(gone), len(rows)) == (22, 5, 27)
    first = enrolled[0]
    assert {k: first[k] for k in ("numero_iscrizione", "denominazione", "verbale",
                                  "data_verbale", "data_iscrizione", "protocollo",
                                  "data_protocollo")} == {
        "numero_iscrizione": "00652", "denominazione": "B-MAX", "verbale": "01/2015",
        "data_verbale": "23/02/2015", "data_iscrizione": "30/01/2015",
        "protocollo": "0006345", "data_protocollo": "30/01/2015"}
    assert next(x for x in enrolled if x["numero_iscrizione"] == "00662")[
        "denominazione"] == "CANEPA & CAMPI"
    bremach = next(x for x in gone if x["numero_iscrizione"] == "00581")
    assert (bremach["sede_legale"], bremach["cap"], bremach["citta"],
            bremach["data_iscrizione"], bremach["data_cancellazione"]) == (
        "VIA CREMONA N. 139", "25124", "BRESCIA", "10/07/2012", "05/10/2015")
    assert bremach["motivazione"].startswith("NON HA PRESENTATO LA DOMANDA DI RINNOVO")
    assert all(any(n.startswith("ocr:") for n in x["notes"]) for x in rows)


@pytest.mark.parametrize("year", ["2018"])
def test_2018s_cut_dates_are_stored_as_the_maintainer_read_them(year, sources):
    """2018 p731's scan cuts each cancellation date's last digit; OCR reads
    `09/04/201:`, one `09/04/2011`, two not at all (`e`, `Ualazei`). The
    maintainer read the page: the year is 2018 (corrections.tsv, for 2018 only:
    `09/04/2011` may be a true date of 2011's register). `_raw` keeps the reading."""
    from lib import extract, source
    from tests.test_extract import stored
    key = "DIFESA/A5/2"
    db = stored(year, extract.read_table(year, key, pages=source.table_pages(year, key)), key)
    got = {n: (d, raw) for n, d, raw in db.execute(
        "SELECT numero_iscrizione, data_cancellazione, data_cancellazione_raw "
        "FROM difesa_a5_2 WHERE data_cancellazione_raw IS NOT NULL")}
    assert got["00652"] == ("2018-04-09", "09/04/2011")
    assert got["00612"] == ("2018-04-09", "09/04/201:")
    assert got["00473"] == ("2018-05-28", "e")
    assert got["00591"] == ("2018-10-08", "Ualazei")
    assert len(got) == 9 and all(d.startswith("2018-") for d, _raw in got.values())


@pytest.mark.parametrize("year", ["2018"])
def test_an_event_dated_outside_the_edition_stops_the_run(year, sources):
    """A report lists the events of its own year only (maintainer, 2026-10-30):
    an enrolment, suspension or cancellation dated another year is a misreading
    (2018 p731: OCR's `09/04/2011` for B-MAX). The check FAILs, so
    ExtractTables exits 1, and names the row. A cancellation's printed
    enrolment date (the grid years) is no event of the year."""
    import ExtractTables as E
    from lib import extract, source
    key = "DIFESA/A5/2"
    spec = extract.specs()[key]
    r = extract.read_table(year, key, pages=source.table_pages(year, key))
    check, issues = E.event_years(year, r, spec)
    assert check[3] == "PASS" and not issues       # B-MAX corrected by the maintainer
    at = [c.key for c in r.columns].index("data_cancellazione")
    bmax = next(x for x in r.rows if x["cells"][at] == "09/04/2011")
    bmax["cells"][at] = "09/04/2001"                # a reading no correction covers
    check, issues = E.event_years(year, r, spec)
    assert check[3] == "FAIL"
    assert any("00652" in detail and "2001" in detail for _p, _kind, detail in issues)


@pytest.mark.parametrize("year", ["2014"])
def test_boxed_cancellations_read_whole(year, sources):
    """2014 boxes each cancellation apart, its walls one row high; each page
    repeats the header; the `Motivazione` row is the box's last, walls or not;
    a narrow column wraps a figure (`0042` over `1`, `19/01/200` over `6`)."""
    import ExtractTables as E
    from lib import extract
    r, rows, _checks = _register(year)
    assert not r.issues
    assert E.event_years(year, r, extract.specs()["DIFESA/A5/2"])[0][3] == "PASS"
    assert not any(x["denominazione"].startswith("SOCIET") for x in rows)   # no header row
    gone = {x["denominazione"]: x for x in rows if x["data_cancellazione"]}
    frasa = gone["IMPRESA COSTRUZIONI FRASA S.R.L."]
    assert (frasa["numero_iscrizione"], frasa["data_cancellazione"]) == ("00503", "24/11/2014")
    assert frasa["motivazione"].startswith("non ha versato il contributo")
    security = gone["SECURITY SYSTEM S.R.L. UNIPERSONALE"]
    assert (security["data_iscrizione"], security["data_cancellazione"]) == (
        "19/01/2006", "24/11/2014")
    # OCR reads the wrapped `1` as `I`: kept as read, noted.
    assert security["numero_iscrizione"] == "0042I"
    assert "numero_iscrizione: '0042I' is not five digits" in security["notes"]
