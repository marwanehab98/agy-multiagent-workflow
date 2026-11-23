"""Unit tests for scripts/watch.py (the live view of a running call) and the call record drive() keeps for it."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import watch  # noqa: E402

LINES = [
    "21:35:32 [worker] start 02-implement (claude pid=1) log=/x/02-implement-1.log",
    "21:35:36 [worker +5s] 02-implement: tool: Skill",
    "21:36:11 [worker +39s] 02-implement: says: Reading the design docs done; now writing the tests.",
    "21:37:17 [worker +105s] 02-implement: still running (no output for 30s)...",
    "21:38:05 [worker +154s] 02-implement: says: RED confirmed. Now GREEN. | tool: Bash (yarn test)",
    "21:40:00 [worker +200s] 02-implement: tool error: No such file",
    "21:41:07 [worker +260s] 02-implement: done (succeeded) pair=claude:opus@high cost=$0.93 turns=14",
    "21:41:07 [progress] 02 stage1 VERDICT: PASS /x/02-s1-1.md",
]
NOTES = [LINES[0], LINES[2], LINES[4], LINES[6], LINES[7]]


class Watch(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.log = self.d / "progress.log"
        self.log.write_text("09:00:00 [progress] an earlier call\n")
        self.sleeper = subprocess.Popen(["sleep", "60"])
        patches = [mock.patch.object(watch, "TICK", 0.05), mock.patch.object(watch, "START_GRACE", 0.3),
                   mock.patch.object(watch, "OLD_GRACE", 0.2)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.sleeper.kill()
        self.sleeper.wait()

    def record(self, cid="c1", ended=False, pid=None):
        (self.d / ".call").write_text(json.dumps({"id": cid, "pid": pid or self.sleeper.pid,
                                                  "offset": self.log.stat().st_size, "ended": ended}))

    def append(self, lines):
        with self.log.open("a") as f:
            f.write("".join(f"{ln}\n" for ln in lines))

    def test_prints_only_notes_of_the_running_call(self):
        self.record()
        self.append(LINES)
        notes, status = watch.watch(self.d, wait=2, batch=0.1)
        self.assertEqual(notes, NOTES)
        self.assertEqual(status, "RUNNING")
        # The next watch starts where this one stopped.
        self.append(["21:42:00 [worker +5s] 02-s1-1: says: Reviewing."])
        notes, status = watch.watch(self.d, wait=1, batch=0.1)
        self.assertEqual((notes, status), (["21:42:00 [worker +5s] 02-s1-1: says: Reviewing."], "RUNNING"))

    def test_quiet_call_returns_running_after_wait(self):
        self.record()
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1), ([], "RUNNING"))

    def test_ended_call_drains_then_waits_for_a_new_one(self):
        self.record()
        watch.watch(self.d, wait=0.2, batch=0.1)
        self.append(LINES[-2:])
        self.record(cid="c1", ended=True)
        notes, status = watch.watch(self.d, wait=2, batch=1)
        self.assertEqual((notes, status), (LINES[-2:], "ENDED"))
        # Already shown to its end and no new call starts: ENDED, nothing repeated.
        self.assertEqual(watch.watch(self.d, wait=2, batch=1), ([], "ENDED"))

    def test_waits_for_the_next_call_to_record_itself(self):
        self.record(cid="c1", ended=True)
        watch.watch(self.d, wait=1, batch=0.1)
        timer = threading.Timer(0.1, lambda: (self.record(cid="c2"), self.append(LINES[:1])))
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertEqual(watch.watch(self.d, wait=1, batch=0.1), ([LINES[0]], "RUNNING"))

    def test_dead_pid_counts_as_ended(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        self.record(pid=dead.pid)
        self.append(LINES[:3])
        self.assertEqual(watch.watch(self.d, wait=2, batch=1), ([LINES[0], LINES[2]], "ENDED"))

    def test_partial_line_waits_for_its_end(self):
        self.record()
        with self.log.open("a") as f:
            f.write("21:50:00 [progress] half")
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1), ([], "RUNNING"))
        with self.log.open("a") as f:
            f.write(" a line\n")
        self.assertEqual(watch.watch(self.d, wait=1, batch=0.1), (["21:50:00 [progress] half a line"], "RUNNING"))

    def test_long_backlog_of_an_unwatched_call_is_cut(self):
        self.record()
        self.append([f"10:00:{i:02d} [progress] event {i}" for i in range(40)])
        notes, _ = watch.watch(self.d, wait=1, batch=0.1)
        self.assertEqual(notes[0], f"({40 - watch.BACKLOG} earlier lines left out)")
        self.assertEqual(notes[1:], [f"10:00:{i:02d} [progress] event {i}" for i in range(40 - watch.BACKLOG, 40)])

    def test_cli(self):
        state = self.d / "state.json"
        state.write_text(json.dumps({"RUN_DIR": str(self.d)}))
        self.record()
        self.append(LINES[:3])
        out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "watch.py"), "--state", str(state),
                              "--wait", "1", "--batch", "0.2"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.splitlines(), [LINES[0], LINES[2], "RUNNING"])
        bad = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "watch.py"), "--state",
                              str(self.d / "nope.json")], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 64)


class CallRecord(unittest.TestCase):
    def test_drive_records_the_call_and_marks_it_ended(self):
        d = Path(tempfile.mkdtemp())
        state = d / "state.json"
        state.write_text(json.dumps({"RUN_DIR": str(d)}))
        (d / "progress.log").write_text("x\n")
        seen = {}

        def body(events):
            seen.update(json.loads((d / ".call").read_text()))
            return 0, "DONE"

        self.assertEqual(common.drive(state, body), 0)
        self.assertEqual((seen["pid"], seen["offset"], seen["ended"]), (os.getpid(), 2, False))
        after = json.loads((d / ".call").read_text())
        self.assertEqual((after["id"], after["ended"]), (seen["id"], True))


if __name__ == "__main__":
    unittest.main()
