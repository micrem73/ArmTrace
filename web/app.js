"use strict";
// ArmTrace explorer: the whole database runs in the browser (sql.js), read-only.
// Views by #hash: #tabelle[/tutte], #tabella/<year>/<key>, #sql[=<query>].

const SQLJS = "https://cdn.jsdelivr.net/npm/sql.js@1.10.3/dist/";
const PAGE = 500;                       // rows rendered at a time
// Columns every printed row carries: they place the row in its source, and
// are shown as the "Fonte" link, not as data (tools/build_site.py).
const PLACE = ["year", "source_file", "pdf_page", "printed_page", "row_ord"];
// Numbers that are identities, not amounts: never 2.025 for a year.
const PLAIN = /^(year|anno|pdf_page|printed_page|row_ord|ordinal|pages|nr)$/;

let db;
const fonti = new Map();                // source file -> official URL (names carry the year)
const labels = new Map();               // "sql_table.col" -> printed Italian label
const main = document.getElementById("main");
// What a table's columns cannot say by themselves.
const AVVERTENZE = {
  mae_a1: "La colonna EURO è stampata vuota per le licenze in euro: lì il valore è l'Ammontare. " +
    "Le licenze con un Valore ai fini doganali non entrano nel totale EURO. Il valore è sulla prima riga " +
    "di ogni licenza; le righe seguenti ne descrivono i materiali.",
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

// ---------- column filters ----------
// A filter is text to find in the cell as shown, or, on a number column, a
// comparison: >1000, <=5, =0, 10-20. Numbers are written the Italian way
// (1.000,5) or plainly (1000.5).

function parseNumber(s) {
  s = s.trim().replace(/\s/g, "");
  if (/,/.test(s)) s = s.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
  return s === "" ? NaN : Number(s);
}

function makeTest(text) {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  const cmp = t.match(/^(>=|<=|>|<|=)\s*(.+)$/);
  const range = t.match(/^(-?[\d.,]+)\s*-\s*(-?[\d.,]+)$/);
  if (cmp && !Number.isNaN(parseNumber(cmp[2]))) {
    const n = parseNumber(cmp[2]);
    const op = { ">": x => x > n, "<": x => x < n, ">=": x => x >= n, "<=": x => x <= n, "=": x => x === n }[cmp[1]];
    return (value) => typeof value === "number" && op(value);
  }
  if (range && !Number.isNaN(parseNumber(range[1])) && !Number.isNaN(parseNumber(range[2]))) {
    const lo = parseNumber(range[1]), hi = parseNumber(range[2]);
    return (value) => typeof value === "number" && value >= lo && value <= hi;
  }
  return (value, shown) => shown != null && shown.toLowerCase().includes(t);
}

// ---------- result grid ----------
// One renderer for every view. Each header sorts (click) and filters (the box
// under it). Options:
//   printed  pair X with X_raw: show the printed text, sort and compare by the parsed value
//   hide     column names not shown (still exported)
//   headers  {col: label} for the header
//   table    the SQL table, for its printed labels
//   cell     (col, value, row, at) -> Node | undefined, to override a cell

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

  // the text a cell shows, which is what a text filter searches
  const text = (c, row) => {
    const v = row[at[c]];
    if (raw[c] != null && row[raw[c]] != null) return row[raw[c]];
    if (v == null) return null;
    if (v instanceof Uint8Array) return `[${v.length} byte]`;
    return PLAIN.test(c) ? String(v) : fmt(v);
  };

  let rows = values.slice();
  let limit = PAGE;
  const tests = {};                     // col -> test
  let sortCol = null, sortDir = 1;

  const body = el("tbody");
  const count = el("span", { class: "count" });
  const more = el("button", { onclick: () => { limit += PAGE; paint(); } }, "Mostra altre righe");
  const arrows = {};

  const matches = row => Object.entries(tests).every(([c, test]) => test(row[at[c]], text(c, row)));

  function cellFor(c, row) {
    const v = row[at[c]];
    const custom = opts.cell && opts.cell(c, v, row, at);
    if (custom !== undefined) return el("td", {}, custom);
    const s = text(c, row);
    if (s == null) return el("td", { class: "null" }, "—");
    return el("td", { class: typeof v === "number" || raw[c] != null ? "num" : null }, s);
  }

  function fonteCell(row) {
    const url = fonteUrl(row[at.source_file], row[at.pdf_page]);
    const page = at.printed_page != null && row[at.printed_page] != null ? row[at.printed_page] : row[at.pdf_page];
    return el("td", {}, url ? el("a", { href: url, target: "_blank", rel: "noopener" }, `p. ${page}`) : "—");
  }

  function paint() {
    const picked = rows.filter(matches);
    body.replaceChildren(...picked.slice(0, limit).map(row =>
      el("tr", {}, hasFonte && fonteCell(row), shown.map(c => cellFor(c, row)))));
    count.textContent = picked.length === values.length
      ? `${nf0.format(values.length)} righe`
      : `${nf0.format(picked.length)} di ${nf0.format(values.length)} righe`;
    more.hidden = picked.length <= limit;
    for (const [c, a] of Object.entries(arrows)) a.textContent = c === sortCol ? (sortDir > 0 ? " ▲" : " ▼") : "";
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

  const label = c => {
    const name = (opts.headers && opts.headers[c]) || (opts.table && labels.get(`${opts.table}.${c}`)) || c;
    return [name, !opts.headers && name !== c && el("small", {}, c)];
  };
  const head = el("tr", {}, hasFonte && el("th", {}, "Fonte"), shown.map(c => {
    arrows[c] = el("span", { class: "arrow" });
    return el("th", {},
      el("button", { class: "sort", title: "Ordina", onclick: () => sortBy(c) }, label(c), arrows[c]),
      el("input", { type: "search", class: "colfilter", placeholder: "filtra",
        title: "Testo da cercare; sui numeri anche >1000, <=5, =0, 10-20",
        "aria-label": `Filtra ${c}`,
        oninput: e => {
          const test = makeTest(e.target.value);
          if (test) tests[c] = test; else delete tests[c];
          limit = PAGE;
          paint();
        } }));
  }));

  const controls = el("div", { class: "controls" },
    el("button", { onclick: () => { sortCol = null; rows = values.slice(); paint(); } }, "Ordine stampato"),
    el("button", { onclick: () => csv(columns, rows.filter(matches), at, opts.name) }, "Scarica CSV"),
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

// ---------- views ----------

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
        if (c !== "key") return undefined;
        return row[at.sql_table]
          ? el("a", { href: `#tabella/${row[at.year]}/${encodeURIComponent(v)}` }, v)
          : el("span", {}, v, " ", el("span", { class: "badge" }, "da estrarre"));
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
  const sql = `SELECT * FROM ${ident(meta.sql_table)}\nWHERE year = ${Number(year)}\nORDER BY row_ord`;
  const result = q(sql);
  const flags = result.columns.indexOf("flags");
  const hide = [...PLACE];
  if (flags >= 0 && result.values.every(r => r[flags] == null)) hide.push("flags");
  const file = result.values.length ? result.values[0][result.columns.indexOf("source_file")] : null;
  show(
    el("p", {}, el("a", { href: "#tabelle" }, "← Tutte le tabelle")),
    el("h1", {}, `${key} · ${year}`),
    el("dl", { class: "facts" },
      el("dt", {}, "Pagine stampate"), el("dd", {}, meta.printed || "—"),
      el("dt", {}, "Volume"), el("dd", {}, file || "—"),
      el("dt", {}, "Tabella SQL"), el("dd", {}, el("code", {}, meta.sql_table))),
    AVVERTENZE[meta.sql_table] && el("p", { class: "warn" }, AVVERTENZE[meta.sql_table]),
    el("p", {}, el("a", { href: "#sql=" + encodeURIComponent(sql) }, "Apri questa tabella nell'editor SQL")),
    grid(result, { printed: true, hide, table: meta.sql_table, name: key.replace(/\//g, "_") + "_" + year }));
}

function viewSql(initial) {
  setView("sql");
  const text = el("textarea", { spellcheck: "false", "aria-label": "Query SQL" });
  text.value = initial || `SELECT operatore, autorizzazioni, valore_euro, source_file, pdf_page
FROM mae_a2
WHERE year = 2025
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
      "Le righe con source_file e pdf_page ricevono il link alla pagina della relazione."),
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
  const keys = new Map(objects("SELECT DISTINCT sql_table, table_key FROM column_def")
    .map(r => [r.sql_table, r.table_key]));
  // printed tables first, then the bookkeeping
  const tables = objects("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name")
    .map(r => r.name).sort((a, b) => (keys.has(b) - keys.has(a)) || a.localeCompare(b));
  return el("aside", { class: "schema" }, el("h2", {}, "Tabelle"), tables.map(name => {
    const cols = objects(`PRAGMA table_info(${ident(name)})`);
    return el("details", {},
      el("summary", {}, name, keys.has(name) && el("span", { class: "count" }, ` ${keys.get(name)}`),
        el("button", { onclick: e => { e.preventDefault(); text.value = `SELECT * FROM ${ident(name)} LIMIT 100`; run(); } }, "apri")),
      el("ul", {}, cols.map(c => el("li", { title: c.type }, c.name,
        labels.has(`${name}.${c.name}`) && el("span", {}, ` · ${labels.get(`${name}.${c.name}`)}`)))));
  }));
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
    return viewTabelle(h === "tabelle/tutte");
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
    for (const r of objects("SELECT file, url FROM fonte")) fonti.set(r.file, r.url);
    // A column printed under one label per year (T2: 2021 … 2025) has no
    // single label: its name is its header.
    for (const r of objects(`SELECT sql_table, col, max(label_it) AS label FROM column_def
                             GROUP BY sql_table, col HAVING count(DISTINCT label_it) = 1 AND max(label_it) != ''`))
      labels.set(`${r.sql_table}.${r.col}`, r.label);
  } catch (e) {
    show(el("p", { class: "error" }, `Il database non si è caricato: ${e.message}`));
    return;
  }
  addEventListener("hashchange", route);
  route();
}

start();
