# AGENTS.md

Commands, conventions, decisions and rules — not a description of the code, which is what the code is for. **Measurements** live in [ROADMAP.md](./ROADMAP.md) §7–§11; reading a table is [READING-TABLES.md](./READING-TABLES.md).

§5 is the one part not inferrable from the code: each is a bug that shipped, and the code that avoids it looks entirely correct. Keep them.

## 1. Commands

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
bash reports_185_1990/download_185.sh reports_185_1990 reports_185_1990/manifest.tsv  # 0. sources
python Reports2PDFTables_2025.py          # 1. -> Out/PDF/ + Out/TRASH/
python CatalogueTables.py --year 2025                    # 2. -> Out/CATALOG/
python VerifyTables.py --year 2025                       # 3. -> Out/VERIFY/
```

`--manifest <path>` on 1 or 3 enables the cross-checks needing it; without it those verdicts silently do not happen. Generate into `--out <scratch>` while debugging — overwriting `Out/` invalidates the last run's `verify-<anno>.json`.

### 1a. One year at a time

Debug against **one** year, measure against the others after. **The user judges "no bug", not the agent** — the verifier reports, a human decides.

**Order 2025 → 2024 → … → 2016**, descending by coverage per effort. A clean year is a git tag (`2025-clean`), never a copied file that drifts.

**To start a year:** `cp Reports2PDFTables_TEMPLATE.py Reports2PDFTables_<anno>.py`, set `YEAR` and `VOLUME` in the copy, run it, edit only the copy. A year is done when its own script runs and `VerifyTables.py --year <anno> --manifest <path>` reports **0 FAIL** — without the manifest the relation exhibits FAIL spuriously, since nothing else witnesses them.

## 2. Architecture

- **Each year owns its detectors; the printed vocabulary is shared.** There is no generic engine. `Reports2PDFTables_<anno>.py` holds the DETECTORS — `page_code`, `scan_headers`, `detect_family`, `build_manifest`, `resolve_repeats`, `stitch` — and the driver, and is **never edited once its year verifies clean**. The year-independent half lives in `lib/` and is shared: `patterns.py` (every regex and constant), `pageio.py` (reading a page, the outline), `emit.py` (manifest → `Out/PDF/` + `Out/TRASH/`), `volumes.py`, `ontology.py`, `indice.py`.
- **The objective is tables produced, not one elegant program.** A detector right for 2025 is wrong for 2024, and merging them means re-testing every year on every change. So years fork deliberately and a new year starts from `Reports2PDFTables_TEMPLATE.py` — a copy of a *verified* year, not of whichever one was being worked on. Never edit the template.
- **`VerifyTables.py` imports from `lib/` and from no year script.** That is the rule the whole scheme rests on: the audit must be one set of checks that can see every year. The moment it imports a detector from a year, it audits that year by its own rules and can no longer report a bug it shares an opinion with. `detect_family` is the case in point — it gates nothing in the verifier, so it was replaced by `seen_family()`, which reports what the verifier can see.
- **A primitive in `lib/` is wrong for every year at once**, where before it was wrong for one. To change one, import it in the year script and shadow it locally. Nobody edits `lib/patterns.py` to suit one year.
- **The output tree is the ontology:** `Out/PDF/<authority>/[<article>/]<code><anno>.PDF`, from `ontology.relative_path()`. Both steps must agree on layout and suffix; step 2 walks with `rglob` because the article level is optional.
- **The manifest key is the triple `(authority, article, code)`** — never the bare code, which is what let the Dogane overwrite MAE. The Dogane print `TAB. N` twice in one volume, and MAE's `TAB M1` is not the Dogane's. A code is unique only within (authority, article).
- **Claimed and trashed pages are disjoint and together are every page.** `Out/TRASH/` is the complement of `table_plan()`, the same function `split_pdf()` writes from, so they cannot drift. Never re-derive the claimed set to compute the trash — two notions of "owned" is how a coverage number starts lying. TRASH is not `unassigned_pages` (pages *inside* a span) and misses over-extension, which is `check_page_overlap()`.
- **Witnesses are unioned, never ranked.** The bookmark tree is human-authored and wins conflicts, but 2025 vol. II's outline omits `MG1`–`MG9`, which only the header scan finds; the header scan reads prose as headers, so ranking it second was wrong too.
- **The verifier never writes inside `Out/PDF/` or `Out/CATALOG/`**, and reads both output layouts so it can audit output predating the ministry tree.
- **Step 2 is `CatalogueTables.py`**

## 3. Rules

- **`manifest.tsv` is exactly 7 tab-separated fields, URL last** (`year leg num vol file pages url`). Five- and six-column lists were both passed by mistake, putting the page count in the URL field; every fetch failed.
- **Run a year with `--volume both`, never one volume at a time** — 2025 `F1` straddles the volume join.
- **No `filter=lfs` in `.gitattributes`** — LFS meters bytes into GitHub's quota rather than preventing commits, and the binaries total over 1 GB.
- **The verifier imports the detectors; it never restates them.** A second spelling of a trap-shaped pattern is a bug however well it is commented. Two divergences are allowed because each names its witness: `page_code` (not family-gated — 2025's volumes mix all four code spaces) and `fold` (digit-one as a fallback only, so 2025's `II` and `II1` both survive).
- **Write the log before the JSON** — the log is what a person reads, so it must not be the casualty of a crash between the two.
- **Guard `build_manifest` per volume, and write artefacts before any summary.** One unreadable volume must cost that volume, not the other twenty-one; a formatting bug in a human-readable report once raised a `TypeError` upstream of the data and took 22 volumes with it.
- **A manifest that parses is not a manifest that is read.** `tables` changed from a mapping to a list; the verifier called `.get()` on it and failed *silently* — wrong, plausible, invisible. Check this class before trusting any run.
- **The catalog inherits step 1's coverage, and that is a trap.** A table it cannot read is one whose *text* was destroyed, not one that does not exist. Any year-over-year comparison of which tables exist must carry its witness (`index` / `bookmark` / `header-run` / `garbled` / `missing`) beside it.
- **Write a year's baseline down before the first fix**, so "no bug" becomes a comparison. `FAIL` is the defect; `WARN` is usually structural and is accounted for, not chased; `SKIP` means the check had no witness.

## 4. Environment

- `pymupdf` is a real dependency (step 2); `pypdf` is what 1 and 3 use. No JRE.
- **`python3 -m venv` fails on this host** (`ensurepip` missing) — use `pip install --target <dir>` or `--user`. Do not conclude something else is broken.
- `Out/` and `reports_185_1990/` are gitignored, rebuilt from `manifest.tsv`.
- The Camera archive needs a browser `User-Agent` and supports range requests: probe a volume with a 200 KB `GET`, not a 15 MB download.
- **~1300 pages cannot yield text**, in three verified classes: *no text layer* (scanned, 2001–2010); *garbled* (subset font, no `ToUnicode` — 2023 I, 2024 I, 2025 I/II, 2018 II, 2020 I); *ciphered* (`/Identity-H`, no `ToUnicode` — 2017 I, 2022 I, 2016 I). Garbling does not break step 1, which needs only page boundaries: one PUA codepoint per character means two pages of one document agree byte for byte. Ciphered is worse, because the text reads as plausible.
- **OCR is out of scope by decision.** 2025's 498 dead pages are that ceiling.

## 5. Traps

**Patterns**

- `\b` is the wrong boundary twice over: `TABLES` parses as `TAB`+`LES`, and `TAB_A1` has none around `TAB` because `_` is a word character. Use `(?<![A-Za-z])TAB[ ._]+` and a negative lookahead.
- A separator spanning newlines reads `"una tabella (F\nG)"` as code `FG`. Use `[^\S\n]+`, **never** `\s+`.
- One trailing digit hides the two-digit codes (`TAB. MG10` → nothing). Every code-reading pattern is built from the single `CODE_TAIL` string, so the digit count is one decision, not one per pattern.
- **A private copy of a trap is the same bug with a second place to fix it in.** That is how the defect above happened: the verifier took its own copy, fixed the copy, left the original broken, and wrote that the original was deliberate. **A divergence needs a reason that is still true**, and a reason naming a file and a pattern rather than a witness is not one.
- **Positional windows cannot work** — 8 lines lost 24 of 28 tables in 2019 vol. I, 24 lines invented `KK1` on 2025 vol. II p263. Use frequency: a table repeats its code on every page of its run, a prose mention appears once.
- `MIN_RUN` applies to **style 3 only** (on all styles it dropped real one-page tables: 2020 vol. II went 41 → 23), and is **waived for the Dogane**, where the prose false positive does not occur.
- `SEGNALAZIONI` counts only in the top 12 lines, and **its presence does not mean the page is an index** — 2025 MEF table pages print it in a running header. What separates an index is how many codes it names: the MEF ELENCO names fifteen, a chart page one. Style 1 needs that same banner check; 2023 vol. II p8 is a summary page carrying a bare `UE` that steals the real table's start.
- The prose stop-list must **not** contain `E` or `I`; both are genuine family-2 codes, and suppressing them cost 4 tables on 2019 vol. I.
- The **rotated margin stamp arrives as two lines** (`Tabella` / `NN`), invisible to both leading and inline patterns; those codes are found today only because the bookmark tree lists them. A **bare capital in the body is not a code** — one sits 108 lines down a Dogane page. A bare code needs three gates: header region (`BARE_CODE_MAX_LINE = 8`), a code the volume's ELENCO promised, and the art. 27 banner.

**Spans and boundaries**

- The **trailing-page stamp needs its position, not just its shape** — shape alone also matched a body row and cut `A1` from 537 pages to 1. It is the 9pt chunk between 30% and 52% of page height. **Never trust its token order** (rotation reverses it per page): the total is the number that does not change. The two PUA runs ending in U+E000 are the words, not the numbers.
- An **appendix is a different table** that prints its base code: `TAB. M` is export, `TAB. M - APPENDICE` is re-export. The suffix is **glued**, or manifest and filename disagree. Its cover has the keyword shredded into `T` / `AB. M - APPENDICE`, so `appendix_code()` probes a joined pair — lose that page and it reverts to the base table. Never gate it on `classify_page() == DOG`.
- A volume can carry an **annex in a code scheme of its own**, colliding with its year-mate's: 2024 vol. II switches to the Dogane scheme at printed 1707 and the bare letters read as `M2`/`N2`/`O2`/`P2`, making five phantoms. The `(authority, article, code)` key makes this structural rather than a filter.
- An **allegato cover lists its own tables**, which the header scan reads as their starts, inventing twelve. Match with whitespace squeezed out.
- **Gazzetta Ufficiale pages bound into a volume hold prose that clears `MIN_RUN`** — 2019 vol. I pp 710–808 was a phantom 22-page table. Skip them *inside* the page scan, not in a pass of its own, and print what was skipped.
- A ministry **prints the same table twice**, inline and in the annex, hundreds of pages apart. Two gaps separate prints and both count: a wide one (`REPRINT_GAP = 40`) and one containing a listing page. The copy kept is the one the listing indexes.
- An **annex ends where the next body's section opens** — the only boundary available in a volume with no INDICE. A name test alone is useless: the MEF's own pages open with `Ministero dell'Economia e delle Finanze`.
- `ELENCO TABELLE SEGNALAZIONI` indexes the tables that **follow** it, and cannot be applied as the scan passes, since a ministry may print a listed table *before* the listing. Hold the candidates and consult them once the volume is read.

**Reading**

- `is_garbled` is a ratio, so it fires on partly garbled pages too; downstream must not read "garbled" as "empty". The verifier skips what it cannot check rather than failing it.
- Digit one may print as a capital `I` (`Tabella DI` for `D1`), but `normalise_digit_one()` must **never** be applied unconditionally — 2025 has both `II` and `II1`. Fold only as a fallback; a harvested index is human-authored.
- **An index row is not always one line** — `INDEX_ROW` is `$`-anchored, so a wrapped title loses the row whole (2024 vol. I's `B5` was lost outright). `read_index()` must also read past the first page reaching eight codes, or a multi-page `ELENCO` is truncated (2024 vol. II listed 12 of 35).

**Code spaces**

- **Three code spaces, not one, and the verifier must know all of them.** Difesa annessi are digits (`2`, `3A`, `4`) and print no code at all; against the art. 27 shape they FAIL, which is what happened to all five in 2025. The relations number their own exhibits `T1`…`T23`, `T81`…`T88`, `T141`, `T142` — the `T` is not decoration, because `safe_code()` strips the dot in `8.1` and manifest and filename would disagree.
- A table's **code may be printed only on its first page**, which no ratio can tell from a prose mention (1/41 and 1/6 are the same shape). `own_code_ratio` is removed; `foreign_table` and `own_code_absent` catch a wrong file.
- `verify-<anno>.html` is the one a person opens, and must keep: **links relative to the report's own directory** (against `out_root` all 110 came out dead — resolve every href, do not eyeball one); **self-contained**, no CDN, so it opens from `file://` offline; and the **verdict as a word as well as a colour**.
- Several bookmark trees carry **container entries with no page** (`'araba.pdf'`, `'0001.pdf'`). Skip them.

## 6. Disproven

Each cost real time. Do not retry.

| approach | verdict |
|---|---|
| `JR_PAGE_ANCHOR_*` named destinations | 2020 II, 2021 II, 2023 III carry them; pypdf resolves most to `None`. Dead end. |
| Margin stamps via `pdftotext` or pypdf | The letters are in the same broken subset font as the body. |
| `pdftotext` on garbled fonts · `pdffonts` → `uni=no` | Identical PUA output; confirms the fault, recovers nothing. |
| fontTools `CharStrings` | The subsetter copied each PUA codepoint into the glyph name. Only outlines remain. |
| OCR of garbled/ciphered pages | Out of scope by decision (§4). |
| Squeezing whitespace to read a listing's codes | The code runs into the title (`TabellaAAEsportazionidefinite…`). Match the keyword with `\s*` between its letters instead. |
| `ontology.MEF_ALT_RE` as the "is this an MEF page" test | pypdf shreds every word it is built from. Use `is_mef_section()`. |

## 7. Records

Baselines and archive coverage — ROADMAP.md §7, §9. 2024 measured and failing — §8. The audit that found — §10. Operational backlog — §11. Reading a table — [READING-TABLES.md](./READING-TABLES.md).
