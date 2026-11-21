"""Tests for scripts/run_design.py against fake_agy.py. Run: bash tests/run_all.sh"""
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
        self.state.write_text(json.dumps({**s, "TOPIC": "demo", "LAUNCH_STYLE_antigravity_small_high": "flag"}))
        (self.tmp / "concepts.md").write_text("concepts\n")
        (self.tmp / "context.md").write_text("context\n")
        self.docs_head = sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD")

    def need(self, line, expect=(20, "ARCHITECT_JOB")):
        return self.expect(*expect, "--result", self.result_file(f"RESULT: {line}"))

    def builder(self, pair="antigravity:small@high", expect=(20, "ARCHITECT_JOB")):
        return self.need(f"NEED builder-check design.md {pair}", expect)

    def docs_clean(self):
        return sh("git", "-C", str(self.docs_wt), "status", "--porcelain", "--untracked-files=all")


class Happy(Design):
    def test_builder_check_happy_path(self):
        self.behave("write-output")
        last, _ = self.builder()
        out = self.run_dir / "builder-check-1.md"
        self.assertEqual(last, f"ARCHITECT_JOB builder-check-done {out}")
        self.assertTrue(out.read_text().strip())
        self.assertTrue((self.run_dir / "results" / "builder-check-1.md").is_file())
        call = self.calls()[0]
        self.assertEqual(call["cwd"], str(self.docs_wt))
        self.assertIn("design.md", call["prompt"])
        self.assertEqual(call["model"], "small")
        self.assertEqual(call["effort"], "high")
        # asking again repeats the same job
        self.assertEqual(self.adv()[1], last)

    def test_result_line_with_trailing_period_and_prose(self):
        self.behave("write-output")
        last, _ = self.expect(20, "ARCHITECT_JOB", "--result", self.result_file(
            "RESULT: NEED builder-check design.md antigravity:small@high. A cheap reader is enough here."))
        self.assertIn("builder-check-done", last)

    def test_need_egm_records_that_egm_was_requested(self):
        self.behave("write-output")
        self.need(f"NEED egm design.md {FINDINGS}")
        self.assertEqual(self.st("EGM_REQUESTED"), "yes")

    def test_unexpected_result_is_an_error(self):
        self.expect(2, "ERROR", "--result", self.result_file("RESULT: PLAN_APPROVED x"))
        self.assertIn("UNEXPECTED_RESULT", self.adv("--result", self.result_file("RESULT: COMMITTED"))[1])

    def test_builder_check_probes_its_pair_first(self):
        s = self.st()
        del s["LAUNCH_STYLE_antigravity_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("ok", "write-output")
        last, _ = self.builder()
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_small_high"), "slug")
        self.assertIn("builder-check-done", last)
        self.assertEqual(len(self.calls()), 2)

    def test_builder_probe_failure_goes_to_architect(self):
        s = self.st()
        del s["LAUNCH_STYLE_antigravity_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("fail", "fail")
        last, _ = self.builder()
        self.assertTrue(last.startswith("ARCHITECT_JOB builder-check-done failed: "), last)
        failure = Path(last.split("failed: ")[1])
        self.assertIn("failed", failure.read_text())
        self.assertEqual(len(self.calls()), 2)

    def test_builder_pair_outside_architect_harness_fails_to_architect(self):
        last, _ = self.builder("other:g@high")
        self.assertTrue(last.startswith("ARCHITECT_JOB builder-check-done failed: "), last)
        self.assertIn("architect's harness", Path(last.split("failed: ")[1]).read_text())
        self.assertEqual(self.calls(), [])

    def test_consult_on_ask_then_answer_resumes(self):
        self.behave("ask", "write-output")
        last, _ = self.builder(expect=(22, "CONSULT"))
        ask_file = Path(last.split()[1])
        self.assertIn("Which edge case", ask_file.read_text())
        ans = self.tmp / "ans.md"
        ans.write_text("Use strict validation.\n")
        last, _ = self.expect(20, "ARCHITECT_JOB", "--answer", str(ans))
        self.assertIn("builder-check-done", last)
        self.assertEqual(self.calls()[1]["conversation"], "conv_1")

    def test_consult_expired_conversation_falls_back_cleanly(self):
        self.behave("ask", "write-output")
        self.builder(expect=(22, "CONSULT"))
        w = json.loads(self.world.read_text())
        w["expired_conversations"] = ["conv_1"]
        self.world.write_text(json.dumps(w))
        ans = self.tmp / "ans.md"
        ans.write_text("Use strict validation.\n" + "x" * 400 + "\n")
        last, _ = self.expect(20, "ARCHITECT_JOB", "--answer", str(ans))
        self.assertIn("builder-check-done", last)
        self.assertEqual(self.calls()[2]["conversation"], "")
        self.assertIn("Use strict validation.", self.calls()[2]["prompt"])


class DocsTree(Design):
    def test_dirty_docs_tree_sends_commit_docs_then_resumes(self):
        self.behave("write-output")
        (self.docs_wt / "plan.md").write_text("uncommitted architect file\n")
        last, _ = self.builder()
        self.assertTrue(last.startswith("ARCHITECT_JOB commit-docs "), last)
        self.assertIn("plan.md", Path(last.split(" ", 2)[2]).read_text())
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.docs_wt / "plan.md").exists())
        sh("git", "-C", str(self.docs_wt), "add", "plan.md")
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "plan")
        last, _ = self.expect(20, "ARCHITECT_JOB", "--result", self.result_file("RESULT: COMMITTED"))
        self.assertEqual(self.st("DOCS_HEAD"), sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"))
        self.assertIn("builder-check-done", last)
        self.assertTrue((self.docs_wt / "plan.md").exists())

    def test_worker_edit_is_discarded_output_kept(self):
        self.behave({"do": "write-output", "edit": True})
        self.builder()
        self.assertEqual(self.docs_clean(), "")
        self.assertEqual(sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"), self.docs_head)
        self.assertEqual((self.docs_wt / "a.txt").read_text(), "a\n")
        self.assertTrue((self.run_dir / "builder-check-1.md").read_text().strip())


class Failures(Design):
    def test_failed_twice_sends_failed_input(self):
        self.behave("fail", "fail")
        last, _ = self.builder()
        saved = self.run_dir / "results" / "builder-check-2.md"
        self.assertEqual(last, f"ARCHITECT_JOB builder-check-done failed: {saved}")
        self.assertIn("could not make the test pass", saved.read_text())
        self.assertEqual(len(self.calls()), 2)

    def test_empty_output_counts_as_failed_then_retry_succeeds(self):
        self.behave("write-empty", "write-output")
        last, _ = self.builder()
        self.assertEqual(last, f"ARCHITECT_JOB builder-check-done {self.run_dir / 'builder-check-2.md'}")
        self.assertEqual(self.st("RETRY_builder-check"), "0")
        self.assertEqual(len(self.calls()), 2)

    def test_usage_limit_counts_as_failed(self):
        self.behave("usage", "usage")
        last, _ = self.builder()
        self.assertIn("failed: ", last)
        self.assertIn("5-hour limit", Path(last.split("failed: ")[1]).read_text())


class Egm(Design):
    def egm_need(self, expect=(20, "ARCHITECT_JOB")):
        return self.need(f"NEED egm design.md {FINDINGS}", expect)

    def test_egm_launch_uses_role_egm(self):
        self.behave("write-output")
        last, _ = self.egm_need()
        call = self.calls()[0]
        self.assertEqual((call["model"], call["effort"]), ("g", "high"))
        out = self.run_dir / "egm-1.md"
        self.assertEqual(last, f"ARCHITECT_JOB egm-done {out}")
        self.assertEqual(out.read_text(), (self.docs_wt / FINDINGS).read_text())
        self.assertIn("Important: x.md:1 unclear.", out.read_text())
        self.assertEqual(self.st("EGM_SKILLS"), "/skills")
        self.assertIn("/skills/egm-review/SKILL.md", call["prompt"])
        self.assertEqual(self.docs_clean(), f"?? {FINDINGS}")

    def commit_findings(self):
        sh("git", "-C", str(self.docs_wt), "add", "-f", FINDINGS)
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "findings", "--allow-empty")

    def test_egm_third_run_sends_egm_limit(self):
        self.behave("write-output", "write-output")
        self.egm_need()
        self.commit_findings()
        self.egm_need()
        self.commit_findings()
        last, _ = self.egm_need()
        self.assertEqual(last, "ARCHITECT_JOB egm-limit none")
        self.assertEqual(self.st("EGM_RUNS"), "3")
        self.assertEqual(len(self.calls()), 2)

    def test_egm_retry_does_not_count_a_second_run(self):
        self.behave("fail", "write-output")
        self.egm_need()
        self.assertEqual(self.st("EGM_RUNS"), "1")

    def test_failed_egm_with_partial_output_is_cleaned_before_retry(self):
        partial = self.docs_wt / FINDINGS
        self.behave({"do": "response", "status": "ERROR", "rc": 1, "edit": str(partial), "edit_text": "partial\n"},
                    "write-output")
        last, _ = self.egm_need()
        self.assertEqual(last, f"ARCHITECT_JOB egm-done {self.run_dir / 'egm-1.md'}")
        self.assertEqual(len(self.calls()), 2)

    def test_resume_interrupted_dispatching_cleans_docs_worktree_and_restarts(self):
        self.state.write_text(json.dumps({
            **self.st(),
            "DESIGN_PHASE": "dispatching",
            "DESIGN_NEED": f"builder-check design.md antigravity:small@high",
            "DOCS_HEAD": self.docs_head,
        }))
        (self.docs_wt / "stray_design.txt").write_text("partial\n")
        self.behave("write-output")
        last, _ = self.expect(20, "ARCHITECT_JOB")
        self.assertFalse((self.docs_wt / "stray_design.txt").exists())
        self.assertIn("builder-check-done", last)

    def test_consult_silent_new_conversation_cleans_and_falls_back(self):
        stray = self.docs_wt / "stray_unresumed.txt"
        self.behave(
            "ask",
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "write-output",
        )
        self.egm_need(expect=(22, "CONSULT"))
        ans = self.tmp / "ans.md"
        ans.write_text("Clarified.\n")
        last, _ = self.expect(20, "ARCHITECT_JOB", "--answer", str(ans))
        self.assertEqual(last, f"ARCHITECT_JOB egm-done {self.run_dir / 'egm-1.md'}")
        self.assertFalse(stray.exists())
        self.assertEqual(self.calls()[2]["conversation"], "")
        self.assertIn("Clarified.", self.calls()[2]["prompt"])

    def test_egm_falls_back_to_bundled_skills_when_missing_from_catalog(self):
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"].pop("egm-review", None)
        cat_path = self.tmp / "no-egm-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))
        self.behave("write-output")
        last, _ = self.egm_need()
        self.assertEqual(last, f"ARCHITECT_JOB egm-done {self.run_dir / 'egm-1.md'}")
        self.assertEqual(self.st("EGM_SKILLS"), str(SKILL / "skills"))
        self.assertIn(str(SKILL / "skills" / "egm-review" / "SKILL.md"), self.calls()[0]["prompt"])


class Lens(Design):
    def lens_need(self, expect=(20, "ARCHITECT_JOB")):
        return self.need(f"NEED lens {self.tmp / 'concepts.md'}", expect)

    def test_lens_second_run_reuses_first_findings(self):
        self.behave("write-output")
        last, _ = self.lens_need()
        first = self.run_dir / "lens-1.md"
        self.assertEqual(last, f"ARCHITECT_JOB lens-done {first}")
        self.assertIn("egm-brainstorm", self.calls()[0]["prompt"])
        last, _ = self.lens_need()
        self.assertEqual(last, f"ARCHITECT_JOB lens-done {first}")
        self.assertEqual(len(self.calls()), 1)

    def test_lens_resolves_egm_brainstorm_independently_of_egm_review_override(self):
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"]["egm-review"] = "/override/skills/egm-review"
        cat["harnesses"]["antigravity"]["skills"].pop("egm-brainstorm", None)
        cat_path = self.tmp / "override-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))
        self.behave("write-output", "write-output")
        self.need(f"NEED egm design.md {FINDINGS}")
        self.assertEqual(self.st("EGM_SKILLS"), "/override/skills")
        sh("git", "-C", str(self.docs_wt), "add", "-f", FINDINGS)
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "findings")
        last, _ = self.lens_need()
        self.assertEqual(last, f"ARCHITECT_JOB lens-done {self.run_dir / 'lens-1.md'}")
        self.assertIn(str(SKILL / "skills" / "egm-brainstorm" / "SKILL.md"), self.calls()[1]["prompt"])


class PlanReview(Design):
    def review_need(self, expect=(20, "ARCHITECT_JOB")):
        return self.need(f"NEED plan-review {self.tmp / 'plan.md'} {self.tmp / 'context.md'}", expect)

    def test_plan_review_limit_input(self):
        (self.tmp / "plan.md").write_text("plan\n")
        self.behave("write-output", "write-output")
        last, _ = self.review_need()
        self.assertEqual(last, f"ARCHITECT_JOB plan-review-done {self.run_dir / 'plan-review-1.md'}")
        self.assertIn("context", self.calls()[0]["prompt"])
        self.review_need()
        last, _ = self.review_need()
        self.assertEqual(last, "ARCHITECT_JOB plan-review-done Plan review limit reached; "
                               f"finish triage with {self.run_dir / 'plan-review-2.md'}")
        self.assertEqual(len(self.calls()), 2)

    def test_plan_review_resolves_egm_review_loop_from_its_own_dir(self):
        (self.tmp / "plan.md").write_text("plan\n")
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"]["egm-review"] = "/override/skills/egm-review"
        cat["harnesses"]["antigravity"]["skills"]["egm-review-loop"] = "/other/skills/egm-review-loop"
        cat_path = self.tmp / "split-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))
        self.behave("write-output", "write-output")
        self.need(f"NEED egm design.md {FINDINGS}")
        sh("git", "-C", str(self.docs_wt), "add", "-f", FINDINGS)
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "findings")
        last, _ = self.review_need()
        self.assertEqual(last, f"ARCHITECT_JOB plan-review-done {self.run_dir / 'plan-review-1.md'}")
        self.assertIn("/other/skills/egm-review-loop/SKILL.md", self.calls()[1]["prompt"])


if __name__ == "__main__":
    unittest.main()
