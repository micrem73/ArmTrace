#!/usr/bin/env bash
# Download Law 185/1990 annual relations (Relazione sulle operazioni autorizzate
# per il controllo dell'esportazione, importazione e transito dei materiali di
# armamento) from the official Camera dei Deputati archive.
set -uo pipefail

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
DEST="${1:?destination dir}"
MANIFEST="${2:?manifest tsv}"
JOBS="${JOBS:-4}"
mkdir -p "$DEST"

# pdfinfo (poppler) is not installed by default on macOS, so validate PDFs by
# their %PDF- header there instead of refusing every download.
if command -v pdfinfo >/dev/null 2>&1; then
  is_pdf() { pdfinfo "$1" >/dev/null 2>&1; }
else
  echo "note: pdfinfo not found, falling back to %PDF- header check"
  is_pdf() { head -c 5 "$1" 2>/dev/null | grep -q '%PDF-'; }
fi

# filename comes from the manifest's `file` column, not derived here, so that
# volumes already on disk under a different name (e.g. the 2024 files) are
# recognised and skipped instead of being re-downloaded.
fetch() {
  local year="$1" leg="$2" num="$3" vol="$4" file="$5" url="$6"
  local dir="$DEST/$year"
  local out="$dir/$file"
  mkdir -p "$dir"

  if [ -s "$out" ] && is_pdf "$out"; then
    echo "SKIP  $year n.$num $vol"
    return 0
  fi

  local tmp; tmp=$(mktemp)
  if curl -sSL --fail --max-time 900 --retry 3 --retry-delay 3 -A "$UA" "$url" -o "$tmp"; then
    if is_pdf "$tmp"; then
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
# The manifest is dispatched with a plain read loop rather than
# `xargs -I{}` + `bash -c`: BSD xargs (macOS) silently declines to substitute
# `{}` when the assembled command would exceed its `-S` replace-size limit
# (255 bytes by default), passing the literal `{}` through, and its `-d` does
# not exist at all. GNU xargs has no such limit, so the same line worked on
# Linux. Concurrency is capped with plain background jobs, since macOS still
# ships bash 3.2 and `wait -n` needs bash 4.3.
pids=()
while IFS=$'\t' read -r year leg num vol file pages url || [ -n "$year" ]; do
  fetch "$year" "$leg" "$num" "$vol" "$file" "$url" &
  pids+=("$!")
  if [ "${#pids[@]}" -ge "$JOBS" ]; then
    wait "${pids[0]}" || true
    pids=("${pids[@]:1}")
  fi
done < <(grep -v -e '^#' -e '^[[:space:]]*$' "$MANIFEST")
wait
echo "--- download complete ---"
