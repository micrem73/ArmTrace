"""Build lib/data/glyphmap.tsv: the character of every garbled glyph outline, or none.

    python3 tools/build_glyphmap.py --windows DIR [--windows DIR ...]

Run once per new edition, then commit the map; lib/glyphs.py reads it. DIR holds
Windows TrueType files named by face (times.ttf, timesbd.ttf, arialbd.ttf,
courbd.ttf, micross.ttf, or <face>-<anything>.ttf for several versions). They are
third-party files: download them, never commit them (DECISIONS.md lists sources).

Each garbled outline (face, fingerprint) gets a character only from one of:
  person   lib/data/glyph-decisions.tsv, the human review
  archive  exactly one character among the archive's readable fonts has this
           outline (any face), and Windows, where it names anything, agrees
  windows  no readable font has it; exactly one Windows character does, after the
           producer's conversion (quadratic Q -> cubic c1 = Q, c2 = P2 + (Q-P2)/3)
           and, for glyphs reaching left of the origin, an x translation
Anything else is written with no character and the reason: ambiguous, lookalike
(one Mac Roman letter among non-Latin twins: a person decides), conflict, no match.
"""

import argparse
import collections
import sys
from pathlib import Path

import pymupdf
from fontTools.agl import toUnicode
from fontTools.pens.basePen import BasePen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import glyphs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DECISIONS = glyphs.DATA / "glyph-decisions.tsv"
TT_EM = 2048
WINDOWS_FACE = {"times": "timesnewroman", "timesbd": "timesnewromanbold", "arialbd": "arialbold",
                "courbd": "couriernewbold", "micross": "microsoftsansserif"}


def mac_roman(c):
    try:
        return len(c.encode("mac_roman")) == 1
    except UnicodeEncodeError:
        return False


def grid_key(segs):
    """Exact key on the 1/6-unit grid the conversion lands on; None if off it."""
    out = []
    for op, pts in segs:
        q = []
        for x, y in pts:
            gx, gy = x * 6, y * 6
            if abs(gx - round(gx)) > 0.06 or abs(gy - round(gy)) > 0.06:
                return None
            q.append((round(gx), round(gy)))
        out.append((op, tuple(q)))
    return tuple(out)


def keys(segs):
    """(exact key, key up to an x translation) of an outline in TrueType units."""
    if not segs:
        return None, None
    x0 = segs[0][1][0][0]
    return grid_key(segs), grid_key([(op, [(x - x0, y) for x, y in pts]) for op, pts in segs])


class ProducerPen(BasePen):
    """A TrueType outline as the producer's Type 1 converter writes it."""

    def __init__(self, gs):
        super().__init__(gs)
        self.segs = []

    def _moveTo(self, p):
        self.segs.append(("M", [p]))

    def _lineTo(self, p):
        self.segs.append(("L", [p]))

    def _curveToOne(self, a, b, p):
        self.segs.append(("C", [a, b, p]))

    def _qCurveToOne(self, q, p):
        self.segs.append(("C", [q, (p[0] + (q[0] - p[0]) / 3, p[1] + (q[1] - p[1]) / 3), p]))

    def _closePath(self):
        pass

    _endPath = _closePath


def windows_refs(dirs):
    """face -> (exact key -> chars, translation key -> chars), over every version given."""
    ref = collections.defaultdict(lambda: (collections.defaultdict(set), collections.defaultdict(set)))
    for d in dirs:
        for path in sorted(Path(d).glob("*.ttf")):
            stem = path.stem.split("-")[0].lower()
            face = WINDOWS_FACE.get(stem) or (stem if stem in glyphs.FACES else None)
            if not face:
                continue
            try:
                tt = TTFont(path)
                gs, cmap = tt.getGlyphSet(), tt.getBestCmap()
            except Exception as e:
                print(f"  skipped {path.name}: {e}")
                continue
            scale = TT_EM / tt["head"].unitsPerEm
            exact, moved = ref[face]
            for cp, g in cmap.items():
                pen = ProducerPen(gs)
                gs[g].draw(pen)
                segs = [(op, [(x * scale, y * scale) for x, y in pts]) for op, pts in pen.segs]
                k, t = keys(segs)
                if k:
                    exact[k].add(chr(cp))
                if t:
                    moved[t].add(chr(cp))
    return ref


def garbled_segs(charstring, fontmatrix):
    pen = RecordingPen()
    charstring.draw(pen)
    sc = fontmatrix[0] * TT_EM
    return [(op[0].upper(), [(x * sc, y * sc) for x, y in args])
            for op, args in pen.value if op not in ("closePath", "endPath")]


def inventory(years):
    """Readable fingerprints -> chars, and every garbled (face, fingerprint) with a sample."""
    readable = collections.defaultdict(set)
    garbled = {}
    fonts = collections.Counter()
    unknown_face = set()
    for year in years:
        for pdf in sorted((ROOT / "In" / year).glob("*.pdf")):
            doc = pymupdf.open(pdf)
            seen = set()
            for page in doc:
                for fx in page.get_fonts():
                    xref, basefont = fx[0], fx[3]
                    if xref in seen or fx[1] != "cff":
                        continue
                    seen.add(xref)
                    top = glyphs.cff_top(doc, xref)
                    if top is None:
                        continue
                    cs, fm = top.CharStrings, top.FontMatrix
                    garbled_names = glyphs.garbled_glyphs(doc, xref, top)
                    if garbled_names:
                        face = glyphs.face_of(basefont)
                        if face is None:
                            unknown_face.add(f"{year} {basefont}")
                        for n in garbled_names if face else ():
                            fp = glyphs.fingerprint(cs[n], fm)
                            if fp is None:
                                continue
                            k = (face, fp)
                            fonts[k] += 1
                            if k not in garbled:
                                garbled[k] = (f"{pdf.relative_to(ROOT / 'In')} p{page.number + 1} "
                                              f"{basefont} {n}", garbled_segs(cs[n], fm))
                    if (doc.xref_get_key(xref, "Encoding")[0] != "null"
                            or doc.xref_get_key(xref, "ToUnicode")[0] != "null"):
                        for n in cs.keys():
                            ch = toUnicode(n)
                            if len(ch) == 1 and not glyphs.PUA.match(ch) and not glyphs.GARBLED_GLYPH.fullmatch(n):
                                fp = glyphs.fingerprint(cs[n], fm)
                                if fp:
                                    readable[fp].add(ch)
            print(f"  {pdf.relative_to(ROOT / 'In')}: {len(seen)} fonts", flush=True)
    return readable, garbled, fonts, unknown_face


def read_decisions():
    out = {}
    for line in DECISIONS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        face, fp, value, item, *_ = line.split("\t")
        out[(face, fp)] = (value, item)
    return out


def classify(k, readable, win, decided):
    """(value, how) for one garbled outline; value '' when it stays undecoded."""
    face, fp = k
    if k in decided:
        return decided[k][0], "person"
    _, segs = SAMPLES[k]
    exact, moved = keys(segs)
    w = set()
    if face in win:
        w = set(win[face][0].get(exact, ())) | set(win[face][1].get(moved, ()))
    arch = readable.get(fp, set())
    if len(arch) == 1:
        c = next(iter(arch))
        if w and c not in w:
            return "", f"conflict: archive {c!r}, windows {''.join(sorted(w))!r}"
        return f"U+{ord(c):04X}", "archive+windows" if w else "archive"
    if len(arch) > 1:
        return "", f"ambiguous: archive {''.join(sorted(arch))!r}"
    if len(w) == 1:
        return f"U+{ord(next(iter(w))):04X}", "windows"
    if w:
        latin = {c for c in w if mac_roman(c)}
        kind = "lookalike" if len(latin) == 1 else "ambiguous"
        return "", f"{kind}: windows {''.join(sorted(w))!r}"
    return "", "no match"


SAMPLES = {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--windows", action="append", default=[], help="directory of Windows .ttf files")
    ap.add_argument("--years", nargs="*", default=sorted(p.name for p in (ROOT / "In").iterdir()
                                                          if p.is_dir() and p.name.isdigit()))
    a = ap.parse_args()
    print("inventory of embedded fonts:")
    readable, garbled, fonts, unknown_face = inventory(a.years)
    SAMPLES.update(garbled)
    win = windows_refs(a.windows)
    decided = read_decisions()
    missing = sorted(set(decided) - set(garbled))
    if missing:
        sys.exit(f"decisions name outlines no source has: {missing}")
    rows, tally = [], collections.Counter()
    for k in sorted(garbled):
        value, how = classify(k, readable, win, decided)
        rows.append((k[0], k[1], value, how, str(fonts[k]), garbled[k][0]))
        tally[how.split(":")[0] if value == "" else how] += 1
    header = ("# Garbled-font glyph map, built by tools/build_glyphmap.py; do not edit.\n"
              "# face\tfingerprint\tcharacter (U+XXXX, notdef, or empty: undecoded)\thow\tfonts\tfirst seen\n")
    glyphs.GLYPHMAP.write_text(header + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} distinct garbled outlines -> {glyphs.GLYPHMAP.relative_to(ROOT)}")
    for how, n in tally.most_common():
        print(f"  {n:6}  {how}")
    for r in rows:
        if not r[2]:
            print("  undecoded:", r[0], r[1], r[3], "|", r[5])
    for f in sorted(unknown_face):
        print("  garbled font of no known face, left undecoded:", f)


if __name__ == "__main__":
    main()
