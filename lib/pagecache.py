"""One text extraction per page, kept on disk.

pypdf's extract_text is 94% of a step-1 run (2025: 1099 of 1165 s), and step 1
used to call it three times per page: read_index, indice.folio_map and
pageio.read_page. attach() reads every page once through read_page, stores
(text, stamp) under Out/CACHE/, and hangs the result on the reader so that
pageio.page_text() and pageio.read_page() answer from it.

The cache key is the PDF's bytes, the pypdf version and the source of
lib/pageio.py, because those three decide what read_page returns. Change any of
them and the entry is simply not found, so a stale cache cannot be read.
"""

import hashlib
import json
from pathlib import Path

import pypdf

from lib import pageio
from lib.patterns import OUT_DIR

CACHE_DIR = Path(OUT_DIR, "CACHE")


def _key(pdf_path):
    h = hashlib.sha256()
    with open(pdf_path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    h.update(pypdf.__version__.encode())
    h.update(Path(pageio.__file__).read_bytes())
    return h.hexdigest()[:32]


def _extract(reader):
    entries = []
    for page in reader.pages:
        try:
            text, stamp = pageio.read_page(page)
            entries.append({"text": text, "stamp": stamp, "error": None})
        except Exception as exc:
            entries.append({"text": None, "stamp": None,
                            "error": f"{type(exc).__name__}: {exc}"})
    return entries


def attach(reader, pdf_path, cache_dir=CACHE_DIR):
    """Load or build the page cache for `reader` and attach it. Returns it."""
    path = Path(cache_dir, f"{Path(pdf_path).stem}-{_key(pdf_path)}.json")
    entries = None
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            entries = None
    if entries is None or len(entries) != len(reader.pages):
        entries = _extract(reader)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    by_ref = {}
    for page, entry in zip(reader.pages, entries):
        ref = page.indirect_reference
        if ref is not None:
            by_ref[(ref.idnum, ref.generation)] = entry
    reader._page_cache = by_ref
    return by_ref
