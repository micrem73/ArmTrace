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

Reports2PDFTables.py              # step 1 — volumes  -> Out/PDF
IndividualTables2SQL.py           # step 2 — per-table PDFs -> Out/CSV
lib/                              # imported by the two scripts, never run
├── ontology.py                  #   ministries, article tokens, Out/ path shape
└── indice.py                     #   ministry page ranges from a volume's INDICE

Out/                             # generated locally, NOT in git (see "Binary files")
└── PDF|CSV/
    ├── MAE/<tabella><anno>.PDF               # Ministero degli Affari Esteri
    ├── MEF/<tabella><anno>.PDF               # Ministero dell'Economia e delle Finanze
    ├── DOG/<articolo>/<tabella><anno>.PDF    # Agenzia delle Dogane e dei Monopoli
    └── DIFESA/<articolo>/<tabella><anno>.PDF # Ministero della Difesa
```

Neither folder is checked in: `reports_185_1990/` and `Out/` are rebuilt from
`manifest.tsv` and the two scripts. The repository tracks only the scripts, the
manifest, `download_185.sh`, `lib/`, `SOURCES.md` and `AGENTS.md`.

The folder name is the **reporting year** covered by the report, not the year it was published. Nothing in the scripts hardcodes a year: step 1 reads it from the `reports_185_1990/<anno>/…` path, and both steps key their output on it as a **filename suffix**, `Out/PDF/MEF/AA2023.PDF` → `Out/CSV/MEF/AA2023.csv`. One folder per table therefore holds one file per reporting year, and step 2 selects the year with `--year` (it matches the suffix, so the suffix and the flag must agree).

Adding a year means dropping its PDFs into `reports_185_1990/<anno>/` — no code change required.

---

## **The output tree is the ontology of the reports**

`Out/` is partitioned by the ministry that produced each table, and by article
where the ministry subdivides by article. This is not tidiness — a table code is
only unique **within** `(authority, article)`, and the archive breaks that
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
MAE `M1` survived and every Dogane `M`…`Q` table was gone from `Out/`. The
authority level is what makes the split correct.

### Authorities

Read off the `INDICE` of each volume and cross-checked against the running
header of every table page. `lib/ontology.py` holds the markers.

| directory | ministry | tables |
|---|---|---|
| `MAE` | Affari Esteri e Cooperazione Internazionale — Unità Autorizzazioni Materiali Armamento | `A1`…`P2`, per operator and per country |
| `MEF` | Economia e Finanze — Dipartimento del Tesoro, Direzione V | art. 27 credit-institution reporting: `AA`, `BB`, … `UE`, `GF`, `NN`, `OO`, `PP`, `LGP` |
| `DOG` | Agenzia delle Dogane e dei Monopoli | the four allegati below |
| `DIFESA` | Difesa — Segretariato Generale / DNA | art. 2 comma 6 maintenance and training, as `Annesso 2`, `3A`, `3B`, `3C`, `4` |

Two of these are easy to miss from the `INDICE`. The Dogane sit *inside* the MEF
part of the index and only become a separate ministry at the point where they
report. The Difesa get no `Tabelle` line at all — their tables are `annessi`,
and before this change they were not extracted, because their running header
`MINISTERO DELLA DIFESA - Annesso 3A` matches none of the code patterns.

### Articles

Only `DOG` and `DIFESA` subdivide by article, and the subdivision is printed on
the page rather than implied by position:

| directory | allegato | article |
|---|---|---|
| `DOG/A1C2` | Operazioni a licenza | art. 1 comma 2 |
| `DOG/A1C89` | Programmi di coproduzione intergovernativa | art. 1 commi 8 lett. a) e 9 lett. a) |
| `DOG/A11C5BIS` | Operazioni a licenza globale di progetto | art. 11 comma **5-bis** |
| `DOG/A10QUATER` | Operazioni ad autorizzazione globale di trasferimento | art. 10 **quater** |
| `DIFESA/A2C6` | annessi 2, 3A, 3B, 3C, 4 | art. 2 comma 6 |

Each Dogane table page carries a qualifier line naming its allegato
(`Programmi Intergovernativi`, `Licenze Globali di Progetto`, `Autorizzazioni
Globali di Trasferimento`, or blank for art. 1 comma 2), so the article is read
off the table itself. Two consequences shaped the design:

- The four covers are printed **consecutively**, and only then do the tables
  follow, so positional inheritance would file every table under the last cover
  read. The per-page qualifier is the only thing that works.
- Article tokens keep the differentiators the law uses. `A10` alone cannot say
  *quater* from *bis* or *quinquies* — all three appear in Law 185/1990, and
  art. 10 quinquies is cited in the Dogane footnote of both 2020 and 2025 — and
  comma 5 and comma 5-bis are different authorisations, so `A11C5BIS` and not
  `A11C5`.

For the Difesa the annesso *is* the table name, so it lands in the filename
position (`DIFESA/A2C6/3A2023.PDF`) rather than adding a level of its own.

A code is therefore identified by the triple `(authority, article, code)`, and
that triple is the manifest key. Nothing downstream may key on the code alone.

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

The archive contains **three** mutually exclusive naming schemes for tables, plus
one unnumbered kind. A volume uses exactly one of the three. This matters
because step 2 dispatches on the code, and because the three schemes need
different header detection. **The code is not unique on its own** — read this
table together with the authority section above.

| family | scheme | example codes | meaning |
|---|---|---|---|
| **1** | art. 27, double letter | `AA` `AA1` `BB` `UE` `FG` `GF` | MEF summary tables |
| **1** | art. 27, MAE detail | `MG1`…`MG18` `MT1` `MT7` `MT13` | global licences — Dogane, 2024–2025, not MAE art. 27 |
| **1** | art. 27, `LGP` | `LGP` | MEF, licenze globali di programma |
| **1** | charts | `NN` `OO` `PP` `GF` | percentage breakdown charts |
| **2** | `A1` … `P2` | `A1` `B7` `C1` `F2` `P2` | MAE per-operator / per-country detail, 31 codes |
| **3** | art. 27, single letter | `A` `B` `D` `E` `G` `J` `Q` | earliest layout, e.g. 2012 vol. I |
| **—** | `Annesso <n>` | `2` `3A` `3B` `3C` `4` | DIFESA, art. 2 comma 6 |

> **Correction to an earlier claim in this file.** The `MG1`…`MG9`, `MT1`, `MT7`
> codes were previously described here as "art. 27, MAE detail — global
> licences". They are neither MAE nor art. 27: they are **Dogane**, `MG1`–`MG18`
> under art. 11 comma 5-bis and `MT1`, `MT7`, `MT13` under art. 10 quater.
> Verified by folio — `Out/PDF/.../MG1` is page 1959 and `MT1` page 2059 of 2025
> vol. II, where the Dogane tables begin at 1508 and the MEF block ends at 1494 —
> and by the fact that the 2025 vol. II bookmark tree contains no `MG`/`MT` entry
> at all, while `LGP` (p442) sits inside the MEF block with `AA`…`UE`.

Family 2 is a **closed set of 31 codes**, confirmed by the bookmark trees of
2021 tom. I, 2023 vol. I and 2025 vol. I, which agree on every code. Family
1, 2 and 3 codes match `^[A-Z]{1,3}\d{0,2}$` — **two** trailing digits, not one:
the Dogane series runs to `MG18`, and with a single-digit tail `TAB. MG10`
matched nothing at all (the group takes `MG1`, the word boundary after it fails
against the following `0`), which hid `MG10`–`MG18` and `MT13` from every
detector and let the span of `MG8` swallow all of them.

Family 2 is not always alone in a volume: 2024 vol. II is family 1 for its first
600-odd pages and then carries the Agenzia delle Dogane annex in a fourth scheme
— single letters `M` `N` `O` `P`, plus `MG*`/`MT*`. See
[AGENTS.md](./AGENTS.md#4-regex-and-detection-traps).

The 2012 index prints the digit one as a capital `I`, so it lists `Tabella DI`
and `Tabella Gl` where the real codes are `D1` and `G1`; `normalise_digit_one()`
folds these back.

Per-family detection rules and the traps behind them are in
[AGENTS.md](./AGENTS.md#4-regex-and-detection-traps).

---

## **Scripts**

### **Reports2PDFTables.py**

Splits a report volume into one PDF per table, written to
`Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF`. For each table it finds
the first page; the last page is one before the next table starts.

```bash
./Reports2PDFTables.py --year 2023 --volume 2
./Reports2PDFTables.py --year 2016 --volume both   # the development baseline
./Reports2PDFTables.py --all                  # every text-bearing volume
./Reports2PDFTables.py --year 2023 --dry-run  # report only, write nothing
./Reports2PDFTables.py --report reports_185_1990/2023/2023_LXVII_n2_VOLUME_II.pdf
```

> **2024 does not work yet.** `--year 2024 --volume both` finds 10 of 31 tables in
> vol. I and five phantom tables in vol. II. The causes are recorded in
> [AGENTS.md](./AGENTS.md#2024-measured-and-wrong); it heads the Outstanding list.

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

**How tables are found.** Four independent detectors, **unioned rather than
ranked**:

- **embedded** — the PDF bookmark tree, present in only 6 of 43 volumes;
- **header** — the table code printed in the page header;
- **annesso** — `MINISTERO DELLA DIFESA - Annesso 3A`, which is neither a
  bookmark nor a table code, and which used to be missed entirely;
- **indice** — not a table finder but a *placement* finder: the volume's own
  table of contents says which ministry produced which page range, and is
  preferred over reading ownership off page furniture wherever it exists
  (see "Which ministry a table belongs to" below).

Bookmarks are human-authored and win on a page conflict, but they are *not* a
superset: on 2025 vol. II the outline omits `MG1`–`MG9` and `MT1`/`MT7`, which
the header scan finds. Conversely the header scan invents codes from prose,
which is why the bookmark wins where they disagree.

Two further detectors fill gaps: an **index** page listing codes with their
page *counts*, used to cross-check derived spans; and the table **title**
repeated through the body, for family 3 where the codes appear only on the
index page.

The index harvest is the weakest of the four and is treated as a *witness*, not
an authority: it stops at the first page that carries enough codes, and a row
whose title wraps onto a second line is lost. On 2024 it returns 28 codes
instead of 31 in vol. I (losing `B5`) and 12 instead of 35 in vol. II. A code
missing from the index is therefore not evidence that it is absent from the
volume.

**Which ministry a table belongs to.** In this order, and the report says which
one it used.

**1. The volume's own INDICE.** It states each ministry's page range outright:

```
MINISTERO DEGLI AFFARI ESTERI E DELLA COOPERAZIONE INTERNAZIONALE ... »  11
  Tabelle ....................................................... »  65
MINISTERO DELLA DIFESA .......................................... » 1232
MINISTERO DELL'ECONOMIA E DELLE FINANZE ......................... » 1298
  Tabelle ....................................................... » 1308
  Relazione sull'attività dell'Agenzia delle dogane e dei monopoli » 1495
  Tabelle ....................................................... » 1508
```

That is the document's own account of its structure, so it beats inferring
ownership from a running header: no argument about which string is exclusive to
whom, and no inheritance across pages that print no marker. `lib/indice.py` parses
it and maps printed folios onto PDF pages. The index prints once, in the first
volume, and covers the later volumes too — verified for 2016, 2018, 2020, 2022,
2023 and 2025, where volumes I and II report identical ranges — so a later
volume borrows its sibling's.

**The heading is letterspaced in 2024.** It prints `I N D I C E`, where every
other year prints `INDICE`, so a plain substring test for `INDICE` rejects it and
2024 was recorded as having no index at all. The heading is matched on the page
with whitespace squeezed out, against a spacing-tolerant pattern.

**A parse must be validated before it is trusted**, because a wrong parse is
silent. On 2018 vol. I the parser reads the "Volume I" heading as a ministry and
produces `MEF block@1`, out of document order and entirely plausible. Rejected
unless: at least three ministry blocks, strictly increasing folios, a folio map
covering the volume, and at least one block inside this volume's own folio span.

The folio offset is the **mode** of `folio − page` over every legible page, not a
linear fit. Page 3 of 2023 vol. II reads `- 3 -`, so fitting across that
discontinuity gives a slope of 2.6 instead of 1 and maps every boundary to
nonsense.

Cross-checked against the header markers on 2025 vol. II (966 pages) and 2023
vol. II (576 pages): **agreement on every page where both speak.**

**2. The page furniture, as fallback** — and a large part of the archive needs
it, because the index is simply not there:

| volume | index |
|---|---|
| 2017 I+II, 2019 I+II, 2021 ×3 | **none** — no `INDICE`, `SOMMARIO` or `INDEX` anywhere |
| 2001–2011 | none, and no text layer either |
| 2012 I+II+III, **2016 I+II, 2018 I+II** | index present but **unnumbered** for the ministries that matter → rejected |
| 2020, 2022, 2023, 2024, 2025 | usable |

The volumes with no index include high-yield ones — 2021 gives 31 + 27 + 15
tables — so an index-only pipeline would strand around a hundred. For those the
authority is read off each page's own furniture and inherited across neighbours,
which is safe because each ministry's block is contiguous. Inheritance stops at
a real boundary: where the pages before and after a gap disagree, the gap is
reported as `UNPLACED` rather than guessed into one of the two.

The discriminators are the ministries' own typographies, in pypdf reading order
(which is neither the visual order nor `pdftotext -layout`'s — the export band
is rotated, so its text lands at a varying position in the content stream):

| ministry | what it prints | pages it fires on, 2025 vol. II |
|---|---|---|
| DOG | `TAB. <code>` — **with** the period | 526, all DOG |
| MEF | `Tabella <code>`, `Dipartimento del Tesoro Direzione V` | 35, all MEF |
| DIFESA | `MINISTERO DELLA DIFESA - Annesso 3A` | 13, all DIFESA |
| MAE | `MAECI -UAMA - CENTRO INFORMATICO …` | 25, all MAE |

**Order by measured exclusivity, not by apparent specificity.** MAE is asked
last because it is the only one of the four that leaks: the 2025 Difesa relation
says its companies file *"comunicazione al MAECI-UAMA"*, so the short form also
fires on two Difesa pages. Requiring `- CENTRO INFORMATICO` — the fragment that
makes it a production stamp rather than a mention — takes that to zero. Asking in
order of "how specific does this look" put MAE second and misfiled those pages;
the Difesa relazione then lost 9 pages to MAE and 33 more to unplaced.

**3. The article, always from the page.** The INDICE bounds ministries but does
not subdivide the Dogane, whose four allegati all sit inside a single block, so
the article can only come from the table's own qualifier line. That part of the
marker machinery is not redundant with the index.

**`MIN_RUN` is waived for the Dogane.** A single-page hit is normally a prose
cross-reference rather than a table — but that false positive came from the MEF
art. 27 narrative, and of the 18 `MG` tables of art. 11 comma 5-bis nine run to
a single page. Applying the threshold there dropped them and let the span of the
surviving `MG8` swallow the rest.

**The Dogane's own cover pages are skipped.** `All.1 - OPERAZIONI A LICENZA` and
`All.1: OPERAZIONI A LICENZA` list that allegato's tables by name, so the header
scan reads them as the starts of twelve tables and truncates the real ones. They
are matched with the page's whitespace squeezed out, because pypdf shreds them
into fragments (`All.1: OPE` arrives as `Al` / `l.1: OPE` / `RAZIONI A`) and no
line ever holds the header. Exactly 9 pages match in 2025 vol. II, and they are
the 9 index pages.

**Gazzetta Ufficiale pages are skipped.** Reports quote and reprint Gazzette
material, and the odd document is bound in whole. Pages carrying the header are
not scanned for tables:

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
implicit — every run prints what it skipped, e.g. `pasted-in: 99 Gazzette
UFFICIALe pages, no tables, skipped (p710-712, p714-808, p810)` — so an
implausible count is visible rather than silent.

A first attempt used the *absence of a valid running folio* instead, which is the
more principled witness — a pasted-in document keeps its own pagination. It was
abandoned: the folio signal is too noisy to threshold, since pypdf does not emit
the running folio first on every page and garbled or blank pages interrupt any
run. A 20-page minimum flagged 206 pages of 2019 vol. I in thirteen scattered
runs and still missed the block it was written for. The gazette header is checked
**inside** the page scan rather than in a pass of its own, because a second full
extraction of a 1048-page volume added enough to push a run past 40 minutes.


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
was unchanged — and on 2024 vol. I it fires on tables it should not, cutting
`A4` by 17 pages, so a year absent from that list is not thereby a year where
the trim is correct. The 2021 and 2019 cases are confirmed independently by the
index page, which lists those E tables as 5 and 34 pages. The full case table is
in [AGENTS.md](./AGENTS.md#6-verification-and-archive-coverage).

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
table of seventy pages. `Out/PDF/MAE/F12025.PDF` is that table.

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
duplicate  : 27 table(s) found in another volume of the same year and not a
             split; kept copy wins
    MAE/A1                 p71-365 (295 pp) dropped, kept from 2021_LXVII_n5_TOMO_I
```

Only when the two spans are *consecutive* — the first ends where its volume
ends, the second starts where its volume starts, and the volumes are
neighbours — are both kept and joined, which is the readable-halves version of
a split. Anything else would invent a table out of two unrelated ones.

**Status:** works on every volume previously verified — **479 tables, zero
failures** across 28 volumes, every one producing a manifest. **2024 was not
among them and does not work**: 10 of 31 tables found in vol. I, five phantom
tables in vol. II. The per-year table, which includes it, is in
[AGENTS.md](./AGENTS.md#archive-coverage).

The pipeline is currently developed against the **2016** reports. Earlier years
are deferred, not broken — see [Archive coverage](./AGENTS.md#archive-coverage)
for the per-year table.
**Status:** **610 tables over the 22 in-scope volumes (2016+), zero UNPLACED.**
The earlier code-only layout was verified separately at **479 tables, zero
failures** across 28 volumes — a different scope and a different key, so the two
counts are not comparable. **2024 was never in either count and does not work**:
under the old keying it found 10 of 31 tables in vol. I and five phantom tables in
vol. II. Per-year figures are in
[AGENTS.md](./AGENTS.md#archive-coverage).

The pipeline is developed against the **2016** reports; `--all` stops at 2015
(`FIRST_YEAR`). Earlier years are deferred, not broken — see
[Archive coverage](./AGENTS.md#archive-coverage).

### **IndividualTables2SQL.py**

Reads the per-table PDFs from step 1 and converts them to CSV via tabula-py.

```bash
python IndividualTables2SQL.py --year 2023
python IndividualTables2SQL.py --year 2023 --input-dir Out/PDF --output-root Out/CSV
```

| Flag | Meaning |
|---|---|
| `--year` | year to process (default `2024`); matches the filename suffix |
| `--input-dir` | per-table PDFs (default `Out/PDF`), walked recursively |
| `--output-root` | CSV root (default `Out/CSV`) |
| `--base` | root for all paths (default `.`) |
| `--sep` | field separator (default `;`) |
| `--encoding` | CSV encoding (default `utf-8-sig`) |

`locate()` reads the ministry and article back out of the path step 1 wrote
(`DOG/A11C5BIS/MG102023.PDF` → `DOG`, `A11C5BIS`, `MG10`, `2023`);
`split_stem()` splits the filename stem into table code and year (`AA2023` →
`AA`, `2023`); `detect_pdf_type()` uses the code; `table_semantics()` maps
`(code, authority, article)` to a family and a human label. No archive code ends
in four digits, so the trailing digit group is always the year — which is what
lets `MG10` and `MT13` be read at all.

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

### **VerifyTables.py**

The audit step. Reads the per-table PDFs step 1 produced, reads the source
volumes, and reaches its own conclusions about whether each file really is one
complete, correctly named table.

```bash
./VerifyTables.py --year 2025
./VerifyTables.py --from-year 2016 --to-year 2025
./VerifyTables.py --year 2025 --dry-run        # verdicts only, no JSON
./VerifyTables.py --year 2025 --sample 3        # first/middle/last pages only
./VerifyTables.py --year 2025 --refresh         # ignore the cache
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
| `--no-cache` / `--refresh` | ignore remembered results |
| `--no-fingerprint` | skip the per-page content hash |
| `--json` | report path (default `<out>/Out/VERIFY/verify-<year>.json`) |
| `--dry-run` | print verdicts, write nothing |

It writes **only** to `Out/VERIFY/`, never to `Out/PDF/` or `Out/CSV/` — an
audit that can alter what it audits is not one.

#### What it checks

Four questions per file, each with its own witness:

- **one table** — the `Pagina N di X` stamp is the only record of a table's
  true length, because each table was exported as a document of its own and
  pasted into the volume afterwards. Stamp total == page count means the file
  *is* that document. Where the stamp is garbled the numbers cannot be read
  but can still be **compared**: the subsetter gave one Private Use codepoint
  per character, so two pages of one document agree character for character on
  the total and a page from another does not. Second witness: the code printed
  on each page, counted against a vocabulary built from codes that *repeat*.
- **complete** — first page is page 1, the total never changes, no interior
  page lacks the stamp, the index's page count agrees where it carries one,
  and printed folios run consecutively (with the volume joins allowed).
- **nothing else** — no blank separator, no ELENCO page, no
  running-header-only page, no page belonging to another table at either end.
- **correctly named** — the code on the file's own pages is the code in the
  filename, and the title the index gives that code appears on page 1.

Then per year: the **general INDICE** (which authority's tables start on which
printed page) and the per-authority **ELENCO TABELLE SEGNALAZIONI** (every code
with its title), plus the bookmark tree where one exists. Every code any of
them lists must have a file; every file must have a code at least one lists.

And the backbone: rebuild each volume's coverage and require every physical
page to belong to exactly one table, or to one of an explicit list of
non-table classes. A page claimed twice fails; a page claimed by nobody is
listed.

#### Verdicts

`PASS` / `FAIL` / `WARN` / `SKIP`, each with a reason. `FAIL` is reserved for
something demonstrably wrong. `WARN` means *not verifiable here* — no stamp on
this volume, no index, garbled text — and never fails a run on its own,
because a warning that turns the whole archive red is one nobody reads. A file
can be structurally perfect and still unreadable (2025 `E` is garbled on all
221 of its pages), so the JSON records the structural verdict and the
legibility count separately.

Per-file results are cached under `Out/VERIFY/.cache/`, keyed on the file's
size and mtime **and** on the options that change the answer, so re-running
after a detector change only re-verifies what moved.

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
authoritative for it. At the head of it: **2024 does not work** — under the
previous keying it found 10 of 31 tables in vol. I and five phantom tables in
vol. II. What is left after that is the product roadmap:

- Process the remaining years — see [Archive coverage](./AGENTS.md#archive-coverage) for what is deferred and why
- Populate a MySQL db
- Create an LLM tool that converts natural language requests into SQL queries
- Create a chatbot enhanced with that tool, allowing the DB to be interrogated in natural language
- Participatory workshop for the design of the interface

---

## **Notes**

- Input data is sourced from publicly available government reports under Italian Law 185/1990 (annual reports on military exports).
- This pipeline is the technical foundation of the ArmTrace civic transparency platform.