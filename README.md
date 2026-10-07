# **ArmTrace — Data Pipeline**

Python scripts that extract and structure Italian military export data from the
government annual reports published under **Law 185/1990** (Article 5, Law
9 July 1990 n. 185): the source reports → one PDF per table → a catalog of what
each table is.

> Working guidance for coding agents lives in [AGENTS.md](./AGENTS.md) for
> *finding* tables — invariants, detection traps, disproven approaches,
> per-year coverage — and in
> [AGENTS-TABLE2SQL.md](./AGENTS-TABLE2SQL.md) for *reading* them: rotation,
> bands, record grammars, and what the tables contain. This file is for a person
> who wants to *run* the pipeline and use its output.

---

## **Get the data**

Nothing is checked into git: the source PDFs (~1.6 GB) and everything the
pipeline generates are both regenerated from `manifest.tsv`. So a working
checkout starts with a download.

```bash
# 1. environment
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2. the source reports: 2001-2025, 51 PDFs, into reports_185_1990/
reports_185_1990/download_185.sh reports_185_1990 reports_185_1990/manifest.tsv

# 3. step 1 — one PDF per table  ->  Out/PDF/
./Reports2PDFTables.py --year 2025 --volume both

# 4. step 2 — what shape is each table?  ->  Out/CATALOG/
./CatalogueTables.py --year 2025 --manifest Out/manifest-2025.jsonl

# 5. audit what step 1 produced   ->  Out/VERIFY/
./VerifyTables.py --year 2025 --manifest <manifest.jsonl>
```

Step 3 prints a report per volume — tables found per ministry, page counts,
what was skipped and why — before it writes anything. Add `--dry-run` to see it
without writing.

**Always pass `--volume both`, never a single volume.** 2025 Tabella F1 is one
table of 70 pages split across the two volumes (printed 1035–1042, then
1043–1104), and a per-volume run loses the eight pages of volume I.

For one year only, use the thin per-year entry point, which pins the year and
refuses a `--year` of its own:

```bash
./Reports2PDFTables_2025.py            # same as --year 2025 --volume both
```

---

## **What you get**

Source reports and generated files are kept apart. `reports_185_1990/` holds
only the original PDFs; everything the pipeline produces goes to `Out/`.

```
reports_185_1990/                 # source reports, one folder per reporting year
├── 2001/ … 2025/                # 25 years, 51 PDFs (2009, 2011 from the SIPRI mirror)
│   └── <anno>_LXVII_nN_VOLUME_<X>.pdf   (or _TOMO_X / _DOCUMENTO_UNICO)
├── manifest.tsv                 # year, leg, num, volume, filename, pages, source URL
├── download_185.sh              # reproduce the download
└── SOURCES.md                   # archive structure, legislature→year mapping, gaps

Out/                             # generated locally, NOT in git
├── PDF/    <authority>/[<article>/]<table><year>.PDF
├── CATALOG/catalog.sqlite       the shape of every table, per year
│           catalog-<year>.jsonl
│           census-<year>.md
└── VERIFY/ verify-<year>.{log,json,html}
```

The folder name is the **reporting year** the report covers, not the year it was
published. Nothing hardcodes a year: step 1 reads it from the
`reports_185_1990/<anno>/…` path and every step keys its output on it as a
**filename suffix**, so one table folder holds one file per reporting year
(`Out/PDF/MEF/AA2023.PDF` for 2023, `AA2024.PDF` for 2024). Adding a year means
dropping its PDFs into `reports_185_1990/<anno>/` — no code change.

### The output tree is the ontology of the reports

`Out/` is partitioned by the ministry that produced each table, and by article
where the ministry subdivides by article. This is not tidiness. **A table code is
only unique within `(authority, article)`**, and the archive breaks that
assumption constantly:

| code | MAE | Dogane |
|---|---|---|
| `M1` | Intermediazioni per Operatore | Esportazioni Definitive |
| `N1` | LGP per Operatore | Temporanee Esportazioni |
| `O1` | LGT per Operatore | Importazioni Definitive |
| `P1` | AGT per Operatore | Temporanee Importazioni |

and inside the Dogane alone, `TAB. N` is printed twice in one volume — once
under art. 1 comma 2 and once under art. 1 commi 8/9. Under the previous
`Out/PDF/<tabella>/` layout the second write silently overwrote the first: the
MAE `M1` survived and every Dogane `M`…`Q` table was gone from the output. The
authority level is what makes the split correct, and `(authority, article,
code)` is the key everything downstream uses.

| directory | ministry | tables |
|---|---|---|
| `MAE` | Affari Esteri e Cooperazione Internazionale — Unità Autorizzazioni Materiali Armamento | `A1`…`P2`, per operator and per country |
| `MEF` | Economia e Finanze — Dipartimento del Tesoro, Direzione V | art. 27 credit-institution reporting: `AA`, `BB`, … `UE`, plus `GF`, `NN`, `OO`, `PP`, `LGP` |
| `DOG` | Agenzia delle Dogane e dei Monopoli | the four allegati below |
| `DIFESA` | Difesa — Segretariato Generale / DNA | art. 2 comma 6 maintenance and training, as `Annesso 2`, `3A`, `3B`, `3C`, `4` |

Two of these are easy to miss. The Dogane sit *inside* the MEF part of the
volume's own index and only become a separate ministry where they report. The
Difesa get no table line at all — their tables are `annessi`, numbered rather
than coded (`MINISTERO DELLA DIFESA - Annesso 3A`), and they match none of the
table-code patterns.

Only `DOG` and `DIFESA` subdivide by article, and the subdivision is printed on
the page rather than inferred from position:

| directory | allegato | article |
|---|---|---|
| `DOG/A1C2` | Operazioni a licenza | art. 1 comma 2 |
| `DOG/A1C89` | Programmi di coproduzione intergovernativa | art. 1 commi 8 lett. a) e 9 lett. a) |
| `DOG/A11C5BIS` | Operazioni a licenza globale di progetto | art. 11 comma **5-bis** |
| `DOG/A10QUATER` | Operazioni ad autorizzazione globale di trasferimento | art. 10 **quater** |
| `DIFESA/A2C6` | annessi 2, 3A, 3B, 3C, 4 | art. 2 comma 6 |

Tokens keep the differentiators the law actually uses: `A11C5BIS` and not
`A11C5`, because comma 5 and comma 5-bis are different authorisations. For the
Difesa the annesso *is* the table name, so it lands in the filename position
(`DIFESA/A2C6/3A2023.PDF`) rather than adding a level of its own.

---

## **Table code families**

The archive uses **three** mutually exclusive naming schemes plus one unnumbered
kind; a volume uses exactly one of the three. **The code is not unique on its
own** — read this table together with the authority section above.

| family | scheme | example codes | meaning |
|---|---|---|---|
| **1** | art. 27, double letter | `AA` `AA1` `BB` `UE` `FG` | MEF summary tables |
| **1** | Dogane, art. 11 comma 5-bis | `MG1`…`MG18` | licenze globali di progetto, 2024–2025 |
| **1** | Dogane, art. 10 quater | `MT1` `MT7` `MT13` | autorizzazioni globali di trasferimento |
| **1** | art. 27, `LGP` | `LGP` | MEF, licenze globali di programma |
| **1** | art. 27, charts | `NN` `OO` `PP` `GF` | percentage breakdown charts |
| **2** | `A1` … `P2` | `A1` `B7` `C1` `F2` `P2` | MAE per-operator / per-country detail, 31 codes |
| **3** | art. 27, single letter | `A` `B` `D` `E` `G` `J` `Q` | earliest layout, e.g. 2012 vol. I |
| **—** | `Annesso <n>` | `2` `3A` `3B` `3C` `4` | DIFESA, art. 2 comma 6 |

The `MG*` and `MT*` series are **Dogane, not MAE and not art. 27** — an earlier
version of this table said otherwise. They were re-attributed by checking printed
folios against the volume's own index, which puts `MG1` at page 1959 of 2025
vol. II, well inside the Dogane block.

A volume is not always one family: 2024 vol. II is family 1 for its first 600
pages and then carries the Dogane annex. Per-family detection rules and the
traps behind them are in
[AGENTS.md](./AGENTS.md#4-regex-and-detection-traps).

---

## **The scripts**

Three steps. Step 1 finds the tables and splits the volumes; step 2 records what
shape each table is; step 3 audits step 1's output. They are independent — step 2
re-reads the PDFs and step 3 re-reads the volumes, and both reach their own
conclusions rather than trusting step 1's manifest.

### 1. `Reports2PDFTables.py` — volumes → `Out/PDF/`

Splits a report volume into one PDF per table, written to
`Out/PDF/<authority>/[<article>/]<table><year>.PDF`.

```bash
./Reports2PDFTables.py --year 2025 --volume both
./Reports2PDFTables.py --all                  # every in-scope volume
./Reports2PDFTables.py --year 2023 --dry-run  # report only, write nothing
./Reports2PDFTables.py --report reports_185_1990/2023/2023_LXVII_n2_VOLUME_II.pdf
```

| Flag | Meaning |
|---|---|
| `--year` | reporting year — **required** when several are present |
| `--volume` | `1`, `2` or `both` (default `2`); use `both` |
| `--report` | explicit PDF path, repeatable |
| `--all` | every in-scope volume (`--all` stops at 2015; earlier years are deferred, not broken) |
| `--base` | root for all paths (default `.`) |
| `--out` | output root (default `<base>/Out`) |
| `--dry-run` | print the coverage report, write nothing |
| `--manifest` | append a JSON manifest to this path — pass it to step 3 |

Volume naming varies across the archive (`volume 1`, `VOLUME_I`, `TOMO_II`,
`DOCUMENTO_UNICO`); all are understood, and the script refuses to guess when
one year has two files claiming the same volume (2021 volume 2).

**How tables are found.** Several independent witnesses are **unioned rather
than ranked**: the PDF bookmark tree where the volume has one, the table code
printed in a page header, the ministry's own running header, and the volume's
printed index of tables. No single witness is trusted alone — a bookmark is
human-authored and wins a conflict, but bookmarks exist in only 6 of 43 volumes
and are not a superset; a header scan invents codes from prose; an index names
tables that no other signal can see. Each volume also states its own ministry
page ranges, which is what places a table under a ministry in the first place.

Every run reports what it found *and what it skipped*, so a decision to leave
something out stays visible rather than silent.

**A table's length is checked against its own margin stamp.** Each table was
exported as a document of its own and pasted into the volume, so every page
carries that document's pagination as `Pagina N di X` in the right margin. That
stamp — not the volume's own continuous folio — is the witness of a table's real
length, and pages pasted from a different document disagree with it.

**Status:** works on the 22 in-scope volumes (2016+), producing a manifest for
each. **2024 does not work** — it is the one year never measured properly, and
the next to be brought up. Per-year figures are in
[AGENTS.md](./AGENTS.md#archive-coverage).

### 2. `CatalogueTables.py` — `Out/PDF/` → `Out/CATALOG/`

Reads each per-table PDF and records **what shape it is**: its columns and their
labels, its record grammar, whether it has vector rules, and whether its text is
readable at all. It deliberately extracts no data — the point is that a table's
shape is neither stable across years nor uniform within one, so nothing can be
loaded into SQL until the shapes are known.

```bash
./CatalogueTables.py --year 2025 --manifest Out/manifest-2025.jsonl
./CatalogueTables.py --year 2025 --sample 7 --dry-run
./CatalogueTables.py --all
```

| Flag | Meaning |
|---|---|
| `--year` | reporting year to catalogue; matches the filename suffix |
| `--input-dir` | per-table PDFs (default `Out/PDF`), walked recursively |
| `--out-root` | output root (default `Out`) |
| `--catalog` | catalog path (default `Out/CATALOG/catalog.sqlite`) |
| `--manifest` | step 1's JSON manifest — the table's *witness* and source span |
| `--sample` | pages per table read for geometry (default 5) |
| `--dry-run` | report only, write nothing |

It writes three things: `catalog.sqlite` (one row per table per year, plus every
page's legibility and every column definition as rows, so the cross-year diff is
a SQL question), `catalog-<year>.jsonl` (the same, flat) and `census-<year>.md`
— the report to open, with coverage, grammars and column signatures.

**Why the catalog exists.** On 2025 the 110 tables do not agree on a shape: 48
print their content rotated 90° inside a portrait page, 57 carry a rotated
table under an upright running banner, four are percentage charts rather than
grids, and the row grain differs by ministry — MAE groups rows under an operator
and closes each group with a printed subtotal, MEF repeats a `Causale`
sub-dimension inside one authorisation number, the Dogane print one flat record
per operator, and the Difesa print cells holding lists wrapped over twenty
lines. A CSV per table would encode all four grains as indistinguishable
rectangles, and diffing those CSVs year over year would report a change in
every column of every table.

**Status:** works on 2025 — 110 tables in 141s, and it recovers the columns the
reports actually print (A1's ten, EE's five, MG10's three). It is a
*measurement* step: where it says `unknown`, that is recorded with a reason
rather than guessed at. 19 of 2025's tables yield no text at all and no catalog
can change that — see the coverage ceiling in
[AGENTS-TABLE2SQL.md](./AGENTS-TABLE2SQL.md#the-ceiling).

### 3. `VerifyTables.py` — audits `Out/PDF/`

The audit step. Reads the per-table PDFs step 1 produced, reads the source
volumes, and reaches its own conclusions about whether each file really is one
complete, correctly named table.

```bash
./VerifyTables.py --year 2025 --manifest <manifest.jsonl>
./VerifyTables.py --from-year 2016 --to-year 2025
./VerifyTables.py --year 2025 --dry-run        # verdicts only, write nothing
./VerifyTables.py --year 2025 --sample 3        # first/middle/last pages only
```

| Flag | Meaning |
|---|---|
| `--year` | reporting year, repeatable |
| `--from-year` / `--to-year` | inclusive range |
| `--all` | every year under `--reports-dir` |
| `--out-root` | root holding `Out/` (default `--base`) |
| `--reports-dir` | the source volumes (default `<base>/reports_185_1990`) |
| `--manifest` | step 1's JSON manifest — **required** for the provenance check |
| `--sample N` | read only N pages per file: fast, and less complete |
| `--no-cache` | ignore remembered results |
| `--no-fingerprint` | skip the per-page content hash |
| `--json` / `--log` / `--html` | report paths (default `<out>/Out/VERIFY/verify-<year>.<ext>`) |
| `--no-html` | skip the HTML report |
| `--dry-run` | print verdicts, write nothing |

It writes **only** to `Out/VERIFY/`, never to `Out/PDF/` or `Out/CATALOG/` — an
audit that can alter what it audits is not one.

#### The three reports

- **`verify-<year>.log`** — the record. Opens with the short answer, then every
  file, then a legend saying what each check asserted.
- **`verify-<year>.json`** — the same run in machine-readable form, with the
  structural verdict and the page-legibility count recorded separately.
- **`verify-<year>.html`** — the one to open. A sortable table of every file,
  **clickable straight to the PDF**, each row expanding to show every check with
  its verdict and detail; filter by verdict or search by code, path or check
  name. Self-contained: no network, no dependencies. Open it directly from disk.

All three are rendered from the same pass, so they cannot describe different
runs.

#### What it checks

Four questions per file, each with its own witness:

- **one table** — the `Pagina N di X` margin stamp is the only record of a table's
  true length; where it is unreadable its *totals* can still be compared across
  pages. Second witness: the code printed on each page, against a vocabulary built
  from codes that repeat.
- **complete** — first page is page 1, the total never changes, no interior page
  lacks the stamp, the index's page count agrees where it carries one, and printed
  folios run consecutively (volume joins allowed).
- **nothing else** — no blank separator, no index page, no running-header-only
  page, no page belonging to another table at either end.
- **correctly named** — the code on the file's own pages is the code in the
  filename, and the title the index gives that code appears on page 1.

Then per year: the volume's own table of contents, its per-authority index of
tables, and the bookmark tree — every code any of them lists must have a file,
and every file must have a code at least one lists. And the backbone: every
physical page must belong to exactly one table, or to an explicit non-table
class. A page claimed twice fails; a page claimed by nobody is listed.

#### Verdicts

`PASS` / `FAIL` / `WARN` / `SKIP`, each with a reason. **`FAIL` is reserved for
something demonstrably wrong** and is the figure to hold at zero. `WARN` means
*not verifiable here* — no stamp on this volume, no index, garbled text — and
never fails a run on its own, because a warning that turns the whole archive red
is one nobody reads. A file can be structurally perfect and still unreadable
(2025 `E` is garbled on all 221 of its pages), which is why the JSON records the
structural verdict and the legibility count separately.

Per-file results are cached under `Out/VERIFY/.cache/`, keyed on the file's size
and mtime **and** on the options that change the answer, so re-running after a
change only re-verifies what moved.

---

## **Requirements**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Steps 1 and 3 need only `pypdf`; step 2 needs `pymupdf`. **No JRE is required by
any step** — the old step 2 shelled out to Java through tabula-py, which is
gone.

---

## **Source reports**

`reports_185_1990/` holds every year available from the official Camera dei
Deputati archive, plus 2009 and 2011 from the SIPRI mirror (see `SOURCES.md`).

Years retrieved: **2001–2025, all 25 years, 51 PDFs, no gaps** — every one has a
row in `manifest.tsv`. 2009 (Doc. LXVII n. 3, tomi I–III) and 2011 (Doc. LXVII
n. 5, tomi I–V) are **not** in the Camera archive — its legislature XVI index
404s and an exhaustive probe of the path space finds nothing — so their rows
point at `sipri.org`, which mirrors the same parliamentary documents. Both are
Senato printings; every other year comes from `documenti.camera.it`.

Doc numbering restarts each legislature, so `n. 1` refers to a different year in
a different legislature. Filenames are therefore prefixed with the reference
year. `manifest.tsv` is the single input to `download_185.sh` and holds exactly
seven tab-separated fields per row — `year`, `leg`, `num`, `vol`, `file`,
`pages`, `url` — with the **source URL last**.

---

## **Repository contents**

No PDF, sqlite or other binary is tracked by git, and **nothing is in Git LFS** —
the repository holds only the scripts, `manifest.tsv`, `download_185.sh`, `lib/`,
`SOURCES.md`, `AGENTS.md`, `AGENTS-TABLE2SQL.md` and this file. Both large sets
are regenerable: the reports from `manifest.tsv` and `SOURCES.md`, and `Out/` by
re-running the pipeline. LFS was never a solution here — it does not stop the
files being committed, it only moves the bytes into a metered quota. **Do not
re-add LFS filter rules.**

---

## **ToDo**

Engineering work is tracked in [AGENTS.md](./AGENTS.md#outstanding), which is
authoritative for it — 2024 is at the head of that list. After it, the product
roadmap:

- Process the remaining years — see [Archive coverage](./AGENTS.md#archive-coverage) for what is deferred and why
- Populate a MySQL db
- Create an LLM tool that converts natural language requests into SQL queries
- Create a chatbot enhanced with that tool, allowing the DB to be interrogated in natural language
- Participatory workshop for the design of the interface

---

## **Notes**

- Input data is sourced from publicly available government reports under Italian Law 185/1990 (annual reports on military exports).
- This pipeline is the technical foundation of the ArmTrace civic transparency platform.