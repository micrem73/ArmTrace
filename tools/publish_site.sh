#!/usr/bin/env bash
# Publish the built explorer to GitHub Pages: site/ becomes the whole content of
# the gh-pages branch, as one commit with no history (the data's history is the
# pipeline's, in git and DECISIONS.md, not here).
#
#     python3 tools/build_site.py [--root DIR]   # first
#     bash tools/publish_site.sh [site]
#
# Served at https://<owner>.github.io/<repo>/ once Pages points at gh-pages.
# Decided 2026-10-19 (DECISIONS.md).
set -euo pipefail

site=${1:-site}
[ -f "$site/index.html" ] && [ -f "$site/armtrace.sqlite" ] ||
  { echo "$site: not a built site; run python3 tools/build_site.py" >&2; exit 1; }
# A site/ left from an older build would go out labelled with today's HEAD.
#   Compared with the build's ?v=<hash> stamps taken out (build_site.stamp).
stale=$(cd "$site" && find . -type f ! -name armtrace.sqlite | sort)
[ "$stale" = "$(cd web && find . -type f | sort)" ] && stale=
for f in web/*; do
  sed 's/?v=[0-9a-f]\{12\}//g' "$site/${f#web/}" 2>/dev/null | cmp -s - "$f" ||
    stale="$stale${stale:+$'\n'}${f#web/} differs"
done
if [ -n "$stale" ]; then
  echo "$stale" >&2
  echo "$site: built from another web/; run python3 tools/build_site.py first" >&2
  exit 1
fi

remote=$(git remote get-url origin)
source=$(git rev-parse --short HEAD)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cp -R "$site"/. "$work"/
touch "$work/.nojekyll"          # serve files as they are, no Jekyll pass
git -C "$work" init -q -b gh-pages
git -C "$work" add -A
git -C "$work" -c user.name="$(git config user.name)" -c user.email="$(git config user.email)" \
  commit -q -m "Explorer built from $source"
git -C "$work" push -q --force "$remote" gh-pages
echo "pushed $site to gh-pages (built from $source)"
