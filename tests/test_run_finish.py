"""Tests for scripts/run_finish.py (coordinator Step 8 items 1-3) against fake_agy.py. Run: bash tests/run_all.sh"""
import json
import shutil
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
        self.state.write_text(json.dumps({
            **self.st(), "TOPIC": "demo", "INITIAL_BASE_SHA": self.older,
            "DESIGN_FILE": str(self.design), "DECISIONS_LOG": str(self.decisions),
            "LAUNCH_STYLE_antigravity_big_high": "flag",
            "LAUNCH_STYLE_antigravity_small_high": "flag",
        }))

    def commit_broken(self):
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
        self.behave("pass")
        last, _ = self.expect(0, "VERIFIED")
        log = self.run_dir / "verify-1.log"
        self.assertEqual(last, f"VERIFIED {log}")
        self.assertEqual(self.st("PHASE"), "finish")
        self.assertIn("VERIFY_1", log.read_text())
        self.assertEqual(self.adv()[1], last)

    def test_final_reviewer_uses_role_architect(self):
        self.behave("pass")
        self.expect(0, "VERIFIED")
        call = self.calls()[0]
        self.assertEqual(call["model"], "big")
        self.assertEqual(call["effort"], "high")
        self.assertEqual(call["cwd"], str(self.impl))
        self.assertIn(f"{self.older}..{self.base}", call["prompt"])
        self.assertIn("The design", call["prompt"])
        self.assertIn("CONTRIBUTING.md", call["prompt"])
        self.assertIn(str(self.run_dir / "verdicts" / "final-1.md"), call["prompt"])
        self.assertTrue((self.run_dir / "results" / "final-settle-1.md").is_file())

    def test_fix_verdict_goes_to_architect(self):
        self.behave("fix")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertEqual(last, f"ARCHITECT_JOB final-review {self.run_dir / 'verdicts' / 'final-1.md'}")
        self.assertEqual(self.adv()[1], last)

    def test_pass_verdict_in_settle_message_only(self):
        self.behave("pass-body-only")
        self.expect(0, "VERIFIED")
        self.assertTrue((self.run_dir / "verdicts" / "final-1.md").read_text().strip().endswith("VERDICT: PASS"))

    def test_no_verdict_twice_counts_as_fix(self):
        self.behave("noverdict", "noverdict")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(last.startswith("ARCHITECT_JOB final-review "), last)

    def test_no_verdict_once_then_pass(self):
        self.behave("noverdict", "pass")
        self.expect(0, "VERIFIED")
        self.assertEqual(len(self.calls()), 2)

    def test_void_final_reviewer_replaced(self):
        self.behave("edit-and-pass", "pass")
        self.expect(0, "VERIFIED")
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(sh("git", "-C", str(self.impl), "status", "--porcelain"), "")

    def test_stopped_final_reviewer_is_void_and_third_void_prompts(self):
        self.behave("stopped", "stopped", "stopped", "pass")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith(" retry stop"), last)
        self.assertEqual(len(self.calls()), 3)
        self.expect(0, "VERIFIED", "--reply", "retry")

    def test_usage_limit_prompts_retry_stop(self):
        self.behave("usage-edit", "pass")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("5-hour limit", self.prompt_text(last))
        self.assertTrue(last.endswith(" retry stop"), last)
        self.assertFalse((self.impl / "stray_usage_edit.txt").exists())
        self.expect(0, "VERIFIED", "--reply", "retry")

    def test_stop_reply_stops(self):
        self.behave("usage")
        self.expect(30, "PROMPT")
        self.expect(40, "STOP", "--reply", "stop")

    def test_consult_on_ask_then_answer_resumes(self):
        self.behave("ask", "pass")
        last, _ = self.expect(22, "CONSULT")
        self.assertIn("Which edge case", Path(last.split()[1]).read_text())
        snap = self.tmp / "mid-finish-state.json"
        orig_agy = self.st("AGY")
        wrapper = self.tmp / "check-finish-agy"
        wrapper.write_text(f"#!/bin/sh\n[ -e {snap} ] || cp {self.state} {snap}\nexec {orig_agy} \"$@\"\n")
        wrapper.chmod(0o755)
        self.state.write_text(json.dumps({**self.st(), "AGY": str(wrapper)}))
        ans = self.tmp / "ans.md"
        ans.write_text("Approved.\n")
        self.expect(0, "VERIFIED", "--answer", str(ans))
        self.assertEqual(self.calls()[1]["conversation"], "conv_1")
        mid = json.loads(snap.read_text())
        self.assertEqual(mid["FIN_PHASE"], "reviewing")
        self.assertNotIn("FIN_ASK_FILE", mid)

    def test_consult_expired_conversation_falls_back_cleanly(self):
        self.behave("ask", "pass")
        self.expect(22, "CONSULT")
        w = json.loads(self.world.read_text())
        w["expired_conversations"] = ["conv_1"]
        self.world.write_text(json.dumps(w))
        ans = self.tmp / "ans.md"
        ans.write_text("Approved.\n" + "y" * 400 + "\n")
        self.expect(0, "VERIFIED", "--answer", str(ans))
        self.assertEqual(self.calls()[2]["conversation"], "")
        self.assertIn("Approved.", self.calls()[2]["prompt"])

    def test_consult_silent_new_conversation_cleans_and_falls_back(self):
        stray = self.impl / "stray_finish.txt"
        self.behave(
            "ask",
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "pass",
        )
        self.expect(22, "CONSULT")
        ans = self.tmp / "ans.md"
        ans.write_text("Approved.\n")
        self.expect(0, "VERIFIED", "--answer", str(ans))
        self.assertFalse(stray.exists())
        self.assertEqual(self.calls()[2]["conversation"], "")
        self.assertIn("Approved.", self.calls()[2]["prompt"])

    def test_starting_state_clears_impl_conversations_and_checks_tree(self):
        self.state.write_text(json.dumps({**self.st(), "IMPL_CONVERSATION_ID_01": "conv_9"}))
        self.behave("pass")
        self.expect(0, "VERIFIED")
        self.assertIsNone(self.st("IMPL_CONVERSATION_ID_01"))

    def test_dirty_tree_at_start_is_an_error(self):
        (self.impl / "stray.txt").write_text("x\n")
        last, _ = self.expect(2, "ERROR")
        self.assertIn("stray.txt", last)
        self.assertEqual(self.calls(), [])


class Fixes(Finish):
    def final_fix(self, f="F1"):
        self.task(f, "none")
        return self.result(f"RESULT: FINAL_FIX {f}", 21, "RUN_QUEUE")

    def test_final_fix_runs_queue_then_reviews_again(self):
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        last, _ = self.final_fix("F1")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["F1"])
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), "1")
        self.SCRIPT, finish = RUN_TASK, self.SCRIPT
        try:
            head = self.feature()
            self.behave("commit", "pass", "pass")
            self.expect(0, "QUEUE_EMPTY")
        finally:
            self.SCRIPT = finish
        self.assertNotEqual(self.st("LAST_MERGED"), head)
        self.behave("pass")
        last, _ = self.expect(0, "VERIFIED")
        spec = self.calls()[-1]["prompt"]
        self.assertIn(f"{self.older}..{self.st('LAST_MERGED')}", spec)
        self.assertIn(str(self.run_dir / "verdicts" / "final-2.md"), spec)

    def test_third_final_fix_prompts_continue_or_stop(self):
        self.state.write_text(json.dumps({**self.st(), "FINAL_FIX_ROUNDS": "2"}))
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
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
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        self.task("F3", "none")
        self.result("RESULT: FINAL_FIX F3", 30, "PROMPT")
        self.expect(40, "STOP", "--reply", "stop")

    def test_invalid_fix_meta_goes_to_fix_meta_job_then_revalidates(self):
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
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
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        self.task("F1", "none")
        (self.docs / "tasks" / "F1.meta").write_text("TITLE=broken\n")
        self.result("RESULT: FINAL_FIX F1", 20, "ARCHITECT_JOB")
        self.result("RESULT: META_FIXED", 20, "ARCHITECT_JOB")
        last, _ = self.result("RESULT: META_FIXED", 2, "ERROR")
        self.assertIn("META_INVALID_3_TIMES", last)

    def unproved_fix(self, f="F1", reviews="antigravity:small@low"):
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        self.task(f, "none")
        meta = self.docs / "tasks" / f"{f}.meta"
        meta.write_text(meta.read_text().replace("antigravity:small@high", reviews))

    def test_final_fix_review_pairs_are_probed_before_the_queue(self):
        self.unproved_fix()
        self.behave("ok")
        last, _ = self.result("RESULT: FINAL_FIX F1", 21, "RUN_QUEUE")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["F1"])
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_small_low"), "slug")
        self.assertEqual(self.st("FINAL_FIX_ROUNDS"), "1")

    def test_final_fix_probe_failure_goes_to_fix_meta(self):
        self.unproved_fix()
        self.behave("fail", "fail")
        last, _ = self.result("RESULT: FINAL_FIX F1", 20, "ARCHITECT_JOB")
        self.assertTrue(last.startswith("ARCHITECT_JOB fix-meta "), last)
        text = Path(last.split()[2]).read_text()
        self.assertIn("PROBLEM F1: REVIEW_1 antigravity:small@low failed its test launch; see ", text)
        self.assertEqual(self.st("META_FAILS"), "1")
        self.task("F1", "none")
        last, _ = self.result("RESULT: META_FIXED", 21, "RUN_QUEUE")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.assertEqual(self.st("META_FAILS"), "0")

    def test_architect_stop(self):
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        self.result("RESULT: STOP", 40, "STOP")

    def test_unexpected_result_is_an_error(self):
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        last, _ = self.result("RESULT: PLAN_APPROVED", 2, "ERROR")
        self.assertIn("UNEXPECTED_ARCHITECT_RESULT", last)

    def test_result_line_punctuation_and_prose_are_ignored(self):
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        self.result("RESULT: STOP. The user chose to stop after the review.", 40, "STOP")

    def test_final_reviewer_gets_every_ac_id_from_the_design(self):
        self.design.write_text("## Acceptance criteria\n- AC-1: adds\n- AC-2: errors\n- AC-10: tests\nAC-2 again\n")
        self.behave("pass")
        self.expect(0, "VERIFIED")
        self.assertIn("exactly these ids, in this order: AC-1 AC-2 AC-10", self.calls()[0]["prompt"])

    def test_result_with_no_job_open_is_an_error(self):
        self.behave("pass")
        self.expect(0, "VERIFIED")
        self.result("RESULT: FINAL_CLEAN", 2, "ERROR")


class Verify(Finish):
    def test_verify_failure_goes_to_architect(self):
        self.commit_broken()
        self.behave("pass")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        log = self.run_dir / "verify-1.log"
        self.assertEqual(last, f"ARCHITECT_JOB verification-failed {log}")
        self.assertIn("VERIFY_1", log.read_text())

    def test_final_clean_after_final_review_job_verifies(self):
        self.behave("fix")
        self.expect(20, "ARCHITECT_JOB")
        last, _ = self.result("RESULT: FINAL_CLEAN", 0, "VERIFIED")
        self.assertEqual(last, f"VERIFIED {self.run_dir / 'verify-1.log'}")

    def test_final_fix_after_verification_failure_reviews_again_then_verifies_again(self):
        self.commit_broken()
        self.behave("pass")
        self.expect(20, "ARCHITECT_JOB")
        self.task("F1", "none")
        self.result("RESULT: FINAL_FIX F1", 21, "RUN_QUEUE")
        sh("git", "-C", str(self.impl), "-c", "user.email=t@t", "-c", "user.name=t", "rm", "-q", "BROKEN")
        sh("git", "-C", str(self.impl), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "unbreak")
        self.state.write_text(json.dumps({**self.st(), "LAST_MERGED": self.impl_head()}))
        self.behave("pass")
        last, _ = self.expect(0, "VERIFIED")
        self.assertEqual(last, f"VERIFIED {self.run_dir / 'verify-2.log'}")

    def test_final_clean_after_failed_verification_prompts_then_continue_opens_pr(self):
        self.commit_broken()
        self.behave("pass")
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
        self.behave("pass")
        self.expect(20, "ARCHITECT_JOB")
        self.result("RESULT: FINAL_CLEAN", 30, "PROMPT")
        self.expect(40, "STOP", "--reply", "stop")

    def test_wrong_reply_repeats_prompt(self):
        self.commit_broken()
        self.behave("pass")
        self.expect(20, "ARCHITECT_JOB")
        first, _ = self.result("RESULT: FINAL_CLEAN", 30, "PROMPT")
        again, _ = self.expect(30, "PROMPT", "--reply", "maybe")
        self.assertEqual(first, again)

    def test_gate_that_cannot_run_is_an_error(self):
        Path(self.st("RUN_VALUES")).write_text("FEATURE_BRANCH=feature\n")
        self.behave("pass")
        last, _ = self.expect(2, "ERROR")
        self.assertIn("NO_COMMANDS", last)


class Legacy(Finish):
    def test_old_format_state_is_refused(self):
        s = self.st()
        del s["ROLE_ARCHITECT"]
        self.state.write_text(json.dumps(s))
        last, _ = self.expect(2, "ERROR")
        self.assertIn("OLD_FORMAT_RUN", last)


class Drift(Finish):
    def setUp(self):
        super().setUp()
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"]["spec-drift"] = "/egm/skills/spec-drift"
        catalog = self.tmp / "catalog.json"
        catalog.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(catalog), "DRIFT_CHECK": "yes",
                                          "LAUNCH_STYLE_antigravity_g_high": "slug"}))

    def final_fix(self, f="F1"):
        self.task(f, "none")
        return self.result(f"RESULT: FINAL_FIX {f}", 21, "RUN_QUEUE")

    def to_drift_job(self):
        self.behave("pass", "write-output")
        return self.expect(20, "ARCHITECT_JOB")

    def test_drift_runs_after_verification_on_the_egm_pick(self):
        last, _ = self.to_drift_job()
        out = self.run_dir / "drift-1.md"
        self.assertEqual(last, f"ARCHITECT_JOB drift-done {out}")
        call = self.calls()[-1]
        self.assertEqual(call["model"], "g-high")
        self.assertEqual(call["cwd"], str(self.impl))
        self.assertIn("/egm/skills/spec-drift/SKILL.md", call["prompt"])
        self.assertIn(str(self.design), call["prompt"])
        self.assertIn(str(self.decisions), call["prompt"])

    def test_clean_drift_result_verifies(self):
        self.to_drift_job()
        last, _ = self.result("RESULT: FINAL_CLEAN", 0, "VERIFIED")
        self.assertEqual(last, f"VERIFIED {self.run_dir / 'verify-1.log'}")
        self.assertEqual(self.adv()[1], last)

    def test_drift_fix_runs_once_then_no_second_drift(self):
        self.to_drift_job()
        last, _ = self.final_fix("F1")
        self.assertEqual(last, "RUN_QUEUE F1")
        self.SCRIPT, finish = RUN_TASK, self.SCRIPT
        try:
            self.behave("commit", "pass", "pass")
            self.expect(0, "QUEUE_EMPTY")
        finally:
            self.SCRIPT = finish
        self.behave("pass")
        self.expect(0, "VERIFIED")
        self.assertEqual(sum("spec-drift" in c["prompt"] for c in self.calls()), 1)

    def test_failed_drift_worker_retries_once_then_reports_failed(self):
        self.behave("pass", "fail", "fail")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertTrue(last.startswith("ARCHITECT_JOB drift-done failed: "), last)

    def test_no_drift_check_when_not_chosen(self):
        self.state.write_text(json.dumps({**self.st(), "DRIFT_CHECK": "no"}))
        self.behave("pass")
        self.expect(0, "VERIFIED")

    def test_drift_usage_limit_prompts_and_retries(self):
        self.behave("pass", "usage", "write-output")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("5-hour limit", self.prompt_text(last))
        last, _ = self.expect(20, "ARCHITECT_JOB", "--reply", "retry")
        self.assertEqual(last, f"ARCHITECT_JOB drift-done {self.run_dir / 'drift-1.md'}")

    def test_drift_retry_uses_fresh_file_after_stale_partial_write(self):
        (self.run_dir / "drift-1.md").write_text("stale content from crashed attempt 1\n")
        self.behave("pass", "fail", "fail")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertTrue(last.startswith("ARCHITECT_JOB drift-done failed: "), last)

    def test_resume_interrupted_reviewing_and_drifting_cleans_worktree(self):
        self.state.write_text(json.dumps({**self.st(), "FIN_PHASE": "reviewing"}))
        (self.impl / "stray_fin_review.txt").write_text("oops\n")
        self.behave("pass", "write-output")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertFalse((self.impl / "stray_fin_review.txt").exists())
        self.assertEqual(last, f"ARCHITECT_JOB drift-done {self.run_dir / 'drift-1.md'}")

        self.state.write_text(json.dumps({**self.st(), "FIN_PHASE": "drifting"}))
        (self.impl / "stray_drift.txt").write_text("oops\n")
        self.behave("write-output")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertFalse((self.impl / "stray_drift.txt").exists())
        self.assertEqual(last, f"ARCHITECT_JOB drift-done {self.run_dir / 'drift-2.md'}")

    def test_drift_falls_back_to_bundled_skills_when_missing_from_catalog(self):
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"].pop("spec-drift", None)
        cat_path = self.tmp / "no-drift-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))
        last, _ = self.to_drift_job()
        self.assertEqual(last, f"ARCHITECT_JOB drift-done {self.run_dir / 'drift-1.md'}")
        self.assertIn(str(SKILL / "skills" / "spec-drift" / "SKILL.md"), self.calls()[-1]["prompt"])

    def test_drift_falls_back_to_flat_harness_sibling_skills_when_missing_from_catalog(self):
        flat_root = self.tmp / "flat-skills"
        pkg = flat_root / "agy-multiagent-workflow"
        shutil.copytree(SKILL / "scripts", pkg / "scripts")
        shutil.copytree(SKILL / "references", pkg / "references")
        (flat_root / "spec-drift").mkdir(parents=True)
        (flat_root / "spec-drift" / "SKILL.md").write_text("---\nname: spec-drift\n---\n")

        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"].pop("spec-drift", None)
        cat_path = self.tmp / "no-drift-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))

        self.SCRIPT = pkg / "scripts" / "run_finish.py"
        last, _ = self.to_drift_job()
        self.assertEqual(last, f"ARCHITECT_JOB drift-done {self.run_dir / 'drift-1.md'}")
        self.assertIn(str(flat_root / "spec-drift" / "SKILL.md"), self.calls()[-1]["prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
