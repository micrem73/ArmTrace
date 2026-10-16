"""Run step 1 for a year into a scratch tree and reduce what it wrote to a
stable, diffable text snapshot.

The snapshot is the regression contract for a verified edition: the manifest's
tables (per volume) and every PDF written under Out/PDF/ with its page count.
Paths of the source files are left out, so moving the sources does not count
as a change.

    python3 tests/snapshot.py 2025 > tests/golden/2025.tsv
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent


def run_step1(year, out):
    manifest = Path(out, "manifest.jsonl")
    subprocess.run(
        [sys.executable, str(ROOT / f"Reports2PDFTables_{year}.py"),
         "--out", str(out), "--manifest", str(manifest), "--no-trash"],
        cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    return manifest


def snapshot(year):
    with tempfile.TemporaryDirectory() as out:
        manifest = run_step1(year, out)
        lines = ["# tables: volume authority article code start end pages"]
        for record in map(json.loads, manifest.read_text().splitlines()):
            volume = Path(record["report"]).stem
            for t in record["tables"]:
                lines.append("\t".join(str(x) for x in (
                    volume, t["authority"], t["article"] or "-", t["code"],
                    t["start"], t["end"], t["pages"])))
        lines.append("# files: path pages")
        pdf_root = Path(out, "Out", "PDF")
        for path in sorted(pdf_root.rglob("*.PDF")):
            pages = len(PdfReader(path).pages)
            lines.append(f"{path.relative_to(pdf_root).as_posix()}\t{pages}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.write(snapshot(sys.argv[1]))
