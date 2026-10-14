#!/usr/bin/env python3
"""
selftest.py -- build synthetic PDFs that exercise every branch of
extract_tables.py and assert on the output.

A table extractor that silently returns nothing and a table extractor that
correctly reports "this page has no text layer" are the same program as far as
a caller is concerned. These assertions exist to tell them apart.

    python selftest.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

import pymupdf

# The import below would otherwise leave a __pycache__ directory inside the
# skill, which is not something to commit.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "et", os.path.join(HERE, "extract_tables.py")
)
et = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(et)

PW, PH = 595, 842
DATA = [
    ["Region", "2023", "2024", "Change"],
    ["North", "120", "145", "+21%"],
    ["South", "98", "101", "+3%"],
    ["East", "210", "198", "-6%"],
    ["West", "76", "90", "+18%"],
]
XS = [50, 170, 290, 410, 530]
YS = [100, 125, 150, 175, 200, 225]

# Distinct data, so that two ruled tables on consecutive pages are two tables
# and not a page-spanning one. A fixture that prints identical tables twice
# cannot tell a false merge apart from a correct stitch.
DATA2 = [
    ["Region", "2023", "2024", "Change"],
    ["Central", "55", "61", "+11%"],
    ["Coastal", "31", "29", "-6%"],
    ["Inland", "88", "95", "+8%"],
    ["Lakes", "12", "19", "+58%"],
]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def draw(page, xs, ys, data, as_rects=False, skip_right=False):
    xr = xs[:-1] if skip_right else xs
    if as_rects:
        for x in xr:
            page.draw_rect(pymupdf.Rect(x, ys[0], x + 1, ys[-1]), color=None, fill=(0, 0, 0))
        for y in ys:
            page.draw_rect(pymupdf.Rect(xs[0], y, xs[-1], y + 1), color=None, fill=(0, 0, 0))
    else:
        for x in xr:
            page.draw_line(pymupdf.Point(x, ys[0]), pymupdf.Point(x, ys[-1]), width=0.8)
        for y in ys:
            page.draw_line(pymupdf.Point(xs[0], y), pymupdf.Point(xs[-1], y), width=0.8)
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            page.insert_text((xs[c] + 6, ys[r] + 17), val, fontsize=10)


def build(tmp):
    doc = pymupdf.open()

    p = doc.new_page(width=PW, height=PH)
    p.insert_text((50, 60), "Ruled, line rules", fontsize=14)
    draw(p, XS, YS, DATA)

    p = doc.new_page(width=PW, height=PH)
    p.insert_text((50, 60), "Ruled, one-point rect rules", fontsize=14)
    draw(p, XS, YS, DATA2, as_rects=True)

    p = doc.new_page(width=PW, height=PH)
    p.insert_text((50, 60), "Borderless", fontsize=14)
    y = 100
    for row in [["Product", "Price", "Stock"], ["Widget", "12.50", "340"],
                ["Gadget", "7.25", "89"], ["Doohickey", "99.00", "12"],
                ["Thing", "45.75", "260"]]:
        p.insert_text((60, y), row[0], fontsize=10)
        p.insert_text((260, y), row[1], fontsize=10)
        p.insert_text((420, y), row[2], fontsize=10)
        y += 24

    p = doc.new_page(width=PW, height=PH)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 150))
    pix.set_rect(pix.irect, (235, 235, 230))
    p.insert_image(pymupdf.Rect(60, 100, 460, 425), pixmap=pix)

    src = pymupdf.open()
    sp = src.new_page(width=PW, height=PH)
    draw(sp, XS, YS, DATA)
    tp = doc.new_page(width=PW, height=PH)
    tp.show_pdf_page(pymupdf.Rect(0, 0, PH, PW), src, 0, rotate=90)

    p = doc.new_page(width=PW, height=PH)
    p.insert_text((50, 60), "Missing right border", fontsize=14)
    draw(p, XS, YS, DATA, skip_right=True)

    # A table spanning a page break: page 7 runs off the bottom edge, page 8
    # resumes at the top and repeats the header. Both are preconditions of
    # is_continuation(), so the fixture has to honour them.
    p = doc.new_page(width=PW, height=PH)
    p.insert_text((50, 60), "Continued", fontsize=14)
    draw(p, XS, [520, 545, 570, 595, 620, 645], [DATA[0], DATA[1], DATA[2]])

    p = doc.new_page(width=PW, height=PH)
    p.insert_text((50, 60), "Continued", fontsize=14)
    draw(p, XS, [120, 145, 170, 195, 220, 245], [DATA[0], DATA[3], DATA[4]])

    path = os.path.join(tmp, "fixture.pdf")
    doc.save(path)
    return path, doc.page_count


def run(path, tmp, extra=None):
    out = os.path.join(tmp, "out")
    # 'both' so the whitespace path is exercised; ruled alone is the default
    # precisely because whitespace is opt-in.
    argv = [path, "--out", out, "--method", "both"] + (extra or [])
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        et.main(argv)
    with open(os.path.join(out, "tables.json"), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    print("text classification")
    check(
        "plain text is readable",
        et.analyse_text("Region 2023 North 120", [], 0)["state"] == "text",
    )
    check(
        "empty page with no images",
        et.analyse_text("   \n ", [], 0)["state"] == "empty",
    )
    check(
        "image-only page is no-text-layer, not empty",
        et.analyse_text("", [], 3)["state"] == "no-text-layer",
    )
    garbled = "".join(chr(0xE000 + (i % 7)) for i in range(400))
    check(
        "Private Use Area text is garbled",
        et.analyse_text(garbled, ["Helvetica Identity-H"], 0)["state"] == "ciphered"
        or et.analyse_text(garbled, ["Helvetica"], 0)["state"] == "garbled",
    )
    check(
        "PUA text with an Identity-H font is ciphered, not garbled",
        et.analyse_text(garbled, ["Font Identity-H"], 0)["state"] == "ciphered",
    )
    # The regression this pins: an /Identity-H font whose ToUnicode CMap works
    # decodes perfectly. Inferring unreadability from the font table alone
    # marked 133 readable pages on a real volume as unreadable and dropped
    # their tables.
    check(
        "Identity-H font that decodes cleanly is NOT called ciphered",
        et.analyse_text(
            "Presidenza del Consiglio dei Ministri " * 30,
            ["Font Identity-H"],
            0,
        )["state"]
        == "text",
    )
    check(
        "DEAD and DEGRADED pages are all excluded from extraction",
        et.DEAD | et.DEGRADED
        == {"no-text-layer", "empty", "unreadable", "garbled", "ciphered"},
    )

    with tempfile.TemporaryDirectory() as tmp:
        path, npages = build(tmp)
        print(f"\nend to end ({npages} pages)")
        d = run(path, tmp)
        by_page = {}
        for t in d["tables"]:
            by_page.setdefault(t["page_start"], []).append(t)

        t1 = by_page.get(1, [None])[0]
        check("page 1 ruled by line segments", t1 and t1["method"] == "ruled")
        check(
            "page 1 shape and header",
            t1 and t1["n_rows"] == 5 and t1["n_cols"] == 4
            and t1["header"] == ["Region", "2023", "2024", "Change"],
            t1 and f"{t1['n_rows']}x{t1['n_cols']} {t1['header']}",
        )
        check(
            "page 1 preserves row order",
            t1 and [r[0] for r in t1["rows_data"]] == ["North", "South", "East", "West"],
            t1 and str([r[0] for r in t1["rows_data"]]),
        )

        t2 = by_page.get(2, [None])[0]
        check(
            "page 2 one-point rect rules read as a table",
            t2 and t2["method"] == "ruled" and t2["n_rows"] == 5 and t2["n_cols"] == 4,
            t2 and f"{t2['n_rows']}x{t2['n_cols']}",
        )
        check(
            "two identical-looking tables on facing pages are NOT merged",
            t1 and t2 and t1["page_end"] == 1 and t2["page_end"] == 2
            and t1["id"] != t2["id"],
            t1 and f"p1 end {t1['page_end']}, p2 end {t2['page_end']}",
        )

        t3 = by_page.get(3, [None])[0]
        check(
            "page 3 borderless found by whitespace",
            t3 and t3["method"] == "whitespace" and t3["n_cols"] == 3
            and t3["header"] == ["Product", "Price", "Stock"],
            t3 and f"{t3['method']} {t3['header']}",
        )

        pre = {p["page_number"]: p["state"] for p in d["preflight"]}
        check("page 4 classified no-text-layer", pre.get(4) == "no-text-layer")
        check(
            "page 4 yields no table and says so",
            4 not in by_page
            and any("missing, not absent" in w for w in d["warnings"]),
        )

        t5 = by_page.get(5, [None])[0]
        check("page 5 rotated content was turned", t5 and t5.get("derotated") in (90, 270))
        check(
            "page 5 turned the right way, not mirrored",
            t5 and t5["header"] == ["Region", "2023", "2024", "Change"],
            t5 and str(t5["header"]),
        )
        check(
            "page 5 row order preserved",
            t5 and [r[0] for r in t5["rows_data"]] == ["North", "South", "East", "West"],
            t5 and str([r[0] for r in t5["rows_data"]]),
        )

        t6 = by_page.get(6, [None])[0]
        check(
            "page 6 missing border is reported, not silent",
            t6 and any("outside the ruled grid" in w for w in t6["warnings"]),
            t6 and str(t6["warnings"]),
        )

        check(
            "pages 7-8 stitched into one table across the page break",
            7 in by_page
            and by_page[7][0]["page_end"] == 8
            and by_page[7][0]["stitched"],
            str([(t["page_start"], t["page_end"]) for t in d["tables"] if t["page_start"] >= 7]),
        )
        check(
            "the continuation page is not also emitted separately",
            8 not in by_page,
        )
        check(
            "stitched table has all four data rows, header once",
            7 in by_page and len(by_page[7][0]["rows_data"]) == 4
            and by_page[7][0]["header"] == ["Region", "2023", "2024", "Change"],
        )

        ids = [t["id"] for t in d["tables"]]
        check("table ids are unique", len(ids) == len(set(ids)))
        d2 = run(path, tmp)
        check(
            "re-running gives identical table ids",
            [t["id"] for t in d2["tables"]] == ids,
        )

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
