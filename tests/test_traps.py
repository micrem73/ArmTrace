"""Rules whose breakage looks like correct code. Each was a bug that shipped."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ast.parse((ROOT / "VerifyTables.py").read_text())


def test_verifier_imports_no_year_script():
    """The verifier audits every year by one set of rules. Importing a year's
    detector would audit that year by its own opinion, and it could no longer
    report a bug it shares."""
    modules = set()
    for node in ast.walk(VERIFIER):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not {m for m in modules if m.startswith("Reports2PDFTables")}


def test_verifier_does_not_redeclare_code_shapes():
    """A private copy of a trap is the same bug with a second place to fix it:
    the verifier once kept its own one-digit code tail after lib/ was widened
    to two, and stopped seeing MG10 and MT13."""
    assigned = {t.id for node in VERIFIER.body if isinstance(node, ast.Assign)
                for t in node.targets if isinstance(t, ast.Name)}
    assert not assigned & {"CODE_RE", "CODE_TAIL", "INLINE_CODE", "LEADING_CODE"}
