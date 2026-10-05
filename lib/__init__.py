"""
Library modules, imported by the two pipeline scripts. Not entry points.

    lib/ontology.py   the ministries, the article tokens, and the Out/ path shape
    lib/indice.py     reading ministry page ranges out of a volume's INDICE

Neither is meant to be run. They hold no argument parsing and print nothing on
their own; `Reports2PDFTables.py` is the only thing that drives them, and
`IndividualTables2SQL.py` imports ontology alone, for the path shape and the
article labels. Keeping them out of the project root says so, and leaves the two
runnable scripts as the only entry points.

They are a package rather than two modules dropped onto sys.path, so the imports
resolve from the project root no matter which directory the caller runs from --
Python puts the *script's* directory on sys.path, not the working directory.
"""