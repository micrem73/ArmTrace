---
name: UnGarblePDF
description: Repair PDFs whose text layer is destroyed, by OCR-ing only the unreadable pages and writing the text back invisible at its original position. Retained but not advertised — see Status below. Use only when a PDF must become machine-readable and the measured limits below have been accepted.
license: MIT
metadata:
  opencode/autoinvoke: false
---

# UnGarblePDF

> **Status: on hold, and deliberately not advertised.**
>
> The code works and is tested. It recovers page structure, headings and prose
> from genuinely garbled pages — but on the page it was measured against it did
> not recover the data that mattered: the small figures inside grey-shaded,
> rotated table cells. Raising the DPI made confidence *worse* (55.2, 54.5,
> 54.2 at 300/450/600), so the limit is contrast rather than resolution and no
> setting fixes it.
>
> It is kept because it is correct, tested, and because the diagnosis it
> encodes is worth not re-deriving. It is hidden from the skill list so nobody
> spends an hour OCR-ing a volume to discover the figures do not come back.
> Load it by ID when that trade is right for a specific job.
>
> `AGENTS.md` §4 and §6 record OCR as out of scope by decision. That is still
> the project's position; this is the parked exception, not a change to it.

Give unreadable PDF pages a real text layer, by OCR.

## What "unreadable" means here, and why it happens

Some PDFs have pages whose character codes are meaningless. Every route from a
character code to a character has been destroyed at PDF generation time:

| Route | State |
|---|---|
| `/ToUnicode` CMap | absent |
| `/Encoding` | absent |
| glyph names in the embedded font | the Private Use codepoint copied into a name — `uniE019` for U+E019 |

That last row is the one to understand. `uniE019` is not a damaged encoding of
the letter `e`; it is the broken value re-encoded as a name. No arithmetic
recovers the original character from it.

**What survives is the glyph outlines** — the page is a picture of text. This
skill renders those pages and runs OCR over them.

Verify the diagnosis on your own file before assuming it; see *Confirm the
diagnosis* below.

## Install

```bash
sudo apt install tesseract-ocr tesseract-ocr-ita    # Debian/Ubuntu
brew install tesseract tesseract-lang               # macOS
```

Check it:

```bash
python scripts/selftest.py     # 18 checks; OCR-dependent ones skip cleanly
```

## Use

Always dry-run first. OCR is the slowest thing in any PDF pipeline and a
1000-page volume can take hours.

```bash
# 1. what would happen, and roughly how long
python scripts/ocr_repair.py volume.pdf --dry-run --pages 600-700

# 2. try a few pages and read the result
python scripts/ocr_repair.py volume.pdf --out fixed.pdf --pages 612-615 \
    --sidecar ./side --dpi 300

# 3. then the real run
python scripts/ocr_repair.py volume.pdf --out fixed.pdf --sidecar ./side
```

Options: `--dpi` (default 300), `--lang` (default `ita+eng`), `--min-conf`
(default 30), `--psm`, `--limit N`, `--pages`, `--sidecar DIR`.

Outputs `fixed.pdf`, `fixed.pdf.manifest.json` (per-page word counts and mean
confidence), and optionally one `.txt` per page.

## How a page is repaired

1. **Turn the page upright** if its content is turned. This is not optional.
2. Render at `--dpi` and OCR, keeping **each word's bounding box**.
3. Remove the broken text objects, then put the page back as a rendered image.
4. Write the OCR words back as **invisible** text, upright, so the text runs
   with the table rather than across it.
5. Re-embed the finished page at the orientation the original had.

Step 1 matters more than it looks. A page whose content is turned 90 degrees
OCRs to letter-shaped noise — but its *horizontal* page furniture still comes
out clean, so the failure looks partial and random rather than total, and the
table body quietly returns `"ODILVINHOANI OULNIO"` where the page says
`"VALORE DECRESCENTE"`. Orientation is read from the page layout rather than
from the characters, so it works on a page whose text is unreadable.

Step 3 cannot be redaction alone. On a page with broken fonts the glyph
outlines **are** the visible content, so removing the text layer leaves a blank
page with the table's cell shading still drawn on it — and because the line art
survives redaction, the result looks like a rendering bug rather than the data
loss it is. The appearance has to be restored from an image.

Step 2 is what keeps the page usable downstream. Text written back without its
geometry is a paragraph where a table used to be, and every column detector
then disagrees with the page. TSV output, not plain text, for that reason.

## Measured on a real page

A page from a 1048-page volume whose text layer is 95% Private Use Area
codepoints, carrying a 25-row table printed 90 degrees sideways:

| | before | after |
|---|---|---|
| words extracted | 0 (all PUA) | **131** |
| PUA characters | 2321 | **0** |
| page appearance | — | pixel-identical |
| table found downstream | none | 33×7, correct column headers |
| data cells | — | unreliable — see `references/ocr-quality.md` |

The headers came back exactly right — `Operatore`, `Autorizzazioni`,
`Valore (EURO)`, `Valore Fini Dog.`, `Incidenza % su totale` — and the small
numeric cells did not. That is the honest shape of the result: **structure,
headings and prose recover; dense small figures in grey-shaded rotated cells
do not.**

## Rules this follows

- **Readable pages are never OCR'd.** Passing good text through OCR replaces it
  with worse text. Readable pages are copied through and counted as skipped.
- **The source is never modified.** Output always goes to a new file.
- **Readability is judged on output, not on the font table.** An earlier
  heuristic called any `/Identity-H` page with low character diversity
  "ciphered" and, on a real 1000-page volume, marked 133 perfectly readable
  pages unreadable and discarded them. A working ToUnicode CMap decodes
  `/Identity-H` perfectly well. The only question that matters is whether the
  extracted characters are text.
- **Low-confidence words are dropped, not guessed.** Below `--min-conf` the
  word is omitted, and the count is in the manifest. A gap in a table is
  visible; a plausible wrong number is not.

## Confirm the diagnosis first

Do not assume a PDF is garbled. Two minutes of checking prevents OCR-ing a
document that was merely encoded unusually, which wastes hours and can make
the text worse.

```python
import pymupdf
doc = pymupdf.open("volume.pdf")
p = doc[611]
print(repr(p.get_text("text")[:120]))
for f in p.get_fonts():
    xref = f[0]
    print(f[3], "ToUnicode:", doc.xref_get_key(xref, "ToUnicode"),
          "Encoding:", doc.xref_get_key(xref, "Encoding"))
```

- Text arrives as `\ue0xx` or similar Private Use codepoints → garbled, proceed.
- Text arrives as real words → **readable**, do not OCR it. Find the pages that
  are genuinely broken first.
- Text arrives as plausible-looking wrong words → a different failure; see
  *Ciphered* below.

## Known limits

- **Quality is tesseract's, not this skill's.** Read `references/ocr-quality.md`
  before trusting numbers off a repaired page. Mean confidence in the manifest
  is the first thing to look at, not the last.
- **Rotated pages.** A page whose content is turned 90° OCRs poorly. The
  table-extractor skill can derotate; do that first and OCR the result.
- **Tables read better than prose.** A rule-bounded table gives OCR column
  boundaries to work with; running prose does not. Expect numeric columns to
  survive and long sentences to be where errors cluster.
- **It cannot invent what the page does not show.** Stamps in a rotated margin,
  overprints, and text lost in a scan are gone before OCR sees them.
- **A repaired PDF is a derived artefact.** Keep the original. If a number looks
  wrong, the wrongness is as likely to be OCR's as the original's.

## Ciphered pages are a different problem

Where `/Identity-H` is present **and the ToUnicode CMap is missing or broken**,
the codes are glyph indices with no table. OCR also handles this, since the
page is still a picture — but the cause is different, and a document can be
part ciphered and part readable. The tool reports each page's own state, so
mixed documents are handled page by page rather than all-or-nothing.
