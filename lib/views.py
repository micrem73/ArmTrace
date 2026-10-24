"""A printed table rebuilt from its facts, and checked against the print.

The reports print visualisations (maintainer, 2026-10-20): a table's data rows
are facts; its group headings, subtotals, totals and summary lines are layout,
generated from the facts. So a table is stored as facts plus the figures it
prints (`printed_figure`), and rebuilt on request by `render`:

  facts      the data rows, in printed order (`row_ord`), each with its group
             (MAE A1's operator, MEF II's carried `Ditta`)
  figures    the printed subtotal and total rows, at the place they are
             printed: what the facts must add up to
  summary    what a table prints outside its grid (MAE A1's last page)

A rebuilt row shows what the report prints: a group heading once per group, a
carried name on its group's first row only, each subtotal and total with its
printed figure, and beside it the sum of the facts, flagged when the two
differ (`checks`: PASS, ROUNDING, FAIL; UNCHECKED where no rule recomputes a
summary line). The checks are the import's oracle and a view's footnotes at
once, so the two cannot drift apart.
"""
import re
from decimal import Decimal

from lib import extract
from lib import grammar as gra


# The rows a table prints that are not facts: a group's subtotal, the total, and
# a summary line in the grid known by its `totals.tsv` label (`line`: T20's
# `Altri`, B4's `di cui` shares), which belongs to the table, not to a group.
FIGURE_KINDS = ("subtotal", "total", "line")


def split(result):
    """(facts, figures, summary) of a table as read (`extract.read_table`)."""
    facts = [r for r in result.rows if r["kind"] == "data"]
    figures = [r for r in result.rows if r["kind"] in FIGURE_KINDS]
    return facts, figures, list(result.totals)


def from_db(db, key, year, spec=None):
    """(facts, figures, summary) of a table as `ExtractTables.py` stored it:
    what `render` needs to rebuild the printed table from the database alone."""
    spec = spec or extract.specs()[key]
    name = extract.sql_name(key)
    by_col = {c.key: c for c in spec.printed}
    order, year_of = [], {}
    for i, col, label in db.execute("SELECT ordinal, col, label_it FROM column_def "
                                    "WHERE table_key=? AND year=? ORDER BY ordinal",
                                    (key, int(year))):
        order.append(by_col.get(col, extract.GAP))
        if order[-1].type == "years":
            year_of[i] = int(extract.YEAR.search(label).group())
    cur = db.execute(f"SELECT * FROM {name} WHERE year=? ORDER BY row_ord", (int(year),))
    names = [d[0] for d in cur.description]
    lines = {}
    for values in cur.fetchall():
        row = dict(zip(names, values))
        lines.setdefault(row["row_ord"], []).append(row)
    linked = _linked(lines, spec)
    rowsums = {}                # a row's own printed total (3B), by row and ordinal
    for row_ord, i, raw in db.execute(
            "SELECT row_ord, ordinal, value_raw FROM printed_figure WHERE table_key=? "
            "AND year=? AND kind='rowsum'", (key, int(year))):
        rowsums[(row_ord, i)] = raw
    facts = []
    for row_ord, rows in lines.items():
        row = rows[0]
        cells, implied = [], {}
        for i, col in enumerate(order):
            if col.type == "years":
                hit = next((r for r in rows if r["anno"] == year_of[i]), {})
                cells.append(hit.get(f"{col.key}_raw") or "")
            elif col.type in ("gap", "group"):
                cells.append("")        # a group is printed as its heading
            elif col.type == "rowsum":
                cells.append(rowsums.get((row_ord, i)) or "")
            elif col.type in ("amount", "number", "date"):
                # A `once` value is on one year's row of the line (3B's edition year).
                held = next((r for r in rows if r[col.key] is not None), row) \
                    if col.once else row
                cells.append(held[f"{col.key}_raw"] or "")
                if held[f"{col.key}_raw"] is None and held[col.key] is not None:
                    implied[col.key] = held[col.key]    # stored, not printed
            else:
                cells.append(row[col.key] or "")
        # A1: a description copied onto a second amount's row is not printed there.
        copied = re.search(r"copied from row \d+: ([\w, ]+)", row.get("flags") or "")
        keys = copied.group(1).split(", ") if copied else []
        facts.append({"kind": "data", "row_ord": row_ord, "pdf_page": row["pdf_page"],
                      "copied": [i for i, c in enumerate(order) if c.key in keys],
                      "printed_page": row["printed_page"], "order": order, "cells": cells,
                      "year_of": year_of, "group": row[spec.group.key] if spec.group else None,
                      "linked": linked.get(row_ord), "implied": implied})
    figures = {}
    for kind, row_ord, pdf_page, printed_page, grp, i, raw in db.execute(
            "SELECT kind, row_ord, pdf_page, printed_page, grp, ordinal, value_raw "
            "FROM printed_figure WHERE table_key=? AND year=? "
            "AND kind NOT IN ('summary', 'rowsum') "
            "ORDER BY row_ord, ordinal", (key, int(year))):
        fig = figures.setdefault(row_ord, {
            "kind": kind, "row_ord": row_ord, "pdf_page": pdf_page,
            "printed_page": printed_page, "group": grp, "order": order,
            "cells": [""] * len(order), "year_of": year_of})
        fig["cells"][i] = raw
    summary = [tuple(r) for r in db.execute(
        "SELECT pdf_page, printed_page, label, value_raw FROM printed_figure "
        "WHERE table_key=? AND year=? AND kind='summary' ORDER BY rowid", (key, int(year)))]
    return facts, list(figures.values()), summary


def _linked(lines, spec):
    """{row_ord: '→ <amount> · <opener>'}: what each row of a `link` table joins
    (A1: its amount and the date opening its authorisation), as printed, so the
    links can be judged against the page."""
    if not spec.link:
        return {}
    amount, _item, opener = spec.link.link
    first = {k: rows[0] for k, rows in lines.items()}
    def raw(row_ord, col):
        row = first.get(row_ord) or {}
        return row.get(f"{col}_raw") or row.get(col) or "(none printed)"
    out = {}
    for k, row in first.items():
        if row.get(spec.link.key) is not None:
            unit = row.get(spec.unit.key) if spec.unit else None
            out[k] = f"→ {raw(row[spec.link.key], amount)}" + (
                f" · {raw(unit, opener)}" if unit is not None else "")
    return out


def _group_of(row, spec):
    if spec.group:
        return row["group"]
    carry = [i for i, c in enumerate(row["order"]) if c.type == "carry"]
    return row["cells"][carry[0]] if carry else None


def _figure_checks(row, scope, spec, everything=(), above=()):
    rule = next((expr for rx, expr in spec.totals if rx.search(row["cells"][0] or "")), None)
    if rule is not None:
        return _line_checks(row, rule.split(), everything, above)
    # A total over blocks (`block`: T13's Categoria and Tipologia) is checked
    # against each block on its own: both add up to it.
    at = next((i for i, c in enumerate(row["order"]) if c.type == "block"), None)
    if at is not None and row["kind"] == "total":
        against = row["order"][at].against or {}
        blocks = {}
        for r in scope:
            blocks.setdefault(r["cells"][at], []).append(r)
        out = []
        for block, rows in blocks.items():
            # A block that breaks down one row of another (T23: the categories
            # add up to MATERIALI) is checked against that row.
            target = next((r for r in scope if against.get(block) in r["cells"]), row)
            tag = f" ({block} = {against[block]})" if target is not row else f" ({block})"
            out += [(name + tag, printed, computed, status)
                    for name, printed, computed, status in _sums(target, rows, spec)]
        return out
    return _sums(row, scope, spec) + _rowsum_checks(row)


def _rowsum_checks(row):
    """A row's own printed total (`rowsum`: 3B's `Nel quadriennio`) against the
    row's cells in the year column it adds up."""
    out = []
    for i, col in enumerate(row["order"]):
        if col.type != "rowsum" or not row["cells"][i]:
            continue
        value, _ = extract.parse("amount", row["cells"][i])
        if value is None:
            continue
        computed = sum(extract.parse("amount", c)[0] or 0
                       for c, other in zip(row["cells"], row["order"]) if other.key == col.of)
        out.append((f"{row['kind']} p{row['pdf_page']} {col.key} row {row['row_ord']}",
                    row["cells"][i], computed, extract._status(computed, value)))
    return out


def _line_checks(row, rule, facts, above):
    """A summary line printed in the grid, checked by its `totals.tsv` rule,
    column by column (a year column cell by cell): `sum rows of Y` the facts,
    in the edition's year only (another year's line may cover rows not
    printed, T20); `sum lines above` the summary lines printed above it;
    `sum over subtotal` the subtotals above it (B4's total: its countries are
    rounded, its areas are not, as A1's); `-` none (UNCHECKED)."""
    out = []
    latest = max(row["year_of"].values(), default=None)
    for i, col in enumerate(row["order"]):
        if not col.sum:
            continue
        printed = row["cells"][i]
        value = _printed_value(printed)
        if value is None:
            continue
        year = row["year_of"].get(i)
        name = f"{row['cells'][0]} p{row['pdf_page']} {col.key}" + (f" {year}" if year else "")
        if rule == ["sum", "rows", "of", "Y"] and year == latest:
            scope = facts
        elif rule == ["sum", "lines", "above"]:
            scope = above
        elif rule == ["sum", "over", "subtotal"]:
            scope = [r for r in above if r["kind"] == "subtotal"]
        else:
            out.append((name, printed, None, "UNCHECKED"))
            continue
        computed = sum(extract.parse("amount", r["cells"][i])[0] or 0 for r in scope)
        out.append((name, printed, computed, extract._status(computed, value)))
    return out


def _printed_value(text):
    """A printed figure's value, a comment after it set aside: A2's and B4's
    `265.139.737,31 (pari a 3,43 %)` (`ExtractTables.figures` stores it so)."""
    return extract.parse("amount", (text or "").split(" (")[0].strip())[0]


def _sums(row, scope, spec):
    out = []
    # A year column prints one cell per year: each is checked on its own.
    for i, col in enumerate(row["order"]):
        if col.type != "years" or not col.sum:
            continue
        value = _printed_value(row["cells"][i])
        if value is None:
            continue
        computed = sum(extract.parse("amount", r["cells"][i])[0] or 0 for r in scope)
        out.append((f"{row['kind']} p{row['pdf_page']} {col.key} {row['year_of'][i]}",
                    row["cells"][i], computed, extract._status(computed, value)))
    for col in (c for c in spec.printed if c.sum and c.type != "years"):
        printed = extract._cell(row, col.key)
        value = _printed_value(printed)
        if value is None:
            continue
        computed = sum(extract._effective(r, col, spec) for r in scope)
        # The group names the subtotal: A1 p410 closes two operators.
        whose = f" ({row['group']})" if row["kind"] == "subtotal" and row["group"] else ""
        out.append((f"{row['kind']} p{row['pdf_page']} {col.key}{whose}", printed,
                    computed, extract._status(computed, value)))
    return out


def render(facts, figures, summary, spec):
    """The table as printed, rebuilt: [{kind, group, cells, pdf_page, checks}].

    `kind` is `group` (a heading), `data`, `subtotal`, `total` or `summary`.
    Cells are the printed text: a fact's own, a figure's printed values."""
    out, scope, everything, lines = [], [], [], []
    heading, run = object(), object()
    carry = None
    for row in sorted(facts + figures, key=lambda r: r["row_ord"]):
        if carry is None:
            carry = {i for i, c in enumerate(row["order"]) if c.type == "carry"}
        if row["kind"] == "data":
            group = _group_of(row, spec)
            if spec.group and group != heading:
                out.append({"kind": "group", "group": group, "cells": [group],
                            "pdf_page": row["pdf_page"], "checks": []})
                heading = group
            first = group != run or not scope
            blank = (carry if not first else set()) | set(row.get("copied", ()))
            cells = [("" if i in blank else c) for i, c in enumerate(row["cells"])]
            out.append({"kind": "data", "group": group, "cells": cells,
                        "pdf_page": row["pdf_page"], "checks": _rowsum_checks(row),
                        "linked": row.get("linked")})
            scope.append(row)
            everything.append(row)
            run = group
        else:
            within = scope if row["kind"] == "subtotal" else everything
            out.append({"kind": row["kind"], "group": row["group"], "cells": list(row["cells"]),
                        "pdf_page": row["pdf_page"],
                        "checks": _figure_checks(row, within, spec, everything, lines)})
            lines.append(row)
            if row["kind"] == "subtotal":
                scope, run = [], object()
    for pdf_page, _printed_page, label, raw in summary:
        out.append({"kind": "summary", "group": None, "cells": [label, raw],
                    "pdf_page": pdf_page,
                    "checks": [_summary_check(label, raw, facts, figures, spec)]})
    return out


def checks(facts, figures, summary, spec):
    """[(check, printed, computed, status)] of every printed figure."""
    return [c for row in render(facts, figures, summary, spec) for c in row["checks"]]


def _summary_check(label, raw, facts, figures, spec):
    rule = next(((m, expr) for rx, expr in spec.totals if (m := rx.search(label))), None)
    value, _ = extract.parse("amount", raw)
    if rule is None or value is None:
        return (f"printed {label!r}", raw, None, "UNCHECKED")
    m, expr = rule
    words = expr.split()
    if words == ["-"]:
        return (f"printed {label!r}", raw, None, "UNCHECKED")
    if words[0] == "count":
        computed = sum(1 for r in facts if extract._cell(r, words[1])
                       or words[1] in r.get("implied", {}))
        value = gra.parse_number(raw)
    elif words[-2:] == ["over", "subtotal"]:
        # The printed subtotals added up: A1's grand EURO total is theirs,
        # each subtotal rounded, and the facts give the roundings' sum more.
        computed = sum((extract.parse("amount", extract._cell(r, words[1]))[0] or 0)
                       for r in figures if r["kind"] == "subtotal")
    else:
        col = next(c for c in spec.printed if c.key == words[1])
        scope = facts
        if "where" in words:
            key, want = words[words.index("where") + 1].split("=")
            want = m.group(1) if want == "$1" else want
            scope = [r for r in scope if extract._cell(r, key) == want]
        if "unless" in words:
            skip = words[words.index("unless") + 1]
            scope = [r for r in scope if not extract._cell(r, skip)]
        computed = sum(extract._effective(r, col, spec) for r in scope)
    return (f"printed {label!r}", raw, computed, extract._status(computed, value))


def cross_checks(db, year):
    """[(year, table_key, check, printed, computed, status)]: each summary table
    in `lib/data/views.tsv` against the table it summarises, per group."""
    out = []
    for row in extract._rows("views.tsv"):
        key, of = row["table"], row["of"]
        s_name, d_name = extract.sql_name(key), extract.sql_name(of)
        if not all(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (n,)).fetchone()
                   for n in (s_name, d_name)):
            continue
        s_group, d_group = row["group"].split("=")     # company ids (`id_ditta`)
        d_names = extract.specs()[of].company.of
        sums = dict(db.execute("SELECT col, sum_sql FROM column_def WHERE table_key=? "
                               "AND year=? AND sum_sql IS NOT NULL", (of, int(year))))
        ordinal = dict(db.execute("SELECT col, ordinal FROM column_def WHERE table_key=? "
                                  "AND year=?", (of, int(year))))
        for pair in row["cols"].split():
            s_col, d_expr = pair.split("=")
            fn, _, inner = d_expr.rstrip(")").partition("(")
            if fn == "subtotal":
                # The detail table's printed subtotals: A2 restates A1's, rounding and
                # all. A subtotal names its group as printed: its id is that name's.
                id_of = dict(db.execute(f"SELECT DISTINCT {d_names}, {d_group} FROM {d_name} "
                                        "WHERE year=?", (int(year),)))
                found = [(id_of.get(grp), value) for grp, value in db.execute(
                    "SELECT grp, value FROM printed_figure WHERE table_key=? AND year=? "
                    "AND kind='subtotal' AND ordinal=?", (of, int(year), ordinal[inner]))]
            elif fn == "count":
                found = db.execute(f"SELECT {d_group}, count({inner}_raw) FROM {d_name} "
                                   "WHERE year=? GROUP BY 1", (int(year),))
            else:
                found = db.execute(f"SELECT {d_group}, round(total({sums.get(fn, fn)}), 2) "
                                   f"FROM {d_name} WHERE year=? GROUP BY 1", (int(year),))
            detail = {}
            for cid, value in found:
                detail[cid] = detail.get(cid, Decimal(0)) + Decimal(str(value or 0))
            summary, names = {}, {}
            s_names = extract.specs()[key].company.of
            for cid, name, raw in db.execute(f"SELECT {s_group}, {s_names}, {s_col}_raw "
                                             f"FROM {s_name} WHERE year=? ORDER BY row_ord",
                                             (int(year),)):
                summary[cid] = summary.get(cid, Decimal(0)) + (gra.parse_number(raw) or 0)
                names.setdefault(cid, []).append(" ".join(name.split()))
            for cid, value in summary.items():
                check = f"same as {of} {s_col} ({' + '.join(names[cid])})"
                if cid is None or cid not in detail:
                    out.append((int(year), key, check, str(value), None, "FAIL"))
                    continue
                got = detail[cid]
                out.append((int(year), key, check, str(value), str(got),
                            extract._status(got, value)))
    return out


# The register of companies across years (maintainer, 2026-10-22): one row per
# register number, from the lines of Difesa A5/2 each year prints (a registration,
# a suspension, a cancellation), each of which stays traceable to its page.
# The name and id are those printed last; `motivazione`, the last one printed.
REGISTER = """
CREATE VIEW IF NOT EXISTS registro_imprese AS
SELECT d.numero_iscrizione,
       (SELECT denominazione FROM difesa_a5_2 x WHERE x.numero_iscrizione = d.numero_iscrizione
        ORDER BY year DESC, row_ord DESC LIMIT 1) AS denominazione,
       (SELECT id_ditta FROM difesa_a5_2 x WHERE x.numero_iscrizione = d.numero_iscrizione
        ORDER BY year DESC, row_ord DESC LIMIT 1) AS id_ditta,
       max(d.data_iscrizione) AS data_iscrizione,
       max(d.data_sospensione) AS data_sospensione,
       max(d.data_cancellazione) AS data_cancellazione,
       (SELECT motivazione FROM difesa_a5_2 x WHERE x.numero_iscrizione = d.numero_iscrizione
        AND motivazione IS NOT NULL ORDER BY year DESC, row_ord DESC LIMIT 1) AS motivazione
FROM difesa_a5_2 d GROUP BY d.numero_iscrizione
"""


def add_register(db):
    """`registro_imprese`, where Difesa A5/2 is in `db`."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='difesa_a5_2'").fetchone():
        db.execute(REGISTER)
