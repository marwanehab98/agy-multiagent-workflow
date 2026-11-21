#!/usr/bin/env bash
# Release a committed version of this skill into a skills folder (the buffer between work in progress and
# what runs load). Never installs uncommitted changes.
#
# Usage: bash install.sh [--ref REF] [--harness NAME]... [--target DIR] [--backups DIR] [--skip-tests]
#   --ref      the commit, tag or branch to release (default HEAD); an older ref is a rollback
#   --harness  install into that harness's skills folder (repeatable; names from
#              `python3 scripts/harnesses.py install-dirs`). Without --harness or --target, every harness that
#              already has the skill is updated, so a release never adds it to a new harness by itself.
#   --target   install into this exact folder instead
#   --backups  where replaced installs are kept, per harness (default ~/.local/share/agy-multiagent-workflow/backups);
#              the newest 5 of each are kept
#   --skip-tests  install without running that ref's test suite first
#   --home, --harness-fixtures  for tests: the home folder and captured CLI output harnesses.py uses
#
# What gets installed: SKILL.md, references/, scripts/, docs/, and a VERSION file. Not tests/ or this script.
# Exit 0: INSTALLED <sha> to <target>.  Exit 1: tests failed or the ref is unknown; nothing changed.
set -euo pipefail

repo=$(cd "$(dirname "$0")" && pwd)
name=agy-multiagent-workflow
ref=HEAD
target=""
harnesses=()
backups="$HOME/.local/share/agy-multiagent-workflow/backups"
run_tests=1
dirs_args=()
while [ $# -gt 0 ]; do
  case $1 in
    --ref) ref=$2; shift 2 ;;
    --harness) harnesses+=("$2"); shift 2 ;;
    --target) target=$2; shift 2 ;;
    --backups) backups=$2; shift 2 ;;
    --skip-tests) run_tests=0; shift ;;
    --home) dirs_args+=(--home "$2"); shift 2 ;;
    --harness-fixtures) dirs_args+=(--fixtures "$2"); shift 2 ;;
    *) echo "usage: install.sh [--ref REF] [--harness NAME]... [--target DIR] [--backups DIR] [--skip-tests]" >&2; exit 64 ;;
  esac
done

# Where to install: (label, folder) pairs.
labels=(); targets=()
if [ -n "$target" ]; then
  labels+=(custom); targets+=("$target")
else
  table=$(python3 -I "$repo/scripts/harnesses.py" install-dirs "${dirs_args[@]}")
  if [ ${#harnesses[@]} -eq 0 ]; then
    while IFS=$'\t' read -r h dir found; do
      [ -e "$dir/$name" ] && { labels+=("$h"); targets+=("$dir/$name"); }
    done <<< "$table"
    if [ ${#targets[@]} -eq 0 ]; then
      echo "ERROR $name isn't installed in any harness yet; pass --harness NAME (one of: $(cut -f1 <<< "$table" | paste -sd' '))"
      exit 1
    fi
  else
    for h in "${harnesses[@]}"; do
      row=$(awk -F'\t' -v h="$h" '$1==h' <<< "$table")
      [ -n "$row" ] || { echo "ERROR unknown harness $h (one of: $(cut -f1 <<< "$table" | paste -sd' '))"; exit 1; }
      [ "$(cut -f3 <<< "$row")" = found ] || { echo "ERROR harness $h isn't installed on this machine"; exit 1; }
      labels+=("$h"); targets+=("$(cut -f2 <<< "$row")/$name")
    done
  fi
fi

sha=$(git -C "$repo" rev-parse --verify -q "$ref^{commit}") || { echo "ERROR unknown ref: $ref"; exit 1; }
if [ "$ref" = HEAD ] && [ -n "$(git -C "$repo" status --porcelain)" ]; then
  echo "NOTE uncommitted changes in $repo are not part of this release"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git -C "$repo" archive "$sha" | tar -x -C "$work"
if [ $run_tests -eq 1 ]; then
  echo "testing $sha ..."
  if ! (cd "$work" && bash tests/run_all.sh > "$work/.test-output" 2>&1); then
    tail -20 "$work/.test-output"
    echo "ERROR the tests of $sha failed; nothing installed"
    exit 1
  fi
fi

release="$work/release"
mkdir -p "$release"
for part in SKILL.md references scripts docs; do
  [ -e "$work/$part" ] && cp -a "$work/$part" "$release/"
done
find "$release" -name __pycache__ -prune -exec rm -rf {} +
{
  echo "commit $sha"
  echo "subject $(git -C "$repo" log -1 --format=%s "$sha")"
  echo "installed $(date -Iseconds) from $repo"
} > "$release/VERSION"

for i in "${!targets[@]}"; do
  dest=${targets[$i]}; keep="$backups/${labels[$i]}"
  mkdir -p "$(dirname "$dest")"
  backup=""
  if [ -e "$dest" ]; then
    mkdir -p "$keep"
    backup="$keep/$(date +%Y%m%d-%H%M%S)-$$"
    mv "$dest" "$backup"
  fi
  cp -a "$release" "$dest"
  if [ -d "$keep" ]; then
    ls -1dt "$keep"/* | tail -n +6 | while read -r old; do rm -rf "$old"; done
  fi
  echo "INSTALLED ${sha:0:12} to $dest (${labels[$i]})${backup:+, previous kept at $backup}"
done
