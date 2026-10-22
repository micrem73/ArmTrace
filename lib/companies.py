"""Companies: ArmTrace's own id for each company the reports name (plan.md
Phase 3 step 6, maintainer 2026-10-21).

`lib/data/companies.tsv` (`id`, `name`) lists each company once, with an id
never reused; `lib/data/company-names.tsv` (`printed`, `id`, `how`) maps every
spelling a report prints to it. `how` is `spelling` (automatic: the names are
the same once case, accents, punctuation, spacing and the way a legal form is
written are set aside: `S.P.A.` = `S.p.A.` = `SPA`), `register <number>` (the
name printed in Difesa A5/2 beside a register number that already has an id:
the number identifies a company before its name does), or `maintainer, <date>`.
Anything else, another legal form (`BERCELLA SPA` / `BERCELLA S.R.L.`), a name
the print cut, a near spelling, goes to the maintainer: `propose` lists it,
nothing is guessed. A printed name with no id is stored NULL, with an issue.
"""
import csv
import difflib
import re
import unicodedata
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
LIST, NAMES = DATA / "companies.tsv", DATA / "company-names.tsv"

# A legal form written out in full, as its initials.
SPELLED = [("SOCIETA A RESPONSABILITA LIMITATA", "SRL"), ("SOCIETA PER AZIONI", "SPA")]
LEGAL = {"SPA", "SRL", "SNC", "SAS", "ARL", "SCARL", "GMBH"}
COMMON = 3         # a word in names starting this many ways says nothing alone (`ITALIA`)
NEAR = 0.9          # difflib ratio of two names, legal forms aside: a candidate


def fold(name):
    """The name with only its spelling set aside: `AERO SEKUR S.p..A.` ->
    `AERO SEKUR SPA`."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = text.replace(".", "")
    text = " ".join(re.sub(r"[^A-Z0-9&]+", " ", text).split())
    for full, short in SPELLED:
        text = re.sub(rf"\b{full}\b", short, text)
    return text


def core(folded):
    """A folded name without its legal form, also one the print cut short
    (`VIRTUALABS SOCIETA A RESPONSABILITA`)."""
    words = [w for w in folded.split() if w not in LEGAL]
    for full, _short in SPELLED:
        for n in range(len(full.split()), 0, -1):
            if words[-n:] == full.split()[:n] and len(words) > n:
                return " ".join(words[:-n])
    return " ".join(words)


def _starts(a, b):
    wa, wb = a.split(), b.split()
    return 0 < len(wa) < len(wb) and wb[:len(wa)] == wa


def _within(a, b, common):
    wa, wb = set(a.split()), set(b.split())
    small = min(wa, wb, key=len)
    return bool(small - common) and (wa <= wb or wb <= wa)


def related(a, b, common=frozenset()):
    """Two folded names the maintainer must compare: the same but for the legal
    form, one cut short of the other, every word of one in the other
    (`PRAVISANI` / `DOTT MARIANO PRAVISANI & C`; not when those are only
    `common` words, `ELETTRONICA`), or nearly the same letters."""
    ca, cb = core(a), core(b)
    return (_starts(a, b) or _starts(b, a) or _within(ca, cb, common)
            or difflib.SequenceMatcher(None, ca, cb).ratio() >= NEAR)


def name_of(printed):
    """A company's name, of {spelling: rows}: the spelling printed most often,
    preferring one with no doubled point or space (`AVIO S.p.A..`)."""
    return min(printed, key=lambda n: (bool(re.search(r"\.\.|  ", n)), -printed[n], n))


def _read(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader((ln for ln in fh if not ln.startswith("#")),
                                   delimiter="\t"))


def load():
    """({id: name}, {printed: id})."""
    return ({int(r["id"]): r["name"] for r in _read(LIST)},
            {r["printed"]: int(r["id"]) for r in _read(NAMES)})


def propose(counts, known=None):
    """What a year's printed names need, given {printed: rows printing it}.

    Returns (spellings, new, review):
      spellings  [(printed, id)]: a known company, in another spelling
      new        [(name, [printed...])]: a company with no candidate, named
                 after its spelling printed most often
      review     [[{"fold", "printed": {name: rows}, "id"}...]]: names to
                 compare, each cluster with the known companies it touches
                 (`id` set)"""
    companies, names = known or load()
    by_fold = {}
    for printed, cid in names.items():
        by_fold.setdefault(fold(printed), set()).add(cid)
    spellings, groups = [], {}
    for printed in sorted(counts):
        if printed in names:
            continue
        ids = by_fold.get(fold(printed), set())
        if len(ids) == 1:
            spellings.append((printed, next(iter(ids))))
        else:
            groups.setdefault(fold(printed), {})[printed] = counts[printed]
    # Known companies are nodes too: a new name may be another form of one.
    nodes = [{"fold": f, "printed": p, "id": None} for f, p in sorted(groups.items())]
    nodes += [{"fold": fold(companies[cid]), "printed": {companies[cid]: 0}, "id": cid}
              for cid in sorted(companies)]
    first_words = {}         # a word -> the first words of the names it is in
    for node in nodes:
        words = core(node["fold"]).split()
        for w in words:
            first_words.setdefault(w, set()).add(words[0])
    common = {w for w, firsts in first_words.items() if len(firsts) >= COMMON}
    links = {i: set() for i in range(len(nodes))}
    for i in range(len(groups)):
        for j in range(i + 1, len(nodes)):
            if related(nodes[i]["fold"], nodes[j]["fold"], common):
                links[i].add(j)
                links[j].add(i)
    new, review, seen = [], [], set()
    for i in range(len(groups)):
        if i in seen:
            continue
        cluster, todo = [], [i]
        while todo:
            k = todo.pop()
            if k not in seen:
                seen.add(k)
                cluster.append(k)
                todo += links[k]
        if len(cluster) == 1:
            printed = nodes[i]["printed"]
            new.append((name_of(printed), sorted(printed)))
        else:
            review.append([nodes[k] for k in sorted(cluster)])
    return spellings, new, review


def write(companies, names):
    """Rewrite both files from {id: name} and {printed: (id, how)}."""
    with open(LIST, "w", encoding="utf-8", newline="") as fh:
        fh.write("# lib/companies.py: one line per company; an id is never reused.\nid\tname\n")
        for cid in sorted(companies):
            fh.write(f"{cid}\t{companies[cid]}\n")
    with open(NAMES, "w", encoding="utf-8", newline="") as fh:
        fh.write("# lib/companies.py: every printed spelling and its company; how: "
                 "spelling (automatic), register <number>, or maintainer, <date>.\nprinted\tid\thow\n")
        for printed in sorted(names, key=lambda n: (names[n][0], n)):
            cid, how = names[printed]
            fh.write(f"{printed}\t{cid}\t{how}\n")


def read_how():
    """{printed: (id, how)} as stored."""
    return {r["printed"]: (int(r["id"]), r["how"]) for r in _read(NAMES)}
