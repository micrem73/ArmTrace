"""
Library modules, imported by the pipeline scripts. Not entry points.

    lib/ontology.py   the ministries, the article tokens, and the Out/ path shape
    lib/indice.py     reading ministry page ranges out of a volume's INDICE
    lib/geometry.py   reading space: rotation, rules, bands, columns
    lib/grammar.py    printed values, band kinds, and a table's record grammar

None is meant to be run. They hold no argument parsing and print nothing on their
own. `Reports2PDFTables.py` drives the first two; `CatalogueTables.py` drives
the last two and imports `ontology` for the path shape and article labels.
Keeping them out of the project root says so, and leaves the scripts in the
root as the only entry points.

They are a package rather than modules dropped onto sys.path, so the imports
resolve from the project root no matter which directory the caller runs from --
Python puts the *script's* directory on sys.path, not the working directory.

Division of authority: `geometry.py` and `grammar.py` know about glyphs and
about printed values, and neither knows what a ministry is. What a table *means*
-- which is what `grammar.py` labels -- is a per-ministry question and belongs
above this package. See AGENTS-TABLE2SQL.md.
"""