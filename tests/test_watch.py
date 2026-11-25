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

    def test_a_worker_paused_for_a_resume_does_not_wake(self):
        line = "10:00:00 [worker +900s] 02-implement: paused: no activity for 900s; resuming its conversation"
        self.assertFalse(watch.wakes(line))
        self.assertTrue(watch.wakes("10:00:00 [worker +950s] 02-implement: timed out: no activity for 900s"))

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
        self.assertEqual((notes, status),
                         (["**Task 02 — Merged**"]
                          + [watch.format_line(ln, self.d) for ln in MILESTONES],
                          "RUNNING"))
        # The next watch starts where this one stopped.
        err_line = "21:44:00 [progress] 03 gate exit=1 /x/03-1.log"
        self.append([err_line])
        notes, status = watch.watch(self.d, wait=30, batch=0.1)
        self.assertEqual((notes, status),
                         (["**Task 03 — Gate failed (exit 1)**", watch.format_line(err_line, self.d)], "RUNNING"))

    def test_routine_lines_wait_for_the_quiet_timeout_and_come_with_the_workers_latest_words(self):
        self.record()
        self.append(LINES[:6])
        started = time.monotonic()
        notes, status = watch.watch(self.d, wait=0.4, batch=0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.4)
        self.assertEqual((notes, status),
                         (["**Still running (0.00666667m check-in)**",
                           watch.format_line(LINES[0], self.d), watch.format_line(LINES[3], self.d)],
                          "RUNNING"))

    def test_quiet_timeout_without_words_shows_the_latest_line(self):
        # Not the start line, which is as old as the worker.
        self.record()
        self.append([LINES[1], LINES[2], LINES[4]])
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1),
                         (["**Still running (0.005m check-in)**", watch.format_line(LINES[4], self.d)], "RUNNING"))

    def test_quiet_timeout_right_after_a_start_shows_the_start(self):
        self.record()
        self.append([LINES[1]])
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1),
                         (["**Still running (0.005m check-in)**", watch.format_line(LINES[1], self.d)], "RUNNING"))

    def test_wait_zero_has_no_quiet_timeout(self):
        self.record()
        self.append(LINES[:6])
        self.later(0.5, lambda: self.append(LINES[-1:]))
        started = time.monotonic()
        notes, status = watch.watch(self.d, wait=0, batch=0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.5)
        self.assertEqual((notes, status),
                         (["**Task 02 — Merged**",
                           watch.format_line(LINES[0], self.d), watch.format_line(LINES[-1], self.d)],
                          "RUNNING"))

    def test_quiet_timeout_with_no_lines_says_so(self):
        self.record()
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1),
                         (["**Still running (nothing new in progress.log for 0.005 minutes)**"], "RUNNING"))

    def test_lines_after_the_wake_are_batched(self):
        self.record()
        self.append(LINES)
        extra = "21:43:09 [progress] 03 impl dispatched claude:opus@high"
        self.later(0.15, lambda: self.append([extra]))
        notes, status = watch.watch(self.d, wait=30, batch=0.5)
        self.assertEqual((notes, status),
                         (["**Task 02 — Merged**"]
                          + [watch.format_line(ln, self.d) for ln in MILESTONES + [extra]],
                          "RUNNING"))

    def test_ended_call_drains_then_waits_for_a_new_one(self):
        self.record()
        self.append(LINES[:4])
        self.later(0.2, lambda: (self.append(LINES[4:9]), self.record(cid="c1", ended=True)))
        notes, status = watch.watch(self.d, wait=30, batch=1)
        # No sign-of-life line at the end of a call: only the milestones.
        self.assertEqual((notes, status),
                         (["**Call finished**"] + [watch.format_line(ln, self.d) for ln in [LINES[0], LINES[7], LINES[8]]],
                          "ENDED"))
        # Already shown to its end and no new call starts: ENDED, nothing repeated.
        self.assertEqual(watch.watch(self.d, wait=30, batch=1), ([], "ENDED"))

    def test_waits_for_the_next_call_to_record_itself(self):
        self.record(cid="c1", ended=True)
        watch.watch(self.d, wait=1, batch=0.1)
        self.later(0.1, lambda: (self.record(cid="c2"), self.append(LINES[-1:])))
        self.assertEqual(watch.watch(self.d, wait=30, batch=0.1),
                         (["**Task 02 — Merged**", watch.format_line(LINES[-1], self.d)],
                          "RUNNING"))

    def test_dead_pid_counts_as_ended(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        self.record(pid=dead.pid)
        self.append(LINES[:4])
        self.assertEqual(watch.watch(self.d, wait=30, batch=1),
                         (["**Call finished**", watch.format_line(LINES[0], self.d)], "ENDED"))

    def test_partial_line_waits_for_its_end(self):
        self.record()
        with self.log.open("a") as f:
            f.write("21:50:00 [progress] 02 merged")
        self.assertEqual(watch.watch(self.d, wait=0.3, batch=0.1)[1], "RUNNING")
        with self.log.open("a") as f:
            f.write(" abc1234\n")
        self.assertEqual(watch.watch(self.d, wait=30, batch=0.1),
                         (["**Task 02 — Merged**", "- `21:50` Task 02: merged at `abc1234`"], "RUNNING"))

    def test_long_backlog_of_an_unwatched_call_is_cut(self):
        self.record()
        raw = [f"10:00:{i:02d} [progress] event {i}" for i in range(40)]
        self.append(raw)
        notes, _ = watch.watch(self.d, wait=30, batch=0.1)
        self.assertEqual(notes[0], "**Mission update**")
        # A bullet like the lines after it, so Markdown does not join it to the header.
        self.assertEqual(notes[1], f"- ({40 - watch.BACKLOG} earlier lines left out)")
        self.assertEqual(notes[2:], [watch.format_line(ln, self.d) for ln in raw[-watch.BACKLOG:]])

    def test_a_new_call_ends_the_watch_of_the_old_one(self):
        self.record()
        self.append(LINES[:1])

        def next_call():
            self.append(LINES[7:9])
            self.record(cid="c2")
            self.append(["21:50:00 [progress] final review 1 dispatched claude:opus@high"])

        self.later(0.2, next_call)
        notes, status = watch.watch(self.d, wait=30, batch=1)
        self.assertEqual((notes, status),
                         (["**Call finished**"] + [watch.format_line(ln, self.d) for ln in [LINES[0], LINES[7], LINES[8]]],
                          "ENDED"))
        # The new call's lines are left to the next watch, from that call's start.
        self.append(["21:51:00 [progress] final review 1 VERDICT: FIX /x/final-1.md"])
        notes, status = watch.watch(self.d, wait=30, batch=0.1)
        self.assertEqual((notes, status),
                         (["**Final review 1 — FIX**",
                           "- `21:50` Final review 1 started (`claude:opus@high`)",
                           "- `21:51` Final review 1: **FIX** (`/x/final-1.md`)"],
                          "RUNNING"))

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
        self.assertEqual(watch.watch(self.d, wait=30, batch=0.1),
                         (["**Task 02 — Merged**", watch.format_line(LINES[-1], self.d)],
                          "RUNNING"))

    def test_a_watch_that_is_about_to_exit_does_not_block_the_next(self):
        self.record()
        t = threading.Thread(target=lambda: watch.watch(self.d, wait=0.3, batch=0.1))
        t.start()
        self.addCleanup(t.join)
        time.sleep(0.15)
        self.later(0.5, lambda: self.append(LINES[-1:]))
        with mock.patch.object(watch, "LOCK_GRACE", 2):
            self.assertEqual(watch.watch(self.d, wait=30, batch=0.1),
                             (["**Task 02 — Merged**", watch.format_line(LINES[-1], self.d)],
                              "RUNNING"))

    def test_cli(self):
        state = self.d / "state.json"
        state.write_text(json.dumps({"RUN_DIR": str(self.d)}))
        self.record()
        self.append(LINES)
        out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "watch.py"), "--state", str(state),
                              "--wait", "30", "--batch", "0.2"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.splitlines(),
                         ["**Task 02 — Merged**"]
                         + [watch.format_line(ln, self.d.resolve()) for ln in MILESTONES]
                         + ["RUNNING"])
        bad = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "watch.py"), "--state",
                              str(self.d / "nope.json")], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 64)


class Formatting(unittest.TestCase):
    def setUp(self):
        self.rd = Path("/home/u/.local/state/mission-control/worktrees/app/topic-docs/.mission")

    def test_format_line_all_milestones_and_workers(self):
        rd = self.rd
        cases = [
            (f"14:04:20 [progress] 05 merged 2e21deaebd54600f572e06199bf7d5064b12fc07",
             "- `14:04` Task 05: merged at `2e21dea`"),
            (f"14:04:20 [progress] 05 merged 2e21deaebd54600f572e06199bf7d5064b12fc07 (repos/backend)",
             "- `14:04` Task 05: merged at `2e21dea` (repos/backend)"),
            ("14:04:20 [progress] 06 impl dispatched antigravity:gemini-4-hydrogen@medium",
             "- `14:04` Task 06: implementation started (`antigravity:gemini-4-hydrogen@medium`)"),
            ("13:57:04 [progress] 05 fix reused antigravity:gemini-4-hydrogen@medium",
             "- `13:57` Task 05: fix round started (`antigravity:gemini-4-hydrogen@medium`)"),
            ("13:57:04 [progress] 05 commit dispatched claude:opus@medium",
             "- `13:57` Task 05: commit round started (`claude:opus@medium`)"),
            ("13:58:00 [progress] 05 impl resume failed; a fresh worker is dispatched claude:opus@high",
             "- `13:58` Task 05: implementation resume failed; fresh worker started (`claude:opus@high`)"),
            ("14:01:53 [progress] 05 fix settled succeeded",
             "- `14:01` Task 05: fix round finished"),
            ("14:01:53 [progress] 05 impl settled failed",
             "- `14:01` Task 05: implementation **FAILED**"),
            (f"14:02:48 [progress] 05 gate exit=0 {rd}/gates/05-2.log",
             "- `14:02` Task 05: gate **PASS** (`gates/05-2.log`)"),
            (f"14:02:48 [progress] 05 gate exit=1 {rd}/gates/05-1.log",
             "- `14:02` Task 05: gate **FAIL** (exit 1) (`gates/05-1.log`)"),
            ("13:51:28 [progress] 05 stage1 reviewer dispatched antigravity:gemini-4-neon@medium",
             "- `13:51` Task 05: stage 1 review started (`antigravity:gemini-4-neon@medium`)"),
            (f"13:53:15 [progress] 05 stage1 VERDICT: PASS {rd}/verdicts/05-s1-1.md",
             "- `13:53` Task 05: stage 1 review **PASS** (`verdicts/05-s1-1.md`)"),
            (f"13:57:04 [progress] 05 stage2 VERDICT: FIX {rd}/verdicts/05-s2-1.md",
             "- `13:57` Task 05: stage 2 review **FIX** (`verdicts/05-s2-1.md`)"),
            (f"13:57:04 [progress] 05 stage2 no verdict {rd}/verdicts/05-s2-1.md",
             "- `13:57` Task 05: stage 2 review **no verdict** (`verdicts/05-s2-1.md`)"),
            ("13:57:04 [progress] 05 stage1 reviewer void: changed the worktree",
             "- `13:57` Task 05: stage 1 reviewer **void** (changed the worktree)"),
            ("13:57:04 [progress] 05 escalation attempt 1: next rung claude:opus@max",
             "- `13:57` Task 05: **escalation attempt 1** (next rung claude:opus@max)"),
            ("13:57:04 [progress] 05 escalation attempt 1 on claude:opus@max from baf9002ac53cbf62370cfee37bd328eb4f8730bf",
             "- `13:57` Task 05: **escalation attempt 1** started on `claude:opus@max` from `baf9002`"),
            ("13:57:04 [progress] 05 replanned into 05a 05b from baf9002ac53cbf62370cfee37bd328eb4f8730bf",
             "- `13:57` Task 05: **replanned** into `05a 05b` from `baf9002`"),
            ("10:18:05 [progress] design safety dispatched claude:opus@high",
             "- `10:18` Design (safety) started (`claude:opus@high`)"),
            (f"10:32:03 [progress] design safety settled succeeded {rd}/safety-1.md",
             "- `10:32` Design (safety) finished (`safety-1.md`)"),
            (f"10:32:03 [progress] design safety failed: {rd}/safety-2.md",
             "- `10:32` Design (safety) **FAILED** (`safety-2.md`)"),
            ("12:37:13 [progress] final review 1 dispatched claude:opus@high",
             "- `12:37` Final review 1 started (`claude:opus@high`)"),
            (f"12:40:00 [progress] final review 1 VERDICT: FIX {rd}/verdicts/final-1.md",
             "- `12:40` Final review 1: **FIX** (`verdicts/final-1.md`)"),
            ("12:40:00 [progress] final reviewer void: stopped without a verdict",
             "- `12:40` Final reviewer **void** (stopped without a verdict)"),
            (f"12:45:00 [progress] verify 1 exit=0 {rd}/verify-1.log",
             "- `12:45` Verify 1: **PASS** (`verify-1.log`)"),
            (f"12:45:00 [progress] verify 1 [repos/backend] exit=2 {rd}/verify-1.log",
             "- `12:45` Verify 1 (repos/backend): **FAIL** (exit 2) (`verify-1.log`)"),
            ("12:50:00 [progress] drift check dispatched claude:opus@high",
             "- `12:50` Drift check started (`claude:opus@high`)"),
            (f"12:51:00 [progress] drift check settled succeeded {rd}/drift-1.md",
             "- `12:51` Drift check finished (`drift-1.md`)"),
            (f"12:51:00 [progress] drift check failed: {rd}/drift-1.md",
             "- `12:51` Drift check **FAILED** (`drift-1.md`)"),
            ("14:12:00 [worker +460s] 06-implement: says: Working on tests now.",
             "- `14:12` `06-implement` (+460s): Working on tests now."),
            (f"14:12:00 [worker +460s] 06-implement: tool: read_file ({rd}/specs/06.txt)",
             "- `14:12` `06-implement` (+460s): tool: read_file (specs/06.txt)"),
            ("14:12:00 [worker +3600s] 06-implement: timed out after 3600s",
             "- `14:12` `06-implement` (+3600s): timed out after 3600s"),
            ("14:12:00 [worker +42s] 06-implement: interrupted",
             "- `14:12` `06-implement` (+42s): interrupted"),
            (f"14:12:00 [worker] start 06-implement (claude pid=1) log={rd}/logs/06.log",
             "- `14:12` `06-implement` started (claude pid=1)"),
            ("13:51:27 [progress] 05 prompt usage-limit: retry",
             "- `13:51` Task 05: usage-limit prompt answered `retry`"),
            ("13:51:27 [progress] final prompt verify-failed: accept",
             "- `13:51` Final: verify-failed prompt answered `accept`"),
            (f"14:12:00 [progress] some future milestone at {rd}/foo.txt",
             "- `14:12` some future milestone at foo.txt"),
            ("(3 earlier lines left out)",
             "(3 earlier lines left out)"),
        ]
        for raw, expected in cases:
            self.assertEqual(watch.format_line(raw, rd), expected, raw)

    def test_format_header_variants(self):
        self.assertEqual(watch.format_header([], False, None, 600), "**Still running (10m check-in)**")
        self.assertEqual(watch.format_header(["10:00:00 [progress] 01 impl dispatched x"], True, None, 600),
                         "**Call finished**")
        header_cases = [
            # The commit and the repo are in the bullet below the header, not in it.
            ("14:04:20 [progress] 05 merged 2e21deaebd54600f572e06199bf7d5064b12fc07",
             "**Task 05 — Merged**"),
            ("14:04:20 [progress] 05 merged 2e21deaebd54600f572e06199bf7d5064b12fc07 (repos/backend)",
             "**Task 05 — Merged**"),
            ("13:57:04 [progress] 05 stage2 VERDICT: FIX /x/05-s2-1.md",
             "**Task 05 — Stage 2 review: FIX**"),
            ("13:57:04 [progress] 05 stage2 no verdict /x/05-s2-1.md",
             "**Task 05 — Stage 2 review: no verdict**"),
            ("13:57:04 [progress] 05 gate exit=1 /x/05-1.log",
             "**Task 05 — Gate failed (exit 1)**"),
            ("13:57:04 [progress] 05 escalation attempt 2: next rung claude:opus@max",
             "**Task 05 — Escalation attempt 2**"),
            ("13:57:04 [progress] 05 replanned into 05a 05b from baf9002ac53cbf62370cfee37bd328eb4f8730bf",
             "**Task 05 — Replanned**"),
            ("13:57:04 [progress] 05 impl settled failed",
             "**Task 05 — Implementation failed**"),
            # The round goes on with a fresh worker: it has not failed.
            ("13:57:04 [progress] 05 fix resume failed; a fresh worker is dispatched claude:opus@high",
             "**Task 05 — Fix round: resume failed, fresh worker started**"),
            ("13:57:04 [progress] 05 stage1 reviewer void: stopped",
             "**Task 05 — Stage 1 reviewer void**"),
            ("13:57:04 [progress] final review 1 VERDICT: FIX /x/final-1.md",
             "**Final review 1 — FIX**"),
            ("13:57:04 [progress] final reviewer void: stopped",
             "**Final reviewer void**"),
            ("13:57:04 [progress] verify 1 [repos/backend] exit=2 /x/verify-1.log",
             "**Verification failed (exit 2)**"),
            ("13:57:04 [progress] design safety failed: /x/safety-1.md",
             "**Design (safety) failed**"),
            ("13:57:04 [progress] drift check failed: /x/drift-1.md",
             "**Drift check failed**"),
            ("13:57:04 [worker +3600s] 05-implement: timed out after 3600s",
             "**Worker `05-implement` — timed out**"),
            ("13:57:04 [worker +42s] 05-implement: interrupted",
             "**Worker `05-implement` — interrupted**"),
            ("13:57:04 [progress] unrecognized wake event",
             "**Mission update**"),
        ]
        for ln, expected in header_cases:
            self.assertEqual(watch.format_header([ln], False, 1.0, 600), expected, ln)


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
