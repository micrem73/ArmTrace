# AGENTS.md — operational contract for coding agents

Invariants that are cheap to state and expensive to rediscover, the detection
traps that have each cost a debugging cycle, the approaches already disproven,
and the measured coverage per year.

Human-facing description of the pipeline — what it does, how to install and run
it, why the design is what it is — is in [README.md](./README.md). The files are
complementary rather than duplicated. Where they overlap, **this file is
authoritative** for the coverage table, the regex traps and the disproven list.

---

## 1. Scope and target

**2016 is the working baseline. 2017+ is next. Pre-2016 is deferred, not
broken.** Nobody should "fix" a deferred year: those numbers were measured, they
are recorded below, and they are not regressions.

| fact | 2016 |
|---|---|
| volumes on disk | `reports_185_1990/2016/2016_LXVII_n5_VOLUME_{I,II}.pdf` |
| vol. II | family 3, 768pp, **34 tables** |
| vol. I | family 2, 716pp, 5 tables |
| printed page numbers | both volumes print a parseable folio |
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
- **Output paths are `<tabella>/<tabella><anno>`** — one folder per table, year
  as suffix. Both steps must agree on the suffix; step 2's `--year` matches it.

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
- **The 2012 index prints digit one as a capital `I`** (`Tabella DI`,
  `Tabella Gl` for `D1`/`G1`); `normalise_digit_one()` folds these back.
- **Several bookmark trees carry container entries with no page** (`'araba.pdf'`,
  `'0001.pdf'`). Skip them.

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
| garbled | subset font, no `ToUnicode` | 2023 I 284pp, 2025 I 356pp, 2018 II 299pp, 2025 II 141pp, 2020 I 174pp (~1250pp) |
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

### Archive coverage

Authoritative copy. Step 1 works for all 28 text-bearing volumes: **479 tables,
zero failures**, every volume producing a manifest.

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
| 2025 | II / I | 3 / 2 | 1048 / 1048 | **65** / 16 | |

**Weak files (≤10 tables): 15 of 28.** The 2012–2017 volumes are the weak
cluster. Deferred years are on disk (13 directories: 2001–2008, 2010, 2012–2015)
and their state is known, not unknown:

- 2001–2010: no text layer (scanned), ~52 chars/page, 13 files.
- 2009, 2011: sourced from the SIPRI mirror, not the Camera archive.
- 2012: does not print a parseable page number; the footer runs the number into
  the text (`Camera dei Deputati — 100 — Senato della Repubblica`). No join
  attempted.
- 2013: same, plus no bookmarks.
- 2014 vol. I, 2015 (both volumes): yield 1 table each; should become an
  explicit *unsupported* list rather than emitting a misleading split.
- 2014 vol. II, 2016, 2017: the weak cluster.

**Bookmarks: exactly 5 volumes, 162 codes** — `2021_TOMO_I` (31),
`2023_VOLUME_I` (31), `2023_VOLUME_II` (34), `2025_VOLUME_I` (16),
`2025_VOLUME_II` (50). `2016_VOL_II`, `2018_VOL_I` and `2023_VOL_III` have
outlines but only `"Pagina vuota"` and annex titles.

### Outstanding

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
- The trailing-page check only runs where the stamp is legible or stable. It
  fires on 2025 I, 2021 I and 2019 I; **2019 vol. II, 2023 vol. III and the
  family-3 volumes carry no stamp at all** (0 stamped pages of 1004 and 518
  respectively), so nothing is checked there. Those volumes are exported
  differently and would need a second kind of witness.