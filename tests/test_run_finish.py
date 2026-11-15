"""Tests for scripts/run_finish.py (coordinator Step 8 items 1-3) against the fake Orca CLI. Run: bash tests/run_all.sh"""
import json
import unittest
from pathlib import Path

from helpers import Base, RUN_TASK, SKILL, sh


class Finish(Base):
    SCRIPT = SKILL / "scripts" / "run_finish.py"

    def setUp(self):
        super().setUp()
        self.design = self.docs / "design.md"
        self.design.write_text("The design\n")
        self.decisions = self.docs / "decisions-log.md"
        self.decisions.write_text("none\n")
        self.state.write_text(json.dumps({**self.st(), "TOPIC": "demo", "INITIAL_BASE_SHA": self.older,
                                          "DESIGN_FILE": str(self.design), "DECISIONS_LOG": str(self.decisions),
                                          "LAUNCH_STYLE_cl_big_high": "flag", "LAUNCH_STYLE_cl_small_high": "flag"}))

    def starts(self):
        return self.calls("worker-start")

    def first_review(self, verdict="pass"):
        self.behave(verdict)
        return self.expect(10, "WAIT")

    def to_job(self, verdict="fix"):
        self.first_review(verdict)
        return self.expect(20, "ARCHITECT_JOB")

    def commit_broken(self):
        """Put BROKEN into the feature head so the VERIFY command fails."""
        (self.impl / "BROKEN").write_text("x\n")
        sh("git", "-C", str(self.impl), "add", "BROKEN")
        sh("git", "-C", str(self.impl), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "broken")
        self.state.write_text(json.dumps({**self.st(), "LAST_MERGED": self.impl_head()}))

    def result(self, text, code, prefix):
        return self.expect(code, prefix, "--architect-result", self.result_file(text))

    def prompt_text(self, last):
        return Path(last.split()[1]).read_text()


class Review(Finish):
    def test_pass_then_verified(self):
        last, _ = self.first_review("pass")
        self.assertTrue(last.startswith("WAIT ctx_1 final-review"), last)
        self.assertEqual(self.st("PHASE"), "finish")
        last, _ = self.expect(0, "VERIFIED")
        log = self.run_dir / "verify-1.log"
        self.assertEqual(last, f"VERIFIED {log}")
        self.assertIn("VERIFY_1", log.read_text())
        self.assertEqual(len(self.calls("worker-release")), 1)
        self.assertEqual(self.adv()[1], last)  # asking again repeats it

    def test_final_reviewer_uses_role_architect(self):
        self.first_review("pass")
        start = self.starts()[0]
        self.assertIn("cl-agent", start)  # cl:big@high launches with the Claude agent
        self.assertIn(f"path:{self.impl}", start)
        task = json.loads(self.world.read_text())["tasks"]["task_1"]
        self.assertEqual(task["title"], "Final review: demo")
        self.assertIn(f"{self.older}..{self.base}", task["spec"])
        self.assertIn("The design", task["spec"])
        self.assertIn("CONTRIBUTING.md", task["spec"])
        self.assertIn(str(self.run_dir / "verdicts" / "final-1.md"), task["spec"])
        self.expect(0, "VERIFIED")
        self.assertTrue((self.run_dir / "results" / "final-settle-1.md").is_file())

    def test_fix_verdict_goes_to_architect(self):
        last, _ = self.to_job("fix")
        self.assertEqual(last, f"ARCHITECT_JOB final-review {self.run_dir / 'verdicts' / 'final-1.md'}")
        self.assertEqual(self.adv()[1], last)

    def test_pass_verdict_in_settle_message_only(self):
        self.first_review("pass-body-only")
        self.expect(0, "VERIFIED")
        self.assertTrue((self.run_dir / "verdicts" / "final-1.md").read_text().strip().endswith("VERDICT: PASS"))

    def test_no_verdict_twice_counts_as_fix(self):
        self.behave("noverdict", "noverdict")
        self.expect(10, "WAIT")
        last, _ = self.expect(10, "WAIT")  # one new reviewer
        self.assertEqual(len(self.starts()), 2)
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertTrue(last.startswith("ARCHITECT_JOB final-review "), last)

    def test_no_verdict_once_then_pass(self):
        self.behave("noverdict", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "VERIFIED")

    def test_void_final_reviewer_replaced(self):
        self.behave("edit-and-pass", "pass")
        self.expect(10, "WAIT")
        last, _ = self.expect(10, "WAIT")
        self.assertEqual(len(self.starts()), 2)
        self.assertEqual(sh("git", "-C", str(self.impl), "status", "--porcelain"), "")
        self.expect(0, "VERIFIED")

    def test_stopped_final_reviewer_is_void_and_third_void_prompts(self):
        self.behave("stopped", "stopped", "stopped", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith(" retry stop"), last)
        self.assertEqual(len(self.starts()), 3)
        self.expect(10, "WAIT", "--reply", "retry")
        self.expect(0, "VERIFIED")

    def test_usage_limit_prompts_retry_stop(self):
        self.behave("usage", "pass")
        self.expect(10, "WAIT")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("5-hour limit", self.prompt_text(last))
        self.assertTrue(last.endswith(" retry stop"), last)
        self.expect(10, "WAIT", "--reply", "retry")
        self.expect(0, "VERIFIED")

    def test_stop_reply_stops(self):
        self.behave("usage")
        self.expect(10, "WAIT")
        self.expect(30, "PROMPT")
        self.expect(40, "STOP", "--reply", "stop")

    def test_starting_state_releases_impl_dispatches_and_checks_tree(self):
        self.state.write_text(json.dumps({**self.st(), "IMPL_DISPATCH_01": "ctx_9"}))
        self.first_review("pass")
        self.assertEqual(self.calls("worker-release")[0][-1], "ctx_9")

    def test_dirty_tree_at_start_is_an_error(self):
        (self.impl / "stray.txt").write_text("x\n")
        last, _ = self.expect(2, "ERROR")
        self.assertIn("stray.txt", last)
        self.assertEqual(self.starts(), [])


class Fixes(Finish):
    def final_fix(self, f="F1"):
        self.task(f, "none")
        return self.result(f"RESULT: FINAL_FIX {f}", 21, "RUN_QUEUE")

    def test_final_fix_runs_queue_then_reviews_again(self):
        self.to_job("fix")
        last, _ = self.final_fix("F1")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["F1"])
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), "1")
        self.SCRIPT, finish = RUN_TASK, self.SCRIPT
        try:
            head = self.feature()
            self.behave("commit", "pass", "pass")
            self.expect(10, "WAIT")
            self.expect(10, "WAIT")
            self.expect(10, "WAIT")
            self.expect(0, "QUEUE_EMPTY")
        finally:
            self.SCRIPT = finish
        self.assertNotEqual(self.st("LAST_MERGED"), head)
        self.behave("pass")
        last, _ = self.expect(10, "WAIT")
        self.assertTrue(last.endswith(" final-review"), last)
        spec = json.loads(self.world.read_text())["tasks"]["task_5"]["spec"]
        self.assertIn(f"{self.older}..{self.st('LAST_MERGED')}", spec)
        self.assertIn(str(self.run_dir / "verdicts" / "final-2.md"), spec)
        self.expect(0, "VERIFIED")

    def test_third_final_fix_prompts_continue_or_stop(self):
        self.state.write_text(json.dumps({**self.st(), "FINAL_FIX_ROUNDS": "2"}))
        self.to_job("fix")
        self.task("F3", "none")
        last, _ = self.result("RESULT: FINAL_FIX F3", 30, "PROMPT")
        self.assertTrue(last.endswith(" continue stop"), last)
        verdict = self.run_dir / "verdicts" / "final-1.md"
        self.assertEqual(self.prompt_text(last), "The final review still finds issues after two final-fix rounds: "
                         f"{verdict}. Reply \"continue\" for one more round, or \"stop\" to stop the run.\n")
        last, _ = self.expect(21, "RUN_QUEUE", "--reply", "continue")
        self.assertEqual(last, "RUN_QUEUE F3")
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), "2")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["F3"])

    def test_third_final_fix_stop(self):
        self.state.write_text(json.dumps({**self.st(), "FINAL_FIX_ROUNDS": "2"}))
        self.to_job("fix")
        self.task("F3", "none")
        self.result("RESULT: FINAL_FIX F3", 30, "PROMPT")
        self.expect(40, "STOP", "--reply", "stop")

    def test_invalid_fix_meta_goes_to_fix_meta_job_then_revalidates(self):
        self.to_job("fix")
        self.task("F1", "none")
        (self.docs / "tasks" / "F1.meta").write_text("TITLE=broken\n")
        last, _ = self.result("RESULT: FINAL_FIX F1", 20, "ARCHITECT_JOB")
        self.assertTrue(last.startswith("ARCHITECT_JOB fix-meta "), last)
        self.assertIn("F1", Path(last.split()[2]).read_text())
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), None)
        self.assertEqual(self.adv()[1], last)
        self.task("F1", "none")
        last, _ = self.result("RESULT: META_FIXED", 21, "RUN_QUEUE")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), "1")

    def test_meta_invalid_three_times_is_an_error(self):
        self.to_job("fix")
        self.task("F1", "none")
        (self.docs / "tasks" / "F1.meta").write_text("TITLE=broken\n")
        self.result("RESULT: FINAL_FIX F1", 20, "ARCHITECT_JOB")
        self.result("RESULT: META_FIXED", 20, "ARCHITECT_JOB")
        last, _ = self.result("RESULT: META_FIXED", 2, "ERROR")
        self.assertIn("META_INVALID_3_TIMES", last)

    def unproved_fix(self, f="F1", reviews="cl:small@low"):
        self.to_job("fix")
        self.task(f, "none")
        meta = self.docs / "tasks" / f"{f}.meta"
        meta.write_text(meta.read_text().replace("cl:small@high", reviews))

    def test_final_fix_review_pairs_are_probed_before_the_queue(self):
        self.unproved_fix()
        self.behave("ok")
        last, _ = self.result("RESULT: FINAL_FIX F1", 10, "WAIT")
        self.assertTrue(last.endswith(" probe"), last)
        self.assertEqual(self.st("WAITING_ON"), last.split()[1])
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01"])
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), None)
        last, _ = self.expect(21, "RUN_QUEUE")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.assertEqual(self.st("LAUNCH_STYLE_cl_small_low"), "flag")
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), "1")

    def test_final_fix_probe_failure_goes_to_fix_meta(self):
        self.unproved_fix()
        self.behave("fail")
        self.result("RESULT: FINAL_FIX F1", 10, "WAIT")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertTrue(last.startswith("ARCHITECT_JOB fix-meta "), last)
        text = Path(last.split()[2]).read_text()
        self.assertIn("PROBLEM F1: REVIEW_1 cl:small@low failed its test launch; see ", text)
        self.assertEqual(self.st("META_FAILS"), "1")
        self.task("F1", "none")
        last, _ = self.result("RESULT: META_FIXED", 21, "RUN_QUEUE")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.assertEqual(self.st("META_FAILS"), "0")

    def test_docs_moved_during_final_fix_probe(self):
        self.unproved_fix()
        self.behave("ok-edit")
        self.result("RESULT: FINAL_FIX F1", 10, "WAIT")
        g = lambda *a: sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", *a)
        (self.docs_wt / "decisions.md").write_text("user-message decision\n")
        g("add", "decisions.md")
        g("commit", "-qm", "architect commit while the probe runs")
        head = g("rev-parse", "HEAD")
        self.expect(21, "RUN_QUEUE", "--docs-moved")
        self.assertEqual(g("rev-parse", "HEAD"), head)
        self.assertEqual(g("status", "--porcelain", "--untracked-files=all"), "")

    def test_architect_stop(self):
        self.to_job("fix")
        self.result("RESULT: STOP", 40, "STOP")

    def test_unexpected_result_is_an_error(self):
        self.to_job("fix")
        last, _ = self.result("RESULT: PLAN_APPROVED", 2, "ERROR")
        self.assertIn("UNEXPECTED_ARCHITECT_RESULT", last)

    def test_result_with_no_job_open_is_an_error(self):
        self.first_review("pass")
        self.result("RESULT: FINAL_CLEAN", 2, "ERROR")


class Verify(Finish):
    def test_verify_failure_goes_to_architect(self):
        self.commit_broken()
        self.first_review("pass")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        log = self.run_dir / "verify-1.log"
        self.assertEqual(last, f"ARCHITECT_JOB verification-failed {log}")
        self.assertIn("VERIFY_1", log.read_text())

    def test_final_clean_after_final_review_job_verifies(self):
        self.to_job("fix")
        last, _ = self.result("RESULT: FINAL_CLEAN", 0, "VERIFIED")
        self.assertEqual(last, f"VERIFIED {self.run_dir / 'verify-1.log'}")

    def test_final_fix_after_verification_failure_reviews_again_then_verifies_again(self):
        self.commit_broken()
        self.first_review("pass")
        self.expect(20, "ARCHITECT_JOB")
        self.task("F1", "none")
        self.result("RESULT: FINAL_FIX F1", 21, "RUN_QUEUE")
        sh("git", "-C", str(self.impl), "-c", "user.email=t@t", "-c", "user.name=t", "rm", "-q", "BROKEN")
        sh("git", "-C", str(self.impl), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "unbreak")
        self.state.write_text(json.dumps({**self.st(), "LAST_MERGED": self.impl_head()}))
        self.behave("pass")
        self.expect(10, "WAIT")
        last, _ = self.expect(0, "VERIFIED")
        self.assertEqual(last, f"VERIFIED {self.run_dir / 'verify-2.log'}")

    def test_final_clean_after_failed_verification_prompts_then_continue_opens_pr(self):
        self.commit_broken()
        self.first_review("pass")
        self.expect(20, "ARCHITECT_JOB")
        log = self.run_dir / "verify-1.log"
        last, _ = self.result("RESULT: FINAL_CLEAN", 30, "PROMPT")
        self.assertTrue(last.endswith(" continue stop"), last)
        self.assertEqual(self.prompt_text(last), f"Verification still fails, and the architect found nothing to fix: "
                         f"{log}. Reply \"continue\" to open the PR anyway, or \"stop\" to stop the run.\n")
        last, _ = self.expect(0, "VERIFIED", "--reply", "continue")
        self.assertEqual(last, f"VERIFIED {log}")
        self.assertIn("accepted a failing verification", (self.run_dir / "run-log.md").read_text())

    def test_final_clean_after_failed_verification_stop(self):
        self.commit_broken()
        self.first_review("pass")
        self.expect(20, "ARCHITECT_JOB")
        self.result("RESULT: FINAL_CLEAN", 30, "PROMPT")
        self.expect(40, "STOP", "--reply", "stop")

    def test_wrong_reply_repeats_prompt(self):
        self.commit_broken()
        self.first_review("pass")
        self.expect(20, "ARCHITECT_JOB")
        first, _ = self.result("RESULT: FINAL_CLEAN", 30, "PROMPT")
        again, _ = self.expect(30, "PROMPT", "--reply", "maybe")
        self.assertEqual(first, again)

    def test_gate_that_cannot_run_is_an_error(self):
        Path(self.st("RUN_VALUES")).write_text("FEATURE_BRANCH=feature\n")  # no VERIFY_n lines
        self.first_review("pass")
        last, _ = self.expect(2, "ERROR")
        self.assertIn("NO_COMMANDS", last)


class Legacy(Finish):
    def test_old_format_state_is_refused(self):
        s = self.st()
        del s["ROLE_ARCHITECT"]
        self.state.write_text(json.dumps(s))
        last, _ = self.expect(2, "ERROR")
        self.assertIn("OLD_FORMAT_RUN", last)


if __name__ == "__main__":
    unittest.main(verbosity=1)
