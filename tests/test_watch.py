"""Unit tests for scripts/watch.py (news from a running call) and the call record drive() keeps for it."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import watch  # noqa: E402
from helpers import removed_after  # noqa: E402

LINES = [
    "21:35:30 [progress] 02 impl dispatched claude:opus@high",
    "21:35:32 [worker] start 02-implement (claude pid=1) log=/x/02-implement-1.log",
    "21:35:36 [worker +5s] 02-implement: tool: Skill",
    "21:36:11 [worker +39s] 02-implement: says: Reading the design docs done; now writing the tests.",
    "21:37:17 [worker +105s] 02-implement: still running (no output for 30s)...",
    "21:40:00 [worker +200s] 02-implement: tool error: No such file",
    "21:41:07 [worker +260s] 02-implement: done (succeeded) pair=claude:opus@high cost=$0.93 turns=14",
    "21:41:07 [progress] 02 impl settled succeeded",
    "21:41:30 [progress] 02 gate exit=0 /x/02-1.log",
    "21:42:07 [progress] 02 stage1 VERDICT: PASS /x/02-s1-1.md",
    "21:43:07 [progress] 02 stage2 VERDICT: PASS /x/02-s2-1.md",
    "21:43:07 [progress] 02 merged 1a1be7313a3b73b8ae50700bbd4fbee1a504d0c5 (repos/backend)",
]
MILESTONES = [ln for ln in LINES if "[progress]" in ln]


class Lines(unittest.TestCase):
    def test_routine_milestones_never_wake(self):
        for event in ("02 impl dispatched claude:opus@medium", "02 fix reused claude:opus@medium",
                      "02 stage1 reviewer dispatched claude:sonnet@high", "02 impl settled succeeded",
                      "02 gate exit=0 /x/02-1.log", "02 stage2 VERDICT: PASS /x/02-s2-1.md",
                      "design safety settled succeeded /x/safety-1.md", "final review 1 VERDICT: PASS /x/final-1.md",
                      "verify 1 [repos/backend] exit=0 /x/verify-1.log", "probe claude:opus@high: launch style flag works",
                      "probe resume: harness versions unchanged; reusing recorded launch styles",
                      # A topic or repo name with "fail" in it is in every path.
                      "02 gate exit=0 /wt/fix-baseline-failures-docs/.mission/gates/02-1.log",
                      "02 stage1 VERDICT: PASS /wt/login-failover-docs/.mission/verdicts/02-s1-1.md",
                      "design lens settled succeeded /wt/fix-failing-tests-docs/.mission/lens-1.md",
                      "verify 1 [repos/failover] exit=0 /wt/failover-docs/.mission/verify-1.log",
                      # The user's own reply to a prompt, and bookkeeping.
                      "02 prompt usage-limit: retry", "final prompt verify-failed: accept",
                      "docs worktree moved by a flight director job: DOCS_HEAD now 1a1be73"):
            line = f"10:00:00 [progress] {event}"
            self.assertTrue(watch.shows(line), event)
            self.assertFalse(watch.wakes(line), event)

    def test_merges_and_trouble_wake(self):
        for event in ("02 merged 1a1be73 (repos/backend)", "02 gate exit=1 /x/02-1.log", "02 impl settled failed",
                      "02 stage1 VERDICT: FIX /x/02-s1-1.md", "02 stage2 no verdict /x/02-s2-1.md",
                      "02 stage1 reviewer void: no verdict line", "02 escalation attempt 1: next rung claude:opus@max",
                      "02 impl resume failed; a fresh worker is dispatched claude:opus@high",
                      "verify 1 exit=2 /x/verify-1.log", "design safety failed: /x/safety-1.md",
                      "probe claude:opus@high: launch style flag failed", "an event added later"):
            self.assertTrue(watch.wakes(f"10:00:00 [progress] {event}"), event)
        for line in ("10:00:00 [worker +3600s] 02-implement: timed out after 3600s",
                     "10:00:00 [worker +5s] 02-implement: stopped rc=1",
                     "10:00:00 [worker +42s] 02-implement: interrupted",
                     "10:00:00 [worker +0s] 02-implement: launch failed: no such file"):
            self.assertTrue(watch.wakes(line), line)

    def test_worker_chatter_is_not_shown(self):
        for line in (LINES[1], LINES[2], LINES[3], LINES[4], LINES[5], LINES[6]):
            self.assertFalse(watch.shows(line), line)


class Watch(unittest.TestCase):
    def setUp(self):
        self.d = Path(removed_after(self, tempfile.mkdtemp()))
        self.log = self.d / "progress.log"
        self.log.write_text("09:00:00 [progress] an earlier call\n")
        self.sleeper = subprocess.Popen(["sleep", "60"])
        patches = [mock.patch.object(watch, "TICK", 0.05), mock.patch.object(watch, "START_GRACE", 0.3),
                   mock.patch.object(watch, "OLD_GRACE", 0.2), mock.patch.object(watch, "LOCK_GRACE", 0.2)]
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

    def later(self, delay, fn):
        timer = threading.Timer(delay, fn)
        timer.start()
        self.addCleanup(timer.join)
        self.addCleanup(timer.cancel)

    def test_a_merge_wakes_and_brings_the_milestones_before_it(self):
        self.record()
        self.append(LINES)
        started = time.monotonic()
        notes, status = watch.watch(self.d, wait=30, batch=0.1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((notes, status), (MILESTONES, "RUNNING"))
        # The next watch starts where this one stopped.
        self.append(["21:44:00 [progress] 03 gate exit=1 /x/03-1.log"])
        notes, status = watch.watch(self.d, wait=30, batch=0.1)
        self.assertEqual((notes, status), (["21:44:00 [progress] 03 gate exit=1 /x/03-1.log"], "RUNNING"))

    def test_routine_lines_wait_for_the_quiet_timeout_and_come_with_the_workers_latest_words(self):
        self.record()
        self.append(LINES[:6])
        started = time.monotonic()
        notes, status = watch.watch(self.d, wait=0.4, batch=0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.4)
        self.assertEqual((notes, status), ([LINES[0], LINES[3]], "RUNNING"))

    def test_quiet_timeout_without_words_shows_the_latest_line(self):
        # Not the start line, which is as old as the worker.
        self.record()
        self.append([LINES[1], LINES[2], LINES[4]])
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1), ([LINES[4]], "RUNNING"))

    def test_quiet_timeout_right_after_a_start_shows_the_start(self):
        self.record()
        self.append([LINES[1]])
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1), ([LINES[1]], "RUNNING"))

    def test_wait_zero_has_no_quiet_timeout(self):
        self.record()
        self.append(LINES[:6])
        self.later(0.5, lambda: self.append(LINES[-1:]))
        started = time.monotonic()
        notes, status = watch.watch(self.d, wait=0, batch=0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.5)
        self.assertEqual((notes, status), ([LINES[0], LINES[-1]], "RUNNING"))

    def test_quiet_timeout_with_no_lines_says_so(self):
        self.record()
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1),
                         (["(nothing new in progress.log for 0.005 minutes)"], "RUNNING"))

    def test_lines_after_the_wake_are_batched(self):
        self.record()
        self.append(LINES)
        self.later(0.15, lambda: self.append(["21:43:09 [progress] 03 impl dispatched claude:opus@high"]))
        notes, status = watch.watch(self.d, wait=30, batch=0.5)
        self.assertEqual((notes, status),
                         (MILESTONES + ["21:43:09 [progress] 03 impl dispatched claude:opus@high"], "RUNNING"))

    def test_ended_call_drains_then_waits_for_a_new_one(self):
        self.record()
        self.append(LINES[:4])
        self.later(0.2, lambda: (self.append(LINES[4:9]), self.record(cid="c1", ended=True)))
        notes, status = watch.watch(self.d, wait=30, batch=1)
        # No sign-of-life line at the end of a call: only the milestones.
        self.assertEqual((notes, status), ([LINES[0], LINES[7], LINES[8]], "ENDED"))
        # Already shown to its end and no new call starts: ENDED, nothing repeated.
        self.assertEqual(watch.watch(self.d, wait=30, batch=1), ([], "ENDED"))

    def test_waits_for_the_next_call_to_record_itself(self):
        self.record(cid="c1", ended=True)
        watch.watch(self.d, wait=1, batch=0.1)
        self.later(0.1, lambda: (self.record(cid="c2"), self.append(LINES[-1:])))
        self.assertEqual(watch.watch(self.d, wait=30, batch=0.1), ([LINES[-1]], "RUNNING"))

    def test_dead_pid_counts_as_ended(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        self.record(pid=dead.pid)
        self.append(LINES[:4])
        self.assertEqual(watch.watch(self.d, wait=30, batch=1), ([LINES[0]], "ENDED"))

    def test_partial_line_waits_for_its_end(self):
        self.record()
        with self.log.open("a") as f:
            f.write("21:50:00 [progress] 02 merged")
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1)[1], "RUNNING")
        with self.log.open("a") as f:
            f.write(" abc\n")
        self.assertEqual(watch.watch(self.d, wait=30, batch=0.1), (["21:50:00 [progress] 02 merged abc"], "RUNNING"))

    def test_long_backlog_of_an_unwatched_call_is_cut(self):
        self.record()
        self.append([f"10:00:{i:02d} [progress] event {i}" for i in range(40)])
        notes, _ = watch.watch(self.d, wait=30, batch=0.1)
        self.assertEqual(notes[0], f"({40 - watch.BACKLOG} earlier lines left out)")
        self.assertEqual(notes[1:], [f"10:00:{i:02d} [progress] event {i}" for i in range(40 - watch.BACKLOG, 40)])

    def test_a_new_call_ends_the_watch_of_the_old_one(self):
        self.record()
        self.append(LINES[:1])

        def next_call():
            self.append(LINES[7:9])
            self.record(cid="c2")
            self.append(["21:50:00 [progress] final review 1 dispatched claude:opus@high"])

        self.later(0.2, next_call)
        notes, status = watch.watch(self.d, wait=30, batch=1)
        self.assertEqual((notes, status), ([LINES[0], LINES[7], LINES[8]], "ENDED"))
        # The new call's lines are left to the next watch, from that call's start.
        self.append(["21:51:00 [progress] final review 1 VERDICT: FIX /x/final-1.md"])
        notes, status = watch.watch(self.d, wait=30, batch=0.1)
        self.assertEqual((notes, status), (["21:50:00 [progress] final review 1 dispatched claude:opus@high",
                                            "21:51:00 [progress] final review 1 VERDICT: FIX /x/final-1.md"], "RUNNING"))

    def test_second_watch_on_a_run_prints_watching(self):
        self.record()
        first = {}
        t = threading.Thread(target=lambda: first.update(out=watch.watch(self.d, wait=1.5, batch=0.1)))
        t.start()
        self.addCleanup(t.join)
        time.sleep(0.3)
        started = time.monotonic()
        self.assertEqual(watch.watch(self.d, wait=30, batch=0.1), ([], "WATCHING"))
        self.assertLess(time.monotonic() - started, 1)
        t.join()
        self.assertEqual(first["out"][1], "RUNNING")
        # The first one's position is untouched by the second, and the lock is free again.
        self.append(LINES[-1:])
        self.assertEqual(watch.watch(self.d, wait=30, batch=0.1), ([LINES[-1]], "RUNNING"))

    def test_a_watch_that_is_about_to_exit_does_not_block_the_next(self):
        self.record()
        t = threading.Thread(target=lambda: watch.watch(self.d, wait=0.3, batch=0.1))
        t.start()
        self.addCleanup(t.join)
        time.sleep(0.15)
        self.later(0.5, lambda: self.append(LINES[-1:]))
        with mock.patch.object(watch, "LOCK_GRACE", 2):
            self.assertEqual(watch.watch(self.d, wait=30, batch=0.1), ([LINES[-1]], "RUNNING"))

    def test_cli(self):
        state = self.d / "state.json"
        state.write_text(json.dumps({"RUN_DIR": str(self.d)}))
        self.record()
        self.append(LINES)
        out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "watch.py"), "--state", str(state),
                              "--wait", "30", "--batch", "0.2"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.splitlines(), MILESTONES + ["RUNNING"])
        bad = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "watch.py"), "--state",
                              str(self.d / "nope.json")], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 64)


class CallRecord(unittest.TestCase):
    def test_drive_records_the_call_and_marks_it_ended(self):
        d = Path(removed_after(self, tempfile.mkdtemp()))
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
