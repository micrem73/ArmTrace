"""tools/build_site.py on a tiny pipeline output: what the explorer is handed."""
import hashlib
import json
import shutil
import sqlite3

import pytest

from tools import build_site

URL = "https://documenti.camera.it/x/INTERO_COM.pdf"
FILE = "2025_LXVII_n4_VOLUME_I.pdf"


def make_year(root, year, table_cols="operatore TEXT, euro DECIMAL, euro_raw TEXT",
              listed=("MAE/A1", "MAE/B4")):
    out = root / "Out" / str(year)
    out.mkdir(parents=True)
    with sqlite3.connect(out / f"armtrace-{year}.sqlite") as db:
        db.executescript(f"""
            CREATE TABLE source_file (year INTEGER, file TEXT, sha256 TEXT);
            CREATE TABLE column_def (sql_table TEXT, table_key TEXT, year INTEGER,
                ordinal INTEGER, col TEXT, type TEXT, label_it TEXT);
            CREATE TABLE mae_a1 (year INTEGER, source_file TEXT, pdf_page INTEGER,
                printed_page INTEGER, row_ord INTEGER, row_kind TEXT, {table_cols});
        """)
        db.execute("INSERT INTO source_file VALUES (?, ?, 'abc')", (year, FILE))
        db.execute("INSERT INTO column_def VALUES ('mae_a1', 'MAE/A1', ?, 1,"
                   " 'operatore', 'TEXT', 'Operatore')", (year,))
        # inserted out of printed order: the build must restore it
        name_col = table_cols.split()[0]
        for ord_, name in ((2, "B"), (1, "A")):
            db.execute("INSERT INTO mae_a1 (year, source_file, pdf_page, printed_page,"
                       f" row_ord, row_kind, {name_col}) VALUES (?, ?, 70, 66, ?, 'data', ?)",
                       (year, FILE, ord_, name))
    with sqlite3.connect(out / "tablesindex.sqlite") as db:
        db.execute("CREATE TABLE tables (year TEXT, key TEXT, authority TEXT, article TEXT,"
                   " code TEXT, status TEXT, pages INTEGER, printed TEXT,"
                   " check_verdict TEXT, notes TEXT)")
        for key in listed:
            db.execute("INSERT INTO tables VALUES (?, ?, 'MAE', '', ?, 'ok', 1, '66-66',"
                       " 'PASS', '')", (str(year), key, key.split("/")[-1]))
    manifest = root / "In" / "manifest.tsv"
    manifest.parent.mkdir(exist_ok=True)
    with open(manifest, "a") as f:
        if not manifest.stat().st_size:
            f.write("# year\tleg\tnum\tvol\tfile\tpages\turl\n")
        f.write(f"{year}\t19\t4\tVOLUME I\t{FILE}\t1048\t{URL}\n")


@pytest.fixture(autouse=True)
def web(tmp_path, monkeypatch):
    """A copy of web/ whose questions fit the tiny database."""
    copy = tmp_path / "web"
    shutil.copytree(build_site.WEB, copy)
    monkeypatch.setattr(build_site, "WEB", copy)
    ask(copy, "SELECT operatore FROM mae_a1")
    return copy


def ask(web, sql, chart=None):
    question = {"id": "q", "gruppo": "g", "titolo": "t", "spiegazione": "s", "sql": [sql]}
    if chart:
        question["grafico"] = chart
    (web / build_site.QUESTIONS).write_text(json.dumps([question]))


def test_rows_catalogue_and_sources(tmp_path):
    make_year(tmp_path, 2025)
    db_path, years = build_site.build(tmp_path, tmp_path / "site")
    assert years == [2025]
    assert (tmp_path / "site" / "index.html").exists()
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT operatore FROM mae_a1").fetchall() == [("A",), ("B",)]
    assert db.execute("SELECT key, sql_table FROM catalogo ORDER BY key").fetchall() == [
        ("MAE/A1", "mae_a1"), ("MAE/B4", None)]
    assert db.execute("SELECT url FROM fonte").fetchall() == [(URL,)]


def test_same_input_same_bytes(tmp_path):
    make_year(tmp_path, 2025)
    digests = {hashlib.sha256(build_site.build(tmp_path, tmp_path / out)[0].read_bytes()
                              ).hexdigest() for out in ("a", "b")}
    assert len(digests) == 1


def test_years_join_into_one_table(tmp_path):
    make_year(tmp_path, 2024)
    make_year(tmp_path, 2025)
    db = sqlite3.connect(build_site.build(tmp_path, tmp_path / "site")[0])
    assert db.execute("SELECT year, count(*) FROM mae_a1 GROUP BY year").fetchall() == [
        (2024, 2), (2025, 2)]


def test_renamed_column_is_refused_not_guessed(tmp_path):
    """A column renamed in another year needs a migration in the extractor."""
    make_year(tmp_path, 2024, table_cols="ditta TEXT, euro DECIMAL, euro_raw TEXT")
    make_year(tmp_path, 2025)
    with pytest.raises(SystemExit, match="mae_a1"):
        build_site.build(tmp_path, tmp_path / "site")


def test_extracted_table_missing_from_list_is_refused(tmp_path):
    make_year(tmp_path, 2025, listed=("MAE/B4",))
    with pytest.raises(SystemExit, match="MAE/A1"):
        build_site.build(tmp_path, tmp_path / "site")


@pytest.mark.parametrize("sql, chart, says", [
    ("SELECT operatore FROM mae_a1 WHERE 0", None, "no rows"),
    ("SELECT nonesuch FROM mae_a1", None, "no such column"),
    ("SELECT operatore FROM mae_a1", {"etichetta": "operatore", "valore": "euro"}, "chart column"),
])
def test_a_question_that_stopped_working_fails_the_build(tmp_path, web, sql, chart, says):
    make_year(tmp_path, 2025)
    ask(web, sql, chart)
    with pytest.raises(SystemExit, match=says):
        build_site.build(tmp_path, tmp_path / "site")


def test_published_questions_are_well_formed():
    """The real questions run in the build; here, only their shape."""
    questions = json.loads((build_site.ROOT / "web" / build_site.QUESTIONS).read_text())
    assert len({d["id"] for d in questions}) == len(questions)
    for d in questions:
        assert {"id", "gruppo", "titolo", "spiegazione", "sql"} <= set(d), d.get("id")
        assert set(d.get("grafico", {})) <= {"etichetta", "valore"}, d["id"]
