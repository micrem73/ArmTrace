#!/usr/bin/env python3
"""Publish a year: the table index for SQL extraction and the review page.

    python3 tools/build_index.py 2025 [--keep-png]

Reads the working files in Out/.work/<year>/ (step 1's table list and trash
runs, the checkers' verdicts), the verifier's report in Out/.work/verify/, and
the provenance each table PDF carries, and writes into Out/<year>/:

    tablesindex.sqlite       tables, segments, trash   -- for SQL extraction
    tablesindex.tsv          one row per table          -- the same, as text
    tablesindex-trash.tsv    one row per trash run      -- the same, as text
    review.html              the one page a person opens

The two TSV files are written from the same rows as the SQLite file, so they
cannot disagree. Last step: the checker PNGs of every PASS table are deleted
(--keep-png keeps them); FAIL, UNSURE and unchecked tables keep theirs, because
review.html shows them. Decided 2026-10-17 (DECISIONS.md).
"""
import csv
import html
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader  # noqa: E402

from lib import layout  # noqa: E402
from lib.patterns import REL_ARTICLE, REL_SALDI, REPORTS_DIR  # noqa: E402

ORDER = {"FAIL": 0, "UNSURE": 1, "PENDING": 2, "PASS": 3}
IMAGE_ORDER = ["source-before", "source-first", "file-first", "file-second",
               "file-mid-1", "file-mid-2", "file-mid-3", "file-last",
               "source-after"]

SCHEMA = """
CREATE TABLE tables (
    year TEXT, key TEXT, authority TEXT, article TEXT, code TEXT,
    status TEXT,            -- ok | provisional | reprint | listed-missing
    witness TEXT,           -- how step 1 found it: header, embedded, listing
    file TEXT,              -- Out/<year>/PDF/... relative to Out/<year>/
    pages INTEGER,
    printed TEXT,           -- printed page ranges, '1035-1042+1043-1104'
    bookmarks TEXT,         -- outline titles pointing into the table, ' | '
    tagged_pages INTEGER,   -- source pages carrying a tagged Table structure
    verify_verdict TEXT,    -- PASS | WARN | FAIL | SKIP (VerifyTables)
    verify_findings TEXT,
    check_verdict TEXT,     -- PASS | FAIL | UNSURE | PENDING (checker)
    check_cause TEXT,
    action TEXT,            -- next step from tools/recheck.py: done | check |
                            -- recheck | second-check | fix | human
    notes TEXT,
    PRIMARY KEY (year, key)
);
CREATE TABLE segments (     -- where each part of a table is in the source
    year TEXT, key TEXT, seq INTEGER,
    source_file TEXT, sha256 TEXT,
    pdf_from INTEGER, pdf_to INTEGER, printed_from INTEGER, printed_to INTEGER,
    cut INTEGER,            -- 1: the table holds a cut-out part of the pages
    clip TEXT,              -- cut only: JSON {pdf page: [x0, y0, x1, y1]}, the
                            -- part's band in source page coordinates
    PRIMARY KEY (year, key, seq)
);
CREATE TABLE trash (        -- source pages no table holds
    year TEXT, volume TEXT, source_file TEXT,
    pdf_from INTEGER, pdf_to INTEGER, printed_from INTEGER, printed_to INTEGER,
    pages INTEGER, reason TEXT, note TEXT,
    trash_file TEXT, trash_from INTEGER, trash_to INTEGER
);
"""
TABLE_COLUMNS = ["year", "key", "authority", "article", "code", "status",
                 "witness", "file", "pages", "printed", "bookmarks",
                 "tagged_pages", "verify_verdict", "verify_findings",
                 "check_verdict", "check_cause", "action", "notes", "sources"]
TRASH_COLUMNS = ["year", "volume", "source_file", "pdf_from", "pdf_to",
                 "printed_from", "printed_to", "pages", "reason", "note",
                 "trash_file", "trash_from", "trash_to"]


def read_tsv(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def tagged_table_pages(pdf_path):
    """{pdf page 1-based} carrying a /Table structure element (tagged PDF)."""
    try:
        import pymupdf
    except ImportError:
        return set()
    doc = pymupdf.open(pdf_path)
    page_of = {doc[i].xref: i + 1 for i in range(doc.page_count)}
    pages = set()

    def ref(value):
        try:
            return int(value.split()[0])
        except (ValueError, IndexError, AttributeError):
            return None

    def walk(xref, depth=0):
        kind, value = doc.xref_get_key(xref, "Pg")
        if kind == "xref" and ref(value) in page_of:
            pages.add(page_of[ref(value)])
        if depth > 6:
            return
        kind, value = doc.xref_get_key(xref, "K")
        if kind == "xref" and ref(value):
            walk(ref(value), depth + 1)
        elif kind == "array":
            for token in value.strip("[]").split(" R"):
                child = ref(token.strip())
                if child:
                    walk(child, depth + 1)

    for xref in range(1, doc.xref_length()):
        try:
            kind, value = doc.xref_get_key(xref, "S")
        except Exception:
            continue
        if kind == "name" and value == "/Table":
            walk(xref)
    return pages


def verifier_verdicts(year):
    """{relative pdf path: (verdict, findings)} from VerifyTables' JSON."""
    path = layout.verify_dir(ROOT) / f"verify-{year}.json"
    if not path.exists():
        return {}
    out = {}
    for entry in json.loads(path.read_text(encoding="utf-8")).get("files", []):
        checks = entry.get("checks") or []
        verdicts = [c.get("verdict") for c in checks]
        verdict = ("FAIL" if "FAIL" in verdicts else "WARN" if "WARN" in verdicts
                   else "PASS" if verdicts else "SKIP")
        findings = "; ".join(f"{c['id']}: {c.get('detail', '')}" for c in checks
                             if c.get("verdict") in ("FAIL", "WARN"))
        rel = Path(entry["path"])
        try:
            rel = rel.resolve().relative_to(layout.pdf_dir(year, ROOT).resolve())
        except ValueError:
            rel = Path(*rel.parts[-3:]) if len(rel.parts) >= 3 else rel
        out[rel.as_posix()] = (verdict, findings)
    return out


AMOUNT = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
TOTAL_LABEL = re.compile(r"^\s*(?:Totale\s+Complessivo|Totali)\b", re.IGNORECASE)
ROW_END = re.compile(r"^\s*Total[ei]\b", re.IGNORECASE)
ROW_TOLERANCE = 4.0     # pt: one printed row's label and amounts
COPY_SHARE = 0.5        # a copy repeats at least half of the annex amounts


def amounts(text):
    """Every non-zero euro amount printed in `text`, in order."""
    return [a for a in AMOUNT.findall(text) if a != "0,00"]


def grand_total(path):
    """Amounts on the last grand-total row of a PDF, read by position.

    By position because pypdf returns that row out of order: 2025 MEF/AA
    extracts "Totale Complessivo" alone on a line, its amounts lines earlier.
    The row is every line within ROW_TOLERANCE of the label across the writing
    direction, in reading order. Rotated tables (2024 MEF/KK1) write upwards,
    so their rows share an x, not a y."""
    try:
        import pymupdf
    except ImportError:
        return []
    found = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            lines = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    text = "".join(sp["text"] for sp in line["spans"]).strip()
                    if not text:
                        continue
                    x0, y0, _x1, y1 = line["bbox"]
                    dx, dy = line.get("dir", (1.0, 0.0))
                    if abs(dx) >= abs(dy):
                        lines.append((y0, x0 if dx > 0 else -x0, text))
                    else:
                        lines.append((x0, -y1 if dy < 0 else y0, text))
            for across, _along, text in lines:
                if TOTAL_LABEL.match(text):
                    row = sorted((along, t) for a, along, t in lines
                                 if abs(a - across) <= ROW_TOLERANCE)
                    found = amounts(" ".join(t for _along, t in row))
    return found


def euro(values):
    return " / ".join(f"€ {v}" for v in values) or "none printed"


def compare_exhibit(code, rel_text, annex_text, rel_total, annex_total):
    """Note for a MEF relation exhibit against the annex table of its code."""
    rel, annex = amounts(rel_text), amounts(annex_text)
    totals = (f"relation total {euro(rel_total or rel)}; "
              f"MEF/{code} total {euro(annex_total)}")
    # The totals agree when one row holds the other: a summary may print only
    # the grand total where the annex prints one per column (2025 KK1).
    if rel_total and annex_total and not (set(rel_total) <= set(annex_total)
                                          or set(annex_total) <= set(rel_total)):
        return f"differs from MEF/{code}: {totals}"
    if rel and set(rel) <= set(annex):
        if len(set(rel)) >= COPY_SHARE * len(set(annex)):
            return f"copy of MEF/{code}: all {len(rel)} amounts match"
        return (f"summary of MEF/{code}, matches: its {len(rel)} amount(s) all "
                f"appear in MEF/{code}; {totals}")
    return f"differs from MEF/{code}: {totals}"


def compare_saldi(rel_text, annex_totals):
    """{code: note} for each row of the uncoded 'Riepilogo saldi' whose code
    names an annex table, compared with that table's grand total. A row ends
    at the next code or at any total line ('Totale Esportazioni', 2025)."""
    rows, current = {}, None
    for line in rel_text.splitlines():
        if line.strip() in annex_totals:
            current = line.strip()
            rows[current] = []
        elif ROW_END.match(line):
            current = None
        elif current:
            rows[current] += amounts(line)
    notes = {}
    for code, values in rows.items():
        total = annex_totals[code]
        if sorted(values) == sorted(total):
            notes[code] = f"{code} matches MEF/{code} total {euro(total)}"
        else:
            notes[code] = (f"{code} differs: relation {euro(values)}; "
                           f"MEF/{code} total {euro(total)}")
    return notes


def pdf_text(path):
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    except Exception:
        return ""


def same_name_notes(year, rows):
    """{key: [note]}: MEF relation exhibits against the annex tables of the same
    code, on both sides (decided 2026-10-18). Read from the cut files, which
    hold only the exhibit; the source page also holds prose and its neighbours."""
    files = {r["key"]: layout.pdf_dir(year, ROOT) / r["file"] for r in rows if r["file"]}
    notes = {}
    for row in rows:
        if row["article"] != REL_ARTICLE or row["key"] not in files:
            continue
        rel_text = pdf_text(files[row["key"]])
        if row["code"] == REL_SALDI:
            annex = {r["code"]: grand_total(files[r["key"]]) for r in rows
                     if r["authority"] == row["authority"] and not r["article"]
                     and r["key"] in files}
            for code, note in compare_saldi(rel_text, annex).items():
                notes.setdefault(row["key"], []).append(note)
                notes.setdefault(f"{row['authority']}/{code}", []).append(
                    f"row of {row['key']}: {note}")
            continue
        annex_key = f"{row['authority']}/{row['code']}"
        if annex_key not in files:
            notes.setdefault(row["key"], []).append(f"no annex table {annex_key}")
            continue
        note = compare_exhibit(row["code"], rel_text, pdf_text(files[annex_key]),
                               grand_total(files[row["key"]]),
                               grand_total(files[annex_key]))
        notes.setdefault(row["key"], []).append(note)
        notes.setdefault(annex_key, []).append(f"relation exhibit {row['key']}: {note}")
    return notes


def index_rows(rows):
    """The table-list rows that become index rows: all but the copies
    resolve_repeats settled away, which share their key with the kept copy and
    whose pages the trash accounts for (2021: 61 MAE tables in tomo II)."""
    return [row for row in rows if row["status"] != "reprint"]


def build(year):
    year = str(year)
    rows = read_tsv(layout.tables_tsv(year, ROOT))
    if not rows:
        raise SystemExit(f"no table list at {layout.tables_tsv(year, ROOT)}: "
                         f"run step 1 first")
    trash_rows = read_tsv(layout.trash_tsv(year, ROOT))
    verified = verifier_verdicts(year)
    actions = {r["key"]: r for r in read_tsv(layout.work_year(year, ROOT) / "queue.tsv")}
    tagged = {pdf.name: tagged_table_pages(pdf)
              for pdf in sorted(Path(ROOT, REPORTS_DIR, year).glob("*.pdf"))}

    extra = same_name_notes(year, rows)
    tables, segments = [], []
    for row in index_rows(rows):
        key = row["key"]
        record = {"sources": [], "bookmarks": []}
        pdf = layout.pdf_dir(year, ROOT) / row["file"] if row["file"] else None
        if pdf and pdf.exists():
            try:
                record = json.loads(PdfReader(pdf).metadata["/Subject"])
            except Exception:
                pass
        tagged_pages = 0
        for seq, src in enumerate(record.get("sources", []), 1):
            a, b = src["pdf_pages"]
            tagged_pages += len([p for p in tagged.get(src["file"], ())
                                 if a <= p <= b])
            segments.append({
                "year": year, "key": key, "seq": seq,
                "source_file": src["file"], "sha256": src["sha256"],
                "pdf_from": a, "pdf_to": b,
                "printed_from": src["printed_pages"][0],
                "printed_to": src["printed_pages"][1],
                "cut": 1 if src.get("cut") else 0,
                "clip": json.dumps(src["clips"], sort_keys=True)
                        if src.get("clips") else None})
        check_file = layout.checks_dir(year, ROOT) / layout.key_dir(key) / "verdict.json"
        check = json.loads(check_file.read_text()) if check_file.exists() else {}
        verdict, findings = verified.get(row["file"], ("", ""))
        tables.append({
            "year": year, "key": key, "authority": row["authority"],
            "article": row["article"], "code": row["code"],
            "status": row["status"], "witness": row["source"],
            "file": f"PDF/{row['file']}" if row["file"] else "",
            "pages": int(row["pages"] or 0), "printed": row["printed"],
            "bookmarks": " | ".join(record.get("bookmarks", [])),
            "tagged_pages": tagged_pages,
            "verify_verdict": verdict, "verify_findings": findings,
            "check_verdict": check.get("verdict", "PENDING") if row["file"] else "",
            "check_cause": check.get("cause", "")
                           or actions.get(key, {}).get("reason", ""),
            "action": actions.get(key, {}).get("action", "") if row["file"] else "",
            "notes": "; ".join(filter(None, [row["notes"], *extra.get(key, [])])),
            "sources": "+".join(f"{s['source_file']}:{s['pdf_from']}-{s['pdf_to']}"
                                + ("(cut)" if s["cut"] else "")
                                for s in segments if s["key"] == key)})
    trash = [{"year": year, "volume": t["volume"], "source_file": t["source_file"],
              "pdf_from": int(t["start"]), "pdf_to": int(t["end"]),
              "printed_from": t["printed_from"], "printed_to": t["printed_to"],
              "pages": int(t["pages"]), "reason": t["reason"], "note": t["note"],
              "trash_file": f"TRASH/{t['trash_file']}",
              "trash_from": int(t["trash_from"]), "trash_to": int(t["trash_to"])}
             for t in trash_rows]

    write_sqlite(year, tables, segments, trash)
    write_tsv(layout.index_tsv(year, ROOT), TABLE_COLUMNS, tables)
    write_tsv(layout.index_trash_tsv(year, ROOT), TRASH_COLUMNS, trash)
    write_review(year, tables, trash)
    return tables, trash


def write_sqlite(year, tables, segments, trash, root=ROOT):
    path = layout.index_sqlite(year, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    cols = [c for c in TABLE_COLUMNS if c != "sources"]
    db.executemany(f"INSERT INTO tables ({','.join(cols)}) VALUES "
                   f"({','.join('?' * len(cols))})",
                   [[t[c] for c in cols] for t in tables])
    scols = ["year", "key", "seq", "source_file", "sha256", "pdf_from", "pdf_to",
             "printed_from", "printed_to", "cut", "clip"]
    db.executemany(f"INSERT INTO segments VALUES ({','.join('?' * len(scols))})",
                   [[s[c] for c in scols] for s in segments])
    db.executemany(f"INSERT INTO trash VALUES ({','.join('?' * len(TRASH_COLUMNS))})",
                   [[t[c] for c in TRASH_COLUMNS] for t in trash])
    db.commit()
    db.close()
    tmp.replace(path)


def write_tsv(path, columns, rows):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\t".join(columns) + "\n")
        for row in rows:
            fh.write("\t".join(str(row[c]).replace("\t", " ").replace("\n", " ")
                               for c in columns) + "\n")


def write_review(year, tables, trash):
    esc = html.escape
    here = layout.year_dir(year, ROOT)

    def rel(path):
        return esc(os.path.relpath(path, here).replace(os.sep, "/"))

    extracted = [t for t in tables if t["file"]]
    pages = sum(t["pages"] for t in extracted)
    count = lambda field, word: sum(1 for t in extracted if t[field] == word)
    # What needs someone: a person first (HUMAN-REVIEW.md), then the work
    # queued for the agents, then verifier failures nobody has queued.
    rank = {"human": 0, "fix": 1, "second-check": 2, "recheck": 3, "check": 4}
    attention = [t for t in extracted
                 if t["action"] in rank or t["verify_verdict"] == "FAIL"]
    attention.sort(key=lambda t: (rank.get(t["action"], 5), t["key"]))
    parts = [f"<title>Review {year}</title>", STYLE, f"<h1>ArmTrace {year}</h1>",
             "<table class='sum'>",
             f"<tr><th>tables</th><td>{len(extracted)}</td><th>pages in tables</th>"
             f"<td>{pages}</td><th>pages in trash</th><td>{sum(t['pages'] for t in trash)}"
             f"</td></tr>",
             "<tr><th>checker</th><td colspan=5>"
             + " · ".join(f"<b>{w}</b> {count('check_verdict', w)}"
                          for w in ("PASS", "FAIL", "UNSURE", "PENDING")) + "</td></tr>",
             "<tr><th>verifier</th><td colspan=5>"
             + " · ".join(f"<b>{w}</b> {count('verify_verdict', w)}"
                          for w in ("PASS", "WARN", "FAIL", "SKIP")) + "</td></tr>",
             "</table>",
             f"<h2>Needs a decision ({len(attention)})</h2>"]
    if not attention:
        parts.append("<p>Nothing: every table passes the checker and the verifier.</p>")
    for t in attention:
        word = t["check_verdict"]
        parts.append(f'<section class="{word.lower()}"><h3>{esc(t["key"])} '
                     f'<span class="v">next: {esc(t["action"] or "-")}</span> '
                     f'<span class="v">{word}</span> '
                     f'<span class="v">verifier {esc(t["verify_verdict"])}</span></h3>')
        parts.append(f'<p class="meta"><a href="{rel(here / t["file"])}">{esc(t["file"])}'
                     f'</a> · {esc(t["sources"])} · printed {esc(t["printed"])}</p>')
        for line in (t["check_cause"], t["verify_findings"], t["notes"]):
            if line:
                parts.append(f"<p>{esc(line)}</p>")
        folder = layout.checks_dir(year, ROOT) / layout.key_dir(t["key"])
        imgs = [folder / f"{label}.png" for label in IMAGE_ORDER
                if (folder / f"{label}.png").exists()]
        if imgs:
            parts.append('<div class="pages">' + "".join(
                f'<figure><a href="{rel(p)}"><img src="{rel(p)}" loading="lazy"></a>'
                f'<figcaption>{p.stem}</figcaption></figure>' for p in imgs) + "</div>")
        parts.append("</section>")

    parts.append(f"<h2>All tables ({len(extracted)})</h2><table class='all'>"
                 "<tr><th>table</th><th>pages</th><th>printed</th><th>checker</th>"
                 "<th>verifier</th><th>notes</th></tr>")
    for t in sorted(extracted, key=lambda t: t["key"]):
        parts.append(f"<tr><td><a href='{rel(here / t['file'])}'>{esc(t['key'])}</a></td>"
                     f"<td>{t['pages']}</td><td>{esc(t['printed'])}</td>"
                     f"<td class='{t['check_verdict'].lower()}'>{t['check_verdict']}</td>"
                     f"<td>{esc(t['verify_verdict'])}</td><td>{esc(t['notes'])}</td></tr>")
    parts.append("</table>")
    parts.append(f"<h2>Trash ({len(trash)} runs)</h2><table class='all'>"
                 "<tr><th>volume</th><th>source pages</th><th>printed</th><th>pages</th>"
                 "<th>reason</th><th>in trash file</th></tr>")
    for t in trash:
        parts.append(f"<tr><td>{esc(t['volume'])}</td><td>{t['pdf_from']}-{t['pdf_to']}</td>"
                     f"<td>{esc(str(t['printed_from']))}-{esc(str(t['printed_to']))}</td>"
                     f"<td>{t['pages']}</td><td>{esc(t['reason'])} {esc(t['note'])}</td>"
                     f"<td><a href='{rel(here / t['trash_file'])}'>{esc(t['trash_file'])}"
                     f"</a> p{t['trash_from']}-{t['trash_to']}</td></tr>")
    parts.append("</table>")
    layout.review_html(year, ROOT).write_text("\n".join(parts), encoding="utf-8")


def prune_pngs(year, tables):
    removed = 0
    for t in tables:
        if t["check_verdict"] == "PASS":
            folder = layout.checks_dir(year, ROOT) / layout.key_dir(t["key"])
            for png in folder.glob("*.png"):
                png.unlink()
                removed += 1
    return removed


STYLE = """<style>
:root{--fg:#1d1d1f;--bg:#fff;--muted:#666;--line:#ccc;--fail:#c0392b;--unsure:#b9770e;--pass:#1e8449}
@media (prefers-color-scheme:dark){:root{--fg:#eee;--bg:#1b1b1d;--muted:#aaa;--line:#444}}
body{font:14px system-ui,sans-serif;margin:16px;max-width:1400px;color:var(--fg);background:var(--bg)}
table{border-collapse:collapse;margin:8px 0} td,th{border:1px solid var(--line);padding:3px 6px;text-align:left}
section{border-left:6px solid var(--line);padding:4px 12px;margin:18px 0}
section.fail{border-color:var(--fail)} section.unsure{border-color:var(--unsure)}
.v{font-size:12px;padding:1px 6px;border:1px solid var(--line);margin-left:6px}
.meta{color:var(--muted)} td.fail{color:var(--fail);font-weight:bold}
td.pass{color:var(--pass)} td.unsure{color:var(--unsure)}
.pages{display:flex;flex-wrap:wrap;gap:8px} figure{margin:0}
img{width:220px;border:1px solid var(--line)} figcaption{font-size:12px;text-align:center}
</style>"""


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--keep-png"]
    if len(args) != 1:
        raise SystemExit(__doc__)
    tables, trash = build(args[0])
    removed = 0 if "--keep-png" in sys.argv else prune_pngs(args[0], tables)
    words = {}
    for t in tables:
        if t["file"]:
            words[t["check_verdict"]] = words.get(t["check_verdict"], 0) + 1
    print(f"published Out/{args[0]}/: {len(tables)} tables "
          f"({', '.join(f'{k} {v}' for k, v in sorted(words.items()))}), "
          f"{len(trash)} trash runs; deleted {removed} PNG(s) of PASS tables")
