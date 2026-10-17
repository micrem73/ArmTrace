#!/usr/bin/env python3
"""Collect the checkers' verdicts into one page a person reviews.

    python3 tools/review_report.py 2025 [--keep-png]

Reads Out/REVIEW/<year>/*/verdict.json and the table list, and writes
Out/REVIEW/<year>/report.html (self-contained apart from the PNGs beside it,
links relative, so it opens from file://) and Out/REVIEW/<year>/summary.tsv.
Tables with no verdict yet are listed as PENDING. FAIL and UNSURE come first,
each with its rendered pages next to the verdict.

Last step: the rendered PNGs of every PASS table are deleted (the report does
not show them, and a re-check renders them again). FAIL, UNSURE and PENDING
keep theirs. --keep-png skips the deletion.
"""
import csv
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORDER = {"FAIL": 0, "UNSURE": 1, "PENDING": 2, "PASS": 3}
IMAGE_ORDER = ["source-before", "source-first", "file-first", "file-second", "file-mid-1",
               "file-mid-2", "file-mid-3", "file-last", "source-after"]


def main(year, keep_png=False):
    review = ROOT / "Out" / "REVIEW" / year
    with open(ROOT / "Out" / f"tables-{year}.tsv", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t") if r["file"]]
    entries = []
    for row in rows:
        folder = review / row["key"].replace("/", "_")
        vfile = folder / "verdict.json"
        verdict = json.loads(vfile.read_text()) if vfile.exists() else None
        entries.append((row, folder, verdict))
    entries.sort(key=lambda e: (ORDER[(e[2] or {}).get("verdict", "PENDING")],
                                e[0]["key"]))

    counts = {}
    with open(review / "summary.tsv", "w", encoding="utf-8") as fh:
        fh.write("key\tverdict\tfile\tcause\n")
        for row, _folder, v in entries:
            word = (v or {}).get("verdict", "PENDING")
            counts[word] = counts.get(word, 0) + 1
            cause = (v or {}).get("cause", "").replace("\t", " ").replace("\n", " ")
            fh.write(f"{row['key']}\t{word}\t{row['file']}\t{cause}\n")

    esc = html.escape
    parts = [f"<title>Review {year}</title>", STYLE,
             f"<h1>Table review {esc(year)}</h1>",
             "<p>" + " · ".join(f"<b>{esc(k)}</b> {n}" for k, n in
                               sorted(counts.items(), key=lambda kv: ORDER[kv[0]]))
             + "</p>"]
    for row, folder, v in entries:
        word = (v or {}).get("verdict", "PENDING")
        parts.append(f'<section class="{word.lower()}"><h2>{esc(row["key"])} '
                     f'<span class="v">{word}</span></h2>')
        parts.append(f'<p class="meta">{esc(row["file"])} · segments '
                     f'{esc(row["segments"])} · printed {esc(row["printed"])} · '
                     f'{esc(row["pages"])} pages'
                     + (f' · {esc(row["notes"])}' if row["notes"] else "") + "</p>")
        if v:
            parts.append(f'<p>{esc(v.get("cause", ""))}</p>')
            if v.get("suggested_bounds"):
                parts.append(f'<p>Suggested bounds: {esc(v["suggested_bounds"])}</p>')
            parts.append("<table><tr><th>check</th><th>result</th><th>seen</th>"
                         "<th>image</th></tr>")
            for name, c in (v.get("checks") or {}).items():
                parts.append(f'<tr class="{esc(c.get("result", ""))}"><td>{esc(name)}'
                             f'</td><td>{esc(c.get("result", ""))}</td><td>'
                             f'{esc(c.get("seen", ""))}</td><td>'
                             f'{esc(c.get("image", ""))}</td></tr>')
            parts.append("</table>")
        if word != "PASS":
            parts.append('<div class="pages">')
            for label in IMAGE_ORDER:
                png = folder / f"{label}.png"
                if png.exists():
                    rel = png.relative_to(review).as_posix()
                    parts.append(f'<figure><a href="{esc(rel)}"><img src="{esc(rel)}" '
                                 f'loading="lazy"></a><figcaption>{label}</figcaption>'
                                 f'</figure>')
            parts.append("</div>")
        parts.append("</section>")
    (review / "report.html").write_text("\n".join(parts), encoding="utf-8")

    removed = 0
    if not keep_png:
        for _row, folder, v in entries:
            if (v or {}).get("verdict") == "PASS":
                for png in folder.glob("*.png"):
                    png.unlink()
                    removed += 1
    print(f"deleted {removed} PNG(s) of PASS tables" if not keep_png
          else "PNGs kept (--keep-png)")
    print(f"{review / 'report.html'}: " +
          ", ".join(f"{k} {n}" for k, n in sorted(counts.items(),
                                                   key=lambda kv: ORDER[kv[0]])))


STYLE = """<style>
body{font:14px system-ui,sans-serif;margin:16px;max-width:1400px}
section{border-left:6px solid #999;padding:4px 12px;margin:18px 0}
section.fail{border-color:#c0392b} section.unsure{border-color:#d68910}
section.pass{border-color:#1e8449} .v{font-size:13px;padding:2px 6px;border:1px solid}
.meta{color:#555} table{border-collapse:collapse} td,th{border:1px solid #ccc;padding:3px 6px}
tr.fail td{background:#fadbd8} tr.unreadable td{background:#fdebd0}
.pages{display:flex;flex-wrap:wrap;gap:8px} figure{margin:0}
img{width:220px;border:1px solid #ccc} figcaption{font-size:12px;text-align:center}
</style>"""


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--keep-png"]
    if len(args) != 1:
        raise SystemExit(__doc__)
    main(args[0], keep_png="--keep-png" in sys.argv[1:])
