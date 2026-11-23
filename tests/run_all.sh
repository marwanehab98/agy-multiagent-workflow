#!/bin/bash
# The single test entry point: shell tests, then the Python tests. Fails if either fails.
cd "$(dirname "$0")/.." || exit 1
# Run worktrees default to ~/.local/state/mission-control; the tests use a throwaway folder instead.
MISSION_CONTROL_WORKTREES=$(mktemp -d "${TMPDIR:-/tmp}/mc-test-worktrees.XXXX")
export MISSION_CONTROL_WORKTREES
trap 'rm -rf "$MISSION_CONTROL_WORKTREES"' EXIT
rc=0
bash tests/test_scripts.sh || rc=1
python3 -I -m unittest discover -s tests -p 'test_*.py' || rc=1
exit $rc
