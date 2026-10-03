#!/usr/bin/env python3
"""
IndividualTables2SQL — turn the per-table PDFs produced by
Reports2PDFTables into CSV files (and, later, SQL).

    Out/PDF/<tabella>/<anno>/<tabella>.pdf  ->  Out/CSV/<tabella>/<anno>/<tabella>.csv

Why CSV and not XLSX: the archive holds tables Excel cannot represent. One
volume yields up to 65 tables (2025 vol. II), and tabula fragments a borderless
one into thousands of pieces. Excel caps a workbook at 255 sheets, a sheet at
1048576 rows and a sheet at 16384 columns, and past any of those it refuses to
open the file at all. CSV has no such cap: one table is one file of any length,
it loads in full into pandas, MySQL or sqlite without a driver having to
understand a spreadsheet, and its text is diffable in git even though the .csv
itself is not tracked.

The table code is the filename stem. Three code schemes exist in the archive:

    family 1  art. 27 double-letter  AA AA1 BB ... UE, MG1-MG9, MT1, MT7,
              GF, NN, OO, PP
    family 2  A1 .. P2                the 31 MAE detail tables
    family 3  art. 27 single-letter  A B D E G J Q  (2012 vol. I)

Usage:
    python IndividualTables2SQL.py --year 2023
    python IndividualTables2SQL.py --year 2023 --input-dir Out/PDF --output-root Out/CSV
    python IndividualTables2SQL.py --year 2023 --base /path/to/project
    python IndividualTables2SQL.py --year 2023 --sep , --encoding utf-8

Requires: tabula-py, pandas, pypdf  (pip install -r requirements.txt)
tabula-py shells out to Java, so a JRE must be available on PATH or via JAVA_HOME.

Known limitation: about 1300 pages across the archive come from fonts whose
character mapping was destroyed at PDF generation time. Those tables split
correctly but extract nothing here; text_status() says so per file. OCR is
the only route and is out of scope here.
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    sys.exit("Errore: pandas non installato. Esegui: pip install -r requirements.txt")

try:
    import tabula
except ImportError:
    sys.exit("Errore: tabula-py non installato. Esegui: pip install -r requirements.txt")

import re

# Percorsi del progetto: Out/PDF/<tabella>/<anno>/ -> Out/CSV/<tabella>/<anno>/
OUT_DIR = "Out"

# Anno di riferimento predefinito (corrisponde alla cartella
# reports_185_1990/<anno>/ da cui derivano i PDF in Out/PDF/<anno>/)
YEAR = "2024"

# Radice dei percorsi, impostabile da CLI con --base
BASE = ""

# I 31 codici della famiglia 2, confermati dagli alberi di segnalibri di
# 2021 tom. I, 2023 vol. I e 2025 vol. I, che concordano su ogni codice.
FAMILY2_CODES = {
    "A1", "A2", "A3", "A4",
    "B1", "B2", "B3", "B4", "B5", "B6", "B7",
    "C1", "C2", "D", "E",
    "F1", "F2", "G1", "G2", "H1", "H2", "I", "L",
    "M1", "M2", "N1", "N2", "O1", "O2", "P1", "P2",
}

# Estensione dei file prodotti. CSV al posto di XLSX: vedi il docstring.
CSV_SUFFIX = ".csv"

# Separatore di campo. Il punto e' e' la scelta, non un default arbitrario:
# le cifre Italiane usano la virgola decimale, quindi un CSV separato da
# virgole fa leggere "1.234,56" come due colonne. Il ';' e' cio' che Excel
# stesso usa con le impostazioni regionali italiane e quello che pandas
# riesce a tipizzare come numeri senza un converter. Per MySQL:
# LOAD DATA INFILE ... FIELDS TERMINATED BY ';'
CSV_SEPARATOR = ";"

# utf-8-sig scrive il BOM che serve a Excel su Windows per non leggere le
# lettere accentate dei nomi ("Paese", "Movimentazioni", "Valore in euro")
# come spazzatura. Il BOM torna anche nella prima intestazione per chi legge
# con encoding='utf-8' esplicito: passare --encoding utf-8 se la destinazione
# e' un database e il BOM non e' desiderato.
CSV_ENCODING = "utf-8-sig"


class PDFTableExtractor:
    """Extract tables from PDF files based on their format"""

    def __init__(self, input_dir=None, output_root=None, year=YEAR, base=BASE,
                 separator=CSV_SEPARATOR, encoding=CSV_ENCODING):
        # Default: Out/PDF/<tabella>/<anno>/*.pdf  ->  Out/CSV/<tabella>/<anno>/*.csv
        self.input_dir = Path(input_dir) if input_dir else Path(base, OUT_DIR, "PDF")
        self.output_root = Path(output_root) if output_root else Path(base, OUT_DIR, "CSV")
        self.year = str(year)
        self.separator = separator
        self.encoding = encoding
        self.output_root.mkdir(parents=True, exist_ok=True)

    def detect_pdf_type(self, pdf_path):
        """Classify a per-table PDF by its filename.

        Reports2PDFTables writes Out/PDF/<code>/<anno>/<code>.pdf, so the
        stem IS the table code. The three code schemes in the archive are:

            family 1  art. 27 double-letter  AA AA1 BB ... UE  MG1 MG3 MT7
            family 2  A1 .. P2                31 codes
            family 3  art. 27 single-letter  A B D E G J Q

        The previous mapping keyed on TAB_N1, TAB_O1, TAB_A2 ... -- names
        that appear nowhere in the 43 reports. Every table fell through to
        the unknown branch, which wrote one sheet per extracted fragment and
        was the cause of the ~4000-sheet hang recorded in the README.
        """
        return Path(pdf_path).stem.upper()

    def table_semantics(self, pdf_type):
        """Describe a code in terms a reader can act on, for the log.

        Returns (family, human label) or (None, None) when the code is not
        recognised. The family matters downstream: families 1 and 3 are MEF
        summary tables with a fixed column layout, family 2 is the MAE
        per-operator/per-country detail.
        """
        code = pdf_type.upper()
        if re.fullmatch(r"[A-Z]{2}\d?", code):
            if code in ("MG1", "MG2", "MG3", "MG4", "MG5", "MG6", "MG7",
                        "MG8", "MG9"):
                return 1, "MAE licenze globali di progetto"
            if code in ("MT1", "MT7"):
                return 1, "MAE licenze globali di trasferimento"
            if code == "LGP":
                return 1, "licenze globali di programma di cooperazione"
            if code in ("NN", "OO", "PP", "GF"):
                return 1, "grafico ripartizione percentuale"
            if code == "UE":
                return 1, "importazioni intra UE"
            if re.fullmatch(r"[A-Z]{2}", code):
                return 1, "art. 27 riepilogo per istituti di credito"
            return 1, "art. 27 riepilogo dettagliato"
        if code in FAMILY2_CODES:
            return 2, "MAE dettaglio operatore / paese"
        if re.fullmatch(r"[A-Z]\d?", code):
            return 3, "art. 27 riepilogo"
        return None, None

    def text_status(self, pdf_path):
        """Report whether the text layer of a per-table PDF is readable.

        Two corruption classes exist in the archive, both confirmed
        unrecoverable (see Reports2PDFTables.py for the evidence):

          garbled   subset fonts with no ToUnicode CMap; the subsetter wrote
                    the Private Use codepoint into the glyph name
                    ("uniE019"), so only outlines remain
          ciphered  /Identity-H fonts with no ToUnicode and no
                    /Differences; text is a substituted alphabet
                    ("/LFHQ]D 2SHUDWRUH") with no mapping table anywhere

        Affected: 2023 vol. I 284pp, 2025 vol. I 356pp, 2018 vol. II 299pp,
        2025 vol. II 141pp (garbled); 2017 vol. I 287pp, 2022 vol. I 66pp
        (ciphered). These tables split correctly but cannot yield data.
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            return "pypdf non disponibile"
        pua = re.compile(r"[\ue000-\uf8ff]")
        try:
            reader = PdfReader(pdf_path, strict=False)
        except Exception as exc:
            return f"lettura fallita: {exc}"
        pages = 0
        garbled = 0
        for page in reader.pages:
            pages += 1
            try:
                text = page.extract_text() or ""
            except Exception:
                garbled += 1
                continue
            legible = sum(1 for c in text if c.isalnum())
            if not text or len(pua.findall(text)) > 0.15 * max(1, legible):
                garbled += 1
        if garbled == pages:
            return (f"testo illeggibile in tutte le {pages} pagine "
                    f"(font senza ToUnicode: serve OCR)")
        if garbled:
            return f"{garbled}/{pages} pagine con testo illeggibile"
        return f"testo leggibile in tutte le {pages} pagine"

    def extract_with_tabula(self, pdf_path, method='lattice'):
        """Extract tables using tabula-py"""
        try:
            if method == 'lattice':
                tables = tabula.read_pdf(
                    pdf_path,
                    pages='all',
                    multiple_tables=True,
                    lattice=True,
                    pandas_options={'header': 'infer'}
                )
            else:  # stream
                tables = tabula.read_pdf(
                    pdf_path,
                    pages='all',
                    multiple_tables=True,
                    stream=True,
                    guess=True,
                    pandas_options={'header': 'infer'}
                )
            return tables
        except Exception as e:
            print(f"    Error with {method} method: {e}")
            return []

    def clean_dataframe(self, df):
        """Clean extracted dataframe"""
        # Remove completely empty rows and columns
        df = df.dropna(how='all', axis=0)
        df = df.dropna(how='all', axis=1)

        # Remove rows where all values are whitespace
        df = df[~df.apply(lambda x: x.astype(str).str.strip().eq('').all(), axis=1)]

        # Strip whitespace from string columns
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.strip()

        return df

    def process_tab_n1(self, tables):
        """Process TAB_N1 format - Global Project Licenses by Operator"""
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_n2(self, tables):
        """Process TAB_N2 format - Global Project Licenses by Country"""
        # Expected columns: Paese, Movimentazioni, Valore in €, Valore fini doganali in €, Spedizioni nr.
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_o1(self, tables):
        """Process TAB_O1 format - Global Transfer Licenses by Operator"""
        # Expected columns: Operatore, Paese, Movimentazioni, Valore in €, Valore fini doganali in €, Spedizioni nr.
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_o2(self, tables):
        """Process TAB_O2 format - Global Transfer Licenses by Country"""
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_a2(self, tables):
        """Process TAB_A2 format - Definitive Exports by Operator"""
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_a4(self, tables):
        """Process TAB_A4 format - Definitive Exports for Cooperation Programs"""
        all_dfs = []
        for table in tables:
            cleaned = self.clean_dataframe(table)
            if len(cleaned) > 0:
                all_dfs.append(cleaned)

        if len(all_dfs) == 0:
            return pd.DataFrame()
        elif len(all_dfs) == 1:
            return all_dfs[0]
        else:
            return all_dfs

    def process_tab_b6(self, tables):
        """Process TAB_B6 format - Definitive Exports by Country (Cooperation Programs)"""
        # Expected columns: Paese di Destinazione, n. Aut., Valore (EURO), Prog. Cooperazione, Cat. - Descrizione
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_b7(self, tables):
        """Process TAB_B7 format - Definitive Exports by Country (No Cooperation Programs)"""
        # Expected columns: Paese di Destinazione, n. Aut., Valore (EURO), Cat., Descrizione Categoria Materiali
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_m1(self, tables):
        """Process TAB_M1 format - Intermediations by Operator (Detail)"""
        # Expected columns: n., Operatore, Valore in euro, Categoria Materiale
        # This format has detailed operations per operator
        all_dfs = []
        for table in tables:
            cleaned = self.clean_dataframe(table)
            if len(cleaned) > 0:
                all_dfs.append(cleaned)

        if len(all_dfs) == 0:
            return pd.DataFrame()
        elif len(all_dfs) == 1:
            return all_dfs[0]
        else:
            return all_dfs

    def process_tab_m2(self, tables):
        """Process TAB_M2 format - Intermediations by Country"""
        # Expected columns: N., Paese, Num., Valore in euro
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_p1(self, tables):
        """Process TAB_P1 format - General Transfer Authorizations by Operator"""
        # Expected columns: Tipologia, Operatore, Movimentazioni, Paese, Valore in €, Valore fini doganali in €, Spedizione nr.
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def process_tab_p2(self, tables):
        """Process TAB_P2 format - General Transfer Authorizations by Country"""
        # Expected columns: Tipologia, Movimentazioni, Paese, Valore in €, Valore fini doganali in €, Spedizioni nr.
        if len(tables) == 1:
            df = tables[0]
        else:
            df = max(tables, key=lambda x: len(x))

        df = self.clean_dataframe(df)
        return df

    def save_to_csv(self, data, output_path, pdf_type):
        """Save extracted data to a single CSV file, one file per table.

        A single PDF can yield thousands of fragments when tabula's lattice
        method misreads a borderless table. Writing one sheet per fragment
        produced ~4000-sheet workbooks, past Excel's 255-sheet limit, and was
        the hang recorded in the README. Fragments are concatenated into a
        single CSV here, which removes both that ceiling and Excel's
        1048576-row one: there is no row count this has to refuse, so a long
        table is written whole rather than dropped with a warning.

        Concatenating fragments that disagree on column count is deliberate:
        pd.concat fills the gaps with NaN, so a long table whose header row is
        only detected on the first page keeps every data row instead of losing
        all but the first fragment.

        lineterminator is pinned to LF rather than left to os.linesep so the
        same input yields byte-identical output on Linux and Windows; Excel
        opens LF-terminated CSV either way.
        """
        frames = data if isinstance(data, list) else [data]
        frames = [f for f in frames if f is not None and len(f) > 0]

        if not frames:
            print("    - nessun dato: file non scritto")
            return 0

        if len(frames) == 1:
            combined = frames[0]
        else:
            combined = pd.concat(frames, ignore_index=True)

        combined.to_csv(output_path, sep=self.separator, index=False,
                        encoding=self.encoding, lineterminator="\n")
        print(f"    - {len(combined)} rows × {len(combined.columns)} columns "
              f"in {len(frames)} frammento/i")
        return len(combined)

    def process_pdf(self, pdf_path):
        """Process a single PDF file"""
        pdf_name = Path(pdf_path).stem
        # L'anno e' il nome della cartella che contiene il PDF: Out/PDF/<tabella>/<anno>/
        src_dir = Path(pdf_path).parent
        year = src_dir.name if src_dir.name.isdigit() else self.year
        print(f"\nProcessing: {pdf_name}.pdf")

        # Detect PDF type
        pdf_type = self.detect_pdf_type(pdf_path)
        print(f"  Type: {pdf_type}")

        # Try lattice method first
        print(f"  Extracting tables (lattice method)...")
        tables = self.extract_with_tabula(pdf_path, method='lattice')

        # If no tables found, try stream method
        if not tables or len(tables) == 0:
            print(f"  Extracting tables (stream method)...")
            tables = self.extract_with_tabula(pdf_path, method='stream')

        if not tables or len(tables) == 0:
            # Distinguish "no table here" from "text unreadable": on the
            # garbled and ciphered pages the characters are absent from the
            # PDF, so tabula can only return empty frames. Saying so avoids
            # the failure being later misread as a tabula bug.
            reason = self.text_status(pdf_path)
            print(f"  ⚠️  No tables found — {reason}")
            return False

        print(f"  ✓ Found {len(tables)} table(s)")

        # Process based on type
        try:
            # I processor specifici esistono per i codici della famiglia 2
            # (A1..P2), gli unici la cui struttura a colonne e' stata
            # verificata. Le famiglie 1 e 3 hanno intestazioni diverse e
            # vengono pulite e concatenate senza elaborazione dedicata.
            family, label = self.table_semantics(pdf_type)
            if family is None:
                print(f"  ⚠️  Codice '{pdf_type}' non riconosciuto")
            else:
                print(f"  Family {family}: {label}")

            cleaned = [self.clean_dataframe(t) for t in tables]
            cleaned = [c for c in cleaned if len(c) > 0]
            if not cleaned:
                print("  ⚠️  Nessuna tabella con dati dopo la pulizia")
                return False
            processed_data = cleaned

            # Save to CSV: Out/CSV/<tabella>/<anno>/<tabella>.csv
            dest_dir = self.output_root / pdf_name / year
            dest_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(dest_dir / f"{pdf_name}{CSV_SUFFIX}")
            self.save_to_csv(processed_data, output_path, pdf_type)

            print(f"  ✓ Saved to: {output_path}")
            return True

        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            import traceback
            traceback.print_exc()
            return False

    def process_all(self):
        """Process all PDF files in input directory"""
        # Percorso previsto: Out/PDF/<tabella>/<anno>/<tabella>.pdf
        pdf_files = sorted(self.input_dir.glob(f"*/{self.year}/*.pdf"))

        # Fallback: cartella piatta con i PDF delle singole tabelle
        if not pdf_files:
            pdf_files = sorted(self.input_dir.glob("*.pdf"))

        if not pdf_files:
            print(f"No PDF files found in {self.input_dir}")
            print("\nTo use this script:")
            print(f"Atteso: {self.input_dir}/<tabella>/<anno>/<tabella>.pdf")
            print("1. Esegui prima Reports2PDFTables per generare i PDF")
            print(f"2. Oppure passa input_dir=... (anno selezionato: {self.year})")
            return

        print(f"Found {len(pdf_files)} PDF file(s)")
        print("=" * 70)

        success = 0
        failed = 0

        for pdf_path in sorted(pdf_files):
            if self.process_pdf(str(pdf_path)):
                success += 1
            else:
                failed += 1

        # Summary
        print("\n" + "=" * 70)
        print("Processing Complete!")
        print(f"  ✓ Success: {success}")
        print(f"  ✗ Failed: {failed}")
        print(f"\nCSV files saved to: {self.output_root}/<tabella>/<anno>/")

def main(argv=None):
    """Punto d'ingresso."""
    parser = argparse.ArgumentParser(
        description="Estrae le tabelle dai PDF per-table in file CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--year", default=YEAR,
        help=f"anno da processare (default: {YEAR})",
    )
    parser.add_argument(
        "--input-dir", default=None,
        help=f"cartella dei PDF per-table (default: <base>/{OUT_DIR}/PDF)",
    )
    parser.add_argument(
        "--output-root", default=None,
        help=f"cartella dei CSV (default: <base>/{OUT_DIR}/CSV)",
    )
    parser.add_argument("--base", default="", help="radice dei percorsi (default: .)")
    parser.add_argument(
        "--sep", default=CSV_SEPARATOR,
        help=f"separatore di campo (default: {CSV_SEPARATOR!r}; usa ',' "
             f"per un CSV RFC 4180, '\\t' per importare in MySQL "
             f"senza FIELDS TERMINATED BY)",
    )
    parser.add_argument(
        "--encoding", default=CSV_ENCODING,
        help=f"encoding dei CSV (default: {CSV_ENCODING}; usa utf-8 per "
             f"non scrivere il BOM di fronte a Excel)",
    )
    args = parser.parse_args(argv)

    pdf_root = Path(args.base, OUT_DIR, "PDF")
    csv_root = Path(args.base, OUT_DIR, "CSV")

    print("IndividualTables2SQL - PDF Table Extractor to CSV")
    print("Supports: TABELLE A1..P2 (famiglia 2) e i codici art. 27")
    print(f"Anno: {args.year}")
    print(f"Input:  {pdf_root}/<tabella>/<anno>/")
    print(f"Output: {csv_root}/<tabella>/<anno>/")
    print("=" * 70)

    extractor = PDFTableExtractor(
        input_dir=args.input_dir, output_root=args.output_root,
        year=args.year, base=args.base,
        separator=args.sep, encoding=args.encoding,
    )
    extractor.process_all()


if __name__ == "__main__":
    main()
