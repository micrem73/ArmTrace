#!/usr/bin/env python3
"""
IndividualTables2SQL — turn the per-table PDFs produced by
Reports2PDFTables into Excel workbooks (and, later, SQL).

    Out/PDF/<tabella>/<tabella><anno>.PDF  ->  Out/XLS/<tabella>/<tabella><anno>.XLS

The table code is the filename stem. Three code schemes exist in the archive:

    family 1  art. 27 double-letter  AA AA1 BB ... UE, MG1-MG9, MT1, MT7,
              GF, NN, OO, PP
    family 2  A1 .. P2                the 31 MAE detail tables
    family 3  art. 27 single-letter  A B D E G J Q  (2012 vol. I)

Usage:
    python IndividualTables2SQL.py --year 2023
    python IndividualTables2SQL.py --year 2023 --input-dir Out/PDF --output-root Out/XLS
    python IndividualTables2SQL.py --year 2023 --base /path/to/project

Requires: tabula-py, pandas, openpyxl, pypdf  (pip install -r requirements.txt)
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

# Percorsi del progetto:
#   Out/PDF/<tabella>/<tabella><anno>.PDF -> Out/XLS/<tabella>/<tabella><anno>.XLS
OUT_DIR = "Out"

# Estensioni prodotte e consumate dalla pipeline. reports_185_1990/ contiene
# i PDF di origine in minuscolo, ma i PDF per-tabella vengono scritti in
# maiuscolo: su Linux i due casi non si equivalgono.
PDF_EXT = ".PDF"
XLS_EXT = ".XLS"

# Anno di riferimento predefinito (corrisponde alla cartella
# reports_185_1990/<anno>/ da cui derivano i PDF in Out/PDF/<tabella>/)
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

# Limite di righe di un foglio Excel (1048576). Raggiunto solo da tabelle
# enormi, ma va controllato: superarlo produce un file che Excel rifiuta di
# aprire, quindi vale la pena saperlo prima.
EXCEL_MAX_ROWS = 1048576


class PDFTableExtractor:
    """Extract tables from PDF files based on their format"""

    def __init__(self, input_dir=None, output_root=None, year=YEAR, base=BASE):
        # Default: Out/PDF/<tabella>/<tabella><anno>.PDF
        #       -> Out/XLS/<tabella>/<tabella><anno>.XLS
        self.input_dir = Path(input_dir) if input_dir else Path(base, OUT_DIR, "PDF")
        self.output_root = Path(output_root) if output_root else Path(base, OUT_DIR, "XLS")
        self.year = str(year)
        self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def split_stem(stem):
        """Split a per-table filename stem into (table code, year).

        Step 1 writes <tabella><anno>.PDF, so the code carries the year as a
        suffix rather than in a parent directory:

            AA2023  ->  ("AA", "2023")     MT7  ->  ("MT7", None)

        A table code never ends in four digits (the archive uses AA AA1 MG1 ...
        A1 .. P2 and single letters), so a trailing four-digit group is always
        the year and never part of the code.
        """
        m = re.search(r"\d{4}$", stem)
        if m is None:
            return stem, None
        return stem[:m.start()], m.group(0)

    def detect_pdf_type(self, pdf_path):
        """Classify a per-table PDF by its filename.

        Reports2PDFTables writes Out/PDF/<code>/<code><anno>.PDF, so the stem
        minus the trailing year IS the table code. The three code schemes in
        the archive are:

            family 1  art. 27 double-letter  AA AA1 BB ... UE  MG1 MG3 MT7
            family 2  A1 .. P2                31 codes
            family 3  art. 27 single-letter  A B D E G J Q

        Without the year-stripping step the code would come back as "AA2023"
        and fall through to the unknown branch, which is the trap the previous
        mapping (TAB_N1, TAB_O1, TAB_A2 ... -- names that appear nowhere in
        the 43 reports) walked into and that wrote one sheet per extracted
        fragment, the cause of the ~4000-sheet hang recorded in the README.
        """
        code, _ = self.split_stem(Path(pdf_path).stem)
        return code.upper()

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

    def save_to_excel(self, data, output_path, pdf_type):
        """Save extracted data to Excel as a single 'Data' sheet.

        A single PDF can yield hundreds of fragments when tabula's lattice
        method misreads a borderless table. Writing one sheet per fragment
        produced ~4000-sheet workbooks, past Excel's 255-sheet limit, and was
        the hang recorded in the README. Fragments are now concatenated into
        one sheet, so the sheet limit no longer applies; only Excel's
        1048576-row limit does, and exceeding it is reported rather than
        silently truncating.

        The file is named <tabella><anno>.XLS, but pandas refuses to hand a
        non-.xlsx name to the openpyxl engine ("Invalid extension for engine
        'openpyxl': 'XLS'"), so the workbook is written to a .xlsx sibling and
        renamed. The bytes are therefore xlsx under an .XLS name, which Excel
        opens after a format-mismatch prompt. Writing true Excel 97-2003 would
        need the unmaintained xlwt engine and cap the sheet at 65536 rows
        instead of 1048576, so the rename is the cheaper trade. Set XLS_EXT to
        ".xlsx" to get an honest extension with no other change.
        """
        frames = data if isinstance(data, list) else [data]
        frames = [f for f in frames if f is not None and len(f) > 0]

        if not frames:
            print("    - nessun dato: foglio non scritto")
            return 0

        if len(frames) == 1:
            combined = frames[0]
        else:
            combined = pd.concat(frames, ignore_index=True)

        if len(combined) > EXCEL_MAX_ROWS:
            print(f"    ⚠️  {len(combined)} righe superano il limite Excel "
                  f"({EXCEL_MAX_ROWS}): il foglio non verra' scritto")
            return 0

        target = Path(output_path)
        staged = target.with_suffix(".xlsx") if target.suffix.lower() != ".xlsx" else target
        with pd.ExcelWriter(staged, engine='openpyxl') as writer:
            combined.to_excel(writer, sheet_name='Data', index=False)
        if staged != target:
            os.replace(staged, target)
        print(f"    - Data: {len(combined)} rows × {len(combined.columns)} columns")
        return len(combined)

    def process_pdf(self, pdf_path):
        """Process a single PDF file"""
        # L'anno e' il suffisso del nome del file, non piu' il nome della
        # cartella: Out/PDF/<tabella>/<tabella><anno>.PDF
        stem = Path(pdf_path).stem
        table_name, file_year = self.split_stem(stem)
        year = file_year or self.year
        if not table_name:
            table_name = stem
        print(f"\nProcessing: {stem}{PDF_EXT}")

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

            # Save to Excel: Out/XLS/<tabella>/<tabella><anno>.XLS
            dest_dir = self.output_root / table_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(dest_dir / f"{table_name}{year}{XLS_EXT}")
            self.save_to_excel(processed_data, output_path, pdf_type)

            print(f"  ✓ Saved to: {output_path}")
            return True

        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            import traceback
            traceback.print_exc()
            return False

    def process_all(self):
        """Process all PDF files in input directory"""
        # Percorso previsto: Out/PDF/<tabella>/<tabella><anno>.PDF
        pdf_files = sorted(self.input_dir.glob(f"*/*{self.year}{PDF_EXT}"))

        # Fallback: PDF sciolti nella root di input invece che in <tabella>/
        if not pdf_files:
            pdf_files = sorted(self.input_dir.glob(f"*{self.year}{PDF_EXT}"))

        if not pdf_files:
            print(f"No PDF files found in {self.input_dir}")
            print("\nTo use this script:")
            print(f"Atteso: {self.input_dir}/<tabella>/<tabella>{self.year}{PDF_EXT}")
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
        print(f"\nExcel files saved to: {self.output_root}/<tabella>/<tabella><anno>{XLS_EXT}")

def main(argv=None):
    """Punto d'ingresso."""
    parser = argparse.ArgumentParser(
        description="Estrae le tabelle dai PDF per-table in file Excel.",
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
        help=f"cartella degli XLS (default: <base>/{OUT_DIR}/XLS)",
    )
    parser.add_argument("--base", default="", help="radice dei percorsi (default: .)")
    args = parser.parse_args(argv)

    pdf_root = Path(args.base, OUT_DIR, "PDF")
    xls_root = Path(args.base, OUT_DIR, "XLS")

    print("IndividualTables2SQL - PDF Table Extractor to Excel - Extended Version")
    print("Supports: TAB_N1, TAB_N2, TAB_O1, TAB_O2, TAB_A2, TAB_A4,")
    print("          TAB_B6, TAB_B7, TAB_M1, TAB_M2, TAB_P1, TAB_P2")
    print(f"Anno: {args.year}")
    print(f"Input:  {pdf_root}/<tabella>/<tabella>{args.year}{PDF_EXT}")
    print(f"Output: {xls_root}/<tabella>/<tabella>{args.year}{XLS_EXT}")
    print("=" * 70)

    extractor = PDFTableExtractor(
        input_dir=args.input_dir, output_root=args.output_root,
        year=args.year, base=args.base,
    )
    extractor.process_all()


if __name__ == "__main__":
    main()
