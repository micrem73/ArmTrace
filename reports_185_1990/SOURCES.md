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

## Year coverage retrieved from Camera archive

Complete: 2010, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021,
2022, 2023, 2025.

**Gaps: 2009 and 2011.** These are Doc. LXVII n. 3 and n. 5 of legislature XVI.
The category index for legislature XVI returns 404 and an exhaustive probe of
the archive path space (volume suffixes `_v01`, `_v01_RS`, tomi `_t1`…`_t5`,
`_ALLEGATO`, and both `INTERO.pdf` / `INTERO_COM.pdf` filenames) found no files.
The Senate PDF server, which lists them, is behind an AWS WAF challenge that
cannot be solved non-interactively.

## File naming

`INTERO.pdf` = "intero" (complete). `INTERO_COM.pdf` = the same document served
under the Camera's "completato" variant; content is equivalent (verified by
page count and first-page text).
