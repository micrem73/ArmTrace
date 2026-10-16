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
| XIII (13) and earlier | n. 1–5 | 1996–2000 (not retrievable from Camera archive) |

Note: doc numbering restarts each legislature, so the same "n. 1" refers to
different years in different legislatures.

## Year coverage

Complete: **2001–2025, all 25 years, no gaps.**

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

## File naming

`INTERO.pdf` = "intero" (complete). `INTERO_COM.pdf` = the same document served
under the Camera's "completato" variant; content is equivalent (verified by
page count and first-page text).
