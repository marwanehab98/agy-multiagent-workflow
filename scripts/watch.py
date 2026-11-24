"""Wait for news worth a turn from the long call running on a run, for the Capsule Communicator to show the user.

Usage:
  python3 -I watch.py --state <RUN_DIR>/state.json [--wait 600] [--batch 20]

The Capsule Communicator starts this in the background next to the call, and its harness wakes it when this script
exits. Every wake-up is a model turn, so the script exits only when:
  - a task merged, or something did not go the routine way (a failed gate, a reviewer verdict other than PASS, an
    escalation, a worker that stopped, timed out or was interrupted, any milestone not listed as routine in ROUTINE), and --batch
    more seconds have passed for lines that follow;
  - the call has ended;
  - --wait seconds have passed without either (--wait 0: no such limit).

Every loop script records the call it is running in <RUN_DIR>/.call (drive() in common.py): its id, pid, where
progress.log ended when it began, and whether it has ended. This script reads progress.log from where the previous
watch stopped (or from the call's start, for a call not watched before) and prints:
  - the note lines: every [progress] milestone since the last watch, routine or not, and each worker that stopped,
    timed out or failed to launch. After --wait quiet seconds it adds the worker's latest line (what it last said,
    else its last line of any kind), so the user sees the run is alive. Lines are printed exactly as progress.log has
    them. Tool calls and what workers say are otherwise left out.
  - then one status line, always last:
      RUNNING   the call is still running: start this script again;
      ENDED     the call has finished (its own status line is in the call's output, not here);
      WATCHING  another watch is already running on this run: nothing to do.
It follows one call: when a new call replaces the one it watches, it prints that call's last lines and ENDED, and
leaves the new call to the next watch. A call not watched before shows at most its last BACKLOG note lines. If the
last watched call has already ended, it waits up to START_GRACE seconds for a new call to begin before printing
ENDED, so it can run right after the call is started in the background.
Exit 0, or 64 on bad usage.
"""
import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

MILESTONE = re.compile(r"\[progress\] ")
# Milestones of a call going the routine way: shown with the next news, never worth a turn of their own.
# ("fail" is ruled out only where no path follows: a path may hold the word, "... resume failed; a fresh worker is
# dispatched <pair>" must not pass as routine.)
ROUTINE = re.compile(r"\[progress\] ((?!.*fail).* (dispatched|reused) \S+|.* settled succeeded( .*)?|.* exit=0( .*)?|"
                     r".* VERDICT: PASS( .*)?|probe .* (works|launch styles)|(\S+|final) prompt \S+: \S+|"
                     r"docs worktree moved by a flight director job: .*)$")
STOPPED = re.compile(r"\[worker \+\d+s\] \S+: (stopped|timed out|launch failed|usage limit|resume failed|"
                     r"interrupted)")
SAYS = re.compile(r"\[worker \+\d+s\] \S+: (says: |done \()")
BACKLOG = 15
START_GRACE = 30
OLD_GRACE = 5
LOCK_GRACE = 5
TICK = 1.0


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path, data):
    """Write path via a temp file of its own (two watchers never share one) and a rename; removed on failure."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OSError):
        return False
    return True


def running(call):
    return bool(call) and not call.get("ended") and alive(call.get("pid"))


def shows(line):
    return bool(MILESTONE.search(line) or STOPPED.search(line))


def wakes(line):
    return shows(line) and not ROUTINE.search(line)


def new_lines(log, offset, limit=None):
    """Complete lines of progress.log after byte offset (and before byte limit, if given); returns (lines, new
    offset)."""
    try:
        size = log.stat().st_size
    except OSError:
        return [], offset
    if limit is not None:
        size = min(size, limit)
    if size < offset:
        if limit is not None:
            return [], offset
        offset = 0
    if size == offset:
        return [], offset
    with log.open("rb") as f:
        f.seek(offset)
        data = f.read(size - offset)
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    return data[:end + 1].decode("utf-8", errors="replace").splitlines(), offset + end + 1


def take_lock(fh):
    """The one-watch-per-run flock on fh. A watch that is about to exit holds it for a moment more, so try for up to
    LOCK_GRACE seconds."""
    start = time.monotonic()
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() - start >= LOCK_GRACE:
                return False
            time.sleep(TICK)


def watch(run_dir, wait, batch):
    with (run_dir / ".watch.lock").open("a+", encoding="utf-8") as fh:
        if not take_lock(fh):
            return [], "WATCHING"
        return follow(run_dir, wait, batch)


def follow(run_dir, wait, batch):
    call_file, pos_file, log = run_dir / ".call", run_dir / ".watch.pos", run_dir / "progress.log"
    start = time.monotonic()
    pos = read_json(pos_file) or {}
    call = read_json(call_file)
    # Run right after a call was started in the background, the new call may not have recorded itself yet: while the
    # recorded call is not running, wait for a new one, up to START_GRACE seconds when the recorded one was already
    # watched to its end, or OLD_GRACE seconds when it ended without being watched (then show how it ended).
    def shown(c):
        return not c or (c.get("id") == pos.get("id") and pos.get("ended"))

    while not running(call) and time.monotonic() - start < (START_GRACE if shown(call) else OLD_GRACE):
        time.sleep(TICK)
        call = read_json(call_file)
    if shown(call):
        return [], "ENDED"
    fresh = call.get("id") != pos.get("id")
    offset = int(call.get("offset", 0)) if fresh else int(pos.get("offset", 0))
    seen, woke = [], None
    while True:
        # A new call in .call means the watched one is over: read up to where the new one began, and stop.
        now_call = read_json(call_file) or call
        replaced = now_call.get("id") != call.get("id")
        if not replaced:
            call = now_call
        ended = replaced or not running(call)
        lines, offset = new_lines(log, offset, int(now_call.get("offset", offset)) if replaced else None)
        seen += lines
        if woke is None and any(wakes(ln) for ln in lines):
            woke = time.monotonic()
        now = time.monotonic()
        if ended or (wait > 0 and now - start >= wait) or (woke is not None and now - woke >= batch):
            break
        time.sleep(TICK)
    write_json(pos_file, {"id": call.get("id"), "offset": offset, "ended": ended})
    keep = {i for i, ln in enumerate(seen) if shows(ln)}
    if not ended and woke is None:
        # Nothing worth a turn in --wait seconds: add the worker's latest line as a sign of life.
        said = [i for i, ln in enumerate(seen) if SAYS.search(ln)]
        other = [i for i in range(len(seen)) if i not in keep]
        keep.update((said or other)[-1:])
    notes = [ln for i, ln in enumerate(seen) if i in keep]
    if not ended and not notes:
        notes = [f"(nothing new in progress.log for {wait / 60:g} minutes)"]
    if fresh and len(notes) > BACKLOG:
        notes = [f"({len(notes) - BACKLOG} earlier lines left out)"] + notes[-BACKLOG:]
    return notes, "ENDED" if ended else "RUNNING"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True)
    ap.add_argument("--wait", type=float, default=600)
    ap.add_argument("--batch", type=float, default=20)
    try:
        a = ap.parse_args()
    except SystemExit:
        return 64
    state = Path(a.state)
    if not state.is_file():
        print(f"ERROR MISSING_FILE {state}")
        return 64
    notes, status = watch(state.resolve().parent, a.wait, a.batch)
    for line in notes:
        print(line)
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
