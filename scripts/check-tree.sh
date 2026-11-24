#!/usr/bin/env bash
# Check that a worktree is exactly at an expected commit with nothing changed.
# Usage: bash check-tree.sh <worktree> <expected_sha> [--allow <path relative to worktree>]...
# Exit 0: HEAD == expected and the only changes (if any) are allowed paths.
# Exit 1: HEAD moved.  Exit 2: unexpected changes (a rename also reports its source path).
# Exit 3: STATUS_FAILED <git's message>; git status failed, so nothing could be checked.  Exit 64: bad usage.
set -u
if [ $# -lt 2 ]; then echo "usage: check-tree.sh <worktree> <expected_sha> [--allow <path>]..." >&2; exit 64; fi
wt=$1; expected=$2; shift 2
allowed=()
while [ $# -gt 0 ]; do
  if [ "$1" = "--allow" ] && [ $# -ge 2 ]; then allowed+=("$2"); shift 2
  else echo "unknown argument: $1" >&2; exit 64; fi
done

head=$(git -C "$wt" rev-parse HEAD) || exit 64
want=$(git -C "$wt" rev-parse "$expected^{commit}") || exit 64
if [ "$head" != "$want" ]; then
  echo "HEAD_MOVED expected=$want actual=$head"
  git -C "$wt" log --oneline "$want..$head" 2>/dev/null | sed 's/^/  new commit: /'
  exit 1
fi

# -z gives unquoted paths; a rename or copy entry is followed by its source path. A rename's source is
# changed too (deleted), so it must be allowed as well; a copy's source is unchanged.
# Read from a file, not a pipe, so a failing `git status` is seen instead of looking like a clean tree.
tmp=$(mktemp -d "${TMPDIR:-/tmp}/check-tree.XXXXXX") || exit 3
trap 'rm -rf "$tmp"' EXIT
if ! git -C "$wt" status --porcelain=v1 -z --untracked-files=all >"$tmp/status" 2>"$tmp/err"; then
  echo "STATUS_FAILED $(tr '\n' ' ' <"$tmp/err" | sed 's/ *$//')"
  exit 3
fi
bad=0
src_of=""
while IFS= read -r -d '' entry; do
  if [ -n "$src_of" ]; then status=$src_of; path=$entry; src_of=""
    case $status in C*) continue ;; esac
  else
    status=${entry:0:2}; path=${entry:3}
    case $status in R*|C*) src_of=$status ;; esac
  fi
  ok=0
  for a in "${allowed[@]+"${allowed[@]}"}"; do [ "$path" = "$a" ] && ok=1; done
  if [ $ok -eq 0 ]; then echo "UNEXPECTED_CHANGE $status $path"; bad=1; fi
done <"$tmp/status"

if [ $bad -ne 0 ]; then exit 2; fi
echo "CLEAN head=$head"
exit 0
