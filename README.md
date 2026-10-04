# **ArmTrace — Data Pipeline**

Two Python scripts that extract and structure Italian military export data from the government annual reports published under **Law 185/1990** (Article 5, Law 9 July 1990 n. 185).

> Working guidance for coding agents lives in [AGENTS.md](./AGENTS.md):
> invariants, detection traps, disproven approaches and archive coverage.

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
├── PDF/<tabella>/<tabella><anno>.PDF   # one PDF per table  (step 1)
└── CSV/<tabella>/<tabella><anno>.csv   # one CSV per table  (step 2)
```

Neither folder is checked in: `reports_185_1990/` and `Out/` are rebuilt from
`manifest.tsv` and the two scripts. The repository tracks only the scripts, the
manifest, `download_185.sh`, `SOURCES.md` and `AGENTS.md`.

The folder name is the **reporting year** covered by the report, not the year it was published. Nothing in the scripts hardcodes a year: step 1 reads it from the `reports_185_1990/<anno>/…` path, and both steps key their output on it as a **filename suffix**, `Out/PDF/AA/AA2023.PDF` → `Out/CSV/AA/AA2023.csv`. One folder per table therefore holds one file per reporting year, and step 2 selects the year with `--year` (it matches the suffix, so the suffix and the flag must agree).

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
`pages`, `url` — with the **source URL last**; the script rejects any row whose
last field is not an `http(s)` URL before creating anything. An earlier
five-column download list and a separate six-column inventory were both passed
to it by mistake, so the two are now one file. See
[AGENTS.md](./AGENTS.md#2-do-not-break-invariants) for the invariant.

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

Per-family detection rules and the traps behind them are in
[AGENTS.md](./AGENTS.md#4-regex-and-detection-traps).

---

## **Scripts**

### **Reports2PDFTables.py**

Splits a report volume into one PDF per table, written to
`Out/PDF/<tabella>/<tabella><anno>.PDF`. For each table it finds the first page;
the last page is one before the next table starts.

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

**A year is processed as a whole.** Every volume named in one run is read before
any of them is written, because a table may run from one volume into the next.
A single volume (`--volume 1`) still works, but a table cut at that volume's
end can only be reported as cut — see *Tables that cross a volume boundary*.

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

### How trailing pages are removed

Deriving a table's end from "the page before the next table starts" over-runs
whenever the source document brought junk with it. Each table was exported as a
document of its own and pasted into the volume, so every page keeps that
document's own pagination, printed as a rotated stamp in the right margin:
**"Pagina N di X"**. The volume folio (`– 1035 –`) runs continuously across
tables and says nothing about boundaries; the stamp is the only witness of a
table's real length.

So if the first page reads `Pagina 1 di X`, the final page must read
`Pagina Y of X`. A different total, or no stamp at all, means the page came
from somewhere else and everything after the last agreeing page is dropped.
`Y` is normally `X` but need not be: **F1 is the first half of table F, which
straddles the two volumes of 2025** (see below), so its 8 pages in volume I end
at `Pagina 8 di 70` and only the totals are compared, never the page numbers.

Verified on the three reported cases and two more found the same way: 2025 I `E`
222 → **221** pages and `F1` 10 → **8** (each dropped a document of its own —
a one-pager and two `PAGINA BIANCA`), 2021 I `E` 6 → **5** and 2019 I `E` 35 →
**34** (a separate one-page document each). Everything else in those volumes
was unchanged. The 2021 and 2019 cases are confirmed independently by the index
page, which lists those E tables as 5 and 34 pages. The full case table is in
[AGENTS.md](./AGENTS.md#6-verification-and-archive-coverage).

It works on garbled text too, because the numbers need not be *read*, only
compared: on subsetted fonts with no `ToUnicode` the stamp comes back as Private
Use Area characters, but the subsetter gave one codepoint per character, so two
pages of the same document still agree character for character on the total and
a page from another document does not. **2025 E is garbled on all 221 pages and
still trims correctly.**

Three guards, because this deletes pages:

- the stamp must be corroborated on at least two pages of the table;
- a table never loses more than `MAX_TRAILING_PAGES` (25);
- if a dropped page's document **carries on past the table**, it is the first
  page of a table no detector found, so nothing is dropped and a note says so —
  trimming it would take those pages out of the split entirely, since no table
  claims them.

Dropping pages loses nothing from the split: they are still in the volume and
still inside the next table's span, so they are written with that table. This
only shortens one output file.

### Tables that cross a volume boundary

The volumes of a year are consecutive parts of one document, numbered
continuously, and a table may straddle the join. **2025 Tabella F1** is the case
in point: printed pages 1035–1042 close volume I, printed 1043–1104 open volume
II, and the margin of the last page reads `Tabella F1 / Pagina 70 di 70` — one
table of seventy pages. `Out/PDF/F1/F12025.PDF` is that table.

The split used to be per volume, so the volume holding the tail wrote its file
over the one holding the head, and only the tail survived. Every volume of a
year is now read before any is written, and a table is written once, from all
the volumes it spans.

`stitch()` only claims the pages when three independent things agree, because
the next volume opening on something *else* is the normal case (2023 vol. III
starts the Agenzia delle Dogane relations, where vol. II ended on Tabella UE):

| test | rejects |
|---|---|
| the cut table must reach the last page of its volume that carries a printed number — blanks and covers carry none | a table that simply ends there |
| the next volume's first numbered page must be the successor of that page's, and the run taken must stay consecutive | a volume that restarts its own numbering (2019) |
| the pages taken must carry the running header the cut table ended on | a new section (2023 Dogane vs UE's MEF header) |

The last test works on garbled pages too, which matters because that is where
a split table hides: each Private Use Area codepoint stands for exactly one
original character, so two pages carrying the same header produce
byte-identical strings, in either volume.

Two details are deliberate:

- **`TAIL_PAGES = 2`.** A table often closes on a differently shaped page, and
  F1's last page is its `Totale autorizzazioni` sheet — dropping it would lose
  the figures. Beyond two such pages the pages are more likely to be the next
  table's.
- **An unreadable footer ends a run** rather than letting it bridge a gap in
  the numbering: a page whose number cannot be checked may hide anything.

Nothing is guessed silently. The console prints the join, the JSON manifest
records it under `continued`, and a table that reaches the end of a volume
without a continuation is left alone rather than padded with front matter.

Verified: `--year 2025 --volume both` writes `F1/F12025.PDF` with **70 pages**,
printed 1035 → 1104, first page `Pagina 1 di 70`, last `Pagina 70 di 70`. Every
other volume boundary in 2012, 2016, 2019, 2021, 2022 and 2023 was checked the
same way: **2025 F1 is the only split in the archive**, and none of them
produces a false join. Two reach the numbering test and are turned away by the
header test — 2022 vol. II and 2021 VOLUME II both open on a new section. The
full reasoning is in [AGENTS.md](./AGENTS.md#6-verification-and-archive-coverage).

### A code in two volumes is not always a split

One code means one file, so a repeat has to be resolved rather than joined.
**2021 prints the MAE tables twice** — tom. I and tom. II carry the same
`A1`…`P2`, and VOL. II prints seven of those codes a third time — which the
per-volume split used to resolve by overwriting, so the output was a coin toss
on which copy survived. `resolve_repeats()` now keeps the best-attested copy
(bookmark > header run > title repeat, earliest volume breaking a tie) and
reports what it dropped:

```
duplicate  : 27 code(s) found in another volume of the same year and not a
             split; kept copy wins
    A1    p71-365 (295 pp) dropped, kept from 2021_LXVII_n5_TOMO_I
```

Only when the two spans are *consecutive* — the first ends where its volume
ends, the second starts where its volume starts, and the volumes are
neighbours — are both kept and joined, which is the readable-halves version of
a split. Anything else would invent a table out of two unrelated ones.

**Status:** works. Verified across all 28 supported volumes — **479 tables,
zero failures**, every volume producing a manifest.

The pipeline is currently developed against the **2016** reports. Earlier years
are deferred, not broken — see [Archive coverage](./AGENTS.md#archive-coverage)
for the per-year table.

### **IndividualTables2SQL.py**

Reads the per-table PDFs from step 1 and converts them to CSV via tabula-py.

```bash
python IndividualTables2SQL.py --year 2023
python IndividualTables2SQL.py --year 2023 --input-dir Out/PDF --output-root Out/CSV
```

| Flag | Meaning |
|---|---|
| `--year` | year to process (default `2024`); matches the filename suffix |
| `--input-dir` | per-table PDFs (default `Out/PDF`) |
| `--output-root` | CSV root (default `Out/CSV`) |
| `--base` | root for all paths (default `.`) |
| `--sep` | field separator (default `;`) |
| `--encoding` | CSV encoding (default `utf-8-sig`) |

`split_stem()` splits the filename stem into table code and year (`AA2023` →
`AA`, `2023`); `detect_pdf_type()` uses the code, `table_semantics()` maps it to
a family and a human label. No archive code ends in four digits, so the trailing
digit group is always the year.

**Why CSV and not XLSX.** The archive holds tables Excel cannot represent.
`detect_pdf_type()` used to key on `TAB_N1`, `TAB_O1`, `TAB_A2` … names that
appear **nowhere in the reports**, so every table took the unknown-type branch;
that branch wrote **one sheet per extracted fragment**, producing ~4000-sheet
workbooks past Excel's 255-sheet limit — the hang previously recorded here (an
early local run, under the old `Out/XLS/` layout, left 16 workbooks for 19 PDFs,
with `TAB_A1`, `Tabella_AA` and `Tabella_EE` never converted). Fragments are
concatenated into a single table, and the output is CSV, so **none of Excel's
ceilings apply**: 255 sheets, 1048576 rows and 16384 columns are all workbook
and sheet properties, and a CSV of any length is one file that pandas, sqlite
or MySQL read directly. Verified: 4000 fragments → one file, all 4000 rows
retained, ragged fragment widths filled with nulls rather than dropped.

Two CSV conventions are deliberate, not defaults:

- **`;` separator.** Italian figures use a comma decimal separator, so
  `1.234,56` in a comma-separated file reads as two columns. `;` is also what
  Excel itself uses under Italian regional settings, and what
  `LOAD DATA INFILE ... FIELDS TERMINATED BY ';'` wants.
- **`utf-8-sig`.** The BOM is what stops Excel on Windows rendering
  `Paese` / `Movimentazioni` / `Valore in €` as mojibake. It also prefixes the
  first header when a reader asks for plain `utf-8`, so pass
  `--encoding utf-8` for a database destination.

**Status:** cannot be run end to end here — **no JRE is installed**, and
tabula-py shells out to Java. The parts changed in this pass were verified
directly: the dispatch fix, the fragment concatenation, the CSV writer
(1.2 M rows past Excel's ceiling → one file, round-tripped intact), and the
flattened layout (one folder per table, year as a filename suffix). See
[Environment state](./AGENTS.md#3-environment-state).

A note on the earlier `.XLS` workaround, now moot: pandas refuses a non-`.xlsx`
name for the openpyxl engine (`Invalid extension for engine 'openpyxl': 'XLS'`),
so that branch wrote a workbook to a `.xlsx` sibling and renamed it, leaving xlsx
bytes under an `.XLS` name. Emitting CSV removes the problem at its root rather
than renaming around it — there is no engine that objects to a `.csv`, and no
row, sheet or column ceiling to work against.

### **Requirements**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

tabula-py shells out to Java, so a JRE (e.g. Temurin 17) must be on `PATH` or
reachable via `JAVA_HOME`. **Not installed in this environment.**

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

No PDF, Excel, CSV or other binary is tracked by git, and **nothing is in Git LFS**.

`.gitignore` excludes `*.pdf`, `*.xls`, `*.xlsx` and `*.csv` — that is, the source
reports in `reports_185_1990/` (51 PDFs, ~1.6 GB) and everything generated under
`Out/`. `.gitattributes` deliberately carries **no** `filter=lfs` rule for them; it
only documents why. LFS was never a solution here: it does not stop the files being
committed, it just moves the bytes into GitHub's metered LFS storage/bandwidth
quota, and this repository's binaries total well over 1 GB.

There is no special case for the 2024 volumes. Earlier revisions had only those two
files in LFS while the other 49 were plain blobs; that split has been removed, so
the 2024 PDFs are ignored exactly like every other source report and are
regenerated from their `manifest.tsv` rows like the rest.

Verified on the current tree: `git ls-files '*.pdf' '*.xls' '*.xlsx' '*.csv'` returns
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

Engineering work is tracked in [AGENTS.md](./AGENTS.md#outstanding), which is
authoritative for it. What is left is the product roadmap:

- Process the remaining years — see [Archive coverage](./AGENTS.md#archive-coverage) for what is deferred and why
- Populate a MySQL db
- Create an LLM tool that converts natural language requests into SQL queries
- Create a chatbot enhanced with that tool, allowing the DB to be interrogated in natural language
- Participatory workshop for the design of the interface

---

## **Notes**

- Input data is sourced from publicly available government reports under Italian Law 185/1990 (annual reports on military exports).
- This pipeline is the technical foundation of the ArmTrace civic transparency platform.