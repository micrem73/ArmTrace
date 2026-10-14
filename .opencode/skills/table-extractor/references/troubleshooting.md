# Troubleshooting

Failure modes of `extract_tables.py`, what each one looks like, and what to
do about it. Ordered by how often they mislead.

---

## "It found no tables" but the page clearly has one

Check, in this order.

**1. The page was not readable.** Look in `preflight.json`. A page in state
`garbled` or `ciphered` is skipped with a warning, and its tables are *missing,
not absent*. This is the most common reason for a low count, and it is silent
in any tool that does not check. It is not silent here.

**2. The page was rotated.** The page's own `/Rotate` may read 0 while the
content is turned 90 degrees in the content stream — the normal case for
scanned and re-laid-out documents. If so, `preflight.json` will not say, but a
`warnings` entry naming a rotation will. If neither appears, the content may
be rotated in a way that still reads as horizontal, which no geometric test
can see; extract that page with `--pages N` and read the cells.

**3. The rules are not rules.** If the table is drawn as one-point filled
rectangles rather than line segments, a detector reading only line segments
finds nothing. This tool reads both, so if you are porting the approach
elsewhere, check this first — it is the single most common cause of
"the page has no tables" for a page that plainly does.

**4. It is a borderless table.** Ruled detection will not see it. Try
`--method both`. Expect noise; see below.

**5. It is genuinely one or two rows.** The default `--min-rows 3` rejects it.
Lower it deliberately: `--min-rows 2`, and read the extra warnings.

---

## It found far more tables than the document contains

Nearly always the whitespace detector reading prose as a table, and/or running
headers and footers being read as two-column tables.

**Running headers and footers.** These repeat on most pages of a report and
look exactly like a tidy two-column table in isolation. The tool removes the
ones whose text recurs across eight or more pages, and flags the rest as
`confidence: "low"`. **Use `--min-cols 3`.** This is the single highest-value
flag: on a 1000-page volume it took 212 candidates down to 15, and every one
of the 15 was a real table.

**Prose wrapping at a fixed width.** A paragraph in a two-column layout
produces a stable vertical gutter and is geometrically identical to a
borderless table. Rejected when most right-hand cells start lower case, when
cells are long, or when rows do not fill their columns. Not always caught — a
sentence beginning with a capital in a narrow column will get through. Read
the rejection list and spot-check.

Page *position* does not help. It is tempting to reject anything in the top or
bottom band of the page, and that was measured on a real document: 9 of 15
genuine tables sat in the top 12% of the page, because tables start right after
a heading. A position filter would have deleted real data.

---

## A table is mirrored, or rows and columns are transposed

The page was rotated and turned the wrong way. The tool prefers the rotation
that makes text run left-to-right, which is right for the overwhelming majority
of documents. For a genuinely right-to-left document the preference is wrong
in principle, though the text is still extracted — in its own direction, which
is the correct outcome.

If a specific page is mirrored, extract it alone and check `derotated` in the
record:

```bash
python scripts/extract_tables.py doc.pdf --pages 610 --out ./x
```

---

## A two-line header came out as two ragged rows

A header cell spanning several columns is assigned to the column containing its
centre, so its words land in one cell and the others are blank. The second line
is usually the clean one, and the first line's fragments belong with it.

This is the main structural weakness of centre-based assignment, and it is not
silently wrong — the fragments are present, just not merged. If merged headers
matter, the data rows are still correct.

---

## A table continues onto the next page but came out as two records

Stitching requires three things to agree: identical header, identical column
count, **and** geometry — the first part must run off the bottom of its page
while the next resumes at the top. That third condition is what keeps two
genuinely separate tables from being merged.

A continuation whose later pages carry *no repeating header* cannot be matched
and stays split. A long table whose later pages are all zeros is the common
case: there is nothing on those pages to match a header against. Recognise
these by the all-zero or near-empty rows and merge downstream on position.

---

## The ruled grid is missing a column or row

The warning `text item(s) sit immediately outside the ruled grid` means text
was dropped because the rules do not enclose it — usually a border that stops
short. A missing right-hand border silently costs the last column.

The warning only fires for text *immediately* outside the grid. A heading two
inches above the table is outside the grid and correctly ignored; the near
misses are the ones worth reporting.

---

## A blank form or an empty table was not detected

Partially filled tables are detected, because a fully enclosed cell with no
text still counts as part of the grid. A table that is **entirely** empty has
no enclosing evidence of its own and is not reported — there is no way to tell
an empty table from an empty box on the page.

---

## `--camelot` disagrees

Not automatically an error. `camelot` and this script differ about what counts
as a table: `camelot`'s lattice needs Ghostscript and its own idea of a ruling,
and the two disagree most on sparse or unusual grids. Treat a disagreement as
"look at this page", which is how the tool reports it.

Requires Ghostscript for the lattice flavour. If it is missing, the check is
skipped with a warning rather than failing the run.

---

## The run is slow

Roughly linear in pages, with the cost dominated by rule collection on vector-
heavy pages. Use `--pages` to work in ranges. One unreadable page never stops
the run — extraction is guarded per page, and per strategy, so a failure costs
that page and not the document.

---

## Exit behaviour

The tool exits 0 even when pages are unreadable or candidates are rejected.
That is deliberate: a document with 40% dead pages is a normal input, not a
crash. **Read the summary and the warnings** — that is where the information
is. A run that reports `total 60` and `406 pages could not be read` is doing
its job; ignoring the second line is how the first one gets misread.

Re-running the same file gives identical table ids, so results can be diffed
between runs.
