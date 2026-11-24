#!/usr/bin/env bash
# Fast-forward the feature branch to a reviewed commit without checking it out anywhere.
# The move is an atomic compare-and-swap: it only happens while the branch still points at
# <expected_old>, so it never overwrites commits made outside the run. Never merges any other way.
# Usage: bash merge-task.sh <repo> <feature_branch> <expected_old> <worker_head>
# Exit 0: moved, or already at <worker_head> from an earlier interrupted call (prints the new head).
# Exit 2: the branch is checked out in a worktree, so moving it would desync that checkout; nothing done.
# Exit 3: not a fast-forward, or the branch moved; nothing done (prints what moved it).
set -u
if [ $# -ne 4 ]; then echo "usage: merge-task.sh <repo> <feature_branch> <expected_old> <worker_head>" >&2; exit 64; fi
repo=$1; branch=$2; old=$3; head=$4
ref="refs/heads/$branch"
old=$(git -C "$repo" rev-parse "$old^{commit}") || exit 64
new=$(git -C "$repo" rev-parse "$head^{commit}") || exit 64
actual=$(git -C "$repo" rev-parse "$ref") || exit 64

holder=$(git -C "$repo" worktree list --porcelain | awk -v r="$ref" '/^worktree /{w=substr($0,10)} $0=="branch "r{print w}')
if [ -n "$holder" ]; then
  echo "BRANCH_CHECKED_OUT branch=$branch in=$holder"
  echo "  Switch that checkout to another branch (git -C $holder switch <other>); the run moves $branch itself."
  exit 2
fi
if ! git -C "$repo" merge-base --is-ancestor "$old" "$new"; then
  echo "NOT_FAST_FORWARD branch=$branch target=$new does not contain $old"
  exit 3
fi
if [ "$actual" = "$new" ] && [ "$new" != "$old" ]; then
  # Already moved by an earlier call that died before the run recorded it (resume after a crash).
  echo "MERGED head=$new"
  exit 0
fi
if [ "$actual" != "$old" ]; then
  echo "NOT_FAST_FORWARD branch=$branch expected=$old actual=$actual"
  git -C "$repo" log --oneline "$old..$actual" | sed 's/^/  moved by: /'
  exit 3
fi
git -C "$repo" update-ref -m "mission-control: fast-forward to reviewed $new" "$ref" "$new" "$old" \
  || { echo "NOT_FAST_FORWARD branch=$branch moved during the update"; exit 3; }
echo "MERGED head=$new"
exit 0
