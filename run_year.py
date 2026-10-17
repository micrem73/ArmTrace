#!/usr/bin/env python3
"""One command for the deterministic part of a year.

    python3 run_year.py 2025

  1. step 1     Reports2PDFTables.py <year>   tables, trash, table list
  2. verify     VerifyTables.py               deterministic audit
  3. recheck    tools/recheck.py              next step per table (check/fix/human)
  4. publish    tools/build_index.py          tablesindex + review.html
  5. page check tools/page_check.py           every page in exactly one place

Stops at the first step that fails. The subagent checks (one table-checker per
table, see CLAUDE.md) run between runs; their verdicts are picked up by step 3,
so after a round of checks run `python3 tools/recheck.py <year>` and
`python3 tools/build_index.py <year>`.
Output layout: lib/layout.py.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(year):
    py = sys.executable
    steps = [
        ("step 1", [py, str(ROOT / "Reports2PDFTables.py"), year]),
        # review.html replaces the verifier's own page: --no-html.
        ("verify", [py, str(ROOT / "VerifyTables.py"), "--year", year,
                    "--no-html"]),
        ("recheck", [py, str(ROOT / "tools" / "recheck.py"), year]),
        ("publish", [py, str(ROOT / "tools" / "build_index.py"), year]),
        ("page check", [py, str(ROOT / "tools" / "page_check.py"), year]),
    ]
    for name, cmd in steps:
        print(f"=== {name}: {' '.join(Path(c).name for c in cmd[1:])}", flush=True)
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode:
            print(f"=== {name} FAILED (exit {result.returncode}); stopping")
            return result.returncode
    print(f"=== {year} done: open Out/{year}/review.html")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
