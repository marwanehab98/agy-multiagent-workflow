#!/usr/bin/env bash
# The user's "merge locally" choice at the end of a run: fast-forward <target> to <feature>, never anything else.
# - <target> checked out in <repo>'s main checkout: only with no uncommitted changes to tracked files there
#   (untracked files are left alone), by `git merge --ff-only`, so the user's checkout follows the branch.
# - <target> not checked out anywhere: moved by an atomic compare-and-swap (`update-ref`), no checkout.
# Usage: bash merge-local.sh <repo> <target_branch> <feature_branch>
# Exit 0: MERGED <target>=<sha>.
# Exit 2: CHECKOUT_DIRTY / CHECKED_OUT_ELSEWHERE / MERGE_FAILED; nothing changed (prints why and the files).
# Exit 3: NOT_FAST_FORWARD; <target> has commits <feature> lacks; nothing changed.
set -u
if [ $# -ne 3 ]; then echo "usage: merge-local.sh <repo> <target_branch> <feature_branch>" >&2; exit 64; fi
repo=$1; target=$2; feature=$3
ref="refs/heads/$target"
old=$(git -C "$repo" rev-parse --verify -q "$ref") || { echo "usage: no branch $target" >&2; exit 64; }
new=$(git -C "$repo" rev-parse --verify -q "refs/heads/$feature") || { echo "usage: no branch $feature" >&2; exit 64; }

if ! git -C "$repo" merge-base --is-ancestor "$old" "$new"; then
  echo "NOT_FAST_FORWARD $target has commits $feature lacks:"
  git -C "$repo" log --oneline "$new..$old" | sed 's/^/  /'
  exit 3
fi

top=$(git -C "$repo" rev-parse --show-toplevel)
holder=$(git -C "$repo" worktree list --porcelain | awk -v r="$ref" '/^worktree /{w=substr($0,10)} $0=="branch "r{print w}')
if [ -z "$holder" ]; then
  git -C "$repo" update-ref -m "orca-multiagent-workflow: user merged $feature" "$ref" "$new" "$old" \
    || { echo "MERGE_FAILED $target moved during the update"; exit 2; }
  echo "MERGED $target=$new"
  exit 0
fi
if [ "$holder" != "$top" ]; then
  echo "CHECKED_OUT_ELSEWHERE $target is checked out in $holder; merge there, or switch it to another branch first."
  exit 2
fi
dirty=$(git -C "$holder" status --porcelain --untracked-files=no)
if [ -n "$dirty" ]; then
  echo "CHECKOUT_DIRTY $holder has uncommitted changes; commit or stash them, then ask again:"
  echo "$dirty" | sed 's/^/  /'
  exit 2
fi
if ! out=$(git -C "$holder" merge --ff-only -q "$feature" 2>&1); then
  echo "MERGE_FAILED git merge --ff-only refused; nothing changed:"
  echo "$out" | sed 's/^/  /'
  exit 2
fi
echo "MERGED $target=$new"
exit 0
