#!/usr/bin/env bash
# Put a worktree back to exactly <sha>: drop commits, tracked edits and untracked files
# (gitignored files such as node_modules are kept). Use only on a worktree whose
# reviewed state is <sha>, after a read-only worker changed it.
# Usage: bash discard-changes.sh <worktree> <sha>
# Exit 0: worktree is clean at <sha>.  Exit 1: still not clean afterwards.  Exit 64: bad usage.
set -u
if [ $# -ne 2 ]; then echo "usage: discard-changes.sh <worktree> <sha>" >&2; exit 64; fi
wt=$1; sha=$2
git -C "$wt" rev-parse "$sha^{commit}" >/dev/null || exit 64
echo "DISCARDING:"
git -C "$wt" status --porcelain --untracked-files=all | sed 's/^/  /'
git -C "$wt" reset --hard "$sha" >/dev/null || exit 1
git -C "$wt" clean -fd >/dev/null || exit 1
bash "$(dirname "$0")/check-tree.sh" "$wt" "$sha"
