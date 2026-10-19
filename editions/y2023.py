"""2023 edition: Doc. LXVII n. 2 (leg. XIX), three volumes.

What is true of this edition only. Everything else is lib/engine.py, shared by
every year; a function named in engine.OVERRIDES may be redefined here when this
layout needs its own version.

  * vol. I: Presidenza, MAE (tables from printed 77), Difesa (875), Interno (941).
  * vol. II: MEF relation (943) and tables (952).
  * vol. III: Agenzia delle dogane relation (1519) and tables (1532).

The Difesa section of vol. I (pdf p879-944) and the Interno page (p945) are
scanned images with no text layer, so no detector can see them. The Difesa
annexes are declared below by page range, read off the scans (decided
2026-10-19, DECISIONS.md); the Interno summary is not a table, as in 2025.
"""

# Three volumes in one run: the folios run on across all three (INDICE, vol. I p3).
VOLUME = "all"

# (authority, article, code, first pdf page, last pdf page). The article is the
# one each annex prints (3A-3C: "art. 2 comma 6 L. 185/90"), filed as in 2025:
# Annesso 2 is the Registro Nazionale delle Imprese (art. 5), Annesso 4 cites none.
_DIFESA_SCANNED = (
    ("DIFESA", "A5", "2", 926, 930),       # printed 922-926, company register
    ("DIFESA", "A2C6", "3A", 931, 932),    # printed 927-928, nulla osta 2023
    ("DIFESA", "A2C6", "3B", 933, 938),    # printed 929-934, nulla osta 2020-2023
    ("DIFESA", "A2C6", "3C", 939, 940),    # printed 935-936, two charts
    ("DIFESA", None, "4", 941, 944),       # printed 937-940, co-production programmes
)


def declared_tables(report):
    """The scanned Difesa annexes of vol. I; nothing elsewhere."""
    if report != "2023_LXVII_n2_VOLUME_I.pdf":
        return []
    return [{"authority": a, "article": art, "code": c, "start": s, "end": e}
            for a, art, c, s, e in _DIFESA_SCANNED]
