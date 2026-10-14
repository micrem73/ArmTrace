#!/usr/bin/env python3
"""
ocr_repair.py -- give unreadable PDF pages a real text layer, by OCR.

Why this exists
---------------
Some PDFs have pages whose text layer is destroyed at generation time. The
fonts carry no ToUnicode CMap and no /Encoding, and the glyph names inside the
embedded font program are the Private Use codepoint copied into a name
("uniE019" for U+E019) -- a re-encoding of the broken value, not a recovery of
the original character. Every route from character code to character fails:

    /ToUnicode      absent
    /Encoding       absent
    CFF glyph names the PUA codepoint under a "uni" prefix

What survives is the glyph *outlines*, which is to say the page is a picture
of text. This script renders those pages and runs OCR over them.

What it does NOT do
-------------------
It does not OCR pages that already have a working text layer. Passing a
readable page through OCR would replace good text with worse text, so readable
pages are copied through untouched and reported as skipped.

How a page is repaired
----------------------
1. Render the page at --dpi.
2. OCR it, keeping each word's bounding box.
3. Copy the original page (its vector art -- the lines that draw table
   borders -- is intact and worth keeping).
4. Remove the broken text objects, leaving the line art and images alone.
5. Write the OCR words back as *invisible* text at their original positions.

Step 4 is what makes the output usable: the extracted text layer is clean
real text rather than broken glyphs interleaved with good text, and the table
rules survive because the rules are not text.

Requires the `tesseract` binary on PATH.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

try:
    import pymupdf
except ImportError:  # pragma: no cover
    sys.exit("pymupdf is required:  pip install pymupdf")

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Legibility
#
# This repeats the rule used by the table-extractor skill. It is duplicated
# rather than imported so each skill stays self-contained and portable, but
# the two must agree: if this says a page is readable, the table extractor
# will try to read it, and any disagreement shows up as a page that one tool
# trusts and the other skips.
# ---------------------------------------------------------------------------

PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


def _is_pua(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in PUA_RANGES)


def legibility(text):
    """Classify extracted page text. Returns (state, reason, pua_ratio).

    Judged on what the extractor actually returned, never on what the font
    table implies. An earlier heuristic called any /Identity-H page with low
    character diversity "ciphered" and, on a real 1000-page volume, marked 133
    perfectly readable pages unreadable. A working ToUnicode CMap decodes
    /Identity-H perfectly well.
    """
    body = [c for c in text if not c.isspace()]
    n = len(body)
    pua = sum(1 for c in body if _is_pua(c))
    ratio = (pua / n) if n else 0.0
    if n == 0:
        return "empty", "no extractable characters", 0.0
    if ratio > 0.30:
        return (
            "unreadable",
            f"{ratio:.0%} of characters are Private Use Area codepoints",
            ratio,
        )
    return "text", "text layer reads as ordinary text", ratio


# ---------------------------------------------------------------------------
# Orientation
#
# A page whose content is turned 90 degrees OCRs as garbage: tesseract reads
# the sideways glyphs and returns letter-shaped noise. The horizontal strip of
# page furniture still reads perfectly, which makes the failure look partial
# and random rather than total -- and the table body, the part that mattered,
# comes back as "ODILVINHOANI OULNIO" where the page says "VALORE
# DECRESCENTE".
#
# So the page is turned upright before it is rendered. The output page keeps
# the original orientation, and the OCR word boxes are mapped back onto it.
# ---------------------------------------------------------------------------


def dominant_dir(page):
    """The page's dominant writing direction, as a (dx, dy) tuple.

    This survives a broken text layer, because it comes from the layout rather
    than from the character mapping. Left-to-right and right-to-left both
    count as upright; text running straight up or down the page does not.
    """
    try:
        d = page.get_text("dict")
    except Exception:
        return None
    counts = {}
    for block in d.get("blocks", ()):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            v = line.get("dir")
            if not v:
                continue
            key = (round(v[0], 2), round(v[1], 2))
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def derive_upright(doc, index, angle):
    """Return (new_doc, page) with the source page's content rotated.

    Embedding through show_pdf_page keeps the text and the vector art in one
    consistent coordinate space, so they stay aligned after the turn.
    """
    src = doc[index]
    r = src.rect
    out = pymupdf.open()
    if angle in (90, 270):
        page = out.new_page(width=r.height, height=r.width)
    else:
        page = out.new_page(width=r.width, height=r.height)
    page.show_pdf_page(page.rect, doc, index, rotate=angle)
    return out, page


def choose_rotation(doc, index):
    """Return 0, 90 or 270 -- the turn that brings the page's text upright.

    Both 90 and 270 end up horizontal and are near-identical in shape, so
    direction decides between them: left-to-right is preferred, because that
    is what almost every document is. A genuinely right-to-left document is
    left as it is, which is the correct outcome for it too.
    """
    d = dominant_dir(doc[index])
    if d is None or abs(d[0]) >= abs(d[1]):
        return 0
    chosen = None
    for angle in (90, 270):
        try:
            _tmp, q = derive_upright(doc, index, angle)
            qd = dominant_dir(q)
        except Exception:
            continue
        if qd is None or abs(qd[0]) < abs(qd[1]):
            continue
        if qd[0] > 0:
            return angle
        if chosen is None:
            chosen = angle
    return chosen if chosen is not None else 90


def unrotate_box(x0, y0, x1, y1, angle, W, H):
    """Map a box from the upright page back onto the original page.

    Measured against pymupdf's own output rather than guessed. A turn of 90
    maps content (X, Y) -> (Y, W - X); 270 maps it to (H - Y, X). The first
    attempt at these formulas was wrong for 90 and 270 and put the text
    mirrored into the corner of the page -- which looks exactly like an OCR
    failure and is not one, which is why it is now pinned by a test.
    """
    if angle == 90:
        return (W - y1, x0, W - y0, x1)
    if angle == 180:
        return (W - x1, H - y1, W - x0, H - y0)
    if angle == 270:
        return (y0, H - x1, y1, H - x0)
    return (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def have_tesseract():
    return shutil.which("tesseract") is not None


def tesseract_langs():
    """Languages tesseract has trained data for.

    `tesseract --list-langs` prints "ita (Italian)", one per line, after a
    header line. The code is the first field; the parenthesised part is a
    description and must not end up in the set.
    """
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return set()
    langs = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or "(" in line.split()[0] or line.lower().startswith("list of"):
            continue
        langs.add(line.split()[0])
    return langs


def ocr_page(pixmap, lang, dpi, psm=None, timeout=180):
    """Render-to-TSV. Returns a list of word dicts with pixel boxes.

    TSV rather than plain text, because the bounding boxes are the whole
    point: text written back without its original geometry is a paragraph
    where a table used to be, and every downstream column detector then
    disagrees with the page.
    """
    words = []
    # Parsed inside the `with`: the temporary directory is removed on exit, so
    # reading the TSV afterwards finds nothing.
    with tempfile.TemporaryDirectory() as td:
        img = os.path.join(td, "page.png")
        pixmap.save(img)
        cmd = ["tesseract", img, os.path.join(td, "out"), "-l", lang,
               "--dpi", str(int(dpi))]
        if psm is not None:
            cmd += ["--psm", str(psm)]
        # The config name goes last and on its own: tesseract treats every
        # trailing argument as a config, so `--psm 6 tsv` works and
        # `tsv --psm 6` silently ignores the --psm. Three page segmentation
        # modes returning byte-identical output is how that was caught.
        cmd += ["tsv"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return [], "tesseract timed out"
        tsv = os.path.join(td, "out.tsv")
        if not os.path.exists(tsv):
            return [], "tesseract produced no output"

        with open(tsv, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
                try:
                    if int(row.get("level", 9)) != 5:
                        continue
                    text = (row.get("text") or "").strip()
                    if not text:
                        continue
                    words.append({
                        "text": text,
                        "x": float(row["left"]),
                        "y": float(row["top"]),
                        "w": float(row["width"]),
                        "h": float(row["height"]),
                        "conf": float(row.get("conf", -1) or -1),
                    })
                except (KeyError, ValueError, TypeError):
                    continue
    return words, None


def overlay_text(page, words, dpi, min_conf=30, fontname="helv"):
    """Write invisible text at the OCR word positions.

    render_mode 3 is "neither fill nor stroke": the text is in the content
    stream and extracts normally, but draws nothing. The page therefore still
    looks like the original while carrying a readable text layer.
    """
    scale = 72.0 / dpi
    written = dropped = 0
    for w in words:
        if w["conf"] >= 0 and w["conf"] < min_conf:
            dropped += 1
            continue
        x = w["x"] * scale
        y = w["y"] * scale
        h = w["h"] * scale
        size = max(1.0, h * 0.82)
        try:
            page.insert_text(
                (x, y + h * 0.80),
                w["text"],
                fontname=fontname,
                fontsize=size,
                render_mode=3,
            )
            written += 1
        except Exception:
            dropped += 1
    return written, dropped


# ---------------------------------------------------------------------------


def check_langs(lang, warnings):
    if not have_tesseract():
        sys.exit(
            "tesseract is not on PATH.\n"
            "  Debian/Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-ita\n"
            "  macOS:          brew install tesseract tesseract-lang"
        )
    avail = tesseract_langs()
    want = [l for l in lang.split("+") if l]
    missing = [l for l in want if l not in avail]
    if missing:
        if want == ["eng"] or not avail:
            warnings.append(
                f"language pack(s) {missing} not installed; available: "
                f"{sorted(avail) or 'none'}"
            )
        else:
            sys.exit(
                f"language pack(s) {missing} not installed.\n"
                f"  available: {sorted(avail)}\n"
                "  install e.g.:  sudo apt install tesseract-ocr-ita"
            )
    return avail


def parse_pages(spec, total):
    if not spec:
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in out if 1 <= p <= total]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="OCR the unreadable pages of a PDF and give them a real "
                    "text layer, leaving readable pages untouched."
    )
    ap.add_argument("pdf")
    ap.add_argument("--out", help="output PDF (required unless --dry-run)")
    ap.add_argument("--sidecar", help="directory for per-page .txt (optional)")
    ap.add_argument("--pages", help="e.g. 600-700,12 (1-indexed)")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--image-dpi", type=int, default=150,
                    help="resolution of the rendered page image that replaces "
                         "the broken glyphs (default 150; lower is smaller "
                         "and still legible)")
    ap.add_argument("--lang", default="ita+eng",
                    help="tesseract languages (default ita+eng)")
    ap.add_argument("--min-conf", type=float, default=30,
                    help="drop OCR words below this confidence (default 30)")
    ap.add_argument("--psm", type=int, default=None,
                    help="tesseract page segmentation mode")
    ap.add_argument("--limit", type=int, default=0,
                    help="OCR at most N unreadable pages, then stop")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be OCR'd and the time estimate, "
                         "and change nothing")
    args = ap.parse_args(argv)

    warnings = []
    avail = check_langs(args.lang, warnings)

    if not args.out and not args.dry_run:
        sys.exit("--out is required (or use --dry-run)")

    doc = pymupdf.open(args.pdf)
    total = doc.page_count
    wanted = parse_pages(args.pages, total)
    print(f"{args.pdf}: {total} page(s), considering {len(wanted)}")

    states = {}
    targets = []
    for pno in wanted:
        try:
            text = doc[pno - 1].get_text("text")
        except Exception as exc:
            state, reason, _ = "unreadable", f"extraction failed: {exc}", 0.0
        else:
            state, reason, _ = legibility(text)
        states[pno] = (state, reason)
        if state != "text":
            targets.append(pno)

    readable = len(wanted) - len(targets)
    print(f"  readable, left alone : {readable}")
    print(f"  unreadable, need OCR : {len(targets)}")
    if args.limit and len(targets) > args.limit:
        targets = targets[: args.limit]
        print(f"  limited by --limit   : {len(targets)}")

    for w in warnings:
        print(f"  ! {w}")

    if args.dry_run:
        secs = len(targets) * 1.5
        print(f"\nDRY RUN -- nothing written.")
        print(f"  would OCR {len(targets)} page(s) at {args.dpi} dpi, "
              f"lang={args.lang}")
        print(f"  rough estimate: {secs/60:.0f}-{secs*3/60:.0f} min "
              f"({secs:.0f}-{secs*3:.0f} s/page is typical)")
        if targets:
            print(f"  first pages: {targets[:10]}")
        return 0

    if not targets:
        print("\nnothing to do: every page in scope already has a text layer")
        return 0

    out = pymupdf.open()
    sidecar_dir = None
    if args.sidecar:
        sidecar_dir = os.path.abspath(args.sidecar)
        os.makedirs(sidecar_dir, exist_ok=True)

    report = []
    t0 = time.time()
    tesseract = tesseract_langs()

    for idx, pno in enumerate(wanted, 1):
        src = doc[pno - 1]
        state, reason = states[pno]

        if state == "text":
            out.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1)
            continue

        # A page whose content is turned 90 degrees has to be turned upright
        # before tesseract sees it, or it reads sideways glyphs and returns
        # letter-shaped noise. The horizontal page furniture still comes out
        # clean, which makes the failure look random rather than total and
        # hides the fact that the body is simply unread.
        try:
            angle = choose_rotation(doc, pno - 1)
        except Exception as exc:
            warnings.append(f"page {pno}: rotation check failed ({exc})")
            angle = 0

        result = None
        words = []
        written = dropped = 0

        try:
            workdoc, work = derive_upright(doc, pno - 1, angle)
        except Exception as exc:
            warnings.append(f"page {pno}: derotation failed ({exc})")
            angle = 0
            try:
                workdoc, work = derive_upright(doc, pno - 1, 0)
            except Exception as exc2:
                workdoc = work = None
                result = f"copy failed: {exc2}"

        if result is None:
            # Rendered before anything is removed: this is the only copy of
            # what the page looks like.
            try:
                img = work.get_pixmap(dpi=args.image_dpi)
                ocr_pix = work.get_pixmap(dpi=args.dpi)
            except Exception as exc:
                result = f"render failed: {exc}"

        if result is None:
            lang = args.lang
            missing = [l for l in lang.split("+") if l not in tesseract]
            if missing:
                fallback = "+".join(l for l in lang.split("+") if l in tesseract)
                if not fallback:
                    fallback = (
                        "eng" if "eng" in tesseract else sorted(tesseract)[0]
                    )
                warnings.append(
                    f"page {pno}: {missing} unavailable, using {fallback}"
                )
                lang = fallback
            words, err = ocr_page(ocr_pix, lang, args.dpi, args.psm)
            if err:
                result = err

        if result is None:
            # Strip the broken glyphs, then put the page back as an image.
            #
            # On a page whose fonts are broken the glyph outlines ARE the
            # visible content, so redaction alone leaves a blank page with the
            # table's cell shading still on it -- which reads as a rendering
            # bug rather than the data loss it is. The line art survives
            # redaction, which makes that failure look deceptively plausible.
            work.add_redact_annot(work.rect)
            try:
                work.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                    text=pymupdf.PDF_REDACT_TEXT_REMOVE,
                )
            except Exception as exc:
                warnings.append(f"page {pno}: redaction failed ({exc})")
            work.insert_image(work.rect, pixmap=img)

            # Written while the page is still upright, so the text runs with
            # the table rather than across it.
            written, dropped = overlay_text(work, words, args.dpi, args.min_conf)

            # Re-embedded at the orientation the original had. Measured at
            # 0.00 pt of error; mapping the boxes back by hand was both
            # fiddlier and wrong on the first attempt.
            page = out.new_page(width=src.rect.width, height=src.rect.height)
            page.show_pdf_page(page.rect, workdoc, 0, rotate=(360 - angle) % 360)

        if result is not None:
            warnings.append(f"page {pno}: {result}")
            out.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1)
            report.append({"page": pno, "state": state, "result": result})
            continue

        if sidecar_dir:
            with open(
                os.path.join(sidecar_dir, f"p{pno:04d}.txt"), "w", encoding="utf-8"
            ) as fh:
                fh.write(" ".join(w["text"] for w in words))

        confs = [w["conf"] for w in words if w["conf"] >= 0]
        report.append({
            "page": pno,
            "state": state,
            "lang": lang,
            "derotated": angle,
            "words_found": len(words),
            "words_written": written,
            "words_dropped_low_conf": dropped,
            "mean_conf": round(sum(confs) / len(confs), 1) if confs else None,
        })

        if idx % 5 == 0 or idx == len(wanted):
            done = len([r for r in report if r.get("words_written") is not None])
            rate = (time.time() - t0) / max(1, done)
            left = (len(targets) - done) * rate
            print(
                f"  [{idx}/{len(wanted)}] ocr {done}/{len(targets)}  "
                f"{rate:.1f}s/page  ~{left/60:.0f} min left",
                flush=True,
            )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    out.save(args.out)
    out.close()

    manifest = {
        "source": os.path.abspath(args.pdf),
        "output": os.path.abspath(args.out),
        "generated_by": f"ungarble-pdf/{VERSION}",
        "params": {
            "dpi": args.dpi, "lang": args.lang, "min_conf": args.min_conf,
            "psm": args.psm, "pages": args.pages,
        },
        "pages": len(wanted),
        "left_untouched_readable": readable,
        "pages_ocred": len([r for r in report if r.get("words_written") is not None]),
        "warnings": warnings,
        "page_report": report,
    }
    with open(args.out + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    done = [r for r in report if r.get("words_written") is not None]
    confs = [r["mean_conf"] for r in done if r.get("mean_conf") is not None]
    print(f"\nwrote {args.out}")
    print(f"wrote {args.out}.manifest.json")
    if done:
        print(
            f"\nOCR'd {len(done)} page(s), "
            f"{sum(r['words_found'] for r in done)} words found, "
            f"mean confidence "
            f"{sum(confs)/len(confs):.1f}" if confs else ""
        )
    if warnings:
        print(f"\n{len(warnings)} warning(s); see the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
