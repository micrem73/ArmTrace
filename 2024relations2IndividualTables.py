#!/usr/bin/env python3
"""
2024relations2IndividualTables — split a Law 185/1990 annual report PDF into
one PDF per table.

    Reports/<anno>/<relazione>.pdf  ->  Out/PDF/<tabella>/<anno>/<tabella>.pdf

The year is read from the ``Reports/<anno>/`` path, so the same script handles
every reporting year.

Usage:
    python 2024relations2IndividualTables.py --year 2024 --volume both
    python 2024relations2IndividualTables.py --year 2024 --volume 2
    python 2024relations2IndividualTables.py --report Reports/2024/relazione.pdf

Requires: pypdf  (pip install -r requirements.txt)
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPORTS_DIR = "Reports"
OUT_DIR = "Out"


def report_year(report_path):
    """Estrae l'anno di RIFERIMENTO della relazione dal percorso Reports/<anno>/..."""
    m = re.search(rf"{REPORTS_DIR}[/\\](\d{{4}})", str(report_path))
    if not m:
        raise ValueError(
            f"Anno non trovato in '{report_path}': il percorso deve essere "
            f"'{REPORTS_DIR}/<anno>/<file>.pdf'"
        )
    return m.group(1)


def out_dir(kind, table_name, year, base=""):
    """Restituisce (creandola se serve) la cartella Out/<PDF|XLS>/<tabella>/<anno>."""
    path = Path(base, OUT_DIR, kind, table_name, year)
    path.mkdir(parents=True, exist_ok=True)
    return path


def dividi_pdf_per_tabelle(input_pdf, base=""):
    """Volume 1: le tabelle sono indicate come 'TAB A1', 'TAB B2', ..."""
    year = report_year(input_pdf)
    pdf_out = Path(base, OUT_DIR, "PDF") / "<tabella>" / "<anno>"

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        sys.exit("Errore: pypdf non installato. Esegui: pip install -r requirements.txt")

    reader = PdfReader(input_pdf)

    # 1. TROVARE LA PAGINA DI INDICE (quella con la colonna "Report")
    # L'utente indica pagina 66, cerchiamo nei dintorni dell'indice 65-75 per sicurezza
    toc_content = ""
    toc_page_index = -1

    print("Ricerca della pagina indice (Report delle tabelle)...")
    for i in range(60, 80):  # Range flessibile intorno a pag 66
        try:
            page_text = reader.pages[i].extract_text()
            # Cerchiamo un pattern tipico dell'indice: "TAB A1" e "TAB B1" insieme
            if "TAB A1" in page_text and "TAB B1" in page_text:
                toc_page_index = i
                toc_content = page_text
                print(f"Indice trovato a pagina indice {i} (Pagina stampata probabile: {i+1})")
                break
        except:
            continue

    if toc_page_index == -1:
        print("Errore: Impossibile trovare la pagina indice con l'elenco delle tabelle.")
        return

    # 2. ESTRARRE I NOMI DELLE TABELLE DALLA COLONNA "REPORT"
    # Cerchiamo stringhe come "TAB A1", "TAB B2", "TAB 8.1"
    # Il pattern cerca "TAB" seguito da spazio e caratteri alfanumerici (es. A1, 8.1, B2)
    tabelle_target = re.findall(r"(TAB\s+[A-Z0-9\.]+)", toc_content)

    # Rimuoviamo duplicati mantenendo l'ordine
    tabelle_target = list(dict.fromkeys(tabelle_target))
    print(f"Tabelle identificate nell'indice: {tabelle_target}")

    # 3. MAPPARE LE PAGINE DEL PDF ALLE TABELLE
    # Creiamo un dizionario: "TAB A1" -> [lista di indici pagina]
    mappa_pagine = {tab: [] for tab in tabelle_target}

    print("Scansione del documento per identificare le pagine...")
    for i, page in enumerate(reader.pages):
        # Saltiamo la pagina dell'indice stesso per evitare falsi positivi
        if i == toc_page_index:
            continue

        text = page.extract_text()
        if not text:
            continue

        # Controlliamo quale tabella è citata nella pagina (header/footer)
        for tab in tabelle_target:
            # Verifica se il codice tabella (es. "TAB A1") è presente nel testo della pagina
            if tab in text:
                mappa_pagine[tab].append(i)
                break  # Assumiamo che una pagina appartenga a una sola tabella

    # 4. CREARE I FILE PDF SEPARATI
    print("Creazione dei file separati...")
    for tab, pagine in mappa_pagine.items():
        if not pagine:
            continue

        writer = PdfWriter()
        for p_idx in pagine:
            writer.add_page(reader.pages[p_idx])

        # Pulisci il nome file (sostituisci spazi con underscore)
        nome_file = tab.replace(" ", "_").replace(".", "-") + ".pdf"
        table_name = os.path.splitext(nome_file)[0]
        output_path = out_dir("PDF", table_name, year, base) / nome_file

        with open(output_path, "wb") as f:
            writer.write(f)

        print(f"Salvato: {nome_file} ({len(pagine)} pagine)")

    print(f"\nOperazione completata. I file sono in '{pdf_out}/'.")


def estrai_tabelle_volume2(input_filename, base=""):
    """Volume 2: le tabelle sono indicate come 'Tabella AA', 'Tabella N1', ..."""
    print(f"File Input: {input_filename}")
    year = report_year(input_filename)
    pdf_out = Path(base, OUT_DIR, "PDF") / "<tabella>" / "<anno>"
    print(f"Cartella Output: {pdf_out}/")

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        sys.exit("Errore: pypdf non installato. Esegui: pip install -r requirements.txt")

    try:
        reader = PdfReader(input_filename)
    except FileNotFoundError:
        print("Errore: Il file PDF non è stato trovato.")
        return

    total_pages = len(reader.pages)

    # ---------------------------------------------------------
    # 1. TROVARE LA PAGINA "ELENCO TABELLE SEGNALAZIONI"
    # ---------------------------------------------------------
    print("Ricerca dell'Indice 'ELENCO TABELLE SEGNALAZIONI'...")

    index_text = ""
    found_index = False

# Fase A: Trova la prima pagina dell'indice
    for i in range(total_pages):
        text = reader.pages[i].extract_text()
        if "ELENCO TABELLE SEGNALAZIONI" in text:
            start_index_page = i
            found_index = True
            print(f" -> Inizio indice trovato a pagina {i+1}")
            break

    # Fase B: Leggi le pagine consecutive dell'indice
    if found_index:
        current_page = start_index_page
        while current_page < total_pages:
            text = reader.pages[current_page].extract_text()

            # Controlliamo se siamo ancora nell'indice
            # (L'intestazione dovrebbe ripetersi su ogni pagina dell'elenco)
            if "ELENCO TABELLE SEGNALAZIONI" in text:
                index_text += "\n" + text
                print(f"    -> Letta pagina indice {current_page+1}")
                current_page += 1
            else:
                # Fine dell'indice
                break
    else:
        print("Errore: Impossibile trovare la pagina 'ELENCO TABELLE SEGNALAZIONI'.")
        return


    # ---------------------------------------------------------
    # 2. ESTRARRE I NOMI DELLE TABELLE DALL'INDICE
    # ---------------------------------------------------------
    # Nel Volume 2 il formato sembra essere "TAB. XX" (con il punto) o simile.
    # Regex: cerca "TAB" opzionalmente seguito da punto, spazi e codice alfanumerico
    pattern_tabelle = r"(Tabella[\.]?\s+[A-Z0-9]+)"

    tabelle_target = re.findall(pattern_tabelle, index_text)

    # Rimuovi duplicati mantenendo l'ordine e pulisci
    tabelle_target = list(dict.fromkeys(tabelle_target))

    if not tabelle_target:
        print("Nessuna tabella trovata nell'indice. Verificare il pattern regex.")
        # Fallback: prova un pattern più generico se il primo fallisce
        tabelle_target = re.findall(r"(TABELLA\s+[A-Z0-9]+)", index_text)

    print(f"Tabelle identificate ({len(tabelle_target)}): {tabelle_target}")

    # ---------------------------------------------------------
    # 3. SCANSIONE E DIVISIONE FILE
    # ---------------------------------------------------------
    mappa_pagine = {code: [] for code in tabelle_target}

    print("Scansione pagine documento...")
    for i in range(total_pages):
        page = reader.pages[i]
        text = page.extract_text()
        if not text: continue

        # 1. Normalizziamo il testo: trasformiamo i ritorni a capo in spazi
        # e rimuoviamo spazi doppi. Questo risolve il problema "Tabella\nII"
        text_clean = " ".join(text.split())

        if "ELENCO TABELLE" in text_clean:
            for code in tabelle_target:
                # 2. Regex migliorata:
                # Cerca "Tabella" seguito da uno o più spazi e dal codice.
                # (?![A-Z0-9]) assicura che dopo il codice non ci siano altre lettere o numeri
                # (così "II" non matcha "II1"), ma permette punteggiatura o spazi.
                pattern = rf"Tabella\s+{re.escape(code)}(?![A-Z0-9])"

                if re.search(pattern, text_clean):
                    mappa_pagine[code].append(page)
                    # Una volta trovata la tabella per questa pagina, passiamo alla prossima
                    break


    # ---------------------------------------------------------
    # 4. SALVATAGGIO PDF
    # ---------------------------------------------------------
    print("Salvataggio file in corso...")
    count_saved = 0

    for tab, pagine in mappa_pagine.items():
        if pagine:
            writer = PdfWriter()
            for p in pagine:
                writer.add_page(p)

            # Pulisci il nome file (rimuovi punti e spazi per il filesystem)
            clean_name = tab.replace(".", "").replace(" ", "_") + ".pdf"
            table_name = os.path.splitext(clean_name)[0]
            file_path = out_dir("PDF", table_name, year, base) / clean_name

            with open(file_path, "wb") as f:
                writer.write(f)

            print(f" -> Salvato: {clean_name} ({len(pagine)} pagine)")
            count_saved += 1
        else:
            print(f" -> Attenzione: Nessuna pagina trovata per {tab}")

    print(f"\nOperazione completata. {count_saved} tabelle estratte.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def detect_year(reports_dir=REPORTS_DIR):
    """Se Reports/ contiene un solo anno, usa quello."""
    years = sorted(
        d.name for d in Path(reports_dir).glob("*") if d.is_dir() and d.name.isdigit()
    )
    if len(years) == 1:
        return years[0]
    if not years:
        return None
    raise SystemExit(
        f"Più anni presenti in {reports_dir}/ ({', '.join(years)}): specifica --year"
    )


def find_report(year, volume, reports_dir=REPORTS_DIR):
    """Trova il PDF del volume richiesto dentro Reports/<anno>/."""
    folder = Path(reports_dir, year)
    if not folder.is_dir():
        raise SystemExit(f"Cartella non trovata: {folder}")

    # "volume 1_442452" -> dopo il numero c'è '_', che è un word char:
    # usiamo un lookahead numerico invece di \b
    matches = [
        p for p in sorted(folder.glob("*.pdf"))
        if re.search(rf"volume[\s_-]*{volume}(?![0-9])", p.stem, re.IGNORECASE)
    ]
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Estrae una tabella per PDF dalla relazione L.185/1990.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--year", help="anno di riferimento, es. 2024")
    parser.add_argument(
        "--volume", default="2", choices=["1", "2", "both"],
        help="quale volume processare (default: 2)",
    )
    parser.add_argument(
        "--report", action="append", default=[],
        help="percorso esplicito del PDF (ripetibile con --volume both)",
    )
    parser.add_argument("--base", default="", help="radice dei percorsi (default: .)")
    args = parser.parse_args(argv)

    jobs = []
    if args.report:
        for report in args.report:
            jobs.append((report, detect_year_from_path(report)))
    else:
        year = args.year or detect_year(args.base or REPORTS_DIR)
        if not year:
            raise SystemExit(
                f"Nessun anno trovato in {args.base or REPORTS_DIR}/: usa --year"
            )
        volumes = ["1", "2"] if args.volume == "both" else [args.volume]
        for volume in volumes:
            jobs.append((str(find_report(year, volume, Path(args.base, REPORTS_DIR))), year))

    for report, _year in jobs:
        volume = "2" if re.search(r"volume[\s_-]*2(?![0-9])", Path(report).stem, re.I) else "1"
        print("=" * 70)
        print(f"Volume {volume} — {report}")
        print("=" * 70)
        if volume == "2":
            estrai_tabelle_volume2(report, base=args.base)
        else:
            dividi_pdf_per_tabelle(report, base=args.base)


def detect_year_from_path(path):
    """Anno per --report: letto dal percorso, con fallback su --year."""
    try:
        return report_year(path)
    except ValueError:
        return "?"


if __name__ == "__main__":
    main()