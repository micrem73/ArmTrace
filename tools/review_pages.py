#!/usr/bin/env python3
"""Render what a checker needs to judge one cut table, and nothing it must guess.

    python3 tools/review_pages.py 2025 MEF/UE

Reads the table's row from Out/.work/<year>/tables.tsv, opens the table PDF in
Out/<year>/PDF/ and the source volume(s), and writes PNGs under
Out/.work/<year>/checks/<key>/ (lib/layout.py):

    file-first.png, file-second.png      first pages of the cut file
    file-last.png                        its last page
    file-mid-<n>.png                     up to 3 evenly spaced interior pages
    source-first.png                     the table's first page, whole, from the
                                         source (the cut may be cropped to the
                                         table and lose the folio)
    source-before.png                    the source page just before the table
    source-after.png                     the source page just after it

Prints one JSON object on stdout: the row, the file's page count, and every
image with its label, PDF index and printed page. Deterministic; renders only.
"""
import csv
import json
import sys
from pathlib import Path

import pymupdf
import hashlib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import layout  # noqa: E402
from lib.patterns import REPORTS_DIR  # noqa: E402
from lib.volumes import find_report  # noqa: E402

DPI = 90
MAX_MID = 3


key_dir = layout.key_dir


def table_row(year, key):
    path = layout.tables_tsv(year, ROOT)
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row["key"] == key:
                return row
    raise SystemExit(f"{key}: not in {path} (rebuild it with --tables)")


def parse_segments(text):
    """'V1:1039-1046+V2:5-66' -> [(1, 1039, 1046), (2, 5, 66)]"""
    out = []
    for part in text.split("+"):
        vol, span = part.split(":")
        a, b = span.split("-")
        out.append((int(vol[1:]), int(a), int(b)))
    return out


def content_hash(pdf):
    """sha1 of the file's page text, page by page: what a verdict judged.

    tools/recheck.py compares it with the file as it is now, so a table whose
    content changed after its check is re-checked, and one whose bytes changed
    only in metadata is not.
    """
    doc = pymupdf.open(pdf)
    text = "\f".join(page.get_text() for page in doc)
    return hashlib.sha1(text.encode()).hexdigest(), doc.page_count


def save(doc, index0, path):
    pix = doc[index0].get_pixmap(dpi=DPI)
    pix.save(str(path))


def main(year, key):
    row = table_row(year, key)
    if not row["file"]:
        raise SystemExit(f"{key}: status {row['status']}, no file to review")
    pdf = layout.pdf_dir(year, ROOT) / row["file"]
    if not pdf.exists():
        raise SystemExit(f"{key}: {pdf} missing (cut it with --only {key})")
    out = layout.checks_dir(year, ROOT) / key_dir(key)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    segments = parse_segments(row["segments"])
    printed = [tuple(map(int, s.split("-"))) for s in row["printed"].split("+")]
    # file page n (0-based) -> (volume, pdf index, printed page)
    where = []
    for (vol, a, b), (pa, _pb) in zip(segments, printed):
        for i in range(a, b + 1):
            where.append((vol, i, pa + (i - a)))

    images = []
    doc = pymupdf.open(pdf)
    n = doc.page_count
    picks = [("file-first", 0)]
    if n > 1:
        picks.append(("file-second", 1))
    interior = list(range(2, n - 1))
    if interior:
        step = max(1, len(interior) // (MAX_MID + 1))
        for k, i in enumerate(interior[step - 1::step][:MAX_MID], 1):
            picks.append((f"file-mid-{k}", i))
    if n > 2:
        picks.append(("file-last", n - 1))
    for label, i in picks:
        path = out / f"{label}.png"
        save(doc, i, path)
        vol, idx, pr = where[i] if i < len(where) else (None, None, None)
        images.append({"label": label, "png": str(path.relative_to(ROOT)),
                       "file_page": i + 1, "volume": vol, "pdf_index": idx,
                       "printed": pr})

    # The neighbours in the source, to judge whether the cut stops too early
    # or too late.
    first_vol, first_idx, _ = segments[0]
    last_vol, _, last_idx = segments[-1]
    for label, vol, idx in (("source-first", first_vol, first_idx),
                            ("source-before", first_vol, first_idx - 1),
                            ("source-after", last_vol, last_idx + 1)):
        src = find_report(year, str(vol), ROOT / REPORTS_DIR)
        sdoc = pymupdf.open(src)
        if 1 <= idx <= sdoc.page_count:
            path = out / f"{label}.png"
            save(sdoc, idx - 1, path)
            images.append({"label": label, "png": str(path.relative_to(ROOT)),
                           "volume": vol, "pdf_index": idx})

    digest, _pages = content_hash(pdf)
    (out / "checked.json").write_text(json.dumps(
        {"key": key, "file": row["file"], "content": digest, "pages": n},
        indent=1), encoding="utf-8")
    print(json.dumps({"year": year, "row": row, "file_pages": n,
                      "images": images}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
