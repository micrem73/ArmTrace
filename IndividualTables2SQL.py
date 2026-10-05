#!/usr/bin/env python3
"""
IndividualTables2SQL — turn the per-table PDFs produced by
Reports2PDFTables into CSV files (and, later, SQL).

    Out/PDF/<authority>/[<article>/]<table><year>.PDF
        ->  Out/CSV/<authority>/[<article>/]<table><year>.csv

The directory layout is the ontology of the reports themselves, so it is
mirrored rather than flattened: a table is identified by the ministry that
produced it and, where the ministry subdivides by article, by the article.
Reports2PDFTables.py builds the same tree and lib/ontology.py owns the tokens.

Why that is not cosmetic: a table code is only unique within
(authority, article). The MAE prints "TAB M1" for Intermediazioni per Operatore
and the Dogane print "TAB. M1" for Esportazioni Definitive, and "TAB. N" appears
twice in one volume -- art. 1 comma 2 and art. 1 commi 8/9. Keyed on the code
alone the second overwrote the first, which is exactly what happened under the
previous Out/PDF/<tabella>/ layout.

Why CSV and not XLSX: the archive holds tables Excel cannot represent. One
volume yields up to 94 tables (2025 vol. II), and tabula fragments a borderless
one into thousands of pieces. Excel caps a workbook at 255 sheets, a sheet at
1048576 rows and a sheet at 16384 columns, and past any of those it refuses to
open the file at all. CSV has no such cap: one table is one file of any length,
it loads in full into pandas, MySQL or sqlite without a driver having to
understand a spreadsheet, and its text is diffable in git even though the .csv
itself is not tracked.

The year is a filename suffix, not a directory level, so one folder per table
holds one file per reporting year.

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

from lib import ontology

# Percorsi del progetto:
#   Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF
#   Out/CSV/<authority>/[<articolo>/]<tabella><anno>.csv
OUT_DIR = "Out"

# Estensioni prodotte e consumate dalla pipeline. reports_185_1990/ contiene
# i PDF di origine in minuscolo, ma i PDF per-tabella vengono scritti in
# maiuscolo: su Linux i due casi non si equivalgono.
PDF_EXT = ".PDF"

# Anno di riferimento predefinito (corrisponde alla cartella
# reports_185_1990/<anno>/ da cui derivano i PDF in Out/PDF/<authority>/)
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
        # Default: Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF
        #       -> Out/CSV/<authority>/[<articolo>/]<tabella><anno>.csv
        self.input_dir = Path(input_dir) if input_dir else Path(base, OUT_DIR, "PDF")
        self.output_root = Path(output_root) if output_root else Path(base, OUT_DIR, "CSV")
        self.year = str(year)
        self.separator = separator
        self.encoding = encoding
        self.output_root.mkdir(parents=True, exist_ok=True)

    def locate(self, pdf_path):
        """(authority, article, code, year) for one per-table PDF.

        Read back out of the path that step 1 wrote:

            MAE/A12023.PDF              -> MAE,  None,  A1,  2023
            MEF/AA2023.PDF              -> MEF,  None,  AA,  2023
            DOG/A1C2/N12023.PDF         -> DOG,  A1C2,  N,   2023
            DIFESA/A2C6/3A2023.PDF      -> DIFESA, A2C6, 3A,  2023

        The depth is not assumed. The authority is the first component; if a
        second component is present and is not the file, it is the article,
        because only DOG and DIFESA have one. A path that does not start with a
        known authority is still walked -- the year and code are what matter for
        extraction -- but it is reported, because it means step 1 wrote something
        this script does not expect.
        """
        path = Path(pdf_path)
        try:
            # Both sides are resolved first: Path.relative_to refuses to mix a
            # relative base with an absolute path, and silently falling back to
            # the bare filename would drop the ministry and flatten the tree.
            rel = path.resolve().relative_to(self.input_dir.resolve())
        except (ValueError, OSError):
            rel = Path(path.name)
        parts = list(rel.parts[:-1])
        stem, year = self.split_stem(path.stem)
        authority = parts[0] if parts else ontology.UNKNOWN
        article = parts[1] if len(parts) > 1 else None
        return authority, article, stem.upper(), year

    @staticmethod
    def split_stem(stem):
        """Split a per-table filename stem into (table code, year).

        Step 1 writes <tabella><anno>.PDF, so the code carries the year as a
        suffix rather than in a parent directory:

            AA2023   ->  ("AA", "2023")     MG102023 -> ("MG10", "2023")

        A table code never ends in four digits. The longest are MG10..MG18 and
        MT13 in the Dogane, so a trailing four-digit group is always the year and
        never part of the code. Reading from the right is what makes MG10 and
        MT13 work at all: an earlier pattern allowed only one trailing digit in
        a code, which could not match them.
        """
        m = re.search(r"\d{4}$", stem)
        if m is None:
            return stem, None
        return stem[:m.start()], m.group(0)

    def detect_pdf_type(self, pdf_path):
        """The table code, from the path step 1 wrote.

        Three code schemes exist in the archive:

            family 1  art. 27 double-letter  AA AA1 BB ... UE  MG1..MG18  MT13
            family 2  A1 .. P2                31 codes
            family 3  art. 27 single-letter  A B D E G J Q

        plus the DIFESA annessi, whose code is the annesso number. Without the
        year-stripping step the code would come back as "AA2023" and fall
        through to the unknown branch, which is the trap the previous mapping
        (TAB_N1, TAB_O1, TAB_A2 ... -- names that appear nowhere in the reports)
        walked into and that wrote one sheet per extracted fragment, the cause
        of the ~4000-sheet hang recorded in the README.
        """
        return self.locate(pdf_path)[2]

    def table_semantics(self, pdf_type, authority=None, article=None):
        """Describe a table in terms a reader can act on, for the log.

        Returns (family, human label) or (None, None) when the code is not
        recognised. The ministry comes first, because it is what disambiguates:
        M1 is MAE "Intermediazioni per Operatore" and Dogane "Esportazioni
        Definitive", and the code alone cannot say which.
        """
        code = pdf_type.upper()
        if authority == ontology.DOG:
            return 2, f"Dogane - {ontology.article_label(authority, article)}"
        if authority == ontology.DIFESA:
            return 4, (f"Difesa - {ontology.article_label(authority, article)}, "
                       f"annesso {code}")
        if re.fullmatch(r"[A-Z]{2}\d{0,2}", code):
            if code == "LGP":
                return 1, "MEF licenze globali di programma di cooperazione"
            if code in ("NN", "OO", "PP", "GF"):
                return 1, "MEF grafico ripartizione percentuale"
            if code == "UE":
                return 1, "MEF importazioni intra UE"
            if re.fullmatch(r"[A-Z]{2}", code):
                return 1, "MEF art. 27 riepilogo per istituti di credito"
            return 1, "MEF art. 27 riepilogo dettagliato"
        if code in FAMILY2_CODES:
            return 2, "MAE dettaglio operatore / paese"
        if re.fullmatch(r"[A-Z]\d?", code):
            return 3, "MEF art. 27 riepilogo"
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
        # Il nome del file porta solo il codice e l'anno; il ministero e
        # l'articolo sono le directory che lo contengono:
        #   Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF
        authority, article, table_name, file_year = self.locate(pdf_path)
        year = file_year or self.year
        if not table_name:
            table_name = Path(pdf_path).stem
        where = "/".join(filter(None, [authority, article]))
        print(f"\nProcessing: {where}/{table_name}{year}{PDF_EXT}")

        pdf_type = self.detect_pdf_type(pdf_path)
        if authority not in ontology.AUTHORITIES:
            print(f"  ⚠️  '{authority}' is not a known authority directory")

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
            family, label = self.table_semantics(pdf_type, authority, article)
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

            # Save to CSV, mirroring the input tree:
            #   Out/CSV/<authority>/[<articolo>/]<tabella><anno>.csv
            rel = ontology.relative_path(authority, article, table_name,
                                          year, CSV_SUFFIX)
            output_path = Path(self.output_root, rel)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.save_to_csv(processed_data, str(output_path), pdf_type)

            print(f"  ✓ Saved to: {output_path}")
            return True

        except Exception as e:
            print(f"  ✗ Error processing: {e}")
            import traceback
            traceback.print_exc()
            return False

    def process_all(self):
        """Process every per-table PDF for the selected year.

        The tree is walked recursively rather than globbed at a fixed depth,
        because the depth is what the ontology decides: MAE and MEF files sit
        two levels down, DOG and DIFESA three.
        """
        # Atteso: Out/PDF/<authority>/[<articolo>/]<tabella><anno>.PDF
        pdf_files = sorted(self.input_dir.rglob(f"*{self.year}{PDF_EXT}"))

        if not pdf_files:
            print(f"No PDF files found in {self.input_dir}")
            print("\nTo use this script:")
            print(f"Atteso: {self.input_dir}/<authority>/[<articolo>/]/"
                  f"<tabella>{self.year}{PDF_EXT}")
            print("1. Esegui prima Reports2PDFTables per generare i PDF")
            print(f"2. Oppure passa input_dir=... (anno selezionato: {self.year})")
            return

        unknown = sorted({self.locate(p)[0] for p in pdf_files}
                         - set(ontology.AUTHORITIES))
        print(f"Found {len(pdf_files)} PDF file(s)")
        if unknown:
            print(f"⚠️  Directory not a known authority: {' '.join(unknown)}")
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
        print(f"\nCSV files saved to: {self.output_root}/<authority>/"
              f"[<articolo>/]<tabella><anno>{CSV_SUFFIX}")

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
    print("Ministeri: " + "  ".join(ontology.AUTHORITIES))
    print(f"Anno: {args.year}")
    print(f"Input:  {pdf_root}/<authority>/[<articolo>/]<tabella>{args.year}{PDF_EXT}")
    print(f"Output: {csv_root}/<authority>/[<articolo>/]<tabella>{args.year}{CSV_SUFFIX}")
    print("=" * 70)

    extractor = PDFTableExtractor(
        input_dir=args.input_dir, output_root=args.output_root,
        year=args.year, base=args.base,
        separator=args.sep, encoding=args.encoding,
    )
    extractor.process_all()


if __name__ == "__main__":
    main()
