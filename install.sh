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

# Copy a file or directory to an exact destination path.
# Falls back from `cp -a` to `cp -R` on mounts (e.g. virtiofs/FUSE) that reject chmod/utimensat.
copy_item() {
  local src=$1 dst=$2
  if ! cp -a "$src" "$dst" 2>/dev/null; then
    [ -d "$src" ] && rm -rf "$dst"
    cp -R "$src" "$dst"
  fi
}

# Copy contents of a directory into an existing destination directory.
copy_into() {
  local src=$1 dst=$2
  if ! cp -a "$src/." "$dst/" 2>/dev/null; then
    cp -R "$src/." "$dst/"
  fi
}

# Move a file or directory into a backup path, falling back to `cp -R` or Python
# `shutil.copyfile` + `rm -rf` on cross-filesystem moves (e.g. virtiofs -> local disk)
# where GNU `mv`/`cp` fail on metadata or unstable inode numbers.
move_backup() {
  local src=$1 dst=$2
  if ! mv "$src" "$dst" 2>/dev/null; then
    [ -d "$src" ] && rm -rf "$dst"
    if ! cp -R "$src" "$dst" 2>/dev/null; then
      [ -d "$src" ] && rm -rf "$dst"
      python3 -I -c '
import os, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
if os.path.isdir(src) and not os.path.islink(src):
    shutil.copytree(src, dst, symlinks=True, copy_function=shutil.copyfile)
else:
    shutil.copyfile(src, dst)
' "$src" "$dst"
    fi
    rm -rf "$src"
  fi
}

release="$work/release"
mkdir -p "$release"
for part in SKILL.md plugin.json THIRD_PARTY_NOTICES.md assets skills references scripts agents resources docs; do
  [ -e "$work/$part" ] && copy_item "$work/$part" "$release/$part"
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
      move_backup "$dest" "$backup"
    fi
    copy_item "$release" "$dest"
    prune_backups "$keep"
    echo "INSTALLED ${sha:0:12} to $dest ($label)${backup:+, previous kept at $backup}"
  else
    mkdir -p "$dest"
    snap="$keep/$(date +%Y%m%d-%H%M%S)-$$"
    if [ -d "$release/skills/mission" ]; then
      main_skill="mission"
    else
      main_skill="$name"
    fi
    prev_managed=()
    for prev_main in mission "$name"; do
      if [ -f "$dest/$prev_main/.managed-skills" ]; then
        while IFS= read -r mname; do
          [ -n "$mname" ] && prev_managed+=("$mname")
        done < "$dest/$prev_main/.managed-skills"
      fi
    done
    count=0
    has_bundled_skills=0
    for sdir in "$release"/skills/*; do
      [ -d "$sdir" ] || continue
      has_bundled_skills=1
      sname=$(basename "$sdir")
      count=$((count + 1))
      if [ -e "$dest/$sname" ]; then
        mkdir -p "$snap"
        move_backup "$dest/$sname" "$snap/$sname"
        backup="$snap"
      fi
    done
    # Back up any previously managed sibling skills and any prior main orchestrator folder
    for prev_main in mission "$name"; do
      if [ -e "$dest/$prev_main" ]; then
        mkdir -p "$snap"
        move_backup "$dest/$prev_main" "$snap/$prev_main"
        backup="$snap"
      fi
    done
    if [ ${#prev_managed[@]} -gt 0 ]; then
      for sname in "${prev_managed[@]}"; do
        if [ -e "$dest/$sname" ]; then
          mkdir -p "$snap"
          move_backup "$dest/$sname" "$snap/$sname"
          backup="$snap"
        fi
      done
    fi
    # Install main orchestrator + shared runtime into $dest/$main_skill
    mkdir -p "$dest/$main_skill"
    if [ -d "$release/skills/$main_skill" ]; then
      copy_into "$release/skills/$main_skill" "$dest/$main_skill"
    elif [ -f "$release/SKILL.md" ]; then
      copy_item "$release/SKILL.md" "$dest/$main_skill/SKILL.md"
      count=1
    fi
    for part in plugin.json THIRD_PARTY_NOTICES.md assets references scripts agents resources docs VERSION; do
      [ -e "$release/$part" ] && copy_item "$release/$part" "$dest/$main_skill/$part"
    done
    # Install each standalone skill directly into $dest/<skill_name>
    managed=()
    for sdir in "$release"/skills/*; do
      [ -d "$sdir" ] || continue
      sname=$(basename "$sdir")
      [ "$sname" = "$main_skill" ] && continue
      copy_item "$sdir" "$dest/$sname"
      managed+=("$sname")
    done
    if [ ${#managed[@]} -gt 0 ]; then
      printf '%s\n' "${managed[@]}" > "$dest/$main_skill/.managed-skills"
    fi
    prune_backups "$keep"
    echo "INSTALLED ${sha:0:12} to $dest/$main_skill ($count skills in $dest) ($label)${backup:+, previous kept at $backup}"
  fi
done
