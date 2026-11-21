#!/usr/bin/env bash
# Release a committed version of this self-contained multi-skill plugin (either into a plugin
# directory with --target, or as individual skills into a harness's skills folder with --harness).
# Never installs uncommitted changes.
#
# Usage: bash install.sh [--ref REF] [--harness NAME]... [--target DIR] [--backups DIR] [--skip-tests]
#   --ref      the commit, tag or branch to release (default HEAD); an older ref is a rollback
#   --harness  install every bundled skill into that harness's skills folder (repeatable; names from
#              `python3 scripts/harnesses.py install-dirs`). Without --harness or --target, every harness
#              that already has agy-multiagent-workflow installed is updated.
#   --target   install the full plugin bundle into this exact folder instead
#   --backups  where replaced installs are kept, per harness (default ~/.local/share/agy-multiagent-workflow/backups);
#              the newest 5 of each are kept
#   --skip-tests  install without running that ref's test suite first
#   --home, --harness-fixtures  for tests: the home folder and captured CLI output harnesses.py uses
#
# What gets installed:
#   --target: plugin.json, THIRD_PARTY_NOTICES.md, assets/, skills/, scripts/, references/, agents/, resources/, docs/, VERSION.
#   --harness / update: each skill under skills/<name>/ into <skills_dir>/<name>/, plus shared runtime
#             (scripts/, references/, agents/, resources/, assets/, docs/, plugin.json, THIRD_PARTY_NOTICES.md, VERSION)
#             inside <skills_dir>/agy-multiagent-workflow/.
# Exit 0: INSTALLED <sha> ... .  Exit 1: tests failed or the ref is unknown; nothing changed.
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
# For --target, folder is the plugin destination; for harnesses, folder is the harness skills root.
labels=(); targets=()
if [ -n "$target" ]; then
  labels+=(custom); targets+=("$target")
else
  table=$(python3 -I "$repo/scripts/harnesses.py" install-dirs "${dirs_args[@]}")
  if [ ${#harnesses[@]} -eq 0 ]; then
    while IFS=$'\t' read -r h dir found; do
      [ -e "$dir/$name" ] && { labels+=("$h"); targets+=("$dir"); }
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
      labels+=("$h"); targets+=("$(cut -f2 <<< "$row")")
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
for part in SKILL.md plugin.json THIRD_PARTY_NOTICES.md assets skills references scripts agents resources docs; do
  [ -e "$work/$part" ] && cp -a "$work/$part" "$release/"
done
find "$release" -name __pycache__ -prune -exec rm -rf {} +
{
  echo "commit $sha"
  echo "subject $(git -C "$repo" log -1 --format=%s "$sha")"
  echo "installed $(date -Iseconds) from $repo"
} > "$release/VERSION"

prune_backups() {
  local keep=$1
  if [ -d "$keep" ] && compgen -G "$keep/*" >/dev/null; then
    ls -1dt "$keep"/* | tail -n +6 | while read -r old; do rm -rf "$old"; done
  fi
}

for i in "${!targets[@]}"; do
  label=${labels[$i]}; dest=${targets[$i]}; keep="$backups/$label"
  backup=""
  if [ "$label" = "custom" ]; then
    mkdir -p "$(dirname "$dest")"
    if [ -e "$dest" ]; then
      mkdir -p "$keep"
      backup="$keep/$(date +%Y%m%d-%H%M%S)-$$"
      mv "$dest" "$backup"
    fi
    cp -a "$release" "$dest"
    prune_backups "$keep"
    echo "INSTALLED ${sha:0:12} to $dest ($label)${backup:+, previous kept at $backup}"
  else
    mkdir -p "$dest"
    snap="$keep/$(date +%Y%m%d-%H%M%S)-$$"
    prev_managed=()
    if [ -f "$dest/$name/.managed-skills" ]; then
      while IFS= read -r mname; do
        [ -n "$mname" ] && prev_managed+=("$mname")
      done < "$dest/$name/.managed-skills"
    fi
    count=0
    has_bundled_skills=0
    for sdir in "$release"/skills/*; do
      [ -d "$sdir" ] || continue
      has_bundled_skills=1
      sname=$(basename "$sdir")
      count=$((count + 1))
      if [ -e "$dest/$sname" ]; then
        mkdir -p "$snap"
        mv "$dest/$sname" "$snap/$sname"
        backup="$snap"
      fi
    done
    if [ ${#prev_managed[@]} -gt 0 ]; then
      for sname in "${prev_managed[@]}"; do
        [ "$sname" = "$name" ] && continue
        if [ -e "$dest/$sname" ]; then
          mkdir -p "$snap"
          mv "$dest/$sname" "$snap/$sname"
          backup="$snap"
        fi
      done
    fi
    if [ "$has_bundled_skills" -eq 0 ] && [ -e "$dest/$name" ]; then
      mkdir -p "$snap"
      mv "$dest/$name" "$snap/$name"
      backup="$snap"
    fi
    # Install main orchestrator + shared runtime into $dest/$name
    mkdir -p "$dest/$name"
    if [ -d "$release/skills/$name" ]; then
      cp -a "$release/skills/$name/." "$dest/$name/"
    elif [ -f "$release/SKILL.md" ]; then
      cp -a "$release/SKILL.md" "$dest/$name/"
      count=1
    fi
    for part in plugin.json THIRD_PARTY_NOTICES.md assets references scripts agents resources docs VERSION; do
      [ -e "$release/$part" ] && cp -a "$release/$part" "$dest/$name/"
    done
    # Install each standalone skill directly into $dest/<skill_name>
    managed=()
    for sdir in "$release"/skills/*; do
      [ -d "$sdir" ] || continue
      sname=$(basename "$sdir")
      [ "$sname" = "$name" ] && continue
      cp -a "$sdir" "$dest/$sname"
      managed+=("$sname")
    done
    if [ ${#managed[@]} -gt 0 ]; then
      printf '%s\n' "${managed[@]}" > "$dest/$name/.managed-skills"
    fi
    prune_backups "$keep"
    echo "INSTALLED ${sha:0:12} to $dest/$name ($count skills in $dest) ($label)${backup:+, previous kept at $backup}"
  fi
done
