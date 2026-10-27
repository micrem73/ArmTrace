"""Banks: ArmTrace's own id for each bank the MEF tables name (maintainer,
2026-10-25, DECISIONS.md: by name, as companies).

A printed bank is its name and, often, its branch: `BANCA NAZIONALE DEL LAVORO
SPA (CAB 01600)`. The CAB is the branch, not the bank: the same bank prints
CAB 01600 in MEF KK1 and 01000 in AA, DEUTSCHE BANK SPA both 01600 and 01612.
So a bank is its name with the CAB left out, spelled as `companies.fold` sets
spelling aside; the CAB is kept in a column of its own.

`lib/data/banks.tsv` (`id`, `name`) lists each bank once, with an id never
reused; `lib/data/bank-names.tsv` (`printed`, `id`, `how`) maps every printed
name (CAB left out) to it. `how` is `spelling` (automatic) or `maintainer,
<date>`. A name with no id is stored NULL, with an issue.
"""
import re

from lib import companies

LIST, NAMES = companies.DATA / "banks.tsv", companies.DATA / "bank-names.tsv"
CAB = re.compile(r"\(\s*CAB\s*(\d+)\s*\)", re.IGNORECASE)


def name(printed):
    """The bank's name as printed, its branch left out."""
    return " ".join(CAB.sub(" ", printed or "").split())


def cab(printed):
    """The branch's CAB printed with the name, or None."""
    m = CAB.search(printed or "")
    return m.group(1) if m else None


def load():
    """({id: name}, {fold of a printed name: id})."""
    return ({int(r["id"]): r["name"] for r in companies._read(LIST)},
            {companies.fold(r["printed"]): int(r["id"]) for r in companies._read(NAMES)})


def id_of(printed, ids):
    return ids.get(companies.fold(name(printed)))


def update(printed):
    """Give each printed bank name an id: its spelling's, or a new bank's.
    Returns the names that made a new bank."""
    banks = {int(r["id"]): r["name"] for r in companies._read(LIST)}
    names = {r["printed"]: (int(r["id"]), r["how"]) for r in companies._read(NAMES)}
    by_fold = {companies.fold(p): cid for p, (cid, _h) in names.items()}
    new = []
    for p in sorted({name(x) for x in printed if name(x)}):
        if p in names:
            continue
        cid = by_fold.get(companies.fold(p))
        if cid is None:
            cid = max(banks, default=0) + 1
            banks[cid] = p
            by_fold[companies.fold(p)] = cid
            new.append(p)
        names[p] = (cid, "spelling")
    with open(LIST, "w", encoding="utf-8", newline="") as fh:
        fh.write("# lib/banks.py: one line per bank; an id is never reused.\nid\tname\n")
        fh.writelines(f"{i}\t{banks[i]}\n" for i in sorted(banks))
    with open(NAMES, "w", encoding="utf-8", newline="") as fh:
        fh.write("# lib/banks.py: every printed bank name (its CAB left out) and its bank; "
                 "how: spelling (automatic) or maintainer, <date>.\nprinted\tid\thow\n")
        fh.writelines(f"{p}\t{cid}\t{how}\n"
                      for p, (cid, how) in sorted(names.items(), key=lambda x: (x[1][0], x[0])))
    return new
