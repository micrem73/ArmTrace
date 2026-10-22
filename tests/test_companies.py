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
