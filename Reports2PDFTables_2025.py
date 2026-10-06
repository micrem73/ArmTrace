#!/usr/bin/env python3
"""
Reports2PDFTables for the 2025 reporting year — step 1, split into one PDF per
table.

    python3 Reports2PDFTables_2025.py [--out <root>] [--dry-run]

This is a thin entry point, not a fork. Every detector lives in
Reports2PDFTables.py, the engine, and this file pins the year and the volumes so
the command cannot be typed wrong. Run it with no arguments and it does the
right thing for 2025; every engine flag still works and is passed through:

    --dry-run          report only, write nothing
    --out <root>       write under <root>/Out/ instead of <base>/Out/
    --manifest <path>  append the per-volume manifest JSON
    --base <dir>       where reports_185_1990/ and Out/ live (default: .)

Both volumes, always. 2025 Tabella F1 is printed 1035-1042 in volume I and
1043-1104 in volume II, so the table straddles the join: a per-volume run loses
the eight pages of volume I, and the engine refuses to join halves it thinks
belong to different years. See AGENTS.md section 1a.

Why a per-year script at all, and why not a copy: AGENTS.md section 1a sets out
the working method, and section 2 lists invariants that only hold if there is
one copy of the logic. This file holds no detection code and never will.
"""

import sys

import Reports2PDFTables as engine

YEAR = "2025"
VOLUME = "both"


def main(argv=None):
    """Pin the year and the volumes, then hand over to the engine.

    A year given on the command line is refused rather than silently ignored,
    so this script cannot be used to run 2024 by accident and leave the output
    looking like a 2025 result.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    for arg in argv:
        if arg.startswith("--year"):
            raise SystemExit(
                f"Reports2PDFTables_{YEAR}.py is pinned to {YEAR}; "
                f"use Reports2PDFTables.py --year for another year, or the "
                f"script for that year."
            )
    return engine.main(["--year", YEAR, "--volume", VOLUME] + argv)


if __name__ == "__main__":
    main()