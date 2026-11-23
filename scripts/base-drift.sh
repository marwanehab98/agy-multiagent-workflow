#!/usr/bin/env bash
# Before landing: has the target branch moved since the run started? A run can take hours, and a PR opened
# against a base that moved may be stale or already conflict.
# Compares <feature_branch> with origin's <target_branch> (fetched; the local branch when there is no origin
# or the fetch fails).
# Usage: bash base-drift.sh <repo> <target_branch> <feature_branch>
# Exit 0: BASE_CURRENT <sha>             the feature branch already contains the target's head.
# Exit 1: BASE_AHEAD <n> <sha> <where>   the target has <n> commits the feature branch lacks (listed, newest first,
#                                        at most 20); <where> is origin or local.
#         Also prints CONFLICTS when a trial merge (no checkout, nothing written) would conflict.
# Exit 64: bad usage.
set -u
if [ $# -ne 3 ]; then echo "usage: base-drift.sh <repo> <target_branch> <feature_branch>" >&2; exit 64; fi
repo=$1; target=$2; feature=$3
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=15}"
feat=$(git -C "$repo" rev-parse --verify -q "refs/heads/$feature^{commit}") || { echo "usage: no branch $feature" >&2; exit 64; }
where=local
tip=$(git -C "$repo" rev-parse --verify -q "refs/heads/$target^{commit}")
if git -C "$repo" remote get-url origin >/dev/null 2>&1 \
   && git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 -C "$repo" fetch --quiet origin "refs/heads/$target" 2>/dev/null; then
  tip=$(git -C "$repo" rev-parse --verify -q "FETCH_HEAD^{commit}") && where=origin
fi
[ -n "$tip" ] || { echo "usage: no branch $target locally or on origin" >&2; exit 64; }
if git -C "$repo" merge-base --is-ancestor "$tip" "$feat"; then
  echo "BASE_CURRENT $tip"
  exit 0
fi
n=$(git -C "$repo" rev-list --count "$feat..$tip")
echo "BASE_AHEAD $n $tip $where"
git -C "$repo" log --oneline -20 "$feat..$tip" | sed 's/^/  /'
# git >= 2.38: a trial merge that writes no files or refs.
if git -C "$repo" merge-tree --write-tree --no-messages "$feat" "$tip" >/dev/null 2>&1; then
  :
elif [ $? -eq 1 ]; then
  echo "CONFLICTS a merge of $target into $feature would conflict"
fi
exit 1
