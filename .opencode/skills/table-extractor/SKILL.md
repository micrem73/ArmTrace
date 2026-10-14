---
name: Table Extractor
description: Extract every table from a PDF into JSON and CSV, with a per-page record of which pages could actually be read. Use when asked to pull tables, data grids, or figures out of a PDF, to convert a report's tables to a spreadsheet or database, or to audit how much of a PDF is machine-readable at all.
license: MIT
metadata:
  opencode/autoinvoke: true
---

# Table Extractor

Extract tables from a PDF. The hard part is not finding tables — it is not
inventing them, and not reporting a page it could not read as a page that
happens to contain no table.

## Quick start

```bash
python scripts/extract_tables.py <file.pdf> --out ./out
```

Writes `tables.json`, `tables.csv` and `preflight.json` to `./out`, and prints
a summary. No installation step: it needs only `pymupdf` (and `pandas` if you
want the CSV).

Verify the install at any time:

```bash
python scripts/selftest.py
```

The self-test builds synthetic PDFs that exercise every branch and asserts on
the output. 23 checks, no network, about two seconds.

## Run it this way first

| Goal | Command |
|---|---|
| Reliable results | `--min-cols 3` — drops two-column blocks, which is the shape of most page furniture |
| Everything, borderless included | `--method both` — much noisier, read the rejections |
| Check a suspicious page | `--pages 610-612` |
| Second opinion on ruled pages | `--camelot` — compares counts, warns on disagreement |
| A page keeps coming out mirrored | `--pages N`, then inspect `bbox` and rotation |

Full flags: `--help`.

## How it works

### 1. Preflight, before anything is extracted

Every page is classified first, because a page whose text layer is broken
yields *plausible-looking nonsense* rather than an error:

| State | Meaning |
|---|---|
| `text` | readable, extraction proceeds |
| `garbled` | subset font with no `ToUnicode`; codepoints are Private Use Area |
| `ciphered` | `/Identity-H` with no usable `ToUnicode`; glyph IDs decode to something, so the text reads like a language but is not one |
| `no-text-layer` | raster image, no characters |
| `empty` / `unreadable` | nothing to read, or the content stream is corrupt |

Non-`text` pages are **skipped with a warning naming the page**. This is the
point of the whole tool: on a 1000-page volume where 40% of pages cannot be
read, the honest output is "600 pages read, 400 missing, here they are" — not
a confident total that silently omits four tenths of the document.

### 2. Normalise rotation

A rotated page is turned upright before detection. The signal is the page's
**writing direction**, not `page.rotation`: when the turn is baked into the
content stream (the usual case for a scanned or re-laid-out page) `/Rotate`
reads 0, and a geometry-based detector silently transposes the table.

When a turn is needed both 90 and 270 are tried, and the one whose text runs
**left-to-right** wins. A shape check cannot choose between them — both end up
horizontal and almost identical — and picking at random mirrors the table about
half the time.

### 3. Detect, choosing on evidence

- **`ruled`** — vector rules, read from *both* line segments and filled
  rectangles. Generators that emit rules as one-point filled rectangles
  produce no line segments at all, so a detector reading only lines reports a
  fully ruled table as a page with no tables.
- **`whitespace`** — column separators are vertical gutters that persist
  across many lines. Persistence is what stops it inventing columns out of
  ordinary word spacing.

`ruled` is the default because it is precise. `whitespace` is opt-in: on real
documents it proposes several times as many candidates as there are tables,
and a paragraph wrapping at a fixed width is geometrically identical to a
borderless table.

### 4. Assemble

- **Page-spanning tables** are merged only when the headers match *and* the
  first part runs off the bottom of its page while the next resumes at the top.
  Header and column agreement alone is not enough: the same table printed on
  two facing pages matches on every count, and merging those concatenates
  unrelated data.
- **Trailing blank rows and columns** are trimmed. Blank rows in the *middle*
  are structure and are kept.

### 5. Emit

`tables.json` — every record carries its own provenance:

```json
{
  "id": "t_1a2b3c4d5e6f",
  "page_start": 610, "page_end": 611, "stitched": false,
  "method": "ruled", "derotated": 0, "confidence": "high",
  "bbox": [50.0, 100.0, 530.0, 645.0],
  "n_rows": 36, "n_cols": 7,
  "header": ["Operatore", "Autorizzazioni", "..."],
  "rows_data": [["MODELMECCANICA SFERICA", "1", "..."]],
  "page_text_state": "text",
  "warnings": []
}
```

`page_text_state` on every table is what makes the output auditable: a table
count without it cannot be compared against another run.

`tables.csv` is long-format (one row per non-empty cell) with the column name
in `header`, so ragged tables survive the round trip.

## Guardrails

These are the rules the tool follows, and they are the reason to trust its
output over a naive extractor:

- **Never report a count without the readability witness.** A page that yields
  no text is not a page without a table.
- **Never drop a candidate silently.** Every rejection — prose misread as a
  table, text outside a ruled grid, a block demoted as page furniture — is
  reported with its page number and the reason.
- **Never merge on weak evidence.** Stitching requires geometry, not just a
  matching header.
- **Never guess a column count.** Narrow grids must earn their place; a
  two-column ruled block is tagged `confidence: "low"` rather than presented as
  an equal.

## Known limits

Read `references/troubleshooting.md` before interpreting a surprising result.
The short version:

- **Multi-row headers fragment.** A header cell that spans columns is assigned
  to the column containing its centre, so a two-line header comes out as two
  ragged rows. The second line is usually the clean one.
- **Borderless tables are not reliably separable from prose.** Use
  `--method both` and read the rejection list; do not trust the count.
- **Two-column blocks are unconfirmed.** Use `--min-cols 3` unless you have
  reason not to.
- **A table that is one long run of zero rows** on later pages is reported once
  per page, not merged, because those pages carry no repeating header to match.
- **Unreadable pages are out of scope.** Pages with no text layer need OCR,
  which this tool does not do and will not pretend to.
