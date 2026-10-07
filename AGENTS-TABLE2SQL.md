# AGENTS-TABLE2SQL.md — reading a table, once you have it

**Scope, and the line this file does not cross.** [AGENTS.md](./AGENTS.md) is
authoritative for everything about *finding* tables: the witnesses, the spans,
the margin stamps, the ministry placement, the per-year coverage, the detection
traps. This file is about *reading* one — rotation, bands, columns, record
grammars, printed values, and what the archive will not yield at all.

Three things are shared between the two files and are stated **in AGENTS.md only**.
Do not restate them here, because a second copy of the manifest key is how the
Dogane-overwrites-MAE bug comes back:

- the manifest key is the triple `(authority, article, code)`;
- the article level in `Out/PDF/<authority>/[<article>/]<table><year>` is optional;
- OCR is out of scope.

Everything below was measured on the pages, not inferred. Where a threshold
appears, the measurement that produced it appears beside it, because an
arbitrary constant in this domain is indistinguishable from a correct one until
it is too late.

---

## 1. What replaced the CSV step, and why the old one was deleted

`IndividualTables2SQL.py` converted each per-table PDF to CSV through tabula-py.
It is **deleted**. It failed on exactly the tables that matter — A1 above all —
and the reasons are structural, not tuning:

- **It could not rotate.** `tabula.read_pdf` has no rotation normalisation, and
  48 of 2025's 110 tables print their content rotated 90° inside a portrait A4
  page. Word boxes come out 13pt wide and 54pt tall.
- **It could not see the rules.** Those tables draw their grid as one-point
  *rectangles*, never as 2-point paths. `PyMuPDF.Page.find_tables(strategy=
  "lines")` returns **zero tables** on 2025 A1 page 1; `strategy="text"` returns
  a 70×22 over-segmentation of a ten-column table.
- **It had no record grammar.** tabula emits a rectangle grid. MAE prints a
  group header, then a record whose description wraps over three lines, then a
  per-group subtotal; MEF repeats a `Causale` sub-dimension inside one
  authorisation number. None of that survives being flattened into cells.

So step 2 is now `CatalogueTables.py`, which **measures** table shapes rather
than extracting them. It is the necessary first step, not a smaller version of
the job: the archive's tables are neither stable across years nor uniform within
one, so nothing can be loaded into SQL until the shapes are known.

```
CatalogueTables.py --year 2025 --manifest Out/manifest-2025.jsonl
    ->  Out/CATALOG/catalog.sqlite   one row per (authority, article, code, year)
        Out/CATALOG/catalog-<year>.jsonl
        Out/CATALOG/census-<year>.md   the report to open
```

`lib/geometry.py` and `lib/grammar.py` hold the reading logic and know nothing
about ministries. `CatalogueTables.py` knows about files and nothing about
glyphs. The seam is deliberate.

## 1a. The working method is unchanged: one year at a time

AGENTS.md §1a's rule applies here with the same arithmetic. A year is
generated, catalogued, read, and judged; only then is the next one started.

| step | 2025 |
|---|---|
| generate (step 1) | ~13 min, 110 tables |
| catalog (step 2) | **141 s** |
| audit (step 3) | ~12 min cold, <1 min warm |

The catalog is fast enough that a full year is cheaper than one verifier run, so
it can be re-run after every edit — which is what the measurement culture in
this repo depends on. A year is judged by a person, not by the agent.

## 1b. Measured 2025, the baseline every later year is diffed against

| | |
|---|---|
| tables / pages | 110 / 1952 |
| content rotated 90° | 48 |
| rotated table under an upright running banner | 57 |
| ruled (a rule ≥ 20pt) | 110 |
| four are percentage **charts**, not grids | 4 (`NN`, `OO`, `PP`, `GF`) |
| text destroyed at the font level | **19 tables, 498 pages** |
| extractable | 91 |

Grammars, each labelled from measured band counts and never assumed per
ministry: **60** `group+subtotal`, **22** `repeated-subdimension`, 4 `chart`,
2 `flat`, 2 `wrapped-list`, **20** `unknown`.

The twenty unknown are 19 tables with no readable text at all — the whole of `E`
(221pp) and `C1` (97pp) among them — plus `H1`, garbled on 10 of its 32 pages,
and DIFESA Annesso 2, a company list with no header band. **None of these is a
pipeline failure.** The census states the reason per table; a coverage claim that
quietly omitted the unreadable third of an archive would be the failure mode this
whole step exists to prevent.

---

## 2. Reading geometry: the traps, each with the measurement that found it

### Rotation is per content block, not per page

A1 page 1 is 1770 rotated glyphs of table and 266 upright glyphs of running
banner, on one page. A page-level "is this rotated" flag is therefore wrong by
construction. `dominant_direction()` counts glyphs per direction from the content
stream and returns the majority *plus* the minority, so the furniture is
identified rather than merely outvoted.

**Never infer rotation from box aspect ratios.** Measured: MEF `GF` 58% "tall",
`PP` 60%, `NN`/`OO` 61%, `KK1` 61%, against a clean family-2 majority of 63–90%.
No threshold separates them, because several tables are genuinely mixed. The
aspect ratio is a symptom; the text matrix is the cause.

### A float/int direction mismatch silently disabled the furniture filter

`Frame.down` is built from a float direction; a caller's literal `[(1, 0)]` is
ints. `(0.0, -1.0) != (0, -1)`, so a set comparison found nothing to filter and
every word stayed. On MEF `NN` the result was **279 "foreign" words and 7 real
ones** — the banner was kept and the table discarded. `lines_of()` now normalises
both sides to rounded ints. *This is the same failure mode as the code-vs-triple
key bug in AGENTS.md §2: a comparison that never matches reads as "no filter
needed", not as a bug.*

### Cell gaps must be em-relative, never points

A printed label arrives as **one word run** — intra-label advance under 0.2 em —
while neighbouring columns sit 90–200pt apart at 7–9pt type, i.e. **3–20 em**.
`CELL_GAP_EM = 1.2` sits in the middle of an empty gap. The first attempt used an
absolute 2pt cut and split MG10's **three** columns into **fourteen**.

### Band gaps must measure line height, not the band's accumulated extent

An early version scaled the cut by the band's own height. That height grows as
the band grows, so the tolerance grows with it: Difesa Annesso 4 came back as
**four bands for three whole pages**, its header buried inside the first data row.
The cut is now `ROW_GAP_EM` × the line's font size, compared against the band's
*top line*.

### A cell's `v` extent is not a row boundary

A cell whose text wraps over twenty lines has a `v` extent covering the whole
record. Grouping on extent absorbs every following line into one band. Group on
the *baseline* (`v0` of the first word) and compare against that.

### One printed line is one band, and that is the right primitive

A record that wraps over several lines (A1's description, the Difesa list cells)
*should* come back as several bands. Deciding they belong to one record is
`lib/grammar.py`'s job, not `geometry.py`'s, and keeping the two apart is what
lets each be tested alone.

---

## 3. Column geometry: printed edges beat printed labels

**Labels are left-aligned inside their cell; values are usually right-aligned.**
On 2025 `EE`, `Importi Segnalati` is printed at u 344.6–379.1 while its values
sit at 371.6–394.8 and `Importi Accessori Segnalati` at 396.9–451.4 with its
values at 453.5–468.0. Mapping values onto label extents files every narrow value
under the wrong column. The page's own rules give the true edges: 6 separators,
5 columns, exactly the printed header.

So `resolve_columns()` prefers the rules, and believes them only when their
column count matches the header's label count to within one — because a
**borderless** table still returns positions. MG10 has no grid at all, but its
running-head underline and its margin-stamp box come back as ten separators, and
taking those at face value turned three columns into eleven.

### When the drawing is incomplete, the body is the witness

DOG `N1` draws column rules at −368.6, −249.8, −130.9 and then jumps to −460, so
its four-column body is bounded by only three of its own edges. No rule-based cut
finds four columns on that page, at any threshold. But **all 89 of its rows are
four cells wide at four consistent reading-space positions**, so the columns are
recovered from the consensus of the data bands (`_consensus_columns`), and the
body is allowed to overrule the drawing. A partial drawing is a fact about the
drawing; the body is the table.

Right-aligned numbers are the reason the consensus needs a tolerance: `6.418.312,50`
and `40,00` are the same column and start 30pt apart. `CONSENSUS_TOL = 12pt`,
`CONSENSUS_SHARE = 0.6`, and the cluster count must equal the modal band width
or `[]` is returned rather than a guess.

### Finding the header band took seven clauses, and each one cost a signature

A header is the **first** band that is wide (≥3 cells), mostly text, short-celled,
free of bare numbers, not doubled, not the margin stamp or the running
furniture, and followed within four lines by a band at least 60% as wide.

| clause | why it is needed |
|---|---|
| ≥3 cells | MG10's header and its data rows are both 3 cells wide, so a width tie is arbitrary |
| mostly text | a header is words; a data band carries numbers |
| no bare numbers | on a wrapped-list table every data row is as wide and as wordy as the header — Annesso 4 was catalogued with the column names `2`, `EFA`, `VELIVOLO`, `ITALIA,` |
| short cells (≤60 chars) | Annesso 4's data cells run to twenty company names; the longest real header cell is 31 (`Materiale oggetto del contratto`) |
| first of its kind on the page | Annesso 4 prints no header on later pages, so scoring all bands assembled a signature from rows on different pages |
| not doubled | MAE `O1` draws its table **twice overlaid**; the doubled band is wider than the real header, so it won and produced the column names `GE AVIO S.R.L. GE AVIO S.R.L. \| EX EX \| ...` |
| not the stamp | the margin stamp is rotated on a rotated page, so it forms a valid-looking band: A1 was briefly catalogued as `MAECI - UAMA - CENTRO INFORMATICO \| Pagina 1 di 537 \| TAB A1` |

### A stacked header is one header, and its columns are the union

MEF art. 27 prints `Anno 2022 | Anno 2023` above `Valore in € | Quantità`, so a
column name is a *path*, not a string. Two things follow:

- Without the year level, 2022's value and 2025's value land in one column and
  every cross-year diff reports that column as changed. `header_path` records the
  join; the inner label stays verbatim.
- DOG `N1` wraps every long column name over two lines, so the header spans two
  bands and **neither alone has three cells**. `_stacked_band_header()` merges a
  group of narrow bands, cutting each cell at its widest internal em gap — the
  column gutter, since no intra-column line break is that wide — then merging the
  pieces across bands by overlap, which is what joins `Numero di operazioni` to
  `svolte`.

The rescue from rules or consensus must run **before** the width test. Tested
after, DOG `N1`'s 91-character second cell fails the test the rescue was meant to
pass, and the header is lost twice over.

And a rescued column's label must be **cut by position**, never returned whole:
four columns rescued from one 91-character cell otherwise produce four identical
91-character "names".

### Charts are ruled too, so curves are what distinguishes them

MEF `NN` runs a 447pt rule across the page — "has rules" says nothing. It carries
**2140** Bezier segments against A1's zero. `CHART_CURVES = 200`, and a charted
page with no columns is labelled `chart`, not `unknown`: `NN`/`OO`/`PP`/`GF` are
`Grafico Ripartizione percentuale`, and saying "no header found" about a pie
chart is a false alarm.

---

## 4. Printed values: never `float()`

Italian convention throughout — `.` groups thousands, `,` divides. `float()`
on `1.234,56` yields **1.234**, wrong by 1000× and still looking like a number.
`parse_number()` returns `Decimal` and returns `None` rather than guessing.

| printed | parsed | note |
|---|---|---|
| `1.234,56` | `1234.56` | |
| `€ 12.877,00` | `12877.00` | currency may lead **or** trail |
| `0,00` | `0.00` | distinct from `—`, which is NULL |
| `—` / `-` | `None` | never zero |
| `016`, `01`, `001` | **`None`** | a leading zero on a bare 1–3 digit integer is the Military List category, **not a quantity**; `016` != `16` for every query anyone will ask |
| `1.234` | `1234` | ambiguous by construction; thousands, because the integer part is 1–3 digits |
| `1,234.56` | `None` | the other convention; refused rather than misread |
| `(500,00)` | `-500.00` | |
| `03/03/2025` | `2025-03-03` | day-first, confirmed by the neighbouring `Data` column |
| `1,234.56`-shaped garbage | `None` | caller keeps `raw_text` |

**A dash and a zero are different facts** and the archive prints both. The
null-vs-zero policy is therefore a *column-level* decision recorded in
`column_def`, never inferred per cell.

---

## 5. Record grammars, and how each is identified

Six labels, all derived from measured band counts. A fifth grammar is entirely
possible in another year, so `unknown` is a real answer and is preferred to a fit.

| grammar | what the page does | how it is recognised |
|---|---|---|
| `group+subtotal` | a group header band, records, then a printed subtotal (MAE A1: `A.C.S.A. STEEL FORGINGS S.P.A.` … `3.700.813,79 0,00`) | group bands **and** subtotal bands both present |
| `repeated-subdimension` | one authorisation carries several causale rows (MEF `EE`: `92956` over three lines) | a key column repeats across consecutive data bands while another varies |
| `flat` | one record per band (DOG `MG10`) | data bands, no group structure |
| `wrapped-list` | cells hold lists wrapped over twenty lines (Difesa Annesso 4) | wide-column text bands with no arithmetic anywhere |
| `chart` | a percentage chart | curves, and no columns |
| `unknown` | a cover, an empty table, or unreadable text | recorded with a reason |

Two traps in the classifier, both found the hard way:

- **A single-cell band is not automatically a group header.** On a wrapped-list
  table every row is a handful of text cells and the single-cell rule claimed all
  113 of them, so Annesso 4 came out as 113 group bands and no data. The test
  that separates them is *width*: a group name is printed across the table, a
  wrapped cell sits inside its column.
- **`_wide_columns` must use the lower quartile, not the median.** With four
  widths the median is the mean of the middle two and therefore lands on the
  widest column — `[30.9, 58.3, 82.8, 96.3]` gives a median of 82.8, nothing
  clears 1.6× it, and the whole table reported no wide column at all.
  Measured populations: A1's description column is 6.3× its quartile, `Tipo` is
  0.9×; Difesa's `DITTE ITALIANE` is 5.1×, its index column 0.2×.

---

## 6. Legibility is a whole-file property, and a partly-destroyed page is unusable

`page_stat` records **every page**, not a sample. A sampled census would overstate
a 221-page table of which 216 are destroyed.

- The **ratio** test (PUA > 15% of alnum) is right in principle and still needs a
  companion: 2025 `H1` clears it while *every label* on the page is a PUA run, and
  the catalog recorded its columns as `pua pua pua`. Three PUA characters in a row
  (`PUA_RUN_RE`) mean the letters are gone, and such a page is refused as a source
  of geometry.
- `garbled` means **partly or wholly** destroyed. Never read it as "this page is
  empty", and never promote a partly readable table to a fully readable one.

The archive's three corruption classes, ~1300 pages that cannot yield data, are
tabulated in AGENTS.md §5 and are **not** restated here. What matters for reading:
a table with them splits correctly, appears in the catalog with
`grammar=unknown` and a reason, and yields **zero rows**. That is the ceiling, not
a defect.

---

## 7. Verification, and the oracle nobody used

**The tables print their own totals.** `Totale complessivo 262.920.093,96` (MG10),
`3.700.813,79 0,00` closing each operator's group (A1). That is the strongest
check available and the CSV step never used it: per additive column,
`SUM` over the projected rows against the printed total, plus row-count
reconciliation against the index's page counts, plus a text-coverage check (share
of extracted characters present in the source page text) to catch silently
dropped rows. Verdicts PASS/FAIL/WARN/SKIP with FAIL at zero, log written before
the JSON — the same discipline as `VerifyTables.py`, which audits the *split* and
knows nothing about the extraction.

## 8. Where the data is going, and the schema decision

**Hybrid: one lossless EAV plus curated projections.** `stg_cell(authority,
article, code, year, page, row_ord, col_ord, row_kind, col_key, raw_text,
num_value, text_value, unit, is_total)`, and a small number of canonical tables
built from the lineage map. Nothing is ever lost and an unmapped family is still
queryable.

Rules that make it a *dump* rather than a database: `DECIMAL` never `FLOAT`; units
in `column_def`, never in a value; canonical columns in `snake_case` with the
printed Italian kept in `column_def.label_it` (the downstream chatbot asks in
Italian, but stable identifiers matter more); deterministic output — fixed table
order, fixed row order by natural key, explicit column lists, 2-dp decimals, LF,
**UTF-8 without BOM** (a BOM in a `.sql` file is a syntax error, unlike the old
CSV default), `BEGIN`/`COMMIT` per file, `--replace` opt-in for `DROP`.

---

## 9. Outstanding

- **Phase 2 — the diff.** Built and queryable, but it has nothing to compare
  against. Needs a second catalogued year, and **step 1 for 2024 is still broken**
  (10 of 31 tables detected in vol I, five phantoms in vol II), so 2024 is where
  the diff earns its keep. The witness must ride along on every presence cell or
  it will report tables disappearing that were never legible.
- **Phase 4 — the extractor.** Per-grammar record assembly (continuation merge,
  group rows, subtotal rows), typed values, `stg_cell`, projections, `.sql`.
  Not started.
- **Phase 5 — the tool bake-off.** Not started. Benchmark set with hand-transcribed
  ground truth: A1 p1 (rotated, ruled, nested), `EE` p1 (repeated sub-dimension),
  `MG10` p2 (flat borderless + printed total), Annesso 4 p1 (wrapped lists), one
  garbled page (expected: nothing). Metrics: row/col precision and recall, cell
  exact-match, ms/page, determinism, dependency footprint. PyMuPDF is the current
  reading layer; pdfplumber, Camelot and tabula-py are to be measured as
  baselines. *Prior, to be checked rather than assumed:* tabula has no rotation
  normalisation and cannot express a group header, so it is expected to fail on
  the 48 rotated tables; it may earn a role on the flat ruled minority.
- **`H1`'s 22 legible pages of 32.** Labels destroyed, digits intact. Worth OCR?
  Currently out of scope by decision; recorded as the ceiling.
- **`DIFESA/A2C6/2` has no header band** and is catalogued `unknown` at 5 legible
  pages. It is a company list (`1. SITAEL S.p.A. | Nr. 00977`), so the honest
  answer may be a *new grammar* rather than a better header detector.
- **MEF `IBB` reports 3 columns named `LEGISLATURA – DISEGNI DI LEGGE E…`** — the
  page furniture, not a header. A real miss, not yet diagnosed.
- **DOG `O1`/`O2` report column names like `e \| , 3`** from a page whose first
  band wins for the wrong reason. Also undiagnosed; both are visible in the
  census, which is the argument for keeping the census.