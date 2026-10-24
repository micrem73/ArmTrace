"""2021 edition: Doc. LXVII n. 5 (leg. XVIII), volume I in two tomi plus volume II.

What is true of this edition only. Everything else is lib/engine.py, shared by
every year; a function named in engine.OVERRIDES may be redefined here when this
layout needs its own version.

  * TOMO_I is "VOLUME I - TOMO I" (folio 1): Presidenza, then MAE (from p18).
  * TOMO_II is "VOLUME I - TOMO II" (folio 790): MAE again, word for word
    (p3-774 = tomo I p18-789, without tomo I's VALORE page p562 and its page
    list p790), a signature leaf (p775), then the Difesa section as scanned
    images with no text layer (p776-841): relation, the Gazzetta Ufficiale's
    Elenco Materiali, the annexes. The engine keeps tomo I's MAE (first copy).
  * VOLUME_II is "VOLUME II" (folio 1629): MEF and Agenzia delle dogane.

One folio run across the three files, read in that order (V1-V3).
"""

VOLUME = "all"

# (authority, article, code, first pdf page, last pdf page), filed as in 2022.
# Tomo II folio = pdf page + 787. p828 (printed 1615) is the covering letter.
_DIFESA_SCANNED = (
    ("DIFESA", "A5", "2", 823, 827),       # printed 1610-1614, company register
    ("DIFESA", "A2C6", "3A", 829, 830),    # printed 1616-1617, nulla osta 2021
    ("DIFESA", "A2C6", "3B", 831, 835),    # printed 1618-1622, nulla osta 2018-2021
    ("DIFESA", None, "4", 836, 839),       # printed 1623-1626, co-production programmes
    ("DIFESA", "A2C6", "3C", 840, 841),    # printed 1627-1628, two charts
)


# Tomo I p40 (printed 38): exports by geographic area 2018-2021, the table 2022
# captions "Tabella 7". 2021 prints no caption; filed as MAE/T7 by the
# maintainer (2026-10-24).
_UNCAPTIONED = (
    ("MAE", None, "T7", 40, 40),
)

_DECLARED = {
    "2021_LXVII_n5_TOMO_I.pdf": _UNCAPTIONED,
    "2021_LXVII_n5_TOMO_II.pdf": _DIFESA_SCANNED,
}


def declared_tables(report):
    """Tomo I's uncaptioned T7 and the scanned Difesa annexes of tomo II."""
    return [{"authority": a, "article": art, "code": c, "start": s, "end": e}
            for a, art, c, s, e in _DECLARED.get(report, ())]
