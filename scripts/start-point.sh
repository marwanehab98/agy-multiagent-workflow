#!/usr/bin/env bash
# Choose the commit the whole run starts from, after fetching, so the design and the code
# are based on the same, current commit.
# Usage: bash start-point.sh <repo> <default_branch>
# Prints `START <sha> <how>`, where <how> is one of:
#   up-to-date | local-behind (origin's commit is used) | local-ahead (local's commit is used)
#   | no-remote | fetch-failed (local's commit is used)
# Exit 0: chosen.  Exit 3: local and origin have diverged; nothing chosen.  Exit 64: bad usage.
set -u
if [ $# -ne 2 ]; then echo "usage: start-point.sh <repo> <default_branch>" >&2; exit 64; fi
repo=$1; branch=$2
local_sha=$(git -C "$repo" rev-parse "refs/heads/$branch^{commit}" 2>/dev/null) || { echo "NO_BRANCH $branch"; exit 64; }
if ! git -C "$repo" remote get-url origin >/dev/null 2>&1; then echo "START $local_sha no-remote"; exit 0; fi
if ! git -C "$repo" fetch --quiet origin "$branch" 2>/dev/null; then echo "START $local_sha fetch-failed"; exit 0; fi
remote_sha=$(git -C "$repo" rev-parse "refs/remotes/origin/$branch^{commit}" 2>/dev/null) || { echo "START $local_sha no-remote"; exit 0; }
if [ "$local_sha" = "$remote_sha" ]; then echo "START $local_sha up-to-date"; exit 0; fi
if git -C "$repo" merge-base --is-ancestor "$local_sha" "$remote_sha"; then echo "START $remote_sha local-behind"; exit 0; fi
if git -C "$repo" merge-base --is-ancestor "$remote_sha" "$local_sha"; then echo "START $local_sha local-ahead"; exit 0; fi
echo "DIVERGED local=$local_sha origin=$remote_sha"
exit 3
