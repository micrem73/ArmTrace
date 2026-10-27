"""Where each edition prints Difesa Annesso 2, the register of companies.

`lib/data/register-pages.tsv`, found by hand (survey of 2026-10-25/27,
DECISIONS.md): for the editions with no full run it is how the register's pages
reach the table index (`tools/register_index.py`); for every edition it says
which pages are printed sideways and which are scans, and the counts the
Difesa narrative states.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "register-pages.tsv"


@dataclass(frozen=True)
class RegisterPages:
    year: int
    volume: str
    pdf_first: int
    pdf_last: int
    printed_first: int
    rotated: frozenset
    layer: str          # text | ocrlayer | scan
    layout: str         # grid | list | snapshot+grid
    lists: str
    narrative: int | None
    note: str

    @property
    def printed_last(self):
        return self.printed_first + self.pdf_last - self.pdf_first


def _pages(text):
    """`240-241,243-244` -> {240, 241, 243, 244}; `-` -> empty."""
    pages = set()
    for part in filter(None, (p.strip() for p in text.split(","))):
        if part == "-":
            continue
        a, _, b = part.partition("-")
        pages.update(range(int(a), int(b or a) + 1))
    return frozenset(pages)


def load(path=PATH):
    """{year: RegisterPages, or None where the edition prints no register}."""
    with open(path, encoding="utf-8") as fh:
        rows = csv.DictReader((line for line in fh if not line.startswith("#")),
                              delimiter="\t")
        out = {}
        for r in rows:
            year = int(r["year"])
            if r["pdf_first"] == "-":
                out[year] = None
                continue
            out[year] = RegisterPages(
                year=year, volume=r["volume"], pdf_first=int(r["pdf_first"]),
                pdf_last=int(r["pdf_last"]), printed_first=int(r["printed_first"]),
                rotated=_pages(r["rotated"]), layer=r["layer"], layout=r["layout"],
                lists=r["lists"],
                narrative=None if r["narrative"] == "-" else int(r["narrative"]),
                note=r["note"])
        return out
