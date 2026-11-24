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
    else its last line of any kind), so the user sees the run is alive. Notes are formatted as a short Markdown
    block (a bold header for why it woke up, followed by one concise bullet per line with <RUN_DIR>/ prefixes
    stripped and SHAs shortened, falling back to the raw text for unrecognized lines; a quiet watch with no
    lines at all is the header alone). Tool calls and what workers say are otherwise left out.
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
STOPPED = re.compile(r"\[worker \+\d+s\] (\S+): (stopped|timed out|launch failed|usage limit|resume failed|"
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
    raw_notes = [ln for i, ln in enumerate(seen) if i in keep]
    if not raw_notes:
        notes = [] if ended else [f"**Still running (nothing new in progress.log for {wait / 60:g} minutes)**"]
    else:
        # The header is chosen from every note, before a long backlog is cut.
        notes = [format_header(raw_notes, ended, woke, wait)]
        if fresh and len(raw_notes) > BACKLOG:
            notes.append(f"- ({len(raw_notes) - BACKLOG} earlier lines left out)")
            raw_notes = raw_notes[-BACKLOG:]
        notes += [format_line(ln, run_dir) for ln in raw_notes]
    return notes, "ENDED" if ended else "RUNNING"


_KIND_LABELS = {"impl": "implementation", "fix": "fix round", "commit": "commit round"}
_PROGRESS_LINE = re.compile(r"^(\d{2}:\d{2}):\d{2} (.*)$")
_SHA = r"[0-9a-f]{7,40}"
_VERDICT = r"(?:VERDICT: (PASS|FIX)|no verdict)"
# One pattern per [progress] event, shared by the header table and the bullet table below.
_MERGED = re.compile(rf"^(\S+) merged ({_SHA})(?: \(([^)]+)\))?$")
_STARTED = re.compile(r"^(\S+) (impl|fix|commit) (dispatched|reused) (\S+)$")
_RESUME_FAILED = re.compile(r"^(\S+) (impl|fix|commit) resume failed; a fresh worker is dispatched (\S+)$")
_SETTLED = re.compile(r"^(\S+) (impl|fix|commit) settled (succeeded|failed)$")
_GATE = re.compile(r"^(\S+) gate exit=(\d+) (.+)$")
_REVIEW_STARTED = re.compile(r"^(\S+) stage(\d+) reviewer dispatched (\S+)$")
_REVIEW_VERDICT = re.compile(rf"^(\S+) stage(\d+) {_VERDICT} (.+)$")
_REVIEW_VOID = re.compile(r"^(\S+) stage(\d+) reviewer void: (.+)$")
_ESCALATION_STARTED = re.compile(rf"^(\S+) escalation attempt (\d+) on (\S+) from ({_SHA})$")
_ESCALATION = re.compile(r"^(\S+) escalation attempt (\d+): (.+)$")
_REPLANNED = re.compile(rf"^(\S+) replanned into (.+) from ({_SHA})$")
_DESIGN_STARTED = re.compile(r"^design (\S+) dispatched (\S+)$")
_DESIGN_DONE = re.compile(r"^design (\S+) settled succeeded (.+)$")
_DESIGN_FAILED = re.compile(r"^design (\S+) failed: (.+)$")
_FINAL_STARTED = re.compile(r"^final review (\d+) dispatched (\S+)$")
_FINAL_VERDICT = re.compile(rf"^final review (\d+) {_VERDICT} (.+)$")
_FINAL_VOID = re.compile(r"^final reviewer void: (.+)$")
_VERIFY = re.compile(r"^verify (\d+)(?: \[([^\]]+)\])? exit=(\d+) (.+)$")
_DRIFT_STARTED = re.compile(r"^drift check dispatched (\S+)$")
_DRIFT_DONE = re.compile(r"^drift check settled succeeded (.+)$")
_DRIFT_FAILED = re.compile(r"^drift check failed: (.+)$")
_PROMPT = re.compile(r"^(\S+) prompt (\S+): (\S+)$")  # the user's reply to a prompt; (\S+) is a task id or "final"


def _kind(m):
    return _KIND_LABELS[m[2]]


def _result(code):
    return "passed" if code == "0" else f"failed (exit {code})"


def _mark(code):
    return "**PASS**" if code == "0" else f"**FAIL** (exit {code})"


# The header for a wake: what the last waking line was about, without the details its bullet gives.
_HEADERS = (
    (_MERGED, lambda m: f"Task {m[1]} — Merged"),
    (_REVIEW_VERDICT, lambda m: f"Task {m[1]} — Stage {m[2]} review: {m[3] or 'no verdict'}"),
    (_GATE, lambda m: f"Task {m[1]} — Gate {_result(m[2])}"),
    (_ESCALATION_STARTED, lambda m: f"Task {m[1]} — Escalation attempt {m[2]}"),
    (_ESCALATION, lambda m: f"Task {m[1]} — Escalation attempt {m[2]}"),
    (_REPLANNED, lambda m: f"Task {m[1]} — Replanned"),
    (_SETTLED, lambda m: f"Task {m[1]} — {_kind(m).capitalize()} {'finished' if m[3] == 'succeeded' else 'failed'}"),
    (_RESUME_FAILED, lambda m: f"Task {m[1]} — {_kind(m).capitalize()}: resume failed, fresh worker started"),
    (_REVIEW_VOID, lambda m: f"Task {m[1]} — Stage {m[2]} reviewer void"),
    (_FINAL_VERDICT, lambda m: f"Final review {m[1]} — {m[2] or 'no verdict'}"),
    (_FINAL_VOID, lambda m: "Final reviewer void"),
    (_VERIFY, lambda m: f"Verification {_result(m[3])}"),
    (_DESIGN_FAILED, lambda m: f"Design ({m[1]}) failed"),
    (_DRIFT_FAILED, lambda m: "Drift check failed"),
)
# The bullet for one [progress] event; rd is the run directory, cut from the paths.
_BULLETS = (
    (_MERGED, lambda m, rd: f"Task {m[1]}: merged at `{m[2][:7]}`" + (f" ({m[3]})" if m[3] else "")),
    (_STARTED, lambda m, rd: f"Task {m[1]}: {_kind(m)} started (`{m[4]}`)"),
    (_RESUME_FAILED, lambda m, rd: f"Task {m[1]}: {_kind(m)} resume failed; fresh worker started (`{m[3]}`)"),
    (_SETTLED, lambda m, rd: f"Task {m[1]}: {_kind(m)} {'finished' if m[3] == 'succeeded' else '**FAILED**'}"),
    (_GATE, lambda m, rd: f"Task {m[1]}: gate {_mark(m[2])} (`{_short_path(m[3], rd)}`)"),
    (_REVIEW_STARTED, lambda m, rd: f"Task {m[1]}: stage {m[2]} review started (`{m[3]}`)"),
    (_REVIEW_VERDICT, lambda m, rd: f"Task {m[1]}: stage {m[2]} review **{m[3] or 'no verdict'}** "
                                    f"(`{_short_path(m[4], rd)}`)"),
    (_REVIEW_VOID, lambda m, rd: f"Task {m[1]}: stage {m[2]} reviewer **void** ({m[3]})"),
    (_ESCALATION_STARTED, lambda m, rd: f"Task {m[1]}: **escalation attempt {m[2]}** started on `{m[3]}` "
                                        f"from `{m[4][:7]}`"),
    (_ESCALATION, lambda m, rd: f"Task {m[1]}: **escalation attempt {m[2]}** ({m[3]})"),
    (_REPLANNED, lambda m, rd: f"Task {m[1]}: **replanned** into `{m[2]}` from `{m[3][:7]}`"),
    (_DESIGN_STARTED, lambda m, rd: f"Design ({m[1]}) started (`{m[2]}`)"),
    (_DESIGN_DONE, lambda m, rd: f"Design ({m[1]}) finished (`{_short_path(m[2], rd)}`)"),
    (_DESIGN_FAILED, lambda m, rd: f"Design ({m[1]}) **FAILED** (`{_short_path(m[2], rd)}`)"),
    (_FINAL_STARTED, lambda m, rd: f"Final review {m[1]} started (`{m[2]}`)"),
    (_FINAL_VERDICT, lambda m, rd: f"Final review {m[1]}: **{m[2] or 'no verdict'}** (`{_short_path(m[3], rd)}`)"),
    (_FINAL_VOID, lambda m, rd: f"Final reviewer **void** ({m[1]})"),
    (_VERIFY, lambda m, rd: f"Verify {m[1]}" + (f" ({m[2]})" if m[2] else "")
                            + f": {_mark(m[3])} (`{_short_path(m[4], rd)}`)"),
    (_DRIFT_STARTED, lambda m, rd: f"Drift check started (`{m[1]}`)"),
    (_DRIFT_DONE, lambda m, rd: f"Drift check finished (`{_short_path(m[1], rd)}`)"),
    (_DRIFT_FAILED, lambda m, rd: f"Drift check **FAILED** (`{_short_path(m[1], rd)}`)"),
    (_PROMPT, lambda m, rd: ("Final" if m[1] == "final" else f"Task {m[1]}") + f": {m[2]} prompt answered `{m[3]}`"),
)


def _short_path(text, run_dir):
    if not run_dir:
        return text
    rd = Path(run_dir)
    for prefix in dict.fromkeys((f"{rd}/", f"{rd.resolve()}/")):
        text = text.replace(prefix, "")
    return text


def format_header(raw_notes, ended, woke, wait):
    """The bold line saying why the watch woke: its last waking line, the quiet timeout, or the end of the call."""
    if woke is None:
        return "**Call finished**" if ended else f"**Still running ({wait / 60:g}m check-in)**"
    waking = [ln for ln in raw_notes if wakes(ln)]
    last = waking[-1] if waking else ""
    m = _PROGRESS_LINE.match(last)
    rest = m.group(2) if m else last
    if rest.startswith("[progress] "):
        ev = rest[len("[progress] "):]
        for pat, fn in _HEADERS:
            hm = pat.match(ev)
            if hm:
                return f"**{fn(hm)}**"
    wm = STOPPED.match(rest)
    if wm:
        return f"**Worker `{wm.group(1)}` — {wm.group(2)}**"
    return "**Mission update**"


def format_line(line, run_dir):
    """Format one progress.log line as a concise Markdown bullet, falling back to raw text if unrecognized."""
    m = _PROGRESS_LINE.match(line)
    if not m:
        return line
    hhmm, rest = m.group(1), m.group(2)
    if rest.startswith("[progress] "):
        ev = rest[len("[progress] "):]
        return f"- `{hhmm}` {_format_progress(ev, run_dir)}"
    return f"- `{hhmm}` {_format_worker(rest, run_dir)}"


def _format_progress(ev, run_dir):
    for pat, fn in _BULLETS:
        pm = pat.match(ev)
        if pm:
            return fn(pm, run_dir)
    return _short_path(ev, run_dir)


def _format_worker(rest, run_dir):
    wm = re.match(r"^\[worker \+(\d+s)\] (\S+): says: (.+)$", rest)
    if wm:
        return f"`{wm.group(2)}` (+{wm.group(1)}): {wm.group(3)}"
    wm = re.match(r"^\[worker \+(\d+s)\] (\S+): (.+)$", rest)
    if wm:
        return f"`{wm.group(2)}` (+{wm.group(1)}): {_short_path(wm.group(3), run_dir)}"
    wm = re.match(r"^\[worker\] start (\S+) \(([^)]+)\)(?: log=(.+))?$", rest)
    if wm:
        return f"`{wm.group(1)}` started ({wm.group(2)})"
    return _short_path(rest, run_dir)


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
