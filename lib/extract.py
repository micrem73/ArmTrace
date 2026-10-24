"""One table's rows, read in the source volumes, as printed.

The SQL side of the pipeline (DECISIONS.md 2026-10-19): a table is read page
by page through `lib/source` (clipped, glyphs decoded), its columns are the
ones agreed in `lib/data/columns.tsv`, and every row keeps the printed text
beside the parsed value. Nothing is filled in, corrected or guessed: a cell
the archive leaves blank stays blank, a value that does not parse is NULL with
its printed text kept and an issue recorded.

Each page is one of four kinds:

  header        it prints the column header in a grid (`geometry.grid_header`);
                the header's labels must match the agreed ones, or the table
                is refused -- a renamed column is a decision, not a guess
  continuation  no header, but its column walls stand where the header's do
                (2025 EE p2, Difesa 4 p2): the columns carry over
  summary       neither: the table's own printed summary (MAE A1's last page,
                `Totale Valore in EURO : 7.721.114.868,99`), kept as label and
                value pairs in `totals`, the extraction's oracle
  illegible     no text to read

The body of a page is what lies inside the table's grid; it is cut into
records by one rule, measured on the five pilot tables:

  * within a box between two horizontal rules, if at most one printed line
    carries a value, the box is one record and its lines are joined: a cell
    that wraps (T2 `Valore autorizzazioni / individuali esportazione (Mrd €)`,
    Difesa 4's company lists, 2024 EE's `Causale`) is one cell;
  * otherwise each printed line is a record, and a line that carries no value
    either heads a group (MAE A1's operator, one run of text across the row)
    or continues the record above it (A1's wrapped `Materiale` description).
    A page that opens with such a line continues the previous page's record.

Row kinds: `total` (a `Totale` label), `subtotal` (amounts only, in a table
with groups), `group`, `data`.
"""
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from lib import geometry as geo
from lib import grammar as gra
from lib import glyphs, source

DATA = Path(__file__).parent / "data"
TOL = geo.RULE_TOL

# A value-shaped cell: what tells a record's line from a wrapped text line.
VALUE_SHAPES = {gra.SHAPE_NUMBER, gra.SHAPE_CURRENCY, gra.SHAPE_DATE,
                gra.SHAPE_PERCENT, gra.SHAPE_CODE, gra.SHAPE_YEAR}
# Columns of printed words: a value's shape there is text (`CAL. 5,56`).
TEXT_TYPES = {"text", "group", "carry", "block"}
# A printed amount: what must never be lost outside a table's body.
AMOUNT_WORD = re.compile(r"\d,\d{2}%?$")
# A note printed under the table belongs to it (DECISIONS.md 2026-10-17).
# Difesa 4 p256 marks its footnote with a bare superscript, `1   Leonardo SpA ...`.
# MAE A2 prints the cooperation share under its totals, `(pari a 3,43 %)`.
NOTE_START = re.compile(r"^\s*(?:\(\s*pari\s+a\b|\(\s*\*+\s*\)|\*+|\(\d+\)|\d{1,2}\s{2,}\S|Fonte\b|Note?\b|N\.\s*B\.)",
                        re.IGNORECASE)
# The page stamp a table prints in its own direction, outside its grid: MAE
# `MAECI - UAMA - CENTRO INFORMATICO  Pagina 80 di 537  TAB A1`, Dogane
# `Pag. 9 di 9`. Not table content, never lost text.
STAMP = re.compile(r"Pag(?:ina|\.)?\s*\d+\s*di\s*\d+|CENTRO\s+INFORMATICO|^TAB\.?\s*\S+$",
                   re.IGNORECASE)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
TOTAL_LEAD = re.compile(r"^total[ei]\b", re.IGNORECASE)
# A subtotal named after its group: MEF II `A.L.A.  S.p.A. Totale  € 1.000.722,54`.
SUBTOTAL_TAIL = re.compile(r"^(\S.*?)\s+totale$", re.IGNORECASE)


# --------------------------------------------------------------------------
# the agreed columns
# --------------------------------------------------------------------------


@dataclass
class Column:
    key: str
    type: str
    labels: list
    sum: bool = False
    else_col: str = None        # `sum else <col>@<col2>=<value>`
    else_when: tuple = None
    unless: str = None          # `unless <col>`: a row with <col> printed adds nothing
    link: tuple = None          # `link`: (amount col, item col, opener col)
    of: str = None              # `company`: the column printing the name
    event: str = None           # `event <WORD>`: filled on the list headed <WORD>
    against: dict = None        # `block`: {block: the row it adds up to, not the total}
    once: bool = False          # `once`: stored on the edition year's row only (3B)

    def sum_sql(self):
        """The SQL a data row adds to this column's printed totals, or None.

        Stored in `column_def.sum_sql`, so a query adds a column up the way
        the table does: `SELECT total(<sum_sql>) FROM <table>` (facts only:
        no subtotal or total row to leave out). The same rule as `_effective`,
        which the checks use; a test holds the two together. `unless` names a valued column, one
        with its printed text in `<col>_raw`."""
        if not self.sum:
            return None
        expr = self.key
        if self.else_col:
            when_col, when_value = self.else_when
            expr = (f"COALESCE({self.key}, CASE WHEN {when_col} = '{when_value}' "
                    f"THEN {self.else_col} END, 0)")
        if self.unless:
            expr = f"CASE WHEN {self.unless}_raw IS NOT NULL THEN 0 ELSE {expr} END"
        return expr


@dataclass
class Spec:
    table: str
    columns: list
    totals: list = field(default_factory=list)   # (regex, expr)

    @property
    def printed(self):
        """The columns the header prints, in order (not the group heading)."""
        # A group heading in a column the header names (MAE B4's `Area Geografica`)
        # is printed; one over the rows with no column (A1's operator) is not.
        return [c for c in self.columns if c.type not in ("link", "unit", "company", "companies")
                and not (c.type == "group" and not any(c.labels))]

    @property
    def group(self):
        return next((c for c in self.columns if c.type == "group"), None)

    @property
    def link(self):
        return next((c for c in self.columns if c.type == "link"), None)

    @property
    def unit(self):
        return next((c for c in self.columns if c.type == "unit"), None)

    @property
    def company(self):
        return next((c for c in self.columns if c.type == "company"), None)

    @property
    def company_lists(self):
        return [c for c in self.columns if c.type == "companies"]

    @property
    def years(self):
        return next((c for c in self.columns if c.type == "years"), None)


def _rows(name):
    with open(DATA / name, encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def specs():
    out = {}
    for row in _rows("columns.tsv"):
        col = Column(row["col"], row["type"],
                     [x for x in (row["labels"] or "").split("|")])
        rule = (row["sum"] or "").split()
        col.once = "once" in rule
        rule = [w for w in rule if w != "once"]
        if col.type == "link":
            col.link = tuple(rule)
        elif col.type in ("company", "companies", "rowsum"):
            col.of = rule[0]
        elif rule[:1] == ["event"]:
            col.event = rule[1]
        elif col.type == "block" and row["sum"]:
            col.against = dict(x.strip().split("=") for x in row["sum"].split(";"))
        elif rule[:1] == ["sum"]:
            col.sum = True
            if rule[1:2] == ["else"]:
                target, cond = rule[2].split("@")
                col.else_col = target
                col.else_when = tuple(cond.split("="))
            if "unless" in rule:
                col.unless = rule[rule.index("unless") + 1]
        out.setdefault(row["table"], Spec(row["table"], [])).columns.append(col)
    for spec in out.values():
        valued = {c.key for c in spec.columns if c.type in ("amount", "number", "date")}
        for col in spec.columns:
            if col.unless and col.unless not in valued:
                raise ValueError(f"{spec.table}.{col.key}: `unless {col.unless}` "
                                 "must name an amount, number or date column")
    for row in _rows("totals.tsv"):
        out[row["table"]].totals.append((re.compile(row["label"]), row["expr"]))
    return out


def sql_name(key):
    """`DOG/A1C2/M1` -> `dog_a1c2_m1`."""
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def fold(label, year):
    """A printed label in comparable form, years relative to the edition."""
    def rel(m):
        k = int(year) - int(m.group())
        return "y" if k == 0 else f"y{-k:+d}"
    text = gra.normalise_text(label).replace("’", "'").lower()
    return YEAR.sub(rel, text)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


@dataclass
class Result:
    key: str
    rows: list = field(default_factory=list)      # dicts, see Reader.emit
    totals: list = field(default_factory=list)    # (pdf_page, printed_page, label, raw)
    notes: list = field(default_factory=list)     # (pdf_page, printed_page, text)
    issues: list = field(default_factory=list)    # (pdf_page, kind, detail)
    labels: list = field(default_factory=list)    # printed header, first page
    columns: list = field(default_factory=list)   # the spec column under each label
    pages: dict = field(default_factory=dict)     # kind -> count


def page_words(page, frame=None):
    """Decoded words in reading space, the furniture's direction left out.

    `frame` is the table's, once known: a page that holds only a header and
    one operator name (MAE A1 p147) has fewer rotated characters than its
    upright running head, and read in the page's own dominant direction the
    table comes out letter by letter."""
    frame = frame or geo.Frame(geo.dominant_direction(page)["right"])
    foreign = [d for d in ((1, 0), (0, 1), (-1, 0), (0, -1))
               if d != (round(frame.right[0]), round(frame.right[1]))]
    words = [w for w in geo.lines_of(page, frame, foreign_dirs=foreign) if not w.foreign]
    return frame, words


def is_note(text):
    """A footnote: one opening as notes do (`(*)`, `Fonte`, ...), or a sentence
    of its own, eight words or more ending a sentence (MAE D, G1, G2, I:
    `LE AUTORIZZAZIONI CON VALORE NULLO SONO CONTABILIZZATE AI SOLI FINI
    DOGANALI (SENZA REGOLAMENTO FINANZIARIO).`)."""
    text = text.strip()
    last = text.split()[-1] if text else ""
    # A sentence's end, not an abbreviation's: A1's operator `... AMEDEO LIA S.P.A.`.
    sentence = text.endswith(".") and "." not in last[:-1]
    return bool(NOTE_START.match(text)) or (len(text.split()) >= 8 and sentence)


def _rotated_cells(page, frame, levels):
    """[(u, v_top, v_bottom, text)]: text printed across the table's lines
    (T13's `Categoria (Military List)`, rotated in its stub), with the floors
    above and below it that cross its column: the cell it labels."""
    out = []
    for span in page.get_texttrace(across=True):
        text = " ".join("".join(chr(c[0]) for c in span["chars"]).split())
        x0, y0, x1, y1 = span["bbox"]
        us, vs = zip(*(frame.point(x, y) for x, y in ((x0, y0), (x1, y1))))
        u = sum(us) / 2
        crossing = [lv[0] for lv in levels if lv[1] - TOL <= u <= lv[2] + TOL]
        above = [v for v in crossing if v <= min(vs)]
        below = [v for v in crossing if v >= max(vs)]
        if text and above and below:
            out.append((u, max(above), min(below), text))
    return out


def _rules(page, frame):
    segs = geo.rule_segments(page, frame, min_len=geo.WALL_MIN_LEN)
    return geo._join_walls(segs["walls"], TOL), geo.join_floors(segs["floors"], TOL)


def _page_columns(cells, walls):
    """The header's columns as this page draws them, or None.

    A continuation page prints the same grid as the header page, but not in
    the same place: 2025 EE p2 stands 10.9 pt right of p1 (odd and even pages
    have different margins), so first every column edge must find a wall at
    one common offset. Failing that, the page may redraw the grid with other
    widths (Difesa 4 p255, EE's last page): the columns are then its own long
    walls, taken in order, when there are exactly as many as the header has
    edges; or all its walls, when merged cells break the inner ones into short
    pieces (MEF II's last page, p427). Anything else is not this table's grid.
    """
    edges = [c["u0"] for c in cells] + [cells[-1]["u1"]]
    positions = sorted({round(u, 1) for u, _a, _b in walls})
    for u in positions:
        d = u - edges[0]
        if all(any(abs(e + d - x) <= TOL for x in positions) for e in edges):
            return [{**c, "u0": c["u0"] + d, "u1": c["u1"] + d} for c in cells]
    longest = max((b - a for _u, a, b in walls), default=0)
    long_walls = geo._merge_positions(
        [u for u, a, b in walls if b - a >= 0.5 * longest], TOL)
    for found in (long_walls, geo._merge_positions([u for u, _a, _b in walls], TOL)):
        if len(found) == len(edges):
            return [{**c, "u0": a, "u1": b}
                    for c, a, b in zip(cells, found, found[1:])]
    return None


# The unprinted space between two blocks of year columns (T20's 2025 and 2024):
# a column of the page, not of the table; a value printed in it is an issue.
GAP = Column("", "gap", [""])


class Reader:
    def __init__(self, year, key, spec):
        self.year, self.key, self.spec = str(year), key, spec
        self.result = Result(key)
        self.cells = None           # the header's columns, reading space
        self.order = None           # header column index -> spec column
        self.year_of = {}           # header column index -> printed year (T2)
        self.group = None           # the current group heading
        self.carried = {}           # column index -> text carried down (`carry`)
        self.frame = None           # the table's reading frame, from its header
        self.row_ord = 0
        self.blocks = []            # labels of cells spanning rows (`block`)

    # -- header ---------------------------------------------------------------

    def accept_header(self, cells, pdf_page):
        texts = [gra.normalise_text(c["text"]) for c in cells]
        overs = [gra.normalise_text(c.get("over", "")) for c in cells]
        # A two-level label is stored as printed, top first: `Anno 2022 / N.`.
        labels = [" / ".join(x for x in (o, t) if x) for o, t in zip(overs, texts)]
        printed = self.spec.printed
        measures = [c for c in printed if c.type == "years"]
        order, year_of = [], {}
        if measures:
            # Fixed columns, in their order, and year columns: a year printed
            # alone (T2: the label is the year, one measure), or over a measure
            # (T10: `Anno 2022` over `N.`), or under one (3B: `INCASSO` over `2022`).
            fixed = [c for c in printed if c.type != "years"]
            for i, (over, text) in enumerate(zip(overs, texts)):
                if fixed and fold(text, self.year) in [
                        fold(x, self.year) for x in fixed[0].labels]:
                    order.append(fixed.pop(0))
                    continue
                if not over and not text and year_of:
                    order.append(GAP)       # between two year blocks (T20): empty
                    continue
                year, measure = None, None
                if over and YEAR.search(over):
                    year, label = YEAR.search(over).group(), text
                elif over and YEAR.fullmatch(text):
                    year, label = text, over
                elif not over and YEAR.fullmatch(text) and len(measures) == 1 \
                        and fold(text, self.year) in [x.lower() for x in measures[0].labels]:
                    year, measure = text, measures[0]
                if year and measure is None:
                    measure = next((m for m in measures if fold(label, self.year) in
                                    [fold(x, self.year) for x in m.labels]), None)
                if measure is None:
                    return self._refuse(pdf_page, labels)
                order.append(measure)
                year_of[i] = int(year)
            if fixed or not year_of:
                return self._refuse(pdf_page, labels)
        else:
            if len(labels) != len(printed):
                return self._refuse(pdf_page, labels)
            for label, col in zip(labels, printed):
                if fold(label, self.year) not in [fold(x, self.year) for x in col.labels]:
                    return self._refuse(pdf_page, labels)
                order.append(col)
        if self.cells is None:
            self.result.labels, self.result.columns = labels, order
        self.cells, self.order, self.year_of = cells, order, year_of
        return True

    def summary_page(self, words):
        """A page with no header whose first line printing an amount is one of
        the table's agreed summary lines (`totals.tsv`): its summary (MAE F1's
        last page, boxed: its walls would pass for the grid's). Rows before it
        make it a page of the grid (MAE D's last page: OCEANIA, then the box)."""
        for band in geo.row_bands(words):
            text = gra.normalise_text(" ".join(w.text for w in sorted(band, key=lambda w: w.u0)))
            if any(AMOUNT_WORD.search(w.text.strip()) for w in band):
                return any(rx.search(text) for rx, _expr in self.spec.totals)
        return False

    def split_merged(self, header, words, walls):
        """A header cell holding two agreed labels in a row, with no wall drawn
        between them in the header (MAE F1: `Ammontare EURO`; the wall runs
        only round the body's amounts): two cells, divided where a wall stands
        in the gap between the two labels' words, else midway."""
        if self.spec.years:
            return header["cells"]
        labels = [[fold(x, self.year) for x in c.labels] for c in self.spec.printed]
        out = []
        for cell in header["cells"]:
            text = fold(gra.normalise_text(cell["text"]), self.year)
            mine = sorted((w for w in words if header["v0"] < w.vmid < header["v1"]
                           and cell["u0"] < w.umid < cell["u1"]), key=lambda w: w.u0)
            cut = None
            if not any(text in alt for alt in labels):
                for k in range(1, len(mine)):
                    a = fold(" ".join(w.text.strip() for w in mine[:k]), self.year)
                    b = fold(" ".join(w.text.strip() for w in mine[k:]), self.year)
                    if any(a in labels[j] and b in labels[j + 1] for j in range(len(labels) - 1)):
                        gap = (mine[k - 1].u1, mine[k].u0)
                        cut = next((u for u, _a, _b in walls if gap[0] < u < gap[1]),
                                   sum(gap) / 2)
                        break
            if cut is None:
                out.append(cell)
                continue
            out += [{**cell, "text": " ".join(w.text.strip() for w in mine if w.umid < cut),
                     "u1": cut},
                    {**cell, "text": " ".join(w.text.strip() for w in mine if w.umid > cut),
                     "u0": cut}]
        return out

    def _refuse(self, pdf_page, labels):
        expected = [c.labels[0] if c.labels else "" for c in self.spec.printed]
        self.result.issues.append(
            (pdf_page, "header", f"printed {labels}, agreed {expected}"))
        return False

    # -- pages ----------------------------------------------------------------

    def read_page(self, sp):
        frame, words = page_words(sp, self.frame)
        if not words:
            self.count("illegible")
            return
        walls, levels = _rules(sp, frame)
        self.blocks = _rotated_cells(sp, frame, levels)
        header = geo.grid_header(sp, words, frame, is_label=gra.is_label_row)
        if header:
            header["cells"] = self.split_merged(header, words, walls)
        if header and (self.cells is None or self.matches(header["cells"])):
            if not self.accept_header(header["cells"], sp.pdf_page):
                raise Refused(self.result.issues[-1][2])
            kind, cells, v_top = "header", header["cells"], header["v1"]
            self.frame = frame
        elif self.cells is not None and self.summary_page(words):
            return self.read_summary(sp, words)
        elif self.cells is None:
            # Before the first header: the table's own cover (DOG M1 p1,
            # `Esportazione Definitiva (EX) / Riepilogo per operatore / TAB. M1`).
            # It may name the table; it may not hold its figures.
            self.count("cover")
            self.check_outside(sp, words, [], float("inf"))
            return
        else:
            cells = _page_columns(self.cells, walls)
            if cells is None:
                return self.read_summary(sp, words)
            kind, v_top = "continuation", None
        self.count(kind)
        u_lo, u_hi = cells[0]["u0"] - TOL, cells[-1]["u1"] + TOL
        mine = [w for w in walls if u_lo <= w[0] <= u_hi]
        floors = [v for v, a, b in levels
                  if b - a >= 0.5 * (u_hi - u_lo) and a <= u_hi and b >= u_lo]
        # The grid's extent is where its walls stand. The floors only extend
        # it downwards (MAE A1 draws walls in its header row alone): above
        # the grid, a page-wide floor is the running head's underline, and
        # taking it as the table's top read `XIX LEGISLATURA` into Difesa 4.
        if v_top is None:
            v_top = min(a for _u, a, _b in mine) - TOL
        # Where the table ends. Its last printed line, page stamp aside,
        # bounds it from below; the grid bounds it more tightly when its walls
        # run through the body (T2: the footnote under the grid is a note, not
        # a row). MAE A1 draws walls in its header and around a few amount
        # boxes only, and rules mid-page around subtotals and groups: there the
        # last line is the bottom.
        lines = [band for band in geo.row_bands(
            [w for w in words if w.vmid > v_top and u_lo < w.umid < u_hi])
            if not STAMP.search(" ".join(w.text for w in band))]
        last_line = max((max(w.v1 for w in band) for band in lines), default=v_top)
        spanning = [b for _u, a, b in mine if b - max(a, v_top) >= 0.5 * (last_line - v_top)]
        total, boxed = None, []
        if len(spanning) >= 2:
            # Down to the last wall of the grid, however short: B4 p682 prints a
            # second area under the first, its total in a narrow box of its own.
            v_bottom = max(spanning + floors[-1:] + [b for _u, _a, b in mine]) + TOL
            # A total in a box of its own under the grid, with its own labels
            # (3B's `TOTALE INCASSO`): the grid ends where its walls do.
            walls_end = max(spanning) + TOL
            total, boxed = self.read_total_box([w for w in words if w.vmid > walls_end],
                                               sp, frame)
            if total:
                v_bottom = walls_end
        else:
            v_bottom = last_line + TOL
        body = [w for w in words
                if v_top < w.vmid < v_bottom and u_lo < w.umid < u_hi]
        self.check_outside(sp, words, body + boxed, v_bottom)
        self.page_cells = cells
        self.read_body(sp, body, [v_top] + [f for f in floors if v_top < f < v_bottom]
                       + [v_bottom], mine, first=(kind == "continuation"))
        if total:
            self.emit(sp, "total", total)

    def matches(self, cells):
        """A header candidate after the first header is one only when it
        prints the agreed labels; else it is a text-only data row (Difesa 4
        p2 opens on `GRECIA, SPAGNA, AUSTRIA.`)."""
        labels = [fold(" / ".join(x for x in (gra.normalise_text(c.get("over", "")),
                                               gra.normalise_text(c["text"])) if x), self.year)
                  for c in cells]
        return labels == [fold(x, self.year) for x in self.result.labels]

    def count(self, kind):
        self.result.pages[kind] = self.result.pages.get(kind, 0) + 1

    def read_total_box(self, below, sp, frame):
        """A total printed in a box of its own under the grid, with its own
        labels (3B: `TOTALE INCASSO` over `2022 ... 2025 | Nel quadriennio`):
        a `total` row, each value under the table column of the same label.
        (row, the box's words), or (None, []) when there is none or a label
        matches no column: its words stay outside the table, an issue."""
        if not below:
            return None, []
        box = geo.grid_header(sp, below, frame, is_label=gra.is_label_row)
        if not box:
            return None, []
        # The title is over the labels, or the line above them in the box's width.
        u0, u1 = box["cells"][0]["u0"], box["cells"][-1]["u1"]
        above = [w for w in below if w.vmid < box["v0"] and u0 < w.umid < u1]
        title = " ".join(dict.fromkeys(c.get("over", "") for c in box["cells"] if c.get("over"))) \
            or " ".join(w.text.strip() for w in sorted(above, key=lambda w: w.u0))
        if not TOTAL_LEAD.match(title):
            return None, []
        mine = [fold(c["text"], self.year) for c in self.cells]
        at = []
        for c in box["cells"]:
            label = fold(gra.normalise_text(c["text"]), self.year)
            if label not in mine:
                return None, []
            at.append(mine.index(label))
        values = [w for w in below if w.vmid > box["v1"]]
        row = [""] * len(self.order)
        row[0] = title
        used = above + [w for w in below if box["v0"] < w.vmid < box["v1"]]
        for c, i in zip(box["cells"], at):
            inside = [w for w in values if c["u0"] < w.umid < c["u1"]]
            row[i] = " ".join(w.text.strip() for w in inside)
            used += inside
        return row, used

    def check_outside(self, sp, words, body, v_bottom):
        """Words outside the body. A note under the table is kept, the page
        stamp is furniture; any other line under the table, and a printed
        amount anywhere outside it, is an issue: never silently dropped."""
        inside = set(map(id, body))
        rest = [w for w in words if id(w) not in inside]
        below = [b for b in geo.row_bands([w for w in rest if w.vmid > v_bottom])]
        noting = False
        for band in below:
            text = " ".join(w.text for w in band)
            if STAMP.search(text):
                pass                # the page stamp: furniture, after a note too
            elif is_note(text) or noting:
                noting = True
                self.result.notes.append((sp.pdf_page, sp.printed_page, text))
            else:
                self.result.issues.append(
                    (sp.pdf_page, "outside", f"line {text[:60]!r} under the table"))
            inside.update(map(id, band))
        for w in rest:
            if id(w) not in inside and AMOUNT_WORD.search(w.text):
                self.result.issues.append(
                    (sp.pdf_page, "outside", f"amount {w.text!r} outside the table"))

    def read_summary(self, sp, words):
        """The table's printed summary, as (label, value) pairs.

        MAE A1 p606: a label may run over several lines (`Di cui relativi a
        trasformazione ... temporanee` / `relative a Programmi
        Intergovernativi` / `:`), each line without a value continuing it until
        a `:` closes it; a line without a value after a closed label heads the
        lines under it (`Totali parziali per tipo valuta:` over `AED`, `BGN`,
        ...: `Totali parziali per tipo valuta: AED`; the line with no currency,
        `Totali parziali per tipo valuta`); a value followed by `%` is
        labelled `<label> (%)`."""
        self.count("summary")
        entries, section = [], ""          # [label, [(value, is_percent)]]
        for band in geo.row_bands(words):
            band = sorted(band, key=lambda w: w.u0)
            if is_note(" ".join(w.text.strip() for w in band)):
                self.result.notes.append((sp.pdf_page, sp.printed_page,
                                          gra.normalise_text(" ".join(w.text for w in band))))
                continue
            values, text = [], []
            # A percentage is `N %` (A1) or `(pari a N %)` (G2), after its value.
            band = [w for w in band if not re.fullmatch(r"\(\s*pari\s+a", w.text.strip())]
            for i, w in enumerate(band):
                shape = gra.value_shape(w.text)
                if shape in (gra.SHAPE_NUMBER, gra.SHAPE_CURRENCY) or (
                        shape == gra.SHAPE_CODE and values == [] and text):
                    pct = i + 1 < len(band) and band[i + 1].text.strip() in ("%", "%)")
                    values.append((w.text, pct))
                elif w.text.strip() not in ("%", "%)"):
                    text.append(w.text)
            text = gra.normalise_text(" ".join(text))
            if STAMP.search(text):
                continue
            if not values:
                if entries and not entries[-1][0].endswith(":"):
                    entries[-1][0] = f"{entries[-1][0]} {text}".strip()
                else:
                    section = text
                continue
            label = f"{section} {text}".strip() if section else text
            entries.append([label, values])
        for label, values in entries:
            label = gra.normalise_text(re.sub(r"\s*:+$", "", label))
            # A second value on the line is labelled `(2)` (G2: `Totale : 556
            # 1.977.363.805,02`), a percentage `(%)`.
            k = 0
            for raw, pct in values:
                k += not pct
                name = f"{label} (%)" if pct else (f"{label} ({k})" if k > 1 else label)
                self.result.totals.append((sp.pdf_page, sp.printed_page, name, raw))

    # -- records --------------------------------------------------------------

    def read_body(self, sp, body, edges, walls, first):
        bands = self.lines(geo.row_bands(body))
        for lo, hi in zip(edges, edges[1:]):
            box = [b for b in bands if lo <= min(w.v0 for w in b) < hi]
            if not box:
                continue
            rows = [geo.cell_matrix([b], self.page_cells)[0] for b in box]
            valued = [r for r in rows if self.has_value(r)]
            # A box is one record only where the column walls run through it
            # (T2, Difesa 4, EE): A1 draws walls in its header alone, and its
            # subtotal and the next operator's name share a box.
            walled = sum(1 for _u, a, b in walls if a <= lo + TOL and b >= hi - TOL) >= 2
            if walled and len(valued) <= 1 and len(rows) > 1:
                joined = [" ".join(filter(None, col)) for col in zip(*rows)]
                self.place(sp, joined, None, first)
            else:
                for band, row in zip(box, rows):
                    self.place(sp, self.label_outside(row, band, walls), band, first)
            first = False

    def label_outside(self, row, band, walls):
        """A row label printed left of its line's walls, outside the box its
        values are in (T20's `Altri`, `TOTALE`, right-aligned under the stub
        and `Pos.`): the first column's, wherever its width reaches."""
        lead = next((i for i, t in enumerate(row) if t), None)
        if (not lead or self.order[0].type not in TEXT_TYPES
                or self.order[lead].type in TEXT_TYPES | {"code"}
                or gra.value_shape(row[lead]) in VALUE_SHAPES):
            return row          # only a label in a column of values (T20's `Pos.`)
        v = sum(w.vmid for w in band) / len(band)
        crossing = [u for u, a, b in walls if a - TOL <= v <= b + TOL]
        cell = self.page_cells[lead]
        mine = [w for w in band if cell["u0"] < w.umid < cell["u1"]]
        if crossing and mine and max(w.u1 for w in mine) <= min(crossing) + TOL:
            row = list(row)
            row[0], row[lead] = row[lead], ""
        return row

    def lines(self, bands):
        """A band printed inside the height of the line above, in columns
        that line leaves empty, is part of that line. MAE A1 sets some
        customs values 3-4 pt low (p181 `105.500,00` at v 137.4-138.9 under
        a line at 133.7-138.7): read as a line of its own, its authorisation
        looked customs-free and entered the EURO subtotal."""
        out = []
        for band in bands:
            if out:
                above = out[-1]
                taken = {i for i, t in enumerate(
                    geo.cell_matrix([above], self.page_cells)[0]) if t}
                mine = {i for i, t in enumerate(
                    geo.cell_matrix([band], self.page_cells)[0]) if t}
                if (all(w.vmid <= max(x.v1 for x in above) for w in band)
                        and not taken & mine):
                    above.extend(band)
                    continue
            out.append(list(band))
        return out

    def has_value(self, row):
        """A value in a column that holds values. In a text column a value's
        shape is only a wrapped description's last line (DOG M `(CLIN` /
        `5)`, MAE A1 `CAL.` / `5,56`): it does not make the line a record."""
        # A value may carry a comment after it in its cell: B4's
        # `265.139.737,31 (pari a 3,43 %)`.
        return any(gra.value_shape(t.split(" (")[0].strip()) in VALUE_SHAPES
                   for t, col in zip(row, self.order) if t and col.type not in TEXT_TYPES)

    def place(self, sp, row, band, first):
        """A record, a group heading, or the continuation of the record above."""
        row = [gra.normalise_text(t) for t in row]
        if band is not None and any(row):
            # A `block` column's label stands in a cell spanning its rows (T13).
            v = sum(w.vmid for w in band) / len(band)
            for i, col in enumerate(self.order):
                if col.type == "block" and not row[i]:
                    cell = self.page_cells[i]
                    row[i] = next((text for u, v0, v1, text in self.blocks
                                   if cell["u0"] < u < cell["u1"] and v0 < v < v1), "")
        if not any(row):
            return
        # A total row opens on its label (`Totale complessivo`); a description
        # that mentions a total (`... TOTALE ...` in A1's Materiale) is not one.
        lead = next(t for t in row if t)
        is_total = bool(TOTAL_LEAD.match(lead)) and self.has_value(row)
        if not is_total and not self.has_value(row):
            # A group heading is one run of text starting in the first column
            # (A1 `A.C.S.A. STEEL FORGINGS S.P.A.`); one in a later column is
            # the wrapped description of the record above.
            # On a page's first line too, after a subtotal: nothing continues
            # a subtotal (MAE I p1154 opens on `OCEANIA`).
            closed = (self.last() or {}).get("kind") == "subtotal"
            if (self.spec.group and band is not None and (not first or closed)
                    and len(geo.column_bands(band)) == 1
                    and min(w.u0 for w in band) < self.page_cells[0]["u1"]):
                text = " ".join(w.text.strip() for w in band)
                if is_note(text):
                    # Where a heading would be: a footnote (B4 `(*) ...`; D, I `LE
                    # AUTORIZZAZIONI CON VALORE NULLO ...`). A record's wrapped line
                    # is never one: it continues the record, above.
                    self.result.notes.append((sp.pdf_page, sp.printed_page, text))
                    return
                self.group = " ".join(w.text for w in band)
                self.emit(sp, "group", [], group=self.group)
                return
            last = self.last()
            if last is not None and (first or band is not None):
                for i, text in enumerate(row):
                    if text:
                        last["cells"][i] = f"{last['cells'][i]} {text}".strip()
                return
        named = SUBTOTAL_TAIL.match(lead) if self.has_value(row) else None
        if is_total:
            kind = "total"
        elif named:
            return self.emit(sp, "subtotal", row, group=self.group or named.group(1))
        elif self.spec.group and self.only_amounts(row):
            kind = "subtotal"
        elif any(rx.search(lead) for rx, _expr in self.spec.totals) and self.has_value(row):
            kind = "subtotal"       # a summary line in the grid, by its label (T20)
        else:
            kind = "data"
        self.emit(sp, kind, row, group=self.group if kind != "total" else None)

    def only_amounts(self, row):
        """A subtotal prints amounts alone, and at least two: MAE A1 boxes
        `3.700.813,79  0,00` under each operator, EURO and Val. Fini Dog.
        both, even at zero. An authorisation line can print its customs value
        alone (`34.299,20` under Val. Fini Dog.): that is a record, and read
        as a subtotal it cut its operator in two."""
        filled = [(i, t) for i, t in enumerate(row) if t]
        # A count too (B4's area lines: `22 | 37.468.488,89`).
        return (len(filled) >= 2
                and all(self.order[i].type in ("amount", "number") for i, _t in filled))

    def last(self):
        rows = [r for r in self.result.rows if r["kind"] != "group"]
        return rows[-1] if rows else None

    def emit(self, sp, kind, row, group=None):
        row, carried = self.carry(sp, kind, list(row) if row else [])
        self.row_ord += 1
        self.result.rows.append({
            "carried": carried,
            "pdf_page": sp.pdf_page, "printed_page": sp.printed_page,
            "row_ord": self.row_ord, "kind": kind, "group": group,
            "cells": list(row) if row else [""] * len(self.order),
            "order": self.order, "year_of": self.year_of,
            "source_file": sp.doc.name.split("/")[-1],
        })


    def carry(self, sp, kind, row):
        """A `carry` column's text, printed on a group's first row only (MEF II
        `Ditta`), filled into the rows under it. The group's subtotal names
        it (`<name> Totale`): a different name is an issue, never overwritten.
        Returns the row and the indices filled in, not printed."""
        carried = set()
        for i, col in enumerate(self.order or []):
            if col.type != "carry" or i >= len(row):
                continue
            if kind == "data":
                if row[i]:
                    self.carried[i] = row[i]
                elif i in self.carried:
                    row[i] = self.carried[i]
                    carried.add(i)
            elif kind in ("subtotal", "total"):
                named = SUBTOTAL_TAIL.match(row[i]) if kind == "subtotal" else None
                if named and self.carried.get(i, named.group(1)) != named.group(1):
                    self.result.issues.append(
                        (sp.pdf_page, "carry", f"{col.key} {self.carried[i]!r} carried "
                                               f"into a group closed by {row[i]!r}"))
                self.carried.pop(i, None)
        return row, carried


class Refused(Exception):
    """The table cannot be read as agreed: its header does not match."""


def read_table(year, key, spec=None, root=".", pages=None):
    """`pages` (SourcePage list) instead of the table index: the tests read
    the sources directly, since `Out/` is not in git."""
    spec = spec or specs()[key]
    if any(c.event for c in spec.columns):
        return read_list(year, key, spec,
                         pages if pages is not None else source.table_pages(year, key, root))
    reader = Reader(year, key, spec)
    for sp in pages if pages is not None else source.table_pages(year, key, root):
        reader.read_page(sp)
    if spec.link:
        link_items(reader.result, spec)
    return reader.result


LIST_HEADING = re.compile(r"^SOCIET\S*\s+([A-Z]+)\s+(\d{4})\s*\((\d+)\)\s*:?$")
LIST_ITEM = re.compile(r"^(\d+)\.\s*(.*)$")
REGISTER_NO = re.compile(r"^(?:(Nr\.?)\s*)?(\d{5})$")
DATE_WORD = re.compile(r"^\d{2}/\d{2}/\d{4}$")
REASON = "Motivazione:"
TOUCH = 0.5         # pt between two spans of one word


def read_list(year, key, spec, pages):
    """A register list (Difesa A5/2): under a heading `SOCIETA' <WORD> <year>
    (<n>)`, numbered items, each a name (over one or more lines), a register
    number, perhaps a date, perhaps `Motivazione: ...`. No grid: each value is
    known by its shape and taken from the item's lines in page order, never
    the text layer's (it gives p1278's numbers after the last reason). The
    heading's WORD names the `event` column the date fills; with no date
    printed, the edition's year (implied, flagged). The count in the heading
    is checked (`totals.tsv`); a gap in the numbering is an issue."""
    result = Result(key)
    order = spec.printed
    by_event = {c.event: c for c in order if c.event}
    label_of = {lab.lower(): c.key for c in order for lab in c.labels if lab}
    seen_labels = {}
    event, number, row_ord, item = None, 0, 0, None

    def close():
        if item is None:
            return
        name, reason, code, date, sp = (item[k] for k in ("name", "reason", "code", "date",
                                                            "sp"))
        cells = {"denominazione": " ".join(name), "motivazione": " ".join(reason),
                 "numero_iscrizione": code or ""}
        row = {"kind": "data", "group": None, "carried": [], "order": order,
               "year_of": {}, "row_ord": item["row_ord"], "pdf_page": sp.pdf_page,
               "printed_page": sp.printed_page, "source_file": sp.doc.name.split("/")[-1]}
        col = by_event[event]
        cells[col.key] = date or ""
        if not date:
            row["implied"] = {col.key: str(year)}
            row["notes"] = [f"{col.key}: no date printed, the edition's year"]
        row["cells"] = [cells.get(c.key, "") for c in order]
        result.rows.append(row)

    for sp in pages:
        _frame, words = page_words(sp)
        started = False
        for band in geo.row_bands(words):
            band = [w for w in sorted(band, key=lambda w: w.u0) if w.text.strip()]
            text = " ".join(w.text.strip() for w in band)
            if not text:
                continue
            heading = LIST_HEADING.match(text)
            first = LIST_ITEM.match(band[0].text.strip())
            if heading:
                close()
                item, number = None, 0
                event = heading.group(1)
                if event not in by_event:
                    raise Refused(f"list `{text}`: no `event {event}` column in columns.tsv")
                result.totals.append((sp.pdf_page, sp.printed_page,
                                      f"SOCIETA' {event} {heading.group(2)}", heading.group(3)))
                started = True
                continue
            if not started and not (first and event):
                continue            # above the list on this page: furniture, prose
            started = True
            labels = [label_of.get(w.text.strip().lower()) for w in band]
            if all(labels):
                seen_labels.update({k: w.text.strip() for k, w in zip(labels, band)})
                continue
            if first:
                close()
                n = int(first.group(1))
                if n != number + 1:
                    result.issues.append((sp.pdf_page, "list", f"item {n} after {number}"))
                number = n
                row_ord += 1
                item = {"name": [], "reason": [], "code": None, "date": None, "sp": sp,
                        "row_ord": row_ord, "in_reason": False}
                band = band[1:]
                if first.group(2).strip():          # `13. VULKAN ...` as one word
                    item["name"].append(first.group(2).strip())
            if item is None:
                result.issues.append((sp.pdf_page, "list", f"line {text[:60]!r} outside an item"))
                continue
            prev = None
            for w in band:
                t = " ".join(w.text.split())
                # Two spans that touch are one word (p1276: bold `S.r.l`, then `.`).
                touching = prev is not None and w.u0 - prev.u1 < TOUCH and not w.text[:1].isspace()
                prev = w
                if (m := REGISTER_NO.match(t)) and item["code"] is None:
                    item["code"] = m.group(2)
                    if m.group(1):
                        seen_labels.setdefault("numero_iscrizione", m.group(1))
                elif DATE_WORD.match(t) and item["date"] is None:
                    item["date"] = t
                elif t.startswith(REASON):
                    item["in_reason"] = True
                    seen_labels.setdefault("motivazione", REASON.rstrip(":"))
                    if t[len(REASON):].strip():
                        item["reason"].append(t[len(REASON):].strip())
                elif t:
                    words = item["reason" if item["in_reason"] else "name"]
                    if touching and words:
                        words[-1] += t
                    else:
                        words.append(t)
        result.pages[("header" if started else "cover")] = \
            result.pages.get("header" if started else "cover", 0) + 1
    close()
    result.labels = [seen_labels.get(c.key, "") for c in order]
    result.columns = list(order)
    return result


def link_items(result, spec):
    """Each item's amount, by the table's `link` rule (columns.tsv): sets
    `link` (the amount row's `row_ord`) on every item it can decide, `unit`
    (the opener row's `row_ord`) on every row, and
    moves a wrapped description off an amount line to the item above.
    Two conventions of the maintainer (2026-10-21) set more: an amount with
    no item of its own, in a unit of more amounts than items, gets the item
    above's description (`copied`: column indices, `notes`: the row's flag);
    an item in a unit with no amount gets an amount of 0 (`implied`)."""
    amount, item, opener = spec.link.link
    # The item's side of the line: its text columns from <item> on (A1: Unita'
    # Misura, Materiale). Valuta is the amount's, and stays with it.
    side = [c.key for c in spec.printed]
    side = side[side.index(item):]
    text = [c.key for c in spec.printed if c.type in TEXT_TYPES and c.key in side]
    described = [c.key for c in spec.printed if c.type in TEXT_TYPES | {"code"} and c.key in side]
    units, unit, group = [], None, object()
    for r in result.rows:
        if r["kind"] != "data":
            continue
        if unit is None or _cell(r, opener) or r["group"] != group:
            unit = []
            units.append(unit)
        group = r["group"]
        r["unit"] = unit[0]["row_ord"] if unit else r["row_ord"]
        unit.append(r)
    for unit in units:
        items = [r for r in unit if _cell(r, item)]
        amounts = [r for r in unit if _cell(r, amount)]
        for r in amounts:
            if _cell(r, item):
                continue
            above = [x for x in items if x["row_ord"] < r["row_ord"]]
            for i, col in enumerate(r["order"]):
                if col.key in text and r["cells"][i]:
                    if not above:
                        result.issues.append((r["pdf_page"], "link", f"{col.key} "
                                              f"{r['cells'][i]!r} on an amount line, no item above"))
                        continue
                    above[-1]["cells"][i] = f"{above[-1]['cells'][i]} {r['cells'][i]}".strip()
                    r["cells"][i] = ""
        where = f"{unit[0]['group'] or ''} {_cell(unit[0], opener)}".strip()
        if not items:
            result.issues.append((unit[0]["pdf_page"], "link",
                                  f"{where}: {len(amounts)} amounts, no item"))
            continue
        if not amounts:
            # LEONARDO p260: a dated item, its amount boxes printed empty.
            for x in items:
                x["link"] = x["row_ord"]
                x["implied"] = {amount: "0"}
                x.setdefault("notes", []).append(
                    f"{amount} 0: amount boxes printed empty (maintainer, 2026-10-21)")
            continue
        if len(amounts) > len(items):
            # FABARM p142: one item, a second amount box on a line of its own.
            for a in amounts:
                if _cell(a, item):
                    continue
                above = [x for x in items if x["row_ord"] < a["row_ord"]]
                if not above:
                    continue
                src, a["copied"] = above[-1], []
                for i, col in enumerate(a["order"]):
                    if col.key in described and not a["cells"][i] and _cell(src, col.key):
                        a["cells"][i] = _cell(src, col.key)
                        a["copied"].append(i)
                a.setdefault("notes", []).append(
                    f"copied from row {src['row_ord']}: "
                    + ", ".join(a["order"][i].key for i in a["copied"]))
            items = [r for r in unit if _cell(r, item) or r.get("copied")]
        if len(amounts) > len(items):
            result.issues.append((unit[0]["pdf_page"], "link",
                                  f"{where}: {len(amounts)} amounts, {len(items)} items"))
            continue
        if len(amounts) == len(items):
            for a, x in zip(amounts, items):
                x["link"] = a["row_ord"]
            continue
        for x in items:
            cover = [a for a in amounts if a["row_ord"] <= x["row_ord"]]
            if cover:
                x["link"] = cover[-1]["row_ord"]
            else:
                result.issues.append((x["pdf_page"], "link",
                                      f"{where}: item {_cell(x, item)} above the first amount"))
        covered = {x.get("link") for x in items}
        for a in amounts:
            if a["row_ord"] not in covered:
                result.issues.append((a["pdf_page"], "link",
                                      f"{where}: amount {_cell(a, amount)} covers no item"))


# --------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------


def parse(col_type, raw):
    """(value, problem) for one printed cell of a column type; never a guess."""
    raw = raw or ""
    if raw == "":
        return None, None
    if col_type in ("text", "code", "group", "carry", "block"):
        return raw, ("undecoded" if glyphs.undecoded(raw) else None)
    if col_type in ("amount", "number", "years"):
        if raw in gra.DASHES:
            return None, None
        value = gra.parse_number(raw)
        return value, (None if value is not None else "not a number")
    if col_type == "date":
        value = gra.parse_date(raw)
        return value, (None if value is not None else "not a date")
    raise ValueError(col_type)


# --------------------------------------------------------------------------
# checks: the table's own printed totals against its rows (D7)
# --------------------------------------------------------------------------


def _cell(row, key):
    for i, col in enumerate(row["order"]):
        if col.key == key:
            return row["cells"][i]
    return ""


def _effective(row, col, spec):
    """A row's value in an additive column; a blank cell counts as the
    column named by its `else` rule (A1: EUR rows leave EURO blank)."""
    if col.unless and _cell(row, col.unless):
        return 0
    value, _ = parse("amount", _cell(row, col.key))
    if value is None and col.else_col:
        when_col, when_value = col.else_when
        if _cell(row, when_col) == when_value:
            value, _ = parse("amount", _cell(row, col.else_col))
    return value or 0


def _status(computed, value):
    """PASS when equal; ROUNDING within 2 cents (the source's own rounding of
    converted amounts: reported apart, never as a pass); FAIL otherwise."""
    if computed == value:
        return "PASS"
    return "ROUNDING" if abs(computed - value) <= ROUNDING else "FAIL"


ROUNDING = __import__("decimal").Decimal("0.02")


def checks(result, spec):
    """[(check, printed, computed, status)]: PASS, ROUNDING, FAIL, or
    UNCHECKED for a printed total no rule recomputes (reported, never dropped).
    The table rebuilt from its facts, compared with its printed figures
    (`lib/views`)."""
    from lib import views
    return views.checks(*views.split(result), spec)
