# READING-TABLES.md — reading a table, once you have it

[AGENTS.md](./AGENTS.md) owns *finding* tables: witnesses, spans, stamps, ministry
placement, detection traps. This file owns *reading* one — rotation, bands, columns,
grammars, printed values, and what the archive will not yield at all. The manifest
key, the optional article level and OCR being out of scope are stated in AGENTS.md
only; a second copy of the key is how the Dogane-overwrites-MAE bug returns.

Everything below was measured on the pages. A threshold with no measurement beside
it is indistinguishable from an arbitrary constant until it is too late.

## 1. Step 2 measures, it does not extract

`IndividualTables2SQL.py` (PDF → CSV via tabula-py) is **deleted**. It failed on the
tables that matter, for structural reasons rather than tuning: **it could not
rotate** (48 of 2025's tables print content rotated 90° inside a portrait page, and
tabula has no rotation normalisation); **it could not see the rules** (those tables
draw their grid as one-point rectangles, and `find_tables(strategy="lines")` returns
*zero* tables on A1 p1 while `strategy="text"` over-segments it 70×22); and **it had
no record grammar** (MAE prints a group header, a three-line wrapped record, then a
per-group subtotal — none of which survives flattening into cells).

So step 2 is `CatalogueTables.py`, which measures shapes. That is the necessary first
step, not a smaller job: the tables are neither stable across years nor uniform within
one, so nothing loads into SQL until the shapes are known.

```bash
python CatalogueTables.py --year 2025 --manifest Out/manifest-2025.jsonl
#   -> Out/CATALOG/catalog.sqlite   one row per (authority, article, code, year)
#      Out/CATALOG/census-<year>.md the report to open
```

**The seam is deliberate:** `lib/geometry.py` and `lib/grammar.py` hold the reading
logic and know nothing about ministries; `CatalogueTables.py` knows about files and
nothing about glyphs. Each is then testable alone.

## 2. Geometry

- **Rotation is per content block, not per page.** A1 p1 is 1770 rotated glyphs of table and 266 upright of running banner on one page, so a page-level flag is wrong by construction. `dominant_direction()` returns majority *plus* minority, so furniture is identified rather than outvoted.
- **Never infer rotation from box aspect ratios.** Measured: MEF `GF` 58% "tall", `PP` 60%, `NN`/`OO` 61%, against a clean family-2 majority of 63–90%. No threshold separates them, because several tables are genuinely mixed. The ratio is a symptom; the text matrix is the cause.
- **A float/int direction mismatch silently disabled the furniture filter.** `(0.0, -1.0) != (0, -1)`, so a set comparison found nothing to filter and every word stayed — MEF `NN` reported 279 "foreign" words and 7 real ones. `lines_of()` normalises both sides to rounded ints. *Same failure mode as the code-vs-triple key bug: a comparison that never matches reads as "no filter needed", not as a bug.*
- **Cell gaps must be em-relative, never points.** A printed label is one word run (intra-label advance under 0.2 em) while neighbouring columns sit 90–200pt apart at 7–9pt type — 3–20 em. `CELL_GAP_EM = 1.2` sits in an empty gap; an absolute 2pt cut split MG10's **three** columns into **fourteen**.
- **Band gaps must measure line height, not the band's own extent.** That height grows as the band grows, so the tolerance grows with it: Difesa Annesso 4 came back as four bands for three whole pages, header buried in the first data row. Cut on `ROW_GAP_EM` × font size against the band's *top line*.
- **A cell's `v` extent is not a row boundary.** A cell wrapping over twenty lines has a `v` extent covering the whole record, so grouping on extent absorbs every following line into one band. Group on the first word's baseline.
- **One printed line is one band**, and that is the right primitive. Deciding wrapped bands belong to one record is `grammar.py`'s job, not `geometry.py`'s.

## 3. Columns

- **Printed edges beat printed labels.** Labels are left-aligned in their cell and values right-aligned, so on `EE` the values of `Importi Segnalati` (u 371.6–394.8) overlap the next label's extent (396.9–451.4) and every narrow value files under the wrong column. The page's own rules give the true edges.
- **Believe the rules only when their column count matches the header's label count to within one** — a *borderless* table still returns positions. MG10 has no grid, but its running-head underline and margin-stamp box come back as ten separators; taken at face value they turned three columns into eleven.
- **When the drawing is incomplete, the body is the witness.** DOG `N1` draws three rules and jumps, so no rule-based cut finds its four columns at any threshold — but all 89 rows are four cells wide at four consistent reading positions. Recover them from the data consensus (`CONSENSUS_TOL = 12pt`, `CONSENSUS_SHARE = 0.6`, cluster count must equal the modal band width or return `[]` rather than a guess). The tolerance exists because right-aligned numbers vary: `6.418.312,50` and `40,00` are the same column 30pt apart. A partial drawing is a fact about the drawing; the body is the table.
- **A header is the first band that is wide (≥3 cells), mostly text, free of bare numbers, short-celled, not doubled, not the stamp or running furniture, and followed within four lines by a band ≥60% as wide.** Every clause cost a signature: MG10's header and rows are both 3 cells wide, so a width tie is arbitrary; Annesso 4 was catalogued with the column names `2`, `EFA`, `VELIVOLO`; MAE `O1` draws its table twice overlaid and the doubled band won; A1 was briefly catalogued as `MAECI - UAMA - CENTRO INFORMATICO | Pagina 1 di 537 | TAB A1`.
- **A stacked header is one header and its columns are the union.** MEF prints `Anno 2022 | Anno 2023` above `Valore in € | Quantità`, so a column name is a *path* — without the year level, 2022's and 2025's values land in one column and every cross-year diff reports it changed. DOG `N1` wraps its names over two lines so **neither band alone has three cells**: merge the narrow bands, cut each cell at its widest internal em gap (the gutter, since no line break is that wide), then merge across bands by overlap. The rescue from rules or consensus must run **before** the width test, or N1's 91-character cell fails the test the rescue was meant to pass. A rescued label must be **cut by position**, never returned whole — four columns from one cell otherwise produce four identical 91-character names.
- **Charts are ruled too, so curves are what distinguishes them.** MEF `NN` runs a 447pt rule — "has rules" says nothing — but carries **2140** Bezier segments against A1's zero. `CHART_CURVES = 200`, and a charted page with no columns is `chart`, not `unknown`: calling "no header found" about a pie chart is a false alarm.

## 4. Printed values — never `float()`

Italian convention throughout: `.` groups thousands, `,` divides, so `float("1.234,56")` yields **1.234**, wrong by 1000× and still looking like a number. `parse_number()` returns `Decimal`, or `None` rather than guessing.

| printed | parsed | note |
|---|---|---|
| `1.234,56` | `1234.56` | |
| `€ 12.877,00` | `12877.00` | currency may lead **or** trail |
| `0,00` | `0.00` | distinct from `—`, which is NULL |
| `—` / `-` | `None` | never zero |
| `016`, `01`, `001` | **`None`** | a leading zero on a bare 1–3 digit integer is the Military List category, **not** a quantity; `016` != `16` |
| `1.234` | `1234` | ambiguous by construction; read as thousands |
| `1,234.56` | `None` | the other convention; refused rather than misread |
| `(500,00)` | `-500.00` | |
| `03/03/2025` | `2025-03-03` | day-first, confirmed by the neighbouring `Data` column |
| anything else | `None` | caller keeps `raw_text` |

**A dash and a zero are different facts** and the archive prints both, so the
null-vs-zero policy is a *column-level* decision in `column_def`, never per cell.

## 5. Record grammars

Six labels, all derived from measured band counts. A seventh is entirely possible in
another year, so `unknown` is a real answer and is preferred to a fit.

| grammar | the page does | recognised by |
|---|---|---|
| `group+subtotal` | group header, records, printed subtotal (MAE A1) | group **and** subtotal bands both present |
| `repeated-subdimension` | one authorisation carries several causale rows (MEF `EE`) | a key column repeats while another varies |
| `flat` | one record per band (DOG `MG10`) | data bands, no group structure |
| `wrapped-list` | cells hold lists over twenty lines (Annesso 4) | wide text bands, no arithmetic anywhere |
| `chart` | a percentage chart | curves, and no columns |
| `unknown` | a cover, an empty table, unreadable text | recorded with a reason |

- **A single-cell band is not automatically a group header.** On a wrapped-list table every row is a few text cells, so the rule claimed all 113 and Annesso 4 came out as 113 group bands and no data. The separator is *width*: a group name is printed across the table, a wrapped cell sits inside its column.
- **`_wide_columns` must use the lower quartile, not the median.** With four widths the median is the mean of the middle two and lands on the widest column — `[30.9, 58.3, 82.8, 96.3]` gives 82.8, nothing clears 1.6× it, and the whole table reported no wide column. Measured: A1's description column is 6.3× its quartile, `Tipo` 0.9×; Difesa's `DITTE ITALIANE` 5.1×, its index column 0.2×.

## 6. Legibility

`page_stat` records **every page**, not a sample — a sampled census would overstate a
221-page table of which 216 are destroyed.

- The PUA **ratio** test (PUA > 15% of alnum) needs a companion: `H1` clears it while *every label* on the page is a PUA run, and the catalog recorded its columns as `pua pua pua`. Three PUA characters in a row (`PUA_RUN_RE`) mean the letters are gone, and such a page is refused as a source of geometry.
- `garbled` means **partly or wholly** destroyed. Never read it as "this page is empty" and never promote a partly readable table to a fully readable one.
- The archive's three corruption classes (~1300 pages) are tabulated in AGENTS.md §4. What matters for reading: a table with them splits correctly, appears with `grammar=unknown` and a reason, and yields **zero rows**. That is the ceiling, not a defect.

## 7. Verification — the oracle nobody used

**The tables print their own totals:** `Totale complessivo 262.920.093,96` (MG10),
`3.700.813,79 0,00` closing each operator's group (A1). That is the strongest check
available and the CSV step never used it: per additive column, `SUM` the projected
rows against the printed total, plus row-count reconciliation against the index's
page counts, plus a text-coverage check (share of extracted characters present in
the source page text) to catch silently dropped rows. Verdicts PASS/FAIL/WARN/SKIP
with FAIL at zero, log written before the JSON — the same discipline as
`VerifyTables.py`, which audits the *split* and knows nothing about extraction.

## 8. Records

The 2025 catalog baseline, the schema decision, and what is still outstanding live in
[ROADMAP.md](./ROADMAP.md) §4, §3 and §6 — not here.
