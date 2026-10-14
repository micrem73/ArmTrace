# ROADMAP — from per-table PDFs to a queryable SQL archive

The order the work is done in, and why this order. It is a *plan*: what is
decided, what is built, and what is left. What has actually been built is
recorded too, so the document does not drift into a wish list.

Scope and authority: [AGENTS.md](./AGENTS.md) owns finding tables and
[AGENTS-TABLE2SQL.md](./AGENTS-TABLE2SQL.md) owns reading them. Neither is
restated here. Three things are shared by all three documents and belong to
AGENTS.md alone: the manifest key is the triple `(authority, article, code)`, the
article level in the path is optional, and OCR is out of scope.

---

## 1. The problem this ordering solves

The archive's tables are **not stable across years and not uniform within one**.
On 2025: 110 tables, of which **48** are rotated on a majority of their pages, and
**57** carry a rotated table under an upright banner — overlapping measures, not
additive, because 20 tables are mixed while upright overall and a further 17 are
rotated and pure. 4 are percentage charts rather than grids, and four different row
grains appear in the same year — MAE groups rows under an operator and closes each
group with a printed subtotal, MEF repeats a `Causale` sub-dimension inside one
authorisation number, the Dogane print one flat record per operator, the Difesa
print cells holding lists wrapped over twenty lines.

A loader written against one year's layout therefore loads one year. Worse, a
CSV-per-table intermediate encodes all four grains as the same rectangle grid, so
diffing those CSVs year over year reports a change in **every column of every
table** — the comparison carries no information at all.

So the shape of a table has to be **measured** before anything is loaded. That is
Phase 1, and it is not a preliminary to the real work; it is the thing that makes
the real work possible.

## 2. Decisions taken, and what closed them

| decision | choice | why |
|---|---|---|
| output architecture | **hybrid**: one lossless EAV plus curated projections | nothing is ever lost; an unmapped family is still queryable and re-projectable without re-parsing PDFs. Pure per-(code, year) tables are faithful but need `UNION ALL` for every cross-year question; a canonical-only model leaves an unmapped year nowhere to land |
| what a `.sql` file is | **deterministic dump**; sqlite for the dev loop | fixed table and row order, explicit column lists, so two runs diff cleanly. A BOM in a `.sql` file is a syntax error — the opposite of the old CSV default |
| study window | **2025 only first**, widen after | 2025 is the richest year and already tagged clean. A diff needs ≥2 years, and the second one must be real |
| reading layer | **PyMuPDF**, pure Python, no JRE | it is the only candidate measured working on rotated pages; tabula has no rotation normalisation at all |
| OCR | **out of scope**, ceiling reported per table | ~1300 pages have destroyed text. Out of scope by decision, not by accident — and the ceiling is stated rather than hidden |
| the old step 2 | **deleted**, not repaired | it could not rotate, could not see the rules, and had no record grammar. See AGENTS-TABLE2SQL.md §1 |

## 3. Where the work stands

### Phase 0 — substrate · **done**

2025's step-1 output is generated (110 per-table PDFs + manifest). Dependencies
settled: `pymupdf` in, `tabula-py` out, no JRE anywhere in the pipeline.

### Phase 1 — the Catalog · **done for 2025**

`CatalogueTables.py` → `Out/CATALOG/{catalog.sqlite, catalog-<year>.jsonl,
census-<year>.md}`. Per `(authority, article, code, year)`: the column signature
with labels kept verbatim **and** a comparable key, the record grammar with the
band counts it was decided from, per-page legibility over the whole file, whether
the page has vector rules or is a chart, and the witness for the table's
existence.

110 tables in **141 s**, so a full year costs less than one verifier run and the
one-year-at-a-time loop holds.

It recovers the columns the reports actually print — A1's ten, EE's five, the
Dogane's MG10–18 `Denominazione operatore | Quantità | Valore (Euro)`, MEF's art.
27 pair, Annesso 4's four. Where it says `unknown`, that is recorded with a
reason rather than guessed at.

### Phase 2 — the evolution diff · **built, not yet run**

The engine is in place (`table_year`, `column_def`, `page_stat` are rows, so the
diff is a SQL question rather than a JSON comparison). It produces nothing useful
yet because it has one year.

What it must answer: what changed in the same table, what disappeared, what
appeared, and how the data inside changed. Specifically — presence matrix with a
witness beside every cell; lineage scoring (title similarity + column-signature
Jaccard + role vector) classifying `same / renamed / split / merged / new / gone /
uncertain`; column-level add/drop/rename/reorder/unit-change; granularity changes,
where a new `country` dimension on an operator table is a schema migration;
additivity audit; page and row drift.

Output `evolution-<from>-<to>.{md,html}` with the HTML clickable to the PDFs, and
a **written baseline** before any fix, per AGENTS.md §1a.

**Blocked on step 1 for 2024**, which is still broken: 10 of 31 tables detected in
vol I, five phantoms in vol II. A diff against a broken year reports tables
vanishing that were never legible, so 2024 must be repaired before it is worth
diffing.

### Phase 3 — the SQL target · **schema decided, not built**

```sql
-- lossless: every extracted cell of every table-year, always
CREATE TABLE stg_cell (
  authority TEXT, article TEXT, code TEXT, year INTEGER,
  page INTEGER, row_ord INTEGER, col_ord INTEGER,
  row_kind TEXT,        -- data | group_header | subtotal | grand_total | continuation
  col_key TEXT,         -- label path as printed, e.g. 'Immagini/Anno 2025/Valore in €'
  raw_text TEXT, num_value DECIMAL(20,4), text_value TEXT,
  unit TEXT, is_total INTEGER
);

-- curated, built from the lineage map
CREATE TABLE mae_authorization (   -- grain: one authorisation
  year, operator_id, currency, amount DECIMAL(18,2), amount_eur DECIMAL(18,2),
  customs_value DECIMAL(18,2), date DATE, code TEXT, table_code TEXT);
CREATE TABLE mae_material_line (   -- grain: one material item under an authorisation
  year, authorization_id, qty NUMERIC, unit_of_measure, description TEXT,
  tipo TEXT, category TEXT);
CREATE TABLE mef_credit_tx (       -- grain: one causale under a credit-institution authorisation
  year, authorization_no TEXT, end_user TEXT, causale TEXT,
  reported_amount DECIMAL(18,2), accessory_amount DECIMAL(18,2));
CREATE TABLE dog_operator_total (  -- grain: operator × operation type
  year, operator_id, operation_type TEXT, qty NUMERIC, value_eur DECIMAL(18,2));
CREATE TABLE difesa_program ( ... );
-- + dimensions: authority, operator, country, credit_institution, material_category
```

Rules: `DECIMAL` never `FLOAT` — `1.234,56` must not become 1.234; units live in
`column_def`, never in a value; canonical columns in `snake_case` with the printed
Italian kept in `column_def.label_it`, because the downstream chatbot asks in
Italian but stable identifiers matter more; **null and zero are different facts**
and the archive prints both, so the policy is recorded per column, never inferred
per cell; `016` is the Military List category, not the number 16.

### Phase 4 — the extractor · **built, two grammars, baseline written**

`ExtractTables.py --year 2025` → `stg_cell` in `catalog.sqlite`, and with
`--dump` → `Out/SQL/armtrace-<year>.sql[.gz]`. Record assembly per grammar
rather than per code: a `group` opens a scope, a repeated name continues it
across pages, a `continuation` folds into the record above, a `subtotal` closes
the scope and is flagged `is_total` so it is never double-counted.

2025: **93 of 150 tables, 94,358 cells, 162 s**, parallel over tables with the
parent sorting by natural key so output is identical whatever order workers
finish in. Four structural bugs found and fixed — the margin stamp becoming a
record, a total band's label landing in a measure column, material rows read as
group headers, and a page-spanning group split into one group per page. All four
are measured in AGENTS-TABLE2SQL.md §9.

### Phase 4b — verification · **built, baseline written, FAIL not yet zero**

`VerifyExtraction.py --year 2025` → `Out/VERIFY/exverify-2025.{log,json,html,md}`.
The oracle is the printed total, per group where the table prints one and per
file where it prints one, compared **exactly** with no tolerance. Every sum is
done in Python on `Decimal`, never in SQL: sqlite has no exact decimal and a
`DECIMAL` column has NUMERIC affinity.

**Baseline: 879 PASS, 166 FAIL, 53 WARN, 573 SKIP.**

**After the FAIL round: 1068 PASS, 11 ROUNDED, 32 FAIL, 51 WARN, 1171 SKIP** — and
**76 of the 93 tables verify with zero FAIL**, up from 48. The count did not fall
monotonically: it reached 62 as defects were fixed, rose to 92 when a new check
was added that found 30 findings nothing had been looking for, and settled at 43
once those turned out to include a real extraction defect rather than a bad check.
Three more real defects took it to 32: the standalone `complessivo` in `TOTAL_RE`
firing on A1's *part name* and a DIFESA footnote, a group's several subtotals each
certifying only its own segment, and a FAIL reporting the worse of two candidate
readings. The last 11 were cents in a **converted-currency** column, which the
report rounds per row -- reported as a new `ROUNDED` verdict carrying the delta and
the `n × 0,005` bound that admitted it, with raw columns still exact.

**Ten root causes, four in the extractor and six in the verifier** — the oracle was
wrong more often than the extraction was. The two worst are both silent:

- `parse_number('192 615.457.414,48') -> 192615457414.48`, two numbers in one
  cell concatenated into one plausible `Decimal`, in 137 cells;
- `-€ 4.875.000,00` was not recognised as a number at all, so `band_kind()` read
  the **row** as a group header and its amount was never parsed. Found by reading
  a rendered page, not by any automated check.

Full list and measurements in AGENTS-TABLE2SQL.md §9.

**Still not FAIL at zero.** All 32 remaining are plain arithmetic mismatches across
15 tables; **78 of the 93 extracted tables verify with zero FAIL.** MAE `A1` is down
to **one**, and the "hundreds of millions" failures that dominated it turned out to
be an artefact of the verifier comparing the wrong candidate reading rather than bad
rows. The rest are **undiagnosed**, and two leads are cheap: `P1` and `P2` fail with
the identical delta, and `T20`'s `N. Aut.` is out by 10 and 12 — a *count*, which
localises to a handful of rows where a money delta does not.

### Phase 5 — the tool bake-off · **not started**

A fixed benchmark set with **hand-transcribed ground truth**, because a comparison
without ground truth is a comparison of opinions:

| page | grammar | the hard part |
|---|---|---|
| A1 p1 | group+subtotal | rotated, ruled, nested, wrapped cells |
| `EE` p1 | repeated-subdimension | one key over several causale rows |
| `MG10` p2 | flat | borderless, printed grand total |
| Annesso 4 p1 | wrapped-list | lists over twenty lines |
| one garbled page | — | expected: nothing |

Metrics: row/col precision and recall against the ground truth, cell exact-match,
ms/page, determinism, dependency footprint. PyMuPDF is the incumbent;
pdfplumber, Camelot and tabula-py are baselines. *Prior to be checked rather than
assumed:* tabula has no rotation normalisation and no concept of a group header,
so it is expected to fail on the rotated tables and may only earn a role on the
flat ruled minority.

---

## 4. The coverage ceiling, stated rather than hidden

A table with a destroyed text layer splits correctly, appears in the catalog with
`grammar=unknown` and a reason, and yields **zero rows**. On 2025 that is 19
tables and 498 pages — the whole of `E` (221pp) and `C1` (97pp) among them — plus
2001–2010, which has no text layer at all.

This is not a defect and must never be coloured as one. The consequence to respect:
**no coverage claim about the archive is honest unless it states the extractable
fraction alongside the total.** If garbled tables are ever to yield data, OCR is
the only route, and that is a separate decision with its own scope.

## 5. The order of work, and why it is this order

```
Phase 1  catalog 2025            done
    ↓
Phase 4  extractor + 4b verify   built, 2 of 6 grammars, baseline written
    ↓
step 1 for 2024                  the second year must be real, or the diff lies
    ↓
catalog 2024
    ↓
Phase 2  the diff                 what changed, what appeared, what vanished
    ↓
Phase 3  canonical schema         designed *by* the diff, not before it
    ↓
Phase 5  bake-off                 would have informed Phase 1; runs late because
                                   the ground truth is the expensive part

Three of these are counter-intuitive and worth stating:

- **Phase 4 moved ahead of Phase 2, which reverses the order this document
  originally gave.** The reason is that the extractor is what *proves* the
  catalog's column geometry, and the catalog was written believing a header's
  label extents were its column edges. They are not, and nothing short of a
  printed total to compare against would have said so: A1's money columns are
  right-aligned and sit outside their labels, so every narrow value overlapped
  the wrong one and `EURO`'s subtotal read as `assembled 0 vs printed 5.360.673,71`
  on a table that is correct. The diff would have been computed over a frame that
  was already wrong. So the oracle came first, and it found the frame wrong. This
  is the plan's own rule — the canonical schema is designed *by* the diff — read
  one step earlier: **the column geometry is designed by the extraction, not
  before it.**

- **The canonical schema is designed after the diff, not before.** Phase 3's tables
  above are *shapes inferred from reading 2025's pages*. If the diff shows a table
  changing grain in 2023, the schema must reflect that, and a schema written first
  would encode one year's assumptions as if they were the ontology.
- **The bake-off runs last, though it would have informed Phase 1.** Choosing a
  reader before writing ground truth is how a tool gets chosen for its
  documentation. It is late because hand-transcribing five pages is the expensive
  part, and doing it after the catalog exists means the benchmark can be chosen to
  cover the grammars the catalog actually found — including any it found that the
  plan did not predict.

## 6. Open questions that are not settled

- **2024's repair.** Step 1 detects 10 of 31 tables in vol I and manufactures five
  phantoms in vol II. Named in AGENTS.md §Outstanding; it is also the year the
  README uses as its worked example, so it is the first thing a reader will run.
- **`H1`'s 22 legible pages of 32.** Labels destroyed, digits intact. Worth OCR, or
  recorded as part of the ceiling?
- **DIFESA Annesso 2** is a company list with no header band, catalogued `unknown`
  at 5 legible pages. It may want a **new grammar** rather than a better header
  detector.
- **The weak cluster.** 15 of 28 volumes yield ≤10 tables, and 2014 vol I / 2015
  yield 1. They need either a family-specific detector or an honest
  partial-coverage marker — not a 1-table split presented as an answer.
- **Beyond the archive.** The product roadmap in README's ToDo — MySQL, natural
  language to SQL, the chatbot — all of it downstream of Phases 2–4, and none of it
  worth building until the data underneath is what the reports actually say.