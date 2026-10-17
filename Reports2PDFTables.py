#!/usr/bin/env python3
"""Step 1 for one edition: one PDF per table, the trash, the table list.

    python3 Reports2PDFTables.py 2025 [--out <root>] [--dry-run] [--no-trash]
                                      [--only MEF/UE] [--tables <path>]

The engine is lib/engine.py; what differs by edition is editions/y<year>.py.
"""
import sys

from lib import engine

if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        raise SystemExit(__doc__)
    engine.run(sys.argv[1], sys.argv[2:])
