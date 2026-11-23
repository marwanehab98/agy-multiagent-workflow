#!/bin/bash
# The single test entry point: the shell tests and each Python test module, in parallel. Fails if any fails.
# TEST_JOBS sets how many run at once (default: the CPU count, at most 4). Works on bash 3.2 without GNU tools.
cd "$(dirname "$0")/.." || exit 1
out=$(mktemp -d "${TMPDIR:-/tmp}/mc-tests.XXXX")
pids=""
# Everything a run creates is under $out, so removing it leaves nothing behind. On Ctrl-C or TERM, stop the jobs
# first; a process a test left running cannot stop the removal (errors ignored, nothing waited on).
trap 'rm -rf "$out" 2>/dev/null; true' EXIT
stop_jobs() { for p in $pids; do pkill -TERM -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done; }
trap 'stop_jobs; exit 130' INT
trap 'stop_jobs; exit 143' TERM
jobs=${TEST_JOBS:-}
if [ -z "$jobs" ]; then
  jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
  [ "$jobs" -gt 4 ] 2>/dev/null && jobs=4
fi
[ "$jobs" -ge 1 ] 2>/dev/null || jobs=1
start=$(date +%s)

# One job per module; each gets its own run-worktrees folder (they default to ~/.local/state/mission-control) and
# TMPDIR, and keeps its output in $out/<name>.log, with its exit status in $out/<name>.rc.
run_one() {
  name=$1
  wt="$out/$name.worktrees"
  tmp="$out/$name.tmp"
  mkdir -p "$wt" "$tmp"
  if [ "$name" = test_scripts.sh ]; then
    MISSION_CONTROL_WORKTREES="$wt" TMPDIR="$tmp" bash tests/test_scripts.sh > "$out/$name.log" 2>&1
  else
    MISSION_CONTROL_WORKTREES="$wt" TMPDIR="$tmp" python3 -I -m unittest discover -s tests -p "$name" \
      > "$out/$name.log" 2>&1
  fi
  echo $? > "$out/$name.rc"
}

# Largest modules first: they take longest, so the run ends sooner.
names="test_scripts.sh $(cd tests && ls -S test_*.py)"
for name in $names; do
  set -- $pids
  if [ $# -ge "$jobs" ]; then
    wait "$1"
    shift
    pids="$*"
  fi
  run_one "$name" &
  pids="$pids $!"
done
wait

rc=0
failed=""
for name in $names; do
  if [ "$(cat "$out/$name.rc" 2>/dev/null)" != 0 ]; then
    rc=1
    failed="$failed $name"
    echo "===== $name FAILED (exit $(cat "$out/$name.rc" 2>/dev/null || echo '?'))"
    cat "$out/$name.log"
  fi
done
py=$(cat "$out"/test_*.py.log | awk '/^Ran [0-9]+ tests? in/ {n += $2} END {print n + 0}')
sh_checks=$(grep -c '^PASS ' "$out/test_scripts.sh.log")
echo "TOTAL $py Python tests run, $sh_checks shell checks passed, $(( $(date +%s) - start ))s with $jobs jobs:" \
  "$([ $rc = 0 ] && echo OK || echo "FAILED$failed")"
exit $rc
