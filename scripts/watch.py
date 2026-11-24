"""Wait for news from the long call running on a run, for the Capsule Communicator to show the user live.

Usage:
  python3 -I watch.py --state <RUN_DIR>/state.json [--wait 90] [--batch 20]

Every loop script records the call it is running in <RUN_DIR>/.call (drive() in common.py): its id, pid, where
progress.log ended when it began, and whether it has ended. This script reads progress.log from where the previous
watch stopped (or from the call's start, for a call not watched before) and blocks until there is news, then prints:
  - the note lines: [progress] milestones, worker start and finish lines, and what each worker says. Tool calls and
    "still running" lines are left out. Lines are printed exactly as progress.log has them.
  - then one status line, always last:
      RUNNING  the call is still running: run this script again;
      ENDED    the call has finished (its own status line is in the call's output, not here).
It returns once it has notes and has waited --batch more seconds for any that follow, once the call ends, or after
--wait seconds, whichever is first, so one run fits in a shell tool's default timeout. A call not watched before shows
at most its last BACKLOG note lines. If the last watched call has already ended, it waits up to START_GRACE seconds
for a new call to begin before printing ENDED, so it can run right after the call is started in the background.
Exit 0, or 64 on bad usage.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

NOTE = re.compile(r"\[progress\] |\[worker\] start |\[worker \+\d+s\] \S+: (says: |done \(|stopped|timed out|launch "
                  r"failed|usage limit|resume failed)")
BACKLOG = 15
START_GRACE = 30
OLD_GRACE = 5
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


def new_lines(log, offset):
    """Complete lines of progress.log after byte offset; returns (lines, new offset)."""
    try:
        size = log.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset
    with log.open("rb") as f:
        f.seek(offset)
        data = f.read(size - offset)
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    text = data[:end + 1].decode("utf-8", errors="replace")
    return [ln for ln in text.splitlines() if NOTE.search(ln)], offset + end + 1


def watch(run_dir, wait, batch):
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
    notes, first = [], None
    while True:
        ended = not running(call)
        lines, offset = new_lines(log, offset)
        notes += lines
        if notes and first is None:
            first = time.monotonic()
        now = time.monotonic()
        if ended or now - start >= wait or (first is not None and now - first >= batch):
            break
        time.sleep(TICK)
        call = read_json(call_file) or call
    write_json(pos_file, {"id": call.get("id"), "offset": offset, "ended": ended})
    if fresh and len(notes) > BACKLOG:
        notes = [f"({len(notes) - BACKLOG} earlier lines left out)"] + notes[-BACKLOG:]
    return notes, "ENDED" if ended else "RUNNING"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True)
    ap.add_argument("--wait", type=float, default=90)
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
