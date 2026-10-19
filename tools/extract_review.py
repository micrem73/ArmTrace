#!/usr/bin/env python3
"""The extraction next to the source pages, for a person to review.

    python3 tools/extract_review.py 2025
        -> Out/<year>/extract-review.html

Reads `Out/<year>/armtrace-<year>.sqlite` (ExtractTables.py) and renders the
chosen source pages beside the rows read from each. Per table: rows by kind,
every check that is not a PASS, and for a FAIL on a subtotal the lines whose
value equals the gap (a hint for the reviewer, never applied).
"""
import argparse
import base64
import html
import re
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import extract, layout, source  # noqa: E402

# Pages that show each reading rule at work (2025 pilot).
DEFAULT_PAGES = {
    "MAE/T2": [27],
    "DIFESA/4": [254, 256],
    "DOG/A1C2/M1": [858, 859, 867],
    "MEF/EE": [287, 289, 327],
    "MAE/A1": [70, 147, 185, 606],
    "MAE/A2": [607, 611],
    "MEF/II": [393, 427],
    "DOG/A1C2/M": [471, 742],
}

CSS = """
body{font:14px/1.45 system-ui,sans-serif;margin:16px;color:#1d1d1f;background:#fff}
h1{font-size:22px}h2{font-size:18px;margin-top:40px;border-top:2px solid #ccc;padding-top:12px}
h3{font-size:15px;margin:24px 0 6px}.pair{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
.pair img{max-width:min(560px,100%);border:1px solid #bbb}.rows{flex:1;min-width:320px;overflow-x:auto}
table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #ddd;padding:2px 5px;vertical-align:top}
th{background:#f3f3f3;text-align:left}.k-group td{background:#eef4ff;font-weight:600}
.k-subtotal td{background:#fff6dc}.k-total td{background:#ffe4e4;font-weight:600}
.FAIL{color:#b00020;font-weight:600}.ROUNDING{color:#8a5a00}.UNCHECKED{color:#555}.PASS{color:#0a6b2d}
.note{color:#555;font-size:12px}code{background:#f3f3f3;padding:0 3px}
"""


def page_image(year, key, pdf_page):
    for sp in source.table_pages(year, key):
        if sp.pdf_page == pdf_page:
            pix = sp.page.get_pixmap(dpi=80, clip=sp.clip)
            return base64.b64encode(pix.tobytes("png")).decode()
    return None


def rows_table(db, name, pdf_page):
    cur = db.execute(f"SELECT * FROM {name} WHERE pdf_page=? ORDER BY row_ord", (pdf_page,))
    cols = [d[0] for d in cur.description]
    keep = [i for i, c in enumerate(cols)
            if c not in ("year", "source_file", "pdf_page", "printed_page")
            and not c.endswith("_raw")]
    out = ["<table><tr>" + "".join(f"<th>{html.escape(cols[i])}</th>" for i in keep) + "</tr>"]
    for row in cur.fetchall():
        kind = row[cols.index("row_kind")]
        out.append(f'<tr class="k-{kind}">' + "".join(
            f"<td>{html.escape('' if row[i] is None else str(row[i]))}</td>" for i in keep)
            + "</tr>")
    return "\n".join(out) + "</table>"


def gap_hints(db, name, key, check, printed, computed):
    """Lines of the failing group whose value equals the gap."""
    if not check.startswith("subtotal p") or computed is None:
        return ""
    gap = Decimal(str(computed)).quantize(Decimal("0.01")) - extract.gra.parse_number(printed)
    col = check.split()[2]
    group = re.search(r"\((.+)\)$", check)
    if not group:
        return ""
    hits = db.execute(
        f"SELECT pdf_page, valuta, ammontare_raw, {col}_raw, val_fini_dog_raw, data_raw "
        f"FROM {name} WHERE row_kind='data' AND operatore=? AND "
        f"(abs(ammontare - ?) < 0.005 OR abs({col} - ?) < 0.005)",
        (group[1], float(gap), float(gap))).fetchall()
    if not hits:
        return f" gap {gap}; no single line of {html.escape(group[1])} equals it"
    return (f" gap {gap}; lines of {html.escape(group[1])} worth it: "
            + "; ".join(f"p{h[0]} {h[1] or '–'} {h[2] or ''} EURO {h[3] or '–'} "
                        f"VFD {h[4] or '–'} {h[5] or ''}" for h in hits))


def failing_pages(db, name, key):
    """Pages of every FAIL: the subtotal's page, the lines worth its gap, the summary."""
    pages = set()
    for check, printed, computed in db.execute(
            "SELECT check_name, printed, computed FROM check_result "
            "WHERE table_key=? AND status='FAIL'", (key,)).fetchall():
        if check.startswith("subtotal p"):
            pages.add(int(check.split()[1][1:]))
            pages.update(int(p) for p in re.findall(r"\bp(\d+) ", gap_hints(
                db, name, key, check, printed, computed)))
        else:
            pages.update(p for (p,) in db.execute(
                "SELECT DISTINCT pdf_page FROM printed_total WHERE table_key=?", (key,)))
    return pages


def build(year, pages, path):
    db = sqlite3.connect(layout.year_dir(year) / f"armtrace-{year}.sqlite")
    out = [f"<!doctype html><meta charset='utf-8'><title>Extraction review {year}</title>"
           f"<style>{CSS}</style><h1>Extraction review, {year}</h1>",
           "<p class='note'>Rows read from the source volumes (ExtractTables.py) beside "
           "the source page they come from. Group headings blue, subtotals yellow, "
           "totals red. Every check that is not a PASS is listed.</p>"]
    for key, wanted in pages.items():
        name = extract.sql_name(key)
        kinds = db.execute(f"SELECT row_kind, count(*) FROM {name} GROUP BY 1").fetchall()
        checks = db.execute("SELECT check_name, printed, computed, status FROM check_result "
                            "WHERE table_key=?", (key,)).fetchall()
        issues = db.execute("SELECT pdf_page, kind, detail FROM extract_issue "
                            "WHERE table_key=?", (key,)).fetchall()
        tally = {}
        for c in checks:
            tally[c[3]] = tally.get(c[3], 0) + 1
        out.append(f"<h2>{html.escape(key)} <code>{name}</code></h2>")
        out.append("<p>Rows: " + ", ".join(f"{k} {n}" for k, n in kinds)
                   + ". Checks: " + (", ".join(f"<span class='{s}'>{s} {n}</span>"
                                               for s, n in sorted(tally.items()))
                                     or "none printed") + f". Issues: {len(issues)}.</p>")
        bad = [c for c in checks if c[3] != "PASS"]
        if bad:
            out.append("<table><tr><th>check</th><th>printed</th><th>from the rows</th>"
                       "<th>status</th></tr>")
            for c in bad:
                hint = gap_hints(db, name, key, *c[:3]) if c[3] == "FAIL" else ""
                out.append(f"<tr><td>{html.escape(c[0])}{hint}</td><td>{c[1]}</td>"
                           f"<td>{c[2] or ''}</td><td class='{c[3]}'>{c[3]}</td></tr>")
            out.append("</table>")
        for p, kind, detail in issues:
            out.append(f"<p class='note'>issue p{p} {kind}: {html.escape(detail)}</p>")
        for p in sorted(set(wanted) | failing_pages(db, name, key)):
            img = page_image(year, key, p)
            printed = db.execute(f"SELECT printed_page FROM {name} WHERE pdf_page=? LIMIT 1",
                                 (p,)).fetchone()
            out.append(f"<h3>PDF page {p}" + (f", printed {printed[0]}" if printed else "")
                       + "</h3><div class='pair'>")
            if img:
                out.append(f"<img alt='source page {p}' src='data:image/png;base64,{img}'>")
            totals = db.execute("SELECT label, value_raw FROM printed_total WHERE "
                                "table_key=? AND pdf_page=?", (key, p)).fetchall()
            body = rows_table(db, name, p)
            if totals:
                body += "<p>Printed totals read from this page:</p><table>" + "".join(
                    f"<tr><td>{html.escape(t[0]) or '(no label)'}</td><td>{t[1]}</td></tr>"
                    for t in totals) + "</table>"
            notes = db.execute("SELECT text FROM table_note WHERE table_key=? AND pdf_page=?",
                               (key, p)).fetchall()
            if notes:
                body += "<p class='note'>Note: " + html.escape(" ".join(n[0] for n in notes)) + "</p>"
            out.append(f"<div class='rows'>{body}</div></div>")
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("year")
    parser.add_argument("--only", action="append", help="one table (repeatable), "
                        "written to Out/.work/<year>/ so the full page is kept")
    args = parser.parse_args()
    if args.only:
        build(args.year, {k: DEFAULT_PAGES.get(k, []) for k in args.only},
              layout.work_year(args.year) / "extract-review-only.html")
    else:
        build(args.year, DEFAULT_PAGES, layout.year_dir(args.year) / "extract-review.html")
