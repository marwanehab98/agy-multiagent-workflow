"""Tests for scripts/run_design.py against the fake Orca CLI. Run: bash tests/run_all.sh"""
import json
import unittest
from pathlib import Path

from helpers import Base, SKILL, sh

FINDINGS = ".run/egm-findings.md"


class Design(Base):
    SCRIPT = SKILL / "scripts" / "run_design.py"

    def setUp(self):
        super().setUp()
        s = self.st()
        (self.docs_wt / "design.md").write_text("design\n")
        sh("git", "-C", str(self.docs_wt), "add", "design.md")
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "design")
        self.state.write_text(json.dumps({**s, "TOPIC": "demo", "LAUNCH_STYLE_cl_small_high": "flag"}))
        (self.tmp / "concepts.md").write_text("concepts\n")
        (self.tmp / "context.md").write_text("context\n")
        self.docs_head = sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD")

    def need(self, line, expect=(10, "WAIT")):
        return self.expect(*expect, "--result", self.result_file(f"RESULT: {line}"))

    def builder(self, pair="cl:small@high", expect=(10, "WAIT")):
        return self.need(f"NEED builder-check design.md {pair}", expect)

    def docs_clean(self):
        return sh("git", "-C", str(self.docs_wt), "status", "--porcelain", "--untracked-files=all")

    def starts(self):
        return self.calls("worker-start")


class Happy(Design):
    def test_builder_check_happy_path(self):
        self.behave("write-output")
        last, _ = self.builder()
        self.assertTrue(last.startswith("WAIT ctx_1 builder-check"), last)
        last, _ = self.expect(20, "ARCHITECT_JOB")
        out = self.run_dir / "builder-check-1.md"
        self.assertEqual(last, f"ARCHITECT_JOB builder-check-done {out}")
        self.assertTrue(out.read_text().strip())
        self.assertTrue((self.run_dir / "results" / "builder-check-1.md").is_file())
        task = json.loads(self.world.read_text())["tasks"]["task_1"]
        self.assertEqual(task["title"], "Builder check: demo")
        self.assertIn("design.md", task["spec"])
        start = self.starts()[0]
        self.assertIn("cl-agent", start)
        self.assertIn(f"path:{self.docs_wt}", start)
        self.assertEqual(len(self.calls("worker-release")), 1)
        # asking again repeats the same job
        self.assertEqual(self.adv()[1], last)

    def test_wait_is_idempotent_while_worker_runs(self):
        self.behave("pending")
        first, _ = self.builder()
        again, _ = self.expect(10, "WAIT")
        self.assertEqual(first, again)
        self.assertEqual(len(self.starts()), 1)

    def test_unexpected_result_is_an_error(self):
        self.expect(2, "ERROR", "--result", self.result_file("RESULT: PLAN_APPROVED x"))
        self.assertIn("UNEXPECTED_RESULT", self.adv("--result", self.result_file("RESULT: COMMITTED"))[1])

    def test_builder_check_probes_its_pair_first(self):
        s = self.st()
        del s["LAUNCH_STYLE_cl_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("ok", "write-output")
        last, _ = self.builder()
        self.assertTrue(last.endswith(" probe"), last)
        last, _ = self.expect(10, "WAIT")
        self.assertTrue(last.endswith(" builder-check"), last)
        self.assertEqual(self.st("LAUNCH_STYLE_cl_small_high"), "flag")
        self.expect(20, "ARCHITECT_JOB")

    def test_builder_probe_wait_sets_waiting_on(self):
        s = self.st()
        del s["LAUNCH_STYLE_cl_small_high"]
        self.state.write_text(json.dumps({**s, "WAITING_ON": "ctx_architect"}))
        self.behave("pending")
        last, _ = self.builder()
        self.assertTrue(last.endswith(" probe"), last)
        self.assertEqual(self.st("WAITING_ON"), last.split()[1])
        self.assertEqual(self.st("STEP"), "run_design probe")

    def test_builder_probe_failure_goes_to_architect(self):
        s = self.st()
        del s["LAUNCH_STYLE_cl_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("fail")
        self.builder()
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertTrue(last.startswith("ARCHITECT_JOB builder-check-done failed: "), last)
        failure = Path(last.split("failed: ")[1])
        self.assertIn("failed", failure.read_text())
        self.assertEqual(len(self.starts()), 1)

    def test_builder_pair_outside_architect_harness_fails_to_architect(self):
        last, _ = self.builder("ag:g@high", expect=(20, "ARCHITECT_JOB"))
        self.assertTrue(last.startswith("ARCHITECT_JOB builder-check-done failed: "), last)
        self.assertIn("architect's harness", Path(last.split("failed: ")[1]).read_text())
        self.assertEqual(self.starts(), [])


class DocsTree(Design):
    def test_dirty_docs_tree_sends_commit_docs_then_resumes(self):
        self.behave("write-output")
        (self.docs_wt / "plan.md").write_text("uncommitted architect file\n")
        last, _ = self.builder(expect=(20, "ARCHITECT_JOB"))
        self.assertTrue(last.startswith("ARCHITECT_JOB commit-docs "), last)
        self.assertIn("plan.md", Path(last.split(" ", 2)[2]).read_text())
        self.assertEqual(self.starts(), [])
        self.assertTrue((self.docs_wt / "plan.md").exists())  # never discarded
        sh("git", "-C", str(self.docs_wt), "add", "plan.md")
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "plan")
        self.expect(10, "WAIT", "--result", self.result_file("RESULT: COMMITTED"))
        self.assertEqual(self.st("DOCS_HEAD"), sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"))
        self.expect(20, "ARCHITECT_JOB")
        self.assertTrue((self.docs_wt / "plan.md").exists())

    def test_worker_edit_is_discarded_output_kept(self):
        self.behave({"do": "write-output", "edit": True})
        self.builder()
        self.expect(20, "ARCHITECT_JOB")
        self.assertEqual(self.docs_clean(), "")
        self.assertEqual(sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"), self.docs_head)
        self.assertEqual((self.docs_wt / "a.txt").read_text(), "a\n")
        self.assertTrue((self.run_dir / "builder-check-1.md").read_text().strip())


    def architect_commit(self, wt):
        """What a consult or user-message architect job does while the worker runs: commit a decision."""
        (wt / "decisions.md").write_text("decision recorded mid-wait\n")
        sh("git", "-C", str(wt), "add", "decisions.md")
        sh("git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "decision")
        return sh("git", "-C", str(wt), "rev-parse", "HEAD")

    def test_docs_moved_keeps_architect_commit_discards_worker_edit(self):
        self.behave({"do": "write-output", "edit": True})
        self.builder()
        head = self.architect_commit(self.docs_wt)
        self.expect(20, "ARCHITECT_JOB", "--docs-moved")
        self.assertEqual(sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"), head)
        self.assertEqual(self.docs_clean(), "")  # the worker's uncommitted edit is still discarded
        self.assertEqual(self.st("DOCS_HEAD"), head)
        self.assertIn("docs worktree moved", (self.run_dir / "run-log.md").read_text())

    def test_without_docs_moved_the_reset_still_applies(self):
        self.behave({"do": "write-output", "edit": True})
        self.builder()
        self.architect_commit(self.docs_wt)
        self.expect(20, "ARCHITECT_JOB")
        self.assertEqual(sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"), self.docs_head)

    def test_docs_moved_during_builder_probe_moves_probe_head(self):
        s = self.st()
        del s["LAUNCH_STYLE_cl_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("ok-edit", "write-output")
        last, _ = self.builder()
        self.assertTrue(last.endswith(" probe"), last)
        head = self.architect_commit(self.docs_wt)
        last, _ = self.expect(10, "WAIT", "--docs-moved")
        self.assertTrue(last.endswith(" builder-check"), last)
        self.assertEqual(sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"), head)
        self.assertEqual(self.docs_clean(), "")


class Failures(Design):
    def test_failed_twice_sends_failed_input(self):
        self.behave("fail", "fail")
        self.builder()
        self.assertTrue(self.expect(10, "WAIT")[0].startswith("WAIT ctx_2 "))  # retried once
        last, _ = self.expect(20, "ARCHITECT_JOB")
        saved = self.run_dir / "results" / "builder-check-2.md"
        self.assertEqual(last, f"ARCHITECT_JOB builder-check-done failed: {saved}")
        self.assertIn("could not make the test pass", saved.read_text())
        self.assertEqual(len(self.starts()), 2)

    def test_empty_output_counts_as_failed_then_retry_succeeds(self):
        self.behave("write-empty", "write-output")
        self.builder()
        self.expect(10, "WAIT")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertEqual(last, f"ARCHITECT_JOB builder-check-done {self.run_dir / 'builder-check-2.md'}")
        self.assertEqual(self.st("RETRY_builder-check"), "0")
        self.assertEqual(len(self.starts()), 2)

    def test_idle_worker_is_nudged_once_then_failed(self):
        self.behave("idle-forever", "write-output")
        self.builder()
        self.expect(10, "WAIT")  # nudged
        self.assertEqual(len(self.calls("send")), 1)
        last, _ = self.expect(10, "WAIT")  # second idle: failed, retried on a new worker
        self.assertEqual(len(self.calls("send")), 1)
        self.assertEqual(len(self.starts()), 2)
        self.expect(20, "ARCHITECT_JOB")

    def test_usage_limit_counts_as_failed(self):
        self.behave("usage", "usage")
        self.builder()
        self.expect(10, "WAIT")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertIn("failed: ", last)
        self.assertIn("5-hour limit", Path(last.split("failed: ")[1]).read_text())


class Egm(Design):
    def egm_need(self, expect=(10, "WAIT")):
        return self.need(f"NEED egm design.md {FINDINGS}", expect)

    def test_egm_launch_uses_role_egm(self):
        self.behave("write-output")
        self.egm_need()
        start = self.starts()[0]
        self.assertIn("ag-agent", start)
        self.assertIn("g-high", start)
        last, _ = self.expect(20, "ARCHITECT_JOB")
        out = self.run_dir / "egm-1.md"
        self.assertEqual(last, f"ARCHITECT_JOB egm-done {out}")
        self.assertEqual(out.read_text(), (self.docs_wt / FINDINGS).read_text())
        self.assertIn("Important: x.md:1 unclear.", out.read_text())
        self.assertEqual(self.st("EGM_SKILLS"), "/skills")
        task = json.loads(self.world.read_text())["tasks"]["task_1"]
        self.assertEqual(task["title"], "EGM review: demo")
        self.assertIn("/skills/egm-review/SKILL.md", task["spec"])
        self.assertEqual(self.docs_clean(), f"?? {FINDINGS}")  # allowed, left for the architect to commit

    def commit_findings(self):
        """What the architect does on egm-done: commit the findings file."""
        sh("git", "-C", str(self.docs_wt), "add", "-f", FINDINGS)
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "findings", "--allow-empty")

    def test_egm_third_run_sends_egm_limit(self):
        self.behave("write-output", "write-output")
        self.egm_need()
        self.expect(20, "ARCHITECT_JOB")
        self.commit_findings()
        self.egm_need()
        self.expect(20, "ARCHITECT_JOB")
        self.commit_findings()
        last, _ = self.egm_need(expect=(20, "ARCHITECT_JOB"))
        self.assertEqual(last, "ARCHITECT_JOB egm-limit none")
        self.assertEqual(self.st("EGM_RUNS"), "3")
        self.assertEqual(len(self.starts()), 2)

    def test_egm_retry_does_not_count_a_second_run(self):
        self.behave("fail", "write-output")
        self.egm_need()
        self.expect(10, "WAIT")
        self.expect(20, "ARCHITECT_JOB")
        self.assertEqual(self.st("EGM_RUNS"), "1")


class Lens(Design):
    def lens_need(self, expect=(10, "WAIT")):
        return self.need(f"NEED lens {self.tmp / 'concepts.md'}", expect)

    def test_lens_second_run_reuses_first_findings(self):
        self.behave("write-output")
        self.lens_need()
        last, _ = self.expect(20, "ARCHITECT_JOB")
        first = self.run_dir / "lens-1.md"
        self.assertEqual(last, f"ARCHITECT_JOB lens-done {first}")
        self.assertEqual(json.loads(self.world.read_text())["tasks"]["task_1"]["title"], "EGM lens pass: demo")
        last, _ = self.lens_need(expect=(20, "ARCHITECT_JOB"))
        self.assertEqual(last, f"ARCHITECT_JOB lens-done {first}")
        self.assertEqual(len(self.starts()), 1)


class PlanReview(Design):
    def review_need(self, expect=(10, "WAIT")):
        return self.need(f"NEED plan-review {self.tmp / 'plan.md'} {self.tmp / 'context.md'}", expect)

    def test_plan_review_limit_input(self):
        (self.tmp / "plan.md").write_text("plan\n")
        self.behave("write-output", "write-output")
        self.review_need()
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertEqual(last, f"ARCHITECT_JOB plan-review-done {self.run_dir / 'plan-review-1.md'}")
        task = json.loads(self.world.read_text())["tasks"]["task_1"]
        self.assertEqual(task["title"], "EGM plan review: demo")
        self.assertIn("context", task["spec"])
        self.review_need()
        self.expect(20, "ARCHITECT_JOB")
        last, _ = self.review_need(expect=(20, "ARCHITECT_JOB"))
        self.assertEqual(last, "ARCHITECT_JOB plan-review-done Plan review limit reached; "
                               f"finish triage with {self.run_dir / 'plan-review-2.md'}")
        self.assertEqual(len(self.starts()), 2)


if __name__ == "__main__":
    unittest.main()
