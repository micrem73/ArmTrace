#!/usr/bin/env bash
# Download Law 185/1990 annual relations (Relazione sulle operazioni autorizzate
# per il controllo dell'esportazione, importazione e transito dei materiali di
# armamento) from the official Camera dei Deputati archive.
set -uo pipefail

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
DEST="${1:?destination dir}"
MANIFEST="${2:?manifest tsv}"
mkdir -p "$DEST"

# filename comes from the manifest's `file` column, not derived here, so that
# volumes already on disk under a different name (e.g. the 2024 files) are
# recognised and skipped instead of being re-downloaded.
fetch() {
  local year="$1" leg="$2" num="$3" vol="$4" file="$5" url="$6"
  local dir="$DEST/$year"
  local out="$dir/$file"
  mkdir -p "$dir"

  if [ -s "$out" ] && pdfinfo "$out" >/dev/null 2>&1; then
    echo "SKIP  $year n.$num $vol"
    return 0
  fi

  local tmp; tmp=$(mktemp)
  if curl -sSL --fail --max-time 900 --retry 3 --retry-delay 3 -A "$UA" "$url" -o "$tmp"; then
    if pdfinfo "$tmp" >/dev/null 2>&1; then
      mv "$tmp" "$out"
      echo "OK    $year n.$num $vol ($(du -h "$out" | cut -f1))"
    else
      echo "BAD   $year n.$num $vol (not a PDF: $url)"
      rm -f "$tmp"
    fi
  else
    echo "FAIL  $year n.$num $vol ($url)"
    rm -f "$tmp"
  fi
}
export -f fetch
export UA DEST

grep -v '^#' "$MANIFEST" | grep -v '^[[:space:]]*$' \
  | xargs -P 4 -d '\n' -I{} bash -c '
      IFS=$'"'"'\t'"'"' read -r year leg num vol file _ url <<< "{}"
      fetch "$year" "$leg" "$num" "$vol" "$file" "$url"
    '
echo "--- download complete ---"
