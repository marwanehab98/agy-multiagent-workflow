#!/usr/bin/env bash
# Release a committed version of this skill into a skills folder (the buffer between work in progress and
# what runs load). Never installs uncommitted changes.
#
# Usage: bash install.sh [--ref REF] [--target DIR] [--backups DIR] [--skip-tests]
#   --ref      the commit, tag or branch to release (default HEAD); an older ref is a rollback
#   --target   where the skill goes (default ~/.claude/skills/orca-multiagent-workflow)
#   --backups  where the replaced install is kept (default ~/.local/share/orca-multiagent-workflow/backups);
#              the newest 5 are kept
#   --skip-tests  install without running that ref's test suite first
#
# What gets installed: SKILL.md, references/, scripts/, docs/, and a VERSION file. Not tests/ or this script.
# Exit 0: INSTALLED <sha> to <target>.  Exit 1: tests failed or the ref is unknown; nothing changed.
set -euo pipefail

repo=$(cd "$(dirname "$0")" && pwd)
ref=HEAD
target="$HOME/.claude/skills/orca-multiagent-workflow"
backups="$HOME/.local/share/orca-multiagent-workflow/backups"
run_tests=1
while [ $# -gt 0 ]; do
  case $1 in
    --ref) ref=$2; shift 2 ;;
    --target) target=$2; shift 2 ;;
    --backups) backups=$2; shift 2 ;;
    --skip-tests) run_tests=0; shift ;;
    *) echo "usage: install.sh [--ref REF] [--target DIR] [--backups DIR] [--skip-tests]" >&2; exit 64 ;;
  esac
done

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

mkdir -p "$(dirname "$target")"
backup=""
if [ -e "$target" ]; then
  mkdir -p "$backups"
  backup="$backups/$(date +%Y%m%d-%H%M%S)-$$"
  mv "$target" "$backup"
fi
mv "$release" "$target"
if [ -d "$backups" ]; then
  ls -1dt "$backups"/* 2>/dev/null | tail -n +6 | while read -r old; do rm -rf "$old"; done
fi
echo "INSTALLED ${sha:0:12} to $target${backup:+ (previous kept at $backup)}"
