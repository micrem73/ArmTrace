"""
Library modules, imported by the pipeline scripts. Not entry points.

    lib/patterns.py   every regex and constant step 1 reads; year-independent
    lib/pageio.py     reading one page: text, stamp, folio, corruption
    lib/emit.py       manifest -> Out/PDF/ and Out/TRASH/
    lib/volumes.py    finding and ordering the source PDFs
    lib/ontology.py   the ministries, the article tokens, and the Out/ path shape
    lib/indice.py     reading ministry page ranges out of a volume's INDICE
    lib/geometry.py   reading space: rotation, rules, bands, columns
    lib/grammar.py    printed values, band kinds, and a table's record grammar

None is meant to be run. They hold no argument parsing and print nothing on their
own. The per-year step 1 scripts drive the first four; `CatalogueTables.py` drives
`geometry` and `grammar` and imports `ontology` for the path shape and article
labels. Keeping them out of the project root says so, and leaves the scripts in
the root as the only entry points.

The first four are shared by every year on purpose: they hold no ministry and no
code vocabulary, only what a page prints. A year that needs different DETECTION
changes its own script; a year that needs a different primitive shadows it
locally. Nobody edits these to suit one year, because a mistake here is a mistake
for all of them.

They are a package rather than modules dropped onto sys.path, so the imports
resolve from the project root no matter which directory the caller runs from --
Python puts the *script's* directory on sys.path, not the working directory.

Division of authority: `patterns.py`, `pageio.py`, `emit.py` and `volumes.py`
know what the archive PRINTS; `geometry.py` and `grammar.py` know what a printed
table MEANS. Neither half knows what a ministry is -- what a table means, which
is what `grammar.py` labels, is a per-ministry question and belongs above this
package. See READING-TABLES.md.
"""