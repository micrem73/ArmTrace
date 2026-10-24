#!/usr/bin/env python3
"""Score the table checker on cuts whose right answer is known.

    python3 tools/eval_checker.py 2025 [--jobs 4] [--only CASE] [--model M] [--keep]

The checker's verdict decides what a table needs next, and nothing else tests it:
an edit to .claude/agents/table-checker.md or a new model could pass a cut that
is one page short and every verdict would still say PASS. So each case in
tests/evals/checker-<year>.tsv takes a correct cut and, unless its mutation is
`none`, breaks it the way a code defect would (a page dropped or added, a wrong
code, printed page, article or authority in the row and the file name).

Each case runs in its own throwaway copy of the repo, outside it: the code and
.claude/ as they are now, In/ linked, and only that case's PDF and table list.
The real agent runs headless (`claude --agent table-checker -p`). Step 1 there is
a stub, so the checker judges the cut it is given instead of re-cutting it.

A FAIL case is caught by FAIL or UNSURE (both reach a person or a second
checker); a PASS case must get PASS. Writes Out/.work/<year>/eval/results.tsv and
each verdict beside it. Exit 1 when a wrong cut got PASS.

Costs tokens and minutes, so it is not part of `make check`. Run it after a
change to the checker, the check-year skill or a convention in CLAUDE.md, after
a model change, and before starting a new year.
"""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import layout, ontology  # noqa: E402
from lib.volumes import report_at  # noqa: E402

TIMEOUT = 1200
STUB = '''#!/usr/bin/env python3
print("eval: the table is already cut; go on with step 2 (tools/review_pages.py)")
'''


def read_tsv(path):
    with open(path, encoding="utf-8") as fh:
        lines = [line for line in fh if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def build_base(year, keys, base):
    """The correct cuts and the year's table list, from the current engine."""
    tables = layout.tables_tsv(year, base)
    cmd = [sys.executable, str(ROOT / "Reports2PDFTables.py"), year,
           "--out", str(base), "--tables", str(tables), "--no-trash"]
    for key in sorted(keys):
        cmd += ["--only", key]
    subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    with open(tables, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def mutate(year, row, src, dst):
    """Write the case's PDF to dst; return its table-list row."""
    row = dict(row)
    mutation = row.pop("mutation")
    if mutation == "none":
        shutil.copyfile(src, dst / row["file"])
        return row
    if "+" in row["segments"]:
        raise SystemExit(f"{row['key']}: mutations need a single-segment table")
    vol, span = row["segments"].split(":")
    a, b = map(int, span.split("-"))
    pa, pb = map(int, row["printed"].split("-"))
    pages = list(PdfReader(src).pages)
    source = PdfReader(report_at(year, vol[1:], ROOT / "In")).pages
    name, _, value = mutation.partition("=")
    if name == "drop-last":
        pages, b, pb = pages[:-1], b - 1, pb - 1
    elif name == "add-next":
        pages, b, pb = pages + [source[b]], b + 1, pb + 1
    elif name == "add-prev":
        pages, a, pa = [source[a - 2]] + pages, a - 1, pa - 1
    elif name == "printed":
        pa, pb = pa + int(value), pb + int(value)
    elif name in ("code", "article", "authority"):
        row[name] = value
    else:
        raise SystemExit(f"unknown mutation {mutation!r}")
    row["segments"], row["printed"], row["pages"] = (
        f"{vol}:{a}-{b}", f"{pa}-{pb}", str(len(pages)))
    row["key"] = "/".join(x for x in (row["authority"], row["article"], row["code"]) if x)
    row["file"] = ontology.relative_path(row["authority"], row["article"] or None,
                                         row["code"], year, vol[1:], pa, ".PDF")
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page)
    target = dst / row["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as fh:
        writer.write(fh)
    return row


def make_tree(year, case, base_rows, base, work):
    """A repo copy for one case, holding only that case's cut."""
    tree = work / case["case"]
    shutil.copytree(ROOT, tree, symlinks=True, ignore=shutil.ignore_patterns(
        ".git", "Out", "In", "__pycache__", ".pytest_cache"))
    (tree / "In").symlink_to(ROOT / "In")
    (tree / "Reports2PDFTables.py").write_text(STUB)
    pdfs = layout.pdf_dir(year, tree)
    original = next(r for r in base_rows if r["key"] == case["key"])
    (pdfs / Path(original["file"]).parent).mkdir(parents=True, exist_ok=True)
    row = mutate(year, {**original, "mutation": case["mutation"]},
                 layout.pdf_dir(year, base) / original["file"], pdfs)
    tables = layout.tables_tsv(year, tree)
    tables.parent.mkdir(parents=True, exist_ok=True)
    with open(tables, "w", encoding="utf-8", newline="") as fh:
        out = csv.DictWriter(fh, fieldnames=list(original), delimiter="\t",
                             lineterminator="\n")
        out.writeheader()
        out.writerow(row)
    return tree, row["key"]


def run_case(year, case, base_rows, base, work, model):
    tree, key = make_tree(year, case, base_rows, base, work)
    prompt = (f"Check year {year}, table {key}. The table is already cut: "
              f"start at step 2.")
    cmd = ["claude", "-p", prompt, "--agent", "table-checker",
           "--allowedTools", "Bash,Read,Write", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    result = {"seconds": "", "cost": ""}
    try:
        done = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                              timeout=TIMEOUT)
        info = json.loads(done.stdout or "{}")
        result["seconds"] = round(info.get("duration_ms", 0) / 1000)
        result["cost"] = round(info.get("total_cost_usd", 0), 3)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    verdict_path = layout.checks_dir(year, tree) / layout.key_dir(key) / "verdict.json"
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        verdict = {}
    return case, key, verdict, result


def outcome(expect, got):
    if got not in ("PASS", "FAIL", "UNSURE"):
        return "error"
    if expect == "PASS":
        return "ok" if got == "PASS" else "false-alarm"
    return "MISSED" if got == "PASS" else "ok"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("year")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", action="append", default=[], help="case id")
    parser.add_argument("--model", default="")
    parser.add_argument("--keep", action="store_true",
                        help="keep the scratch trees (path printed)")
    args = parser.parse_args()

    cases = read_tsv(ROOT / "tests" / "evals" / f"checker-{args.year}.tsv")
    if args.only:
        cases = [c for c in cases if c["case"] in args.only]
    work = Path(tempfile.mkdtemp(prefix=f"armtrace-eval-{args.year}-"))
    out = layout.work_year(args.year, ROOT) / "eval"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    try:
        base = work / "_base"
        base_rows = build_base(args.year, {c["key"] for c in cases}, base)
        print(f"{len(cases)} case(s), {args.jobs} at a time, in {work}", flush=True)
        with ThreadPoolExecutor(args.jobs) as pool:
            futures = [pool.submit(run_case, args.year, c, base_rows, base, work,
                                   args.model) for c in cases]
            results = []
            for future in futures:
                case, key, verdict, cost = future.result()
                got = verdict.get("verdict", "none")
                failed = ",".join(k for k, v in verdict.get("checks", {}).items()
                                  if v.get("result") in ("fail", "unreadable"))
                row = {"case": case["case"], "key": key, "mutation": case["mutation"],
                       "expect": case["expect"], "got": got,
                       "kind": verdict.get("kind") or "", "checks": failed,
                       "outcome": outcome(case["expect"], got), **cost}
                results.append(row)
                (out / f"{case['case']}.json").write_text(
                    json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  {row['outcome']:<11} {row['case']:<20} {row['expect']} -> "
                      f"{got} {row['kind']} {failed}", flush=True)
    finally:
        if args.keep:
            print(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    agent = ROOT / ".claude" / "agents" / "table-checker.md"
    with open(out / "results.tsv", "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# table-checker.md sha1 "
                 f"{hashlib.sha1(agent.read_bytes()).hexdigest()[:12]}"
                 f" model {args.model or 'default'}\n")
        writer = csv.DictWriter(fh, fieldnames=list(results[0]), delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)

    count = {k: sum(r["outcome"] == k for r in results)
             for k in ("ok", "MISSED", "false-alarm", "error")}
    wrong = [r for r in results if r["expect"] == "FAIL"]
    cost = sum(r["cost"] for r in results if r["cost"] != "")
    print(f"caught {sum(r['outcome'] == 'ok' for r in wrong)}/{len(wrong)} wrong cuts; "
          f"missed {count['MISSED']}; false alarms {count['false-alarm']}/"
          f"{len(results) - len(wrong)}; errors {count['error']}; "
          f"cost ${cost:.2f} -> {out / 'results.tsv'}")
    return 1 if count["MISSED"] or count["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
