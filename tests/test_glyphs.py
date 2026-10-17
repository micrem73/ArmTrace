"""Garbled MAE fonts (lib/glyphs.py): the map is certain or silent, never wrong."""
import re

import pymupdf
import pytest

from conftest import ROOT
from lib import glyphs


def rows(path):
    return [ln.split("\t") for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


VALUE = re.compile(r"U\+[0-9A-F]{4,5}|notdef|")


def test_glyphmap_names_each_outline_once():
    keys = [(r[0], r[1]) for r in rows(glyphs.GLYPHMAP)]
    assert len(keys) == len(set(keys))
    assert all(VALUE.fullmatch(r[2]) for r in rows(glyphs.GLYPHMAP))


def test_glyphmap_decodes_only_by_a_named_witness():
    """A character comes from a person, the archive or Windows; an undecoded row says why."""
    for face, fp, value, how, *_ in rows(glyphs.GLYPHMAP):
        if value:
            assert how in ("person", "archive", "archive+windows", "windows"), (face, fp, how)
        else:
            assert how.split(":")[0] in ("ambiguous", "lookalike", "conflict", "no match"), (face, fp, how)


def test_every_person_decision_is_in_the_map():
    built = {(r[0], r[1]): (r[2], r[3]) for r in rows(glyphs.GLYPHMAP)}
    for face, fp, value, item, *_ in rows(glyphs.DATA / "glyph-decisions.tsv"):
        assert built.get((face, fp)) == (value, "person"), item


@pytest.mark.parametrize("basefont, face", [
    ("CRPBNN+BdjvpmTimesNewRoman", "timesnewroman"),          # "mt" inside the random prefix
    ("MCEQLQ+KssmxmTimesNewRoman,Bold", "timesnewromanbold"),
    ("UIDPNH+PspqfsMdhnjwMicrosoftSansSerif", "microsoftsansserif"),
    ("OYCHIL+SqktsqCourierNew,Bold", "couriernewbold"),
    ("ABCDEF+TimesNewRomanPS-BoldMT", "timesnewromanbold"),
    ("ABCDEF+ArialMT", None),                                  # regular Arial is not a garbled face
])
def test_face_reads_suffixes_only(basefont, face):
    assert glyphs.face_of(basefont) == face


def test_decode_leaves_unmapped_characters_as_printed():
    assert glyphs.decode("x", {"": "A"}) == "Ax"
    assert glyphs.undecoded("Ax") == 1


# Decoded lines checked against the rendered page (2026-10-17).
GOLDEN = [
    ("2023", "2023_LXVII_n2_VOLUME_I.pdf", 498, "01/01/2023 - 31/12/2023"),
    # a font repaired by callas: its CMap names every glyph but one hyphen, left at U+E000
    ("2023", "2023_LXVII_n2_VOLUME_I.pdf", 823, "VELIVOLO STAR - 9KW STARTER GENERATORE A9529900"),
    ("2023", "2023_LXVII_n2_VOLUME_I.pdf", 823, "CB06501-01"),
    ("2025", "2025_LXVII_n4_VOLUME_I.pdf", 606, "Totale Valore in EURO :"),
    ("2025", "2025_LXVII_n4_VOLUME_I.pdf", 606, "265.139.737,31"),
]


@pytest.mark.parametrize("year, name, page, expected", GOLDEN, ids=lambda v: str(v))
def test_decoded_page_reads_as_printed(year, name, page, expected, sources):
    doc = pymupdf.open(ROOT / "In" / year / name)
    text = glyphs.page_text(doc, doc[page - 1])
    assert expected in text
    assert glyphs.undecoded(text) == 0
