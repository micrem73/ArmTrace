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

---

## **Scripts**

### **2024relations2IndividualTables.py**

Splits a report volume into one PDF per table. It locates the table index (`ELENCO TABELLE SEGNALAZIONI` from Volume 2 onwards, the `TAB` column in Volume 1), extracts the table names, and writes one PDF per table.

```bash
python 2024relations2IndividualTables.py --year 2024 --volume both
python 2024relations2IndividualTables.py --year 2019 --volume 2
python 2024relations2IndividualTables.py --report reports_185_1990/2024/lxvii_3_volume\ 1_442452.pdf
```

| Flag | Meaning |
|---|---|
| `--year` | reporting year — **required**, 25 years are present |
| `--volume` | `1`, `2` or `both` (default `2`) |
| `--report` | explicit PDF path, skips year/volume detection |
| `--base` | root for all paths (default `.`) |

Volume detection handles both naming schemes (`volume 1`, `VOLUME_I`, `TOMO_II`) and treats `DOCUMENTO_UNICO` as a single volume. It refuses to guess when a year has two files claiming the same volume — which is currently the case for **2021 volume 2**, where `2021_LXVII_n5_TOMO_II.pdf` and `2021_LXVII_n5_VOLUME_II.pdf` are two *different* documents (844 and 1070 pages) with colliding names.

**Status:** does not currently work. Verified against the 2024 Volume II report, it extracts **0 tables**: the page scan is gated on the `"ELENCO TABELLE"` index heading, which actual table pages do not contain, so no page is ever assigned to a table. Untested for any year other than 2024.

### **IndividualTables2SQL.py**

Reads the per-table PDFs from step 1 and converts them to Excel via tabula-py.

```bash
python IndividualTables2SQL.py --year 2024
python IndividualTables2SQL.py --year 2024 --input-dir Out/PDF --output-root Out/XLS
```

| Flag | Meaning |
|---|---|
| `--year` | year to process (default `2024`) |
| `--input-dir` | per-table PDFs (default `Out/PDF`) |
| `--output-root` | workbooks root (default `Out/XLS`) |
| `--base` | root for all paths (default `.`) |

**Status:** does not run to completion. It hangs on `TAB_A1.pdf`, which is missing from `type_mapping` and therefore takes the unknown-type branch, writing ~4000 empty sheets — past Excel's 255-sheet limit. Consistent with this, a local run left 16 workbooks in `Out/XLS/` for the 19 PDFs in `Out/PDF/`: `TAB_A1`, `Tabella_AA` and `Tabella_EE` were never converted. (`Out/` is not in git, so those counts describe a local run rather than committed state.)

### **Requirements**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

tabula-py shells out to Java, so a JRE (e.g. Temurin 17) must be on `PATH` or reachable via `JAVA_HOME`.

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

---

## **ToDo**

- Fix the step 1 index bug so tables are actually extracted (currently 0 tables)
- Fix the `TAB_A1` type detection and cap sheets per workbook so step 2 completes
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