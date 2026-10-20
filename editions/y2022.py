"""2022 edition: Doc. LXVII n. 1 (leg. XIX), two volumes.

What is true of this edition only. Everything else is lib/engine.py, shared by
every year; a function named in engine.OVERRIDES may be redefined here when this
layout needs its own version.

  * vol. I: Presidenza, MAE (tables from printed 77), Difesa (947), Interno (1014).
  * vol. II: MEF relation (1015) and tables (1024), Agenzia delle dogane
    relation (1589) and tables (1602).

The Difesa section of vol. I is scanned images with no text layer (pdf
p952-957, p999-1017), around the Gazzetta Ufficiale's Elenco Materiali
(Annesso 1, p958-998, pasted in, no table). The other annexes are declared below
by page range, read off the scans, as in 2023. The Interno section is a cover
and a blank page.
"""

VOLUME = "both"

# (authority, article, code, first pdf page, last pdf page), filed as in 2023.
_DIFESA_SCANNED = (
    ("DIFESA", "A5", "2", 999, 1003),      # printed 995-999, company register
    ("DIFESA", "A2C6", "3A", 1004, 1005),  # printed 1000-1001, nulla osta 2022
    ("DIFESA", "A2C6", "3B", 1006, 1011),  # printed 1002-1007, nulla osta 2019-2022
    ("DIFESA", "A2C6", "3C", 1012, 1013),  # printed 1008-1009, two charts
    ("DIFESA", None, "4", 1014, 1017),     # printed 1010-1013, co-production programmes
)


def declared_tables(report):
    """The scanned Difesa annexes of vol. I; nothing elsewhere."""
    if report != "2022_LXVII_n1_VOLUME_I.pdf":
        return []
    return [{"authority": a, "article": art, "code": c, "start": s, "end": e}
            for a, art, c, s, e in _DIFESA_SCANNED]
