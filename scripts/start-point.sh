#!/usr/bin/env bash
# Choose the commit the whole run starts from, after fetching, so the design and the code
# are based on the same, current commit.
# Usage: bash start-point.sh <repo> <base_branch>
# Prints `START <sha> <how>`, where <how> is one of:
#   up-to-date | local-behind (origin's commit is used) | local-ahead (local's commit is used)
#   | local-only (origin exists, branch not on origin) | no-remote | fetch-failed (local's commit is used)
# Exit 0: chosen.  Exit 3: local and origin have diverged; nothing chosen.
# Exit 64: bad usage, or NO_BRANCH <base_branch> when there is no local branch of that name.
set -u
if [ $# -ne 2 ]; then echo "usage: start-point.sh <repo> <base_branch>" >&2; exit 64; fi
repo=$1; branch=$2
# Never wait on a password prompt or a dead network: fail fast and fall back to the local commit.
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=15}"
net=(-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30)
local_sha=$(git -C "$repo" rev-parse "refs/heads/$branch^{commit}" 2>/dev/null) || { echo "NO_BRANCH $branch"; exit 64; }
if ! git -C "$repo" remote get-url origin >/dev/null 2>&1; then echo "START $local_sha no-remote"; exit 0; fi
git "${net[@]}" -C "$repo" ls-remote --exit-code origin "refs/heads/$branch" >/dev/null 2>&1
ls_rc=$?
if [ "$ls_rc" -eq 2 ]; then echo "START $local_sha local-only"; exit 0; fi
if [ "$ls_rc" -ne 0 ]; then echo "START $local_sha fetch-failed"; exit 0; fi
if ! git "${net[@]}" -C "$repo" fetch --quiet origin "refs/heads/$branch" 2>/dev/null; then echo "START $local_sha fetch-failed"; exit 0; fi
# FETCH_HEAD, not refs/remotes/origin/<branch>: a single-branch clone or a custom fetch refspec may not update that.
remote_sha=$(git -C "$repo" rev-parse "FETCH_HEAD^{commit}" 2>/dev/null) || { echo "START $local_sha fetch-failed"; exit 0; }
if [ "$local_sha" = "$remote_sha" ]; then echo "START $local_sha up-to-date"; exit 0; fi
if git -C "$repo" merge-base --is-ancestor "$local_sha" "$remote_sha"; then echo "START $remote_sha local-behind"; exit 0; fi
if git -C "$repo" merge-base --is-ancestor "$remote_sha" "$local_sha"; then echo "START $local_sha local-ahead"; exit 0; fi
echo "DIVERGED local=$local_sha origin=$remote_sha"
exit 3
