#!/usr/bin/env bash
# Tells CI whether a push or PR touched anything besides docs. Prints
# code=false only if every file changed since $1 is docs, so the test and
# image-build jobs can skip; when unsure, everything runs.
#   bash scripts/ci-changes.sh <base-sha>
set -euo pipefail
base="${1:-}"
all() { echo "ci-changes: $1, running everything" >&2; echo code=true; exit 0; }
[ -n "$base" ] && [ "$base" != 0000000000000000000000000000000000000000 ] || all "no base"
git cat-file -e "$base^{commit}" 2>/dev/null || all "base $base not in clone"
changed=$(git diff --name-only "$base" HEAD)
[ -n "$changed" ] || all "empty diff"
while IFS= read -r f; do
  case "$f" in
    # Only what no build, test or lint step reads. VERSION and the compose
    # file are not docs: the images copy one and CI validates the other.
    *.md | docs/* | *.png | *.jpg | *.jpeg | *.gif | *.webp) ;;
    *) all "$f is not docs" ;;
  esac
done <<< "$changed"
echo code=false
