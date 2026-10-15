# ROADMAP — from per-table PDFs to a queryable SQL archive

The order the work is done in, and why this order. It is a *plan*: what is
decided, what is built, and what is left. What has actually been built is
recorded too, so the document does not drift into a wish list.

Scope and authority: [AGENTS.md](./AGENTS.md) owns finding tables and
[READING-TABLES.md](./READING-TABLES.md) owns reading them. Neither is
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
| the old step 2 | **deleted**, not repaired | it could not rotate, could not see the rules, and had no record grammar. See READING-TABLES.md §1 |

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
are measured in READING-TABLES.md §9.

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

Full list and measurements in READING-TABLES.md §9.

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
  phantoms in vol II. Measured at §8, backlogged at §11; it is also the year the
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

---

# The measured record

## 7. Baselines

Written before the first fix, updated when the year closes. `FAIL` is the defect; `WARN` is
usually structural and is accounted for, not chased; `SKIP` means no witness.

| year | files | pages | status |
|---|---|---|---|
| **2025** | **150** | **1982** | tagged **`2025-clean`** — judged clean; 142 ok, 0 FAIL, 8 warn |
| 2016–2023 | | | not started |
| 2024 | | | **baseline re-measured 2026-10-11, failing** — §8 |

**The 2024 baseline, re-measured 2026-10-11** before the first fix, from
`Reports2PDFTables_2024.py` (a fresh copy of the template) on `--dry-run`: **98 tables,
41 + 57** — the 31 + 35 of the 2026-10-05 measurement in §8, plus the 31 relation
exhibits vol. I carries. The two are not comparable and must not be added together; §8
has both. Already right at this baseline: `stitch()` does **not** fire (`continued`
empty in both volumes, as §8 requires), vol. I's family is 2, and vol. II's 35 MEF tables
are exactly the 35 codes its bookmark tree names.

**Three tags, three claims.** `2025-baseline` (`b0bbbf2`, 2026-10-06) marks the FAIL-free baseline to iterate from — 110 files, *not yet judged clean*, which is what it always said. `2025-verified` (`5d91d35`) marks what the machine measured. `2025-clean` (`31be7d7`, 2026-10-11) marks the user's judgement, and it is the only one that claims the year is clean. Between the first and last the manifest grew from 110 files to 150 and the FAILs went 31 → 0; §11 and the tag body carry the detail.

**The 2025 row was 119 files / 1952 pages until 2026-10-11**, moving when the relation
exhibits (`T1`…`T23`, `T81`…`T88`, `T141`, `T142`) were added as a fourth code space. A
baseline not re-measured after a feature lands describes a version nobody has any more. The
old 8 WARNs on 119 files were unfixable by construction — 5 × `stamp_total` (digits in a
broken font, so the total cannot be *read*; that it is constant across the file is checked,
and holds), 2 × `edges.unreadable_pages`, 1 × `own_code_absent` on a bookmark-attested file.
`own_code_ratio` was removed: a table printing its code on its first page and a plain running
header after that is the ordinary shape of a real table.
## 8. 2024: measured, and wrong

The README's worked example, never run until 2026. Ground truth is **31 + 35 = 66**; step 1
produces 10 + 35 and manufactures five phantoms — wrong in both directions at once.

| | vol. I (1016pp) | vol. II (1260pp) |
|---|---|---|
| family | 2 ✓ | **3 ✗** — it is family 1 |
| detected | **10** | 35 real + **5 phantom** `O1 M2 N2 O2 P2` |
| why | 335 of 1016pp garbled; `B5` lost to the wrapped-row trap | the Dogane annex read as family-2 codes |

Two regression assertions: **no 2024 table straddles the volume join** (tables stop at
printed 1000, folios run continuously), so `stitch()` must not fire; and **vol. I has only
three `Pagina vuota` bookmarks**, so its header traps carry the whole detection.

## 9. Archive coverage

Two counts exist because the key changed; **do not compare them.** The old code-only layout
was verified across 28 text-bearing volumes at **479 tables, zero failures**. The current
triple key gives **610 tables over the 22 in-scope volumes (2016+), zero UNPLACED** — the
same tables plus the Dogane and Difesa series the old key could not separate. "Zero
failures" is scoped to what it was measured on.

| year | volumes | family | tables |
|---|---|---|---|
| 2012–2015 | III·DOC·II·I / I·II | 1·2·3 | 1·6·16, 15, 3·1, 1·1 — deferred |
| 2016 | II / I | 3 / 2 | 34 / 5 — baseline; vol. I weak |
| 2017 | II / I | 2 | 8 / 5 — weak |
| 2018 | I / II | 2 | 22 / 8 |
| 2019 | I / II | 2 / 3 | 23 / 10 |
| 2020 | II / I | 3 / 2 | 41 / 10 |
| 2021 | TOMO_I / TOMO_II / VOL_II | 2 | 31 / 27 / 8 |
| 2022 | II / I | 3 / 2 | 40 / 23 |
| 2023 | II / I / III | 1 / 2 / 2 | 35 / 31 / 5 |
| **2024** | II / I | 3✗ / 2 | 40 / 10 — **failing**, §8 |
| 2025 | II / I | 3 / 2 | **150** |

**Weak files (≤10 tables): 15 of 28** — 2012–2017 is the cluster; 2024 vol. I also lands
at 10 but is *broken*, not weak. Deferred years are on disk with known state: 2001–2010 no
text layer, 2009/2011 from the SIPRI mirror, 2012–2013 no parseable page number.
**Bookmarks exist for exactly 6 volumes** (197 codes); four more have outlines but only
`"Pagina vuota"` and container entries.

## 10. The audit that found (2025)

`VerifyTables.py` over the 81 then-exported tables gave **55 PASS, 8 FAIL, 18 WARN** —
`MG8` holding ten tables, `M` holding `TAB N`, `O` holding `TAB P`, `MT7` holding
`TAB MT13`, `P`/`N` as prose, `E`/`F1` with a foreign trailing page. Two were stale
output written hours before the trim fix, two since closed by the ministry tree. What it
leaves behind is the shape of the method: an independent witness per claim, FAIL at zero.
## 11. Operational backlog

Defects and gaps in the pipeline, distinct from the open questions in §6.

- **2016 vol. II leaves 358 of 768 pages unattributed, and they are tables.** The block prints `TAB. M` on 278 of them; the header scan detects only the *suffixed* series and never the four bare codes, so the main art. 1 comma 2 allegato is absent from the output while the summary tables beside it are exported. The bare series *is* found in 2025 and merely misread in 2024, so the codes are not the obstacle — 2016 loses them because nothing else witnesses them. **Needs an engine change** — a new header style for `TAB. M` with no suffix, gated against prose — and a re-run of every year in scope (AGENTS.md §2).
- **2024 is measured and failing — fix it before extending anything.** §8. **Needs an engine change** in `detect_family()`, and a re-run of every year in scope. Likewise 2015 and 2014 vol. I should join 2001–2010 in an explicit *unsupported* list rather than emitting a misleading 1-table split.
- **The trailing-page check only runs where the stamp is legible** — 2019 vol. II, 2023 vol. III and the family-3 volumes carry none, so nothing is checked there and they are exported differently. They need a second kind of witness.
- **Recover the missing page numbers in the 2016 and 2018 indices.** Their ministry rows carry none, so validation rejects the index and the volume falls back to page furniture. The numbers are on the same page.
- **Explain the `MEF/II` key collisions** in 2020 vol. II and 2024 vol. 2: two detections landed on one triple and one was dropped. Genuine duplicate or placement error; undecided.
- **Enumerate the 2024 vol. II Dogane annex codes** — sampled every 15th page, not yet enumerated. And resolve the 2021 volume-2 filename collision: `TOMO_II.pdf` and `VOLUME_II.pdf` are different documents (844 and 1070pp) claiming the same volume, so `parse_volume()` refuses to guess.
- **Subset-font repair: measure 2024 vol. I (phase A step 0).** The plan quotes 98.0% on 2025; 2024 vol. I is a different subset family. The digest-level reading stands: a **volume-local** codebook resolves **70.7%** of the garbled slots, and re-measured independently at **73.2%**; the **42-volume corpus** resolves **309 of 324** distinct target digests, and the curve **saturates at 2018–2021**. Re-measured end-to-end over all 1016 pages, decoding charstring digests and substituting per `visitor_text`: **330 garbled pages, residual median 0.00%, p90 0.00%, max 0.38%, 18 unresolved characters out of 548,873 (99.9967% resolved), 0 of 330 pages over the 5% stop-gate.** No-op invariant held twice, at **651/651** and **655/655** pages carrying no subset font, byte-identical to pypdf. Two corrections to the digest-level reading, both of which change the implementation. **(1) `space` is 53 of the 64 slots a corpus codebook cannot name, and it is not furniture to leave alone.** It is resolvable — from the garbled font's *own AGL glyph name*, never by outline, which is precisely the cross-face outline collision the plan warns of and the reason it was written off. Missing spaces are thousands separators (`1 234 567,89`), so they fall *inside numbers*, where they cost the most. The rule neither the plan nor the first reading states: **a slot whose own name is in AGL is taken from the name; the outline is consulted only for `uniE0xx`.** That single change took residual from **2.42% to 0.00%** median. What remains is **11 genuinely unnameable slots** — independently the same 11 the digest-level reading found, which is the convergence worth keeping. **(2) `is_garbled()` under-fires, so the plan's framing understates the win.** 31 pages the decoder substantially repairs are reported clean — p612 carries 128 PUA against 1342 alnum, 9.5%, under the 15% threshold, because the ratio is diluted by the page's legible furniture. Amendment 1's `WARN decode untrusted` guard must key on measured residual, never on `is_garbled`, and the affected population is larger than §8 assumed. On the corpus: **a four-volume sample is not enough**, and fails in a way a saturation curve cannot show — it lacks `MicrosoftSansSerif`, which is ~76% of 2024 vol. I's unresolved characters and appears *only* as PUA subsets there, while readable exemplars sit in 2018 vol. I, 2021 tomo I/II and 2022 vol. I. The 42-volume corpus already has that key. So the harvest must be corpus-wide and must record which faces it covered; "the curve saturated" is not the test. Not measured here: 2025 under this codebook, the `ciphered` class, and whether the name rule holds on other garbled years. Plan: `subset-font-repair.md`; amendments and order: `font-repair-phase-a.md`.

### Closed on 2026-10-11

- **`TRASH` wrote nothing for 2025 vol. I.** `trash_reason()` took `tables` and never used it, and looked the key up through a `by_path` that stopped existing when the ministry tree rekeyed `tables` from paths to the `(authority, article, code)` triple. Every volume whose trash reached that last witness raised `NameError`; vol. I was the only one to reach it, because vol. II's runs are all claimed earlier in the chain. The rewrite that fixed the adjacency test left its prose and not its lookup — the same shape as the `ELENCO_CODE` bug, and the reason the rule in AGENTS.md §3 exists. Fixed by rebuilding the display string from `tables`; verified on the real manifest and on the real write path (16 files, accounting identical to the pre-fix log).
- **31 of 2025's files FAILed `uniqueness.own_code_absent`.** Every relation exhibit, absent from the INDICE *and* every bookmark tree, so the manifest was their only witness — an artefact of verifying without `--manifest`, not a defect. With it: **142 ok, 0 FAIL, 8 warn**, and `provenienza` runs for the first time (1982/2096 attributed, 0 duplicates, 117 unattributed).
- **The 11 `no-detector` runs on vol. I** are narrative under a relation exhibit (p26) and two charts (p57), not undetected tables. Trashing them is correct.
