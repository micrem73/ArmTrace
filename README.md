# **ArmTrace — Data Pipeline**

Two Python scripts that extract and structure Italian military export data from the government annual reports published under **Law 185/1990** (Article 5, Law 9 July 1990 n. 185).

---

## **Structure**

Source reports and generated files are kept apart. `reports_185_1990/` holds only the original PDFs, everything produced by the pipeline goes to `Out/`.

```
reports_185_1990/                 # source reports, one folder per reporting year
├── 2001/ … 2025/                # 25 years, 51 PDFs (2009 and 2011 from the SIPRI mirror)
│   └── <anno>_LXVII_nN_VOLUME_<X>.pdf   (or _TOMO_X / _DOCUMENTO_UNICO)
├── manifest.tsv                 # year, leg, num, volume, filename, pages, source URL
├── download_185.sh              # reproduce the download
└── SOURCES.md                   # archive structure, legislature→year mapping, gaps

Out/                             # generated locally, NOT in git (see "Binary files")
├── PDF/<tabella>/<anno>/<tabella>.pdf   # one PDF per table  (step 1)
└── XLS/<tabella>/<anno>/<tabella>.xlsx  # one workbook per table  (step 2)
```

Neither folder is checked in: `reports_185_1990/` and `Out/` are rebuilt from
`manifest.tsv` and the two scripts. The repository tracks only the scripts, the
manifest, `download_185.sh` and `SOURCES.md`.

The folder name is the **reporting year** covered by the report, not the year it was published. Both `reports_185_1990/` and `Out/` are keyed by it, so nothing in the scripts hardcodes a year: it is parsed from the `reports_185_1990/<anno>/…` path.

Adding a year means dropping its PDFs into `reports_185_1990/<anno>/` — no code change required.

> The 2024 volumes keep their original filenames
> (`lxvii_3_volume 1_442452.pdf`, `lxvii_3_volume 2_442453.pdf`) rather than the
> `<anno>_LXVII_nN_VOLUME_<X>.pdf` convention. Both are understood by
> `parse_volume()`, and `manifest.tsv` records the real names so that
> `download_185.sh` recognises them and skips them instead of re-fetching. This is
> the only difference from the other years — they are sourced and stored exactly
> like the rest.

`manifest.tsv` is the single input to `download_185.sh` and must hold exactly
seven tab-separated fields per row — `year`, `leg`, `num`, `vol`, `file`,
`pages`, `url` — with the **source URL last**. An earlier five-column download
list and a separate six-column inventory were both passed to the script by
mistake, which made the page count land in the URL field and every fetch fail
with `URL using bad/illegal format`. The two are now one file, and the script
rejects any row whose last field is not an `http(s)` URL before creating
anything.

---

## **Table code families**

The archive contains **three** mutually exclusive naming schemes for tables. A
volume uses exactly one. This matters because step 2 dispatches on the code,
and because the three schemes need different header detection.

| family | scheme | example codes | meaning |
|---|---|---|---|
| **1** | art. 27, double letter | `AA` `AA1` `BB` `UE` `FG` `GF` | MEF summary tables |
| **1** | art. 27, MAE detail | `MG1`…`MG9` `MT1` `MT7` `LGP` | global licences, only from 2025 |
| **1** | charts | `NN` `OO` `PP` `GF` | percentage breakdown charts |
| **2** | `A1` … `P2` | `A1` `B7` `C1` `F2` `P2` | MAE per-operator / per-country detail, 31 codes |
| **3** | art. 27, single letter | `A` `B` `D` `E` `G` `J` `Q` | earliest layout, e.g. 2012 vol. I |

Family 2 is a **closed set of 31 codes**, confirmed by the bookmark trees of
2021 tom. I, 2023 vol. I and 2025 vol. I, which agree on every code. Family 1
and 3 codes match `^[A-Z]{1,3}\d?$`.

The 2012 index prints the digit one as a capital `I`, so it lists `Tabella DI`
and `Tabella Gl` where the real codes are `D1` and `G1`; `normalise_digit_one()`
folds these back.

---

## **Scripts**

### **Reports2PDFTables.py**

Splits a report volume into one PDF per table. For each table it finds the
first page; the last page is one before the next table starts.

```bash
./Reports2PDFTables.py --year 2023 --volume 2
./Reports2PDFTables.py --year 2024 --volume both
./Reports2PDFTables.py --all                  # every text-bearing volume
./Reports2PDFTables.py --year 2023 --dry-run  # report only, write nothing
./Reports2PDFTables.py --report reports_185_1990/2023/2023_LXVII_n2_VOLUME_II.pdf
```

| Flag | Meaning |
|---|---|
| `--year` | reporting year — **required**, 25 years are present |
| `--volume` | `1`, `2` or `both` (default `2`) |
| `--report` | explicit PDF path, repeatable |
| `--all` | every volume in `reports_185_1990/` |
| `--base` | root for all paths (default `.`) |
| `--out` | output root (default `<base>/Out`) |
| `--dry-run` | print the coverage report, write nothing |
| `--manifest` | append a JSON manifest to this path |

Volume detection handles both naming schemes (`volume 1`, `VOLUME_I`, `TOMO_II`)
and treats `DOCUMENTO_UNICO` as a single volume. It refuses to guess when a
year has two files claiming the same volume — currently **2021 volume 2**, where
`2021_LXVII_n5_TOMO_II.pdf` and `2021_LXVII_n5_VOLUME_II.pdf` are two
*different* documents (844 and 1070 pages) with colliding names.

**How tables are found.** Two independent detectors, **unioned rather than
ranked**:

- **embedded** — the PDF bookmark tree, present in only 5 of 43 volumes;
- **header** — the table code printed in the page header.

Bookmarks are human-authored and win on a page conflict, but they are *not* a
superset: on 2025 vol. II the outline omits `MG1`–`MG9` and `MT1`/`MT7`, which
the header scan finds. Conversely the header scan invents codes from prose,
which is why the bookmark wins where they disagree.

Two further detectors fill gaps: an **index** page listing codes with their
page *counts*, used to cross-check derived spans; and the table **title**
repeated through the body, for family 3 where the codes appear only on the
index page.

**Status:** works. Verified across all 28 supported volumes — **479 tables,
zero failures**, every volume producing a manifest. Coverage per year is in
[the log below](#log-for-the-next-agent).

### **IndividualTables2SQL.py**

Reads the per-table PDFs from step 1 and converts them to Excel via tabula-py.

```bash
python IndividualTables2SQL.py --year 2023
python IndividualTables2SQL.py --year 2023 --input-dir Out/PDF --output-root Out/XLS
```

| Flag | Meaning |
|---|---|
| `--year` | year to process (default `2024`) |
| `--input-dir` | per-table PDFs (default `Out/PDF`) |
| `--output-root` | workbooks root (default `Out/XLS`) |
| `--base` | root for all paths (default `.`) |

`detect_pdf_type()` reads the table code from the filename stem;
`table_semantics()` maps it to a family and a human label.

**Status:** cannot be run end to end here — **no JRE is installed**, and
tabula-py shells out to Java. The parts changed in this pass were verified
directly (see the log). Two fixes:

- `detect_pdf_type()` keyed on `TAB_N1`, `TAB_O1`, `TAB_A2` … names that appear
  **nowhere in the reports**. Every table took the unknown-type branch.
- That branch wrote **one sheet per extracted fragment**, producing ~4000-sheet
  workbooks past Excel's 255-sheet limit — the hang previously recorded here
  (a local run left 16 workbooks in `Out/XLS/` for 19 PDFs in `Out/PDF/`, with
  `TAB_A1`, `Tabella_AA` and `Tabella_EE` never converted). Fragments are now
  concatenated into a single `Data` sheet, so the sheet limit no longer
  applies; only Excel's 1048576-row limit does, and exceeding it is reported
  rather than silently truncated. Verified: 4000 fragments → one sheet, all
  4000 rows retained.

### **Requirements**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

tabula-py shells out to Java, so a JRE (e.g. Temurin 17) must be on `PATH` or
reachable via `JAVA_HOME`. **Not installed in this environment.**

---

## **Log for the next agent**

### Step 1 is solved; step 2 is not yet run

The split works for all 28 text-bearing volumes. Step 2 has never been executed
successfully because Java is absent — treat its output as unverified.

### Coverage, measured

| year | volume | family | pages | tables |
|---|---|---|---|---|
| 2012 | III / II / I | 1 / 2 / 3 | 608 / 1268 / 1242 | 1 / 6 / 16 |
| 2013 | DOCUMENTO_UNICO | 2 | 1672 | 15 |
| 2014 | II / I | 2 / 3 | 656 / 664 | 3 / 1 |
| 2015 | I / II | 2 | 1032 / 716 | 1 / 1 |
| 2016 | II / I | 3 / 2 | 768 / 716 | **34** / 5 |
| 2017 | II / I | 2 | 746 / 748 | 8 / 5 |
| 2018 | I / II | 2 | 742 / 764 | 22 / 8 |
| 2019 | I / II | 2 / 3 | 826 / 1004 | 23 / 10 |
| 2020 | II / I | 3 / 2 | 958 / 732 | **41** / 10 |
| 2021 | TOMO_I / TOMO_II / VOL_II | 2 | 793 / 844 / 1070 | 31 / 27 / 8 |
| 2022 | II / I | 3 / 2 | 1088 / 1020 | **40** / 23 |
| 2023 | II / I / III | 1 / 2 / 2 | 580 / 946 / 518 | **35** / 31 / 5 |
| 2025 | II / I | 3 / 2 | 1048 / 1048 | **65** / 16 |

**Weak files (≤10 tables): 15 of 28.** 2015 (both volumes) and 2014 vol. I
yield 1 table and should be treated as unsupported — no index, no bookmarks,
codes not repeated. The 2012–2017 volumes are the weak cluster.

### Bookmarks: exactly 5 volumes, 162 codes

`2021_TOMO_I` (31) · `2023_VOLUME_I` (31) · `2023_VOLUME_II` (34) ·
`2025_VOLUME_I` (16) · `2025_VOLUME_II` (50).

`2016_VOL_II`, `2018_VOL_I` and `2023_VOL_III` have outlines but only
`"Pagina vuota"` and annex titles.

**Named destinations are a dead end.** 2020 vol. II (522), 2021 vol. II (570)
and 2023 vol. II (1689) carry `JR_PAGE_ANCHOR_*` entries — JasperReports
hyperlink anchors, not per-table — and pypdf resolves most to `None`. Do not
spend time here.

Titles need two patterns, and **neither can use `\b`**: underscore is a word
character, so `TAB_A1` has no word boundary around `TAB`. Use
`(?<![A-Za-z])TAB[ ._]+` for the separated form and a separate `tabella`
alternative for the glued form (`tabellaAA_2025`).

Several bookmark trees carry container entries with **no page**
(`'araba.pdf'`, `'0001.pdf'`, `'0001.pdf'`). Skip them.

### 2025 vol. I is *not* missing bookmarks

It ends at `F1` (p1039–1048, the last 10 pages). `F2`…`P2` live in
**2025 VOLUME_II**, whose outline starts at `F2` (p67). The two volumes
continue each other. Do not "fix" this.

### Three text-corruption classes, all unrecoverable

Roughly **1300 pages** across the archive cannot yield data. Verified, not assumed.

| class | cause | evidence | affected |
|---|---|---|---|
| **no text layer** | scanned images | 2001–2010, ~52 chars/page | 13 files |
| **garbled** | subset font, no `ToUnicode` | 2023 vol. I 284pp, 2025 vol. I 356pp, 2018 vol. II 299pp, 2025 vol. II 141pp, 2020 vol. I 174pp | ~1250pp |
| **ciphered** | `/Identity-H`, no `ToUnicode`, no `/Differences` | 2017 vol. I 287pp, 2022 vol. I 66pp, 2016 vol. I 28pp | ~390pp |

**Garbled — three recovery routes all failed:**

1. `pdftotext` (poppler 22.02) → identical PUA output.
2. `pdffonts` → `uni = no` confirmed on every affected font.
3. fontTools `CharStrings` → `['.notdef', 'uniE019', 'uniE026', …]`. The
   subsetter copied each PUA codepoint into the glyph name, so `uniE019` *is*
   U+E019 renamed. **Only outlines remain.** The `/Encoding` array is 51 real
   entries followed by 94 `.notdef` slots.

**Ciphered** is worse, because the text reads as plausible: `"/LFHQ]D 2SHUDWRUH"`
where the header is `"TABELLA N1 PER OPERATORE"`. It is not a uniform shift
(`2SHUDWRUH` needs −3, `GHI` needs −3, but `6SHGL]LRQL` maps `]` to `)`, not
`I`). The text is emitted by `/Identity-H` fonts with **no mapping table
anywhere in the PDF**.

These pages are digitally rendered, so OCR is technically viable — but OCR is
**out of scope by decision**. They still split correctly, because step 1 needs
only page boundaries. `IndividualTables2SQL.text_status()` reports them per
file so the failure is not misread as a tabula bug.

### Regex traps — each one cost a debugging cycle

- **`\bTAB\b` without a trailing boundary** parses `TABLES` as `TAB`+`LES` and
  `TABLET` as `TAB`+`LET`. Invented codes `LES`, `LET`, `EL`, `ILE`, `ILI`.
- **A separator that spans newlines** reads `"una tabella (F\nG)"` as code
  `FG`. Use `[^\S\n]+`, never `\s+`.
- **Positional windows cannot work.** 8 lines lost 24 of 28 tables in 2019
  vol. I; 24 lines invented `KK1` on 2025 vol. II p263
  (`"come da elencazione sintetica della tabella KK1"`). Every threshold in
  between was wrong somewhere. **Use frequency instead**: a real table repeats
  its code on every page of its run; a prose mention appears once.
- **`MIN_RUN` must apply to style 3 only.** Applied to all styles it dropped
  real one-page tables — 2020 vol. II went 41 → 23.
- **`SEGNALAZIONI` must only be honoured in the top 12 lines.** On 2025 vol. II
  the *table* pages print it in a trailing header block; a plain substring test
  discards every table in the file.
- **Style 1 needs an `ELENCO TABELLE` banner check.** 2023 vol. II p8 is a
  summary page carrying a bare `UE` under `Operazioni disciplinate`, which
  steals the start of the real `Tabella UE` at p543. Resolved by the bookmark.
- **Prose stop-list: do not put `E` or `I` in it.** Both are genuine family 2
  codes; suppressing them cost 4 tables on 2019 vol. I.

### Cross-validation available

Where an index page exists it gives page **counts**, which validate the derived
spans without needing page numbers. 2022 vol. I: `A1` = 401 pages, detected
span 82–482 = 401. Roughly 19/22 spans agree; the mismatches are a useful
error signal, not noise.

### Outstanding

- 2015 and 2014 vol. I should join 2001–2010 in an explicit *unsupported* list
  rather than emitting a misleading 1-table split.
- The 15 weak files (≤10 tables) need either a family-specific detector or an
  honest partial-coverage marker in the output.
- `process_tab_a1` … `process_tab_p2` are dead code now that dispatch is
  family-based. They document the family 2 column layouts and may be worth
  keeping as reference, but nothing calls them.
- Install a JRE and run step 2 for real — nothing about its output is verified.
- OCR decision, if garbled tables are ever to yield data.

---

## **Source reports**

`reports_185_1990/` holds every year available from the official Camera dei Deputati
archive, plus 2009 and 2011 from the SIPRI mirror (see `SOURCES.md`).

Years retrieved: **2001–2025, all 25 years, 51 PDFs, no gaps** — every one of them
has a row in `manifest.tsv`. 2009 (Doc. LXVII n. 3, tomi I–III) and 2011 (Doc. LXVII
n. 5, tomi I–V) are **not** in the Camera archive — its legislature XVI index 404s
and an exhaustive probe of the path space finds nothing — so their `manifest.tsv`
rows point at `sipri.org`, which mirrors the same parliamentary documents. Both are
Senato printings; every other year comes from `documenti.camera.it`. Nothing is
missing from the manifest: the eight SIPRI-sourced rows are ordinary rows, and
`download_185.sh` fetches them like any other.

Doc numbering restarts each legislature, so `n. 1` refers to a different year in a different legislature. Filenames are therefore prefixed with the reference year.

Re-fetch with:

```bash
reports_185_1990/download_185.sh <dest> reports_185_1990/manifest.tsv
```

---

## **Binary files**

No PDF, Excel or other binary is tracked by git, and **nothing is in Git LFS**.

`.gitignore` excludes `*.pdf`, `*.xls` and `*.xlsx` — that is, the source reports
in `reports_185_1990/` (51 PDFs, ~1.6 GB) and everything generated under `Out/`.
`.gitattributes` deliberately carries **no** `filter=lfs` rule for them; it only
documents why. LFS was never a solution here: it does not stop the files being
committed, it just moves the bytes into GitHub's metered LFS storage/bandwidth
quota, and this repository's binaries total well over 1 GB.

There is no special case for the 2024 volumes. Earlier revisions had only those two
files in LFS while the other 49 were plain blobs; that split has been removed, so
the 2024 PDFs are ignored exactly like every other source report and are
regenerated from their `manifest.tsv` rows like the rest.

Verified on the current tree: `git ls-files '*.pdf' '*.xls' '*.xlsx'` returns
nothing, `git lfs ls-files` is empty, and `git check-attr` reports no LFS attribute
for `reports_185_1990/2024/lxvii_3_volume 1_442452.pdf`.

To obtain the files locally:

```bash
reports_185_1990/download_185.sh reports_185_1990 reports_185_1990/manifest.tsv
```

then re-run the two scripts to regenerate `Out/`.

Both sets are regenerable: the source reports from the official URLs listed in
`reports_185_1990/SOURCES.md` and `manifest.tsv`, and `Out/` by re-running the
pipeline scripts. See the notes in `.gitignore` and `.gitattributes`; **do not
re-add LFS filter rules.**

---

## **ToDo**

- Install a JRE and run step 2 end to end for the first time
- Mark 2015 and 2014 vol. I as unsupported
- Improve the 15 weak volumes (≤10 tables detected)
- Decide whether to OCR the ~1300 garbled/ciphered pages
- Resolve the 2021 volume-2 filename collision
- Process the remaining 24 years (2025 is downloaded but has not been through the pipeline)
- Populate a MySQL db
- Create an LLM tool that converts natural language requests into SQL queries
- Create a chatbot enhanced with that tool, allowing the DB to be interrogated in natural language
- Participatory workshop for the design of the interface

---

## **Notes**

- Input data is sourced from publicly available government reports under Italian Law 185/1990 (annual reports on military exports).
- This pipeline is the technical foundation of the ArmTrace civic transparency platform.