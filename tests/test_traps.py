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


@pytest.mark.parametrize("year", ["2025"])
def test_cut_clip_maps_back_to_the_source_page(year, sources):
    """The SQL extractor finds a cut exhibit on its SOURCE page by the clip the
    splitter returns (DECISIONS.md 2026-10-19). The splitter cuts on a page
    turned upright, so the band must be turned back: on rot90 p40 the wrong
    quarter turn puts Tabella 8.3's band over the margin, and the extractor
    would read the furniture as the table."""
    import pymupdf

    from lib import geometry
    from lib.emit import _FRAME_ROTATE, source_rect
    doc = pymupdf.open(ROOT / "In" / year / "2025_LXVII_n4_VOLUME_I.pdf")
    for pno in (27, 40, 60):
        src = doc[pno - 1]
        turn = _FRAME_ROTATE[geometry.Frame.for_page(src).name]
        w, h = ((src.rect.height, src.rect.width) if turn in (90, 270)
                else (src.rect.width, src.rect.height))
        canvas = pymupdf.open().new_page(width=w, height=h)
        canvas.show_pdf_page(canvas.rect, doc, pno - 1, rotate=turn)
        whole = source_rect(src, turn, canvas.rect)
        assert all(abs(a - b) < 0.01 for a, b in zip(whole, src.rect)), pno
        words = {}
        for x in src.get_text("words"):
            words.setdefault(x[4], []).append(x)
        for x in canvas.get_text("words"):
            if len(x[4]) > 4 and len(words.get(x[4], ())) == 1:
                back = pymupdf.Rect(source_rect(src, turn, pymupdf.Rect(x[:4])))
                assert abs(back.x0 - words[x[4]][0][0]) < 2, (pno, x[4])
                assert abs(back.y0 - words[x[4]][0][1]) < 2, (pno, x[4])


# The pilot tables' headers as printed, read off the rendered source pages
# (2026-10-19). A ruled table's columns come from its grid: the gaps between
# words merged DOG M1's three labels into one and missed T2's header entirely.
PILOT_HEADERS = [
    ("2025", "2025_LXVII_n4_VOLUME_I.pdf", 70, None,
     ["Valuta", "Ammontare", "EURO", "Val. Fini Dog.", "Data", "Q.ta'",
      "Unita' Misura", "Materiale oggetto del contratto", "Tipo", "Cat."]),
    ("2025", "2025_LXVII_n4_VOLUME_II.pdf", 287, None,
     ["Numero Autorizzazione", "Utilizzatore Finale", "Causale",
      "Importi Segnalati", "Importi Accessori Segnalati"]),
    ("2025", "2025_LXVII_n4_VOLUME_II.pdf", 254, None,
     ["NR.", "PROGRAMMA", "TIPOLOGIA", "PAESI PARTECIPANTI",
      "DITTE ITALIANE PARTECIPANTI"]),
    ("2025", "2025_LXVII_n4_VOLUME_I.pdf", 27, None,
     ["", "2021", "2022", "2023", "2024", "2025"]),
    ("2025", "2025_LXVII_n4_VOLUME_II.pdf", 859, None,
     ["Denominazione operatore", "Numero di operazioni svolte",
      "Stato di avanzamento annuale (Euro)", "Utilizzo anni precedenti (Euro)"]),
    ("2025", "2025_LXVII_n4_VOLUME_II.pdf", 288, "data",   # EE continuation page
     None),
    # A header may name a total; only a data row prints one with its value.
    ("2025", "2025_LXVII_n4_VOLUME_II.pdf", 393, None,
     ["Ditta", "Numero Autorizzazione", "Utilizzatore Finale", "Causale", "Totale"]),
    ("2025", "2025_LXVII_n4_VOLUME_I.pdf", 607, None,
     ["Operatore", "Autorizzazioni", "Valore (EURO)", "di cui, per Prog. di Cooperazione",
      "Valore Fini Dog.", "di cui, per Prog. di Cooperazione", "Incidenza % su totale"]),
]


@pytest.mark.parametrize("year,volume,page,kind,expected", PILOT_HEADERS,
                         ids=lambda v: str(v) if isinstance(v, int) else None)
def test_grid_header_reads_the_printed_columns(year, volume, page, kind,
                                               expected, sources):
    import pymupdf

    import CatalogueTables as C
    from lib import geometry as geo
    from lib import grammar as gra
    from lib.source import SourcePage
    doc = pymupdf.open(ROOT / "In" / year / volume)
    sp = SourcePage(doc, page, None)
    geom = C.page_geometry(sp, geo.dominant_direction(sp))
    header = geo.grid_header(sp, geom["words"], geom["frame"],
                             is_label=gra.is_label_row)
    if expected is None:
        assert header is None       # a page that prints no header has none
    else:
        assert [c["text"] for c in header["cells"]] == expected


def test_a_credit_note_prints_its_sign_before_the_euro():
    """2025 MEF EE prints a credit note as `-€ 142.942,72`. Read as no number,
    every credit note counted zero and the column summed 9.711.703,48 over
    the printed `Totale Complessivo`."""
    from decimal import Decimal

    from lib.grammar import parse_number, value_shape
    assert parse_number("-€ 142.942,72") == Decimal("-142942.72")
    assert parse_number("€ -40,66") == Decimal("-40.66")
    assert value_shape("-€ 142.942,72") == "currency"
