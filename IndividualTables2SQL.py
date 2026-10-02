#!/usr/bin/env python3
"""
IndividualTables2SQL — turn the per-table PDFs produced by
2024relations2IndividualTables into Excel workbooks (and, later, SQL).

    Out/PDF/<tabella>/<anno>/<tabella>.pdf  ->  Out/XLS/<tabella>/<anno>/<tabella>.xlsx

Usage:
    python IndividualTables2SQL.py --year 2024
    python IndividualTables2SQL.py --year 2024 --input-dir Out/PDF --output-root Out/XLS
    python IndividualTables2SQL.py --year 2024 --base /path/to/project

Requires: tabula-py, pandas, openpyxl  (pip install -r requirements.txt)
tabula-py shells out to Java, so a JRE must be available on PATH or via JAVA_HOME.
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

# Percorsi del progetto: Out/PDF/<tabella>/<anno>/ -> Out/XLS/<tabella>/<anno>/
OUT_DIR = "Out"

# Anno di riferimento predefinito (corrisponde alla cartella
# reports_185_1990/<anno>/ da cui derivano i PDF in Out/PDF/<anno>/)
YEAR = "2024"

# Radice dei percorsi, impostabile da CLI con --base
BASE = ""


class PDFTableExtractor:
    """Extract tables from PDF files based on their format"""

    def __init__(self, input_dir=None, output_root=None, year=YEAR, base=BASE):
        # Default: Out/PDF/<tabella>/<anno>/*.pdf  ->  Out/XLS/<tabella>/<anno>/*.xlsx
        self.input_dir = Path(input_dir) if input_dir else Path(base, OUT_DIR, "PDF")
        self.output_root = Path(output_root) if output_root else Path(base, OUT_DIR, "XLS")
        self.year = str(year)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def detect_pdf_type(self, pdf_path):
        """Detect the type of PDF based on filename"""
        filename = Path(pdf_path).stem.upper()

        # Map filename patterns to table types
        type_mapping = {
            'TAB_N1': 'TAB_N1',
            'TAB_N2': 'TAB_N2',
            'TAB_O1': 'TAB_O1',
            'TAB_O2': 'TAB_O2',
            'TAB_A2': 'TAB_A2',
            'TAB_A4': 'TAB_A4',
            'TAB_B6': 'TAB_B6',
            'TAB_B7': 'TAB_B7',
            'TAB_M1': 'TAB_M1',
            'TAB_M2': 'TAB_M2',
            'TAB_P1': 'TAB_P1',
            'TAB_P2': 'TAB_P2',
        }

        for pattern, table_type in type_mapping.items():
            if pattern in filename or pattern.replace('_', '') in filename:
                return table_type

        return 'UNKNOWN'

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
        """Save extracted data to Excel file"""
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            if isinstance(data, list):
                # Multiple dataframes
                for idx, df in enumerate(data, 1):
                    sheet_name = f'Table_{idx}' if len(data) > 1 else 'Data'
                    sheet_name = sheet_name[:31]  # Excel limit
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    print(f"    - {sheet_name}: {len(df)} rows × {len(df.columns)} columns")
            else:
                # Single dataframe
                data.to_excel(writer, sheet_name='Data', index=False)
                print(f"    - Data: {len(data)} rows × {len(data.columns)} columns")

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
            print(f"  ⚠️  No tables found")
            return False

        print(f"  ✓ Found {len(tables)} table(s)")

        # Process based on type
        try:
            processor_map = {
                'TAB_N1': self.process_tab_n1,
                'TAB_N2': self.process_tab_n2,
                'TAB_O1': self.process_tab_o1,
                'TAB_O2': self.process_tab_o2,
                'TAB_A2': self.process_tab_a2,
                'TAB_A4': self.process_tab_a4,
                'TAB_B6': self.process_tab_b6,
                'TAB_B7': self.process_tab_b7,
                'TAB_M1': self.process_tab_m1,
                'TAB_M2': self.process_tab_m2,
                'TAB_P1': self.process_tab_p1,
                'TAB_P2': self.process_tab_p2,
            }

            if pdf_type in processor_map:
                processed_data = processor_map[pdf_type](tables)
            else:
                # Unknown type - save all tables as-is
                processed_data = [self.clean_dataframe(t) for t in tables]

            # Save to Excel: Out/XLS/<tabella>/<anno>/<tabella>.xlsx
            dest_dir = self.output_root / pdf_name / year
            dest_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(dest_dir / f"{pdf_name}.xlsx")
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
        # Percorso previsto: Out/PDF/<tabella>/<anno>/<tabella>.pdf
        pdf_files = sorted(self.input_dir.glob(f"*/{self.year}/*.pdf"))

        # Fallback: cartella piatta con i PDF delle singole tabelle
        if not pdf_files:
            pdf_files = sorted(self.input_dir.glob("*.pdf"))

        if not pdf_files:
            print(f"No PDF files found in {self.input_dir}")
            print("\nTo use this script:")
            print(f"Atteso: {self.input_dir}/<tabella>/<anno>/<tabella>.pdf")
            print("1. Esegui prima 2024relations2IndividualTables per generare i PDF")
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
        print(f"\nExcel files saved to: {self.output_root}/<tabella>/<anno>/")

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
    print(f"Input:  {pdf_root}/<tabella>/<anno>/")
    print(f"Output: {xls_root}/<tabella>/<anno>/")
    print("=" * 70)

    extractor = PDFTableExtractor(
        input_dir=args.input_dir, output_root=args.output_root,
        year=args.year, base=args.base,
    )
    extractor.process_all()


if __name__ == "__main__":
    main()
