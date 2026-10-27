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
NOTE_START = re.compile(r"^\s*(?:\(\s*pari\s+a\b|\(\s*\*+\s*\)|\*+|\(\d+\)|\d{1,2}\s{2,}\S|Fonte\b|Legenda\b|Note?\b|N\.\s*B\.)",
                        re.IGNORECASE)
# The page stamp a table prints in its own direction, outside its grid: MAE
# `MAECI - UAMA - CENTRO INFORMATICO  Pagina 80 di 537  TAB A1`, Dogane
# `Pag. 9 di 9`. Not table content, never lost text.
STAMP = re.compile(r"Pag(?:ina|\.)?\s*\d+\s*di\s*\d+|CENTRO\s+INFORMATICO|^TAB\.?\s*\S+$",
                   re.IGNORECASE)
# The Parliament's running head, in the table's direction on a page that does
# not rotate it (Difesa 4: `Camera dei Deputati … – 1292 – Senato della Repubblica`).
FURNITURE = re.compile(r"Camera\s+dei\s+Deputati|Senato\s+della\s+Repubblica|LEGISLATURA|"
                       r"DISEGNI\s+DI\s+LEGGE|DOCUMENTI|^\W*\d{1,4}\W*$")
RUNNING_HEAD = re.compile(r"Camera\s+dei\s+Deputati|Senato\s+della\s+Repubblica|LEGISLATURA\s+[—–-]")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
PERIOD = re.compile(r"((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2})")
# A line naming a block's year alone (T21 `Anno 2022` over each year's block).
YEAR_HEADING = re.compile(r"(?:Anno\s+)?((?:19|20)\d{2})", re.IGNORECASE)
FOOTNOTE = re.compile(r"^\(\s*\*+\s*\)")
# An operator or licence with no movement in the year (MAE N1, O1, P1, P2).
NO_MOVEMENT = re.compile(r"(?:nessuna|senza)\s+movimentazion[ei]\s+di\s+materiale"
                         r"(?:\s+nel\s+periodo\s+di\s+riferimento)?", re.IGNORECASE)
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
    span: int = None            # `periods`: a column's years past its first (T3: 4)
    scales: str = None          # `scale <col>`: the unit printed after <col>'s value (T6)
    rank: bool = False          # `rank`: a position printed in brackets, `(76)` (T6)
    prints_text: bool = False   # `text`: a year column printing text (T14.1's operator)
    under: str = None           # `under <col>`: printed under <col>'s value, its cell (T1)
    colour: dict = None         # `colour`: {legend label: column or number}, from fills (T15)

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
        return [c for c in self.columns if c.type not in ("link", "unit", "company", "companies",
                                                           "bank", "cab")
                and not (c.type == "group" and not any(c.labels)) and not c.colour]

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
    def units(self):
        return [c for c in self.columns if c.type == "unit"]

    @property
    def company(self):
        return next((c for c in self.columns if c.type == "company"), None)

    @property
    def company_lists(self):
        return [c for c in self.columns if c.type == "companies"]

    @property
    def colours(self):
        """The columns read from the cells' fill (T15's `scadute`)."""
        return [c for c in self.columns if c.colour]

    @property
    def years(self):
        return next((c for c in self.columns if c.type == "years"), None)


def _rows(name):
    with open(DATA / name, encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def not_imported():
    """The tables the maintainer chose not to import: one `none` line each in
    columns.tsv (charts, 2026-10-25). No spec: nothing extracts them."""
    return {row["table"] for row in _rows("columns.tsv") if row["type"] == "none"}


def specs():
    out = {}
    for row in _rows("columns.tsv"):
        if row["type"] == "none":
            continue
        col = Column(row["col"], row["type"],
                     [x for x in (row["labels"] or "").split("|")])
        if col.type == "periods":
            # Year columns, each a span of years (T3 `Y-19-Y-15`): read as
            # `years` by its first year, `span` the years after it.
            first, last = (int(x) for x in re.findall(r"Y-(\d+)", col.labels[0]))
            col.type, col.span = "years", first - last
        if (row["sum"] or "").startswith("colour "):
            col.colour = dict(x.strip().split("=") for x in row["sum"][7:].split(";"))
            out.setdefault(row["table"], Spec(row["table"], [])).columns.append(col)
            continue
        rule = (row["sum"] or "").split()
        col.once = "once" in rule
        rule = [w for w in rule if w != "once"]
        if "under" in rule:
            at = rule.index("under")
            col.under, rule = rule[at + 1], rule[:at] + rule[at + 2:]
        if col.type == "link":
            col.link = tuple(rule)
        elif col.type in ("company", "companies", "rowsum", "bank", "cab") or (
                col.type == "unit" and rule):
            col.of = rule[0]
        elif rule[:1] == ["event"]:
            col.event = rule[1]
        elif rule[:1] == ["scale"]:
            col.scales = rule[1]
        elif rule == ["rank"]:
            col.rank = True
        elif rule == ["text"]:
            col.prints_text = True
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
    def rel2(m):                # a date's two-digit year (Difesa 3A `01/01/25`, T1 `2025/24`)
        k = int(year) % 100 - int(m.group())
        return "y" if k == 0 else f"y{-k:+d}"
    text = gra.normalise_text(label).replace("’", "'").lower()
    text = re.sub(r"(?<=[\dy]/)\d{2}\b", rel2, YEAR.sub(rel, text))
    # Spaces do not tell labels apart: T88 p2 breaks `autorizzazion i`.
    return re.sub(r"\s+", "", text)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


@dataclass
class Result:
    key: str
    rows: list = field(default_factory=list)      # dicts, see Reader.emit
    totals: list = field(default_factory=list)    # (pdf_page, printed_page, label, raw)
    notes: list = field(default_factory=list)     # (pdf_page, printed_page, text)
    caption: list = field(default_factory=list)   # printed lines above the first header
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
    # The Parliament's running head does not vote: on a page holding a few
    # rotated rows (DOG A1C89 O) it outnumbers them.
    frame = frame or geo.Frame(geo.dominant_direction(page, exclude=FURNITURE.search)["right"])
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
        printed = self.spec.printed
        if not self.spec.years and len(cells) > len(printed):
            # The gap between two header boxes is no column (MAE T17): an
            # empty cell is dropped when the rest are as many as agreed.
            kept = [c for c in cells if c["text"].strip() or c.get("over", "").strip()]
            if len(kept) == len(printed):
                cells = kept
        if not self.spec.years and len(cells) + 1 == len(printed):
            # One cell holding two agreed labels stacked (MAE N1 `Licenza Data
            # Aut.`: the licence over its date): two columns, the second of no
            # width; `place` gives it the cell's date.
            for k, c in enumerate(cells):
                if k + 1 < len(printed) and fold(c["text"], self.year) in [
                        fold(f"{a} {b}", self.year) for a in printed[k].labels
                        for b in printed[k + 1].labels]:
                    a, b = printed[k].labels[0], printed[k + 1].labels[0]
                    cells = cells[:k] + [{**c, "text": a}, {**c, "text": b, "u0": c["u1"]}] \
                        + cells[k + 1:]
                    self.stacked = (k, k + 1)
                    break
        texts = [gra.normalise_text(c["text"]) for c in cells]
        overs = [gra.normalise_text(c.get("over", "")) for c in cells]
        # A two-level label is stored as printed, top first: `Anno 2022 / N.`.
        labels = [" / ".join(x for x in (o, t) if x) for o, t in zip(overs, texts)]
        printed = self.spec.printed
        measures = [c for c in printed if c.type == "years"]
        order, year_of, seen = [], {}, {}
        runs, run_years, last_run = 0, [], {}
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
                elif not over and YEAR.fullmatch(text) and len(measures) > 1 \
                        and all(fold(text, self.year) in [x.lower() for x in m.labels]
                                for m in measures):
                    # Bare years for several measures (MAE T4): the k-th run of
                    # years is the k-th measure; a run ends where a year repeats.
                    if run_years and int(text) <= run_years[-1]:
                        runs += 1
                        run_years = []
                    run_years.append(int(text))
                    if runs >= len(measures):
                        return self._refuse(pdf_page, labels)
                    year, measure = text, measures[runs]
                    last_run.setdefault(runs, []).append(i)
                elif not over and PERIOD.fullmatch(text) and len(measures) == 1 \
                        and measures[0].span is not None \
                        and fold(text, self.year) in [fold(x, self.year) for x in measures[0].labels]:
                    year, measure = PERIOD.fullmatch(text).group(1), measures[0]   # T3
                if year and measure is None:
                    # Two measures may share a label printed over both (T81:
                    # `N. autorizzazioni e valore in €` over the count and the
                    # value): the k-th column under it is the k-th measure.
                    same = [m for m in measures if fold(label, self.year) in
                            [fold(x, self.year) for x in m.labels]]
                    seen[(year, label)] = k = seen.get((year, label), -1) + 1
                    measure = same[k] if k < len(same) else None
                if measure is None:
                    return self._refuse(pdf_page, labels)
                order.append(measure)
                year_of[i] = int(year)
            if fixed or not year_of:
                return self._refuse(pdf_page, labels)
            if last_run and runs + 1 < len(measures):
                # Measures beyond the runs print under the last run's values, on
                # the line below (T4's % under each value): a column of no width
                # beside each of its cells; `place` fills it.
                extra = measures[runs + 1:]
                stack = {}
                for i in reversed(last_run[runs]):
                    for m in reversed(extra):
                        cells.insert(i + 1, {**cells[i], "text": "", "u0": cells[i]["u1"]})
                        order.insert(i + 1, m)
                        year_of = {(j + 1 if j > i else j): y for j, y in year_of.items()}
                        year_of[i + 1] = year_of[i]
                # Positions after the inserts: each last-run cell, then its stack.
                pos = [j for j, m in enumerate(order) if m is measures[runs]]
                for j in pos:
                    stack[j] = [j + 1 + k for k in range(len(extra))]
                self.stacked_years = stack
                labels = labels + [""] * (len(cells) - len(labels))
        else:
            if len(labels) != len(printed):
                return self._refuse(pdf_page, labels)
            for label, text, col in zip(labels, texts, printed):
                # A two-level label matches by its own (lower) label too (MEF
                # KK1, MAE T17: `Autorizzazioni Generali / AGT 1` is `AGT 1`).
                agreed = [fold(x, self.year) for x in col.labels]
                if fold(label, self.year) not in agreed and fold(text, self.year) not in agreed:
                    return self._refuse(pdf_page, labels)
                order.append(col)
        if self.cells is None:
            # A label the text layer spaces differently (T22 `N umero`) is
            # stored in its agreed spelling: the print does not differ.
            def agreed(lab, col):
                same = [x for x in (col.labels if col is not GAP else [])
                        if "Y" not in x and re.sub(r"\s+", "", x) == re.sub(r"\s+", "", lab)]
                return same[0] if same else lab
            self.result.labels = [agreed(lab, col) for lab, col in zip(labels, order)]
            self.result.columns = order
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

    def summary_line(self, sp, band):
        """A line under the grid printing an agreed summary (`totals.tsv`) and
        its figures (MEF REL/SALDI `Totali (Imp + Esp.) 6.475.669.142,80 €
        570.708.294,23`): its figures, the k-th labelled `(k)` from the second
        on, as a summary page's. False when it is none."""
        words = sorted(band, key=lambda w: w.u0)
        figures = [w for w in words if AMOUNT_WORD.search(w.text.strip())]
        label = gra.normalise_text(" ".join(w.text.strip() for w in words
                                            if w not in figures and w.text.strip() != "€"))
        if not figures or not any(rx.search(label) for rx, _expr in self.spec.totals):
            return False
        for k, w in enumerate(figures, 1):
            name = f"{label} ({k})" if k > 1 else label
            self.result.totals.append((sp.pdf_page, sp.printed_page, name, w.text.strip()))
        return True

    def left_stub(self, cells, header, words):
        """The agreed table opens with unlabelled columns the grid leaves out
        (MEF REL/SALDI: the code and the description stand left of its ruled
        amount columns): they are added, left of the header, divided where the
        lines there start (the widest gaps between line starts)."""
        printed = self.spec.printed
        lo = cells[0]["u0"]
        if len(cells) + 1 == len(printed) and any(printed[-1].labels):
            # The last label printed right of the header box (MAE E `Cat.`).
            hi = cells[-1]["u1"]
            right = sorted((w for w in words if header["v0"] < w.vmid < header["v1"]
                            and w.u0 >= hi - TOL and w.text.strip()), key=lambda w: w.u0)
            text = " ".join(w.text.strip() for w in right)
            if right and fold(text, self.year) in [fold(x, self.year) for x in printed[-1].labels]:
                end = max(w.u1 for w in right)
                return cells + [{"text": text, "u0": hi, "u1": end + (end - hi),
                                 "bold": any(w.bold for w in right)}]
        if len(cells) + 1 == len(printed) and any(printed[0].labels):
            # Its label on the line under the header, left of it (MEF KK1
            # `Istituti di credito`): that line is the header's too.
            under = geo.row_bands([w for w in words if w.vmid > header["v1"] and w.text.strip()])
            first = under[0] if under else []
            text = " ".join(w.text.strip() for w in sorted(first, key=lambda w: w.u0))
            if first and max(w.u1 for w in first) <= lo + TOL and \
                    fold(text, self.year) in [fold(x, self.year) for x in printed[0].labels]:
                header["v1"] = max(w.v1 for w in first)
                return [{"text": text, "u0": min(w.u0 for w in first) - 1, "u1": lo,
                         "bold": any(w.bold for w in first)}] + cells
            return cells
        n = next((i for i, c in enumerate(printed) if any(c.labels)), len(printed))
        if self.spec.years:
            # Year columns print one cell per year: the count says nothing.
            # Their table opens with unlabelled columns when the first agreed
            # column has no label and its header has none left of the years
            # (MAE T5's area).
            n = next((i for i, c in enumerate(printed) if any(c.labels) or c.type == "years"),
                     len(printed))
            if n == 0 or not YEAR.search(cells[0]["text"] + cells[0].get("over", "")):
                return cells
        elif n == 0 or len(cells) + n != len(printed):
            return cells
        if n == 1 and printed[0].type == "block":
            # A block printed under no header cell (T21: `Anno 2022` over each
            # year's block): a column of no width, its value the heading's.
            return [{"text": "", "u0": lo, "u1": lo, "bold": False}] + cells
        left = [w for w in words if w.vmid > header["v1"] and w.u1 <= lo + TOL and w.text.strip()]
        starts = sorted({round(min(w.u0 for w in band)) for band in geo.row_bands(left)}
                        | {round(w.u0) for band in geo.row_bands(left)
                           for w in sorted(band, key=lambda w: w.u0)[:1]})
        if len(starts) < n:
            return cells
        gaps = sorted(range(1, len(starts)), key=lambda i: starts[i] - starts[i - 1])[-(n - 1):] \
            if n > 1 else []
        edges = [starts[0] - 1] + [starts[i] - 1 for i in sorted(gaps)] + [lo]
        return [{"text": "", "u0": a, "u1": b, "bold": False}
                for a, b in zip(edges, edges[1:])] + cells

    def box_header(self, words, walls, levels):
        """A header printed in one ruled box with no wall inside it (MAE B6:
        five labels, the column walls starting under the box), or None. The
        box is the header only when its words, left to right, are the agreed
        labels in their order; the columns divide where a body wall stands in
        the gap between two labels, else midway."""
        if self.spec.years or not self.spec.printed:
            return None
        # Unlabelled leading columns stand left of the box (`left_stub`).
        printed = self.spec.printed
        n = next((i for i, c in enumerate(printed) if any(c.labels)), len(printed))
        labels = [[fold(x, self.year) for x in c.labels] for c in printed[n:]]
        if not labels:
            return None
        for top, bottom in zip(levels, levels[1:]):
            if bottom[0] - top[0] < geo.ROW_GAP_MIN:
                continue
            inside = sorted((w for w in words if top[0] < w.vmid < bottom[0] and w.text.strip()),
                            key=lambda w: w.u0)
            groups, k = [], 0
            for j, alts in enumerate(labels):
                for end in range(k + 1, len(inside) + 1):
                    if fold(" ".join(w.text.strip() for w in inside[k:end]), self.year) in alts:
                        groups.append(inside[k:end])
                        k = end
                        break
                else:
                    break
            if len(groups) != len(labels) or k != len(inside):
                continue
            edges = [min(w.u0 for w in groups[0]) - 1]
            for a, b in zip(groups, groups[1:]):
                gap = (max(w.u1 for w in a), min(w.u0 for w in b))
                edges.append(next((u for u, v0, _v1 in walls if gap[0] < u < gap[1]
                                   and v0 >= bottom[0] - geo.RULE_TOL), sum(gap) / 2))
            edges.append(max(w.u1 for w in groups[-1]) + 1)
            # The box's own edges, where its walls stand.
            box = [u for u, v0, v1 in walls if v0 <= top[0] + geo.RULE_TOL
                   and v1 >= bottom[0] - geo.RULE_TOL]
            if len(box) >= 2:
                edges[0], edges[-1] = min(box), max(box)
            cells = [{"text": " ".join(w.text.strip() for w in g), "u0": a, "u1": b,
                      "bold": any(w.bold for w in g)}
                     for g, a, b in zip(groups, edges, edges[1:])]
            box_header = {"cells": cells, "v0": top[0], "v1": bottom[0]}
            box_header["cells"] = self.left_stub(cells, box_header, words)
            return box_header
        return None

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
        self.levels, self.words = levels, words
        header = geo.grid_header(sp, words, frame, is_label=gra.is_label_row)
        if header:
            header["cells"] = self.left_stub(self.split_merged(header, words, walls),
                                             header, words)
        else:
            header = self.box_header(words, walls, levels)
        if header and (self.cells is None or self.matches(header["cells"])):
            first = self.cells is None
            if not self.accept_header(header["cells"], sp.pdf_page):
                raise Refused(self.result.issues[-1][2])
            if first:
                self.read_caption(sp, [w for w in words if w.vmid < header["v0"]])
            kind, cells, v_top = "header", self.cells, header["v1"]   # as accepted
            self.frame = frame
        elif self.cells is not None and self.summary_page(words):
            return self.read_summary(sp, words)
        elif self.cells is None:
            # Before the first header: the table's own cover (DOG M1 p1,
            # `Esportazione Definitiva (EX) / Riepilogo per operatore / TAB. M1`).
            # It may name the table; it may not hold its figures.
            self.count("cover")
            self.read_caption(sp, words)
            return
        else:
            cells = _page_columns(self.cells, walls)
            if cells is None:
                # No grid: the summary page, perhaps under the table's last rows
                # (MAE L p168: WASS's heading, row and subtotal, then the
                # summary). Those rows are read in the previous page's columns.
                bands = geo.row_bands(words)
                start = next((i for i, b in enumerate(bands) if self.opens_summary(b)), 0)
                if start and getattr(self, "page_cells", None):
                    self.count("continuation")
                    above = [w for b in bands[:start] for w in b]
                    lo, hi = min(w.v0 for w in above), max(w.v1 for w in above)
                    self.read_body(sp, above, [lo - 1, hi + 1], [], first=True)
                    return self.read_summary(sp, [w for b in bands[start:] for w in b])
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
        # Any word reaching into the grid's columns is the grid's: an operator's
        # heading starts in the margin (F1 p17 `LEE Srl`, its middle 0.4 pt out).
        body = [w for w in words
                if v_top < w.vmid < v_bottom and w.u1 > u_lo and w.u0 < u_hi]
        self.check_outside(sp, words, body + boxed, v_bottom, v_top)
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

    def read_caption(self, sp, words):
        """The table's caption: each printed line above its first header, and
        a cover page's, as printed (maintainer, 2026-10-25), its `TAB. M1`
        too. The page stamp and the running head are furniture; a printed
        amount is no caption, and stays an issue."""
        for band in geo.row_bands(words):
            text = " ".join(" ".join(w.text for w in sorted(band, key=lambda w: w.u0)).split())
            if text and not RUNNING_HEAD.search(text) and (
                    not STAMP.search(text) or re.fullmatch(r"TAB\.?\s*\S+", text)):
                self.result.caption.append(text)
                heading = YEAR_HEADING.fullmatch(text)
                if heading and self.headless_block():
                    self.heading = heading.group(1)     # the first block's (T21)
        for w in words:
            if AMOUNT_WORD.search(w.text):
                self.result.issues.append(
                    (sp.pdf_page, "outside", f"amount {w.text!r} outside the table"))

    def check_outside(self, sp, words, body, v_bottom, v_top=None):
        """Words outside the body. A note under the table is kept, the page
        stamp is furniture; any other line under the table, a word beside the
        grid within its height, and a printed amount anywhere outside it, is
        an issue: never silently dropped."""
        inside = set(map(id, body))
        rest = [w for w in words if id(w) not in inside]
        if v_top is not None:
            for band in geo.row_bands([w for w in rest if v_top < w.vmid < v_bottom]):
                text = " ".join(w.text.strip() for w in band)
                if text and not STAMP.search(text):
                    self.result.issues.append(
                        (sp.pdf_page, "outside", f"{text[:60]!r} beside the table"))
                inside.update(map(id, band))
        below = [b for b in geo.row_bands([w for w in rest if w.vmid > v_bottom])]
        noting = False
        for band in below:
            text = " ".join(w.text for w in band)
            if STAMP.search(text):
                pass                # the page stamp: furniture, after a note too
            elif is_note(text) or noting:
                noting = True
                self.result.notes.append((sp.pdf_page, sp.printed_page, text))
            elif not self.summary_line(sp, band):
                self.result.issues.append(
                    (sp.pdf_page, "outside", f"line {text[:60]!r} under the table"))
            inside.update(map(id, band))
        for w in rest:
            if id(w) not in inside and AMOUNT_WORD.search(w.text):
                self.result.issues.append(
                    (sp.pdf_page, "outside", f"amount {w.text!r} outside the table"))

    def label_line(self, sp, band):
        """A line that is a label, not a row: kept as a note. A colour legend's
        label (T15 `scadute`), or text in value columns only, running across
        their walls (T15's `Totali per anno`, a merged cell under the total
        row): a value column never holds text. True when taken."""
        text = gra.normalise_text(" ".join(w.text for w in sorted(band, key=lambda w: w.u0)))
        legend = {label for c in self.spec.colours for label in c.colour}
        cells = getattr(self, "page_cells", None) or []
        values = [(c["u0"], c["u1"]) for c, col in zip(cells, self.order or [])
                  if col.type in ("years", "amount", "number")]
        inner = [u for (a, b) in values for u in (a, b)]
        crossing = (values and re.search(r"[^\W\d_]{3}", text)
                    and not any(gra.parse_number(w.text.strip()) is not None for w in band)
                    and not NO_MOVEMENT.search(text)
                    and all(any(a - 1 <= w.u0 and w.u1 <= b + 1 for a, b in
                                [(min(inner), max(inner))]) for w in band)
                    and any(w.u0 < u - 1 and w.u1 > u + 1 for w in band for u in inner))
        if text in legend or crossing:
            self.result.notes.append((sp.pdf_page, sp.printed_page, text))
            return True
        return False

    def opens_summary(self, band):
        """A line opening with an agreed summary label (`totals.tsv`), its words
        left of the grid's value columns: MAE H1 `Totale Autorizzazioni :`.
        Only for a table whose summary is a page or a block of lines (`count`,
        `sum ... over subtotal` rules), not a grid's own total row."""
        if not any(e.split()[0] == "count" or e.endswith("over subtotal")
                   for _rx, e in self.spec.totals):
            return False
        text = gra.normalise_text(" ".join(w.text.strip() for w in sorted(band, key=lambda w: w.u0)))
        return any(rx.match(text) for rx, _e in self.spec.totals)

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
        # The page stamp is furniture, inside the grid too (MAE B6 p693).
        bands = [b for b in self.lines(geo.row_bands(body))
                 if not STAMP.search(" ".join(w.text.strip() for w in b))
                 and not self.label_line(sp, b)]
        # The summary may start on a page that repeats the header (MAE H1
        # p110), or under its last rows: from its first agreed label on, the
        # page is the summary's, never more of the record above.
        start = next((i for i, b in enumerate(bands) if self.opens_summary(b)), None)
        if start is not None:
            self.read_summary(sp, [w for b in bands[start:] for w in b])
            bands = bands[:start]
        for lo, hi in zip(edges, edges[1:]):
            box = [b for b in bands if lo <= min(w.v0 for w in b) < hi]
            if not box:
                continue
            rows = [geo.cell_matrix([b], self.page_cells)[0] for b in box]
            # `(*) ...` closing a box (B1's last `di cui` line): the asterisk's
            # note, not more of the line's label.
            notes = [i for i, r in enumerate(rows) if i and not self.has_value(r)
                     and FOOTNOTE.match(" ".join(t for t in r if t))]
            for i in notes[:1]:
                self.result.notes.append((sp.pdf_page, sp.printed_page,
                                          " ".join(" ".join(t for t in r if t) for r in rows[i:])))
                box, rows = box[:i], rows[:i]
            valued = [r for r in rows if self.has_value(r)]
            # A box is one record only where the column walls run through it
            # (T2, Difesa 4, EE): A1 draws walls in its header alone, and its
            # subtotal and the next operator's name share a box.
            walled = sum(1 for _u, a, b in walls if a <= lo + TOL and b >= hi - TOL) >= 2
            # Lines a unit ties together (`riga_<unit>` opened by a printed
            # text column: B6's programmes under a country) are rows of their own;
            # a box round a carried name's lines (MAE P1's operators) is joined.
            opener = {c.of for c in self.spec.units if c.of}
            united = any(c.key in opener and c.type == "text" for c in self.order)
            # A box holding the header again or a block's year heading (T21)
            # is no wrapped record: its lines are placed one by one.
            marked = any(self.repeats_header([gra.normalise_text(t) for t in r])
                         or YEAR_HEADING.fullmatch(" ".join(t for t in r if t).strip())
                         for r in rows)
            if walled and len(valued) <= 1 and len(rows) > 1 and not united and not marked:
                joined = [" ".join(filter(None, col)) for col in zip(*rows)]
                words = [w for b in box for w in b]
                self.place(sp, joined, None, first, v=sum(w.vmid for w in words) / len(words))
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

    def no_movement(self, row):
        """`nessuna movimentazione di materiale ...` printed in a value
        column: the row of an operator with no movement, its own record. The
        text leaves the cell, the value is 0 (maintainer, 2026-10-25: a
        correction, in the row's flags). Edits `row`; returns the flag."""
        for i, (t, col) in enumerate(zip(row, self.order)):
            m = NO_MOVEMENT.search(t) if t else None
            if not m or col.type not in ("amount", "number"):
                continue
            printed = NO_MOVEMENT.sub("", t).strip()
            row[i] = printed or "0"
            self.no_movement_flag = (f"no movement: printed {t!r}" if printed
                                     else f"corrected: printed {t!r}, stored 0")
            return self.no_movement_flag
        return None

    def stacked_line(self, row):
        """MAE T4 prints a record on three lines: the values, then the band's
        label and counts, then each value's % under it. A line printing only
        the last run's values, no label, is the record's: below a record whose
        values are filled, its stacked measure; else held for the label line.
        Edits `row` (the held values join it); True when the line is taken."""
        stack = getattr(self, "stacked_years", None)
        if not stack:
            return False
        filled = {i for i, t in enumerate(row) if t}
        held = self.__dict__.pop("held_values", None)
        if filled and filled <= set(stack):
            last = self.last()
            if (last is not None and last["kind"] == "data" and held is None
                    and all(last["cells"][i] for i in filled)
                    and not any(last["cells"][j] for i in filled for j in stack[i])):
                for i in filled:
                    last["cells"][stack[i][0]] = row[i]
                return True
            self.held_values = row
            return True
        if held:
            for i, t in enumerate(held):
                if t and not row[i]:
                    row[i] = t
        return False

    def headless_block(self):
        """The agreed table opens with a `block` printed under no header cell."""
        first = self.spec.printed[0] if self.spec.printed else None
        return first is not None and first.type == "block" and not any(first.labels)

    def repeats_header(self, row):
        """A line printing the agreed labels again, two at least, each in its
        own column (T12 prints its header over every year's block)."""
        hits = 0
        for text, col in zip(row, self.order):
            if not text:
                continue
            if fold(text, self.year) not in [fold(x, self.year) for x in col.labels if x]:
                return False
            hits += 1
        return hits >= 2

    def upright_block(self, cell, v, band):
        """The label printed upright in a cell spanning several rows (T12's
        year, T7's area): the words in the column between the floors that
        cross it above and below this row, but those of `band`."""
        u = (cell["u0"] + cell["u1"]) / 2
        crossing = sorted(lv[0] for lv in self.levels if lv[1] - TOL <= u <= lv[2] + TOL)
        above = max((x for x in crossing if x < v), default=None)
        below = min((x for x in crossing if x > v), default=None)
        if above is None or below is None:
            return ""
        mine = set(map(id, band))
        words = sorted((w for w in self.words if cell["u0"] < w.umid < cell["u1"]
                        and above < w.vmid < below and id(w) not in mine),
                       key=lambda w: (round(w.v0), w.u0))
        return " ".join(" ".join(w.text for w in words).split())

    def place(self, sp, row, band, first, v=None):
        """A record, a group heading, or the continuation of the record above.
        `v`: where a row joined from a box's lines stands (it has no band)."""
        row = [gra.normalise_text(t) for t in row]
        if self.stacked_line(row):
            return
        a, b = getattr(self, "stacked", (None, None))
        if a is not None and row[a] and not row[b] and gra.value_shape(row[a]) == "date":
            row[a], row[b] = "", row[a]          # the stacked cell's date (N1)
        if self.repeats_header(row):
            return              # the header printed again inside the grid (T12, each year)
        text = " ".join(t for t in row if t)
        heading = YEAR_HEADING.fullmatch(text)
        if heading and not self.has_value(row) and self.headless_block():
            self.heading = heading.group(1)
            return
        if (band is not None or v is not None) and any(row):
            # A `block` column's label stands in a cell spanning its rows (T13).
            v = v if band is None else sum(w.vmid for w in band) / len(band)
            for i, col in enumerate(self.order):
                if col.type == "block":
                    # The whole cell, every line of it, on each of its rows
                    # (T7 prints an area on two lines over three rows).
                    cell = self.page_cells[i]
                    row[i] = " ".join(text for u, v0, v1, text in self.blocks
                                      if cell["u0"] < u < cell["u1"] and v0 < v < v1) \
                        or self.upright_block(cell, v, ()) or row[i] \
                        or (getattr(self, "heading", "") if not any(col.labels) else "")
        if not any(row):
            return
        self.no_movement(row)
        text = " ".join(t for t in row if t)
        if not self.has_value(row) and FOOTNOTE.match(text):
            # `(*) ...` under the summary lines (B1): the asterisk's note.
            self.result.notes.append((sp.pdf_page, sp.printed_page, text))
            return
        # A total row opens on its label (`Totale complessivo`); a description
        # that mentions a total (`... TOTALE ...` in A1's Materiale) is not one.
        # The row's label: its first cell, or the first beside a block's value
        # when that one opens a total (T12: the year, then `Totale`; T7 prints
        # `TOTALE` in the block's own column).
        lead = next(t for t in row if t)
        beside = next((t for t, c in zip(row, self.order) if t and c.type != "block"), lead)
        if not TOTAL_LEAD.match(lead) and TOTAL_LEAD.match(beside):
            lead = beside
        # A row named as a block's total in the agreed rule, belonging to no
        # block (MAE T5 `In Mio EUR` under its block): that block's total.
        targets = {t for c in self.order if c.type == "block" and c.against
                   for t in c.against.values()}
        blockless = all(not t for t, c in zip(row, self.order) if c.type == "block")
        is_total = (bool(TOTAL_LEAD.match(lead)) or (lead in targets and blockless)) \
            and self.has_value(row)
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
                heading = " ".join(w.text for w in band)
                above = self.result.rows[-1] if self.result.rows else None
                if above is not None and above["kind"] == "group" and not first:
                    # The heading's second line, no row between (MAE L `...
                    # IMPIANTI` / `SPECIALI`): one heading.
                    self.group = above["group"] = gra.normalise_text(f"{above['group']} {heading}")
                    return
                self.group = heading
                self.emit(sp, "group", [], group=self.group)
                return
            last = self.last()
            # Under a unit opened by a text column (B6, B7: a country), a line
            # filling a cell already filled is the unit's next row, not more of it.
            opener = {c.of for c in self.spec.units if c.of}
            textual = any(c.key in opener and c.type == "text" for c in self.order)
            if (textual and last is not None and last["kind"] == "data"
                    and any(t and last["cells"][i] for i, t in enumerate(row))):
                return self.emit(sp, "data", row, group=self.group)
            if last is not None and (first or band is not None):
                prefix = self.__dict__.setdefault("carry_prefix", {})
                for i, text in enumerate(row):
                    if not text:
                        continue
                    if self.order[i].type == "carry" and i in last.get("carried", ()):
                        # Not more of a value carried down: the next one's
                        # first line (MAE P1 `IVECO DEFENCE` / `VEHICLES S.P.A.`).
                        prefix[i] = f"{prefix.get(i, '')} {text}".strip()
                        continue
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
            # A summary line in the grid, known by its label (T20's `Altri`, the
            # `di cui` shares under B4's total): the table's, no group's.
            return self.emit(sp, "line", row)
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
        flag, self.no_movement_flag = getattr(self, "no_movement_flag", None), None
        # The page's columns, for the row's cells' fill (T15).
        box = {"columns": [(c["u0"], c["u1"]) for c in self.page_cells]} if self.spec.colours else {}
        self.result.rows.append({
            **box,
            **({"notes": [flag]} if flag else {}),
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
                head = self.__dict__.get("carry_prefix", {}).pop(i, "")
                if head:                # a name's first line, printed above (MAE P1)
                    row[i] = f"{head} {row[i]}".strip()
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
    pages = list(pages if pages is not None else source.table_pages(year, key, root))
    for marked, read in ((lambda c: c.event, read_list), (lambda c: c.scales, read_year_cells),
                         (lambda c: c.under, read_stacked),
                         (lambda c: c.prints_text, read_side_by_side),
                         (lambda c: c.type == "anno", read_transposed)):
        if any(marked(c) for c in spec.columns):
            return read(year, key, spec, pages)
    reader = Reader(year, key, spec)
    for sp in pages:
        reader.read_page(sp)
    summaries_under(reader.result, spec)
    stacked_up(reader.result, getattr(reader, "stacked", None))
    if spec.link:
        link_items(reader.result, spec)
    if spec.colours:
        from_colours(reader.result, spec, pages)
    set_units(reader.result, spec)
    return reader.result


def read_transposed(year, key, spec, pages):
    """A table printing a year per column and a measure per row (MAE T9: each
    cell a value and its unit, `261,76 Mio €`): one row per year, `anno` its
    year, each measure from the printed row whose cells end with that column's
    agreed label (its unit). The header must be the agreed year labels."""
    result = Result(key)
    anno = next(c for c in spec.printed if c.type == "anno")
    measures = [c for c in spec.printed if c is not anno]
    row_ord = 0
    for sp in pages:
        frame, words = page_words(sp)
        header = geo.grid_header(sp, words, frame, is_label=gra.is_label_row)
        if not header:
            result.pages["cover"] = result.pages.get("cover", 0) + 1
            continue
        cells = header["cells"]
        labels = [gra.normalise_text(c["text"]) for c in cells]
        if not all(fold(x, year) in [fold(a, year) for a in anno.labels] for x in labels):
            raise Refused(f"printed {labels}, agreed {anno.labels}")
        result.pages["header"] = result.pages.get("header", 0) + 1
        result.labels, result.columns = labels, spec.printed
        per = {}
        for band in geo.row_bands([w for w in words if w.vmid > header["v1"] and w.text.strip()]):
            row = [gra.normalise_text(t) for t in geo.cell_matrix([band], cells)[0]]
            for m in measures:
                unit = fold(m.labels[0], year)
                if row and all(fold(t, year).endswith(unit) for t in row if t) and any(row):
                    per[m.key] = [t[:len(t) - len(m.labels[0])].strip() if t else "" for t in row]
                    break
            else:
                text = " ".join(t for t in row if t)
                if text:
                    result.issues.append((sp.pdf_page, "outside", f"line {text[:60]!r}: no measure"))
        for j, label in enumerate(labels):
            row_ord += 1
            result.rows.append({
                "carried": set(), "pdf_page": sp.pdf_page, "printed_page": sp.printed_page,
                "row_ord": row_ord, "kind": "data", "group": None,
                "cells": [YEAR.search(label).group()] + [per.get(m.key, [""] * len(labels))[j]
                                                         for m in measures],
                "order": spec.printed, "year_of": {},
                "source_file": sp.doc.name.split("/")[-1]})
    return result


DOUBLE_WALL = 4.0         # two rules this close are one wall drawn double (T14.2)
SCALES = {"K": 1000, "Mio": 1000000, "Mrd": 1000000000}
RANK = re.compile(r"\(\s*(\d+)\s*\)")


def read_year_cells(year, key, spec, pages):
    """A table printing under each year a value, its unit and a rank, no label
    each and no wall between them (MAE T6: `2,591 Mrd (1)`). The years are a
    line of the agreed year labels; a word belongs to the year whose label is
    nearest, and is its value, unit or rank by shape. Left of the first year:
    the fixed columns, a number then text (`1 KUWAIT`). A word of no known
    shape, or a year cell missing a part, is an issue (but a zero alone:
    nothing that year, so no unit and no rank): nothing is guessed."""
    result = Result(key)
    printed = spec.printed
    measures = [c for c in printed if c.type == "years"]
    fixed = [c for c in printed if c.type != "years"]
    unit = next(c for c in measures if c.scales)
    rank = next((c for c in measures if c.rank), None)
    value = next(c for c in measures if c.key == unit.scales)
    agreed = [fold(x, year) for x in value.labels]
    row_ord = 0
    for sp in pages:
        frame, words = page_words(sp)
        bands = geo.row_bands([w for w in words if w.text.strip()])
        head = next((i for i, b in enumerate(bands)
                     if len(b) == len(agreed) and all(YEAR.fullmatch(w.text.strip()) for w in b)), None)
        if head is None:
            result.pages["cover"] = result.pages.get("cover", 0) + 1
            continue
        years = sorted(bands[head], key=lambda w: w.u0)
        labels = [w.text.strip() for w in years]
        if sorted(fold(x, year) for x in labels) != sorted(agreed):
            raise Refused(f"printed {labels}, agreed {value.labels}")
        result.pages["header"] = result.pages.get("header", 0) + 1
        result.labels, result.columns = [""] * len(fixed) + labels, printed
        mids = [(w.u0 + w.u1) / 2 for w in years]
        step = min(b - a for a, b in zip(mids, mids[1:]))
        left = mids[0] - step / 2
        for band in bands[head + 1:]:
            band = sorted(band, key=lambda w: w.u0)
            text = " ".join(w.text.strip() for w in band)
            if STAMP.search(text):
                continue
            if is_note(text):
                result.notes.append((sp.pdf_page, sp.printed_page, gra.normalise_text(text)))
                continue
            stub = [w.text.strip() for w in band if (w.u0 + w.u1) / 2 < left]
            if not stub or not stub[0].isdigit():
                result.issues.append((sp.pdf_page, "outside", f"line {text[:60]!r}"))
                continue
            row_ord += 1
            cells, year_of = [stub[0], gra.normalise_text(" ".join(stub[1:]))], {}
            order = list(fixed)
            for j, (label, mid) in enumerate(zip(labels, mids)):
                mine = [w.text.strip() for w in band if (w.u0 + w.u1) / 2 >= left
                        and min(range(len(mids)), key=lambda k: abs(mids[k] - (w.u0 + w.u1) / 2)) == j]
                part = {}
                for t in mine:
                    kind = (unit if t in SCALES else rank if rank and RANK.fullmatch(t)
                            else value if gra.parse_number(t) is not None else None)
                    if kind is None or kind.key in part:
                        result.issues.append((sp.pdf_page, "outside", f"{label} {t!r}"))
                        continue
                    part[kind.key] = t
                # A zero alone: nothing exported that year, no unit, no rank (T6 `0,0`).
                alone = list(part) == [value.key] and gra.parse_number(part[value.key]) == 0
                if part and len(part) < len(measures) and not alone:
                    result.issues.append((sp.pdf_page, "outside", f"{label}: {' '.join(mine)!r} "
                                          "is not value, unit and rank"))
                for m in measures:
                    year_of[len(cells)] = int(YEAR.search(label).group())
                    cells.append(part.get(m.key, ""))
                    order.append(m)
            result.rows.append({
                "carried": set(), "pdf_page": sp.pdf_page, "printed_page": sp.printed_page,
                "row_ord": row_ord, "kind": "data", "group": None, "cells": cells,
                "order": order, "year_of": year_of, "source_file": sp.doc.name.split("/")[-1]})
    return result


def read_side_by_side(year, key, spec, pages):
    """A table printing its years side by side, each year a table of its own
    (MAE T14.1: each year's top 15 operators, a year's `Operatore` its own).
    Each half is the agreed columns between the drawn walls (a double rule one
    wall): the first fixed column (the rank, unlabelled), then the year's
    measures under their labels. In a half, a line with figures is a record;
    a line with text only is part of the nearest record's name (`FABBRICA
    D'ARMI PIETRO BERETTA` / `S.p.A.`); a rank printed alone (centred in its
    cell) is the nearest record's. The k-th records of the halves are row k,
    their ranks the same. A line whose label is an agreed summary line is that
    line, each half's figures under its year."""
    result = Result(key)
    printed = spec.printed
    rank, measures = printed[0], printed[1:]
    text_col = next(c for c in measures if c.prints_text)
    per = 1 + len(measures)
    row_ord = 0
    for sp in pages:
        frame, words = page_words(sp)
        words = [w for w in words if w.text.strip()]
        bands = geo.row_bands(words)
        years_at = next((i for i, b in enumerate(bands)
                         if b and all(re.fullmatch(r"Anno\s+(?:19|20)\d{2}", w.text.strip()) for w in b)),
                        None)
        if years_at is None:
            result.pages["cover"] = result.pages.get("cover", 0) + 1
            continue
        heads = sorted(bands[years_at], key=lambda w: w.u0)
        labels = sorted(bands[years_at + 1], key=lambda w: w.u0)
        walls, _levels = _rules(sp, frame)
        us = sorted(u for u, _a, _b in walls)
        merged = []
        for u in us:
            if merged and u - merged[-1][-1] <= DOUBLE_WALL:
                merged[-1].append(u)
            else:
                merged.append([u])
        us = [sum(g) / len(g) for g in merged]
        lo = max((u for u in us if u < labels[0].u0), default=None)
        hi = min((u for u in us if u > labels[-1].u1), default=None)
        inner = [u for u in us if lo is not None and hi is not None and lo <= u <= hi]
        if len(inner) != per * len(heads):
            raise Refused(f"p{sp.pdf_page}: {len(inner)} walls, {per * len(heads)} agreed")
        edges = [-float("inf")] + inner
        def column(w):
            mid = (w.u0 + w.u1) / 2
            return next((j for j in range(len(edges) - 1) if edges[j] <= mid < edges[j + 1]), None)
        # The header: each half's labels in its measures' columns.
        printed_labels = [""] * (len(edges) - 1)
        for w in labels:
            j = column(w)
            if j is not None:
                printed_labels[j] = gra.normalise_text(f"{printed_labels[j]} {w.text}")
        for h in range(len(heads)):
            for k, m in enumerate(measures):
                got = printed_labels[h * per + 1 + k]
                if fold(got, year) not in [fold(x, year) for x in m.labels]:
                    raise Refused(f"printed {printed_labels}, agreed "
                                  f"{[m.labels[0] for m in measures]}")
        years = [int(YEAR.search(w.text).group()) for w in heads]
        result.pages["header"] = result.pages.get("header", 0) + 1
        result.labels, result.columns = printed_labels, printed
        halves = [{"records": [], "ranks": [], "names": []} for _ in heads]
        lines = []
        for band in bands[years_at + 2:]:
            text = " ".join(w.text.strip() for w in sorted(band, key=lambda w: w.u0))
            if STAMP.search(text):
                continue
            if is_note(text):
                result.notes.append((sp.pdf_page, sp.printed_page, gra.normalise_text(text)))
                continue
            v = sum(w.vmid for w in band) / len(band)
            cells = [""] * (len(edges) - 1)
            for w in sorted(band, key=lambda w: w.u0):
                j = column(w)
                if j is None:
                    result.issues.append((sp.pdf_page, "outside", f"{w.text.strip()!r}"))
                    continue
                cells[j] = gra.normalise_text(f"{cells[j]} {w.text}")
            label = next((c for c in cells if c), "")
            if any(rx.match(label) for rx, _e in spec.totals) or TOTAL_LEAD.match(label):
                lines.append(cells)
                continue
            for h, half in enumerate(halves):
                mine = cells[h * per:(h + 1) * per]
                figures = [t for t, m in zip(mine[1:], measures) if t and not m.prints_text]
                if figures:
                    half["records"].append({"v": v, "cells": mine})
                elif mine[0] and not any(mine[1:]):
                    half["ranks"].append((v, mine[0]))
                elif any(mine[1:]):
                    half["names"].append((v, mine))
        for h, half in enumerate(halves):
            recs = half["records"]
            if not recs:
                continue
            nearest = lambda v: min(recs, key=lambda r: abs(r["v"] - v))  # noqa: E731
            at = 1 + measures.index(text_col)
            for v, mine in half["names"]:
                rec = nearest(v)
                name, other = mine[at], [t for k, t in enumerate(mine[1:], 1) if t and k != at]
                if other:
                    result.issues.append((sp.pdf_page, "outside", f"{' '.join(mine)!r}"))
                    continue
                before = v < rec["v"]
                rec["cells"][at] = gra.normalise_text(
                    f"{name} {rec['cells'][at]}" if before else f"{rec['cells'][at]} {name}")
            for v, r in half["ranks"]:
                rec = nearest(v)
                if rec["cells"][0]:
                    result.issues.append((sp.pdf_page, "outside", f"rank {r!r}: its record has one"))
                else:
                    rec["cells"][0] = r
        count = max(len(h["records"]) for h in halves)
        for k in range(count):
            recs = [h["records"][k]["cells"] if k < len(h["records"]) else [""] * per for h in halves]
            ranks = {c[0] for c in recs if c[0]}
            if len(ranks) != 1:
                result.issues.append((sp.pdf_page, "rank", f"record {k + 1}: ranks {sorted(ranks)}"))
            row_ord += 1
            result.rows.append(_side_row(sp, row_ord, "data", [min(ranks, default="")], recs,
                                         rank, measures, years))
        for cells in lines:
            label = next(c for c in cells if c)
            recs = [cells[h * per:(h + 1) * per] for h in range(len(heads))]
            for c in recs:              # the label, printed in each half, stored once
                for j, t in enumerate(c):
                    if t == label:
                        c[j] = ""
            row_ord += 1
            kind = "total" if TOTAL_LEAD.match(label) else "line"
            result.rows.append(_side_row(sp, row_ord, kind, [label], recs, rank, measures, years))
    return result


def _walls_merged(sp, frame):
    """The page's walls as positions, a double rule one wall."""
    walls, levels = _rules(sp, frame)
    merged = []
    for u in sorted(u for u, _a, _b in walls):
        if merged and u - merged[-1][-1] <= DOUBLE_WALL:
            merged[-1].append(u)
        else:
            merged.append([u])
    return [sum(g) / len(g) for g in merged], levels


SECTION = re.compile(r"[A-ZÀ-Ù]{4,}")


def read_stacked(year, key, spec, pages):
    """A table whose record prints a value over its count, in one cell (MAE
    T1, `under` in columns.tsv), in sections each repeating the header: a line
    of one capitalised word (`ESPORTAZIONI`) heads a section, its header a
    label per column (the first, the text column's; `Anno 2025`, `Variaz.
    2025/24`). The columns are the walls across the header; a column's
    year is its label's (Y-k for `Variaz. Y/Y-k`). A line with a decimal
    value opens a record; the line under it with whole values is its count.
    The text column's lines, between two rules drawn across the table, are
    the nearest record's label. A `-` before a figure is its sign."""
    result = Result(key)
    printed = [c for c in spec.printed if c.type != "group"]
    stub = printed[0]
    tops = [c for c in printed[1:] if not c.under]
    below = {c.under: c for c in printed if c.under}
    row_ord, section = 0, None
    for sp in pages:
        frame, words = page_words(sp)
        bands = [b for b in geo.row_bands([w for w in words if w.text.strip()])
                 if not STAMP.search(" ".join(w.text for w in b))]
        us, levels = _walls_merged(sp, frame)
        width = max((b - a for _v, a, b in levels), default=0)
        across = sorted(v for v, a, b in levels if b - a >= width - 2 * DOUBLE_WALL)
        heads = [i for i, b in enumerate(bands)
                 if fold(" ".join(w.text for w in sorted(b, key=lambda w: w.u0)), year)
                 .startswith(fold(stub.labels[0], year))]
        if not heads:
            result.pages["cover"] = result.pages.get("cover", 0) + 1
            continue
        result.pages["header"] = result.pages.get("header", 0) + len(heads)
        lo, hi = min(across), max(across)
        edges = [u for u in us if min(a for _v, a, _b in levels) - DOUBLE_WALL <= u
                 <= max(b for _v, _a, b in levels) + DOUBLE_WALL]
        def column(w):
            mid = (w.u0 + w.u1) / 2
            return next((j for j in range(len(edges) - 1) if edges[j] <= mid < edges[j + 1]), None)
        def cells_of(band):
            out = [[] for _ in range(len(edges) - 1)]
            for w in sorted(band, key=lambda w: w.u0):
                j = column(w)
                if j is not None:
                    out[j].append(w.text.strip())
            # A sign printed apart from its figure (`-` `20,34%`).
            return [re.sub(r"^-\s+(?=\d)", "-", gra.normalise_text(" ".join(t))) for t in out]
        if not lo <= min(b[0].vmid for b in bands[heads[0]:]) or hi is None:
            raise Refused(f"p{sp.pdf_page}: no rule across the table")
        for n, h in enumerate(heads):
            v_head = sum(w.vmid for w in bands[h]) / len(bands[h])
            # The section's title: the line of one capitalised word above the header.
            title = next((i for i in range(h - 1, -1, -1)
                          if SECTION.fullmatch(" ".join(w.text.strip() for w in bands[i]))), None)
            if title is not None:
                section = bands[title][0].text.strip()
            top = bands[title][0].vmid if title is not None else -float("inf")
            floor = min((v for v in across if v > v_head), default=None)
            if floor is None:
                raise Refused(f"p{sp.pdf_page}: header with no rule under it")
            head = [b for b in bands if top < b[0].vmid < floor]
            labels = [""] * (len(edges) - 1)
            for b in sorted(head, key=lambda b: b[0].vmid):
                for j, t in enumerate(cells_of(b)):
                    if t:
                        labels[j] = gra.normalise_text(f"{labels[j]} {t}")
            order, year_of = [], {}
            for j, label in enumerate(labels):
                if j == 0:
                    if fold(label, year) != fold(stub.labels[0], year):
                        raise Refused(f"printed {labels}, agreed {stub.labels}")
                    continue
                m = next(((m, i) for m in tops for i, x in enumerate(m.labels)
                          if fold(x, year) == fold(label, year)), None)
                if m is None:
                    raise Refused(f"printed {labels}, agreed {[c.labels for c in tops]}")
                k = re.search(r"Y(?:-(\d+))?$", m[0].labels[m[1]])
                order.append((j, m[0], int(year) - int(k.group(1) or 0)))
            result.labels, result.columns = labels, printed
            end = heads[n + 1] if n + 1 < len(heads) else len(bands)
            body = [b for b in bands[:end] if b[0].vmid > floor]
            if n + 1 < len(heads) and title is not None:
                nxt = next((i for i in range(heads[n + 1] - 1, h, -1)
                            if SECTION.fullmatch(" ".join(w.text.strip() for w in bands[i]))), None)
                if nxt is not None:
                    body = [b for b in body if b[0].vmid < bands[nxt][0].vmid]
            records, texts = [], []
            for b in body:
                cells = cells_of(b)
                v = sum(w.vmid for w in b) / len(b)
                if cells[0]:
                    texts.append((v, cells[0]))
                figures = [cells[j] for j, _m, _y in order if cells[j]]
                if not figures:
                    continue
                # The values decide, not the variations (`0,27%` on a count line).
                if any("," in cells[j] for j, m, _y in order if not m.prints_text):
                    records.append({"v": [v], "top": cells, "under": None})
                elif records and records[-1]["under"] is None:
                    records[-1]["under"] = cells
                    records[-1]["v"].append(v)
                else:
                    result.issues.append((sp.pdf_page, "outside", f"{' '.join(figures)!r}: no value above"))
            if not records:
                continue
            for v, t in texts:
                box = (max([x for x in across if x <= v], default=-float("inf")),
                       min([x for x in across if x > v], default=float("inf")))
                mine = [r for r in records if box[0] <= sum(r["v"]) / len(r["v"]) < box[1]]
                if not mine:
                    result.issues.append((sp.pdf_page, "outside", f"{t!r}: no record"))
                    continue
                rec = min(mine, key=lambda r: abs(sum(r["v"]) / len(r["v"]) - v))
                rec.setdefault("label", []).append(t)
            for rec in records:
                label = gra.normalise_text(" ".join(rec.get("label", [])))
                cells, row_order, year_of = [label], [stub], {}
                for j, m, y in order:
                    for col, src in ((m, rec["top"]), (below.get(m.key), rec["under"])):
                        if col is None:
                            continue
                        year_of[len(cells)] = y
                        cells.append(src[j] if src else "")
                        row_order.append(col)
                row_ord += 1
                kind = "total" if TOTAL_LEAD.match(label) else "data"
                result.rows.append({
                    "carried": set(), "pdf_page": sp.pdf_page, "printed_page": sp.printed_page,
                    "row_ord": row_ord, "kind": kind, "group": section, "cells": cells,
                    "order": row_order, "year_of": year_of,
                    "source_file": sp.doc.name.split("/")[-1]})
    return result


def _side_row(sp, row_ord, kind, lead, halves, rank, measures, years):
    cells, order, year_of = list(lead), [rank], {}
    for anno, half in zip(years, halves):
        for m, t in zip(measures, half[1:]):
            year_of[len(cells)] = anno
            cells.append(t)
            order.append(m)
    return {"carried": set(), "pdf_page": sp.pdf_page, "printed_page": sp.printed_page,
            "row_ord": row_ord, "kind": kind, "group": None, "cells": cells, "order": order,
            "year_of": year_of, "source_file": sp.doc.name.split("/")[-1]}


def from_colours(result, spec, pages):
    """A column read from the fill of each value's cell (`colour` in
    columns.tsv, MAE T15's `scadute`). The legend prints each label inside a
    swatch; a fill is the label whose swatch is nearest in colour (two shades
    of orange are one), within 0.15 a channel. A fill's row is the one whose
    label the first column prints between the same two rules across the
    table; its year, the column it stands in. The column is added to each data
    row, one cell per year (no fill: 0), `from the cell's colour` in its notes.
    A fill no swatch is near, or in no row, is an issue: nothing guessed."""
    for col in spec.colours:
        found = {}                                  # (pdf page, label, column) -> value
        for sp in pages:
            columns = next((r["columns"] for r in result.rows
                            if r["pdf_page"] == sp.pdf_page and r.get("columns")), None)
            if not columns:
                continue
            frame, words = page_words(sp)
            words = [w for w in words if w.text.strip()]
            boxes = geo.fills(sp, frame)
            legend = {}
            for band in geo.row_bands(words):
                text = gra.normalise_text(" ".join(w.text for w in sorted(band, key=lambda w: w.u0)))
                if text in col.colour:
                    u = sum((w.u0 + w.u1) / 2 for w in band) / len(band)
                    v = sum(w.vmid for w in band) / len(band)
                    legend[text] = next((b for b in boxes if b[0] <= u <= b[2] and b[1] <= v <= b[3]),
                                        None)
            if None in legend.values() or set(legend) != set(col.colour):
                result.issues.append((sp.pdf_page, "colour", f"legend {sorted(legend)}: "
                                      f"agreed {sorted(col.colour)}, each with its swatch"))
                continue
            years = {i for r in result.rows if r["pdf_page"] == sp.pdf_page
                     for i, c in enumerate(r["order"]) if c.type == "years"}
            width = columns[-1][1] - columns[0][0]
            _walls, levels = _rules(sp, frame)
            across = sorted(v for v, a, b in levels if b - a >= 0.9 * width)
            for b in boxes:
                u, v = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                j = next((j for j, (a, z) in enumerate(columns) if a <= u <= z), None)
                if j not in years or not across or not across[0] < v < across[-1]:
                    continue                        # the header's, the legend's: no value's
                lo = max(x for x in across if x < v)
                hi = min(x for x in across if x > v)
                label = gra.normalise_text(" ".join(
                    w.text for w in sorted(words, key=lambda w: (round(w.vmid), w.u0))
                    if lo < w.vmid < hi and columns[0][0] <= (w.u0 + w.u1) / 2 <= columns[0][1]))
                near = min(legend, key=lambda k: _distance(legend[k][4], b[4]))
                if _distance(legend[near][4], b[4]) > 0.15:
                    result.issues.append((sp.pdf_page, "colour",
                                          f"{label} column {j}: fill {b[4]} no legend names"))
                    continue
                found[(sp.pdf_page, label, j)] = near
        used = set()
        for row in result.rows:
            if row["kind"] != "data" or "columns" not in row:
                continue
            cells, order, year_of = list(row["cells"]), list(row["order"]), dict(row["year_of"])
            for i, c in enumerate(row["order"]):
                if c.type != "years" or i not in row["year_of"]:
                    continue
                where = (row["pdf_page"], row["cells"][0], i)
                meant = col.colour[found[where]] if where in found else None
                used.add(where)
                value = ("0" if meant is None else row["cells"][i]
                         if meant in {x.key for x in spec.columns} else meant)
                year_of[len(cells)] = row["year_of"][i]
                cells.append(value)
                order.append(col)
            row["cells"], row["order"], row["year_of"] = cells, order, year_of
            row.setdefault("notes", []).append(f"{col.key}: from the cell's colour")
        for page, label, j in sorted(set(found) - used):
            result.issues.append((page, "colour", f"{label!r} column {j}: a fill in no row"))


def _distance(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def stacked_up(result, stacked):
    """A stacked cell's second value printed on the line under the first
    (MAE N1: the licence's date beside its next movement) goes up to the row
    printing the first, when that row has none."""
    if not stacked:
        return
    a, b = stacked
    opener = None
    for r in result.rows:
        if r["kind"] != "data":
            continue
        if r["cells"][a]:
            opener = r
        elif r["cells"][b] and opener is not None and not opener["cells"][b]:
            opener["cells"][b], r["cells"][b] = r["cells"][b], ""


def summaries_under(result, spec):
    """A summary box printed under a value column, its agreed label
    (`totals.tsv`) over its figure (Difesa 3A: `TOTALE AUTORIZZATO NEL 2025
    ...` over 1.651.658.093,82 €). Its label, on several lines, is read into
    the last row's cell: cut off there; the row printing figures only under
    those labels is the summaries', not a record."""
    rows = [r for r in result.rows if r["kind"] == "data"]
    for r, nxt in zip(rows, rows[1:]):
        pending = {}
        for i, (t, col) in enumerate(zip(r["cells"], r["order"])):
            if not t or col.type not in ("amount", "number"):
                continue
            m = next((m for rx, _e in spec.totals
                      if (m := re.search(rx.pattern.lstrip("^"), t))), None)
            if m:
                pending[i] = t[m.start():]
        filled = {i for i, t in enumerate(nxt["cells"]) if t}
        if not pending or not filled or not filled <= set(pending):
            continue
        for i, label in pending.items():
            r["cells"][i] = r["cells"][i][:len(r["cells"][i]) - len(label)].strip()
        for i in sorted(filled):
            result.totals.append((nxt["pdf_page"], nxt.get("printed_page"), pending[i], nxt["cells"][i]))
        result.rows.remove(nxt)


def set_units(result, spec):
    """Each `riga_<unit>` named by an opener column (no `link`): the row_ord of
    the last row printing that column (not carried down into it), or opening
    a unit listed before it, or a new group (DECISIONS.md 2026-10-25)."""
    cols = [c for c in spec.units if c.of]
    opened, group = {}, object()
    for r in result.rows:
        if r["kind"] != "data":
            continue
        printed = {col.key for i, (t, col) in enumerate(zip(r["cells"], r["order"]))
                   if t and i not in r.get("carried", ())}
        outer = r["group"] != group
        group = r["group"]
        for c in cols:
            if outer or c.of in printed or c.key not in opened:
                opened[c.key] = r["row_ord"]
                outer = True
            r.setdefault("units", {})[c.key] = opened[c.key]


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
                # Above the list: its caption before the first item, furniture after.
                if row_ord == 0 and not RUNNING_HEAD.search(text) and not STAMP.search(text):
                    result.caption.append(" ".join(text.split()))
                continue
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
