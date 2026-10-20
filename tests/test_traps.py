"""Rules whose breakage looks like correct code. Each was a bug that shipped."""
import ast
from pathlib import Path

import pytest

from lib.engine import OVERRIDES

ROOT = Path(__file__).resolve().parent.parent
SHARED = [ROOT / "VerifyTables.py", ROOT / "run_year.py",
          *sorted((ROOT / "lib").glob("*.py")), *sorted((ROOT / "tools").glob("*.py"))]


def parse(path):
    return ast.parse(path.read_text())


@pytest.mark.parametrize("path", SHARED, ids=lambda p: str(p.relative_to(ROOT)))
def test_shared_code_imports_no_year_script(path):
    """The verifier audits every year by one set of rules, and lib/ is wrong for
    every year at once. Importing a year's detector would judge that year by its
    own opinion, so a bug it shares could no longer be reported."""
    modules = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not {m for m in modules if m.startswith("Reports2PDFTables")}


# Divergences from lib/patterns.py the verifier is allowed, each with its reason.
ALLOWED_COPIES = {
    "STAMP_WORD": "widened to the archive's 'Pag.' and 'Page N of X' stamps",
}


def test_verifier_keeps_no_private_copy_of_patterns():
    """A private copy of a trap is the same bug with a second place to fix it:
    the verifier once kept its own one-digit code tail after lib/ was widened
    to two, and stopped seeing MG10 and MT13."""
    def assigned(path):
        return {t.id for node in parse(path).body if isinstance(node, ast.Assign)
                for t in node.targets if isinstance(t, ast.Name) and t.id.isupper()}
    copies = assigned(ROOT / "VerifyTables.py") & assigned(ROOT / "lib" / "patterns.py")
    assert copies <= ALLOWED_COPIES.keys()


EDITIONS = sorted((ROOT / "editions").glob("y*.py"))


@pytest.mark.parametrize("path", EDITIONS, ids=lambda p: p.name)
def test_edition_replaces_only_declared_functions(path):
    """Each year was once a full copy of the engine, and a fix reached only the
    year it was made in. An edition may redefine an engine function only if
    engine.OVERRIDES names it, so a second copy cannot grow back unseen."""
    def functions(p):
        return {n.name for n in parse(p).body if isinstance(n, ast.FunctionDef)}
    engine = functions(ROOT / "lib" / "engine.py")
    assert not (functions(path) & engine) - set(OVERRIDES)
    assert set(OVERRIDES) <= engine


def test_page_labels_that_contradict_the_folio_are_not_trusted():
    """2024 vol. II labels its pages I-IV, 1, 2, ... but the paper continues vol.
    I's numbering: PDF p55 is labelled 51 and printed 1061 (folio offset +1006).
    Trusting the labels named all 89 vol. II tables 1010 pages low."""
    from lib.emit import printed_page
    labels = ["I", "II", "III", "IV"] + [str(n) for n in range(1, 1257)]
    vol2 = {"page_labels": labels, "folio_offset": 1006}
    assert printed_page(vol2, 55) == 1061
    # Labels that agree with the folio keep winning (2025: all 150 tables).
    agree = {"page_labels": ["i", "ii"] + [str(n) for n in range(3, 100)],
             "folio_offset": 0}
    assert printed_page(agree, 55) == 55


def test_verifier_keys_tables_on_authority_not_code():
    """2024: MAE (vol. I) and the Dogane (vol. II) both print N2, O1, P2. Keyed
    on the code, the verifier held DOG/A1C2/P2 to MAE's pages and MAE's index
    title, failing 20 correct files."""
    from VerifyTables import index_entry, locate_span
    volumes = [
        {"report": "In/2024/v1.pdf", "manifest": {"tables": [
            {"authority": "MAE", "article": "", "code": "P2",
             "start": 1013, "end": 1014, "pages": 2, "source": "header"}]}},
        {"report": "In/2024/v2.pdf", "manifest": {"tables": [
            {"authority": "DOG", "article": "A1C2", "code": "P2",
             "start": 1113, "end": 1115, "pages": 3, "source": "header"}]}},
    ]
    assert locate_span(volumes, "P2", "DOG", "A1C2")[:3] == ("v2", 1113, 1115)
    assert locate_span(volumes, "P2", "MAE", "")[:3] == ("v1", 1013, 1014)
    per_authority = {"P2": {"code": "P2", "title": "Importazione temporanea",
                            "authorities": {"MAE"}}}
    assert index_entry(per_authority, "P2", "MAE")["title"]
    assert index_entry(per_authority, "P2", "DOG") is None


def test_full_run_removes_tables_it_did_not_write(tmp_path):
    """Fixing 2024 vol. II's page numbers wrote 3A2024V2P1061.PDF beside the old
    3A2024V2P51.PDF: 244 files for 153 tables. A run's output must not depend on
    the runs before it."""
    from lib.emit import remove_stale
    keep = tmp_path / "DIFESA" / "A2C6" / "3A2024V2P1061.PDF"
    old = keep.with_name("3A2024V2P51.PDF")
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"%PDF")
    old.write_bytes(b"%PDF")
    assert remove_stale(tmp_path, [str(keep)]) == [str(old)]
    assert keep.exists() and not old.exists()


def test_title_check_reads_what_the_mae_page_prints():
    """2024 MAE: the page prints the title in other inflections ("ESPORTAZIONI
    DEFINITIVE" for "Esportazione definitiva"), or none, only the stamp
    "Pagina 1 di 5 TAB N1". 24 correct cuts failed identity.title."""
    from VerifyTables import Checks, check_title

    def verdict(text, title, code="N1", total=5):
        checks = Checks()
        check_title({1: {"text": text}}, {"title": title}, checks,
                    code=code, total=total)
        return checks.items[-1]["verdict"]

    a3 = "ESPORTAZIONI DEFINITIVE: AUTORIZZAZIONI PER OPERATORI , VALORE DECRESCENTE"
    assert verdict(a3, "Esportazione definitiva per operatori, valore decrescente") == "PASS"
    n1 = "Operatore Licenza Programma\nMAECI -UAMA Pagina 1 di 5 TAB N1\nCamera dei Deputati"
    title = "Relazione analitica delle licenze globali di progetto"
    assert verdict(n1, title) == "SKIP"
    # The stamp must name this file's code and page count, or it is no witness.
    assert verdict(n1, title, code="N2") == "FAIL"
    assert verdict(n1, title, total=4) == "FAIL"


def test_dogane_tab_marker_needs_a_word_boundary():
    """2024 vol. II p465 (MEF Tabella II) prints "MALTA" beside "B.M.A. BUIZZA
    MAZZEI"; extracted as "MALTAB.M.A.", it read as the Dogane "TAB." marker,
    filed p462-465 under DOG art. 1 comma 2 and split II in two: the file kept
    p461 and 136 pages went to the trash."""
    from lib.ontology import classify_page
    assert classify_page("Dipartimento del Tesoro Direzione V\n"
                         "AVIO S.p.A. Totale\nMALTAB.M.A. BUIZZA MAZZEI\n")[0] != "DOG"
    assert classify_page("Tipo di operazione: Trasferimento TAB. MT1\n")[0] == "DOG"


MEF_BLOCK = ("Ministero dell'Economia e delle Finanze \nDipartimento del Tesoro "
             "Direzione V - Ufficio VIII \nOperazioni disciplinate dall'art. 27, legge "
             "09/07/1990, n. 185 - Relazione attività 2024 \n")


def test_mef_relation_exhibits_are_tables_not_trash():
    """2024 vol. II p78-82 (and 2025 p263-267): the MEF relation prints six
    exhibits -- the uncoded 'Riepilogo saldi', UE, LGP, IAA, FG, KK1. Four of
    them disagree with their annex tables, and all six went to the trash as a
    'second print'. 2025 extracts the captions in fragments ("Ta\\nbella LGP")."""
    from lib.pageio import relation_exhibits
    page79 = ("5 \n" + MEF_BLOCK + "Tabella UE - Riepilogo saldi importazioni \n"
              "Totale Complessivo € 427.488.850,54 \n" + MEF_BLOCK
              + "Tabella LGP - Licenze Globali di programma di cooperazione \n"
              "AEREA S.p.A. PAESI NATO € 1.059.450,91 \n")
    rel, still_open = relation_exhibits(page79, None)
    assert rel == [("Tabella", "UE"), ("Tabella", "LGP")] and still_open == "LGP"
    # p80 opens with LGP's last rows, then IAA: a continuation, then a start.
    page80 = ("6 \nLEONARDO S.p.A. € 14.836.237,72 \nTotale Complessivo "
              "€ 1.607.577.600,40 \n" + MEF_BLOCK + "Tabella IAA - Intermediazioni "
              "\nTotale Complessivo € 86.516.466,20 \n")
    rel, still_open = relation_exhibits(page80, "LGP")
    assert rel == [("Segue", "LGP"), ("Tabella", "IAA")] and still_open is None
    # Prose with euro amounts after a closed exhibit is not a continuation.
    assert relation_exhibits("9 \nper un totale di € 2,1 miliardi \n", None) == ([], None)
    # The uncoded summary, and 2025's fragmented caption.
    saldi = "come segue \nRiepilogo saldi esportazioni e importazioni \nTotali € 1,00 \n"
    assert relation_exhibits(saldi, None)[0] == [("Tabella", "SALDI")]
    fragmented = ("Mi\nnistero dell'Economia e delle Finanze \nDip\nartimento del Tesoro "
                  "\nOperazio\nni disciplinate - Relaz\nione attività 2025 \nTa\nbella LGP - Lic\nenze \n")
    assert relation_exhibits(fragmented, None)[0] == [("Tabella", "LGP")]


def test_relation_exhibit_codes_and_keys():
    """MAE exhibits are numbered (Tabella 8.3 -> T83, filed MAE/T83); MEF
    exhibits print the annex code, so they are filed under REL to stay apart
    from the annex table of the same name (MEF/REL/UE beside MEF/UE)."""
    from lib.pageio import rel_code
    from lib.patterns import REL_ARTICLE, rel_article
    assert rel_code("8.3") == "T83" and rel_code("UE") == "UE"
    assert rel_article("UE") == REL_ARTICLE and rel_article("T83") is None
    from VerifyTables import index_entry
    assert index_entry({"UE": {"title": "x", "authorities": ["MEF"]}}, "UE", "MEF", "REL") is None


def test_same_name_notes_report_copies_and_differences():
    """The user's rule (2026-10-18): each MEF relation exhibit says, in the SQL
    notes, whether it copies the annex table of the same name or disagrees with
    it -- 2024 FG totals 4.380.567.163,68 in the relation, 4.315.708.982,69 in
    the annex. Never silently pick one."""
    from tools.build_index import compare_exhibit, compare_saldi
    annex_lgp = "AEREA € 1.059.450,91\nASE € 312.944,23\nTotale Complessivo € 1.372.395,14"
    note = compare_exhibit("LGP", annex_lgp, annex_lgp, ["1.372.395,14"], ["1.372.395,14"])
    assert note.startswith("copy of MEF/LGP")
    note = compare_exhibit("FG", "X € 1,00", "X € 1,00", ["4.380.567.163,68"],
                           ["4.315.708.982,69"])
    assert "differs from MEF/FG" in note and "4.380.567.163,68" in note \
        and "4.315.708.982,69" in note
    # 2025 UE: a one-row summary whose amount is the annex grand total.
    note = compare_exhibit("UE", "UE € 345.713.316,98", annex_lgp + "\n€ 345.713.316,98",
                           [], ["345.713.316,98"])
    assert note.startswith("summary of MEF/UE") and "matches" in note
    # 2025 KK1: the relation prints only the grand total, the annex prints a
    # total per column with the grand total last. That agrees.
    note = compare_exhibit("KK1", "A € 1,00", "A € 1,00\nB € 2,00\nC € 3,00\nD € 4,00",
                           ["2.722.028.341,40"], ["44.430.731,06", "2.722.028.341,40"])
    assert note.startswith("summary of MEF/KK1") and "matches" in note
    # 2025 SALDI: subtotal rows ("Totale Esportazioni") are not part of BB.
    saldi = ("AA \nper Istituti di credito € 4.553.511.273,94 € 377.783.139,39\n"
             "BB \nEsportazioni temporanee € 5,00 € 6,00\n"
             "Totale Esportazioni € 11,00 € 12,00\nTotali (Imp + Esp.) 9,00")
    notes = compare_saldi(saldi, {"AA": ["4.553.410.664,28", "388.516.607,30"],
                                  "BB": ["5,00", "6,00"]})
    assert "AA differs" in notes["AA"] and "4.553.410.664,28" in notes["AA"]
    assert notes["BB"].startswith("BB matches")


def test_trash_holds_no_table():
    """The user's rule (2026-10-18): the trash may hold covers, prose and
    furniture, never a table. 2024 vol. II trashed six MEF relation exhibits."""
    from tools.page_check import table_like
    header = "Camera dei Deputati Senato della Repubblica\n– 1707 –\n"
    assert not table_like(header + "Esportazione Definitiva (EX)\nTAB. M\n")      # cover
    assert not table_like(header + "per un totale di € 1.000,00, € 2.000,00 e € 3,00\n")  # prose
    exhibit = (MEF_BLOCK + "Tabella FG - Finanziamenti\nA € 1,00\nB € 2,00\n"
               "Totale Complessivo € 3,00\n")
    assert table_like(header + exhibit) == "MEF exhibit caption"
    assert table_like(header + "TAB. MG1\n" + "X € 1,00\n" * 3) == "TAB. code"


def test_totals_row_label_is_its_leftmost_line():
    """2025 vol. II p263: 'Totali (Imp + Esp.)' sits at y=519, its amounts at
    518. Sorting on the rounded y put an amount first, the totals row failed the
    label test, and the Riepilogo saldi file lost its last row."""
    from lib.emit import first_row_label
    lines = [(518.3, 531, "6.475.669.142,80", 307), (518.4, 531, "€ 570.708.294,23", 419),
             (519.1, 532, "Totali (Imp + Esp.)", 183)]
    assert first_row_label(lines) == "Totali (Imp + Esp.)"


def test_dogane_cover_with_the_code_on_the_next_line():
    """2023 vol. III p345: "Riepilogo per operatore TAB." / "M1" is the only
    place M1 is named; its data pages print no code, so P swallowed M1..P1. On
    p389 the same shape left the cover unclassified and the table lost its
    article (DOG/MAPPENDICE instead of DOG/A1C89/MAPPENDICE)."""
    from lib.engine import page_code
    from lib.ontology import classify_page
    from lib.patterns import COVER_STYLE
    cover = ["Esportazione Definitiva (EX)", "Riepilogo per operatore TAB.", "M1"]
    assert page_code(cover, "\n".join(cover), set(), 1) == ("M1", COVER_STYLE)
    appendix = "Programmi Intergovernativi\nRiesportazione (RE) TAB.\n M - APPENDICE\n"
    assert classify_page(appendix) == ("DOG", "A1C89", None)
    assert classify_page("si veda la TAB.\nIl totale delle operazioni\n")[0] is None


def test_dogane_pages_do_not_make_a_volume_family_2():
    """2023 vol. III holds the Dogane alone and has no index. Its sampled pages
    print M1..P2, which made it family 2 (MAE), and family 2's style 3 dropped
    bare M, MG1-MG18 and MT1-MT13: 20 tables instead of 51."""
    from lib.engine import detect_family

    class Page:
        def __init__(self, text):
            self.text = text

    class Reader:
        pages = [Page("Tipo di operazione: Esportazione Definitiva (EX) TAB. M2\n"),
                 Page("Licenze Globali di Progetto TAB. MG4\n")]

    import lib.engine as engine
    saved = engine.page_text
    engine.page_text = lambda page: page.text
    try:
        assert detect_family(Reader(), set()) == 1
    finally:
        engine.page_text = saved


def test_bare_relation_exhibit_titles():
    """2023 vol. II p8-10 prints four MEF relation exhibits without 'Tabella
    <code> -' (2024 and 2025 print it before the same titles). The annex repeats
    two titles (p364 IAA, p539 LGP) under its 'ELENCO TABELLE' header."""
    from lib.pageio import relation_exhibits
    page8 = (MEF_BLOCK + "Riepilogo saldi importazioni intracomunitarie \nTotali \nUE \n"
             "€ 358.644.372,28 \n" + MEF_BLOCK
             + "Licenze Globali di programma di cooperazione \nAEREA S.p.A. € 1,00 \n")
    rel, still_open = relation_exhibits(page8, None)
    assert rel == [("Tabella", "UE"), ("Tabella", "LGP")] and still_open == "LGP"
    annex = ("ELENCO TABELLE\n" + MEF_BLOCK + "IAA\nIntermediazioni per Aziende - "
             "Riepilogo generale\nTabella\nX € 1,00\n")
    assert relation_exhibits(annex, None) == ([], None)
    coded = MEF_BLOCK + "Tabella FG - Finanziamenti-Garanzie per intermediari - Riepilogo generale\n"
    assert relation_exhibits(coded, None)[0] == [("Tabella", "FG")]
    # 2022 vol. II p9: LGP's last rows and its total open the page, in a font
    # whose euro sign pypdf reads as "Φ"; the total row alone says it continues.
    p9 = ("PIETRO ROSA TBM S.r.l. Φ\x03ϰϵ͘ϵϬϮ͕ϲϱ \nTotale Complessivo Φ\x03ϵϵϯ \n"
          + MEF_BLOCK + "Intermediazioni per Aziende - Riepilogo generale\n")
    assert relation_exhibits(p9, "LGP")[0] == [("Segue", "LGP"), ("Tabella", "IAA")]
    # 2022 vol. II p10 names FG's intermediaries "istituti di credito".
    fg2022 = MEF_BLOCK + "Finanziamenti-Garanzie per istituti di credito - Riepilogo generale\n"
    assert relation_exhibits(fg2022, None)[0] == [("Tabella", "FG")]


def test_a_scan_is_not_a_legible_page():
    """2023 vol. I p926-944: the Difesa annexes are images, and the act's own
    running header is their only text. Counted as legible, the verifier failed
    all five for not printing their code."""
    from lib.pageio import no_text_layer
    scan = ["Senato della Repubblica Camera dei Deputati",
            "XIX  LEGISLA TURA − DISEGNI DI LEGGE E RELAZIONI - DOCUMENTI -  DOC. LXVII, N. 2 VOLUME I",
            "–    927    –"]
    assert no_text_layer(scan)
    assert not no_text_layer(scan + ["MINISTERO DELLA DIFESA - Annesso 3A"])
    assert not no_text_layer([])
    from VerifyTables import page_code
    assert page_code(["Riepilogo per operatore TAB.", "M1"], set()) == "M1"


def test_a_segue_page_continues_its_exhibit():
    """2023 vol. I p41 is headed "SEGUE Tabella 8.8" and holds the table's
    "Totale per area" row; unread, T88 stopped a page short and its total went
    to the trash."""
    from lib.pageio import rel_heading
    assert rel_heading("SEGUE Tabella 8.8") == ("Tabella", "8.8")
    assert rel_heading("Tabella 8.8") == ("Tabella", "8.8")
    assert rel_heading("Nella Tabella 8.8 si vede") is None


def test_a_cover_whose_header_pypdf_splits_is_still_a_cover():
    """2023 vol. III p393: pypdf prints the act's header after the cover's
    caption and splits it ("XIX  LEGISLA TURA"); unrecognised as furniture, it
    hid the "TAB. O" ending and DOG/A1C89/O kept its cover."""
    from lib.pageio import is_table_cover
    p393 = ("Programmi Intergovernativi\n \nImportazione Definitiva (IM) \nTAB. O\n"
            "Senato della Repubblica Camera dei Deputati\n"
            "XIX  LEGISLA TURA − DISEGNI DI LEGGE E RELAZIONI - DOCUMENTI -  DOC. LXVII, N. 2 VOLUME III\n"
            "– 1907    –\n")
    assert is_table_cover(p393, "O")


def test_a_ciphered_page_is_read_back():
    """2022 vol. I p938-950: Identity-H fonts with no ToUnicode hand pypdf each
    glyph's index, the character code less 29. Read as lost, N1 to P2 vanished
    into M1, which ran 26 pages where the index lists 12."""
    from lib.pageio import decipher, is_ciphered
    page = ("/LFHQ]D 9DORUH\x03ILQL 6SHGL]LRQL /LFHQ]H\n"
            "0$(&,\x03\x108$0$\x03\x10\x03&(1752\x03,1)250$7,&2 3DJLQD\x03\x14\x03GL\x03\x17 7$%\x031\x14\n"
            "Camera dei Deputati Senato della Repubblica\n"
            "–    934    –")
    assert is_ciphered(page)
    assert decipher(page).splitlines() == [
        "Licenza Valore fini Spedizioni Licenze",
        "MAECI -UAMA - CENTRO INFORMATICO Pagina 1 di 4 TAB N1",
        "Camera dei Deputati Senato della Repubblica",
        "–    934    –"]
    clear = "Tabella M1\nXIX LEGISLATURA\nTAB B4"
    assert decipher(clear) == clear


def test_the_export_stamp_names_a_one_page_table():
    """2022 vol. I p558 (B5) and p937 (M2) are one page each and name their code
    only in the MAECI export stamp. As a style-3 hit a single page reads as a
    prose mention and both were dropped; the stamp is no prose."""
    from lib.engine import page_code
    from lib.patterns import STAMP_STYLE
    b5 = ["PROGRAMMI DI COOPERAZIONE ELENCO PER PAESE DI DESTINAZIONE",
          "MAECI - UAMA - CENTRO INFORMATICO Pagina di 11 TAB B5"]
    assert page_code(b5, "\n".join(b5), set(), 2) == ("B5", STAMP_STYLE)
    prose = ["come riportato nella TAB B5 allegata"]
    assert page_code(prose, prose[0], set(), 2) == ("B5", 3)


def test_a_trailing_page_stamped_for_another_report_is_dropped():
    """2022 vol. I p798: the one-page VALORE summary follows E with no code of
    its own and E took it (59 pages, index 58). Its stamp names VALORE, not E."""
    from lib.pageio import stamp_code, trim_foreign_stamp
    assert stamp_code(["MAECI - UAMA - CENTRO INFORMATICO Pagina di 11 VALORE"]) == "VALORE"
    assert stamp_code(["MAECI - UAMA - CENTRO INFORMATICO Pagina di 58 58 TAB E"]) == "E"
    assert stamp_code(["Pagina 3"]) is None
    codes = {740: "E", 797: "E", 798: "VALORE"}
    assert trim_foreign_stamp(740, 798, codes, "E") == (797, 1)
    assert trim_foreign_stamp(740, 797, codes, "E") == (797, 0)
    assert trim_foreign_stamp(740, 799, {799: None}, "E") == (799, 0)


def test_dogane_codes_pass_a_mef_vocabulary():
    """2022 vol. II p14 lists "Tabella AA" ... "Tabella DD2" one per line, so the
    MEF ELENCO is read as the volume's index, and its twelve codes then threw
    away every Dogane "TAB. M" header: no table from p592 to p816."""
    from lib.engine import page_code
    p593 = ["Tipo di operazione: Esportazione Definitiva (EX) TAB. M",
            "Denominazione operatore Codice"]
    assert page_code(p593, "\n".join(p593), {"AA", "BB", "CC"}, 1) == ("M", 3)
    prose = ["come da elencazione sintetica della tabella KK1"]
    assert page_code(prose, prose[0], {"AA", "BB"}, 1) == (None, None)


def test_a_cover_code_glued_to_its_caption():
    """2022 vol. II p927, p938, p941: "Riepilogo per operatoreTAB. M1". With a
    word boundary required before TAB, M1, N1 and O1 of art. 1 comma 2 were
    never found and P ran on over them. 2024's "MALTAB.M.A." (an uppercase
    join) must still not read as a code."""
    from lib.engine import page_code
    from lib.patterns import INLINE_CODE
    cover = ["Esportazione Definitiva", "(EX)Riepilogo per", "operatoreTAB. M1"]
    assert page_code(cover, "\n".join(cover), set(), 1)[0] == "M1"
    assert not INLINE_CODE.search("BANCA MALTAB.M.A. S.P.A.")
    assert not INLINE_CODE.search("non accettabile M1")
    from lib.ontology import classify_page
    assert classify_page("\n".join(cover))[:2] == ("DOG", "A1C2")


def test_the_printers_barcode_leaf_is_blank():
    """2022 vol. II p1088, after "PAGINA BIANCA": only the printer's barcode
    ("*190670036670*", twice). Not blank, it kept the volume's last table
    (DOG/A10QUATER/MT13) running two pages past its data."""
    from lib.pageio import is_blank
    assert is_blank(["*190670036670*", "*190670036670*"])
    assert is_blank(["PAGINA BIANCA"])
    assert not is_blank(["*190670036670*", "Austria 16"])
