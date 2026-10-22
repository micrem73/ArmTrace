#!/usr/bin/env python3
"""Give every company a year's tables name its ArmTrace id (lib/companies.py).

    python3 tools/companies.py 2025
    python3 tools/companies.py 2025 --same "BERCELLA SPA" "BERCELLA S.R.L."
    python3 tools/companies.py 2025 --apart "PRAVISANI S.P.A." "DOTT. MARIANO ..."

Reads the printed names from Out/<year>/armtrace-<year>.sqlite (the columns a
`company` line of lib/data/columns.tsv names), then updates
lib/data/companies.tsv and company-names.tsv: first by register number (a name
Difesa A5/2 prints beside a number that already has an id takes it), then a
known company's other spelling, and a company with no candidate, automatically; the clusters left are printed
for the maintainer, who answers with --same (one company) or --apart (each its
own). A name given stands for all its spellings. Re-extract afterwards: the ids
are stored at extraction.

The register of companies (Difesa A5/2, every extracted year) is a witness to
these decisions: a register number under two ids (two companies that are one)
or an id under two register numbers (a merge that was wrong) is printed as a
WARNING for the maintainer, never corrected here. Exit 0.
"""
import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import companies, extract, layout  # noqa: E402


def printed_counts(year):
    db = sqlite3.connect(layout.year_dir(year, ROOT) / f"armtrace-{year}.sqlite")
    counts = {}
    for key, spec in extract.specs().items():
        name = extract.sql_name(key)
        if spec.company and db.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                                       (name,)).fetchone():
            for printed, n in db.execute(f"SELECT {spec.company.of}, count(*) FROM {name} "
                                         f"WHERE {spec.company.of} IS NOT NULL GROUP BY 1"):
                counts[printed] = counts.get(printed, 0) + n
    return counts


def register(root=ROOT):
    """[(numero_iscrizione, id_ditta, denominazione, year)]: every line of
    Difesa A5/2 in every year extracted."""
    rows = []
    for path in sorted(Path(root, layout.OUT).glob("*/armtrace-*.sqlite")):
        db = sqlite3.connect(path)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='difesa_a5_2'").fetchone():
            rows += db.execute("SELECT numero_iscrizione, id_ditta, denominazione, year "
                               "FROM difesa_a5_2 WHERE numero_iscrizione IS NOT NULL").fetchall()
    return rows


def by_register(rows, names):
    """A name printed with a register number that already has an id takes it,
    before any matching of names (maintainer, 2026-10-22: the register number
    is the first identity of a company): `how` is `register <number>`."""
    known = {}
    for numero, cid, _name, _year in rows:
        if cid is not None:
            known.setdefault(numero, set()).add(cid)
    for numero, _cid, printed, _year in rows:
        if printed not in names and len(known.get(numero, ())) == 1:
            names[printed] = (next(iter(known[numero])), f"register {numero}")


def witness(root=ROOT):
    """[warning]: where a table contradicts the ids, in every year extracted
    (maintainer, 2026-10-22: every decision stays open to the tables). The
    register numbers of Difesa A5/2: one number under two ids, one id under
    two numbers. A table grouped by company (a `group` or `carry` column: MAE
    A1's operator heading, MEF II's `Ditta`) printing one id as two groups."""
    seen = [r for r in register(root) if r[1] is not None]
    out = []
    grouped = [(key, spec) for key, spec in extract.specs().items() if spec.company and any(
        c.key == spec.company.of and c.type in ("group", "carry") for c in spec.columns)]
    for path in sorted(Path(root, layout.OUT).glob("*/armtrace-*.sqlite")):
        db = sqlite3.connect(path)
        for key, spec in grouped:
            name, of, col = extract.sql_name(key), spec.company.of, spec.company.key
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
                continue
            for cid, year, printed in db.execute(
                    f"SELECT {col}, year, group_concat(DISTINCT {of}) FROM {name} "
                    f"WHERE {col} IS NOT NULL GROUP BY {col}, year "
                    f"HAVING count(DISTINCT {of}) > 1"):
                out.append(f"id {cid} is printed as separate groups in {key} {year}: "
                           f"two companies?  {printed.replace(',', '; ')}")
    for by, other, what in ((0, 1, "register number {} is under ids {}: one company?"),
                            (1, 0, "id {} is under register numbers {}: two companies?")):
        found = {}
        for row in seen:
            found.setdefault(row[by], {}).setdefault(row[other], []).append(f"{row[2]} ({row[3]})")
        for key, under in sorted(found.items()):
            if len(under) > 1:
                out.append(what.format(key, ", ".join(map(str, sorted(under))))
                           + "  " + "; ".join(n for names in under.values() for n in names))
    return out


def decide(given, counts, names, listed, together):
    """Each name given, with its spellings, into one company (`together`) or
    each into its own; a name already with an id keeps it."""
    folds = {companies.fold(g) for g in given}
    missing = [g for g in given if g not in counts and g not in names]
    if missing:
        sys.exit(f"not printed in this year's tables: {missing}")
    how = f"maintainer, {datetime.date.today().isoformat()}"
    groups = {}
    for printed in counts:
        if companies.fold(printed) in folds:
            groups.setdefault(companies.fold(printed), {})[printed] = counts[printed]
    if together:
        groups = {"": {p: n for g in groups.values() for p, n in g.items()}}
    for group in groups.values():
        ids = {names[p][0] for p in group if p in names}
        if len(ids) > 1:
            sys.exit(f"already different companies: {sorted(group)}")
        cid = ids.pop() if ids else max(listed, default=0) + 1
        listed.setdefault(cid, companies.name_of(group))
        for printed in group:
            names[printed] = (cid, how)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("year")
    parser.add_argument("--same", nargs="+", metavar="NAME")
    parser.add_argument("--apart", nargs="+", metavar="NAME")
    args = parser.parse_args(argv)
    counts = printed_counts(args.year)
    listed, names = companies.load()[0], companies.read_how()
    for given, together in ((args.same, True), (args.apart, False)):
        if given:
            decide(given, counts, names, listed, together)
    by_register(register(), names)
    spellings, new, review = companies.propose(
        counts, (listed, {p: cid for p, (cid, _h) in names.items()}))
    for printed, cid in spellings:
        names[printed] = (cid, "spelling")
    for name, printed in new:
        cid = max(listed, default=0) + 1
        listed[cid] = name
        for p in printed:
            names[p] = (cid, "spelling")
    companies.write(listed, names)
    print(f"companies {args.year}: {len(counts)} printed names, {len(listed)} companies; "
          f"+{len(spellings)} spellings, +{len(new)} new; "
          f"{len(review)} clusters for the maintainer")
    for warning in witness():
        print(f"  WARNING {warning}")
    for i, cluster in enumerate(review, 1):
        print(f"  {i:>2}. " + "  |  ".join(
            (f"[{node['id']}] " if node["id"] else "") + " ; ".join(node["printed"])
            for node in cluster))
    return 0


if __name__ == "__main__":
    sys.exit(main())
