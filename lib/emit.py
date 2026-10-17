"""Turning a manifest into files: one PDF per table, plus the trash.

Split out of Reports2PDFTables.py on 2026-10-11. Everything here READS the manifest and
WRITES the output; none of it detects anything. So the writer and the trash dump cannot
drift from each other, and a year that forks its detectors changes nothing in here.

It also carries the shared-page cutter, which reads a page carrying more than one exhibit
and writes each half to its own file. That is a capability rather than a year: it fires
only when the manifest says the volume has shared pages, and 2025 vol. I is simply the
volume that has them."""


import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from lib import layout, ontology
from lib.patterns import PDF_EXT, REL_HEADING
from lib.pageio import rel_code


def _pymupdf():
    """The geometry library, imported lazily.

    Only the pages that carry more than one relation exhibit need it, and only
    when a run actually writes. pypdf does the rest of step 1, so an import at
    module scope would make pymupdf a hard dependency of a detection-only run
    such as --dry-run.
    """
    try:
        import pymupdf
    except ImportError:
        return None
    from lib import geometry
    return pymupdf, geometry



def cut_exhibit_page(volume_path, page_no, keep_code, out_path, manifest,
                     volume_index):
    """Write one relation exhibit off a page that carries more than one.

    2025 vol. I prints two exhibits on a page three times over: 8.3 beside 8.4
    on p40, 8.6 beside 8.7 on p42, and 18 stacked above 19 on p60. At page
    grain they cannot be separated -- one physical page, two tables -- so the
    page is cut along the bands the exhibits themselves print.

    Four things this has to get right, each of which was wrong in a first
    attempt and is recorded because none of them fails loudly:

      * the CUT has to be real, not just a clip. `show_pdf_page(clip=...)` leaves
        the text layer whole, so pypdf still read "Tabella 8.3" out of the 8.4
        file -- both files contained both tables. Only apply_redactions()
        rewrites the content stream and deletes what is outside;
      * the tables are printed ROTATED 90 degrees inside a portrait page, so the
        page is turned upright first. 48 of 2025's tables are like this;
      * the turn has to come from lib/geometry.Frame, which reads the direction
        off the content stream's text matrix, rather than from a guess at the
        page. p40 and p42 come out rot90 and p60 upright, and the band is taken
        along "down the page in reading space", which is the same axis for both;
      * the crop must not be measured from get_text("words"), which splits words
        on the PAGE axes and so under-reports rotated text: it found
        "D'AMERICA" but not the "STATI UNITI" before it, and a crop computed
        from it silently cut the label to "MITI D'AMERICA". The crop is taken
        in the upright frame, where the axes agree with the text.

    `keep_code` is the exported code ("T83"); the exhibit it names is found by
    its printed number ("8.3"), read from the manifest rather than guessed back
    out of the code. A page whose band cannot be isolated returns False rather
    than writing a file that would hold two tables.
    """
    libs = _pymupdf()
    if libs is None:
        return False
    pymupdf, geometry = libs

    document = pymupdf.open(volume_path)
    try:
        source = document[page_no - 1]
        frame = geometry.Frame.for_page(source)
        turn = _FRAME_ROTATE.get(frame.name)
        if turn is None:
            return False
        # Page size after the turn: a quarter turn swaps the two.
        if turn in (90, 270):
            width, height = source.rect.height, source.rect.width
        else:
            width, height = source.rect.width, source.rect.height

        # Draw the page upright. Everything after this is axis-aligned, which is
        # what makes both the band arithmetic and the text extraction reliable.
        work = pymupdf.open()
        canvas = work.new_page(width=width, height=height)
        canvas.show_pdf_page(pymupdf.Rect(0, 0, width, height), document,
                             page_no - 1, rotate=turn)

        wanted = printed_number(keep_code, manifest, volume_index)
        headings = exhibit_headings(canvas)
        band = exhibit_band(canvas, headings, wanted, height)
        if band is None:
            return False
        lo, hi = band
        for cut in (pymupdf.Rect(0, 0, width, lo),
                    pymupdf.Rect(0, hi, width, height)):
            if cut.is_empty or cut.is_infinite:
                continue
            canvas.add_redact_annot(cut, fill=False)

        # The volume furniture goes with the prose. On a rotated page it runs
        # vertically while the table runs horizontally, so it sits INSIDE the
        # band's y-range and no horizontal cut can reach it. What separates them
        # is the writing direction, and it separates them cleanly: measured on
        # 2025 vol. I p40, the table is 1689 characters one way and the furniture
        # 262 the other, with nothing in between. The tally survives the rotation
        # -- (0,-1) and (1,0) become (1,0) and (0,1) -- so it can be read on the
        # canvas the band was cut on.
        drop_minority_direction(canvas)

        canvas.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            # The archive draws its grids as one-point rectangles, so a rule is
            # removed when the band TOUCHES it, not only when it covers it: the
            # stricter test leaves 616 orphaned rules and the page reads as an
            # empty grid beside the table.
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE)

        # Crop to what survived, and write it at that size rather than leaving a
        # page of A4 with a table in one corner of it.
        box = None
        for word in canvas.get_text("words"):
            rect = pymupdf.Rect(word[:4])
            box = rect if box is None else box | rect
        for drawing in canvas.get_drawings():
            box = box | drawing["rect"]
        if box is None or box.is_empty:
            return False
        crop = pymupdf.Rect(box.x0 - CUT_MARGIN, box.y0 - CUT_MARGIN,
                            box.x1 + CUT_MARGIN, box.y1 + CUT_MARGIN) & canvas.rect
        out = pymupdf.open()
        page = out.new_page(width=crop.width, height=crop.height)
        page.show_pdf_page(pymupdf.Rect(0, 0, crop.width, crop.height),
                           work, 0, clip=crop)
        out.save(out_path)
        out.close()
        work.close()
        return True
    finally:
        document.close()



# A note printed under a table's grid belongs to the table: "(*) Per la presenza
# di licenze cumulative..." under Tabella 2. It opens with a note marker.
NOTE_START = re.compile(r"^\s*(?:\(\s*\*+\s*\)|\*+|\(\d+\)|Fonte\b|Note?\b|N\.\s*B\.)",
                        re.IGNORECASE)
NOTE_MAX_GAP = 14.0     # pt from the grid to the note, and between note lines


def table_foot(page, grid_bottom, limit):
    """Where a ruled table really ends: its grid, or the notes printed under it.

    Lines are read top-down from the grid. The first must open with a note
    marker and sit within NOTE_MAX_GAP of the grid; the note then runs while
    each next line follows within NOTE_MAX_GAP. Prose after the table (which
    never opens with a marker) is left out, as decided for Tabella 12.
    """
    lines = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            x0, y0, x1, y1 = line["bbox"]
            if grid_bottom - 1 <= y0 < limit:
                text = "".join(sp.get("text", "") for sp in line.get("spans", []))
                if text.strip():
                    lines.append((y0, y1, text))
    lines.sort()
    bottom = grid_bottom
    if not lines or lines[0][0] - grid_bottom > NOTE_MAX_GAP \
            or not NOTE_START.match(lines[0][2]):
        return bottom
    for y0, y1, _text in lines:
        if y0 - bottom > NOTE_MAX_GAP:
            break
        bottom = max(bottom, y1)
    return bottom


def exhibit_band(canvas, headings, wanted, height):
    """(lo, hi) of exhibit `wanted` ('8.3') on an UPRIGHT canvas, or None.

    A band runs from its OWN heading down to the next heading; the last band
    runs to the foot of the page. Starting at the previous heading keeps the
    previous exhibit's body; starting at zero keeps the narrative the ministry
    prints above an exhibit (p63: 17 lines of 8.3.2 ahead of Tabella 21).
    Charts are headings like tables, so a chart sharing the page is a boundary.

    The band starts HEADING_GAP above its own heading, not CUT_MARGIN: the
    wider margin reached into the exhibit above and put 8.6's "Totale per
    area" row into the 8.7 file (p42). A ruled exhibit ends at its grid, or at
    the note printed under it (table_foot): prose after a table -- p48, the
    paragraph introducing Tabella 13 -- belongs to neither exhibit.

    Shared by cut_exhibit_page (keep the band) and cut_leftover_page (keep
    everything but the bands), so the two cannot disagree on a boundary.
    """
    numbers = [h[1] for h in headings]
    if wanted is None or wanted not in numbers:
        return None
    index = numbers.index(wanted)
    lo = headings[index][2] - HEADING_GAP
    hi = (headings[index + 1][2] - HEADING_GAP
          if index + 1 < len(headings) else float(height))
    rules = [d["rect"] for d in canvas.get_drawings()
             if d["rect"].y0 >= lo and d["rect"].y1 <= hi]
    if rules:
        hi = min(hi, table_foot(canvas, max(r.y1 for r in rules), hi)
                 + HEADING_GAP)
    return lo, hi


def cut_leftover_page(volume_path, page_no, codes, out_path, manifest,
                      volume_index):
    """Write what is left of a cut page once its exported exhibits are taken.

    Every part of every page lives in exactly one output (decided 2026-10-17):
    the exhibit bands go to their table files, and this -- the narrative, the
    charts, the running header -- goes to the volume's trash file. The page is
    turned upright as for the cut, and each exhibit band is removed only across
    the exhibit's own width, so the volume furniture printed along the edge of a
    rotated page stays here. Returns False when nothing is left.
    """
    libs = _pymupdf()
    if libs is None:
        return False
    pymupdf, geometry = libs
    document = pymupdf.open(volume_path)
    try:
        source = document[page_no - 1]
        frame = geometry.Frame.for_page(source)
        turn = _FRAME_ROTATE.get(frame.name)
        if turn is None:
            return False
        if turn in (90, 270):
            width, height = source.rect.height, source.rect.width
        else:
            width, height = source.rect.width, source.rect.height
        work = pymupdf.open()
        canvas = work.new_page(width=width, height=height)
        canvas.show_pdf_page(pymupdf.Rect(0, 0, width, height), document,
                             page_no - 1, rotate=turn)
        headings = exhibit_headings(canvas)
        drawings = [d["rect"] for d in canvas.get_drawings()]
        for code in codes:
            band = exhibit_band(canvas, headings,
                                printed_number(code, manifest, volume_index),
                                height)
            if band is None:
                continue
            lo, hi = band
            inside = [r for r in drawings if r.y0 >= lo and r.y1 <= hi]
            x0 = min((r.x0 for r in inside), default=0) - CUT_MARGIN
            x1 = max((r.x1 for r in inside), default=width) + CUT_MARGIN
            # The heading and any note may run wider than the grid.
            for block in canvas.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    bx0, by0, bx1, by1 = line["bbox"]
                    if lo <= by0 and by1 <= hi and (bx1 - bx0) < width * 0.9 \
                            and line.get("dir", (1, 0))[0] > 0.5:
                        x0, x1 = min(x0, bx0 - 2), max(x1, bx1 + 2)
            canvas.add_redact_annot(pymupdf.Rect(max(0, x0), lo,
                                                 min(width, x1), hi), fill=False)
        canvas.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if not canvas.get_text("words") and not canvas.get_images():
            return False
        work.save(out_path)
        work.close()
        return True
    finally:
        document.close()


def drop_minority_direction(page):
    """Redact everything not written along the page's dominant direction.

    Used to take the volume furniture off a rotated table page. It annotates;
    the caller applies, because a redaction pass rewrites the content stream and
    is worth doing once per page rather than once per span.

    pymupdf is imported here rather than at module scope on purpose: it is only
    needed to CUT a page, and a --dry-run detection pass must not require it.
    """
    libs = _pymupdf()
    if libs is None:
        return
    pymupdf, _geometry = libs
    import math

    tally = Counter()
    spans = []
    for span in page.get_texttrace():
        chars = span.get("chars") or []
        if not chars:
            continue
        dx, dy = span.get("dir", (1.0, 0.0))
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        key = (round(dx / length, 3), round(dy / length, 3))
        tally[key] += len(chars)
        spans.append((key, span.get("bbox")))
    if len(tally) < 2:
        return
    (keep, _total), = tally.most_common(1)
    for key, bbox in spans:
        if key == keep or not bbox:
            continue
        page.add_redact_annot(pymupdf.Rect(bbox), fill=False)



def exhibit_headings(page):
    """[(kind, number, y0, y1)] for each exhibit heading on an UPRIGHT page.

    Read as LINES with the same anchored pattern rel_headings() uses on pypdf
    text, not by looking for a number near the word "Tabella". The adjacency
    guess is wrong on an unrotated page: there the number sits to the RIGHT of
    the keyword on the same baseline, not below it, so a test that looks
    downwards picks up whatever digit happens to be underneath -- on 2025 vol. I
    p45 it read "Grafico 2022" and "Tabella 2025" (the year column headings),
    cut the wrong bands, and shipped a file with no table in it. Matching the
    line is the same discriminator that makes detection safe, applied twice.

    Only valid once the page has been turned, which is why cut_exhibit_page()
    does the turning itself rather than handing a raw volume page here.
    """
    found = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "")
                           for span in line.get("spans", [])).strip()
            m = REL_HEADING.match(text)
            if m:
                x0, y0, _x1, y1 = line["bbox"]
                found.append((m.group(1).capitalize(), m.group(2), y0, y1))
    found.sort(key=lambda heading: heading[2])
    return found



def printed_number(code, manifest, volume_index):
    """The printed number behind an exported code: 'T83' -> '8.3'.

    Not derivable from the code -- 'T83' could be Tabella 83 or Tabella 8.3 and
    nothing in the string says which -- so it is read from the manifest, where
    scan_headers recorded the exhibits as printed. Read from rel_pages, which
    lists every relation exhibit page and not only the shared ones, because
    every one of them is cut now.
    """
    for entry in manifest.get("rel_pages") or []:
        for kind, number in entry["exhibits"]:
            if rel_code(number) == code:
                return number
    return None



def table_plan(readers, manifests):
    """{(authority, article, code): [(volume_index, start, end)]}, the pages
    each exported PDF will contain.

    The single source of truth for "which pages does the output own". Both the
    writer (split_pdf) and the trash dump (dump_trash) read it, so the two
    cannot drift into disagreeing about what was written -- a second,
    independently reconstructed notion of the claimed pages is exactly how a
    coverage report starts lying.

    A copy settled away by resolve_repeats is skipped, so its pages are claimed
    by nobody and land in the trash; that is the point. The leading pages of a
    table that continues into the next volume come from that volume's "continued"
    entry and are attributed to it, so a page is never claimed twice and never
    claimed by the volume it does not physically belong to.

    Volumes are referenced by index rather than by reader object, so a caller
    can hold the reader alive and compare plans without relying on identity.
    """
    plan = {}
    for idx, manifest in enumerate(manifests):
        for tkey, info in manifest["tables"].items():
            if info.get("kept") is False:
                continue
            plan.setdefault(tkey, []).append((idx, info["start"], info["end"]))
        for tkey, cont in manifest.get("continued", {}).items():
            plan.setdefault(tkey, []).append((idx, cont["start"], cont["end"]))
    return plan



def table_key(tkey):
    """'MEF/UE', 'DOG/A1C2/N': the (authority, article, code) key as one string."""
    authority, article, code = tkey
    return "/".join(x for x in (authority, article, code) if x)


def printed_page(manifest, page):
    """The page number printed on the paper for PDF page `page` (1-based).

    First the PDF's own page labels (/PageLabels, stored on the manifest by
    step 1): the publisher's declaration, no guess. Then the volume's folio
    offset (indice.folio_map, the mode of folio - page) for volumes without
    numeric labels. Then the PDF index; table_list() notes that case.
    """
    labels = manifest.get("page_labels") or []
    if 0 < page <= len(labels) and str(labels[page - 1]).isdigit():
        return int(labels[page - 1])
    offset = manifest.get("folio_offset")
    return page + offset if offset is not None else page


def _segments_text(segments):
    return "+".join(f"V{idx + 1}:{start}-{end}" for idx, start, end in segments)


TABLE_LIST_FIELDS = ("key", "authority", "article", "code", "status", "source",
                     "segments", "printed", "pages", "file", "notes")


def _printed_text(manifests, segments):
    return "+".join(f"{printed_page(manifests[idx], start)}-"
                    f"{printed_page(manifests[idx], end)}"
                    for idx, start, end in segments)


def table_list(readers, manifests, year, labels):
    """One row per table of the year, in the order a person reads the volumes.

    The rows for written tables come from table_plan(), the plan split_pdf()
    writes from, so the list cannot name a page range the PDF does not have.
    Around them: copies resolve_repeats settled away (status `reprint`, no
    file) and codes a listing promised but no page carries (`listed-missing`).
    `segments` are PDF page indices (1-based, per volume), what a cut reads;
    `printed` are the page numbers printed on the paper, what the filename
    carries.
    """
    plan = table_plan(readers, manifests)
    rows = []
    for tkey, segments in plan.items():
        idx, start, _end = segments[0]
        info = manifests[idx]["tables"].get(tkey) or \
            manifests[segments[-1][0]]["tables"].get(tkey, {})
        notes = []
        for m_idx, manifest in enumerate(manifests):
            b = manifest.get("boundary", {}).get(table_key(tkey))
            if b:
                # `total` is how many pages the span was trimmed by at that
                # boundary, not a page count the table declares.
                notes.append(f"stopped before V{m_idx + 1}:{b.get('pages')} "
                             f"({b.get('authority')}), trimmed {b.get('total')}")
            for cover in manifest.get("covers") or []:
                if (cover["authority"], cover["article"], cover["code"]) == tkey:
                    notes.append(f"cover V{m_idx + 1}:{cover['page']} dropped")
            if tkey[2] in (manifest.get("singletons") or []):
                notes.append(f"singleton V{m_idx + 1}")
        if any(manifests[i].get("folio_offset") is None for i, _s, _e in segments):
            notes.append("no folio: printed = PDF index")
        rows.append({
            "key": table_key(tkey), "authority": tkey[0],
            "article": tkey[1] or "", "code": tkey[2],
            "status": "provisional" if info.get("provisional") else "ok",
            "source": info.get("source", ""),
            "segments": _segments_text(segments),
            "printed": _printed_text(manifests, segments),
            "pages": sum(e - s + 1 for _i, s, e in segments),
            "file": ontology.relative_path(
                tkey[0], tkey[1], tkey[2], year, idx + 1,
                printed_page(manifests[idx], start), PDF_EXT),
            "notes": "; ".join(sorted(set(notes))),
        })
    for idx, manifest in enumerate(manifests):
        for tkey, info in manifest["tables"].items():
            if info.get("kept") is False:
                rows.append({
                    "key": table_key(tkey), "authority": tkey[0],
                    "article": tkey[1] or "", "code": tkey[2],
                    "status": "reprint", "source": info.get("source", ""),
                    "segments": _segments_text([(idx, info["start"], info["end"])]),
                    "printed": _printed_text(manifests,
                                             [(idx, info["start"], info["end"])]),
                    "pages": info["end"] - info["start"] + 1, "file": "",
                    "notes": "copy settled away by resolve_repeats"})
        for code in manifest.get("listed_missing") or []:
            rows.append({
                "key": code, "authority": "", "article": "", "code": code,
                "status": "listed-missing", "source": "listing", "segments": "",
                "printed": "",
                "pages": 0, "file": "", "notes": f"listed in {labels[idx]}"})
    return rows


def write_table_list(path, rows):
    """Write table_list() rows as TSV, replacing any previous list."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(TABLE_LIST_FIELDS)]
    for row in rows:
        lines.append("\t".join(str(row[f]).replace("\t", " ")
                               for f in TABLE_LIST_FIELDS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_SHA256 = {}


def _sha256(path):
    """SHA-256 of a source volume, computed once per run."""
    if path not in _SHA256:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        _SHA256[path] = h.hexdigest()
    return _SHA256[path]


def _bookmarks(reader):
    """[(pdf page 1-based, title)] for every outline entry with a page."""
    found = []

    def walk(items):
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            try:
                found.append((reader.get_destination_page_number(item) + 1,
                              str(item.title)))
            except Exception:
                continue  # container entries with no page ('araba.pdf')
    try:
        walk(reader.outline)
    except Exception:
        pass
    return found


def provenance(tkey, segments, readers, manifests, paths, year, cut=False):
    """Document info for one split PDF: where every page of it came from.

    The split PDFs are a review view; SQL extraction reads the source volumes
    (DECISIONS.md). This keeps a file traceable on its own, once separated
    from the table list: /Title is the table key, /Subject a JSON record with
    each source file, its SHA-256, the PDF pages and their printed numbers,
    and the bookmark titles that point into the table. Deterministic: no dates.
    """
    import json
    sources, marks = [], []
    for idx, start, end in segments:
        path = paths[idx] if paths is not None else None
        sources.append({
            "file": Path(path).name if path else None,
            "sha256": _sha256(path) if path else None,
            "pdf_pages": [start, end],
            "printed_pages": [printed_page(manifests[idx], start),
                              printed_page(manifests[idx], end)],
            # True when the file holds a cut-out part of these pages, not the
            # whole pages: the rest of each page is in the volume's trash file.
            "cut": cut,
        })
        marks += [title for page, title in _bookmarks(readers[idx])
                  if start <= page <= end]
    record = {"table": table_key(tkey), "authority": tkey[0],
              "article": tkey[1], "code": tkey[2], "year": str(year),
              "sources": sources, "bookmarks": marks}
    return {"/Title": table_key(tkey),
            "/Subject": json.dumps(record, ensure_ascii=False, sort_keys=True),
            "/Creator": "ArmTrace step 1 (Reports2PDFTables)"}


def split_pdf(readers, manifests, out_root, year, paths=None, only=None):
    """Write one PDF per table under the ministry/article tree.

        <out_root>/PDF/MAE/A12023.PDF
        <out_root>/PDF/MEF/AA2023.PDF
        <out_root>/PDF/DOG/A1C2/N12023.PDF
        <out_root>/PDF/DIFESA/A2C6/3A2023.PDF

    The authority comes from the ministry that printed the table, not from the
    volume it came from, so Out/PDF/MEF/AA2023.PDF holds Tabella AA from
    whichever volume carried it. The year is a filename suffix rather than a
    directory level, so one folder per table holds one file per reporting year.

    `readers` and `manifests` are parallel sequences, one entry per volume of
    the year. A table is written once, from the volume that detected it; when
    it continues into the next volume, that volume's manifest carries the
    leading pages under "continued", and they are appended here so both halves
    land in the same file. Previously each volume wrote its own file over the
    same name, and whichever ran last won: 2025 F1 lost the 8 pages of vol. I.

    Segments are keyed by the full (authority, article, code), so a code that
    two ministries both print becomes two files rather than one overwriting the
    other -- which is the failure the authority level was introduced to fix. A
    copy settled away by resolve_repeats is skipped: one table, one file.

    The plan comes from table_plan(), which dump_trash() reads as well.

    `only`, a set of table_key() strings, writes just those tables: the cut one
    subagent asks for, from the same plan a full run would use.
    """
    plan = table_plan(readers, manifests)
    if only is not None:
        plan = {k: v for k, v in plan.items() if table_key(k) in only}

    # Every relation exhibit is cut, not only the ones sharing a page. An exhibit
    # that has a page to itself is no cleaner for it: the ministry writes the
    # narrative that introduces the exhibit onto the same page, so copying the
    # page whole shipped the prose too -- 17 lines of it ahead of Tabella 21 on
    # p63. Cutting every one of them is the same code and 31 pages instead of 10.
    rel_pages = {}
    for idx, manifest in enumerate(manifests):
        for entry in manifest.get("rel_pages") or []:
            rel_pages.setdefault(idx, set()).add(entry["page"])

    written, cut_pages, cut_failed = [], [], []
    for (authority, article, code), segments in plan.items():
        # Where the table came from, in the filename. The FIRST segment, so a
        # table crossing the volume join names the volume it starts in -- 2025
        # F1 is V1 p1039, not the vol. II half it continues into.
        first_idx, first_start, _first_end = segments[0]
        rel = ontology.relative_path(authority, article, code, year,
                                     first_idx + 1,
                                     printed_page(manifests[first_idx], first_start),
                                     PDF_EXT)
        path = layout.pdf_dir(year, out_root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)

        # A table whose every page needs cutting is written entirely by the cut,
        # because a pypdf writer and a pymupdf page cannot be mixed in one file
        # without going through bytes. That is the common case here: the six
        # exhibits that share a page are the only ones cut, and each is one page.
        cut_pages_here = []
        for idx, start, end in segments:
            if paths is None:
                continue
            for p in range(start, end + 1):
                if p in rel_pages.get(idx, ()):
                    cut_pages_here.append((idx, p))

        if cut_pages_here and paths is not None and len(cut_pages_here) == sum(
                end - start + 1 for _idx, start, end in segments):
            # Every page of this file is a cut page, which is the case for every
            # relation exhibit: they are one page long, and their page is cut so
            # that the prose the ministry prints around them stays out. The cut
            # is written by pymupdf and read back by pypdf, so it goes via a
            # temporary file rather than being merged in memory -- the two
            # libraries do not share a page object.
            merged = PdfWriter()
            ok = True
            scratch = tempfile.mkdtemp(prefix="relazione-cut-")
            try:
                for n, (idx, p) in enumerate(cut_pages_here):
                    tmp = Path(scratch, f"{n}.PDF")
                    if not cut_exhibit_page(paths[idx], p, code, str(tmp),
                                            manifests[idx], idx):
                        ok = False
                        cut_failed.append((code, p))
                        break
                    merged.add_page(PdfReader(str(tmp)).pages[0])
                    cut_pages.append((code, p))
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            if ok:
                merged.add_metadata(provenance((authority, article, code),
                                               segments, readers, manifests,
                                               paths, year, cut=True))
                with open(path, "wb") as fh:
                    merged.write(fh)
                written.append(str(path))
                continue

        writer = PdfWriter()
        for idx, start, end in segments:
            reader = readers[idx]
            for p in range(start, end + 1):
                if paths is not None and p in rel_pages.get(idx, ()):
                    # A cut page inside a file that also holds whole pages: the
                    # whole pages go in as they are, and the cut page is left out
                    # rather than silently carrying prose. Reported. Reached only
                    # by a multi-page exhibit, since a single-page one takes the
                    # branch above.
                    cut_failed.append((code, p))
                    continue
                writer.add_page(reader.pages[p - 1])
        writer.add_metadata(provenance((authority, article, code), segments,
                                       readers, manifests, paths, year))
        with open(path, "wb") as fh:
            writer.write(fh)
        written.append(str(path))

    if cut_pages:
        print(f"    cut          : {len(cut_pages)} relation exhibit page(s) cut "
              f"off a shared page")
    if cut_failed:
        print(f"    cut WARNING  : {len(cut_failed)} shared page(s) not exported "
              f"whole: {', '.join(f'{c}@p{p}' for c, p in cut_failed[:6])}")
    return written



def claimed_pages(plan, volume_count, page_counts):
    """{volume_index: {page: [table keys claiming it]}}.

    A page claimed twice is possible in principle -- the spans are contiguous by
    construction, so it should not be -- and the caller reports it rather than
    letting it be counted as either attributed or trashed.
    """
    claims = [{} for _ in range(volume_count)]
    for tkey, segments in plan.items():
        for idx, start, end in segments:
            # Clipped to the volume: a span that runs past the last page is a
            # defect to report, not an IndexError to raise at the last volume of
            # the run. See dump_trash(), which prints the overage.
            last = page_counts[idx]
            for p in range(max(1, start), min(end, last) + 1):
                claims[idx].setdefault(p, []).append(tkey)
    return claims



def span_past_end(plan, volume_count, page_counts):
    """{volume_index: [(code, end)]} for spans reaching past their volume.

    build_manifest clamps every end to the page count, so this is empty in
    practice. It exists because claimed_pages() clips defensively, and a silent
    clip would hide the very defect it was protecting against.
    """
    over = {}
    for tkey, segments in plan.items():
        for idx, _start, end in segments:
            if idx < volume_count and end > page_counts[idx]:
                over.setdefault(idx, []).append((tkey[2], end))
    return over



def trash_runs(claimed, page_count):
    """Maximal [start, end] ranges of pages no table claims, in page order."""
    runs, start = [], None
    for p in range(1, page_count + 1):
        if p in claimed:
            if start is not None:
                runs.append((start, p - 1))
                start = None
        elif start is None:
            start = p
    if start is not None:
        runs.append((start, page_count))
    return runs



def trash_reason(manifest, start, end, tables):
    """(slug, note) for one unattributed run.

    Only witnesses the manifest records exactly are asserted. `boundary` and
    `dropped` name real page numbers and `listings`/`pasted_pages` are page
    sets, so a match is a fact. `trimmed` records only a COUNT, so it is
    consulted by adjacency -- the run begins where a trimmed table's own pages
    stop -- and the note names the table rather than claiming a page list.

    Order is most-specific first. A run that satisfies no witness is reported as
    `no-detector`, which is the honest answer and not a failure: a volume
    legitimately holds pages no table owns.
    """
    # Exact page sets. The whole run has to be inside the set: a run that is
    # only partly pasted Gazzetta is a mixed region, and calling it Gazzetta
    # would misdescribe the pages that are not.
    #
    # A PARTIAL overlap is not discarded, it is reported on whichever reason the
    # run earns. 2025 vol. II p194-235 is one run of 42 pages of which 31 are
    # the Gazzetta block: the run opens where MAE/P2 was cut, so that is what it
    # is called, and the Gazzetta pages inside it are named -- otherwise the
    # folder would say "42 pages, no reason" and hide both facts.
    inside = []
    for slug, pages in (("gazzetta-pasted", manifest.get("pasted_pages") or []),
                        ("elenco", manifest.get("listings") or {})):
        known = set(pages)
        if not known:
            continue
        hit = [p for p in range(start, end + 1) if p in known]
        if len(hit) == end - start + 1:
            return slug, ""
        if hit:
            covered = (f"{hit[0]}-{hit[-1]}" if len(hit) > 1 else str(hit[0]))
            inside.append(f"{len(hit)}p {slug} ({covered}) dentro il blocco")

    def note(base=""):
        """The run's own reason, plus whatever known region sits inside it."""
        return "; ".join(filter(None, [base] + inside))

    # Exact spans, one run to one dropped copy or one discarded reprint.
    for tkey, info in (manifest.get("dropped") or {}).items():
        if info["start"] == start and info["end"] == end:
            where = "/".join(filter(None, tkey))
            return "dropped-copy", note(f"{where}, tenuta la copia di "
                                        f"{info['kept_from']}")
    for info in manifest.get("repeated_prints") or []:
        if info["start"] == start and info["end"] == end:
            where = "/".join(str(part) for part in info["key"] if part)
            return "given-up-reprint", note(
                f"{where}, tenuta la stampa "
                f"p{info['kept_from']}-{info['kept_to']}")

    # A run opening exactly on a boundary the volume itself printed is what that
    # clamp gave up. The authority is the one the index named, when it did.
    for key, info in (manifest.get("boundary") or {}).items():
        if start in info["pages"]:
            who = info.get("authority")
            return "cut-at-boundary", note(
                f"{key} fermata a p{start}"
                + (f", il volume assegna p{start} a {who}" if who else ""))

    # Adjacency only: the pages immediately after a table that had its tail
    # stripped off are those stripped pages, when nothing else claims them. The
    # manifest records a COUNT here and no page numbers, so the match is made on
    # position and bounded by the count -- an adjacency match larger than the
    # count recorded is somebody else's pages and is not claimed.
    #
    # The adjacency is tested against the table NAMED BY THE KEY, not against
    # every table in the volume. Testing against all of them made any page that
    # happened to follow any table look like a trimmed tail, and labelled it with
    # whichever key came first in dict order: 2025 vol. I reported eleven pages of
    # relation prose as "MAE/E, -1p finale/i", which is a sentence about a table
    # 800 pages away. It stayed hidden while those pages sat inside derived spans,
    # and surfaced the moment the relation's exhibits stopped claiming them.
    #
    # `trimmed` is keyed by the DISPLAY string "MAE/E" while `tables` is keyed by
    # the (authority, article, code) triple, so the lookup has to rebuild that
    # string. It is built the same way build_manifest built the key -- filter out
    # the None article -- or the two will not match. That is what happened: the
    # lookup referred to a `by_path` that no longer existed once `tables` stopped
    # being keyed by path, and every volume whose trash reached this witness
    # raised NameError and wrote no trash at all. 2025 vol. I was the only one to
    # reach it, because vol. II's runs are all claimed earlier in this chain.
    by_path = {"/".join(filter(None, k)): info for k, info in tables.items()}
    for key, count in (manifest.get("trimmed") or {}).items():
        info = by_path.get(key)
        if info and info["end"] + 1 == start and end - start + 1 <= count:
            return "trimmed-tail", note(f"{key}, -{count}p finale/i")

    if start == 1:
        return "front-matter", note()
    if end == manifest.get("page_count"):
        return "volume-tail", note()
    return "no-detector", note()



def dump_trash(volumes, plan, out_root, year, write=True):
    """Write Out/TRASH/<anno>/<volume>_TRASH.PDF: every unattributed page of a volume.

        Out/2025/TRASH/2025_LXVII_n4_VOLUME_II_TRASH.PDF
        Out/.work/2025/trash.tsv          (one row per run, every volume)

    One file per volume, with an outline entry per contiguous run (the pages
    between two tables, a block of Gazzetta pages): a run is still the unit a
    person triages, but the folder is two files rather than fifty. The TSV
    gives every run its source pages, size, reason and its pages inside the
    trash file; it is published into Out/<year>/tablesindex*.

    `write=False` prints the accounting and writes nothing, so --dry-run still
    reports the coverage -- which is the number a reader most wants from a run
    that cannot be trusted yet.

    Two things this deliberately does not do. It does not decide that an
    unattributed page is a defect: most are not (cover, INDICE, relation prose,
    Gazzetta pages), so the reason is reported rather than judged. And it does
    not reproduce the verifier's over-extension check -- a page a table wrongly
    swallowed is attributed and never reaches here.

    Guarded per volume, for the reason build_manifest is: one volume must not
    cost the others.
    """
    page_counts = [len(v["reader"].pages) for v in volumes]
    claims = claimed_pages(plan, len(volumes), page_counts)
    overage = span_past_end(plan, len(volumes), page_counts)

    written = []
    for volume in volumes:
        volume["trash_rows"] = []
    for idx, volume in enumerate(volumes):
        # Guarded per volume, for the reason build_manifest is: the trash is a
        # review artefact and must never be the thing that costs a run its PDFs.
        # The per-table PDFs are already on disk by the time this is called.
        try:
            written += dump_volume_trash(idx, volume, claims[idx],
                                         overage.get(idx, []), out_root,
                                         year, write)
        except Exception as exc:
            print(f"    TRASH FAILED for {volume['label']} (artefacts are safe): "
                  f"{type(exc).__name__}: {exc}")
    if write:
        rows = [row for volume in volumes for row in volume.get("trash_rows", [])]
        target = layout.trash_tsv(year, out_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("\t".join(TRASH_FIELDS) + "\n")
            for row in rows:
                fh.write("\t".join(str(row[f]).replace("\t", " ")
                                   for f in TRASH_FIELDS) + "\n")
        written.append(str(target))
    return written


TRASH_FIELDS = ("volume", "source_file", "start", "end", "pages", "reason",
                "note", "printed_from", "printed_to", "trash_file",
                "trash_from", "trash_to")



def dump_volume_trash(idx, volume, claimed, overage, out_root, year, write):
    """One volume's share of dump_trash(). Returns the paths written."""
    manifest = volume["manifest"]
    reader = volume["reader"]
    stem = volume["label"]
    page_count = len(reader.pages)
    written = []

    # A span running past the volume's last page is reported, not clipped
    # silently. It cannot happen from build_manifest, which clamps every end to
    # the page count, so a non-empty list is a real finding.
    if overage:
        print(f"    SPAN PAST END: {len(overage)} span(s) reach past p{page_count}"
              f" in {stem} (clipped when the trash was written): "
              + ", ".join(f"{code} p{end}" for code, end in overage[:6]))

    # The closed system: assert the arithmetic before writing anything, so a
    # mismatch is a reported defect rather than a silently wrong folder.
    twice = sorted(p for p, keys in claimed.items() if len(keys) > 1)
    runs = trash_runs(claimed, page_count)
    trashed = sum(end - start + 1 for start, end in runs)
    covered = len(claimed) + trashed
    if covered != page_count:
        print(f"    TRASH ACCOUNTING FAILED for {stem}: {covered} pages "
              f"accounted for out of {page_count}")
    # A page carrying two exhibits is claimed by both, each file holding its own
    # cut-out part. Any other double claim is a real defect.
    shared = {e["page"] for e in manifest.get("rel_pages") or []}
    twice = [p for p in twice if p not in shared]
    if twice:
        print(f"    DOUBLE-CLAIMED: {len(twice)} page(s) claimed by two "
              f"tables in {stem} (first p{twice[0]}) -- not trashed")

    print(f"    trash       : {trashed:5} pages unattributed in "
          f"{len(runs)} run(s) of {page_count} "
          f"({trashed / page_count:.0%} of the volume)")
    if runs:
        print(f"                 -> {layout.trash_dir(year, out_root)}/"
              f"{stem}_TRASH{PDF_EXT}")

    rows = []
    for start, end in runs:
        slug, note = trash_reason(manifest, start, end, manifest["tables"])
        rows.append((start, end, end - start + 1, slug, note))

    # What is left of each cut page once its exhibits are taken: the prose,
    # charts and running header around them. Every part of every page lives in
    # exactly one output; tools/page_check.py proves it at the end of a run.
    leftovers = []
    for entry in manifest.get("rel_pages") or []:
        page = entry["page"]
        codes = sorted(key[2] for key in claimed.get(page, ()))
        if codes:
            leftovers.append((page, codes))

    if not write or not (rows or leftovers):
        return written

    folder = layout.trash_dir(year, out_root)
    folder.mkdir(parents=True, exist_ok=True)
    # Clear this volume's own previous trash before writing, so a run that now
    # yields fewer blocks does not leave the old ones behind: a stale file here
    # would be indistinguishable from a current one. Scoped to this volume's stem
    # and to _p*-PDF / .tsv -- never a recursive delete, and never anything in
    # Out/PDF.
    for stale in folder.glob(f"{stem}_p*{PDF_EXT}"):
        stale.unlink()
    for stale in folder.glob(f"{stem}_TRASH{PDF_EXT}"):
        stale.unlink()

    # One PDF per volume (decided 2026-10-17): every unattributed page of the
    # volume, in order, with an outline entry per run so the file is navigable
    # run by run. The TSV maps each run to its pages inside that file.
    import json
    writer = PdfWriter()
    placed = []
    for start, end, size, slug, note in rows:
        first = len(writer.pages)
        for p in range(start, end + 1):
            writer.add_page(reader.pages[p - 1])
        placed.append((first + 1, len(writer.pages)))
        try:
            writer.add_outline_item(
                f"p{start}-{end} ({size}pp) {slug}"
                + (f" - {note}" if note else ""), first)
        except Exception:
            pass
    source = volume.get("path")
    scratch = tempfile.mkdtemp(prefix="relazione-leftover-")
    try:
        for page, codes in leftovers:
            tmp = Path(scratch, f"{page}.PDF")
            if not source or not cut_leftover_page(source, page, codes, str(tmp),
                                                   manifest, idx):
                continue
            first = len(writer.pages)
            writer.add_page(PdfReader(str(tmp)).pages[0])
            note = "outside " + " ".join(codes)
            rows.append((page, page, 1, "cut-leftover", note))
            placed.append((first + 1, first + 1))
            try:
                writer.add_outline_item(f"p{page} leftover ({note})", first)
            except Exception:
                pass
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    writer.add_metadata({
        "/Title": f"{stem} - unattributed pages",
        "/Subject": json.dumps({
            "volume": stem, "year": str(year),
            "file": Path(source).name if source else None,
            "sha256": _sha256(source) if source else None,
            "runs": [{"pdf_pages": [start, end],
                      "printed_pages": [printed_page(manifest, start),
                                        printed_page(manifest, end)],
                      "reason": slug}
                     for start, end, _size, slug, _note in rows],
        }, ensure_ascii=False, sort_keys=True),
        "/Creator": "ArmTrace step 1 (Reports2PDFTables)",
    })
    path = folder / f"{stem}_TRASH{PDF_EXT}"
    with open(path, "wb") as fh:
        writer.write(fh)
    written.append(str(path))

    # The trash index rows go back to dump_trash, which writes one
    # .work/<year>/trash.tsv for every volume of the year.
    volume["trash_rows"] = [
        {"volume": stem, "source_file": Path(source).name if source else "",
         "start": start, "end": end, "pages": size, "reason": slug, "note": note,
         "printed_from": printed_page(manifest, start),
         "printed_to": printed_page(manifest, end),
         "trash_file": path.name, "trash_from": t0, "trash_to": t1}
        for (start, end, size, slug, note), (t0, t1) in zip(rows, placed)]

    for start, end, size, slug, note in rows[:8]:
        print(f"        p{start}-{end:<6} {size:5}pp  {slug:16}"
              + (f" {note}" if note else ""))
    if len(rows) > 8:
        print(f"        ... {len(rows) - 8} more run(s)")
    return written



def report(manifest, label):
    n = len(manifest["tables"])
    print(f"{label:46} family={manifest['family']} "
          f"pages={manifest['page_count']:5} tables={n:3} "
          f"{'OK' if n else 'NO-TABLES'}")

    by_authority = {}
    for (authority, article, code) in manifest["tables"]:
        by_authority.setdefault((authority, article), []).append(code)
    for (authority, article), codes in sorted(by_authority.items(),
                                              key=lambda kv: (kv[0][0],
                                                              kv[0][1] or "")):
        label_txt = ontology.article_label(authority, article)
        suffix = f"  {label_txt}" if label_txt else ""
        print(f"    {authority:8} {'/'.join(filter(None, [authority, article])):14}"
              f" {len(codes):3} tables  {' '.join(sorted(codes))}{suffix}")

    if manifest["unplaced_tables"]:
        print(f"    UNPLACED  : {len(manifest['unplaced_tables'])} tables with "
              f"no ministry marker: {' '.join(manifest['unplaced_tables'])}")
    print(f"    ministry  : {manifest.get('provenance_source', '-')}")
    if manifest.get("pasted_ranges"):
        spans = ", ".join(f"p{a}-{b}" if a != b else f"p{a}"
                          for a, b in manifest["pasted_ranges"])
        print(f"    pasted-in: {len(manifest['pasted_pages'])} Gazzetta "
              f"UFFICIALe pages, no tables, skipped ({spans})")
    if manifest["key_collisions"]:
        print(f"    COLLISION : {len(manifest['key_collisions'])} tables dropped, "
              f"two detections landed on one (authority, article, code):")
        for c in manifest["key_collisions"][:6]:
            # article is None for MAE and MEF, so the parts are filtered rather
            # than joined blindly -- a blind join raised here and aborted the run.
            where = "/".join(str(part) for part in c["key"] if part)
            print(f"        {where:22} kept p{c['kept_start']}, "
                  f"dropped p{c['dropped_start']}")
    if manifest["vocabulary"]:
        print(f"    index      : {len(manifest['vocabulary'])} codes "
              f"{' '.join(manifest['vocabulary'])}")
    if manifest.get("listings"):
        listed = sum(len(c) for c in manifest["listings"].values())
        pages = ", ".join(f"p{p}({len(c)})" for p, c
                          in manifest["listings"].items())
        print(f"    ELENCO     : {listed} codes named ahead of the annex on "
              f"{len(manifest['listings'])} page(s) {pages}")
    if manifest.get("listed_found"):
        print(f"    ELENCO hit : {manifest['listed_found']} page(s) claimed by "
              f"the volume's own listing alone, covering "
              f"{manifest.get('listed_tables', 0)} table(s) no header or "
              f"outline named")
    if manifest.get("listed_missing"):
        print(f"    LISTED BUT NOT FOUND: {len(manifest['listed_missing'])} "
              f"code(s) the volume's own index promises and no detector found: "
              + " ".join(f"{c}@p{p}" for p, c in manifest["listed_missing"][:12]))
    if manifest.get("repeated_prints"):
        print(f"    reprinted  : {len(manifest['repeated_prints'])} table(s) "
              f"printed twice in the volume; one copy exported")
        for info in manifest["repeated_prints"][:8]:
            where = "/".join(str(part) for part in info["key"] if part)
            print(f"        {where:12} p{info['start']}-{info['end']} "
                  f"({info['pages']}pp) given up, kept p{info['kept_from']}"
                  f"-{info['kept_to']}")
    if manifest["conflicts"]:
        print(f"    conflicts  : {len(manifest['conflicts'])}, resolved to bookmark")
        for c in manifest["conflicts"][:6]:
            print(f"        {c['code']:5} bookmark p{c['embedded']:<5} "
                  f"header p{c['header']:<5} run={c['run']} "
                  f"({c['resolved_from']})")
    prov = [k[2] for k, i in manifest["tables"].items() if i["provisional"]]
    if prov:
        print(f"    provisional: {' '.join(prov)}")
    if manifest["garbled_pages"]:
        print(f"    garbled    : {manifest['garbled_pages']} pages, text "
              f"unrecoverable (no ToUnicode; step 2 extracts nothing)")
    if manifest["ciphered_pages"]:
        print(f"    ciphered   : {manifest['ciphered_pages']} pages, text "
              f"unrecoverable (Identity-H, no encoding table)")
    if manifest["stamped_pages"]:
        print(f"    stamp      : 'Pagina N di X' on "
              f"{manifest['stamped_pages']}/{manifest['page_count']} pages")
    if manifest["trimmed"]:
        detail = ", ".join(f"{c} -{n}p" for c, n in
                           sorted(manifest["trimmed"].items()))
        print(f"    stripped   : {len(manifest['trimmed'])} trailing pages "
              f"removed ({detail})")
    if manifest.get("boundary"):
        print(f"    bounded    : {len(manifest['boundary'])} table(s) stopped at "
              f"a section boundary the volume prints (INDICE or ELENCO)")
        for where, info in list(manifest["boundary"].items())[:8]:
            at = ", ".join(f"p{p}" for p in info["pages"])
            who = f" (p{info['pages'][0]} starts {info['authority']})" \
                if info["authority"] else ""
            print(f"        {where:22} -{info['total']} trailing page(s), "
                  f"stopped before {at}{who}")
    notes = [(k[2], i["stamp_note"]) for k, i in manifest["tables"].items()
             if i.get("stamp_note")]
    for code, note in notes[:6]:
        print(f"    stamp note : {code:5} {note}")
    checked = [i for i in manifest["tables"].values() if "index_agrees" in i]
    if checked:
        agree = sum(1 for i in checked if i["index_agrees"])
        mism = [(k[2], i["pages"], i["index_pages"]) for k, i in
                manifest["tables"].items() if "index_agrees" in i
                and not i["index_agrees"]]
        print(f"    index check: {agree}/{len(checked)} spans agree with the "
              f"index page counts, {len(mism)} mismatch {mism[:4]}")
    for tkey, cont in manifest.get("continued", {}).items():
        where = "/".join(str(part) for part in tkey if part)
        print(f"    continued  : {where} carries on from "
              f"{cont['from_volume']} p{cont['from_end']}, here "
              f"p{cont['start']}-{cont['end']} ({cont['pages']} pages) "
              f"-> one PDF of {cont['total_pages']}")
    dropped = manifest.get("dropped", {})
    if dropped:
        print(f"    duplicate  : {len(dropped)} table(s) found in another "
              f"volume of the same year and not a split; kept copy wins")
        for tkey, info in list(dropped.items())[:8]:
            where = "/".join(str(part) for part in tkey if part)
            print(f"        {where:22} p{info['start']}-{info['end']} "
                  f"({info['pages']} pp) dropped, kept from {info['kept_from']}")


# ==========================================================================
# the pages no table owns
# ==========================================================================
#
# What lands here is the complement of table_plan(), not a second detection
# pass. Every page the exported PDFs contain is claimed by exactly one of them,
# so whatever is left over is by definition unattributed -- and the arithmetic is
# a closed system: claimed + trashed == every physical page of the volume, which
# is asserted per volume below rather than hoped for.
#
# It is NOT the same thing as manifest["unassigned_pages"], which counts pages no
# detector read a code on. Almost all of those sit INSIDE a derived span: ends
# come from the next table's start, so a table's pages need not repeat its code.
# And it is not the over-extension check either -- a page a table wrongly
# swallowed (Tabella M holding TAB N) is attributed, so it never appears here.
# That failure belongs to VerifyTables.check_page_overlap().

TRASH_DIR = "TRASH"


# Reason slugs, printed in the report and written to the TSV. An empty note means
# the slug is the whole explanation.
TRASH_REASONS = {
    "front-matter": "copertina, INDICE, frontespizio: precede la prima tabella",
    "volume-tail": "dopo l'ultima tabella: PAGINA BIANCA, chiusura, allegati",
    "elenco": "ELENCO TABELLE SEGNALAZIONI della pagina",
    "gazzetta-pasted": "pagina Gazzetta Ufficiale incollata nel volume",
    "cut-at-boundary": "ritagliata: la tabella si ferma al confine che il volume stampa",
    "trimmed-tail": "pagine finali tolte dalla tabella (stamp o PAGINA BIANCA di un altro documento)",
    "dropped-copy": "copia scartata: stesso codice in piu' volumi, non uno spezzamento",
    "given-up-reprint": "seconda stampa scartata: la stessa tabella stampata due volte nel volume",
    "no-detector": "nessun rilevatore ha rivendicato questa pagina",
}


# The rotation that brings a page's writing direction upright, as the argument
# show_pdf_page() takes it. Not the frame's own name: that is a description of
# where the text points, this is how far to turn the page to fix it.
_FRAME_ROTATE = {"upright": 0, "rot90": 270, "rot180": 180, "rot270": 90}


# Blank border left around a cut exhibit, in points. Generous because the rule
# layer is measured exactly and the text is not: a hairline sits on the table
# edge and the last column of digits butts against it, so a tight crop clips
# the figure.
CUT_MARGIN = 20.0
# Gap kept between an exhibit band and what borders it: its own heading above,
# the next heading or its last grid rule below.
HEADING_GAP = 2.0
