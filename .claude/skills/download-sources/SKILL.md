---
name: download-sources
description: Download the official Law 185/1990 report PDFs into In/<year>/ from the URLs in In/manifest.tsv, checking each file against the manifest's page count. Use when sources are missing, duplicated or suspect, before extracting an edition, or when the user asks to fetch, refresh or re-download the reports for one or more years.
---

# Download sources

The reports are the pipeline's only input. They come from the URLs in
`In/manifest.tsv` and nowhere else: never copy, pick between or patch a local
copy found elsewhere on disk (see `DECISIONS.md`, "Sources live in `In/<year>/`").

## Manifest

`In/manifest.tsv`, tab-separated, `#` lines are comments:

```
year  leg  num  vol  file  pages  url
```

`file` is the name the PDF gets in `In/<year>/`; `pages` is the expected page
count. Years missing from the Camera archive (2009, 2011) point at the SIPRI
mirror; `In/SOURCES.md` records why.

## Steps

1. **Pick the rows.** All years: use `In/manifest.tsv` as is. Some years only:
   filter it into the scratchpad, keeping the header comment, e.g.

   ```bash
   awk -F'\t' '/^#/ || $1==2025' In/manifest.tsv > "$SCRATCH/manifest-2025.tsv"
   ```

2. **Run the script** from the repo root (it can take several minutes per year;
   run it in the background for many years):

   ```bash
   bash In/download_185.sh In <manifest>
   ```

   `JOBS=<n>` sets parallel downloads (default 4). Each row prints one status
   (plus a `PAGES` line after `OK` or `SKIP` when the count differs), and the script exits 1 if any row is `ERR`, `BAD`, `FAIL` or `PAGES`:

   | Status | Meaning | Action |
   |---|---|---|
   | `OK`   | downloaded, is a PDF, page count matches | none |
   | `SKIP` | valid PDF already in place, not downloaded | none |
   | `PAGES` | file is a PDF but its page count differs from the manifest's `pages` (file is kept) | report it; see step 3 |
   | `BAD`  | server returned something that is not a PDF | report URL to the user |
   | `FAIL` | curl error (network, 404, timeout) | retry once; then report |
   | `ERR`  | manifest row malformed (field 7 not a URL) | fix the manifest row |

   Page counts are read with `pdfinfo`. If the script prints
   `note: pdfinfo not found`, no page count was checked: say so in the report.

3. **Replacing a suspect file.** A `PAGES` file is not the one the manifest
   describes. Do not edit the manifest to match the file. The script skips any
   valid PDF already in place, so a wrong file is never refreshed on its own.
   Ask the user first, then move the file aside (to the scratchpad, not `rm`)
   and re-run step 2 for that row only.

4. **Report** a short table: year, volume, status. List every `BAD`, `FAIL`,
   `ERR` and `PAGES` with its URL. Do not start extraction on a year with any
   of these.

## Rules

- Never hand-edit, re-save or rename a file in `In/<year>/`. A file opened and
  saved by a PDF viewer is no longer the official file.
- A new year or a changed URL goes in `In/manifest.tsv` (with its source noted in
  `In/SOURCES.md`), not in the script.
- `In/` PDFs are git-ignored; do not try to commit them.
