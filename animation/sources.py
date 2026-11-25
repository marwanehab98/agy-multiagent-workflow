"""Where cues come from: a recorded progress.log (replay), a live one (follow), or a scripted demo."""
import json
from pathlib import Path

from cues import Cue, parse_line, parse_lines, task_ids

GAP_CAP = 1.2    # seconds: the longest pause a replay keeps between two cues, before --speed


def replay_schedule(lines, speed=1.0, cap=GAP_CAP):
    """[(seconds from start, cue)]: each real gap between cues is capped at `cap`, then divided by speed. The last
    entry is an "end" cue, so the replay lands."""
    cues = parse_lines(lines)
    out, at, prev = [], 1.0, None
    for c in cues:
        if prev is not None:
            gap = c.stamp - prev
            if gap < 0:          # past midnight
                gap += 86400
            at += min(gap, cap) / max(speed, 0.01)
        out.append((at, c))
        prev = c.stamp
    out.append((at + 1.0, Cue("end")))
    return out


def demo_schedule():
    """Every scene in run order, without a log: design, reviews, three tasks (a miss, a hit), landing."""
    steps = [
        (1.0, Cue("director", None, "start")),
        (5.5, Cue("director_done", None, "start", True)),
        (2.0, Cue("design_review", None, "safety")),
        (5.0, Cue("design_result", None, "safety", False)),
        (2.5, Cue("director", None, "safety-done")),
        (5.0, Cue("design_review", None, "safety")),
        (5.0, Cue("design_result", None, "safety", True)),
        (2.5, Cue("probe", None, "claude:opus@high")),
        (2.5, Cue("impl", "01", "impl")),
        (8.0, Cue("settled", "01", "impl", True)),
        (1.0, Cue("gate", "01", None, True)),
        (1.2, Cue("review", "01", 1)),
        (1.5, Cue("verdict", "01", 1, True)),
        (1.2, Cue("review", "01", 2)),
        (1.5, Cue("verdict", "01", 2, True)),
        (1.5, Cue("merged", "01")),
        (3.5, Cue("impl", "02", "impl")),
        (4.0, Cue("settled", "02", "impl", True)),
        (1.0, Cue("gate", "02", None, False)),
        (3.0, Cue("impl", "02", "fix")),
        (2.0, Cue("settled", "02", "fix", True)),
        (1.0, Cue("gate", "02", None, True)),
        (1.2, Cue("review", "02", 1)),
        (1.5, Cue("verdict", "02", 1, True)),
        (1.2, Cue("review", "02", 2)),
        (1.5, Cue("verdict", "02", 2, True)),
        (1.5, Cue("merged", "02")),
        (3.5, Cue("impl", "03", "impl")),
        (4.0, Cue("settled", "03", "impl", False)),
        (2.8, Cue("escalation", "03", 1)),
        (3.0, Cue("director", None, "escalate")),
        (1.5, Cue("escalation_start", "03", 1)),
        (1.0, Cue("impl", "03", "impl")),
        (4.0, Cue("settled", "03", "impl", True)),
        (1.0, Cue("gate", "03", None, True)),
        (1.2, Cue("review", "03", 1)),
        (1.5, Cue("verdict", "03", 1, True)),
        (1.2, Cue("review", "03", 2)),
        (1.5, Cue("verdict", "03", 2, True)),
        (1.5, Cue("merged", "03")),
        (4.0, Cue("final", None, 1)),
        (4.0, Cue("final_verdict", None, 1, True)),
        (2.0, Cue("verify", None, 1, True)),
        (2.0, Cue("drift")),
        (3.0, Cue("drift_result", None, None, True)),
        (2.0, Cue("director", None, "pr")),
        (9.0, Cue("director", None, "report")),
        (3.0, Cue("end")),
    ]
    out, at = [], 0.0
    for gap, cue in steps:
        at += gap
        out.append((at, cue))
    return out


class Follower:
    """Tails a live progress.log: whole lines only, from where the last poll stopped; starts over if the file
    shrinks (a new run in the same place)."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.rest = b""

    def read_lines(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset, self.rest = 0, b""
        if size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
        self.offset += len(data)
        data = self.rest + data
        end = data.rfind(b"\n")
        if end < 0:
            self.rest = data
            return []
        self.rest = data[end + 1:]
        return data[:end + 1].decode("utf-8", errors="replace").splitlines()

    def poll(self):
        return [c for c in (parse_line(ln) for ln in self.read_lines()) if c is not None]

    def waiting_on_user(self):
        """True while the run's state file (next to progress.log) says the run is waiting for the user: the Capsule
        Communicator sets WAITING_ON=user before every turn it ends on a relay or a fixed prompt."""
        try:
            state = json.loads((self.path.parent / "state.json").read_text(encoding="utf-8"))
            return state.get("WAITING_ON") == "user"
        except (OSError, ValueError, AttributeError):
            return False

    def queued_ids(self):
        """Task ids in the run's queue.txt (next to progress.log), when there is one."""
        try:
            return (self.path.parent / "queue.txt").read_text(encoding="utf-8").split()
        except OSError:
            return []


def known_ids(lines):
    return task_ids(parse_lines(lines))
