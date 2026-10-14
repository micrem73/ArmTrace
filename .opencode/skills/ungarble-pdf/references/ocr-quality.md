# OCR quality

The repair is only as good as tesseract's reading of the page. This is what to
check before trusting a number off a repaired page.

## Read the manifest first

`repaired.pdf.manifest.json` carries, per page:

| Field | Meaning |
|---|---|
| `words_found` | words tesseract reported |
| `words_written` | words actually placed on the page |
| `words_dropped_low_conf` | words below `--min-conf`, omitted rather than guessed |
| `mean_conf` | tesseract's mean confidence, 0–100 |

A page with `mean_conf` under ~80 deserves a look. A page with a high mean can
still be wrong in the places that matter — confidence is per word, and a
confidently misread digit is still a wrong digit.

## Where OCR errors actually land

**Numbers.** OCR confuses characters that share a shape: `0`/`O`, `1`/`l`/`I`,
`5`/`S`, `8`/`B`, `6`/`G`, and `7`/`1`. In a column of figures these are
indistinguishable from correct output. If a figure matters, check it against
the source page image rather than trusting the extracted value.

**Decimal separators and thousands separators.** A comma, a period and a
apostrophe are easily swapped, and European and Anglo conventions differ. A
value of `79.382.597,01` can come back as `79,382,597.01` — numerically the
same, textually different, and a silent failure for anything that parses it.
Compare against the page before converting.

**Accented characters.** Italian text carries à è é ì ò ù and the occasional
`ﬁ`/`ﬂ` ligatures. With the wrong `--lang` these come back as noise. Use
`--lang ita+eng` for Italian; check the manifest for dropped words, which is
what a wrong language looks like.

**Rules and borders.** OCR occasionally reads a table border as a character. A
cell reading `|`, `—` or `I` where a number should be is usually a rule, not a
glyph.

## Tuning, and what actually helps

### Raising the DPI is usually the wrong first move

Measured on a real page — a sideways table, small serif figures on grey cell
shading, which is about the worst realistic case:

| DPI | words | mean confidence | table headers | data cells |
|---|---|---|---|---|
| 300 | 198 | 55.2 | correct | garbage |
| 450 | 217 | 54.5 | correct | garbage |
| 600 | 204 | 54.2 | correct | garbage |

Confidence went *down* as resolution went up, and the data cells did not improve
at all. When the problem is low contrast against cell shading rather than
too few pixels, more pixels buy nothing.

Raise DPI only when the print is genuinely too small to resolve. Otherwise spend
the time elsewhere.

### Page segmentation mode matters more

Same page, same DPI, varying `--psm`:

| psm | words | mean confidence | what you get |
|---|---|---|---|
| 4 (one column) | 174 | 55.7 | headers, some data |
| **6 (one block)** | 148 | **70.5** | clean headers, data rows empty |
| 11 (sparse) | 184 | 57.3 | headers, some data |
| 12 (sparse + OSD) | 208 | 57.0 | clean headers, most data |

A real trade-off, and no mode that wins on both. `--psm 6` gives the best
confidence and the cleanest headings but returns the data cells empty. `--psm
12` keeps more data at lower confidence. Run both on a page you can check and
pick per document.

Confidence and usefulness are not the same axis: psm 6 scored highest and lost
the data.

### Other levers

| Symptom | Try |
|---|---|
| Whole page is one table | `--psm 6` |
| Column text interleaved on a two-column page | `--psm 4` |
| Sparse table on a mostly blank page | `--psm 11` or `--psm 12` |
| Spurious empty words | raise `--min-conf` to 40–50 |
| Low contrast against cell shading | binarise the rendered page before OCR |

## Where it fails, specifically

**Small figures in shaded cells of a rotated table.** This resists everything,
and it is common in statistical annexes: the type is small, serif, set
sideways, and printed on a grey fill. Headers survive because they are larger
and bolder. Data cells come back as `|`, `ooo`, `so]`, or empty.

The structure is still worth having. Knowing the page holds a 7-column table
titled `ESPORTAZIONI DEFINITIVE ... PER OPERATORI, VALORE DECRESCENTE` with
those exact column names is real information, and it is what makes the
unrecovered cells visible as gaps rather than as absence. But do not treat the
figures as data.

## Checking the result

The repaired page should extract as real text, with its geometry intact:

```python
import pymupdf
doc = pymupdf.open("repaired.pdf")
p = doc[0]
print(repr(p.get_text("text")[:200]))      # real words, no PUA boxes
print(len(p.get_drawings()), "rules kept")  # table borders survive
w = p.get_text("words")
rows = {}
for x in w:
    rows.setdefault(round(x[1]), []).append(x[4])
print({y: len(v) for y, v in rows.items()}) # rows of words, not one long stream
```

The last line is the one that matters. A single long line means the geometry
was lost and the page will read as prose to any column detector.

## When OCR is the wrong tool

- **The page is genuinely blank or is an image, not text.** There is nothing to
  read; the document's data does not exist on the page.
- **The text is rotated in the content stream.** OCR reads it as garbage.
  Derotate first — the table-extractor skill does this — then OCR.
- **You need the text to be exact.** OCR is a best estimate. For figures that
  must be right, read them off the page image or get a better source.
- **The volume is enormous and mostly readable.** OCR only the unreadable
  pages; the tool does this already, but check the skip count is what you
  expect before starting a long run.

## Cost

Roughly 1–3 seconds per page at 300 dpi on an ordinary machine, dominated by
tesseract rather than by rendering. A 350-page repair is an hour, not a
minute. Use `--dry-run` for the estimate, `--limit` to test, and `--pages` to
work in ranges. The manifest records per-page results, so a long run that is
interrupted has still told you what it did.
