"""Garbled MAE fonts: give each glyph its character back, by outline, never by guess.

The MAE annex of 2020 and 2023-2025 prints with Windows fonts whose glyphs were
renamed uniE000... with no /ToUnicode, so text extraction returns Private Use Area
code points (DECISIONS.md, "garbled pages"). The outlines are intact. Each outline
is fingerprinted and looked up in `lib/data/glyphmap.tsv`, built once by
`tools/build_glyphmap.py` from the archive's readable fonts, the Windows fonts and
the human review (`lib/data/glyph-decisions.tsv`).

A glyph the map does not name stays as its PUA code point: unmistakable in the
output, traceable to its font, and never a wrong letter. `undecoded()` counts them.
"""

import hashlib
import io
import re
from functools import lru_cache
from pathlib import Path

from fontTools.cffLib import CFFFontSet
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.transformPen import TransformPen

DATA = Path(__file__).resolve().parent / "data"
GLYPHMAP = DATA / "glyphmap.tsv"

GARBLED_GLYPH = re.compile(r"uniE[0-9A-F]{3}")
PUA = re.compile(r"[-]")
SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")
SPAN_NAME_LEN = 24  # PyMuPDF cuts a span's font name (tag removed) to 24 characters
NOTDEF = "notdef"   # the map's value for the .notdef box: no character

# The five faces, longest name first so "timesnewromanbold" wins over "timesnewroman",
# with the space width (1000-unit em) the space rule checks.
FACES = {
    "timesnewromanbold": 250,
    "microsoftsansserif": 278,
    "couriernewbold": 600,
    "timesnewroman": 250,
    "arialbold": 278,
}


def face_of(basefont):
    """'UIDPNH+PspqfsMdhnjwMicrosoftSansSerif' -> 'microsoftsansserif'; None if not one of five."""
    n = re.sub(r"[,\- ]", "", SUBSET_TAG.sub("", basefont).lower())
    n = re.sub(r"(ps)?(mt)?\d*$", "", n).replace("psbold", "bold")  # suffixes only: the prefix is random
    return next((f for f in FACES if n.endswith(f)), None)


def fingerprint(charstring, fontmatrix):
    """Hash of a glyph's outline in 1000-unit em, None when it draws nothing."""
    k = [m * 1000 for m in fontmatrix]
    pen = RecordingPen()
    charstring.draw(TransformPen(pen, k))
    sig = "|".join(op[0] + "".join(_point(a) for a in args) for op, args in pen.value)
    return hashlib.sha1(sig.encode()).hexdigest()[:16] if sig else None


def _point(a):
    if isinstance(a, tuple) and len(a) == 2:
        return f"{round(a[0], 1) + 0.0},{round(a[1], 1) + 0.0};"
    return f"{a!r};"  # not a point (a broken or exotic charstring): kept, so it never matches a letter


def cff_top(doc, xref):
    """The font's CFF top dict, or None when it is not an embedded CFF font."""
    try:
        _, ext, _, content = doc.extract_font(xref)
    except Exception:
        return None
    if ext != "cff" or not content:
        return None
    try:
        fs = CFFFontSet()
        fs.decompile(io.BytesIO(content), None)
        top = fs[fs.fontNames[0]]
        top.CharStrings, top.FontMatrix  # CID-keyed or broken fonts fail here
    except Exception:
        return None
    return top


def to_unicode(doc, xref):
    """code -> text of the font's /ToUnicode CMap (bfchar and bfrange); None if it has none."""
    kind, val = doc.xref_get_key(xref, "ToUnicode")
    if kind != "xref":
        return None
    cmap = doc.xref_stream(int(val.split()[0])).decode("latin-1")
    out = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            out[int(src, 16)] = bytes.fromhex(dst).decode("utf-16-be", "replace")
    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        for a, b, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            for i, code in enumerate(range(int(a, 16), int(b, 16) + 1)):
                out[code] = chr(int(dst, 16) + i)
    return out


def garbled_glyphs(doc, xref, top):
    """The font's uniE... glyphs whose text comes out as their own PUA code point.

    Two cases. The MAE fonts as supplied have no /ToUnicode: text extraction falls back
    to the glyph name, so every uniE... glyph is garbled. A font repaired by a later
    tool carries a /ToUnicode that names most glyphs and still sends a few codes to
    U+E0xx; only a glyph whose code the CMap sends to exactly its own name is taken,
    so a CMap that says anything else about it is believed. [] when nothing is garbled."""
    names = [n for n in top.CharStrings.keys() if GARBLED_GLYPH.fullmatch(n)]
    if not names:
        return []
    cmap = to_unicode(doc, xref)
    if cmap is None:
        return sorted(names)
    if isinstance(top.Encoding, str):
        return []
    return sorted({g for code, g in enumerate(top.Encoding)
                   if GARBLED_GLYPH.fullmatch(g) and cmap.get(code) == chr(int(g[3:], 16))})


def widths_by_glyph(doc, xref, top):
    """Printed advance width of each glyph, from the font dict's /Widths."""
    kind, val = doc.xref_get_key(xref, "Widths")
    if kind == "xref":
        val = doc.xref_object(int(val.split()[0]))
    first = doc.xref_get_key(xref, "FirstChar")
    if kind == "null" or first[0] != "int" or isinstance(top.Encoding, str):
        return {}
    w = [round(float(v)) for v in val.strip("[] \n").split()]
    out = {}
    for code, g in enumerate(top.Encoding):
        i = code - int(first[1])
        if g != ".notdef" and 0 <= i < len(w):
            out[g] = w[i]
    return out


@lru_cache(maxsize=1)
def glyphmap():
    """(face, fingerprint) -> character, or NOTDEF. Undecoded rows carry no character."""
    out = {}
    for line in GLYPHMAP.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        face, fp, value, _how, *_ = line.split("\t")
        if value:
            out[(face, fp)] = NOTDEF if value == NOTDEF else chr(int(value[2:], 16))
    return out


def font_map(doc, xref, basefont):
    """PUA character -> decoded character for one font's garbled glyphs; {} if none.

    The space rule (DECISIONS.md): shape cannot name a glyph that draws nothing, so
    the lowest-numbered glyph that is not the .notdef box is the space when it is
    empty and has the face's space width. Every other empty glyph stays undecoded.
    """
    top = cff_top(doc, xref)
    face = face_of(basefont)
    if top is None or face is None:
        return {}
    names = garbled_glyphs(doc, xref, top)
    if not names:
        return {}
    known = glyphmap()
    fps = {n: fingerprint(top.CharStrings[n], top.FontMatrix) for n in names}
    out = {}
    for n in names:
        ch = known.get((face, fps[n])) if fps[n] else None
        if ch and ch != NOTDEF:
            out[chr(int(n[3:], 16))] = ch
    if to_unicode(doc, xref) is not None:
        return out  # the space rule is for fonts with no CMap at all
    first = next((n for n in names if known.get((face, fps[n])) != NOTDEF), None)
    if first and fps[first] is None and widths_by_glyph(doc, xref, top).get(first) == FACES[face]:
        out[chr(int(first[3:], 16))] = " "
    return out


def page_maps(doc, page):
    """Span font name -> font map, for every garbled font on the page.

    Spans name their font cut to 24 characters, so two fonts can share a span name;
    such a name gets no map (its text stays undecoded) rather than the wrong one."""
    by_span = {}
    for fx in page.get_fonts():
        xref, basefont = fx[0], fx[3]
        m = _font_map_cached(doc, xref, basefont)
        span = SUBSET_TAG.sub("", basefont)[:SPAN_NAME_LEN]
        if span in by_span and by_span[span] is not m:
            by_span[span] = None
        else:
            by_span[span] = m
    return {k: v for k, v in by_span.items() if v}


_CACHE = {}


def _font_map_cached(doc, xref, basefont):
    key = (id(doc), doc.name, xref)
    if key not in _CACHE:
        _CACHE[key] = font_map(doc, xref, basefont)
    return _CACHE[key]


def decode(text, fmap):
    """The text with every mapped PUA character replaced; the rest left as printed."""
    if not fmap:
        return text
    return PUA.sub(lambda m: fmap.get(m.group(), m.group()), text)


def decode_spans(doc, page, rawdict=None):
    """The page's `get_text('dict')` with each span's text decoded in place."""
    maps = page_maps(doc, page)
    d = rawdict or page.get_text("dict")
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                s["text"] = decode(s["text"], maps.get(s["font"]))
    return d


def page_text(doc, page):
    """The page's plain text, line by line, decoded."""
    d = decode_spans(doc, page)
    return "\n".join("".join(s["text"] for s in ln["spans"])
                     for b in d.get("blocks", []) for ln in b.get("lines", []))


def undecoded(text):
    """How many characters of `text` are still garbled."""
    return len(PUA.findall(text))
