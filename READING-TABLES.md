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

The 2025 catalog baseline — tables, pages, grammar counts, unreadable pages — is
**not** restated here: it is a measurement, it moves when a feature lands, and
ROADMAP.md owns it. A second copy of a baseline goes stale silently.

**The seam is deliberate:** `lib/geometry.py` and `lib/grammar.py` hold the reading
logic and know nothing about ministries; `CatalogueTables.py` knows about files and
nothing about glyphs. Each is then testable alone.

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

### A1 defeats both witnesses at once, and the printed totals say so

**This is the defect the extractor exists to find, and it is still open.** On
A1 p1 the vector layer returns **three** rules, at −105.4, −97.0 and −78.8 — all
of them to the *right* of the table, which spans −782.5…−137.9. They are the
margin stamp's box, not the table's grid. `resolve_columns()` clips to the labels
and returns `source='header'`: ten columns, all of them the **left-aligned header
labels**, which is the one frame the file's own §3 warns against.

The body then disagrees with it, and `_consensus_columns` cannot arbitrate:
it clusters on cell `u0`, and A1's money columns are right-aligned, so a 13-band
sample yields clusters of `n=1,2,2,2,1,2,4,4,10,4,4` — kept = 1 against a modal
width of 9, so `[]`. A wrong frame is worse than an absent one, so it declines.
The catalog records A1's ten labels and stops there, which is **correct for the
catalog** — it reports what is printed. It is not sufficient for extraction.

What the numbers look like with the label frame in force, on six pages:

| col | printed label extent | where the values actually sit |
|---|---|---|
| 1 `Ammontare` | −729.2 … −692.8 | right edge **−690.69**, and it *overlaps* col 2's label |
| 2 `EURO` | −662.2 … −641.8 | `856.813,79` ends at −626.4 |
| 3 `Val. Fini Dog.` | −610.4 … −566.7 | `34.299,20` at −591.1 … −564.4 |

`cell_matrix()` puts a word in the column it overlaps most, so `34.299,20` —
which lands in **col 1** of the frame, the `Ammontare` column — is charged to the
wrong column. The verifier then reports what the arithmetic says:

```
FAIL group.subtotal p2: group 15 · EURO: assembled 0 vs printed 100938
FAIL group.subtotal p3: group 89 · Val. Fini Dog.: assembled 197969.36 vs printed 655242.46
FAIL file.grand_total  p2: file · EURO: assembled 875443.43 vs printed 3700813.79
```

**A projection of the occupied columns is the third witness, and it works.**
Collecting every word's extent over the data bands and marking the `u` positions
that are **empty in ≥90% of them** finds nine gutters and therefore ten columns:

```
u -782.5 … -759.5   -745.5 … -721.5   -690.5 … -550.0   -511.5 … -488.5
    -471.0 … -445.0   -415.5 … -397.0   -210.0 … -181.0
    -173.5 … -154.5   -143.5 … -137.5
```

Gutters, not clusters, is the right primitive here and the reason the cluster
version failed: a gutter is a **shared absence**, which survives right-alignment,
whereas a cluster is a shared *position*, which right-alignment destroys. The
one wide gutter (−690.5…−550.0) is genuine — `Ammontare`, `EURO` and
`Val. Fini Dog.` have no gap between them on most rows because a wide amount in
one sits against a narrow amount in the next.

Two things are **not** fixed by this and are recorded rather than papered over:
the label frame's cells are not *edges* (they are the text's own extents), so
header-to-column mapping still needs the header's words; and whether a recovered
frame agrees with the labels is a check in its own right, not an assumption.

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

## 9. Extraction: step 4 and step 5, and the measured baseline

`ExtractTables.py` assembles records per grammar and writes `stg_cell`;
`VerifyExtraction.py` audits them against the totals the tables print. Neither is
finished. **This section is the baseline, written before the first fix, exactly
as AGENTS.md §1a requires — and the user judges "clean", not the agent.**

### Measured 2025 — baseline, then after the FAIL round

| | baseline | after the FAIL round |
|---|---|---|
| tables catalogued | 150 (122 extractable) | same |
| grammars implemented | `flat`, `group+subtotal` | same |
| cells written | 94,358 | **94,606** |
| tables with **zero FAIL** | 48 of 93 | **78 of 93** |
| verifier | 879 PASS, 166 FAIL, 53 WARN, 573 SKIP | **1068 PASS, 11 ROUNDED, 32 FAIL, 51 WARN, 1171 SKIP** |
| `row.reconciliation` | 18 FAIL | **0 FAIL** |

**The FAIL count did not fall monotonically, and the reason matters.** It went
166 → 119 → 105 → 89 → 75 → 62 as the defects below were fixed, then **up to 92**
when a *new check* was added — a total may name the scope it closes
(`ALBANIA Totale`) — which found 30 findings nothing had been looking for, and
then **down to 43** through three more real defects (`### 11` below, the
`complessivo` keyword, and the mixed-currency delta being reported against the
wrong candidate), and finally **to 32** once the rounding rule was settled. Adding
a check that finds defects is only a gain if the check is right, which is why
each new one was validated against a case it could get wrong before being kept.

Twelve root causes, each measured. **Five were in the extractor, seven in the
verifier**, which is worth knowing before reading the list: the oracle was wrong
more often than the extraction was.

### 1. `parse_number` concatenated two numbers into one — **data corruption**

    '192 615.457.414,48'  ->  192615457414.48

A count and a value that landed in the same cell, because the header over them
is stacked, parsed into one plausible-looking `Decimal` wrong by nine orders of
magnitude. **137 cells of 2025.** Now refused: a space between digits whose
*tail* carries a separator means two numbers, while `1 234` is still a thousands
separator. This is the worst class of bug in the project — plausible, and
silently wrong — and no column check would ever have found it, only a total that
did not add up.

### 2. A printed value on a continuation line was dropped

A1 p64 prints its `Val. Fini Dog.` figure **on its own line** under a record whose
cell for that column is empty. `_merge_into` skipped it, reasoning that a column
the record left empty was never printed — and threw away `400.000,00`, which is
how one group's subtotal came out short by exactly that, in two columns at once.
A continuation into an empty column is now a **cell of that record**.

### 3. A header that disagrees with the catalog is not a header

On **33 of A1's 537 pages** `find_header` returned a band of description text --
`TD-H-200 -  TECNOLOGIA` -- and resolved it to *five* columns against the ten the
catalog measured, so every value on those pages landed under the wrong label. The
catalog's count is now the authority and the page keeps the last good frame.

### 4. A subtotal cannot close a group that has no records

On T20 the country is printed on a band of its own and its figures on the next, so
the figures arrive as a band of eight numbers with no text -- indistinguishable
from a subtotal. 29 of them were taken for the totals of the country above, on a
table that prints **no totals at all**. `KIND_TOTAL` is exempt, because that is
the band carrying a `Totale` *label*, and it is the one printed total a table with
no groups legitimately has -- demoting it would delete MG10's only oracle.

### 5. A row of bare years is a column header, not data

DIFESA Annesso 3B prints `2022  2023  2024  2025` as a band of its own between a
label and its total, and it became four data records. The signature was exact:
the verifier reported totals off by **2022, 2023, 2024 and 2025**, one per year
column. Two or more bare years and nothing else in a band is a header.

### 6. A total names its own scope

The MEF art. 27 tables print the country **inside the first row of its run** --
`CANADA  75107  LEONARDO S.p.A.  Costi di riparazione  € …` -- while the total is
`ALBANIA Totale    € 46.691.064,55`. There is no group band to open a scope from,
so every total was read as file-level and the last country compared with the whole
table. The scope is recoverable from the data: the key is already in every row.

### 10. A signed amount was not a measurement, so its row became a group header

**Found by reading the page, and it is the subtlest of the ten.** `CURRENCY_RE` was
`^\s*€`, anchored on the symbol. The archive also prints the sign *first* --
`-€ 4.875.000,00` -- so neither that pattern nor the trailing one matched, the `€`
survived, `NUMBER_RE` refused the string, and `value_shape()` answered **`text`**
for a negative euro amount.

The consequence was not a wrong number but a **misclassified row**. `band_kind()`
reads a band as `subtotal` only when it has two or more numeric cells and no
text; with the amount counted as text, `93215 | M.E.S. S.P.A. | Importo
Transazione | -€ 4.875.000,00` fell through to `numeric == 0 and text >= 1` and
came back **`KIND_GROUP`**. The extractor then treated the whole row as a group
header spanning the table: the authorisation number, the company and the causale
were collapsed into one text cell and **the amount was never parsed**.

One row in 42. `SLOVACCHIA` came out `28.993.692,09` against a printed
`24.118.692,09` -- and `28.993.692,09 − 4.875.000,00 = 24.118.692,09`, exactly.
The sign is now captured and moved in front rather than swallowed, so a refund
does not become a payment, and `value_shape()` and `parse_number()` share one
`strip_currency()` so they cannot disagree again.

The general lesson, and the reason it is written up at this length: **`band_kind()`
reads *shapes*, not *meaning*, so anything that changes a shape silently changes
a row's role in the table.** A currency symbol in an unexpected position does not
just fail to parse -- it reclassifies the record around it. It was caught by hand,
because no automated check could distinguish "one row is a group header" from "the
group header happens to contain numbers".

### 7–9. Three defects in the verifier, each of which was reporting a correct extraction as wrong

- **A column with no numbers has no witness.** `Scadenza Autorizz.` holds *dates*
  (`17/12/2028`); treating the missing sum as `Decimal(0)` invented a mismatch.
- **A printed total equal to one of its own values is not a sum.** `%` printed 100
  against a row sum of 400; `N. autorizzazioni` printed 192 against
  152.532.836.754,81. 19 of 36 `file.grand_total` failures were this.
- **`eur_total` checked `Val. Fini Dog.` last instead of first.** A1's `EURO`
  subtotal is the group's EUR total, and a row printing the same figure in both
  columns was counted **twice**. `BAYKAR PIAGGIO` printed `687.940,05` and came
  out `519.863,79`. The rule, confirmed on three groups: a row that prints a
  `Val. Fini Dog.` figure has already been counted there and contributes nothing
  to the EUR total.

And one identity: `row.reconciliation` now holds because `band:<kind>` is counted
**after** the furniture drop and **after** the two reclassifications above. A
counter that records a band under a kind it was then reclassified away from made
nine T8x tables and three DIFESA tables report `data: 7 bands but 6 records` on
correct extractions.

### 11. A caption containing "totale" is not a total

`TOTAL_RE` fires on the **word**, and T141/T142's bottom summary line is

    Numero totale operatori per anno    138    Numero totale …    146

shapes `total_label, code, total_label, code` -- and `138`/`146` are `SHAPE_CODE`,
because a bare three-digit integer reads as an identity (the same rule that keeps
`016` out of `16`). So the band carried **no arithmetic at all** and was called a
total, putting two operator counts into the `Valore in €` columns to be compared
against 7,4 billion of euro.

`KIND_TOTAL` now requires a numeric cell. This is the conservative direction: a
genuine total row prints its figures on its own line in every table measured, and
a label printed alone is a heading, which is a truer description than a total it
has no figures for.

**It fixed the misreading but not the tables**, and the earlier claim that this
was one bug worth 8 FAILs was wrong. T141 still fails 4 and T142 3. What changed
is that the verifier now reads the **real** total -- `3.830.646.982,64` instead of
the caption's `138` -- so the remaining difference is a real one: the 18 extracted
rows carry `3.231` authorisations against a printed `2.155`, and 1,93x the printed
money. The two ratios differ (1,50x on the count, 1,93x on the value), so it is
not duplication, and **the cause is not established** -- it needs the page.

### 12. `complessivo` is not a total word on its own

The largest single win in the round, and it came from reading one row.

`TOTAL_RE` listed `complessivo` as a standalone alternative. In 2025 that fired
on three rows that **carry money**, and every one of them is ordinary prose:

| row | text | what it is |
|---|---|---|
| MAE `A1` 9549 | `USD 26/09/2025 NUMERO COMPLESSIVO STAFFA SUPPORTO TUBO P/N2WBH01227-0003` | a **data row** |
| MAE `A4` 1421 | `USD JSF 26/09/2025 NUMERO COMPLESSIVO STAFFA SUPPORTO TUBO` | a **data row** |
| DIFESA `3C` 52 | `* Valore riferito al volume complessivo delle n. 107 nuove istanze …` | a **footnote** |

"NUMERO COMPLESSIVO STAFFA" is a *part name* -- a complete-number bracket, and
the one A1's USD contracts are for. Reading those as totals meant a data row's
money became a printed figure to reconcile against, and a footnote became one
too.

The word is now honoured only where `totale` heads it, which `totale` already
matches. **Measured: of 66 cells containing `complessivo`, 64 are legitimate
`Totale complessivo` rows across DOG, MAE and MEF, and 2 were the false ones
above.** Removing it took the year from 50 FAILs to 44, and killed DIFESA `3C`
entirely.

The lesson generalises past this word: **a keyword list is a claim about the
corpus, and the cheapest way to audit one is to grep the extracted rows for it
and read every hit.** 66 hits is a list a person can read; it took two minutes
and found a defect no test had.

### 13. A group may print several subtotals, and each certifies its own segment

A subtotal does not certify the whole scope -- it certifies **the rows since the
previous one**. A1 p306-309 shows why: `O.M.A. OFFICINE MECCANICHE AERONAUTICHE
S.P.A.` opens a scope, prints a subtotal, prints its *name again* on p307 and
prints a second one. Four subtotals in all, four segments.

The verifier kept the **last** subtotal per column and compared the whole scope
against it, so a four-page scope was measured against one segment's figure:

    group 9492 · EURO: assembled 2.281.080,80 vs printed 789.718,24

`789.718,24` is the last of four printed figures; the first was `1.691.118,52`.
The rows were right and the comparison was comparing one segment against all four.
`group_totals()` now returns the subtotals **in reading order with their
`row_ord`**, and `check_group_totals()` cuts the scope at each. Three of 9492's
four segments reconcile exactly, which is what makes the fourth a real finding
rather than a bad rule.

Rows **after** the last subtotal get a new `group.uncertified` WARN rather than
being added to any figure: nothing in the table vouches for them.

### 14. A FAIL must report the reading it stands on

With two candidate readings -- the column alone, and the mixed-currency rule --
the deltas differ by orders of magnitude, and the verifier reported whichever it
tried first. A1 group 384 read

    group 384 · EURO: assembled 6.099.294,06 vs printed 32.212.217,15
                (delta 26112923.09)

while the mixed-currency rule puts it out by **0,02**. Those are the same defect,
and only one of them is worth a page. A FAIL now reports the **closer** reading
and names which rule produced it. This changed no verdict and was still worth
doing: the triage before it was reading six figures wrong by a factor of a
million.

### 15. The rounding rule: `ROUNDED`, and why it is a bound and not a band

A1's `EURO` column is a **conversion**. Each row's figure is a foreign amount
restated at some rate and rounded to the cent for print; the report's subtotal
does not come from summing those rounded figures. So an exact-equality rule on
that column is checking something the source never promised.

The evidence that this is the source's arithmetic and not a misread:

| | groups reconciling exactly | groups not |
|---|---|---|
| count | 149 of 159 | 10 |
| mean rows per group | **60** | **257** |

and the deltas are **mixed-sign** (+0,02 and −0,02 both occur), holding at ±0,02
even across a **1,069-row** group. A bad parse would be one sign and proportional
to one bad cell. A random walk over a thousand roundings stays at two cents; that
is textbook. **No raw-currency column ever fails** -- `Ammontare` reconciles
exactly everywhere -- which is the control that makes the reading safe.

So the rule, as decided:

- **raw columns** (`Valuta`, `Ammontare`) keep **exact equality**. A cent there is
  a FAIL, because nothing can explain a cent in a figure that was never converted.
- **derived columns** (`EURO`, `Val. Fini Dog.`) are compared against
  `rounding_bound(n) = n × 0,005` -- the most `n` per-row roundings can be out.
  That is a **guarantee, not a guess**: each printed figure carries at most half a
  cent, so the error cannot exceed `n/2` cents whatever the values are. (The
  *expected* size is far smaller, `0,0029 × sqrt(n)`, which is why the measured
  residuals are a cent or two even where `n` is in the hundreds.)
- inside the bound the verdict is **`ROUNDED`**, not PASS and not FAIL, and the
  finding prints the delta **and** the bound that admitted it. Outside it, FAIL.

11 findings are `ROUNDED`. The rule admitted exactly the 11 derived-column cents
and left the two non-derived ones standing: T141's `%` (100,01 vs 100) and T22's
`Valore in €`. Those are separate findings, not rounding.

The flat alternative -- accept anything within ±0,02 anywhere -- was rejected
because it would equally silence a genuine one-cent parse error in a three-row
group, and because it throws away the row-count evidence that is what
distinguishes rounding from a misread.

### What the 32 remaining FAILs are

All 32 are `group.subtotal` or `file.grand_total`, across **15 tables**, and
every one is a plain arithmetic mismatch -- none is a bookkeeping or
classification failure any more. **78 of the 93 extracted tables verify with zero
FAIL.**

MAE `A1` is down to **one** FAIL from 17. What the round established about it:

- **138 of its 159 `EURO` subtotals reconcile exactly**, and 11 more are
  `ROUNDED`, so **149 of 159** are accounted for and only **one** is unexplained:
  group 4731, `-12.304,98`.
- The earlier "hundreds of millions" failures were an artefact of comparing the
  raw column instead of the mixed-currency rule, not of bad rows.
- A1 prints **no file-level total**, so there is no second witness at file level.

The rest are **undiagnosed**, and the two biggest families are visibly different
problems:

| count | table | reading |
|---|---|---|
| 6 | MAE `T20` | country rows against printed totals; a two-line-per-row table with a stacked header. `N. Aut.` is out by 10 and 12 -- **counts**, so this is rows in or out, not rounding |
| 4 | MAE `T141` | rows carry **1,50x** the printed authorisation count and **1,93x** the printed value. The ratios differ, so it is not duplication |
| 3 each | `T1`, `T12`, `T142`, `N1` | mixed; `N1` group 155 fails on three columns at once, one of them a **count** (`Spedizioni`, 30.708) |
| 2 | MAE `T21` | `Q.tà` out by 1.789 and `Valore in Euro` out by 3,07bn |
| 1 each | `B7` (+0,03), `N2`, `O2`, `P1`, `P2`, `A1`, `DOG `M1`` | `file.grand_total`; `P1` and `P2` fail identically, which suggests a shared cause |

`P1` and `P2` carrying the **same** delta (`70.250.043,33`) is the cheapest lead
in the list -- two tables failing the same way usually share one defect.

### Two dump bugs, and the check that now owns them

Both were found by **loading the finished dump**, and neither was visible to
anything else: the file was written, the `.gz` was produced, and the row counter
reported a correct 97,017.

1. **Every row carried its own `;`.** The terminator belongs to the statement,
   not the row, so the output read `);,` at every boundary and sqlite refused it
   at line 293 with `near ",": syntax error`. Nothing that counts rows can catch a
   syntax error.
2. **A value containing `;` ended the statement early.** `column_def.samples`
   holds JSON like `["… TESTO A CASO;", "…"]`, so hand-quoting truncated the row.
   Strings are now quoted by handing them to the driver's own `quote()`, which is
   the same code the driver uses to build the SQL it runs.

`check_dump()` runs on every `--dump` and refuses to return a dump that does not
load into an empty database with the row counts it claims. It costs one parse of
a file just written — seconds against a 150 s run — and it is the difference
between shipping a dump and shipping a file that looks like one.

**sqlite's `quote()` truncates at a NUL** rather than escaping it, because the C
API takes a length-terminated string, and silently writing half a value is the one
thing a lossless store must never do. No value in the archive contains one —
measured over all 94,358 cells and the whole catalog — so the writer refuses the
character and says so rather than letting it fire silently on a future year.

### Measured output

2025, opt-in `--dump`: **13.1 MB plain, 1.1 MB gzipped (8%)**, 97,017 rows across
four tables, loaded back and compared. The size is far below the 300–600 MB this
section's plan predicted, because the archive is 94k cells rather than the
millions the "537-page A1" figure implied — A1 is 537 pages of *wrapped*
description text, most of which is one cell per row.

---

## 10. Outstanding

Ordered by what unblocks the most, which is not the same as what is easiest.

- **The 32 FAILs, table by table.** All plain arithmetic, 15 tables, none
  diagnosed. Two leads are the cheapest:
  - **`P1` and `P2` fail with the identical delta** (`70.250.043,33`). Two tables
    failing the same way almost always share one defect, and either one renders
    the cause.
  - **`T20`'s `N. Aut.` is out by 10 and 12.** A *count* cannot be rounding, so
    this is rows in or out of scope, and the same for `N1` group 155's
    `Spedizioni` (30.708) and `T21`'s `Q.tà` (1.789). Counts localise better than
    money: a count of the wrong size names a handful of rows, not a sum.
  - **`T141`/`T142`**: rows carry 1,50x the printed authorisation count and 1,93x
    the printed value. The ratios differ, so it is not duplication. Needs a page.
  - **A1 group 4731** (`-12.304,98`) is the last one in the largest table, and
    A1's `EUR` rule is validated on 149 groups, so the rule is not the suspect.
- **Phase 2 — the diff.** Built and queryable, but it has nothing to compare
  against. Needs a second catalogued year, and **step 1 for 2024 is still broken**
  (10 of 31 tables detected in vol I, five phantoms in vol II), so 2024 is where
  the diff earns its keep. The witness must ride along on every presence cell or
  it will report tables disappearing that were never legible.
- **`ROUNDED` has never been exercised by a defect.** The rule is validated on 11
  real residuals and on the two cents it correctly refused, but nothing yet proves
  it would catch a genuine one-cent error in a large derived column -- the bound is
  `n/2` cents, so at `n = 1.069` it admits ±5,35. If that looseness ever matters,
  the tightening is the statistical bound (`0,0029 × sqrt(n)`), not a smaller
  constant.
- **`INLINE_CODE` and `LEADING_CODE` still cannot match a two-digit code**
  (`MT13`). Ten 2025 tables are invisible to step 1 because of it. `\d{0,2}` plus a
  negative lookahead is the fix; `VERIFIER_INLINE` in `VerifyTables.py` already
  has it. **Deliberately untouched** so the two never drift apart silently.
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
- **Phase 5 — the tool bake-off.** Not started. Benchmark set with hand-transcribed
  ground truth: A1 p1 (rotated, ruled, nested), `EE` p1 (repeated sub-dimension),
  `MG10` p2 (flat borderless + printed total), Annesso 4 p1 (wrapped lists), one
  garbled page (expected: nothing). Metrics: row/col precision and recall, cell
  exact-match, ms/page, determinism, dependency footprint. PyMuPDF is the current
  reading layer; pdfplumber, Camelot and tabula-py are to be measured as
  baselines. *Prior, to be checked rather than assumed:* tabula has no rotation
  normalisation and cannot express a group header, so it is expected to fail on
  the 48 rotated tables; it may earn a role on the flat ruled minority.
