"""Tests for scripts/run_task.py against the fake Orca CLI. Run: bash tests/run_all.sh"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import Base, sh


class HappyPath(Base):
    def test_two_tasks_merge_in_order(self):
        (self.run_dir / "queue.txt").write_text("01 02\n")
        self.behave("commit", "pass", "pass", "commit", "pass", "pass")
        self.assertIn(" impl", self.expect(10, "WAIT")[0])
        self.assertIn(" review1", self.expect(10, "WAIT")[0])
        self.assertIn(" review2", self.expect(10, "WAIT")[0])
        last, lines = self.expect(10, "WAIT")
        self.assertTrue(lines[0].startswith("MERGED 01 "), lines)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        _, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(lines[0].startswith("MERGED 02 "))
        self.assertEqual(self.feature(), self.impl_head())
        self.assertEqual(sh("git", "-C", str(self.main), "branch", "--show-current"), "main")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), [])
        starts = self.calls("worker-start")
        impl = [c for c in starts if "ag-agent" in c]
        self.assertEqual(len(impl), 2)
        self.assertTrue(all("--keep-open" in c and "g-high" in c for c in impl))
        reviews = [c for c in starts if "cl-agent" in c]
        self.assertEqual(len(reviews), 4)
        self.assertTrue(all("--keep-open" not in c and "small" in c for c in reviews))
        self.assertEqual(self.st("WAITING_ON"), None)

    def test_wait_is_idempotent_while_worker_runs(self):
        self.behave("commit", "pending")
        self.expect(10, "WAIT")
        first, _ = self.expect(10, "WAIT")
        again, _ = self.expect(10, "WAIT")
        self.assertEqual(first, again)
        self.assertEqual(len(self.calls("worker-start")), 2)


class GateAndFixes(Base):
    def test_stage2_counter_survives_gate_pass(self):
        # impl, s1 PASS, s2 FIX, fix fails gate, fix passes gate, s2 FIX, fix, s2 FIX -> escalate
        self.behave("commit", "pass", "fix", "commit-broken", "unbreak", "fix", "commit", "fix")
        self.until(20, "ESCALATE")
        self.assertEqual(self.st("STAGE_FIX_ROUNDS_01_S2"), "2")
        self.assertEqual(len(self.calls("worker-start")), 8)

    def test_gate_pass_resumes_the_failed_review_stage(self):
        # impl, s1 PASS, s2 FIX, fix fails gate, fix passes gate -> the next reviewer is stage 2
        self.behave("commit", "pass", "fix", "commit-broken", "unbreak", "pending")
        for _ in range(5):                           # impl, s1, s2, fix 1, fix 2
            self.expect(10, "WAIT")
        self.assertIn(" review2", self.expect(10, "WAIT")[0])

    def test_total_fix_cap_escalates(self):
        # s1 FIX(t1), gate fail(t2), gate pass, s1 PASS, s2 FIX(t3), gate fail(t4), gate pass, s2 FIX -> cap
        self.behave("commit", "fix", "commit-broken", "unbreak", "pass", "fix", "commit-broken", "unbreak", "fix")
        self.until(20, "ESCALATE")
        self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "4")
        self.assertEqual(self.st("STAGE_FIX_ROUNDS_01_S2"), "1")

    def test_gate_failure_goes_to_fix_on_same_terminal(self):
        self.behave("commit-broken", "unbreak", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        fix_start = self.calls("worker-start")[1]
        self.assertIn("--terminal", fix_start)
        self.assertTrue(list((self.run_dir / "gates").glob("01-*.log")))
        self.assertIn(" review1", self.expect(10, "WAIT")[0])
        self.assertEqual(self.st("GATE_FIX_ROUNDS_01"), "0")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("LOOP_01"), "merged")

    def test_fix_falls_back_to_fresh_worker_when_released(self):
        self.behave("commit", "fix", "commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        w = json.loads(self.world.read_text())
        w["dispatches"]["ctx_1"]["terminalState"] = "released"
        self.world.write_text(json.dumps(w))
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        fix_start = self.calls("worker-start")[2]
        self.assertNotIn("--terminal", fix_start)
        self.assertIn("--keep-open", fix_start)

    def test_two_fix_rounds_then_escalate_then_next_rung(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        for _ in range(6):
            self.expect(10, "WAIT")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("task 01, tier standard, attempt 1, next rung ag:g@xhigh, replan allowed", last)
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn(f"task=01 base={self.base}", hist)
        self.assertEqual(hist.count("VERDICT: FIX"), 3)
        self.expect(20, "ESCALATE")  # re-emitted while waiting on the architect
        note = self.tmp / "note.md"
        note.write_text("steps 1-2 done\n")
        self.behave("commit", "pass", "pass")
        last, _ = self.expect(10, "WAIT", "--architect-result",
                              self.result_file(f"RESULT: ESCALATE {self.base} {note} ag:g@xhigh"))
        self.assertIn("g-xhigh", self.calls("worker-start")[-1])
        spec = Path(self.st("IMPLEMENT_SPEC_01")).read_text()
        self.assertIn("steps 1-2 done", spec)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("LOOP_01"), "merged")

    def test_verdict_falls_back_to_the_settle_message(self):
        self.behave("commit", "pass-body-only", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertIn(" review2", self.expect(10, "WAIT")[0])
        self.assertIn("VERDICT: PASS", (self.run_dir / "verdicts" / "01-s1-1.md").read_text())
        self.expect(0, "QUEUE_EMPTY")

    def test_prose_settle_message_uses_the_review_file(self):
        self.behave("commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertEqual(self.st("NO_VERDICT_01_S1"), "0")
        self.assertTrue(list((self.run_dir / "results").glob("01-s1-settle-*.md")))
        self.expect(0, "QUEUE_EMPTY")

    def test_no_verdict_twice_counts_as_fix(self):
        self.behave("commit", "noverdict", "noverdict", "pending")
        self.expect(10, "WAIT")
        self.assertIn("ctx_2 review1", self.expect(10, "WAIT")[0])
        self.assertIn("ctx_3 review1", self.expect(10, "WAIT")[0])
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        self.assertEqual(self.st("REVIEW_STAGE_01"), "1")

    def test_stage2_fix_rechecks_then_returns_to_stage2(self):
        self.behave("commit", "pass", "fix", "commit", "pass")
        for _ in range(3):
            self.expect(10, "WAIT")
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        self.assertIn(" review2", self.expect(10, "WAIT")[0])
        self.assertTrue(list((self.run_dir / "gates").glob("01-2.log")))
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(len([c for c in self.calls("worker-start") if "cl-agent" in c]), 3)


class TreeAndWorkers(Base):
    def test_reviewer_that_edits_is_void_and_replaced(self):
        self.behave("commit", "edit-and-pass", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertIn("ctx_3 review1", self.expect(10, "WAIT")[0])
        self.assertTrue(list((self.run_dir / "verdicts").glob("01-void-s1-*.md")))
        self.assertEqual(sh("git", "-C", str(self.impl), "status", "--porcelain"), "")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "0")

    def test_stopped_reviewer_is_replaced(self):
        self.behave("commit", "stopped", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertIn("ctx_3 review1", self.expect(10, "WAIT")[0])
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")

    def test_idle_implementer_is_nudged_and_finishes(self):
        self.behave("idle-then-commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertIn(" impl", self.expect(10, "WAIT")[0])
        self.assertEqual(len(self.calls("send")), 1)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("ATTEMPT_01"), "0")

    def test_idle_forever_implementer_escalates_after_two_nudges(self):
        self.behave("idle-forever")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.assertEqual(len(self.calls("send")), 2)
        self.assertIn("ended its turn 3 times", (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_uncommitted_work_gets_a_commit_worker(self):
        self.behave("dirty", "commit-all", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertIn(" commit", self.expect(10, "WAIT")[0])
        self.assertIn("--terminal", self.calls("worker-start")[1])
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")

    def test_no_commits_escalates(self):
        self.behave("nocommit")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.assertIn("settled succeeded with no commits",
                      (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_failed_implementer_message_goes_into_history(self):
        self.behave("fail")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.assertIn("AssertionError: 1 != 2", (self.run_dir / "verdicts" / "01-history.md").read_text())


class Prompts(Base):
    def test_usage_limit_prompts_then_retry_restarts(self):
        self.behave("usage")
        self.expect(10, "WAIT")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("5-hour limit", Path(last.split()[1]).read_text())
        self.assertEqual(self.st("ATTEMPT_01"), "0")
        self.expect(30, "PROMPT", "--reply", "maybe")
        self.behave("commit", "pass", "pass")
        self.assertIn(" impl", self.expect(10, "WAIT", "--reply", "retry")[0])
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")

    def test_rejected_pair_twice_prompts_then_architect_stops(self):
        self.behave("fail")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        bad = self.result_file(f"RESULT: ESCALATE {self.base} /dev/null cl:big@high")
        self.expect(20, "ESCALATE", "--architect-result", bad)
        last, _ = self.expect(30, "PROMPT", "--architect-result", bad)
        self.assertIn("rejected twice", Path(last.split()[1]).read_text())
        self.expect(20, "ESCALATE", "--reply", "retry")
        self.expect(40, "STOP", "--architect-result", self.result_file("RESULT: STOP"))

    def test_reset_sha_before_base_is_rejected(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.until(20, "ESCALATE")
        note = self.tmp / "note.md"
        note.write_text("n\n")
        self.expect(20, "ESCALATE", "--architect-result",
                    self.result_file(f"RESULT: ESCALATE {self.older} {note} ag:g@xhigh"))
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn("not between the task's base", hist)
        self.assertEqual(self.impl_head(), self.st("WORKER_HEAD_01"))  # nothing was reset

    def test_replan_sha_before_base_is_rejected(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.until(20, "ESCALATE")
        queue = (self.run_dir / "queue.txt").read_text()
        self.expect(20, "ESCALATE", "--architect-result", self.result_file(f"RESULT: REPLAN {self.older} 01b"))
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn("not between the task's base", hist)
        self.assertEqual(self.impl_head(), self.st("WORKER_HEAD_01"))
        self.assertEqual((self.run_dir / "queue.txt").read_text(), queue)

    def test_escalate_sha_off_the_head_line_is_rejected(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.until(20, "ESCALATE")
        g = lambda *a: sh("git", "-C", str(self.impl), "-c", "user.email=t@t", "-c", "user.name=t", *a)
        head = g("rev-parse", "HEAD")
        g("checkout", "-q", "--detach", self.base)
        (self.impl / "side.txt").write_text("s\n")
        g("add", "side.txt")
        g("commit", "-qm", "side")
        side = g("rev-parse", "HEAD")
        g("checkout", "-q", "--detach", head)
        note = self.tmp / "note.md"
        note.write_text("n\n")
        self.expect(20, "ESCALATE", "--architect-result",
                    self.result_file(f"RESULT: ESCALATE {side} {note} ag:g@xhigh"))
        self.assertIn("not between the task's base", (self.run_dir / "verdicts" / "01-history.md").read_text())
        self.assertEqual(self.impl_head(), head)

    def test_three_void_reviewers_prompt_retry_or_stop(self):
        self.behave("commit", "stopped", "stopped", "stopped")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith("retry stop"))
        text = Path(last.split()[1]).read_text()
        self.assertIn("Task 01 stage 1: three reviewers in a row were void", text)
        self.behave("pass", "pass")
        self.expect(10, "WAIT", "--reply", "retry")
        self.assertEqual(self.st("VOIDS_01_S1"), "0")

    def test_two_gate_failures_escalate(self):
        self.behave("commit-broken", "commit", "commit")
        self.until(20, "ESCALATE")
        self.assertEqual(self.st("GATE_FIX_ROUNDS_01"), "2")

    def test_escalation_attempt_resets_fix_counters(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.until(20, "ESCALATE")
        note = self.tmp / "note.md"
        note.write_text("n\n")
        self.behave("commit", "pass", "pass")
        self.expect(10, "WAIT", "--architect-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} ag:g@xhigh"))
        for k in ("STAGE_FIX_ROUNDS_01_S1", "STAGE_FIX_ROUNDS_01_S2", "GATE_FIX_ROUNDS_01", "TOTAL_FIX_ROUNDS_01"):
            self.assertEqual(self.st(k), "0", k)

    def test_drift_before_task_prompts_and_rebuild_resets(self):
        tree = sh("git", "-C", str(self.main), "rev-parse", "feature^{tree}")
        moved = sh("git", "-C", str(self.main), "-c", "user.email=t@t", "-c", "user.name=t",
                   "commit-tree", tree, "-p", self.base, "-m", "outside")
        sh("git", "-C", str(self.main), "update-ref", "refs/heads/feature", moved)
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("moved by:", Path(last.split()[1]).read_text())
        self.behave("pending")
        self.expect(10, "WAIT", "--reply", "rebuild")
        self.assertEqual(self.impl_head(), moved)
        self.assertEqual(self.st("LAST_MERGED"), moved)
        self.assertEqual(self.st("BASE_SHA_01"), moved)

    def test_branch_checked_out_blocks_merge(self):
        self.behave("commit", "pass", "pass")
        self.run_one_task_clean()
        sh("git", "-C", str(self.main), "switch", "-q", "feature")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("BRANCH_CHECKED_OUT", Path(last.split()[1]).read_text())
        sh("git", "-C", str(self.main), "switch", "-q", "main")
        _, lines = self.expect(0, "QUEUE_EMPTY", "--reply", "continue")
        self.assertTrue(lines[0].startswith("MERGED 01"))


class PicksAndLadder(Base):
    def use_up_ladder(self):
        """Fail the first attempt and both rungs; stop at the third escalation."""
        self.behave("fail", "fail", "fail")
        self.expect(10, "WAIT")
        note = self.tmp / "note.md"
        note.write_text("n\n")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("next rung ag:g@xhigh", last)
        self.expect(10, "WAIT", "--architect-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} ag:g@xhigh"))
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("attempt 2, next rung cl:big@high", last)
        self.expect(10, "WAIT", "--architect-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} cl:big@high"))
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("attempt 3, ladder used up, replan allowed", last)
        return note

    def test_old_format_state_refused(self):
        s = self.st()
        del s["ROLE_ARCHITECT"]
        self.state.write_text(json.dumps(s))
        rc, last, _ = self.adv()
        self.assertEqual((rc, last.split()[:2]), (2, ["ERROR", "OLD_FORMAT_RUN"]))

    def test_bad_usage_exits_64(self):
        for args in (["advance", "--nope"], []):
            r = subprocess.run([sys.executable, "-I", str(self.SCRIPT), "--state", str(self.state), *args],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 64, r.stderr)

    def test_ladder_from_picks(self):
        self.use_up_ladder()
        self.assertEqual(self.st("PAIR_01"), "cl:big@high")

    def test_past_ladder_pair_is_probed_first(self):
        note = self.use_up_ladder()
        self.behave("ok", "pending")
        last, _ = self.expect(10, "WAIT", "--architect-result",
                              self.result_file(f"RESULT: ESCALATE {self.base} {note} cl:small@low"))
        self.assertTrue(last.endswith(" probe"), last)
        self.assertEqual(self.st("LOOP_01"), "probe:3")
        self.assertEqual(self.st("PAIR_01"), "cl:big@high")
        last, _ = self.expect(10, "WAIT")
        self.assertTrue(last.endswith(" impl"), last)
        self.assertEqual(self.st("PAIR_01"), "cl:small@low")
        self.assertEqual(self.st("LAUNCH_STYLE_cl_small_low"), "flag")
        self.assertIn("--effort", self.calls("worker-start")[-1])

    def test_docs_moved_during_past_ladder_probe(self):
        note = self.use_up_ladder()
        self.behave("ok-edit", "pending")
        self.expect(10, "WAIT", "--architect-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} cl:small@low"))
        g = lambda *a: sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", *a)
        (self.docs_wt / "decisions.md").write_text("user-message decision\n")
        g("add", "decisions.md")
        g("commit", "-qm", "architect commit while the probe runs")
        head = g("rev-parse", "HEAD")
        last, _ = self.expect(10, "WAIT", "--docs-moved")
        self.assertTrue(last.endswith(" impl"), last)
        self.assertEqual(g("rev-parse", "HEAD"), head)
        self.assertEqual(g("status", "--porcelain", "--untracked-files=all"), "")

    def test_failed_probe_rejects_the_answer(self):
        note = self.use_up_ladder()
        self.behave("fail")
        self.expect(10, "WAIT", "--architect-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} cl:small@low"))
        last, _ = self.expect(20, "ESCALATE")
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn("launch test for cl:small@low failed; see", hist)
        self.assertIn("probe-cl_small_low.md", hist)
        self.assertEqual(self.st("LOOP_01"), "escalating")
        self.assertEqual(self.st("PAIR_01"), "cl:big@high")

    def test_launch_uses_recorded_style(self):
        s = self.st()
        s["LAUNCH_STYLE_ag_g_high"] = "flag"
        self.state.write_text(json.dumps(s))
        self.behave("pending")
        self.expect(10, "WAIT")
        call = self.calls("worker-start")[0]
        self.assertEqual(call[call.index("--model"):call.index("--model") + 4], ["--model", "g", "--effort", "high"])


class Replan(Base):
    def setUp(self):
        super().setUp()
        self.state.write_text(json.dumps({**self.st(), "LAUNCH_STYLE_cl_small_high": "flag"}))  # proved at Step 3

    def replan_to(self, tid, reviews="cl:small@low"):
        (self.run_dir / "queue.txt").write_text("01 02\n")
        self.behave("fail")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.task(tid, "AC-1", "REPLANNED_FROM=01")
        meta = self.docs / "tasks" / f"{tid}.meta"
        meta.write_text(meta.read_text().replace("cl:small@high", reviews))

    def test_replan_review_pairs_are_probed_before_the_queue_changes(self):
        self.replan_to("01a")
        self.behave("ok", "pending")
        last, _ = self.expect(10, "WAIT", "--architect-result", self.result_file(f"RESULT: REPLAN {self.base} 01a"))
        self.assertTrue(last.endswith(" probe"), last)
        self.assertEqual(self.st("WAITING_ON"), last.split()[1])
        self.assertEqual(self.st("LOOP_01"), "replan-probe")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01", "02"])
        self.assertEqual(json.loads(self.world.read_text())["tasks"]["task_2"]["title"], "Launch test: cl:small@low")
        last, _ = self.expect(10, "WAIT")
        self.assertTrue(last.endswith(" impl"), last)
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01a", "02"])
        self.assertEqual(self.st("LAUNCH_STYLE_cl_small_low"), "flag")

    def test_replan_probe_failure_goes_to_fix_meta(self):
        self.replan_to("01a")
        self.behave("fail")
        self.expect(10, "WAIT", "--architect-result", self.result_file(f"RESULT: REPLAN {self.base} 01a"))
        last, _ = self.expect(21, "FIX_META")
        text = Path(last.split()[1]).read_text()
        self.assertIn("PROBLEM 01a: REVIEW_1 cl:small@low failed its test launch; see ", text)
        self.assertIn("PROBLEM 01a: REVIEW_2 cl:small@low failed its test launch; see ", text)
        self.assertIn("probe-cl_small_low.md", text)
        self.assertEqual(self.st("META_FAILS"), "1")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01", "02"])
        self.task("01a", "AC-1", "REPLANNED_FROM=01")  # the architect picks a proven pair
        self.behave("pending")
        self.assertIn(" impl", self.expect(10, "WAIT", "--architect-result",
                                           self.result_file("RESULT: META_FIXED"))[0])
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01a", "02"])
        self.assertEqual(self.st("META_FAILS"), "0")

    def test_replan_runs_fix_meta_then_replaces_task(self):
        (self.run_dir / "queue.txt").write_text("01 02\n")
        self.behave("fail")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.task("01a", "AC-1")  # missing REPLANNED_FROM
        last, _ = self.expect(21, "FIX_META", "--architect-result",
                              self.result_file(f"RESULT: REPLAN {self.base} 01a"))
        self.assertIn("needs REPLANNED_FROM=01", Path(last.split()[1]).read_text())
        self.expect(21, "FIX_META")
        self.task("01a", "AC-1", "REPLANNED_FROM=01")
        self.behave("pending")
        self.assertIn(" impl", self.expect(10, "WAIT", "--architect-result",
                                           self.result_file("RESULT: META_FIXED"))[0])
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01a", "02"])
        self.assertEqual(self.st("CURRENT_TASK"), "01a")

    def test_replan_of_a_replanned_task_is_rejected(self):
        self.task("01", "AC-1", "REPLANNED_FROM=00")
        self.behave("fail")
        self.expect(10, "WAIT")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("replan not allowed", last)
        self.expect(20, "ESCALATE", "--architect-result", self.result_file(f"RESULT: REPLAN {self.base} 01b"))
        self.assertIn("Replan is not allowed", (self.run_dir / "verdicts" / "01-history.md").read_text())


class FinalFix(Base):
    def test_final_fix_task_runs_after_queue_empty(self):
        (self.run_dir / "queue.txt").write_text("01\n")
        self.behave("commit", "pass", "pass")
        self.run_one_task_clean()
        self.expect(0, "QUEUE_EMPTY")
        self.task("F1", "none")
        (self.run_dir / "queue.txt").write_text("F1\n")
        head = self.feature()
        self.behave("commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertEqual(self.st("BASE_SHA_F1"), head)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        _, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(lines[0].startswith("MERGED F1"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
