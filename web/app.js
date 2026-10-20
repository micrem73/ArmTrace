"use strict";
// ArmTrace explorer: the whole database runs in the browser (sql.js), read-only.
// Views by #hash: #domande, #domanda/<id>, #tabelle[/tutte], #tabella/<year>/<key>,
// #sql[=<query>], #metodo.

const SQLJS = "https://cdn.jsdelivr.net/npm/sql.js@1.10.3/dist/";
const PAGE = 500;                       // rows rendered at a time
// Columns every printed row carries: they place the row in its source, and
// are shown as the "Fonte" link, not as data (tools/build_site.py).
const PLACE = ["year", "source_file", "pdf_page", "printed_page", "row_ord", "row_kind"];
// Numbers that are identities, not amounts: never 2.025 for a year.
const PLAIN = /^(year|anno|pdf_page|printed_page|row_ord|ordinal|pages|nr)$/;

let db;
let domande = [];                       // web/domande.json, checked by tools/build_site.py
const fonti = new Map();                // source file -> official URL (names carry the year)
const labels = new Map();               // "sql_table.col" -> printed Italian label
const main = document.getElementById("main");
// What a table's columns cannot say by themselves. Goes when the database
// carries the rule (columns.tsv in the extractor).
const AVVERTENZE = {
  mae_a1: "La colonna EURO è stampata vuota per le licenze in euro: lì il valore è l'Ammontare. " +
    "Le licenze con un Valore ai fini doganali non entrano nel totale EURO. Il valore è sulla prima riga " +
    "di ogni licenza; le righe seguenti ne descrivono i materiali. Per i totali per azienda usa la Tabella A2.",
};

// ---------- helpers ----------

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

function q(sql, params = []) {
  const stmt = db.prepare(sql);
  try {
    stmt.bind(params);
    const columns = stmt.getColumnNames();
    const values = [];
    while (stmt.step()) values.push(stmt.get());
    return { columns, values };
  } finally {
    stmt.free();
  }
}

function objects(sql, params) {
  const { columns, values } = q(sql, params);
  return values.map(v => Object.fromEntries(columns.map((c, i) => [c, v[i]])));
}

const ident = name => '"' + String(name).replace(/"/g, '""') + '"';
const nf0 = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function fmt(v) {
  if (typeof v === "number") return Number.isInteger(v) ? nf0.format(v) : nf2.format(v);
  return String(v);
}

function fonteUrl(file, page) {
  const url = fonti.get(file);
  return url && page != null ? `${url}#page=${page}` : null;
}

// Replace the page body; optional blocks written as `cond && node` drop out.
function show(...nodes) {
  main.replaceChildren(...nodes.flat(Infinity).filter(n => n != null && n !== false));
}

function setView(name) {
  for (const a of document.querySelectorAll(".top nav a"))
    a.classList.toggle("on", a.dataset.view === name);
}

// ---------- result grid ----------
// One renderer for every view. Options:
//   printed  pair X with X_raw: show the printed text, sort by the parsed value
//   hide     column names not shown (still exported)
//   cell     (col, value, row) -> Node | undefined, to override a cell

function grid(result, opts = {}) {
  const { columns, values } = result;
  const at = Object.fromEntries(columns.map((c, i) => [c, i]));
  const hide = new Set(opts.hide || []);
  const raw = {};
  if (opts.printed) {
    for (const c of columns) if (at[c + "_raw"] != null) { raw[c] = at[c + "_raw"]; hide.add(c + "_raw"); }
  }
  const shown = columns.filter(c => !hide.has(c));
  const hasFonte = at.source_file != null && at.pdf_page != null;
  const kindAt = at.row_kind;

  let rows = values.slice();
  let limit = PAGE;
  let filter = "";
  let dataOnly = false;
  let sortCol = null, sortDir = 1;

  const body = el("tbody");
  const count = el("span", { class: "count" });
  const more = el("button", { onclick: () => { limit += PAGE; paint(); } }, "Mostra altre righe");

  function matches(row) {
    if (dataOnly && kindAt != null && row[kindAt] !== "data") return false;
    if (!filter) return true;
    return row.some(v => v != null && String(v).toLowerCase().includes(filter));
  }

  function cellFor(c, row) {
    const v = row[at[c]];
    const custom = opts.cell && opts.cell(c, v, row, at);
    if (custom !== undefined) return el("td", {}, custom);
    if (raw[c] != null && row[raw[c]] != null) return el("td", { class: "num" }, row[raw[c]]);
    if (v == null) return el("td", { class: "null" }, "—");
    if (v instanceof Uint8Array) return el("td", { class: "null" }, `[${v.length} byte]`);
    if (PLAIN.test(c)) return el("td", { class: "num" }, String(v));
    return el("td", { class: typeof v === "number" ? "num" : null }, fmt(v));
  }

  function fonteCell(row) {
    const url = fonteUrl(row[at.source_file], row[at.pdf_page]);
    const page = at.printed_page != null && row[at.printed_page] != null ? row[at.printed_page] : row[at.pdf_page];
    return el("td", {}, url ? el("a", { href: url, target: "_blank", rel: "noopener" }, `p. ${page}`) : "—");
  }

  function paint() {
    const picked = rows.filter(matches);
    body.replaceChildren(...picked.slice(0, limit).map(row =>
      el("tr", { class: kindAt != null ? row[kindAt] : null },
        hasFonte && fonteCell(row), shown.map(c => cellFor(c, row)))));
    count.textContent = picked.length === values.length
      ? `${nf0.format(values.length)} righe`
      : `${nf0.format(picked.length)} di ${nf0.format(values.length)} righe`;
    more.hidden = picked.length <= limit;
    return picked;
  }

  function sortBy(c) {
    sortDir = sortCol === c ? -sortDir : 1;
    sortCol = c;
    const i = at[c];
    rows = values.slice().sort((a, b) => {
      const x = a[i], y = b[i];
      if (x == null) return y == null ? 0 : 1;
      if (y == null) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * sortDir;
      return String(x).localeCompare(String(y), "it") * sortDir;
    });
    limit = PAGE;
    paint();
  }

  const head = el("tr", {}, hasFonte && el("th", {}, "Fonte"), shown.map(c => {
    const label = (opts.headers && opts.headers[c]) || (opts.table && labels.get(`${opts.table}.${c}`));
    return el("th", { title: "Ordina", onclick: () => sortBy(c) }, label || c,
      label && !opts.headers && el("small", {}, c));
  }));

  const controls = el("div", { class: "controls" },
    el("input", { type: "search", placeholder: "Filtra le righe…",
      oninput: e => { filter = e.target.value.trim().toLowerCase(); limit = PAGE; paint(); } }),
    kindAt != null && el("label", {},
      el("input", { type: "checkbox", onchange: e => { dataOnly = e.target.checked; limit = PAGE; paint(); } }),
      " solo righe di dati"),
    el("button", { onclick: () => { sortCol = null; rows = values.slice(); paint(); } }, "Ordine stampato"),
    el("button", { onclick: () => csv(columns, values.filter(matches), at, opts.name) }, "Scarica CSV"),
    count);

  paint();
  return el("div", {}, controls, el("div", { class: "scroll" }, el("table", {}, el("thead", {}, head), body)), more);
}

function csv(columns, values, at, name) {
  const hasFonte = at.source_file != null && at.pdf_page != null;
  const cols = hasFonte ? [...columns, "fonte_url"] : columns;
  const esc = v => {
    if (v == null) return "";
    const s = String(v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [cols.map(esc).join(",")];
  for (const row of values) {
    const out = row.map(esc);
    if (hasFonte) out.push(esc(fonteUrl(row[at.source_file], row[at.pdf_page])));
    lines.push(out.join(","));
  }
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = el("a", { href: URL.createObjectURL(blob), download: (name || "armtrace") + ".csv" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- chart ----------
// One series, horizontal bars, value at the bar's end; the table below is the
// full view. Only the first rows: a chart of 160 bars reads as texture.

const compact = new Intl.NumberFormat("it-IT", { notation: "compact", maximumFractionDigits: 1 });

function bars(result, { etichetta, valore }, max = 15) {
  const lis = [].concat(etichetta).map(c => result.columns.indexOf(c));
  const vi = result.columns.indexOf(valore);
  const rows = result.values.filter(r => typeof r[vi] === "number" && r[vi] >= 0).slice(0, max);
  if (lis.includes(-1) || vi < 0 || !rows.length) return null;
  const label = r => lis.map(i => (r[i] == null ? "—" : String(r[i]))).join(" · ");
  const top = Math.max(...rows.map(r => r[vi])) || 1;
  return el("figure", { class: "bars" },
    el("figcaption", {}, valore.replace(/_/g, " ")),
    rows.map(r => el("div", { class: "bar-row", title: `${label(r)}: ${fmt(r[vi])}` },
      el("span", { class: "bar-label" }, label(r)),
      el("span", { class: "bar-track" },
        // the value label sits after the bar: keep 72px for it
        el("span", { class: "bar", style: `width:calc((100% - 72px) * ${(r[vi] / top).toFixed(4)})` }),
        el("span", { class: "bar-value" }, compact.format(r[vi]))))),
    result.values.length > rows.length &&
      el("p", { class: "count" }, `Le prime ${rows.length} righe di ${result.values.length}: tutte nella tabella.`));
}

// ---------- views ----------

function viewDomande() {
  setView("domande");
  const gruppi = [...new Set(domande.map(d => d.gruppo))];
  show(
    el("h1", {}, "Che cosa si può chiedere"),
    el("p", { class: "lead" },
      "Le relazioni sull'export di armamenti sono migliaia di pagine di tabelle. Trasformate in un database, " +
      "rispondono a domande come queste in un attimo. Ogni risposta mostra la query che la produce, " +
      "da modificare, e rimanda alla pagina della relazione da cui viene ogni riga."),
    gruppi.map(g => [
      el("h2", {}, g),
      el("div", { class: "cards" }, domande.filter(d => d.gruppo === g).map(d =>
        el("a", { class: "card", href: `#domanda/${d.id}` },
          el("strong", {}, d.titolo), el("span", {}, d.spiegazione))))]));
}

function viewDomanda(id) {
  setView("domande");
  const d = domande.find(x => x.id === id);
  if (!d) return show(el("p", { class: "error" }, `Domanda ${id}: non trovata.`));
  const sql = d.sql.join("\n");
  const result = q(sql);
  show(
    el("p", {}, el("a", { href: "#domande" }, "← Tutte le domande")),
    el("p", { class: "count" }, d.gruppo),
    el("h1", {}, d.titolo),
    el("p", { class: "lead" }, d.spiegazione),
    d.grafico && bars(result, d.grafico),
    el("details", { class: "query" }, el("summary", {}, "La query"),
      el("pre", {}, sql),
      el("p", {}, el("a", { href: "#sql=" + encodeURIComponent(sql) }, "Modificala nell'editor SQL"))),
    grid(result, { hide: [...PLACE, "prima_riga", "prima_voce"], name: d.id }));
}

function viewTabelle(tutte) {
  setView("tabelle");
  const [n] = objects("SELECT count(*) AS tot, count(sql_table) AS done FROM catalogo");
  const cat = q(`SELECT year, key, authority, code, printed, pages, sql_table, check_verdict
                 FROM catalogo ${tutte ? "" : "WHERE sql_table IS NOT NULL"}
                 ORDER BY year DESC, authority, key`);
  show(
    el("h1", {}, "Tabelle delle relazioni"),
    el("p", { class: "lead" },
      `${n.done} delle ${n.tot} tabelle trovate nelle relazioni sono già lette riga per riga: aprile per ` +
      "filtrarle, ordinarle, scaricarle e risalire alla pagina di ogni riga."),
    el("p", {}, el("label", {},
      el("input", { type: "checkbox", checked: !tutte,
        onchange: e => { location.hash = e.target.checked ? "#tabelle" : "#tabelle/tutte"; } }),
      " solo le tabelle già estratte")),
    grid(cat, {
      name: "catalogo",
      hide: ["sql_table"],
      headers: { year: "Anno", key: "Tabella", authority: "Autorità", code: "Codice",
                 printed: "Pagine stampate", pages: "Pagine", check_verdict: "Verifica del taglio" },
      cell: (c, v, row, at) => {
        if (c === "key") {
          const sqlTable = row[at.sql_table];
          return sqlTable
            ? el("a", { href: `#tabella/${row[at.year]}/${encodeURIComponent(v)}` }, v)
            : el("span", {}, v, " ", el("span", { class: "badge" }, "da estrarre"));
        }
        return undefined;
      },
    }));
}

function viewTabella(year, key) {
  setView("tabelle");
  const [meta] = objects("SELECT * FROM catalogo WHERE year = ? AND key = ?", [year, key]);
  if (!meta || !meta.sql_table) {
    show(el("p", { class: "error" }, `Tabella ${key} ${year}: non ancora estratta.`));
    return;
  }
  const sql = `SELECT * FROM ${ident(meta.sql_table)}\nWHERE year = ${Number(year)}\nORDER BY source_file, pdf_page, row_ord`;
  const result = q(sql);
  const checks = objects(`SELECT status, count(*) AS n FROM check_result
                          WHERE year = ? AND table_key = ? GROUP BY status ORDER BY status`, [year, key]);
  const totals = objects(`SELECT label, value_raw, printed_page, pdf_page FROM printed_total
                          WHERE year = ? AND table_key = ? ORDER BY pdf_page, rowid`, [year, key]);
  const notes = objects(`SELECT text FROM table_note WHERE year = ? AND table_key = ? ORDER BY pdf_page, rowid`,
                        [year, key]);
  const flags = result.columns.indexOf("flags");
  const hide = [...PLACE];
  if (flags >= 0 && result.values.every(r => r[flags] == null)) hide.push("flags");
  const firstFile = result.values.length ? result.values[0][result.columns.indexOf("source_file")] : null;
  show(
    el("p", {}, el("a", { href: "#tabelle" }, "← Tutte le tabelle")),
    el("h1", {}, `${key} · ${year}`),
    el("dl", { class: "facts" },
      el("dt", {}, "Pagine stampate"), el("dd", {}, meta.printed || "—"),
      el("dt", {}, "Volume"), el("dd", {}, firstFile || "—"),
      el("dt", {}, "Tabella SQL"), el("dd", {}, el("code", {}, meta.sql_table)),
      el("dt", {}, "Totali stampati"), el("dd", {},
        checks.length ? checks.map(c => `${c.status} ${c.n}`).join(" · ") : "nessuno",
        " ", el("span", { class: "count" }, "(somme delle righe confrontate con i totali che la tabella stampa)")),
      meta.notes && [el("dt", {}, "Note"), el("dd", {}, meta.notes)]),
    AVVERTENZE[meta.sql_table] && el("p", { class: "warn" }, AVVERTENZE[meta.sql_table]),
    notes.length > 0 && el("p", { class: "lead" }, notes.map(n => n.text).join(" ")),
    totals.length > 0 && el("details", {}, el("summary", {}, "Totali come stampati"),
      el("ul", {}, totals.map(t => el("li", {}, `${t.label}: ${t.value_raw} `,
        el("a", { href: fonteUrl(firstFile, t.pdf_page), target: "_blank", rel: "noopener" }, `p. ${t.printed_page}`))))),
    el("p", {}, el("a", { href: "#sql=" + encodeURIComponent(sql) }, "Apri questa tabella nell'editor SQL")),
    grid(result, { printed: true, hide, table: meta.sql_table, name: key.replace(/\//g, "_") + "_" + year }));
}

function viewSql(initial) {
  setView("sql");
  const text = el("textarea", { spellcheck: "false", "aria-label": "Query SQL" });
  text.value = initial || `SELECT operatore, autorizzazioni, valore_euro, source_file, pdf_page
FROM mae_a2
WHERE year = 2025 AND row_kind = 'data'
ORDER BY valore_euro DESC
LIMIT 20`;
  const out = el("div");
  const share = el("button", { onclick: async () => {
    try { await navigator.clipboard.writeText(location.href); share.textContent = "Link copiato"; }
    catch { share.textContent = "Copia il link dalla barra degli indirizzi"; }
    setTimeout(() => (share.textContent = "Condividi"), 2000);
  } }, "Condividi");

  function run() {
    const sql = text.value.trim();
    history.replaceState(null, "", "#sql=" + encodeURIComponent(sql));
    const t0 = performance.now();
    try {
      const results = db.exec(sql);
      const ms = Math.round(performance.now() - t0);
      const last = results[results.length - 1];
      out.replaceChildren(
        el("p", { class: "count" }, `${ms} ms`),
        last ? grid(last, { name: "query" }) : el("p", { class: "status" }, "Nessuna riga."));
    } catch (e) {
      out.replaceChildren(el("p", { class: "error" }, e.message));
    }
  }
  text.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } });

  show(
    el("h1", {}, "SQL"),
    el("p", { class: "lead" },
      "Interroga direttamente il database. Gira tutto nel tuo browser, in sola lettura: nessuna query può cambiare i dati. " +
      "Le righe con source_file e pdf_page ricevono il link alla pagina della relazione. " +
      "Nelle tabelle le righe hanno un tipo (row_kind: data, group, subtotal, total): per sommare, filtra row_kind = 'data'."),
    el("div", { class: "sqlgrid" },
      schema(text, run),
      el("div", {}, text,
        el("div", { class: "controls" },
          el("button", { class: "primary", onclick: run }, "Esegui"),
          el("span", { class: "count" }, "Ctrl+Invio"), share),
        out)));
  run();
}

function schema(text, run) {
  const tables = objects(`SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY
                          name IN ('catalogo', 'fonte', 'source_file', 'column_def', 'printed_total',
                                   'table_note', 'extract_issue', 'check_result'), name`);
  const keys = new Map(objects("SELECT DISTINCT sql_table, table_key FROM column_def")
    .map(r => [r.sql_table, r.table_key]));
  return el("aside", { class: "schema" }, el("h2", {}, "Tabelle"), tables.map(({ name }) => {
    const cols = objects(`PRAGMA table_info(${ident(name)})`);
    return el("details", {},
      el("summary", {}, name, keys.has(name) && el("span", { class: "count" }, ` ${keys.get(name)}`),
        el("button", { onclick: e => { e.preventDefault(); text.value = `SELECT * FROM ${ident(name)} LIMIT 100`; run(); } }, "apri")),
      el("ul", {}, cols.map(c => el("li", { title: c.type }, c.name,
        labels.has(`${name}.${c.name}`) && el("span", {}, ` · ${labels.get(`${name}.${c.name}`)}`)))));
  }));
}

function viewMetodo() {
  setView("metodo");
  const [c] = objects(`SELECT count(*) AS tot, count(sql_table) AS done,
                       group_concat(DISTINCT year) AS years FROM catalogo`);
  const sqlTables = objects("SELECT DISTINCT sql_table FROM column_def ORDER BY sql_table").map(r => r.sql_table);
  const nrows = sqlTables.reduce((n, t) => n + q(`SELECT count(*) FROM ${ident(t)}`).values[0][0], 0);
  const checks = objects("SELECT status, count(*) AS n FROM check_result GROUP BY status ORDER BY n DESC");
  const issues = q("SELECT count(*) FROM extract_issue").values[0][0];
  const vols = objects("SELECT year, volume, pages, url, sha256 FROM fonte ORDER BY year DESC, file");
  show(
    el("h1", {}, "Metodo"),
    el("p", { class: "lead" },
      "ArmTrace legge le relazioni annuali al Parlamento sull'export di armamenti (legge 185/1990) " +
      "e ne trasforma le tabelle in un database. Solo fonti ufficiali; nessun valore è corretto, completato o stimato."),
    el("dl", { class: "facts" },
      el("dt", {}, "Anni"), el("dd", {}, c.years),
      el("dt", {}, "Tabelle trovate"), el("dd", {}, nf0.format(c.tot)),
      el("dt", {}, "Tabelle estratte"), el("dd", {}, `${nf0.format(c.done)} (${nf0.format(nrows)} righe)`),
      el("dt", {}, "Totali stampati verificati"), el("dd", {}, checks.map(x => `${x.status} ${nf0.format(x.n)}`).join(" · ")),
      el("dt", {}, "Problemi di lettura registrati"), el("dd", {}, nf0.format(issues))),
    el("h2", {}, "Come si legge una riga"),
    el("p", {}, "Ogni tabella SQL corrisponde a un codice di tabella stampato (MAE/A1 → mae_a1) ed è condivisa fra gli anni. " +
      "Ogni riga porta l'anno, il file della relazione, la pagina del PDF e il numero di pagina stampato: il link «Fonte» " +
      "apre quella pagina sul sito della Camera. Accanto a ogni valore numerico c'è il testo come stampato (colonne _raw); " +
      "un trattino stampato è vuoto, mai zero."),
    el("p", {}, "Le tabelle stampano i propri totali: per ogni totale, la somma delle righe estratte è confrontata con il valore stampato " +
      "(PASS: uguale; ROUNDING: entro 2 centesimi di arrotondamento; UNCHECKED: non confrontabile)."),
    el("h2", {}, "Limiti"),
    el("ul", {},
      el("li", {}, "Le tabelle non ancora estratte compaiono nell'elenco ma non hanno righe."),
      el("li", {}, "I nomi (aziende, paesi, banche) sono come stampati: la stessa azienda può comparire scritta in modi diversi."),
      el("li", {}, "Parte delle tabelle MAE è stampata con caratteri senza mappa: il testo è ricostruito glifo per glifo; " +
        "quello che non si può riconoscere resta segnalato in flags, non indovinato."),
      el("li", {}, "Le relazioni prima del 2011 non hanno testo selezionabile e non sono incluse.")),
    el("h2", {}, "Fonti"),
    el("ul", {}, vols.map(v => el("li", {}, `${v.year} ${v.volume}, ${v.pages} pagine: `,
      el("a", { href: v.url, target: "_blank", rel: "noopener" }, "PDF ufficiale"),
      " ", el("small", { class: "count" }, `SHA-256 ${v.sha256.slice(0, 12)}…`)))),
    el("h2", {}, "Scarica"),
    el("p", {}, el("a", { href: "armtrace.sqlite", download: true }, "armtrace.sqlite"),
      " — il database intero, apribile con qualsiasi client SQLite."));
}

// ---------- routing ----------

function route() {
  const h = decodeURIComponent(location.hash.slice(1));
  try {
    if (h.startsWith("sql")) return viewSql(h.startsWith("sql=") ? h.slice(4) : "");
    if (h.startsWith("tabella/")) {
      const [, year, ...key] = h.split("/");
      return viewTabella(Number(year), key.join("/"));
    }
    if (h === "metodo") return viewMetodo();
    if (h.startsWith("domanda/")) return viewDomanda(h.slice(8));
    if (h.startsWith("tabelle")) return viewTabelle(h === "tabelle/tutte");
    return viewDomande();
  } catch (e) {
    show(el("p", { class: "error" }, e.message));
  } finally {
    window.scrollTo(0, 0);
  }
}

async function start() {
  try {
    const SQL = await initSqlJs({ locateFile: f => SQLJS + f });
    const res = await fetch("armtrace.sqlite");
    if (!res.ok) throw new Error(`armtrace.sqlite: ${res.status}`);
    db = new SQL.Database(new Uint8Array(await res.arrayBuffer()));
    db.exec("PRAGMA query_only = ON");
    domande = await (await fetch("domande.json")).json();
    for (const r of objects("SELECT file, url FROM fonte")) fonti.set(r.file, r.url);
    for (const r of objects("SELECT DISTINCT sql_table, col, label_it FROM column_def"))
      labels.set(`${r.sql_table}.${r.col}`, r.label_it);
  } catch (e) {
    show(el("p", { class: "error" }, `Il database non si è caricato: ${e.message}`));
    return;
  }
  addEventListener("hashchange", route);
  route();
}

start();
