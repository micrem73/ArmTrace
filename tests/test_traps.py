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
