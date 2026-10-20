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
import json
import re
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import extract, layout, source, views  # noqa: E402

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


def view_table(view, labels, pdf_page):
    """The rebuilt rows of one page: what the database alone gives back."""
    out = ["<table><tr>" + "".join(f"<th>{html.escape(x)}</th>" for x in labels) + "</tr>"]
    for row in (r for r in view if r["pdf_page"] == pdf_page):
        cells = row["cells"]
        if row["kind"] == "group":
            cells = [cells[0]] + [""] * (len(labels) - 1)
        elif row["kind"] == "summary":
            cells = [cells[0], cells[1]] + [""] * (len(labels) - 2)
        flags = [c for c in row["checks"] if c[3] != "PASS"]
        note = "".join(f"<br><span class='{c[3]}'>{c[3]}: rows give {c[2]}</span>"
                       for c in flags)
        out.append(f'<tr class="k-{row["kind"]}">' + "".join(
            f"<td>{html.escape(str(c))}</td>" for c in cells[:len(labels)])
            + (f"<td>{note}</td>" if note else "") + "</tr>")
    return "\n".join(out) + "</table>"


def gap_hints(db, name, key, check, printed, computed):
    """Facts of the failing group whose value equals the gap."""
    if not check.startswith("subtotal p") or computed is None:
        return ""
    gap = Decimal(str(computed)).quantize(Decimal("0.01")) - extract.gra.parse_number(printed)
    group = re.search(r"\((.+)\)$", check)
    view = db.execute("SELECT definition FROM view_def WHERE table_key=?", (key,)).fetchone()
    if not group or not view:
        return ""
    definition = json.loads(view[0])
    cols = [c for c in definition["summed"]]
    # MAE A1: a gap is often one EUR line's Ammontare (its EURO printed blank).
    amounts = cols + [c for c in ("ammontare",) if c not in cols]
    present = {d[1] for d in db.execute(f"PRAGMA table_info({name})")}
    amounts = [c for c in amounts if c in present]
    hits = db.execute(
        f"SELECT pdf_page, {', '.join(c + '_raw' for c in amounts)} FROM {name} "
        f"WHERE {definition['group']}=? AND ("
        + " OR ".join(f"abs({c} - ?) < 0.005" for c in amounts) + ")",
        (group[1], *[float(gap)] * len(amounts))).fetchall()
    if not hits:
        return f" gap {gap}; no single line of {html.escape(group[1])} equals it"
    return (f" gap {gap}; lines of {html.escape(group[1])} worth it: "
            + "; ".join(f"p{h[0]} " + " ".join(f"{c} {v or '–'}" for c, v in zip(amounts, h[1:]))
                        for h in hits))


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
                "SELECT DISTINCT pdf_page FROM printed_figure WHERE table_key=? "
                "AND kind!='subtotal'", (key,)))
    return pages


def sample_pages(year, key):
    """First, middle and last page of a table with no pages chosen for it."""
    pdf = [sp.pdf_page for sp in source.table_pages(year, key)]
    return sorted({pdf[0], pdf[len(pdf) // 2], pdf[-1]}) if pdf else []


def build(year, pages, path, db_path=None):
    db = sqlite3.connect(db_path or layout.year_dir(year) / f"armtrace-{year}.sqlite")
    out = [f"<!doctype html><meta charset='utf-8'><title>Extraction review {year}</title>"
           f"<style>{CSS}</style><h1>Extraction review, {year}</h1>",
           "<p class='note'>Each source page beside the table as the database rebuilds "
           "it (lib/views.py): the facts, with headings, subtotals and totals put back "
           "from the printed figures. Group headings blue, subtotals yellow, totals red; "
           "where a figure and its rows disagree, the rows' sum is shown beside it. "
           "Every check that is not a PASS is listed.</p>"]
    for key, wanted in pages.items():
        name = extract.sql_name(key)
        wanted = wanted or sample_pages(year, key)
        view, labels = [], []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
            view = views.render(*views.from_db(db, key, year), extract.specs()[key])
            labels = [x for (x,) in db.execute(
                "SELECT label_it FROM column_def WHERE table_key=? ORDER BY ordinal", (key,))]
        else:
            # Refused: no rows, only the reason and the pages it was refused on.
            db.execute(f"CREATE TEMP TABLE {name} (pdf_page INTEGER, printed_page INTEGER)")
        kinds = {}
        for row in view:
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        kinds = sorted(kinds.items())
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
            body = view_table(view, labels, p) if view else ""
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
    parser.add_argument("--db", type=Path, help="read this database (a table's own, "
                        "tools/import_queue.py) instead of Out/<year>/'s")
    parser.add_argument("--html", type=Path, help="write the page here")
    args = parser.parse_args()
    if args.only:
        build(args.year, {k: DEFAULT_PAGES.get(k, []) for k in args.only},
              args.html or layout.work_year(args.year) / "extract-review-only.html",
              args.db)
    else:
        build(args.year, DEFAULT_PAGES, layout.year_dir(args.year) / "extract-review.html")
