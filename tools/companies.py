#!/usr/bin/env python3
"""Give every company a year's tables name its ArmTrace id (lib/companies.py).

    python3 tools/companies.py 2025
    python3 tools/companies.py 2025 --same "BERCELLA SPA" "BERCELLA S.R.L."
    python3 tools/companies.py 2025 --apart "PRAVISANI S.P.A." "DOTT. MARIANO ..."

Reads the printed names from Out/<year>/armtrace-<year>.sqlite (the columns a
`company` line of lib/data/columns.tsv names), then updates
lib/data/companies.tsv and company-names.tsv: a known company's other spelling,
and a company with no candidate, automatically; the clusters left are printed
for the maintainer, who answers with --same (one company) or --apart (each its
own). A name given stands for all its spellings. Re-extract afterwards: the ids
are stored at extraction. Exit 0.
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
    for i, cluster in enumerate(review, 1):
        print(f"  {i:>2}. " + "  |  ".join(
            (f"[{node['id']}] " if node["id"] else "") + " ; ".join(node["printed"])
            for node in cluster))
    return 0


if __name__ == "__main__":
    sys.exit(main())
