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
  echo "note: pdfinfo not found, falling back to %PDF- header check; page counts will not be checked"
  is_pdf() { head -c 5 "$1" 2>/dev/null | grep -q '%PDF-'; }
fi

# A valid PDF can still be the wrong file (another volume, a truncated or
# re-saved copy), so its page count is compared with the manifest's `pages`
# column. The file is kept either way: a mismatch may be a manifest error, and
# it is reported again on every run until one of the two is fixed.
# Prints the actual count, or nothing when it cannot be read.
page_count() {
  command -v pdfinfo >/dev/null 2>&1 || return 0
  pdfinfo "$1" 2>/dev/null | awk '/^Pages:/{print $2}'
}

check_pages() {
  local out="$1" want="$2" label="$3" got
  case "$want" in ''|*[!0-9]*) return 0 ;; esac
  got=$(page_count "$out")
  [ -z "$got" ] && return 0
  if [ "$got" != "$want" ]; then
    echo "PAGES $label: $got pages, manifest says $want ($out)"
    return 1
  fi
}

# filename comes from the manifest's `file` column, not derived here, so that
# volumes already on disk under a different name (e.g. the 2024 files) are
# recognised and skipped instead of being re-downloaded.
fetch() {
  local year="$1" leg="$2" num="$3" vol="$4" file="$5" pages="$6" url="$7"
  local dir="$DEST/$year"
  local out="$dir/$file"

  # The manifest must have exactly 7 tab-separated fields with the URL last.
  # Bail out before mkdir so a wrong manifest cannot leave empty year dirs behind.
  case "$url" in
    http://*|https://*) ;;
    *) echo "ERR   $year n.$num $vol: manifest field 7 is not a URL ('$url')" >&2
       return 1 ;;
  esac

  mkdir -p "$dir"

  if [ -s "$out" ] && is_pdf "$out"; then
    echo "SKIP  $year n.$num $vol"
    check_pages "$out" "$pages" "$year n.$num $vol"
    return
  fi

  local tmp; tmp=$(mktemp)
  if curl -sSL --fail --max-time 900 --retry 3 --retry-delay 3 -A "$UA" "$url" -o "$tmp"; then
    if is_pdf "$tmp"; then
      mv "$tmp" "$out"
      echo "OK    $year n.$num $vol ($(du -h "$out" | cut -f1))"
      check_pages "$out" "$pages" "$year n.$num $vol"
    else
      echo "BAD   $year n.$num $vol (not a PDF: $url)"
      rm -f "$tmp"
      return 1
    fi
  else
    echo "FAIL  $year n.$num $vol ($url)"
    rm -f "$tmp"
    return 1
  fi
}
# The manifest is dispatched with a plain read loop rather than
# `xargs -I{}` + `bash -c`: BSD xargs (macOS) silently declines to substitute
# `{}` when the assembled command would exceed its `-S` replace-size limit
# (255 bytes by default), passing the literal `{}` through, and its `-d` does
# not exist at all. GNU xargs has no such limit, so the same line worked on
# Linux. Concurrency is capped with plain background jobs, since macOS still
# ships bash 3.2 and `wait -n` needs bash 4.3.
# Each job's exit status is collected, so the script exits non-zero when any
# row was ERR, BAD, FAIL or PAGES.
failed=0
pids=()
while IFS=$'\t' read -r year leg num vol file pages url || [ -n "$year" ]; do
  fetch "$year" "$leg" "$num" "$vol" "$file" "$pages" "$url" &
  pids+=("$!")
  if [ "${#pids[@]}" -ge "$JOBS" ]; then
    wait "${pids[0]}" || failed=$((failed + 1))
    pids=("${pids[@]:1}")
  fi
done < <(grep -v -e '^#' -e '^[[:space:]]*$' "$MANIFEST")
# bash 3.2 treats "${pids[@]}" on an empty array as unbound under `set -u`.
for pid in ${pids[@]+"${pids[@]}"}; do
  wait "$pid" || failed=$((failed + 1))
done
if [ "$failed" -gt 0 ]; then
  echo "--- download complete: $failed row(s) need attention ---"
  exit 1
fi
echo "--- download complete ---"
