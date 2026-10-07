# AGENTS.md — operational contract for coding agents

Invariants that are cheap to state and expensive to rediscover, the detection
traps that have each cost a debugging cycle, the approaches already disproven,
and the measured coverage per year.

Human-facing description of the pipeline — what it does, how to install and run
it, why the design is what it is — is in [README.md](./README.md). The files are
complementary rather than duplicated. Where they overlap, **this file is
authoritative** for the working method (§1a), the coverage table, the regex
traps and the disproven list.

---

## 1a. Working method: one year at a time

**A change is debugged against ONE year, and only then measured against the
others.** Not the reverse, and not as a rule for the finished pipeline — §6's
archive-wide runs still exist and are still the coverage record. It is the rule
for *getting a fix right*, and it exists because of the arithmetic:

| loop step | 2025 only | all 22 volumes |
|---|---|---|
| generate (write) | **13 min** | ~67 min |
| verify, cold cache | **12 min** | ~4 h |
| verify, warm cache | **<1 min** | ~5 min |

Measured on this archive. A full iteration over everything costs ~5 h, so it
cannot be run after every edit — which means a change *gets* measured against
the whole archive only occasionally, and only for changes somebody remembered
to check. Checking one year costs ~15 min, so it happens after every edit.

The loop is: **fix a bug → regenerate that year → run the verifier on that year
→ read the verdicts.** Repeat until the user judges it clean. Then move to the
next year. **The user judges "no bug"**, not the agent: the verifier reports, a
human decides.

### Order: 2025 first, then down

2025 → 2024 → 2023 → 2022 → 2021 (three volumes) → 2020 → 2019 → 2018 → 2017 →
2016.

Descending, not ascending, and the reason is coverage per unit of effort: 2025
yields 119 tables across 2 volumes and exercises every code family, all four
ministries, the ELENCO index, a garbled section and a cross-volume stitch; 2016
yields 43 across 2 volumes. Fixing the rich volume first means the traps that
would bite ten times are found once. 2024 is second for the opposite reason —
it is the year that has *never been run* (§6), so its ground truth is unknown
and it is expected to be the longest single session.

### One engine, thin per-year scripts

**`Reports2PDFTables.py` stays the engine and holds all detection logic. A
per-year script is a thin entry point that imports it and pins year and
volumes — it is NOT a fork.**

```
Reports2PDFTables.py          the engine; every detector lives here
Reports2PDFTables_2025.py     ~20 lines: imports the engine, pins 2025 / both volumes
Reports2PDFTables_2024.py     same shape
```

This was a deliberate choice against forking, and the reason is §2: the
invariants in this file ("the manifest key is the triple", "nothing hardcodes a
year", "step 1 and step 2 must agree on the layout") are only enforceable if
there is one copy of the code to enforce them on. Ten forked copies drift, the
drift is invisible, and `is_mef_section` ends up existing ten times with ten
subtly different bodies. A per-year override is *data* — an expected table
count, a list of known traps — never forked logic.

### Milestones are tags, not files

A year being clean is recorded as a git tag, `2025-clean`, so it is an
immutable reproducible snapshot rather than a copy of a file that then drifts.
Tag on the commit that closed the year, and note the finding in this file.

### Baselines, as measured

One row per year, written before the first fix and updated when the year closes.
`FAIL` is what must reach zero. `WARN` is not a defect in itself — see the
note below the table.

| year | files | pages | PASS | FAIL | WARN | SKIP | status |
|---|---|---|---|---|---|---|---|
| **2025** | 119 | 1952 | 111 | **0** | 8 | 0 | FAIL-free, awaiting judgement |
| 2016 | | | | | | | not started |
| 2017 | | | | | | | not started |
| 2018 | | | | | | | not started |
| 2019 | | | | | | | not started |
| 2020 | | | | | | | not started |
| 2021 | | | | | | | not started |
| 2022 | | | | | | | not started |
| 2023 | | | | | | | not started |
| 2024 | | | | | | | not started |

**What the 8 WARNs on 2025 are**, so that they are accounted for rather than
chased — three populations, none of them a fixable defect:

| count | check | files | why it cannot be closed |
|---|---|---|---|
| 5 | `completeness.stamp_total` | B1, C1, E, F1, L | the stamp's digits are in one of the broken fonts, so the total cannot be *read*. What can be checked — that it is identical on every page of the file, i.e. one document and not a pasted one — is checked and holds, on 4, 52, 70, 97 and 221 pages |
| 2 | `edges.unreadable_pages` | A1, A4 | the pages are garbled: no `ToUnicode` in the font, so there is no text to check the file edges against (§3). Permanent by construction |
| 1 | `uniqueness.own_code_absent` + `edges.unreadable_pages` | H1 | "H1" is on no *legible* page; 9 of its 32 pages are garbled and carry it invisibly. The file is found by a human-authored bookmark, so it is real — the WARN is the verifier saying it cannot confirm which table it is |

Note the last one is a WARN and not a FAIL precisely because the bookmark
attests it: that is the `attested` flag doing its job. It was a FAIL until the
manifest was taught to count as a witness too (see `manifest_tables()`), which
had been silently broken since the ministry tree made `tables` a list.

The other 21 WARNs of the 110-file baseline were all `own_code_ratio`, and the
check is **gone**: a table that prints its code on its first page and a plain
running header after that is the ordinary shape of a real table, so a ratio
never carried a verdict. 41 pages of Tabella EE carry "EE" once; the nine Dogane
appendices carry theirs on the cover alone. The count survives in the detail of
`own_code_run` because it is the cheapest thing in the report to judge by eye.
What still catches a file holding the wrong table is `foreign_table` (another
code repeated over `MIN_RUN` pages) and `own_code_absent` (this file's own code
on no legible page).

### Before touching anything: write the baseline down

For each year, **generate it and run the verifier BEFORE the first fix, and
record the verdicts.** The verifier reports four verdicts — PASS / FAIL / WARN /
SKIP — and "no bug" is not the same as "no FAIL":

- **FAIL** is a real defect. Zero is the target.
- **WARN** is usually not fixable by design. §5 rules OCR out of scope, and the
  garbled and ciphered pages (§3) cannot yield text at all, so a WARN on those
  is permanent. Each WARN must be *accounted for*, not chased.
- **SKIP** means the check had no witness. Fine.

The point of the written baseline is that "no bug" then becomes a comparison
rather than an impression. §6 records that 2024 "was never run" and that the
2025 audit sat un-actioned for a while; both are what happens without one.

**Where output goes.** Generate into a scratch `--out` while a year is being
debugged, and write to `Out/` only once the year is judged clean. Overwriting
`Out/` mid-session invalidates any `Out/VERIFY/verify-<anno>.json` referring to
the previous run, which is the only record of what the last output actually was.

---

## 1. Scope and target

**2016 is the working baseline. 2017+ is next. Pre-2016 is deferred, not
broken.** Nobody should "fix" a deferred year: those numbers were measured, they
are recorded below, and they are not regressions.

**In scope is 2016+.** `--all` stops at `FIRST_YEAR = 2016`
(`Reports2PDFTables.py`), so the 2015-and-earlier scans are never reported. Not
a judgement about those volumes so much as a decision to stop carrying them:
presenting "0 tables" as a finding is worse than not running them. Explicit
`--year` on an older year still works and still writes.

| fact | 2016 |
|---|---|
| volumes on disk | `reports_185_1990/2016/2016_LXVII_n5_VOLUME_{I,II}.pdf` |
| vol. II | family 3, 768pp, **34 tables** |
| vol. I | family 2, 716pp, 5 tables |
| printed page numbers | both volumes print a parseable folio |
| INDICE | present, but its rows for Interno, Difesa and Sviluppo economico carry no page number, so validation rejects it and the volume falls back to page furniture |
| vol. II outline | exists, but only `"Pagina vuota"` + annex titles |

2016 exercises **both** code families and both volumes, and its page numbers are
readable — which matters, because the cross-volume `stitch()` and the
trailing-page trim both rest on the printed folio. vol. II's 34 tables is a
strong signal the detectors work on it. Caveats:

- **vol. I is in the weak cluster** at 5 tables. Not a blocker, but not evidence
  of correctness either — same cluster as 2012–2017.
- **28pp of vol. I is ciphered** (`/Identity-H`, no `ToUnicode`, no
  `/Differences`). Step 1 still splits those pages, since it needs only page
  boundaries; step 2 cannot extract text from them. Expect `text_status()` to
  report them.
- **vol. II's bookmark tree is useless** for detection. The header scan is the
  only working detector there, so the header traps in §4 matter most here.
- 2016 was already checked for false cross-volume joins and produced none.

---

## 2. Do-not-break invariants

- **`manifest.tsv` is exactly 7 tab-separated fields, source URL last** —
  `year`, `leg`, `num`, `vol`, `file`, `pages`, `url`. The script rejects any
  row whose last field is not an `http(s)` URL before creating anything. A
  five-column download list and a six-column inventory were both passed to it by
  mistake, putting the page count in the URL field; every fetch failed with
  `URL using bad/illegal format`.
- **Run a year with `--volume both` (or `--all`), never one volume at a time.**
  2025 F1 straddles the volume join; per-volume runs lose half of it.
- **Do not re-add `filter=lfs` rules to `.gitattributes`.** LFS does not prevent
  committing, it just moves bytes into a metered quota, and this repo's binaries
  total over 1 GB.
- **`process_tab_n1` … `process_tab_p2` are dead code.** Dispatch is
  family-based now (`detect_pdf_type()` / `table_semantics()`). They document
  family-2 column layouts and may be worth keeping as reference, but nothing
  calls them.
- **Nothing hardcodes a year.** It comes from the `reports_185_1990/<anno>/` path
  and is used as a filename suffix, so adding a year needs no code change.
- **Step 1 writes `Out/PDF/<authority>/[<article>/]<tabella><anno>`** — the path
  is derived from the manifest key by `ontology.relative_path()`, and a code is
  unique only within (authority, article). Both steps must agree on the layout
  and on the suffix; step 2's `--year` matches the suffix, and it walks the tree
  with `rglob` because the article level is optional.
- **The manifest key is the triple `(authority, article, code)`.** Do not key on
  the bare code anywhere: that is what let the Dogane overwrite MAE, and it would
  undo the whole point of the authority level.
- **`VerifyTables.py` reads both output layouts** — the ministry tree above, and
  the older `Out/PDF/<tabella>/<tabella><anno>` one folder per table — so it can
  audit output written before the ministry tree landed. It never *writes* inside
  `Out/PDF/` or `Out/CSV/`: it is an audit, and an audit that can alter what it
  audits is not one. Everything it produces goes to `Out/VERIFY/`. Do not "fix"
  a finding by editing the verifier's output paths.
- **The audit's notion of "claimed" must cover `continued` as well as `tables`,**
  for the same reason the generator's must. `check_provenance()` took its page
  claims from `manifest_tables()` alone and so called 62 real pages of 2025 vol. II
  unattributed — the leading segment of F1, which `split_pdf()` had appended to the
  same writer. Reading only one of the two lists is the same blind spot TRASH was
  added to remove, sitting in the tool that exists to check the generator. Both
  halves now go through `manifest_continued()`, the sibling of `manifest_tables()`.
  Note what the agreement between the two numbers does and does not prove: the
  verifier's set is the complement of `table_plan()`, and both are derived from the
  same manifest, so agreement shows the verifier *reads* the manifest as the
  generator *wrote* it — not that the spans in it are right. A wrong span agrees
  just as quietly. The independent witness for spans is the index page count.
- **The claimed and the trashed pages are disjoint and together are every page of
  the volume.** `Out/TRASH/<anno>/<volume>_p<a>-<b>.PDF` holds the pages no
  exported PDF contains, computed as the complement of `table_plan()` — the *same*
  function `split_pdf()` writes from, so the two cannot drift. Never write a page
  into both, and never reconstruct the claimed set a second way in order to
  compute the trash: two independent notions of "owned" is exactly how a coverage
  number starts lying. The TSV beside them records each run's reason. Measured
  2025: **72 pages per volume, 7%** (69pp of front matter + 3 trimmed tails in
  vol. I; in vol. II the 42pp cut at p194, of which 31 are the Gazzetta block).
  **TRASH is not `unassigned_pages`** — that counts pages no detector read a code
  on, and nearly all of those sit *inside* a derived span — **and it does not
  catch over-extension**, since a page `Tabella M` wrongly swallowed is
  *attributed*. That stays `VerifyTables.check_page_overlap()`.

---

## 3. Environment state

So nobody re-diagnoses:

- **No JRE installed.** tabula-py shells out to Java, so **step 2 output is
  entirely unverified.** Treat `Out/CSV/` as untrusted until a JRE is on `PATH`
  or reachable via `JAVA_HOME`.
- `Out/` and `reports_185_1990/` are both gitignored and rebuilt from
  `manifest.tsv` plus the two scripts.
- venv: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`

---

## 4. Regex and detection traps

Each of these is a bug that shipped.

- **`\bTAB\b` without a trailing boundary** parses `TABLES` as `TAB`+`LES` and
  `TABLET` as `TAB`+`LET`. Invented codes: `LES`, `LET`, `EL`, `ILE`, `ILI`.
- **A separator that spans newlines** reads `"una tabella (F\nG)"` as code
  `FG`. Use `[^\S\n]+`, **never** `\s+`.
- **Positional windows cannot work.** 8 lines lost 24 of 28 tables in 2019
  vol. I; 24 lines invented `KK1` on 2025 vol. II p263 (`"come da elencazione
  sintetica della tabella KK1"`). Every threshold in between was wrong
  somewhere. **Use frequency instead**: a real table repeats its code on every
  page of its run; a prose mention appears once.
- **`MIN_RUN` must apply to style 3 only.** Applied to all styles it dropped real
  one-page tables — 2020 vol. II went 41 → 23.
- **`SEGNALAZIONI` must only be honoured in the top 12 lines.** On 2025 vol. II
  the *table* pages print it in a trailing header block; a plain substring test
  discards every table in the file.
- **Style 1 needs an `ELENCO TABELLE` banner check.** 2023 vol. II p8 is a
  summary page carrying a bare `UE` under `Operazioni disciplinate`, which
  steals the start of the real `Tabella UE` at p543. Resolved by the bookmark.
- **Prose stop-list: do not put `E` or `I` in it.** Both are genuine family-2
  codes; suppressing them cost 4 tables on 2019 vol. I.
- **An appendix is a different table, not more of the one it is appended to, and
  it prints the base code.** `TAB. M` is `Esportazione Definitiva (EX)`;
  `TAB. M - APPENDICE` is `Riesportazione (RE)` — a different operation type, a
  different set of rows, and the Dogane's own summary lists it separately
  (`Riesportazione (RE) M Appendice – M1 Appendice – M2 Appendice`, 2025 vol. II
  p462). `INLINE_CODE` matches `TAB. M` and stops, so the appendix went to the
  base table: **M was 291pp instead of 273pp**, and six of its tables carried a
  second one inside — the same shape as the `MG8` defect below, reached from the
  other direction. `APPENDIX_CODE` returns `MAPPENDICE`, and the suffix is
  **glued, not spaced**, because the verifier reads the code back out of the
  filename stem: a manifest saying `"M APPENDICE"` against a file named
  `MAPPENDICE2025.PDF` builds a vocabulary the manifest cannot match.
  Three sub-traps, each measured:
  - **The cover page's keyword is shredded** into `'T'` / `'AB. M - APPENDICE'`,
    as on every other page of that table (13 pages of 2025 vol. II read `None`
    from `classify_page` for this reason). `appendix_code()` probes a joined
    pair of lines. Lose that one page and it is handed back to `M`, because a
    table's end is the next table's start minus one.
  - **Gating the shape on `classify_page() == DOG` is therefore wrong**, and
    fixing the ontology's `DOG_CODE_RE` to tolerate the same shredding is what
    makes the key whole. Without it the opening page grouped as
    `(DOG, None, MAPPENDICE)` — one page, its own file — while the rest keyed
    `(DOG, A1C2, MAPPENDICE)`: 120 files instead of 119, with a stray
    `Out/PDF/DOG/MAPPENDICE2025.PDF` holding a single page. Only pages carrying
    an article take part in grouping, so one unclassifiable opening page is
    enough to invent a table.
  - **Style 3 is returned, not a style of its own**, so `MIN_RUN` keeps governing
    these pages. The Dogane waive that threshold, which is what carries the
    one-page appendices (`M1APPENDICE`, `O2APPENDICE`, …); a new style number
    would exempt them from the rule everywhere. `N`, `P` and their riepiloghi
    have **no** appendix, because only EX and IM have a "re-" counterpart.
    Nine of them exist on 2025 vol. II: `M`/`M1`/`M2`/`O`/`O1`/`O2` under
    art. 1 c. 2, `O`/`O1`/`O2` under art. 1 commi 8/9 (the A1C89 `M` appendix is
    a single row worth € 0,00, p464, and was never printed).
- **A volume can carry an annex in a code scheme of its own, and it collides
  with the year-mate's vocabulary.** 2024 vol. II is family 1 (`AA`…`UE`) from
  p88, then the Agenzia delle Dogane annex takes over at printed 1707 (pdf p701,
  running to the end of the volume) and is coded **`TAB. M` / `N` / `O` / `P`**
  plus `MG1 MG3 MG4 MG9 MT1 MT7` (*sampled every 15th page, not yet enumerated*).
  These are single letters, so **not** family 2, so they miss `FAMILY2_CODES` and
  should be rejected. They are not: `page_code()` style 3 accepts any candidate
  that *is* a family-2 code regardless of this volume's vocabulary, so `TAB. O`
  is read as **`O1`**, `TAB. P` as **`P2`**, `TAB. M` as **`M2`**. That
  manufactures five phantom tables in vol. II, and `P2` spans **p1113–1260
  (148pp)**, swallowing the whole annex; `resolve_repeats()` then drops `O1` and
  `P2` as duplicates of the *real* vol. I codes, which hides the mistake behind a
  plausible-looking report. A code seen in a volume whose own vocabulary never
  lists it is a cross-volume leak, not a table. **This is now structural rather
  than a filter:** the manifest is keyed by `(authority, article, code)`, so the
  Dogane's `M`/`N`/`O`/`P` land under `DOG/A1C2` and `DOG/A1C89` and cannot
  collide with MAE's `M1`/`N1`/`O1`/`P1` even though the code strings overlap.
  Keep the key a triple — keying on the bare code brings this bug back.
- **A code pattern that allows only one trailing digit hides the two-digit ones.**
  `TAB. MG10` matched nothing: the group takes `MG1` and the word boundary then
  fails against the following `0`. So `MG10`–`MG18` and `MT13` were invisible and
  `MG8`'s span swallowed them. They are **Dogane** (`art. 11 comma 5-bis`), not MAE
  `art. 27` as previously documented — `Out/PDF/DOG/A11C5BIS/MG102025.PDF` is
  printed page 1959 of 2025 vol. II. Verified by folio, not by inference.
- **`MIN_RUN` is waived for the Dogane.** Of the 18 MG tables, nine are a single
  page and print their code only there, so the threshold dropped them while
  `MG8`'s span absorbed the rest. Waived for `ontology.DOG`, where the prose
  false positive the threshold guards against does not occur: those tables print
  `TAB. <code>` in the running header of a data page, not in narrative.
- **An allegato cover lists its own tables, and the header scan reads it as their
  starts.** `All.1 - OPERAZIONI A LICENZA` and `All.1: OPERAZIONI A LICENZA` name
  `M`, `M1`, `M2`, `N`, `N1`, `N2`, `O`, `O1`, `O2`, `P`, `P1`, `P2`, inventing
  twelve tables and truncating the real ones. Matched with the page's whitespace
  squeezed out — pypdf shreds it (`All.1: OPE` arrives as `Al` / `l.1: OPE` /
  `RAZIONI A`) so no line ever holds the header. Exactly 9 pages match in 2025
  vol. II, and they are the 9 index pages.
- **Gazzetta Ufficiale pages bound into a volume hold prose that clears `MIN_RUN`.**
  2019 vol. I p710–808 reprints drug prices and patents from Serie generale 1588,
  and its prose cross-references "Tab. I" on two consecutive pages — a phantom
  22-page table, previously the only `UNPLACED` in the volume. `indice.is_pasted()`
  skips pages carrying the header, **inside** the page scan rather than in a pass
  of its own: a second full extraction of a 1048-page volume added enough to push
  a run past 40 minutes. Skipped ranges are printed every run
  (`pasted-in: 31 Gazzetta UFFICIALe pages …`) so the assumption stays auditable.
  **Assumed, and taken as settled: a gazette page holds no table of interest.**
- **The trailing-page stamp needs its position, not just its shape.** The shape
  alone also matches body rows: `"MUNIZIONAMENTO CALIBRO 120 MM , APPOSITAMENTE"`
  (2025 vol. I p70) reads as `CALIBRO 120 MM ,` and cut **A1 from 537 pages to
  1**. The stamp is the 9pt header chunk between 30% and 52% of page height; both
  filters are in `read_page()` (`STAMP_SIZE`, `STAMP_BAND`).
- **Do not trust the token order inside the stamp.** The rotation reverses it
  page to page: 2025 vol. I p70 is `Pagina 1 di 537`, p611 is `Pagina 5 5di`,
  p817 is `Pagina di 1 221`. Identify the total as *the number that does not
  change* across the table's pages, never by position.
- **The two PUA runs ending in U+E000 are the words, not the numbers.** On a
  garbled page the subsetter mapped the space glyph to U+E000 as well, so
  `"Pagina "` and `"di "` keep it and the digits do not.
- **Titles need two patterns and neither can use `\b`** — underscore is a word
  character, so `TAB_A1` has no word boundary around `TAB`. Use
  `(?<![A-Za-z])TAB[ ._]+` for the separated form plus a separate `tabella`
  alternative for the glued form (`tabellaAA_2025`).
- **The rotated margin stamp arrives as two lines, and its code can also
  arrive bare.** On the 2025 MEF charts pypdf yields `'Tabella'` on one line
  and `'NN'` on the next, so neither `LEADING_CODE` (needs `Tabella AA` on one
  line) nor `INLINE_CODE` (the same) can see it. `NN`, `OO`, `PP` and `GF` are
  detected today only because the bookmark tree lists them. A detector that
  wants the code off those pages needs a third shape.
- **A bare capital in the body is not a code.** The Dogane annexes list
  MG-table numbers in prose, and one of those lines is a lone `I`, 108 lines
  down the page — which reads as Tabella I. Any rule accepting a bare code
  line must gate it to the header region (the stamp sits at the top of the
  page; `BARE_CODE_MAX_LINE = 8` in `VerifyTables.py`) **and** to a vocabulary
  already restricted to codes that repeat.
- **`is_garbled` is a ratio, so it fires on partly garbled pages too.** 2025
  Tabella A1's last page is its `Totale Autorizzazioni` sheet with 128 Private
  Use characters among 681 legible ones, 18.8% against a 15% threshold, and
  is reported garbled. That is correct — the line is genuinely half
  subsetted — but it means "garbled" covers partial as well as total loss.
  Anything downstream must not read it as "this page is empty"; the verifier
  skips the checks it cannot make instead of failing them.
- **The 2012 index prints digit one as a capital `I`** (`Tabella DI`,
  `Tabella Gl` for `D1`/`G1`); `normalise_digit_one()` folds these back.
- **An index row is not always one line.** `INDEX_ROW` is `$`-anchored, so a row
  whose *title* wraps is skipped whole. 2024 vol. I's `B5`, `B6` and `B7` all
  break onto a second line, so the harvest returns **28 codes instead of 31**.
  `B6` and `B7` are then still found by the header scan (they are in
  `FAMILY2_CODES`), but **`B5` is lost outright** and all three lose the index
  cross-check. Allow the title to run over a line, or fall back to the family
  vocabulary when a wrapped row leaves a gap.
- **`read_index()` stops at the first page that reaches eight codes**, so a
  multi-page `ELENCO` is truncated. 2024 vol. II's index starts at pdf p85 and
  lists only `AA`…`DD2` — **12 of the volume's 35** — with the rest continuing
  onto p86+. Keep reading while the following pages are still index pages.
- **`normalise_digit_one` must never be applied unconditionally**: the 2025
  art. 27 scheme has *both* `II` and `II1` as distinct tables, and folding maps
  both onto `I1`, after which neither is recognisable. Tabella II's own first
  page reads `Tabella II`. Fold only as a fallback — the code as printed wins
  whenever the volume knows it (`fold()` in `VerifyTables.py`). A harvested
  index is human-authored and printed correctly, so it needs no folding at all.
- **`SEGNALAZIONI` in the top lines does not mean the page is an index.** The
  2025 MEF table pages carry it in a running header of their own, so three
  data pages of Tabella P2 classified as index pages and were reported as
  trailing junk that does not exist. The 2025 MEF chart pages print
  `ELENCO TABELLE` in that same header while being one table each, so trusting
  the marker condemns `NN`, `OO`, `PP`, `GF`. What separates a real index is
  how many codes it names: the MEF ELENCO lists fifteen on one page, a chart
  page names one.
- **`ELENCO TABELLE SEGNALAZIONI` is an index of the tables that FOLLOW it, and
  it is the only witness for 35 tables per volume.** Not of the tables already
  found: it is printed immediately before a ministry's own table annex and names
  what comes next. Three consequences, all now implemented:
  - It is the **boundary** that stops the last table before it from running on
    into the annex. On 2025 vol. II the DIFESA annesso 4 detected at p254 took
    the derived end p272 and swallowed the whole MEF relation, its three inline
    tables and the listing. Separately the INDICE says p259 starts INTERNO, so
    the two clamps are independent witnesses and either alone fixes it.
  - It is a **detector** (style 6). The MEF prints five of its tables *inline in
    the relation*, before the listing, each titled `Tabella <code> - <title>`
    and each shredded by pypdf into `Ta` / `bella FG - Finanziamenti-Garanzie`,
    so `INLINE_CODE` never sees the keyword whole. And in the annex the code is
    printed **bare**, with `Tabella` corrupted to `Ta€ella` by a substituted
    euro sign, so only the code is legible. Both shapes need the listing as the
    whitelist; 2021 vol. II goes from 2 MEF tables to 35 on this alone.
  - It **cannot be applied as the scan goes past**, because a ministry may print
    a listed table *before* the listing. The pages it applies to have to be held
    (`candidates` in `scan_headers`) and consulted once the volume is read; a
    second extraction pass over 1048 pages costs more than the whole rest of the
    run.
- **A bare code line needs three gates, not one.** A code in the header region
  that the volume's own ELENCO promised, on a page carrying the MEF art. 27
  banner. Without the third gate AGENTS.md's own example bites: the Dogane
  annexes list MG-table numbers in prose and one of those lines is a lone `I`,
  108 lines down a page, which reads as Tabella I. `BARE_CODE_MAX_LINE = 8`.
- **A ministry prints the same table twice**, once inline in the relation and
  once in the annex, and the two prints are hundreds of pages apart (2025 vol.
  II: UE, LGP, IAA, FG, KK1 on pp 263-269 and again on pp 340-446). Grouping the
  detections by code joins them and gives one table a span covering all the
  prose in between. Two kinds of gap separate prints and both must count: a
  **wide** one (`REPRINT_GAP = 40`, against a widest measured interruption of
  20) and one **containing a listing page** — 2021 vol. II quotes four saldi as
  bare codes at p6-9 and prints the real Tabella AA at p16, ten pages later, with
  the ELENCO on p13-15 in between. The copy kept is the one the listing indexes.
- **An annex ends where the next body's section opens**, which is the only
  boundary available in a volume with no INDICE. 2021 vol. II's UE took the
  derived end p917 and swallowed 332 pages of Dogane relation; p586 reads
  `DIREZIONE DOGANE / Ufficio controlli dogane / R E L A Z I O N E`. A name test
  alone is useless — the MEF's own pages open with `Ministero dell'Economia e
  delle Finanze` — so it takes all three of: a body named in the first three
  lines, **no** promised code anywhere on the page, and no table title.
- **Three code spaces, not one, and the verifier has to know all of them.** The
  Difesa annexes are numbered `2`, `3A`, `3B`, `3C`, `4` and print **no table
  code anywhere** — the running header is `MINISTERO DELLA DIFESA - Annesso 3A`
  and the annesso number *is* the name. So `layout.code_shape` (which asks
  "is this art. 27 shaped?") fails all five, and `uniqueness.own_code_absent`
  fails them again because the string `4` never appears on the page. Both were
  5 FAILs on correct files. The ministry tree is what exposed them: under the
  flat layout these collided with the art. 27 codes on the bare code and were
  never written at all.
- **`verify-<anno>.html` is the third view of the same payload, and it is the one
  a person opens.** Alongside the log and the JSON, written in the same pass so
  the three cannot describe different runs: sortable table of all files, click
  to the PDF, `▸` expands every check with its verdict and detail. Three things
  it must stay:
  - **Links are relative to the report's own directory**, not to `out_root`.
    They differ: the report lands in `Out/VERIFY/` and the tables in
    `Out/PDF/`, so the honest href is `../PDF/MEF/UE2025.PDF`. Computed against
    `out_root` it came out as `Out/PDF/...` and **all 110 links were dead** —
    the worst failure this report can have, since the point of it is that a
    click opens a file. Verify by resolving every href, not by eyeballing one.
  - **Self-contained**: inline CSS and vanilla JS, no CDN, no webfont. It has to
    open from `file://` on a machine with no network. Links rather than an
    embedded `<iframe>`, because inlining 110 PDFs makes a multi-hundred-MB file
    and browsers refuse local iframes anyway.
  - **The verdict is a word as well as a colour**, so it survives a colour-blind
    reader and a monochrome print.
- **Write the log BEFORE the JSON, not after.** The JSON was written first and
  the log last, so a run that died between them left a JSON with no log beside
  it — which is what happens when the verifier is piped into `head`: the pipe
  closes and the next `print` raises `BrokenPipeError`. The log is the artefact a
  person actually reads, so it is the one that must not be the casualty. It is
  now flushed early and rewritten at the end. (Cost me one confused round.)
- **A manifest shape change silently disarms the verifier.** Step 1 wrote
  `tables` as a mapping of code → record until the ministry tree, and writes a
  **list** of records since (json has no tuple keys, so the
  `(authority, article, code)` triple became three fields). The verifier called
  `.get(code)` and `set(m_tables)` on it — `AttributeError`, `TypeError` — and
  with `--manifest` unset there was no attestation at all, so the source column
  read `sconosciuta` and every real table lost its witness. It failed *silently*
  rather than loudly: wrong, plausible, and invisible. `manifest_tables()` now
  reads both shapes; **check for this class before trusting a verifier run**,
  because a manifest that parses is not a manifest that is read.
- **A table's code may be printed only on its first page**, and the ratio check
  cannot tell that from a prose mention — it is 1/41 for Tabella EE and 1/6 for
  the 2025 phantom `P`, the same shape, so it could never decide anything and
  only ever warned on correct files. `own_code_ratio` is **removed**; `attested`
  stays a reported field, and `foreign_table` plus `own_code_absent` are what
  now catch a file holding the wrong table. (The Difesa annessi are why the
  attestation idea survives at all: they are in **no index and no bookmark tree**
  — the INDICE gives DIFESA no "Tabelle" line — so Annesso 3B, header on 1 page
  of 8, has nothing but step 1 to vouch for it.)
- **Several bookmark trees carry container entries with no page** (`'araba.pdf'`,
  `'0001.pdf'`, 2024 vol. II's `'RELAZIONE ARMAMENTI - file MEF CORRETTO.pdf'`).
  Skip them.

---

## 5. Disproven approaches

Each of these cost real time. Do not retry them.

| approach | verdict |
|---|---|
| `JR_PAGE_ANCHOR_*` named destinations (JasperReports hyperlink anchors) | 2020 vol. II (522), 2021 vol. II (570), 2023 vol. II (1689) carry them — hyperlink anchors, not per-table — and pypdf resolves most to `None`. **Dead end** — do not spend time here. |
| Margin stamps via `pdftotext -layout` or pypdf | The letters are in the same broken subset font as the body text. Verified, then dropped. |
| `pdftotext` on garbled fonts (poppler 22.02) | Returns identical PUA output. |
| `pdffonts` → `uni = no` | Confirms the fault, recovers nothing. |
| fontTools `CharStrings` | `['.notdef', 'uniE019', 'uniE026', …]` — the subsetter copied each PUA codepoint into the glyph name, so `uniE019` *is* U+E019 renamed. Only outlines remain. |
| OCR of the garbled/ciphered pages | **Out of scope by decision**, not by accident. |
| Squeezing whitespace out of a listing page to read its codes | The code runs straight into the title: `TabellaAAEsportazionidefinite…`, and then no lookahead can tell `AA` from `AAE`. Match the keyword with `\s*` between its letters instead and keep the real separator before the code. |
| `ontology.MEF_ALT_RE` as the "is this an MEF art. 27 page" test | It does not match those pages. pypdf shreds every word it is built from — `Dip` / `artimento del Tesoro Direzione V`, `Operazio` / `ni disciplinate dall'art. 27` — so neither alternative survives. Use `is_mef_section()`, which squeezes the whitespace first. |

Garbling does not break step 1: it only needs page boundaries, and the stamp
comparison works on garbled pages because one PUA codepoint per character means
two pages of the same document agree byte for byte. **2025 E is garbled on all
221 pages and still trims correctly.**

Ciphered is worse than garbled because the text reads as plausible:
`"/LFHQ]D 2SHUDWRUH"` where the header is `"TABELLA N1 PER OPERATORE"`. Not a
uniform shift (`2SHUDWRUH` needs −3, `GHI` needs −3, but `6SHGL]LRQL` maps `]`
to `)`, not `I`).

**Three corruption classes, ~1300 pages that cannot yield data. Verified, not
assumed.**

| class | cause | affected |
|---|---|---|
| no text layer | scanned images | 2001–2010, ~52 chars/page, 13 files |
| garbled | subset font, no `ToUnicode` | 2023 I 284pp, 2025 I 356pp, **2024 I 335pp**, 2018 II 299pp, 2025 II 141pp, 2020 I 174pp (~1585pp) |
| ciphered | `/Identity-H`, no `ToUnicode`, no `/Differences` | 2017 I 287pp, 2022 I 66pp, 2016 I 28pp (~390pp) |

---

## 6. Verification and archive coverage

**How to check a change:**

- **Settle a disputed boundary by rendering pages as images** — `pdftoppm -png`.
  This is how the 70-page extent of 2025 F1 was confirmed.
- **Cross-check derived spans against index page counts.** 2022 vol. I: `A1` =
  401 pages, detected span 82–482 = 401. Roughly 19/22 spans agree; **mismatches
  are an error signal, not noise.**

**Reading the printed page number.** The stitch — and the "where does the content
stop" question generally — rests on the number printed in the footer, which
survives even on garbled pages: the digits live in a font whose `ToUnicode` came
through while the letters did not. `printed_numbers()` wants the digits framed by
dashes — `– 1103 –`, `- 1103 -`, `1103 -` — and ignores anything else, so a bare
`2015` in a title and the numeric range `27 - 15` inside a table cell are both
left alone. Roman numerals do not match, which is what keeps the 2025 index
(`– III –`) out of the sequence. A page can print **two** numbers (2021 tom. II
carries the volume-local one next to the volume-wide one), so the result is a
set and the tests ask whether *some* pairing holds rather than guessing which
number is meant. Checked across the archive: 2016, 2019, 2021, 2022, 2023 and
2025 all print it this way; **2012 and 2013 do not** (see the deferred list).

**Trailing-page trim — the confirmed cases.** Each table was exported as a
document of its own and pasted into the volume, so its pages carry that
document's own `Pagina N di X` stamp; the volume folio runs continuously across
tables and says nothing about boundaries. Where the stamp's total disagrees with
the first page's, everything after the last agreeing page came from elsewhere.

| volume | table | was | now | the page(s) dropped |
|---|---|---|---|---|
| 2025 I | **E** | 222 | **221** | a separate one-page document, `Pagina 1 di 1` |
| 2025 I | **F1** | 10 | **8** | two `PAGINA BIANCA` |
| 2021 I | E | 6 | **5** | a separate one-page document |
| 2019 I | E | 35 | **34** | a separate one-page document |
| 2021 I, 2019 I | all others | — | unchanged | |

The 2021 and 2019 cases are confirmed independently by the index page, which
lists those E tables as 5 and 34 pages: trimming turns a span/index mismatch
into an agreement.

**Why the 62 pages between the 2025 volumes belong to F1.** Volume I stops at
`F1`: its last eight content pages, printed 1035–1042, the two physical pages
after them being `PAGINA BIANCA`. `F2`…`P2` live in **2025 VOLUME_II**, whose
outline starts at `F2` (printed 1105). The volume index on page 3 of vol. II
agrees: `MINISTERO DEGLI AFFARI ESTERI … (segue) Pag.1043`, `Tabelle » 1043`,
`MINISTERO DELLA DIFESA » 1232`. Decisively, the other 30 family-2 codes are
unaffected: `A1`…`F1` in vol. I and `F2`…`P2` in vol. II account for all 31 of
`FAMILY2_CODES`, which is what proves the pages in between belong to F1 and not
to a table nobody detected.

**2021 tom. I / tom. II is a duplicate, not a split.** Both print the MAE
family-2 tables — `A1` spans 295 pages in each, down to the page, with identical
opening pages — and VOL. II prints seven of those codes a third time. Neither
copy was wrong; the per-volume split used to write each over the previous one, so
which survived was a coin toss. `resolve_repeats()` keeps the best-attested copy
(bookmark > header run > title repeat, earliest volume breaking a tie) and
reports what it dropped. Only when two spans are *consecutive* — the first ends
where its volume ends, the second starts where its volume starts, neighbours —
are both kept and joined; anything else would invent a table out of two
unrelated ones.

### How the detectors are combined, in one place

The README says "several independent witnesses, unioned rather than ranked" and
stops there, because the reason only matters to whoever changes them. So:

| detector | what it reads | how many volumes have it |
|---|---|---|
| **embedded** | the PDF bookmark tree | 6 of 43 (§ Archive coverage) |
| **header** | styles 1–5: the code in a page header, the ministry's own furniture, the DIFESA annesso | every text-bearing volume |
| **listed** (style 6) | the volume's own ELENCO TABELLE SEGNALAZIONI, cross-checked against the page | 2016–2025, the MEF annex |
| **title** | the table title repeated in the body | family 3 only, where codes appear on the index page and nowhere else |
| **indice** | the volume's INDICE: not a table finder but a **placement** finder, saying which ministry owns which page range | 11 of 22 |

**Why union and not rank.** No single witness is sufficient, in either
direction. The bookmark tree is human-authored and wins a page conflict, but it
is not a superset — 2025 vol. II's outline omits `MG1`–`MG9` and `MT1`/`MT7`
entirely, which only the header scan finds. Conversely the header scan reads
prose as headers ("come da elencazione sintetica della tabella KK1"), which is
why a rank that put the bookmark second was never right either. An index naming a
table no other signal can see is a third kind of evidence again, and it is what
makes the MEF annex detectable in a volume with no outline.

**The three tests `continuation_pages()` requires before claiming a join** — all
three must agree, because the next volume opening on something *else* is the
normal case (2023 vol. III starts the Dogane relations where vol. II ended on
Tabella UE):

1. the cut table must reach the last page of its volume carrying a **printed
   number** — blanks and covers carry none, so a table that simply ends there
   is rejected;
2. the next volume's first numbered page must be the **successor** of that page's,
   and the run taken must stay consecutive — which rejects a volume that restarts
   its own numbering (2019), and an unreadable footer ends a run rather than
   letting it bridge a gap;
3. the pages taken must carry the **running header** the cut table ended on,
   read from the previous volume so an unrelated page cannot pose as the match.

Test 3 works on garbled pages, which is where a split table hides: one Private
Use Area codepoint stands for exactly one original character, so two pages
carrying the same header produce byte-identical strings in either volume.
`TAIL_PAGES = 2` is deliberate — a table often closes on a differently shaped
page (F1's `Totale autorizzazioni` sheet) and dropping it would lose the figures.

### Gazzetta Ufficiale pages, per volume

`is_pasted()` skips any page whose squeezed text carries `GAZZETTAUFFICIALE`,
because the reports quote and reprint Gazzette material and the odd document is
bound in whole. Measured page ranges, so a run that suddenly skips a large block
is recognisable as this rather than as a detection failure:

| volume | pages | what it is |
|---|---|---|
| 2019 I | 710–808 | drug prices and patents, Serie generale 1588 — a foreign document |
| 2025 II | 205–235 | CAT armament/dual-use annex, Serie generale 1319 |
| 2016 I, 2018 I, 2022 I, 2023 II, 2024 vol. 2 | 39–46 each | Gazzette material quoted by the ministry |
| 2020 I, 2017 I, 2021 TOMO_I, 2024 vol. 1 | 1–14 each | incidental |

The 2019 block is why that volume used to carry a phantom 22-page table `I`: the
gazette's prose cross-references "Tab. I" on two consecutive pages, which cleared
`MIN_RUN`. It was the only `UNPLACED` table in the volume and is now gone, with
MAE 23 and DIFESA 4 unchanged.

**Assumed, and taken as settled: a page headed Gazzetta Ufficiale contains no
table of interest.** So skipping them costs nothing and no per-volume
before-and-after diff is owed. The assumption stays auditable rather than
implicit — every run prints what it skipped, e.g. `pasted-in: 31 Gazzetta
UFFICIALe pages, no tables, skipped (p205-235)` — so an implausible count is
visible rather than silent.

A first attempt used the *absence of a valid running folio* instead, which is the
more principled witness — a pasted-in document keeps its own pagination. It was
abandoned: the folio signal is too noisy to threshold, since pypdf does not emit
the running folio first on every page and garbled or blank pages interrupt any
run. A 20-page minimum flagged 206 pages of 2019 vol. I in thirteen scattered
runs and still missed the block it was written for. The gazette header is checked
**inside** the page scan rather than in a pass of its own, because a second full
extraction of a 1048-page volume added enough to push a run past 40 minutes.

### Archive coverage

Authoritative copy, and note there are **two** counts because the key changed.
The old code-only layout was verified across 28 text-bearing volumes at **479
tables, zero failures** — that is the table below, and it is a record of what the
*previous* key produced. The current `(authority, article, code)` key gives
**610 tables over the 22 in-scope volumes (2016+), zero UNPLACED**: the same
tables plus the Dogane and Difesa series the old key could not separate, at the
cost of no longer being comparable to the 479. Do not compare the two.

**2024 was never in the 479 and does not work** — it is measured below for the
first time and fails. Treat "zero failures" as scoped to what it was measured on.

| year | volume | family | pages | tables | status |
|---|---|---|---|---|---|
| 2012 | III / II / I | 1 / 2 / 3 | 608 / 1268 / 1242 | 1 / 6 / 16 | deferred |
| 2013 | DOCUMENTO_UNICO | 2 | 1672 | 15 | deferred |
| 2014 | II / I | 2 / 3 | 656 / 664 | 3 / 1 | deferred; vol. I *unsupported* |
| 2015 | I / II | 2 | 1032 / 716 | 1 / 1 | deferred; *unsupported* |
| 2016 | II / I | 3 / 2 | 768 / 716 | **34** / 5 | **baseline**; vol. I weak |
| 2017 | II / I | 2 | 746 / 748 | 8 / 5 | weak |
| 2018 | I / II | 2 | 742 / 764 | 22 / 8 | |
| 2019 | I / II | 2 / 3 | 826 / 1004 | 23 / 10 | |
| 2020 | II / I | 3 / 2 | 958 / 732 | **41** / 10 | |
| 2021 | TOMO_I / TOMO_II / VOL_II | 2 | 793 / 844 / 1070 | 31 / 27 / 8 | |
| 2022 | II / I | 3 / 2 | 1088 / 1020 | **40** / 23 | |
| 2023 | II / I / III | 1 / 2 / 2 | 580 / 946 / 518 | **35** / 31 / 5 | |
| **2024** | **II / I** | **3✗ / 2** | **1260 / 1016** | **40 / 10** | **measured, failing** — see below |
| 2025 | II / I | 3 / 2 | 1048 / 1048 | **65** / 16 | |

### The output tree is the ontology, so placement is a detector

`Out/PDF/<authority>/[<article>/]<table><anno>.PDF`, with the path derived from
the manifest key by `ontology.relative_path()`. A code is unique only within
(authority, article) — the Dogane print `TAB. N` twice in one volume, under art. 1
comma 2 and again under art. 1 commi 8/9, and MAE's `TAB M1` is a different table
from the Dogane's `TAB. M1`. Under the old code-only path the second silently
overwrote the first, so `Out/PDF/M1` held the MAE table and every Dogane `M`…`Q`
table was **absent from the output entirely**. Verified after the fix: `N`, `N1`,
`N2`, `O`, `O1`, `O2` each exist twice, once under `DOG/A1C2` and once under
`DOG/A1C89`.

Four authorities, read off the `INDICE` and cross-checked against page headers:

| authority | tables | how it is found |
|---|---|---|
| `MAE` | family 2, `A1`…`P2` | INDICE row; also the volume header |
| `MEF` | art. 27, `AA`…`UE`, `LGP`, `GF`, `NN`, `OO`, `PP` | INDICE row |
| `DOG` | four allegati under art. 1 c.2, art. 1 commi 8/9, art. 11 c.5-bis, art. 10 quater | INDICE row + the article from each table's own qualifier line |
| `DIFESA` | "annessi" under art. 2 c.6, **no code at all** | `ANNEXO_RE` on `MINISTERO DELLA DIFESA - Annesso 3A` |

**DIFESA was being missed entirely.** Its tables are annexes with no table code
and no "Tabelle" line in the index, and their header matched no detector. The
annesso number is both the code and the last path segment; the letter must stay
adjacent and uppercase, or `Annesso 4 TABELLA RIASSUNTIVA` reads as annesso "4 T"
and splits one table in two.

**The Dogane carry a fifth kind of table under the same two articles**, and it
is the one the article level alone cannot separate: `MAPPENDICE`, `M1APPENDICE`,
`M2APPENDICE`, `OAPPENDICE`, `O1APPENDICE`, `O2APPENDICE` sit beside their base
codes with the *same* qualifier line, because `Riesportazione (RE)` is a
different operation type under the same authorisation, not a different one. Only
the printed suffix distinguishes them, so it is carried in the code itself
(`MAPPENDICE`, §4 "An appendix is a different table"). Nine on 2025 vol. II; the
A1C89 series has three, since its `M` appendix was never printed.

**Article tokens keep the differentiators the law uses.** comma 5 and comma 5-bis
are different authorisations, and art. 10 has bis/quater/quinquies — so
`A11C5BIS` and `A10QUATER`, never `A11C5` or `A10`. Only `DOG` and `DIFESA`
subdivide; an article named by any other authority is a stray mention (an MEF
narrative page discussing art. 11 comma 5-bis must not gain a path level) and is
dropped rather than invented.

**Placement prefers the volume's own `INDICE`,** because it states the ministry
ranges outright — no argument about which string is exclusive to whom, and no
inheritance across pages printing no marker. `lib/indice.py` maps printed folios
onto PDF pages and the report says which source was used (`ministry: INDICE …` vs
`ministry: page furniture`). Agreement between index and headers on 2025 vol. II
(966pp) and 2023 vol. II (576pp) is total.

Three `INDICE` traps, each of which produced a wrong answer:

- **The heading is letterspaced in 2024** — `I N D I C E`, so a plain substring
  test for `INDICE` rejects it and 2024 was recorded as having no index at all.
  Matched with whitespace squeezed out against a spacing-tolerant pattern.
- **A parse must be validated before it is trusted**, because a wrong parse is
  silent. On 2018 vol. I the parser reads the "Volume I" heading as a ministry and
  produces `MEF block@1` — out of document order and entirely plausible. Rejected
  unless: ≥3 ministry blocks, strictly increasing folios, a folio map covering the
  volume, and at least one block inside this volume's own span.
- **The folio offset is the *mode* of `folio − page`**, not a linear fit. Page 3
  of 2023 vol. II reads `- 3 -`, so fitting across that discontinuity gives a
  slope of 2.6 instead of 1 and maps every boundary to nonsense.

The index is unusable in 11 of 22 volumes: 2017, 2019 and all three 2021 have none
at all, while 2012, 2016 and 2018 have one whose ministry rows carry no page
number — those are rejected rather than filing tables under the wrong ministry.
The article always comes from the table's own qualifier line, since the index does
not subdivide the Dogane.

### 2024: measured, and wrong

`--year 2024 --volume both --dry-run` writes **50 manifest entries, 48 files**
after `resolve_repeats()` drops 2. The correct answer is **31 + 35 = 66** plus
the Dogane annex, so it is wrong in *both* directions at once. This is the year
the README uses as its worked example, and it had never been run.

| | vol. I (1016pp) | vol. II (1260pp) |
|---|---|---|
| family | 2 ✓ | **3 ✗** — it is family 1 |
| ground truth | **31** codes, index at pdf p70 | **35** codes, bookmark tree |
| detected | **10** — `A1 A2 A4 B6 B7 M1 N1 O1 P1 P2` | 35 real + **5 phantom** `O1 M2 N2 O2 P2` |
| why | **335 of 1016 pages garbled**, so the header scan cannot see them; `B5` lost to the wrapped-row trap (§4) | the Dogane annex read as family-2 codes (§4) |
| trim | fired on a year not in the list above: `A2 -5p`, `A4 -17p`; `B7` refused a 338pp trim as over the cap | — |
| index check | **3/8** agree; `A1` 498/497, `M1` 10/9, `N1` 6/5, `O1` 3/2 — the off-by-ones are the trim | vocabulary truncated to 12/35 |

Two more recorded facts, both useful as regression assertions:

- **No 2024 table straddles the volume join.** Tables stop at printed 1000 (`P2`),
  the folios run continuously (vol. I ends ~1010, vol. II restarts at 1011), so
  `stitch()` must **not** fire here. The general `INDICE` at vol. II p3 is the
  map: MAE *Tabelle » 66* (vol. I), MEF *Tabelle » 1091*, Dogane *Tabelle » 1707*.
- **2024 vol. I has only three `Pagina vuota` bookmarks**, so its header traps
  carry the whole detection — there is no fallback.

**Weak files (≤10 tables): 15 of 28.** The 2012–2017 volumes are the weak
cluster. (2024 vol. I also lands at 10, but it is not *weak* — it is broken, see
below; do not fold it into this count.) Deferred years are on disk (13
directories: 2001–2008, 2010, 2012–2015) and their state is known, not unknown:

- 2001–2010: no text layer (scanned), ~52 chars/page, 13 files.
- 2009, 2011: sourced from the SIPRI mirror, not the Camera archive.
- 2012: does not print a parseable page number; the footer runs the number into
  the text (`Camera dei Deputati — 100 — Senato della Repubblica`). No join
  attempted.
- 2013: same, plus no bookmarks.
- 2014 vol. I, 2015 (both volumes): yield 1 table each; should become an
  explicit *unsupported* list rather than emitting a misleading split.
- 2014 vol. II, 2016, 2017: the weak cluster.

**Bookmarks: exactly 6 volumes, 197 codes** — `2021_TOMO_I` (31),
`2023_VOLUME_I` (31), `2023_VOLUME_II` (34), `2024_VOLUME_II` (35),
`2025_VOLUME_I` (16), `2025_VOLUME_II` (50). `2016_VOL_II`, `2018_VOL_I`,
`2023_VOL_III` and `2024_VOLUME_I` have outlines but only `"Pagina vuota"`,
annex titles and container entries.

### Two robustness rules that already cost a run

- **Guard `build_manifest` per volume.** One unreadable volume must cost that
  volume, not the remaining twenty-one.
- **Write the artefacts before the summary, and guard the summary.** A formatting
  bug in a human-readable report once raised a `TypeError` *upstream* of the data
  and took the run with it: volumes 21 to 43 were never attempted, and the volume
  that raised lost both its manifest row and its PDFs. Nothing cosmetic may sit
  upstream of the data. The same reasoning put `is_pasted()` inside the page scan
  rather than in a pass of its own.

### What the audit found (2025)

`VerifyTables.py` run over the 81 exported 2025 tables: **55 PASS, 8 FAIL, 18
WARN, 0 SKIP.** The eight failures, all corroborated by reading the source
pages by hand:

| file | finding |
|---|---|
| `MG8` | 20pp holding **ten** tables (MG8, MG10…MG18) |
| `M` | holds `TAB N` on pp 292-312 |
| `O` | holds `TAB P` on pp 58-76, then 63pp of narrative |
| `MT7` | holds `TAB MT13` on pp 3-4, plus a trailing blank |
| `P` | 6pp of Dogane relation prose; `Tab. P` is a mention |
| `N` | 3pp of Dogane annex prose |
| `E` | 222pp, last page carries a different document's stamp |
| `F1` | 10pp, two trailing `PAGINA BIANCA` |

Two of the eight are **stale output, not defects**: `E2025.PDF` and
`F12025.PDF` were written 2026-10-03 10:43 and the trailing-trim fix landed
2026-10-03 14:14, four hours later. Current step 1 produces 221 and 8, which
the manifest confirms. Re-running step 1 clears both with no code change.

Also worth knowing: `MG8`, `P`, `N` and `M`/`O`/`MT7` all sit in the Dogane
annexes, which announce their tables in **prose** rather than an index, so
nothing in the archive names them and only repetition can find them.

That audit predates the ministry tree, and two of its eight have since been
closed by it (`M` and `O` no longer hold `TAB N` / `TAB P`, because the article
level separates them). A ninth finding was never in it, because no check could
see it: **the Dogane appendix sat inside its base table** (§4, "An appendix is a
different table"), so `M` was 291pp rather than 273pp and six tables carried a
second one within them. The current run over **119 files** is
**111 PASS, 0 FAIL, 8 WARN, 0 SKIP**.

### Outstanding

- **2016 vol. II leaves 358 of 768 pages unattributed, and they are tables.**
  Found by `Out/TRASH/`, not by the verifier: `Out/TRASH/2016/2016_LXVII_n5_VOLUME_II_p286-643.PDF`
  is 358 pages of Dogane data printing `TAB. M` on 278 of them, `TAB. N` 19,
  `TAB. O` 28, `TAB. P` 20. The header scan detects only the *suffixed* series
  (`M1`…`P2`, from p644) and never the four bare codes, so the main art. 1
  comma 2 allegato is absent from the output entirely while the summary tables
  beside it are exported. The run is reported as `cut-at-boundary` because that
  is what opened it (UNKNOWN/PP stops at p286 on the INDICE block edge) — the
  reason names the boundary, and the TSV is where the reader finds out the block
  is 358 pages of tables rather than prose. The bare-letter series *is* found in
  2025 (`DOG/A1C2` carries `M`, `N`, `O`, `P`) and in 2024 it is misread as
  `M2`/`N2`/`O2`/`P2` (see §4), so the codes themselves are not the obstacle —
  2016 loses them because nothing else in that volume witnesses them. Worth a
  detector pass of its own, since the bare series is the *main* art. 1 comma 2
  allegato and its absence is invisible to every check that only looks at the
  manifest.
- **2024 is measured and failing — fix it before extending anything else.** Ten
  of 31 tables in vol. I, five phantom tables in vol. II, family misdetected as
  3, `A4` trimmed by 17 pages. Every cause is named in §4 and in *2024: measured,
  and wrong* above. It is also the year the README advertises, so it is the first
  thing a reader will run.
- **`INLINE_CODE` cannot match a two-digit code, so ten 2025 tables are
  invisible to step 1.** The pattern ends in `([A-Z]{1,3}\d?)` — one digit at
  most — and its trailing `\b` then does the rest. For `TAB. MT13` the greedy
  letters take `MT`, `\d?` takes `1`, and `\b` cannot hold between `1` and `3`;
  every shorter reading fails it too, so the match is abandoned outright.
  Measured: `'TAB. MT7' -> ['MT7']`, `'TAB. MT13' -> []`. `LEADING_CODE` and
  `CODE_RE` share the defect. Consequence, found by `VerifyTables.py`: under the
  old code-only output layout, `Out/PDF/MG8/MG82025.PDF` was twenty pages holding
  **ten** tables — MG8, MG10…MG18, two pages each — and `Out/PDF/MT7/MT72025.PDF`
  held MT13 on pp 3-4. None of those codes appears in any index or bookmark tree,
  so nothing else in the pipeline can notice.
  The fix is `\d{0,2}` plus a negative lookahead instead of `\b`, which is what
  `VERIFIER_INLINE` in `VerifyTables.py` already does; **the generator is
  deliberately untouched for now.** Do not "fix" the verifier back to the
  shared pattern.
  *Partly mitigated since:* the ministry tree now files them separately —
  `DOG/A11C5BIS/MG10…MG18` each get their own file, 2pp apiece — because the
  Dogane annex is keyed by article rather than by code. The pattern is still
  wrong and still worth fixing.
- 2015 and 2014 vol. I should join 2001–2010 in an explicit *unsupported* list
  rather than emitting a misleading 1-table split.
- The 15 weak files (≤10 tables) need either a family-specific detector or an
  honest partial-coverage marker in the output.
- Install a JRE and run step 2 for real — nothing about its output is verified.
- OCR decision, if garbled tables are ever to yield data.
- Resolve the 2021 volume-2 filename collision: `2021_LXVII_n5_TOMO_II.pdf` and
  `2021_LXVII_n5_VOLUME_II.pdf` are two different documents (844 and 1070 pages)
  claiming the same volume, so `parse_volume()` refuses to guess.
- Extend coverage to the deferred years, weakest first — the ordering of the work
  is set by §1, not by the age of the report.
- **Recover the missing page numbers in the 2016 and 2018 indices.** Their
  ministry rows carry none, so validation rejects the index and the volume falls
  back to page furniture. The numbers are on the same page; reading them would
  bring four more volumes onto the index.
- **Explain the `MEF/II` key collisions** reported in 2020 vol. II and
  2024 vol. 2: two detections landed on one (authority, article, code) and one
  was dropped. Either a genuine duplicate or a placement error; undecided.
- **Enumerate the 2024 vol. II Dogane annex codes.** They are sampled every 15th
  page and flagged as not yet enumerated, so §4's annex description is partial.
- The trailing-page check only runs where the stamp is legible or stable. It
  fires on 2025 I, 2021 I and 2019 I; **2019 vol. II, 2023 vol. III and the
  family-3 volumes carry no stamp at all** (0 stamped pages of 1004 and 518
  respectively), so nothing is checked there. Those volumes are exported
  differently and would need a second kind of witness.