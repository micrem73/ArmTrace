#!/usr/bin/env python3
"""Step 5 -- verify what step 4 extracted, against what the tables print.

    ./VerifyExtraction.py --year 2025

An audit of the *extraction*, in the same sense `VerifyTables.py` is an audit of
the *split*: it reads what was written and compares it against a witness the
archive itself supplies. It never writes inside `Out/PDF/` or `Out/CATALOG/`, and
everything it produces goes to `Out/VERIFY/`.

**The witnesses are the tables' own printed totals.** A1 closes every operator
group with its own subtotal -- `3.700.813,79  0,00` -- and MG10 prints
`Totale complessivo 262.920.093,96`. No amount of self-consistency in the
extractor can fake those: they are set in type by someone else's arithmetic.
The old CSV step never used them, which is why a wrong extraction could sit in
the output looking like data.

Five checks, each answering one question the others cannot:

    group.subtotal     does the SUM of a group's data rows equal the subtotal the
                       table prints for it, per column
    file.grand_total   the same, for a table that prints one total for the file
    row.reconciliation did every data band the extractor classified become a
                       record, and did every band it classified as a subtotal
                       become a total rather than a record
    group.empty        is a group header carrying zero data bands -- a boundary
                       detected wrongly, which would otherwise make the
                       per-group check pass vacuously
    text.coverage      are the extracted characters actually on the page, which
                       catches a column realigned onto the wrong label

**Every sum here is done in Python on `Decimal`, never in SQL.** sqlite has no
exact decimal: a `DECIMAL(20,4)` column has NUMERIC affinity, so `1234.56` is
stored as a double and `SUM()` accumulates in floating point. Reading a cell back
with `CAST(num_value AS TEXT)` and adding the strings is exact, because
sqlite renders a double with `%.15g` and a euro amount at 2 dp is 11 significant
digits. `check_decimals()` records the ceiling where that stops holding.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pymupdf

try:
    from CatalogueTables import CATALOG_DDL, open_catalog
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from CatalogueTables import CATALOG_DDL, open_catalog

import lib.geometry as geo
import lib.grammar as gra

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"

#: A total that is out, but by no more than the report's own per-row rounding can
#: account for. See `rounding_bound`.
ROUNDED = "ROUNDED"

#: Verdicts that mean "the extraction is wrong", used for the table-level roll-up
#: and anywhere a reader wants the defects alone.
BAD_VERDICTS = (FAIL,)

def _roll_up(fs):
    """A table's verdict from its findings' worst one.

    `ROUNDED` sits between `PASS` and `WARN`: the arithmetic reconciles to within
    what the report's own rounding allows, which is a qualified pass and not an
    absence of evidence. `FAIL` outranks it, and `WARN` outranks `PASS`.
    """
    order = (FAIL, WARN, ROUNDED, SKIP, PASS)
    present = {f.verdict for f in fs}
    for verdict in order:
        if verdict in present:
            return verdict
    return PASS


TOTAL_KINDS = ("subtotal", "total")
DATA_KINDS = ("data",)

#: What one printed figure can be out by, at most, when it was rounded to the cent.
HALF_CENT = Decimal("0.005")

#: A double rendered by `%.15g` round-trips exactly up to this many significant
#: digits. Beyond it sqlite has already lost digits and no reader can recover
#: them, so it is reported rather than silently accepted.
SIGNIFICANT_DIGITS = 15


class Finding:
    __slots__ = ("table", "check", "verdict", "detail", "page")

    def __init__(self, table, check, verdict, detail="", page=None):
        self.table = table
        self.check = check
        self.verdict = verdict
        self.detail = detail
        self.page = page

    def as_dict(self):
        return {"table": self.table, "check": self.check,
                "verdict": self.verdict, "detail": self.detail, "page": self.page}


def label_of(authority, article, code):
    return "/".join(x or "-" for x in (authority, article, code))


# --------------------------------------------------------------------------
# reading the extracted cells
# --------------------------------------------------------------------------

def read_cells(conn, authority, article, code, year):
    """Every extracted row of one table-year, as `(row_ord, col_ord, kind, group,
    col_key, raw, num)`.

    `num` is a `Decimal` parsed from the *text* rendering, never from the stored
    double -- see the module docstring.
    """
    rows = []
    for row_ord, col_ord, kind, group_ord, col_key, raw, num_text in conn.execute(
            "SELECT row_ord, col_ord, row_kind, group_ord, col_key, raw_text,"
            " CAST(num_value AS TEXT) FROM stg_cell WHERE authority=? AND"
            " article IS ? AND code=? AND year=? ORDER BY row_ord, col_ord",
            (authority, article, code, year)):
        value = None
        if num_text not in (None, ""):
            try:
                value = Decimal(str(num_text))
            except Exception:
                value = None
        rows.append((row_ord, col_ord, kind, group_ord, col_key, raw, value))
    return rows


# --------------------------------------------------------------------------
# check 1 + 2: the printed totals
# --------------------------------------------------------------------------

def values_by_column(rows, kinds, group_ord=None):
    """`{col_ord: set(values)}` -- used only to decide additive-ness."""
    out = defaultdict(set)
    for row_ord, col_ord, kind, group, _key, _raw, num in rows:
        if kind not in kinds or col_ord is None:
            continue
        if group_ord is not None and group != group_ord:
            continue
        if num is not None:
            out[col_ord].add(num)
    return out


def sum_by_column(rows, kinds, group_ord=None):
    """`{col_ord: Decimal}` over the rows of `kinds`, optionally one group only."""
    out = defaultdict(Decimal)
    for row_ord, col_ord, kind, group, _key, _raw, num in rows:
        if kind not in kinds or col_ord is None:
            continue
        if group_ord is not None and group != group_ord:
            continue
        if num is not None:
            out[col_ord] += num
    return out


def not_additive(printed_value, values):
    """True when a printed total is one of its own column's values.

    **A column whose printed total equals one of its data values is not being
    summed.** The archive prints several columns whose total is a *fact about the
    column* rather than an arithmetic sum of it, and comparing a sum against them
    is comparing two different quantities:

      | column | printed | the sum of its rows | what the figure is |
      |---|---|---|---|
      | `%` | 100 | 400.00 | the share, which must come to 100 |
      | `N. autorizzazioni` | 192 | 152.532.836.754,81 | a count of rows |
      | `Pos.` | 2 | 55 | a rank |

    These were 19 of the 36 `file.grand_total` failures, and every one was the
    verifier disagreeing with the archive about what a number means.

    The test is that the total appears among the column's own values. It is safe
    in the direction that matters: for a genuinely additive column the total
    equals one of its rows only when every other row is zero, and then the sum
    check would have passed anyway, so nothing is lost. What is avoided is
    inventing a defect out of a column the table never intended to be summed.
    """
    return printed_value in values


def rounding_columns(cols):
    """The column ordinals whose printed figure is a **conversion**, and so cannot
    be expected to equal the sum of the rounded per-row figures.

    `money_columns()` returns `(currency, amount, converted, val_fini)`. The first
    two are *raw*: the amount as it was contracted, printed exactly. The last two
    are derived -- `EURO` is a foreign amount restated at some rate, `Val. Fini
    Dog.` a computed customs value -- and each is rounded to the cent for print.
    Only the derived pair gets the rounding bound; the raw pair keeps exact
    equality, which is what is left to catch a misread digit.
    """
    if not cols:
        return frozenset()
    return frozenset(o for o in (cols[2], cols[3]) if o is not None)


def rounding_bound(contributors):
    """The most a total of `contributors` rounded figures can be out: `n × 0,005`.

    This is a **guarantee, not a guess**. Each printed figure carries at most half
    a cent of rounding, so the error over `n` of them cannot exceed `n/2` cents
    whatever the values are. For reference the *expected* size is far smaller --
    uniform rounding has a standard deviation of `0,005/sqrt(3)` per figure, so
    about `0,0029 * sqrt(n)` cents over `n` rows -- which is why the measured
    residuals are a cent or two even where `n` is in the hundreds.

    `n = 0` means nothing contributed, so there is nothing to be out by.
    """
    return Decimal(contributors) * HALF_CENT


def _within_rounding(rounding, col_ord, delta):
    """Is `delta` inside the rounding guarantee for this column?

    `rounding` is `{col_ord: contributors}` and holds **only derived columns**, so
    a raw column is never admitted whatever `delta` is. An absent entry means no
    bound was offered for that column, and absence is not permission.
    """
    if not rounding or col_ord not in rounding:
        return False
    return abs(delta) <= rounding_bound(rounding[col_ord])


def compare_totals(check, table, sums, printed, keys, group=None,
                   alt_sums=None, alt_label=None, present=None,
                   rounding=None):
    """Compare assembled sums against printed totals, per column.

    Equality is **exact**, with no tolerance band, for every column except the
    derived pair -- and that exception is a *bound*, not a band. AGENTS.md §6
    records the rule this follows: "mismatches are an error signal, not noise", and
    an arithmetic witness that tolerates an error is not a witness. So:

    - a **raw** column (`Valuta`, `Ammontare`) is checked for exact equality, and
      a cent off it is a FAIL, because nothing can explain a cent in a figure that
      was never converted;
    - a **derived** column (`EURO`, `Val. Fini Dog.`) is checked against
      `rounding_bound(n)`, the most the report's own per-row rounding can account
      for. Measured on A1's 159 groups: 149 reconcile **exactly**, and the 10 that
      do not average 257 rows against 60 for those that do, with mixed-sign deltas
      of ±0,01–0,02 -- one group holding ±0,02 across 1,069 rows. That is a random
      walk over rounded conversions, not a misread digit, and the residual is
      reported as `ROUNDED` with both the delta and the bound that admitted it.

    A column the total leaves blank has no witness here. That is reported as SKIP
    rather than passed silently, because a check that is quietly not run reads
    exactly like a check that quietly succeeded.

    **`present` is the same argument for a column that holds no numbers at all.**
    `sums` carries only columns that actually summed something, so a missing entry
    means "no value in this column anywhere in the group" -- not "the sum is
    zero". Treating the missing entry as `Decimal(0)` and calling the difference a
    FAIL is how DIFESA Annesso 3A came to report
    `Scadenza Autorizz.: assembled 0 vs printed 6106265.97`: that column holds
    *dates* (`17/12/2028`), and the printed figure beside it belongs to a
    two-label summary block whose columns do not align with the table's. There is
    nothing to sum and nothing to compare, and calling it a mismatch is the
    verifier inventing a defect.

    **`alt_sums` is the mixed-currency case, and it is measured.** A1 prints its
    subtotal in the `EURO` column, but the printed figure is the **EUR total**, not
    the sum of that one column -- see `eur_total()`.

    **`rounding` carries the row count behind a derived column**, so a residual
    inside the report's own rounding is reported as `ROUNDED` rather than FAIL.
    The rule is exact for every other column, and a `ROUNDED` finding still prints
    the delta *and* the bound that admitted it, so the reader can see the residual
    was inside the guarantee rather than merely tolerated.
    """
    findings = []
    if not printed:
        return [Finding(table, check, SKIP,
                        "no printed total to compare against")]
    checked = set(printed)
    for col_ord, printed_value in sorted(printed.items()):
        key = keys.get(col_ord) or f"column {col_ord}"
        where = f"group {group}" if group is not None else "file"
        if col_ord not in sums:
            # Nothing summed in this column, so there is no claim to check.
            findings.append(Finding(table, check, SKIP,
                                    f"{where} · {key}: the total prints "
                                    f"{printed_value} but this column holds no "
                                    f"number in the "
                                    f"{'group' if group is not None else 'file'}"
                                    f", so there is no witness", col_ord))
            continue
        actual = sums[col_ord]
        if actual == printed_value:
            findings.append(Finding(table, check, PASS,
                                    f"{where} · {key}: {actual} == printed "
                                    f"{printed_value}", col_ord))
            continue
        if present and printed_value in (present.get(col_ord) or ()):
            findings.append(Finding(table, check, SKIP,
                                    f"{where} · {key}: the total "
                                    f"{printed_value} is one of the column's own "
                                    f"values, so it reports the column rather "
                                    f"than summing it; there is no witness",
                                    col_ord))
            continue
        alternative = (alt_sums or {}).get(col_ord)
        if alternative is not None and alternative == printed_value:
            findings.append(Finding(table, check, PASS,
                                    f"{where} · {key}: printed {printed_value} "
                                    f"is {alt_label} ({alternative}), not this "
                                    f"column alone ({actual})", col_ord))
            continue
        delta = actual - printed_value
        # **A FAIL must report the reading it actually stands on.** With two
        # candidates -- the column alone, and `alt_sums` -- the deltas differ by
        # orders of magnitude, and reporting the wrong one sends the reader to the
        # wrong page. A1 group 384 reads `delta 26112923.09` under the column sum
        # while the mixed-currency rule puts it out by **0,02**; those are the same
        # defect, and only one of them is worth a page.
        if alternative is not None:
            alt_delta = alternative - printed_value
            if abs(alt_delta) < abs(delta):
                if _within_rounding(rounding, col_ord, alt_delta):
                    bound = rounding_bound(rounding[col_ord])
                    findings.append(Finding(
                        table, check, ROUNDED,
                        f"{where} · {key}: printed {printed_value} is "
                        f"{alt_label} ({alternative}), which is {alt_delta} away "
                        f"— inside the {bound} the report's own per-row "
                        f"rounding to the cent allows over "
                        f"{rounding[col_ord]} rounded figures", col_ord))
                    continue
                findings.append(Finding(
                    table, check, FAIL,
                    f"{where} · {key}: printed {printed_value} is {alt_label} "
                    f"({alternative}), off by {alt_delta}; the column alone sums "
                    f"to {actual}, off by {delta}", col_ord))
                continue
        if _within_rounding(rounding, col_ord, delta):
            bound = rounding_bound(rounding[col_ord])
            findings.append(Finding(
                table, check, ROUNDED,
                f"{where} · {key}: assembled {actual} vs printed {printed_value} "
                f"(delta {delta}) — inside the {bound} the report's own per-row "
                f"rounding to the cent allows over {rounding[col_ord]} rounded "
                f"figures", col_ord))
            continue
        findings.append(Finding(table, check, FAIL,
                                f"{where} · {key}: assembled {actual} vs "
                                f"printed {printed_value} "
                                f"(delta {delta})", col_ord))
    unverified = sorted(c for c in sums if c not in checked)
    for col_ord in unverified:
        findings.append(Finding(table, check, SKIP,
                                f"{keys.get(col_ord) or f'column {col_ord}'}: "
                                f"the total prints no figure here, so there is "
                                f"no witness for this column "
                                f"(assembled {sums[col_ord]})", col_ord))
    return findings


def group_totals(rows):
    """`{group_ord: [(row_ord, {col_ord: value}), ...]}` -- in reading order.

    **A group may print several subtotals, and each certifies only the rows since
    the previous one.** Measured on A1 p306-309: `O.M.A. OFFICINE MECCANICHE
    AERONAUTICHE S.P.A.` opens a scope, prints a subtotal, then prints its name
    *again* on p307 and prints a second subtotal — two segments under one
    operator. The earlier code kept the **last** subtotal per column and compared
    the whole scope against it, so a four-page scope was measured against one
    segment's figure:

        group 9492 · EURO: assembled 2,281,080.80 vs printed 789,718.24

    `789.718,24` is the second of four printed figures; the first was
    `1.691.118,52`. The rows are right and the comparison was comparing one
    segment against all four.

    The list is in reading order and each entry keeps its own `row_ord`, which is
    what lets the caller cut the scope into segments. `group_ord is None` is
    excluded and handled by the file-level arm, or a grand total would be checked
    twice under two names.
    """
    out = {}
    for row_ord, col_ord, kind, group, _key, _raw, num in rows:
        if kind not in TOTAL_KINDS or group is None:
            continue
        buckets = out.setdefault(group, [])
        if not buckets or buckets[-1][0] != row_ord:
            buckets.append((row_ord, {}))
        if col_ord is not None and num is not None:
            buckets[-1][1][col_ord] = num
    return out


def group_start(rows, group_ord):
    """The first `row_ord` belonging to a group -- its header, or its first row."""
    start = None
    for row_ord, col_ord, kind, group, _key, _raw, _num in rows:
        if group == group_ord and (start is None or row_ord < start):
            start = row_ord
    return start


#: A1's money columns, measured from the page: `EURO` is the third column, and
#: the currency column that decides whether a row needs conversion is the first.
MIXED_CURRENCY_LABEL = ("the group's EUR total: a row whose figure is printed in "
                        "Val. Fini Dog. is counted there and excluded here; every "
                        "other row contributes its converted value, or its amount "
                        "when it needed no conversion")

#: Label fragments that identify A1's four money columns, in preference order.
#: Read from `column_def` rather than hardcoded as ordinals, because a table whose
#: currency column happens to be its third would otherwise be read with A1's
#: arithmetic applied to it -- the same mistake as keying a table by its bare
#: code. A table that does not carry all three labels gets `None` and is compared
#: column by column instead.
_CURRENCY_LABELS = ("valuta", "currency", "divisa", "moneta")
_CONVERTED_LABELS = ("euro",)
_VALFINI_LABELS = ("val. fini", "val fini", "fin dog", "valore finale",
                   "val. fin. dog")
_AMOUNT_LABELS = ("ammontare", "importo", "amount", "valore in", "importi")


def money_columns(keys):
    """`(currency, amount, converted, val_fini)` ordinals from the column labels.

    `None` unless all four are found, which is what keeps this off tables it does
    not describe: on 2025 only the family-2 MAE tables carry this shape, and the
    rest fall back to a plain per-column comparison.
    """
    found = {}
    for ordinal, label in sorted(keys.items()):
        low = geo.deaccent(str(label or "")).lower()
        if "currency" not in found and any(t in low for t in _CURRENCY_LABELS):
            found["currency"] = ordinal
        elif "converted" not in found and any(t in low for t in _CONVERTED_LABELS):
            found["converted"] = ordinal
        elif "val_fini" not in found and any(t in low for t in _VALFINI_LABELS):
            found["val_fini"] = ordinal
        elif "amount" not in found and any(t in low for t in _AMOUNT_LABELS):
            found["amount"] = ordinal
    if len(found) < 4:
        return None
    return (found["currency"], found["amount"], found["converted"],
            found["val_fini"])


def eur_total_parts(rows, cols, group_ord=None):
    """`(total, contributors)` -- the EUR total, and how many figures it adds up.

    The count is the **rounding exposure** of the answer: each figure it adds was
    printed rounded to the cent, so a sum of `n` of them can be out by as much as
    `rounding_bound(n)`. Carrying it out of the sum is what lets the caller admit a
    residual for a derived column without admitting one for a raw one.

    Returns `(None, 0)` when the table does not have the four columns, so the
    caller compares column by column, which is the right check everywhere else.

    **The rule, read off three groups' own numbers rather than inferred** --
`Val. Fini Dog.` decides, and it is checked *first*:

  * a row that prints a `Val. Fini Dog.` figure has **already been counted
    there**, so it contributes nothing to the EUR total;
  * every other row contributes its `EURO` value when it has one (a row
    converted from another currency), and its `Ammontare` when it does not
    (a row already in EUR).

`A.T.C. SRL`, printed `100.938,00`:

| row | Valuta | Ammontare | EURO | Val. Fini Dog. | counts |
|---|---|---|---|---|---|
| 1 | EUR | 85.748,00 | — | — | 85.748,00 |
| 2 | EUR | 34.299,20 | — | **34.299,20** | excluded |
| 3 | EUR | 15.190,00 | — | — | 15.190,00 |

`85.748,00 + 15.190,00 = 100.938,00`. Row 2 is excluded because its figure is
in the third column.

`BAYKAR PIAGGIO AEROSPACE SPA`, printed `687.940,05` -- the case that proved
the priority. Row 1 carries **both** `EURO 485.483,79` and
`Val. Fini Dog. 485.483,79`, the same figure in both columns:

| row | Ammontare | EURO | Val. Fini Dog. | counts |
|---|---|---|---|---|
| 1 | 402.000,00 | 485.483,79 | **485.483,79** | excluded |
| 2 | 34.380,00 | 34.380,00 | — | 34.380,00 |
| 3 | 65.000,00 | — | **65.000,00** | excluded |
| 4 | 130.000,00 | — | **130.000,00** | excluded |
| 5 | 653.560,05 | — | — | 653.560,05 |

`34.380,00 + 653.560,05 = 687.940,05`. An earlier version added `EURO` first
and excluded on `Val. Fini Dog.` afterwards, so row 1 counted **twice** and
this group came out as `assembled 519863.79 vs printed 687940.05`.

    """
    if not cols:
        return None, 0
    _currency, amount_ord, converted_ord, val_fini_ord = cols
    values = [r for r in rows
              if r[2] in DATA_KINDS
              and (group_ord is None or r[3] == group_ord)]
    by_row = _by_row(values)
    if not by_row:
        return None, 0
    total = Decimal(0)
    contributors = 0
    for cells in by_row.values():
        if val_fini_ord in cells:
            continue                      # already counted in Val. Fini Dog.
        value = cells.get(converted_ord)
        if value is None:
            value = cells.get(amount_ord)
        if value is not None:
            total += value
            contributors += 1
    # **Rows that all sit in `Val. Fini Dog.` give a total of zero, not no total.**
    # An earlier version returned `None` whenever nothing was added, which merged
    # two different situations: a group with no data (no witness) and a group whose
    # every row is already counted in the customs column (a witness that says
    # zero). A1 group 9735 is the second -- it prints `EURO 0` beside
    # `Val. Fini Dog. 16.281.472,47`, every one of its rows carrying the same
    # figure in both columns -- and it was reported as
    # `assembled 16281472.48 vs printed 0`.
    return total, contributors


def eur_total(rows, cols, group_ord=None):
    """The EUR total alone; see `eur_total_parts` for the count alongside it."""
    return eur_total_parts(rows, cols, group_ord)[0]


def rounding_exposure(rows, cols, extra=None):
    """`{col_ord: contributors}` for the **derived** columns only.

    `extra` carries counts for readings the caller built itself -- the mixed-currency
    total's count against its `converted` column, for instance. Raw columns are
    never entered, which is what keeps a cent in an `Ammontare` a FAIL.
    """
    if not cols:
        return dict(extra or {})
    _currency, _amount_ord, converted_ord, val_fini_ord = cols
    derived = rounding_columns(cols)
    by_row = _by_row([r for r in rows if r[2] in DATA_KINDS])
    out = dict(extra or {})
    for ordinal in derived:
        if ordinal in out:
            continue
        out[ordinal] = sum(1 for cells in by_row.values()
                           if cells.get(ordinal) is not None)
    # The EUR column's exposure under the mixed-currency rule is the number of
    # figures that rule actually adds, not the number sitting in the column: a row
    # already counted in `Val. Fini Dog.` never reaches the EUR total.
    return out
    return total


def _by_row(values):
    """`{row_ord: {col_ord: value}}` for a group's rows."""
    out = {}
    for row_ord, col_ord, _kind, _group, _key, _raw, num in values:
        if num is not None:
            out.setdefault(row_ord, {})[col_ord] = num
    return out


def check_group_totals(table, rows, keys, cols=None):
    """Check every printed subtotal against the rows it actually certifies.

    A group is cut into **segments** at its printed subtotals: the first subtotal
    certifies the rows from the group's start, the second the rows after the
    first, and so on. Rows after the last subtotal are reported as uncertified
    rather than being added to any figure, because nothing in the table vouches
    for them.

    A group with one subtotal behaves exactly as before; the segmentation only
    changes what happens when a group prints more than one, which A1 does for an
    operator whose name is repeated across a page boundary mid-contract.
    """
    findings = []
    totals = group_totals(rows)
    data = [r for r in rows if r[2] in DATA_KINDS]
    rows_per_group = Counter(r[3] for r in data)
    for group in sorted(totals):
        segments = totals[group]
        start = group_start(rows, group)
        previous = start
        if rows_per_group.get(group, 0) == 0:
            findings.append(Finding(table, "group.empty", WARN,
                                    f"group {group} carries 0 data rows but "
                                    f"prints {len(segments)} subtotal(s)"))
        for index, (total_ord, printed) in enumerate(segments, 1):
            segment_rows = [r for r in data
                            if r[3] == group and previous <= r[0] < total_ord]
            sums = sum_by_column(segment_rows, DATA_KINDS)
            mixed, mixed_n = eur_total_parts(segment_rows, cols)
            alt = {c: mixed for c in printed} if mixed is not None else None
            exposure = rounding_exposure(
                segment_rows, cols,
                extra=({cols[2]: mixed_n} if mixed is not None and cols else {}))
            label = (f"group {group}" if len(segments) == 1
                     else f"group {group} segment {index}/{len(segments)}")
            findings.extend(compare_totals(
                "group.subtotal", table, sums, printed, keys, group=label,
                alt_sums=alt, alt_label=MIXED_CURRENCY_LABEL,
                present=values_by_column(segment_rows, DATA_KINDS),
                rounding=exposure))
            previous = total_ord
        trailing = [r for r in data
                    if r[3] == group and r[0] > segments[-1][0]]
        if trailing:
            findings.append(Finding(
                table, "group.uncertified", WARN,
                f"group {group}: {len(trailing)} row(s) come after the last "
                f"printed subtotal and no figure vouches for them"))
    if not totals:
        findings.append(Finding(table, "group.subtotal", SKIP,
                                "this table prints no per-group subtotal"))
    return findings


#: A total that names its own scope in its label: `ALBANIA Totale`,
#: `A.C.S.A. STEEL FORGINGS S.P.A. Totale`.
SCOPED_TOTAL_RE = re.compile(r"^(?P<key>\S.*?)\s*[Tt]otale\.?\s*$")


def scoped_totals(rows):
    """`[(row_ord, scope name, {col_ord: value})]` for every self-naming total.

    **A total can name the scope it closes.** Measured on the MEF art. 27 tables,
    where the country is not printed on a band of its own but *inside the first
    row of its run* -- `CANADA  75107  LEONARDO S.p.A.  Costi di riparazione  € …`
    -- while the total is `ALBANIA Totale    € 46.691.064,55`. There is no group
    band to open a scope from, so the extractor records none; read as a
    file-level total, the last country's figure was compared with the whole
    table's sum, giving `Totale: assembled 6495665600.07 vs printed 6485953896.59`
    on a correct extraction.

    The key is already in every row, so the scope is recoverable from the data
    without retro-attributing anything. When no column carries the name, the
    total is reported as having no witness -- the honest answer when the label
    names a scope the extraction cannot locate.
    """
    labels = {}
    for row_ord, col_ord, kind, _group, _key, raw, _num in rows:
        if kind in TOTAL_KINDS and col_ord is None and raw:
            match = SCOPED_TOTAL_RE.match(gra.normalise_text(raw))
            if match:
                labels[row_ord] = gra.normalise_text(match.group("key"))
    out = []
    for row_ord, name in sorted(labels.items()):
        totals = {col_ord: num
                  for r_ord, col_ord, kind, group, _k, _raw, num in rows
                  if kind in TOTAL_KINDS and group is None
                  and col_ord is not None and num is not None
                  and r_ord == row_ord}
        out.append((row_ord, name, totals))
    return out


def rows_in_scope(rows, name, upto_row_ord):
    """The data rows of one `<name> Totale` scope, and the column naming it.

    The scope runs from **the row bearing the name to the row before the total**,
    not over the rows that bear the name. An earlier version matched only the
    rows carrying the name, so on MEF `GG` -- where a country is printed once, at
    the head of a run of a dozen rows -- it compared the printed total against a
    single row and every one of the 92 checks came back SKIP.

    `upto_row_ord` is the total's own `row_ord`, and `row_ord` is monotone over
    the table, so the extent is exact: the **last** occurrence of the name before
    the total opens the scope. Using the last rather than the first is what
    handles a country appearing twice -- once as a title and once at the head of
    its run.
    """
    col = None
    start = None
    for row_ord, col_ord, kind, _group, _key, raw, _num in rows:
        if kind not in DATA_KINDS or row_ord >= upto_row_ord:
            continue
        if gra.normalise_text(raw or "") != name:
            continue
        if col is None or row_ord > start:
            col = col_ord
            start = row_ord
    if col is None or start is None:
        return [], None
    subset = [r for r in rows
              if r[2] in DATA_KINDS and start <= r[0] < upto_row_ord]
    return subset, col


def check_scoped_totals(table, rows, keys, cols=None):
    """Check every `<scope> Totale` against the rows that scope contains."""
    findings = []
    for row_ord, name, totals in scoped_totals(rows):
        if not totals:
            findings.append(Finding(table, "group.subtotal", SKIP,
                                    f"{name}: the total prints no figure"))
            continue
        subset, col = rows_in_scope(rows, name, row_ord)
        if not subset:
            findings.append(Finding(table, "group.subtotal", SKIP,
                                    f"{name} Totale: no row carries that name "
                                    f"before it, so the rows it closes cannot "
                                    f"be identified"))
            continue
        sums = sum_by_column(subset, DATA_KINDS)
        mixed, mixed_n = eur_total_parts(subset, cols)
        findings.extend(compare_totals(
            "group.subtotal", table, sums, totals, keys, group=name,
            alt_sums=({c: mixed for c in totals} if mixed is not None else None),
            alt_label=MIXED_CURRENCY_LABEL,
            present=values_by_column(subset, DATA_KINDS),
            rounding=rounding_exposure(
                subset, cols,
                extra=({cols[2]: mixed_n} if mixed is not None and cols else {}))))
    return findings


def check_grand_total(table, rows, keys, cols=None):
    """A total printed for the whole file, as opposed to one that names a scope.

    A `<scope> Totale` is **excluded** here and handled by
    `check_scoped_totals()`: comparing the whole table against one country's
    figure is the defect this split removes.
    """
    scoped = {row_ord for row_ord, _n, _t in scoped_totals(rows)}
    printed = {}
    for row_ord, col_ord, kind, group, _key, _raw, num in rows:
        if kind in TOTAL_KINDS and group is None and col_ord is not None \
                and num is not None and row_ord not in scoped:
            printed[col_ord] = num
    if not printed:
        return [Finding(table, "file.grand_total", SKIP,
                        "no file-level total printed"
                        + (" (every total names its own scope)"
                           if scoped else ""))]
    sums = sum_by_column(rows, DATA_KINDS)
    mixed, mixed_n = eur_total_parts(rows, cols)
    return compare_totals("file.grand_total", table, sums, printed, keys,
                          alt_sums=({c: mixed for c in printed}
                                    if mixed is not None else None),
                          alt_label=MIXED_CURRENCY_LABEL,
                          present=values_by_column(rows, DATA_KINDS),
                          rounding=rounding_exposure(
                              rows, cols,
                              extra=({cols[2]: mixed_n}
                                     if mixed is not None and cols else {})))


# --------------------------------------------------------------------------
# check 3: did every band become a record
# --------------------------------------------------------------------------

def check_reconciliation(table, detail, rows):
    """Every classified band became the row its kind promises.

    This is not a re-run of the band classification -- that would duplicate the
    extractor rather than check it. It is the narrower and more useful question:
    having called a band `data`, did a record exist for it, and having called a
    band `subtotal`, did it become a *total* rather than a data record.
    """
    findings = []
    if not detail or detail.get("status") != "extracted":
        return findings
    emitted = defaultdict(set)
    for row_ord, _col, kind, _group, _key, _raw, _num in rows:
        emitted[kind].add(row_ord)
    pairs = [("data", "data"), ("group", "group_header"),
             ("subtotal", "subtotal"), ("total", "total")]
    
    for band_kind, row_kind in pairs:
        classified = detail.get("band:" + band_kind)
        if classified is None:
            continue
        # Furniture is subtracted from the *record* kinds it was stealing from.
        # It cannot be attributed to one of them in advance -- on MG10 the stamp
        # was classified `group`, on A1 the furniture line is one `group` band
        # per page, and which kind it takes depends on the page's geometry. So
        # the subtraction is a comparison, not a mapping: whichever kind emitted
        # `classified` records when `classified - dropped` were expected is the
        # one it came out of. A total whose kind has no records at all is not
        # excused -- furniture cannot create a total.
        dropped = detail.get("furniture", 0)
        got = len(emitted.get(row_kind, ()))
        want = classified
        if dropped and classified > got:
            want = classified - dropped
        # Furniture is dropped *before* classification is counted, so when the
        # furniture band was one this kind would otherwise have claimed, the
        # expectation is `classified - furniture` -- which can be zero or even
        # negative if the count and the drops came from different windows. A
        # negative expectation compared against a real count reports "−13 bands
        # but 15 records" on a correct run, so the subtraction is floored and the
        # finding states what it compared against.
        if want < 0:
            want = 0
        if got == want:
            note = "" if want == classified else \
                f" ({classified} classified, {want} after dropping furniture)"
            findings.append(Finding(table, "row.reconciliation", PASS,
                                    f"{band_kind}: {got} bands -> {got} records"
                                    f"{note}"))
        else:
            findings.append(Finding(table, "row.reconciliation", FAIL,
                                    f"{band_kind}: {want} bands but {got} records "
                                    f"emitted (of {classified} classified)"))
    merged = detail.get("merged", 0)
    cont = (detail.get("band:continuation", 0)
            + detail.get("band:wrapped", 0))
    if cont:
        verdict = PASS if merged == cont else FAIL
        findings.append(Finding(table, "row.reconciliation", verdict,
                                f"continuation: {cont} bands, {merged} merged "
                                f"into the record above"))
    # `other` is a band with filled cells the classifier could not name. It is
    # assembled as a data record, because dropping it would lose a printed cell,
    # but it is reported because "the classifier was unsure" and "this is a
    # record" are different facts and only one of them is a defect.
    other = detail.get("band:other", 0)
    if other:
        findings.append(Finding(table, "band.unclassified", WARN,
                                f"{other} band(s) classified 'other' and "
                                f"assembled as data records"))
    return findings


# --------------------------------------------------------------------------
# check 4 + 5: text coverage and decimal integrity
# --------------------------------------------------------------------------

def check_text_coverage(table, pdf_path, conn, authority, article, code, year,
                        pages_to_check):
    """Are the extracted characters actually printed on the page?

    A column realigned onto the wrong label still yields real characters, so a
    coverage check cannot detect it -- but it *does* detect an extraction that
    invented text or merged two records' worth of words into one, and it is the
    cheapest independent read of the result available without re-running the
    extractor.
    """
    if not pdf_path or not Path(pdf_path).exists() or not pages_to_check:
        return [Finding(table, "text.coverage", SKIP, "no page to read")]
    doc = pymupdf.open(pdf_path)
    matched = total = 0
    try:
        n = len(doc)
        wanted = {min(n, max(1, int(round(p * (n - 1) + 1))))
                  for p in pages_to_check}
        for page_no in sorted(wanted):
            text = gra.normalise_text(doc[page_no - 1].get_text("text") or "")
            if not text:
                continue
            for _row_ord, _col, _kind, _group, _key, raw, _num in conn.execute(
                    "SELECT row_ord, col_ord, row_kind, group_ord, col_key,"
                    " raw_text, CAST(num_value AS TEXT) FROM stg_cell WHERE"
                    " authority=? AND article IS ? AND code=? AND year=? AND"
                    " page=?", (authority, article, code, year, page_no)):
                for word in str(raw or "").split():
                    total += len(word)
                    if word in text:
                        matched += len(word)
    finally:
        doc.close()
    if total == 0:
        return [Finding(table, "text.coverage", SKIP,
                        "no extracted text on the sampled pages")]
    share = matched / total
    if share >= 0.98:
        return [Finding(table, "text.coverage", PASS,
                        f"{share:.3f} of extracted characters found on the page")]
    return [Finding(table, "text.coverage", WARN,
                    f"only {share:.3f} of extracted characters found on the "
                    f"page -- a column may be realigned")]


def check_decimals(table, rows):
    """The 15-significant-digit ceiling, recorded where it is reached."""
    for row_ord, col_ord, _kind, _group, _key, raw, num in rows:
        if num is None:
            continue
        digits = len(num.as_tuple().digits)
        if digits > SIGNIFICANT_DIGITS:
            return [Finding(table, "decimal.exact", WARN,
                            f"row {row_ord} column {col_ord}: {digits} significant "
                            f"digits, past sqlite's {SIGNIFICANT_DIGITS}")]
    return []


# --------------------------------------------------------------------------

def audit_table(conn, base, pdf_path, coverage_pages, key_labels):
    authority, article, code, year = base
    table = label_of(authority, article, code)
    row = conn.execute(
        "SELECT extractable, extract_reason, grammar, n_columns, pages,"
        "       legible_pages, extract_detail FROM table_year WHERE authority=?"
        " AND article IS ? AND code=? AND year=?",
        (authority, article, code, year)).fetchone()
    if row is None:
        return [Finding(table, "catalog", SKIP, "not in the catalog")], {}
    extractable, reason, grammar, n_columns, pages, legible, detail_json = row
    detail = json.loads(detail_json) if detail_json else {}
    if not extractable:
        return [Finding(table, "extract", SKIP,
                        f"not extractable: {reason}")], detail
    if not detail:
        return [Finding(table, "extract", SKIP,
                        "not extracted yet (run ExtractTables.py)")], detail
    if detail.get("status") == "skip":
        return [Finding(table, "extract", SKIP,
                        f"{detail.get('reason')} (grammar '{grammar}')")], detail
    if detail.get("status") == "error":
        return [Finding(table, "extract", FAIL,
                        detail.get("reason", "extraction failed"))], detail

    findings = [Finding(table, "extract", PASS,
                        f"grammar '{grammar}', {detail.get('rows', 0):,} cells over "
                        f"{detail.get('legible_pages', 0)}/{detail.get('pages', 0)} "
                        f"readable pages")]
    rows = read_cells(conn, authority, article, code, year)
    cols = money_columns(key_labels)
    findings += check_scoped_totals(table, rows, key_labels, cols)
    findings += check_group_totals(table, rows, key_labels, cols)
    findings += check_grand_total(table, rows, key_labels, cols)
    findings += check_reconciliation(table, detail, rows)
    findings += check_decimals(table, rows)
    findings += check_text_coverage(table, pdf_path, conn, authority, article,
                                    code, year, coverage_pages)
    if detail.get("drift_page_count"):
        findings.append(Finding(table, "column.drift", WARN,
                                f"{detail['drift_page_count']} page(s) resolved a "
                                f"different column count than the catalog's "
                                f"{n_columns}"))
    if detail.get("illegible_page_count"):
        findings.append(Finding(table, "pages.illegible", WARN,
                                f"{detail['illegible_page_count']} of {pages} "
                                f"page(s) have no usable text (garbled or empty)"))
    if detail.get("no_frame_page_count"):
        findings.append(Finding(table, "pages.no_frame", WARN,
                                f"{detail['no_frame_page_count']} page(s) carried "
                                f"no column frame -- covers, and pages before the "
                                f"first header"))
    if detail.get("furniture"):
        findings.append(Finding(table, "furniture.dropped", PASS,
                                f"{detail['furniture']} furniture band(s) "
                                f"(margin stamps, running headers) dropped before "
                                f"classification"))
    return findings, detail


def key_labels_for(conn, base):
    """`{ordinal: header_path}` for one table-year, so a finding names a column.

    Taken from `column_def` rather than from the extracted cells, so a check
    reports `Importi Segnalati` rather than `column 4`.
    """
    return {
        ordinal: (path or label or "")
        for ordinal, label, path in conn.execute(
            "SELECT ordinal, label, header_path FROM column_def WHERE"
            " authority=? AND article IS ? AND code=? AND year=?", base)
    }


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def write_log(path, findings, counts, year, elapsed):
    """The log is written **before** the JSON, and rewritten at the end.

    The log is the artefact a person actually reads, so it must not be the
    casualty of a run that dies between the two -- which is what happened when the
    JSON was written first and this verifier was piped into `head`: the pipe
    closed and the next `print` raised BrokenPipeError, leaving a JSON with no log
    beside it.
    """
    lines = [f"verify-extraction {year}: {counts['PASS']} PASS, {counts[ROUNDED]} "
             f"ROUNDED, {counts['FAIL']} "
             f"FAIL, {counts['WARN']} WARN, {counts['SKIP']} SKIP "
             f"in {elapsed:.1f}s", ""]
    _write_log(path, lines + _body(findings))
    return lines


def _write_log(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def _body(findings):
    by_table = defaultdict(list)
    for f in findings:
        by_table[f.table].append(f)
    lines = []
    for table in sorted(by_table):
        fs = by_table[table]
        worst = _roll_up(fs)
        lines.append(f"[{worst}] {table}")
        for f in sorted(fs, key=lambda x: (x.check, x.page or 0)):
            where = f" p{f.page}" if f.page else ""
            lines.append(f"    {f.verdict:<7} {f.check}{where}: {f.detail}")
        lines.append("")
    return lines


HTML_CSS = """
body{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#111}
h1{font-size:1.3rem} table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{border:1px solid #ccc;padding:.3rem .5rem;text-align:left;vertical-align:top}
th{background:#f2f2f2;position:sticky;top:0}
.PASS{color:#0a6}.FAIL{color:#b00;font-weight:700}.WARN{color:#a60}.SKIP{color:#666}
.ROUNDED{color:#06c;font-style:italic}
details{margin:.2rem 0} summary{cursor:pointer}
code{background:#f6f6f6;padding:0 .2rem}
"""


def write_html(path, findings, counts, year):
    by_table = defaultdict(list)
    for f in findings:
        by_table[f.table].append(f)
    out = ["<!doctype html><html><head><meta charset='utf-8'>",
           f"<title>verify-extraction {year}</title><style>{HTML_CSS}</style>",
           "</head><body>",
           f"<h1>verify-extraction {year}</h1><p>{counts['PASS']} PASS, "
           f"<span class='FAIL'>{counts['FAIL']} FAIL</span>, "
           f"<span class='WARN'>{counts['WARN']} WARN</span>, "
           f"<span class='ROUNDED'>{counts[ROUNDED]} ROUNDED</span>, "
           f"{counts['SKIP']} SKIP</p>",
           "<table><tr><th>table</th><th>verdict</th><th>checks</th></tr>"]
    for table in sorted(by_table):
        fs = by_table[table]
        worst = _roll_up(fs)
        out.append(f"<tr><td><code>{table}</code></td>"
                   f"<td class='{worst}'>{worst}</td><td>")
        for f in sorted(fs, key=lambda x: (x.check, x.page or 0)):
            where = f" p{f.page}" if f.page else ""
            out.append(f"<details><summary class='{f.verdict}'>{f.verdict} "
                       f"{f.check}{where}</summary><div>{f.detail}</div>"
                       f"</details>")
        out.append("</td></tr>")
    out.append("</table></body></html>")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))


def write_md(path, findings, counts, year, elapsed):
    lines = [f"# Extraction census {year}", "",
             f"{counts['PASS']} PASS · **{counts['FAIL']} FAIL** · "
             f"{counts['WARN']} WARN · {counts['SKIP']} SKIP · "
             f"{counts[ROUNDED]} ROUNDED "
             f"({elapsed:.1f}s)", "",
             "| table | verdict | the finding |", "|---|---|---|"]
    for f in findings:
        if f.verdict in (FAIL, WARN, ROUNDED):
            lines.append(f"| `{f.table}` | **{f.verdict}** | {f.check}: "
                         f"{f.detail} |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit the cells ExtractTables.py wrote, against the totals "
                    "the tables themselves print.")
    parser.add_argument("--year", required=True)
    parser.add_argument("--out-root", default="Out")
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--coverage-pages", type=float, nargs="*",
                        default=[0.15, 0.5, 0.85],
                        help="fractions of each table to read for the text "
                             "coverage check; empty to skip it")
    args = parser.parse_args(argv)

    year = int(args.year)
    out_root = Path(args.out_root)
    catalog = Path(args.catalog) if args.catalog else out_root / "CATALOG" / "catalog.sqlite"
    verify_dir = out_root / "VERIFY"
    verify_dir.mkdir(parents=True, exist_ok=True)
    log_path = verify_dir / f"exverify-{year}.log"
    json_path = verify_dir / f"exverify-{year}.json"
    html_path = verify_dir / f"exverify-{year}.html"
    md_path = verify_dir / f"exverify-{year}.md"

    if not catalog.exists():
        print(f"catalog not found: {catalog}", file=sys.stderr)
        return 2

    started = time.time()
    conn = open_catalog(str(catalog))
    findings = []
    try:
        bases = [(a, r, c, year) for a, r, c in conn.execute(
            "SELECT authority, article, code FROM table_year WHERE year=?"
            " ORDER BY authority, article, code", (year,))]
        for base in bases:
            path = conn.execute(
                "SELECT path FROM table_year WHERE authority=? AND article IS ?"
                " AND code=? AND year=?", base).fetchone()
            import ExtractTables as ext
            found = ext.locate(out_root, path[0] if path else None)
            pdf = str(found) if found else None
            fs, _detail = audit_table(conn, base, pdf, args.coverage_pages,
                                      key_labels_for(conn, base))
            findings.extend(fs)
    finally:
        conn.close()

    counts = {v: sum(1 for f in findings if f.verdict == v)
              for v in (PASS, FAIL, WARN, SKIP, ROUNDED)}
    elapsed = time.time() - started

    write_log(log_path, findings, counts, year, elapsed)
    payload = {"year": year, "counts": counts, "elapsed": round(elapsed, 2),
               "findings": [f.as_dict() for f in findings]}
    json_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                         encoding="utf-8")
    write_html(html_path, findings, counts, year)
    write_md(md_path, findings, counts, year, elapsed)

    print(f"\nverify-extraction {year}: {counts['PASS']} PASS, "
          f"{counts[ROUNDED]} ROUNDED, {counts['FAIL']} FAIL, "
          f"{counts['WARN']} WARN, {counts['SKIP']} SKIP in {elapsed:.1f}s")
    print(f"  {log_path}\n  {json_path}\n  {html_path}\n  {md_path}")
    for f in findings:
        if f.verdict == FAIL:
            print(f"  FAIL {f.table}: {f.check}: {f.detail}")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())