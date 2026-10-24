"""Finding the source PDFs and putting them in order.

Split out of Reports2PDFTables.py on 2026-10-11. Filenames are the only thing read here,
and their shape is stable across the whole archive -- 2024 arabic, 2019 roman, 2021 TOMO
standing in for VOLUME, 2001 DOCUMENTO_UNICO."""


import re
from pathlib import Path

from lib.patterns import FIRST_YEAR, REPORTS_DIR, ROMAN


def parse_volume(stem):
    """Volume number from a report filename.

        lxvii_3_volume 1_442452.pdf        -> 1   (2024, arabic)
        2019_LXVII_n3_VOLUME_I.pdf         -> 1   (roman)
        2021_LXVII_n5_TOMO_II.pdf          -> 2   (TOMO as VOLUME)
        2001_LXVII_n1_DOCUMENTO_UNICO.pdf  -> 1   (single volume)
    """
    m = re.search(r"(?:volume|tomo)[\s_-]*([IVX]+|\d+)", stem, re.IGNORECASE)
    if m:
        token = m.group(1).upper()
        return int(token) if token.isdigit() else ROMAN.get(token)
    if re.search(r"documento[\s_-]*unico", stem, re.IGNORECASE):
        return 1
    return None



def report_year(report_path):
    """Reporting year from the In/<anno>/ path."""
    m = re.search(rf"{REPORTS_DIR}[/\\](\d{{4}})", str(report_path))
    if not m:
        raise ValueError(
            f"Anno non trovato in '{report_path}': il percorso deve essere "
            f"'{REPORTS_DIR}/<anno>/<file>.pdf'"
        )
    return m.group(1)



def detect_year(reports_dir=REPORTS_DIR):
    """If the folder contains exactly one year, use it."""
    years = sorted(
        d.name for d in Path(reports_dir).glob("*")
        if d.is_dir() and d.name.isdigit()
    )
    if len(years) == 1:
        return years[0]
    if not years:
        return None
    raise SystemExit(
        f"Più anni presenti in {reports_dir}/ ({', '.join(years)}): specifica --year"
    )



def find_report(year, volume, reports_dir=REPORTS_DIR):
    """The PDF for the requested volume inside In/<anno>/."""
    folder = Path(reports_dir, year)
    if not folder.is_dir():
        raise SystemExit(f"Cartella non trovata: {folder}")
    wanted = int(volume)
    matches = [p for p in sorted(folder.glob("*.pdf"))
               if parse_volume(p.stem) == wanted]
    if not matches:
        available = ", ".join(p.name for p in sorted(folder.glob("*.pdf"))) or "(nessun PDF)"
        raise SystemExit(
            f"Nessun PDF del volume {volume} in {folder}.\nTrovati: {available}"
        )
    if len(matches) > 1:
        raise SystemExit(
            f"Ambiguo: più PDF del volume {volume} in {folder}: "
            + ", ".join(p.name for p in matches)
            + "\nUsa --report per specificare il file."
        )
    return matches[0]



def report_at(year, number, reports_dir=REPORTS_DIR):
    """The source PDF that V<number> names: the number-th file of the year in
    reading order (group_by_year), not the volume its filename claims. 2021's
    TOMO_II and VOLUME_II both say volume 2; they are V2 and V3."""
    folder = Path(reports_dir, str(year))
    reports = sorted((p for p in folder.glob("*.pdf") if parse_volume(p.stem)),
                     key=lambda p: (parse_volume(p.stem), str(p)))
    if not 1 <= int(number) <= len(reports):
        raise SystemExit(f"Nessun V{number} in {folder}: "
                         f"{', '.join(p.name for p in reports) or '(nessun PDF)'}")
    return reports[int(number) - 1]



def collect_jobs(args):
    """Resolve the CLI flags to a list of (report_path, year) jobs."""
    reports_dir = Path(args.base, REPORTS_DIR)
    if args.report:
        return [(r, year_from_path(r)) for r in args.report]
    if args.all:
        jobs = []
        for folder in sorted(reports_dir.glob("*")):
            if not folder.is_dir() or not folder.name.isdigit():
                continue
            if int(folder.name) < FIRST_YEAR:
                continue
            for pdf in sorted(folder.glob("*.pdf")):
                jobs.append((str(pdf), folder.name))
        return jobs

    year = args.year or detect_year(str(reports_dir))
    if not year:
        raise SystemExit(
            f"Nessun anno trovato in {reports_dir}/: usa --year"
        )
    if args.volume == "all":             # every volume in the folder (2023: three)
        # Every file, not every volume number: 2021's TOMO_II (vol. I, tomo II)
        # and VOLUME_II both parse as 2. Reading order as in group_by_year.
        folder = Path(reports_dir, year)
        reports = [str(p) for p in folder.glob("*.pdf") if parse_volume(p.stem)]
        return [(r, year) for r in
                sorted(reports, key=lambda p: (parse_volume(Path(p).stem), p))]
    else:
        volumes = ["1", "2"] if args.volume == "both" else [args.volume]
    return [(str(find_report(year, v, reports_dir)), year)
            for v in volumes]



def year_from_path(path):
    try:
        return report_year(path)
    except ValueError:
        return "?"



def group_by_year(jobs):
    """Regroup (report, year) jobs into {year: [report, ...]}, in volume order.

    The volumes of a year have to meet each other before any of them is
    written, because a table may run from one into the next. Order within the
    year is by parsed volume number, with the filename as the tie-break: that
    is what makes 2021 TOMO I -> TOMO II -> VOLUME II come out in reading
    order despite the colliding volume-2 names.

    A path that yields no year at all still gets a group, so those volumes are
    processed but never stitched: `main` refuses to join a boundary whose two
    halves cannot be attributed to the same year.
    """
    grouped = {}
    for report, year in jobs:
        grouped.setdefault(year, []).append(report)
    for reports in grouped.values():
        reports.sort(key=lambda p: (parse_volume(Path(p).stem) or 0, p))
    return grouped



def sibling_index(report_path, base=""):
    """The volume of the same year that carries the INDICE, or None.

    The index prints once, in the first volume, and lists the page ranges of the
    whole document including the later volumes -- confirmed for 2016, 2018, 2020,
    2022, 2023 and 2025, where volume I and volume II report identical ranges.
    So a volume without its own index borrows its first sibling's.
    """
    folder = Path(report_path).parent
    if parse_volume(Path(report_path).stem) == 1:
        return None                      # already the first volume
    candidates = [p for p in sorted(folder.glob("*.pdf"))
                  if parse_volume(p.stem) == 1]
    if len(candidates) == 1:
        return str(candidates[0])
    # Two files claim volume 1 (2021 has both TOMO_I and VOLUME_I); prefer the
    # one that actually parses, and let validation decide.
    return str(candidates[0]) if candidates else None
