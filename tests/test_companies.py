"""Company ids: only spelling is matched automatically; the rest goes to the
maintainer (plan.md Phase 3 step 6)."""
from lib import companies as C


def test_spelling_folds_to_one_name():
    for a, b in [("AERO SEKUR S.p..A.", "AERO SEKUR SPA"),
                 ("CPM ELETTRONICA  SRL", "C.P.M. ELETTRONICA S.R.L."),
                 ("M.E.S. S.P.A. MECCANICA PER L ELETTRONICA",
                  "M.E.S. S.P.A. MECCANICA PER L'ELETTRONICA"),
                 ("VIRTUALABS SOCIETA' A RESPONSABILITA' LIMITATA", "VIRTUALABS S.R.L."),
                 ("IVECO - OTO MELARA Società Consortile a.r.l.",
                  "IVECO - OTO MELARA SOCIETÀ CONSORTILE A.R.L.")]:
        assert C.fold(a) == C.fold(b)
    assert C.fold("BERCELLA SPA") != C.fold("BERCELLA S.R.L.")


def test_only_spelling_is_automatic():
    counts = {"AVIO S.p.A..": 3, "AVIO S.P.A.": 5, "BERCELLA SPA": 1, "BERCELLA S.R.L.": 2,
              "LEONARDO CAE ADVANCED JET TRAINING": 1,
              "LEONARDO CAE ADVANCED JET TRAINING SRL": 4, "PRAVISANI S.P.A.": 1,
              "DOTT. MARIANO PRAVISANI & C. S.r.L.": 1, "FIAMM ENERGY TECHNOLOGY SPA": 1,
              "FIAMM ENERGY TECNOLOGY S.P.A.": 1, "ZF PADOVA SRL": 1}
    spellings, new, review = C.propose(counts, ({}, {}))
    assert not spellings
    assert new == [("AVIO S.P.A.", ["AVIO S.P.A.", "AVIO S.p.A.."]),
                   ("ZF PADOVA SRL", ["ZF PADOVA SRL"])]
    assert sorted(sorted(n for node in c for n in node["printed"]) for c in review) == [
        ["BERCELLA S.R.L.", "BERCELLA SPA"],               # another legal form
        ["DOTT. MARIANO PRAVISANI & C. S.r.L.", "PRAVISANI S.P.A."],     # within
        ["FIAMM ENERGY TECHNOLOGY SPA", "FIAMM ENERGY TECNOLOGY S.P.A."],  # near
        ["LEONARDO CAE ADVANCED JET TRAINING", "LEONARDO CAE ADVANCED JET TRAINING SRL"]]


def test_a_known_company_takes_its_spellings_and_its_candidates():
    known = ({7: "AVIO S.P.A."}, {"AVIO S.P.A.": 7})
    spellings, new, review = C.propose({"AVIO S.p.A..": 1, "GE AVIO S.R.L.": 1}, known)
    assert spellings == [("AVIO S.p.A..", 7)]
    assert not new
    assert [[node["id"] for node in c] for c in review] == [[None, 7]]


def test_a_common_word_alone_links_nothing():
    names = ["ELETTRONICA S.P.A.", "CPM ELETTRONICA SRL", "GEM ELETTRONICA S.R.L.",
             "ELETTRONICA MARITTIMA SRL"]
    _, new, review = C.propose(dict.fromkeys(names, 1), ({}, {}))
    assert not review and len(new) == 4


def test_the_register_warns_where_it_contradicts_the_ids(tmp_path):
    """Difesa A5/2's register number identifies a company: the same number
    under two ids, or one id under two numbers, is a warning for the
    maintainer (2026-10-22), whatever the years."""
    import sqlite3
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import companies as tool
    for year, rows in (("2024", [("00001", 1, "A SRL"), ("00002", 2, "B SPA")]),
                       ("2025", [("00001", 3, "A S.R.L."), ("00004", 2, "B S.P.A."),
                                 ("00005", 5, "C SRL"), ("00005", 5, "C SRL")])):
        (tmp_path / "Out" / year).mkdir(parents=True)
        db = sqlite3.connect(tmp_path / "Out" / year / f"armtrace-{year}.sqlite")
        db.execute("CREATE TABLE difesa_a5_2 (numero_iscrizione, id_ditta, denominazione, year)")
        db.executemany("INSERT INTO difesa_a5_2 VALUES (?,?,?,?)",
                       [r + (int(year),) for r in rows])
        # MEF II groups its rows by `Ditta`: one id as two groups is a doubt.
        db.execute("CREATE TABLE mef_ii (ditta, id_ditta, year)")
        db.executemany("INSERT INTO mef_ii VALUES (?,?,?)",
                       [("B SPA", 2, int(year)), ("B S.R.L.", 2, int(year))])
        db.commit()
    out = tool.witness(tmp_path)
    assert len(out) == 4
    assert out[0].startswith("id 2 is printed as separate groups in MEF/II 2024")
    assert out[2].startswith("register number 00001 is under ids 1, 3")
    assert out[3].startswith("id 2 is under register numbers 00002, 00004")


def test_a_register_number_names_its_company_before_any_name_matching():
    """A renamed company, printed with its register number, keeps its id
    without going to the maintainer (2026-10-22)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import companies as tool
    rows = [("00938", 280, "VALTRO S.r.l.", 2024), ("00938", None, "VALTRO GROUP S.p.A.", 2025),
            ("00001", 1, "A SRL", 2024), ("00001", 2, "A' SRL", 2025),     # two ids: no
            ("00002", None, "B SRL", 2025)]
    names = {"VALTRO S.r.l.": (280, "spelling")}
    tool.by_register(rows, names)
    assert names["VALTRO GROUP S.p.A."] == (280, "register 00938")
    assert "B SRL" not in names and len(names) == 2


def test_a_list_of_companies_is_split_where_the_print_separates_them():
    """Difesa 4's `DITTE ITALIANE PARTECIPANTI`: commas outside brackets, a
    `: ` or a `. ` before a capital (a comma the print left out); asides in
    brackets and a footnote mark are no part of a name (maintainer, 2026-10-24)."""
    assert C.split_list("TELESPAZIO, THALES ALENIA SPACE ITALIA.") == [
        "TELESPAZIO", "THALES ALENIA SPACE ITALIA"]
    assert C.split_list("MBDA ITALIA, LEONARDO COMPANY (compresa ex OTOMELARA), AVIO S.p.A., "
                        "KNDS Amno Italy S.p.A. LINKRA SRL, MI.TEL TELEOPTIX, T.CONSULT.") == [
        "MBDA ITALIA", "LEONARDO COMPANY", "AVIO S.p.A.", "KNDS Amno Italy S.p.A.",
        "LINKRA SRL", "MI.TEL TELEOPTIX", "T.CONSULT"]
    assert C.split_list("LEONARDO (MAJOR SUB CONTRACTORS). Altre aziende italiane partecipanti: "
                        "ERA ELECTRONIC SYSTEM, FIMAC.") == [
        "LEONARDO", "Altre aziende italiane partecipanti", "ERA ELECTRONIC SYSTEM", "FIMAC"]
    assert C.split_list("WASS Submarine Systems (Fincantieri)1.") == ["WASS Submarine Systems"]
    assert C.split_list("MECAER, , MEPIT, TXT E TECH S.R.L..") == ["MECAER", "MEPIT",
                                                                   "TXT E TECH S.R.L."]
    assert C.split_list("PIAGGIO AERO INDUSTRIES (ora BAYKAR PIAGGIO AEROSPACE a far data dal "
                        "30.06.2025), SIRIO PANEL") == ["PIAGGIO AERO INDUSTRIES", "SIRIO PANEL"]
    # A bracket the print never closes (programme 15) runs to the end of its name.
    assert C.split_list("LEONARDO (inquadrata nell’ambito del consorzio IAMCO (International "
                        "Aerospace Management Company) per le necessità della flotta.") == [
        "LEONARDO"]
