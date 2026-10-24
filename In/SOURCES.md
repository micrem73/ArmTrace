# Law 185/1990 annual relations — source map

Italian law 9 July 1990, n. 185, Art. 5 requires the Government to send
Parliament an annual relation on authorised operations for the control of the
export, import and transit of armaments material.

## Official sources consulted

| Source | URL | Status |
|---|---|---|
| Camera dei Deputati — document archive | `documenti.camera.it/_dati/leg{17,18,19}/lavori/documentiparlamentari/IndiceETesti/067/<numero>/<INTERO\|INTERO_COM>.pdf` | Working, no WAF |
| Camera dei Deputati — category index (leg XVII–XIX) | `www.camera.it/leg{17,18,19}/494?idLegislatura={leg}&categoria=067&tipologiaDoc=elenco_categoria` | Working |
| Camera dei Deputati — category index (leg XI–XVI) | same pattern | **404** — index not published |
| Camera dei Deputati — leg XIII archive | `leg13.camera.it/_dati/leg13/lavori/documentiparlamentari/indiceetesti/lxvii/elenco.htm` → `<numero>/pdfel.htm` → `INTERO.pdf` / `INTERO_COM.pdf` | Working |
| Camera dei Deputati — leg X–XII document cards | `legislature.camera.it/documenti/documentiparlamentari/frmTrovaDoc.asp?stato=1&txtLeg={leg}&txtCategoria={067\|107}&txtNumero={n}` → `legislature.camera.it/_dati/leg{11,12}/lavori/stampati/pdf/<id>.pdf` | Working (no report file for leg X) |
| Senato della Repubblica — non-legislative documents | `www.parlamento.it/static/bgt/listadocumenti/{leg}/1/{tipoDoc}/0/index.html` | Index readable |
| Senato della Repubblica — PDF server | `www.senato.it/service/PDF/PDFServer/DF/{id}.pdf` | **Blocked** by AWS WAF challenge (HTTP 202) |
| SIPRI — national reports on arms exports (Italy) | `www.sipri.org/databases/national-reports/Italy` → `www.sipri.org/sites/default/files/…/national_reports/italy/*.pdf` | Working, no WAF — mirrors the reports for **1990–2021** |

## Legislature → year mapping (Doc. LXVII, category 067)

| Legislature | Doc numbers | Reference years |
|---|---|---|
| XIX (19) | n. 1–4 | 2022, 2023, 2024, 2025 |
| XVIII (18) | n. 1–5 | 2017, 2018, 2019, 2020, 2021 |
| XVII (17) | n. 1–5 | 2012, 2013, 2014, 2015, 2016 |
| XVI (16) | n. 1–5 | 2007, 2008, 2009, 2010, 2011 |
| XV (15) | n. 1–2 | 2005, 2006 |
| XIV (14) | n. 1–4 | 2001, 2002, 2003, 2004 |
| XIII (13) | n. 1–5 | 1996, 1997, 1998, 1999, 2000 |
| XII (12) | n. 1–2 | 1994, 1995 |
| XI (11) | **Doc. CVII** n. 1–3 | 1991, 1992, 1993 |
| X (10) | **Doc. CVII** n. 1 | 1990 |

Note: doc numbering restarts each legislature, so the same "n. 1" refers to
different years in different legislatures. In legislatures X and XI the relation
was **Doc. CVII**; Doc. LXVII was then the report on publishing ("stato
dell'editoria"). The files of 1990–1993 are named `<year>_CVII_n<N>_…`.

## Year coverage

Complete: **1990–2025, all 36 years, no gaps.**

**2009 and 2011 are not in the Camera archive.** These are Doc. LXVII n. 3 and
n. 5 of legislature XVI. The category index for legislature XVI returns 404, and an
exhaustive probe of the archive path space (volume suffixes `_v01`, `_v01_RS`, tomi
`_t1`…`_t5`, `_ALLEGATO`, and both `INTERO.pdf` / `INTERO_COM.pdf` filenames)
returns nothing. The Senate PDF server, which lists them, is behind an AWS WAF
challenge that cannot be solved non-interactively.

They were instead retrieved from the **SIPRI national-reports mirror**, which hosts
the same parliamentary documents:

| Year | Doc | SIPRI file | Volume | Pages |
|---|---|---|---|---|
| 2009 | LXVII n. 3 | `Italy09_2.pdf` | TOMO I | 825 |
| 2009 | LXVII n. 3 | `Italy09_3.pdf` | TOMO II | 953 |
| 2009 | LXVII n. 3 | `Italy09_4.pdf` | TOMO III | 1113 |
| 2011 | LXVII n. 5 | `italy_11_1.pdf` | TOMO I | 808 |
| 2011 | LXVII n. 5 | `Italy_11_2.pdf` | TOMO II | 690 |
| 2011 | LXVII n. 5 | `Italy_11_3.pdf` | TOMO III | 768 |
| 2011 | LXVII n. 5 | `italy_11_4.pdf` | TOMO IV | 800 |
| 2011 | LXVII n. 5 | `Italy_11_5.pdf` | TOMO V | 396 |

Each was verified by downloading it and reading page 1, which states the doc
number and reference year (`Doc. LXVII n. 3 … (Anno 2009)`, `Doc. LXVII n. 5 …
(Anno 2011)`). Note both years are *Senato* printings, whereas the other years in
this manifest are Camera printings — same document series, different typesetting.

SIPRI also hosts, and we deliberately **excluded**, three files that carry no
volume number and would confuse volume detection:

- `Italy09_1.pdf` — the 2009 **ALLEGATO** (16 pp), the separate sectoral relation
  of the Ministero dello sviluppo economico, not part of the three tomi.
- `Italy_11_short.pdf`, `Italy_2009_short-version.pdf` — abridged one-volume
  summaries of the full relations.

## 1990–2000: Camera archives of legislatures X–XIII (added 2026-10-24)

Found on the Camera site first; SIPRI only for the one year the Camera does not
hold. Each file was downloaded and its first page read (all ten 1990–1999 first
pages are scans with no text layer, so read as images): doc, number, legislature
and `(ANNO …)` match the manifest row.

| Year | Doc | Source | Pages | Printing |
|---|---|---|---|---|
| 2000 | LXVII n. 5, leg XIII | Camera `lxvii/005/INTERO.pdf` | 333 | Camera |
| 1999 | LXVII n. 4, leg XIII | Camera `lxvii/004_RS/INTERO_COM.pdf` | 273 | Camera |
| 1998 | LXVII n. 3, leg XIII | Camera `lxvii/003/INTERO.pdf` | 565 | Camera |
| 1997 | LXVII n. 2, leg XIII | Camera `lxvii/002_RS/INTERO_COM.pdf` | 309 | Camera |
| 1996 | LXVII n. 1, leg XIII | Camera `lxvii/001/INTERO.pdf` | 279 | Camera |
| 1995 | LXVII n. 2, leg XII | Camera `stampati/pdf/37745.pdf` | 336 | Senato |
| 1994 | LXVII n. 1, leg XII | Camera `stampati/pdf/37741.pdf` | 240 | Senato |
| 1993 | CVII n. 3, leg XI | Camera `stampati/pdf/36577.pdf` | 296 | Senato |
| 1992 | CVII n. 2, leg XI | Camera `stampati/pdf/36574.pdf` | 448 | Camera |
| 1991 | CVII n. 1, leg XI | Camera `stampati/pdf/36572.pdf` | 540 | Senato |
| 1990 | CVII n. 1, leg X | **SIPRI** `Italy_90.pdf` | 228 | Camera |

**1990 is the one SIPRI file.** The Camera's card for Doc. CVII n. 1 (leg X,
transmitted 9 May 1991) links only the record of sitting 627, which announces
the relation, not the relation itself. SIPRI's copy is the Camera printing
(first page: "Camera dei Deputati, X legislatura, Doc. CVII n. 1, Anno 1990").
The other SIPRI files for 1991–2000 (`Italy_91.pdf` … `Italy_2000.pdf`) were
not used: the Camera holds those years.

## File naming

`INTERO.pdf` = "intero" (complete). `INTERO_COM.pdf` = the same document served
under the Camera's "completato" variant; content is equivalent (verified by
page count and first-page text).
