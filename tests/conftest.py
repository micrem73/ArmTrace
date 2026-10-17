import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sources(year):
    """The year's volumes are in In/<year>/ with the manifest's page counts.

    They are not in git, so a fresh worktree has none: say so, instead of
    letting step 1 fail in a way that reads like a regression."""
    problems = []
    for line in (ROOT / "In" / "manifest.tsv").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        row = line.split("\t")
        if row[0] != year:
            continue
        path, pages = ROOT / "In" / year / row[4], int(row[5])
        if not path.exists():
            problems.append(f"missing {path.relative_to(ROOT)}")
        elif len(PdfReader(path).pages) != pages:
            problems.append(f"{path.relative_to(ROOT)}: not {pages} pages")
    if problems:
        pytest.fail(f"sources for {year}: " + "; ".join(problems)
                    + ". Run: bash In/download_185.sh In In/manifest.tsv",
                    pytrace=False)
