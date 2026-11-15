#!/usr/bin/env bash
# Check that a branch still points at an expected commit (drift check before dispatch).
# Usage: bash branch-at.sh <repo> <branch> <expected_sha>
# Exit 0: branch is at expected.  Exit 1: branch moved (prints the commits that moved it).
set -u
if [ $# -ne 3 ]; then echo "usage: branch-at.sh <repo> <branch> <expected_sha>" >&2; exit 64; fi
repo=$1; branch=$2; expected=$3
actual=$(git -C "$repo" rev-parse "refs/heads/$branch") || exit 64
want=$(git -C "$repo" rev-parse "$expected^{commit}") || exit 64
if [ "$actual" = "$want" ]; then echo "AT $actual"; exit 0; fi
echo "BRANCH_MOVED branch=$branch expected=$want actual=$actual"
git -C "$repo" log --oneline "$want..$actual" | sed 's/^/  moved by: /'
exit 1
