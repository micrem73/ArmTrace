# ROADMAP — from per-table PDFs to a queryable SQL archive

The order the work is done in, and why. What is decided, what is built, what is left.
[AGENTS.md](./AGENTS.md) owns finding tables, [READING-TABLES.md](./READING-TABLES.md)
owns reading them; neither is restated here. AGENTS.md is the operating contract; this file
holds the *plan* (§1–§4) and the *measurements* (§5–§9), because a measurement is not a
rule — it goes stale on its own schedule and belongs with the state of the work.

**The problem this ordering solves.** The archive's tables are neither stable across years
nor uniform within one, so a schema designed before the second year is catalogued encodes
one year's assumptions as if they were the ontology, and every later table either fails to
fit or gets forced. Hence: catalog first, diff second, schema by the diff.## 1. Where the work stands

| phase | what | status |
|---|---|---|
| 0 substrate | `lib/`, manifest, ministry tree | **done** |
| 1 catalog | shapes, grammars, legibility | **done for 2025** (110 tables, 19 unreadable) |
| 2 evolution diff | what changed, appeared, vanished | **built, not yet run** — needs a second catalogued year |
| 3 canonical schema | EAV plus curated projections | **schema decided, not built** |
| 4 extractor | record assembly, typed values, `.sql` | **not started** |
| 4b verification | against the printed totals | **not started** |
| 5 tool bake-off | PyMuPDF vs pdfplumber/Camelot/tabula | **not started** |

Decisions already closed: `IndividualTables2SQL.py` is deleted in favour of
`CatalogueTables.py` (READING-TABLES.md §1), and **OCR is out of scope by decision**, so the
dead pages in §3 are a ceiling to state, not a gap to engineer around.
**Schema (Phase 3), decided not built.** One lossless EAV — `stg_cell(authority, article,
code, year, page, row_ord, col_ord, row_kind, col_key, raw_text, num_value, text_value,
unit, is_total)` — plus curated projections from a lineage map, so nothing is lost and an
unmapped family stays queryable. What makes it a *dump* rather than a database: `DECIMAL`
never `FLOAT`; units in `column_def`, never in a value; canonical columns `snake_case`
with the printed Italian in `column_def.label_it`; deterministic output — fixed table and
row order, explicit column lists, 2-dp decimals, LF, **UTF-8 without BOM** (a BOM in a
`.sql` file is a syntax error), `BEGIN`/`COMMIT` per file, `--replace` opt-in for `DROP`.

## 2. The order of work, and why it is this order

```
Phase 1 catalog 2025 (done) → step 1 for 2024 → catalog 2024
    ↓
Phase 2 the diff → Phase 3 schema → Phase 4 extractor + verify → Phase 5 bake-off
```

Two steps are counter-intuitive. **The schema is designed after the diff**, because a
schema written first encodes one year's assumptions as ontology. **The bake-off runs last**
though it would have informed Phase 1: choosing a reader before writing ground truth is how
a tool gets chosen for its documentation, and hand-transcribing five pages is the expensive
part — done after the catalog exists, the benchmark can cover the grammars actually found.

## 3. The coverage ceiling, stated rather than hidden

A table with a destroyed text layer splits correctly, appears with `grammar=unknown` and
a reason, and yields **zero rows**. On 2025 that is 19 tables and 498 pages — the whole of
`E` (221pp) and `C1` (97pp) among them — plus 2001–2010, which has no text layer at all.
This is not a defect and must never be coloured as one. The consequence to respect: **no
coverage claim is honest unless it states the extractable fraction alongside the total.**
## 4. Open questions

- **2024's repair** — §6. The README's worked example and the first thing a reader runs.
- **The weak cluster** — 15 of 28 volumes yield ≤10 tables; 2014 vol. I and 2015 yield 1. They need a family-specific detector or an honest partial-coverage marker, not a 1-table split presented as an answer.
- **`DIFESA/A2C6/2`** is a company list with no header band, catalogued `unknown` at 5 legible pages. The honest answer may be a *new grammar* rather than a better header detector.
- **Two undiagnosed misses**: MEF `IBB` reports 3 columns named `LEGISLATURA – DISEGNI DI LEGGE E…` (page furniture, not a header), and DOG `O1`/`O2` report names like `e | , 3`. Both visible in the census, which is the argument for keeping the census.
- **`H1`'s 22 legible pages of 32** — labels destroyed, digits intact. Worth OCR, or part of the ceiling?
- **The bake-off benchmark** — A1 p1 (rotated, ruled, nested), `EE` p1, `MG10` p2, Annesso 4 p1, one garbled page (expected: nothing). *To be checked rather than assumed:* tabula has no rotation normalisation and cannot express a group header, so it is expected to fail on the 48 rotated tables.

---

# The measured record

## 5. Baselines

Written before the first fix, updated when the year closes. `FAIL` is the defect; `WARN` is
usually structural and is accounted for, not chased; `SKIP` means no witness.

| year | files | pages | status |
|---|---|---|---|
| **2025** | **150** | **1982** | tagged **`2025-clean`** — judged clean; 142 ok, 0 FAIL, 8 warn |
| 2016–2023 | | | not started |
| 2024 | | | **baseline re-measured 2026-10-11, failing** — §6 |

**The 2024 baseline, re-measured 2026-10-11** before the first fix, from
`Reports2PDFTables_2024.py` (a fresh copy of the template) on `--dry-run`: **98 tables,
41 + 57** — the 31 + 35 of the 2026-10-05 measurement in §6, plus the 31 relation
exhibits vol. I carries. The two are not comparable and must not be added together; §6
has both. Already right at this baseline: `stitch()` does **not** fire (`continued`
empty in both volumes, as §6 requires), vol. I's family is 2, and vol. II's 35 MEF tables
are exactly the 35 codes its bookmark tree names.

**Three tags, three claims.** `2025-baseline` (`b0bbbf2`, 2026-10-06) marks the FAIL-free baseline to iterate from — 110 files, *not yet judged clean*, which is what it always said. `2025-verified` (`5d91d35`) marks what the machine measured. `2025-clean` (`31be7d7`, 2026-10-11) marks the user's judgement, and it is the only one that claims the year is clean. Between the first and last the manifest grew from 110 files to 150 and the FAILs went 31 → 0; §9 and the tag body carry the detail.

**The 2025 row was 119 files / 1952 pages until 2026-10-11**, moving when the relation
exhibits (`T1`…`T23`, `T81`…`T88`, `T141`, `T142`) were added as a fourth code space. A
baseline not re-measured after a feature lands describes a version nobody has any more. The
old 8 WARNs on 119 files were unfixable by construction — 5 × `stamp_total` (digits in a
broken font, so the total cannot be *read*; that it is constant across the file is checked,
and holds), 2 × `edges.unreadable_pages`, 1 × `own_code_absent` on a bookmark-attested file.
`own_code_ratio` was removed: a table printing its code on its first page and a plain running
header after that is the ordinary shape of a real table.
## 6. 2024: measured, and wrong

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

## 7. Archive coverage

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
| **2024** | II / I | 3✗ / 2 | 40 / 10 — **failing**, §6 |
| 2025 | II / I | 3 / 2 | **150** |

**Weak files (≤10 tables): 15 of 28** — 2012–2017 is the cluster; 2024 vol. I also lands
at 10 but is *broken*, not weak. Deferred years are on disk with known state: 2001–2010 no
text layer, 2009/2011 from the SIPRI mirror, 2012–2013 no parseable page number.
**Bookmarks exist for exactly 6 volumes** (197 codes); four more have outlines but only
`"Pagina vuota"` and container entries.

## 8. The audit that found (2025)

`VerifyTables.py` over the 81 then-exported tables gave **55 PASS, 8 FAIL, 18 WARN** —
`MG8` holding ten tables, `M` holding `TAB N`, `O` holding `TAB P`, `MT7` holding
`TAB MT13`, `P`/`N` as prose, `E`/`F1` with a foreign trailing page. Two were stale
output written hours before the trim fix, two since closed by the ministry tree. What it
leaves behind is the shape of the method: an independent witness per claim, FAIL at zero.
## 9. Operational backlog

Defects and gaps in the pipeline, distinct from the open questions in §4.

- **2016 vol. II leaves 358 of 768 pages unattributed, and they are tables.** The block prints `TAB. M` on 278 of them; the header scan detects only the *suffixed* series and never the four bare codes, so the main art. 1 comma 2 allegato is absent from the output while the summary tables beside it are exported. The bare series *is* found in 2025 and merely misread in 2024, so the codes are not the obstacle — 2016 loses them because nothing else witnesses them. **Needs an engine change** — a new header style for `TAB. M` with no suffix, gated against prose — and a re-run of every year in scope (AGENTS.md §2).
- **2024 is measured and failing — fix it before extending anything.** §6. **Needs an engine change** in `detect_family()`, and a re-run of every year in scope. Likewise 2015 and 2014 vol. I should join 2001–2010 in an explicit *unsupported* list rather than emitting a misleading 1-table split.
- **The trailing-page check only runs where the stamp is legible** — 2019 vol. II, 2023 vol. III and the family-3 volumes carry none, so nothing is checked there and they are exported differently. They need a second kind of witness.
- **Recover the missing page numbers in the 2016 and 2018 indices.** Their ministry rows carry none, so validation rejects the index and the volume falls back to page furniture. The numbers are on the same page.
- **Explain the `MEF/II` key collisions** in 2020 vol. II and 2024 vol. 2: two detections landed on one triple and one was dropped. Genuine duplicate or placement error; undecided.
- **Enumerate the 2024 vol. II Dogane annex codes** — sampled every 15th page, not yet enumerated. And resolve the 2021 volume-2 filename collision: `TOMO_II.pdf` and `VOLUME_II.pdf` are different documents (844 and 1070pp) claiming the same volume, so `parse_volume()` refuses to guess.
- **Subset-font repair: measure 2024 vol. I (phase A step 0).** The plan quotes 98.0% on 2025; 2024 vol. I is a different subset family. The digest-level reading stands: a **volume-local** codebook resolves **70.7%** of the garbled slots, and re-measured independently at **73.2%**; the **42-volume corpus** resolves **309 of 324** distinct target digests, and the curve **saturates at 2018–2021**. Re-measured end-to-end over all 1016 pages, decoding charstring digests and substituting per `visitor_text`: **330 garbled pages, residual median 0.00%, p90 0.00%, max 0.38%, 18 unresolved characters out of 548,873 (99.9967% resolved), 0 of 330 pages over the 5% stop-gate.** No-op invariant held twice, at **651/651** and **655/655** pages carrying no subset font, byte-identical to pypdf. Two corrections to the digest-level reading, both of which change the implementation. **(1) `space` is 53 of the 64 slots a corpus codebook cannot name, and it is not furniture to leave alone.** It is resolvable — from the garbled font's *own AGL glyph name*, never by outline, which is precisely the cross-face outline collision the plan warns of and the reason it was written off. Missing spaces are thousands separators (`1 234 567,89`), so they fall *inside numbers*, where they cost the most. The rule neither the plan nor the first reading states: **a slot whose own name is in AGL is taken from the name; the outline is consulted only for `uniE0xx`.** That single change took residual from **2.42% to 0.00%** median. What remains is **11 genuinely unnameable slots** — independently the same 11 the digest-level reading found, which is the convergence worth keeping. **(2) `is_garbled()` under-fires, so the plan's framing understates the win.** 31 pages the decoder substantially repairs are reported clean — p612 carries 128 PUA against 1342 alnum, 9.5%, under the 15% threshold, because the ratio is diluted by the page's legible furniture. Amendment 1's `WARN decode untrusted` guard must key on measured residual, never on `is_garbled`, and the affected population is larger than §6 assumed. On the corpus: **a four-volume sample is not enough**, and fails in a way a saturation curve cannot show — it lacks `MicrosoftSansSerif`, which is ~76% of 2024 vol. I's unresolved characters and appears *only* as PUA subsets there, while readable exemplars sit in 2018 vol. I, 2021 tomo I/II and 2022 vol. I. The 42-volume corpus already has that key. So the harvest must be corpus-wide and must record which faces it covered; "the curve saturated" is not the test. Not measured here: 2025 under this codebook, the `ciphered` class, and whether the name rule holds on other garbled years. Plan: `subset-font-repair.md`; amendments and order: `font-repair-phase-a.md`.

### Closed on 2026-10-11

- **`TRASH` wrote nothing for 2025 vol. I.** `trash_reason()` took `tables` and never used it, and looked the key up through a `by_path` that stopped existing when the ministry tree rekeyed `tables` from paths to the `(authority, article, code)` triple. Every volume whose trash reached that last witness raised `NameError`; vol. I was the only one to reach it, because vol. II's runs are all claimed earlier in the chain. The rewrite that fixed the adjacency test left its prose and not its lookup — the same shape as the `ELENCO_CODE` bug, and the reason the rule in AGENTS.md §3 exists. Fixed by rebuilding the display string from `tables`; verified on the real manifest and on the real write path (16 files, accounting identical to the pre-fix log).
- **31 of 2025's files FAILed `uniqueness.own_code_absent`.** Every relation exhibit, absent from the INDICE *and* every bookmark tree, so the manifest was their only witness — an artefact of verifying without `--manifest`, not a defect. With it: **142 ok, 0 FAIL, 8 warn**, and `provenienza` runs for the first time (1982/2096 attributed, 0 duplicates, 117 unattributed).
- **The 11 `no-detector` runs on vol. I** are narrative under a relation exhibit (p26) and two charts (p57), not undetected tables. Trashing them is correct.
