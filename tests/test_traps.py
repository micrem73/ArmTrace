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
