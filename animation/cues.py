"""Turn progress.log lines into scene cues for the animation engine.

The [progress] milestone patterns are imported from scripts/watch.py, so a format change there is picked up here.
Only the [worker] start/done line shapes are defined locally (watch.py matches those inline, not as constants).
Lines that match nothing give no cue and are ignored.
"""
import re
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))  # -I drops script dirs from sys.path
import watch  # noqa: E402

# kind: what happened. task: the task id ("03"), or None. arg: a stage number, a job name, a review kind, etc.
# ok: True / False for outcomes, None otherwise. stamp: seconds since midnight from the line, or None.
Cue = namedtuple("Cue", "kind task arg ok stamp")
Cue.__new__.__defaults__ = (None, None, None, None)

_WORKER_START = re.compile(r"^\[worker\] start (\S+) \(")
_WORKER_DONE = re.compile(r"^\[worker \+\d+s\] (\S+): done \((\w+)\)")
_PROBE = re.compile(r"^probe (\S+): launch style \S+ works$")
_DIRECTOR_TAG = re.compile(r"^director-(.+)-\d+$")


def _stamp(hhmmss):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return h * 3600 + m * 60 + s


def _director_job(tag):
    m = _DIRECTOR_TAG.match(tag)
    return m.group(1) if m else None


def parse_line(line):
    """One progress.log line -> a Cue, or None when the line is not one the animation shows."""
    line = line.rstrip("\r\n")
    m = re.match(r"^(\d{2}:\d{2}:\d{2}) (.*)$", line)
    if not m:
        return None
    try:
        stamp = _stamp(m.group(1))
    except ValueError:
        return None
    rest = m.group(2)
    if rest.startswith("[progress] "):
        cue = _progress(rest[len("[progress] "):])
    else:
        cue = _worker(rest)
    return cue._replace(stamp=stamp) if cue else None


def _worker(rest):
    m = _WORKER_START.match(rest)
    if m:
        job = _director_job(m.group(1))
        return Cue("director", None, job) if job else None
    m = _WORKER_DONE.match(rest)
    if m:
        job = _director_job(m.group(1))
        return Cue("director_done", None, job, m.group(2) == "succeeded") if job else None
    return None


def _progress(ev):
    w = watch
    m = w._MERGED.match(ev)
    if m:
        return Cue("merged", m[1])
    m = w._STARTED.match(ev)
    if m:
        return Cue("impl", m[1], m[2])
    m = w._RESUME_FAILED.match(ev)
    if m:
        return Cue("impl", m[1], m[2])
    m = w._SETTLED.match(ev)
    if m:
        return Cue("settled", m[1], m[2], m[3] == "succeeded")
    m = w._GATE.match(ev)
    if m:
        return Cue("gate", m[1], None, m[2] == "0")
    m = w._REVIEW_STARTED.match(ev)
    if m:
        return Cue("review", m[1], int(m[2]))
    m = w._REVIEW_VERDICT.match(ev)
    if m:
        return Cue("verdict", m[1], int(m[2]), m[3] == "PASS")
    m = w._REVIEW_VOID.match(ev)
    if m:
        return Cue("void", m[1], int(m[2]))
    m = w._ESCALATION_STARTED.match(ev)
    if m:
        return Cue("escalation_start", m[1], int(m[2]))
    m = w._ESCALATION.match(ev)
    if m:
        return Cue("escalation", m[1], int(m[2]))
    m = w._REPLANNED.match(ev)
    if m:
        return Cue("replanned", m[1], tuple(m[2].split()))
    m = w._DESIGN_STARTED.match(ev)
    if m:
        return Cue("design_review", None, m[1])
    m = w._DESIGN_DONE.match(ev)
    if m:
        return Cue("design_result", None, m[1], True)
    m = w._DESIGN_FAILED.match(ev)
    if m:
        return Cue("design_result", None, m[1], False)
    m = w._FINAL_STARTED.match(ev)
    if m:
        return Cue("final", None, int(m[1]))
    m = w._FINAL_VERDICT.match(ev)
    if m:
        return Cue("final_verdict", None, int(m[1]), m[2] == "PASS")
    m = w._FINAL_VOID.match(ev)
    if m:
        return Cue("final_void")
    m = w._VERIFY.match(ev)
    if m:
        return Cue("verify", None, int(m[1]), m[3] == "0")
    m = w._DRIFT_STARTED.match(ev)
    if m:
        return Cue("drift")
    m = w._DRIFT_DONE.match(ev)
    if m:
        return Cue("drift_result", None, None, True)
    m = w._DRIFT_FAILED.match(ev)
    if m:
        return Cue("drift_result", None, None, False)
    m = _PROBE.match(ev)
    if m:
        return Cue("probe", None, m[1])
    return None


def parse_lines(lines):
    return [c for c in (parse_line(ln) for ln in lines) if c is not None]


def task_ids(cues):
    """Task ids in order of first appearance, without ids that were replanned into others."""
    seen, dropped = [], set()
    for c in cues:
        if c.task and c.task not in seen:
            seen.append(c.task)
        if c.kind == "replanned":
            dropped.add(c.task)
            for t in c.arg:
                if t not in seen:
                    seen.append(t)
    return [t for t in seen if t not in dropped]
