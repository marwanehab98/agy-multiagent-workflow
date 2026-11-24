"""Tests for scripts/run_design.py against fake_agy.py. Run: bash tests/run_all.sh"""
import json
import shutil
import unittest
from pathlib import Path

from helpers import Base, SKILL, sh

FINDINGS = ".mission/safety-findings.md"


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

    def need(self, line, expect=(20, "DIRECTOR_JOB")):
        return self.expect(*expect, "--result", self.result_file(f"RESULT: {line}"))

    def builder(self, pair="antigravity:small@high", expect=(20, "DIRECTOR_JOB")):
        return self.need(f"NEED preflight-check design.md {pair}", expect)

    def docs_clean(self):
        return sh("git", "-C", str(self.docs_wt), "status", "--porcelain", "--untracked-files=all")

    @staticmethod
    def write_dirs(call):
        """The folders a read-only agy worker may write to (its --settings write_file rules)."""
        settings = json.loads(call["argv"][call["argv"].index("--settings") + 1])
        return [a[len("write_file("):-1] for a in settings["permissions"]["allow"] if a.startswith("write_file(")]


class Happy(Design):
    def test_preflight_check_happy_path(self):
        self.behave("write-output")
        last, _ = self.builder()
        out = self.run_dir / "preflight-check-1.md"
        self.assertEqual(last, f"DIRECTOR_JOB preflight-check-done {out}")
        self.assertTrue(out.read_text().strip())
        self.assertTrue((self.run_dir / "results" / "preflight-check-1.md").is_file())
        call = self.calls()[0]
        self.assertEqual(call["cwd"], str(self.docs_wt))
        self.assertIn("design.md", call["prompt"])
        self.assertEqual(call["model"], "small")
        self.assertEqual(call["effort"], "high")
        # asking again repeats the same job
        self.assertEqual(self.adv()[1], last)

    def test_result_line_with_trailing_period_and_prose(self):
        self.behave("write-output")
        last, _ = self.expect(20, "DIRECTOR_JOB", "--result", self.result_file(
            "RESULT: NEED preflight-check design.md antigravity:small@high. A cheap reader is enough here."))
        self.assertIn("preflight-check-done", last)

    def test_need_safety_records_that_safety_was_requested(self):
        self.behave("write-output")
        self.need(f"NEED safety design.md {FINDINGS}")
        self.assertEqual(self.st("SAFETY_REQUESTED"), "yes")

    def test_unexpected_result_is_an_error(self):
        self.expect(2, "ERROR", "--result", self.result_file("RESULT: PLAN_APPROVED x"))
        self.assertIn("UNEXPECTED_RESULT", self.adv("--result", self.result_file("RESULT: COMMITTED"))[1])

    def test_preflight_check_probes_its_pair_first(self):
        s = self.st()
        del s["LAUNCH_STYLE_antigravity_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("ok", "write-output")
        last, _ = self.builder()
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_small_high"), "slug")
        self.assertIn("preflight-check-done", last)
        self.assertEqual(len(self.calls()), 2)

    def test_preflight_probe_usage_limit_exits_31_and_the_next_call_probes_again(self):
        s = self.st()
        del s["LAUNCH_STYLE_antigravity_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("usage")
        last, _ = self.builder(expect=(31, "USAGE_LIMIT"))
        self.assertTrue(last.startswith("USAGE_LIMIT antigravity:small@high "), last)
        self.assertIn("5-hour limit", last)
        self.assertEqual(self.st("DESIGN_PHASE"), "probe")
        self.assertIsNone(self.st("LAUNCH_STYLE_antigravity_small_high"))
        self.behave("ok", "write-output")
        last, _ = self.expect(20, "DIRECTOR_JOB")
        self.assertEqual(last, f"DIRECTOR_JOB preflight-check-done {self.run_dir / 'preflight-check-1.md'}")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_small_high"), "slug")

    def test_builder_probe_failure_goes_to_director(self):
        s = self.st()
        del s["LAUNCH_STYLE_antigravity_small_high"]
        self.state.write_text(json.dumps(s))
        self.behave("fail", "fail")
        last, _ = self.builder()
        self.assertTrue(last.startswith("DIRECTOR_JOB preflight-check-done failed: "), last)
        failure = Path(last.split("failed: ")[1])
        self.assertIn("failed", failure.read_text())
        self.assertEqual(len(self.calls()), 2)

    def test_builder_pair_outside_director_harness_fails_to_director(self):
        last, _ = self.builder("other:g@high")
        self.assertTrue(last.startswith("DIRECTOR_JOB preflight-check-done failed: "), last)
        self.assertIn("flight director's harness", Path(last.split("failed: ")[1]).read_text())
        self.assertEqual(self.calls(), [])

    def test_consult_on_ask_then_answer_resumes(self):
        self.behave("ask", "write-output")
        last, _ = self.builder(expect=(22, "CONSULT"))
        ask_file = Path(last.split()[1])
        self.assertIn("Which edge case", ask_file.read_text())
        ans = self.tmp / "ans.md"
        ans.write_text("Use strict validation.\n")
        last, _ = self.expect(20, "DIRECTOR_JOB", "--answer", str(ans))
        self.assertIn("preflight-check-done", last)
        self.assertEqual(self.calls()[1]["conversation"], "conv_1")

    def test_consult_expired_conversation_falls_back_cleanly(self):
        self.behave("ask", "write-output")
        self.builder(expect=(22, "CONSULT"))
        w = json.loads(self.world.read_text())
        w["expired_conversations"] = ["conv_1"]
        self.world.write_text(json.dumps(w))
        ans = self.tmp / "ans.md"
        ans.write_text("Use strict validation.\n" + "x" * 400 + "\n")
        last, _ = self.expect(20, "DIRECTOR_JOB", "--answer", str(ans))
        self.assertIn("preflight-check-done", last)
        self.assertEqual(self.calls()[2]["conversation"], "")
        self.assertIn("Use strict validation.", self.calls()[2]["prompt"])


class DocsTree(Design):
    def test_dirty_docs_tree_sends_commit_docs_then_resumes(self):
        self.behave("write-output")
        (self.docs_wt / "plan.md").write_text("uncommitted flight director file\n")
        last, _ = self.builder()
        self.assertTrue(last.startswith("DIRECTOR_JOB commit-docs "), last)
        self.assertIn("plan.md", Path(last.split(" ", 2)[2]).read_text())
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.docs_wt / "plan.md").exists())
        sh("git", "-C", str(self.docs_wt), "add", "plan.md")
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "plan")
        last, _ = self.expect(20, "DIRECTOR_JOB", "--result", self.result_file("RESULT: COMMITTED"))
        self.assertEqual(self.st("DOCS_HEAD"), sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"))
        self.assertIn("preflight-check-done", last)
        self.assertTrue((self.docs_wt / "plan.md").exists())

    def test_a_failing_git_status_is_an_error_not_a_dirty_tree(self):
        index = Path(sh("git", "-C", str(self.docs_wt), "rev-parse", "--path-format=absolute", "--git-path", "index"))
        good = index.read_bytes()
        index.write_bytes(b"not an index")
        try:
            last, _ = self.builder(expect=(2, "ERROR"))
        finally:
            index.write_bytes(good)
        self.assertIn("CHECK_TREE STATUS_FAILED", last)
        self.assertEqual(self.calls(), [])

    def test_worker_edit_is_discarded_and_counts_as_failed(self):
        self.behave({"do": "write-output", "edit": True}, "write-output")
        last, _ = self.builder()
        self.assertEqual(last, f"DIRECTOR_JOB preflight-check-done {self.run_dir / 'preflight-check-2.md'}")
        self.assertEqual(self.docs_clean(), "")
        self.assertEqual(sh("git", "-C", str(self.docs_wt), "rev-parse", "HEAD"), self.docs_head)
        self.assertEqual((self.docs_wt / "a.txt").read_text(), "a\n")
        self.assertIn("changed the docs worktree", (self.run_dir / "results" / "preflight-check-1.md").read_text())
        self.assertEqual(self.st("RETRY_preflight-check"), "0")
        self.assertEqual(len(self.calls()), 2)

    def test_worker_that_edits_twice_sends_failed(self):
        self.behave({"do": "write-output", "edit": True}, {"do": "write-output", "edit": True})
        last, _ = self.builder()
        saved = self.run_dir / "results" / "preflight-check-2.md"
        self.assertEqual(last, f"DIRECTOR_JOB preflight-check-done failed: {saved}")
        self.assertIn("changed the docs worktree", saved.read_text())
        self.assertEqual(self.docs_clean(), "")

    def test_safety_worker_that_edits_beyond_its_findings_counts_as_failed(self):
        self.behave({"do": "write-output", "edit": True}, "write-output")
        last, _ = self.need(f"NEED safety design.md {FINDINGS}")
        self.assertEqual(last, f"DIRECTOR_JOB safety-done {self.run_dir / 'safety-2.md'}")
        self.assertIn("changed the docs worktree", (self.run_dir / "results" / "safety-1.md").read_text())
        self.assertEqual(self.docs_clean(), f"?? {FINDINGS}")


class Failures(Design):
    def test_failed_twice_sends_failed_input(self):
        self.behave("fail", "fail")
        last, _ = self.builder()
        saved = self.run_dir / "results" / "preflight-check-2.md"
        self.assertEqual(last, f"DIRECTOR_JOB preflight-check-done failed: {saved}")
        self.assertIn("could not make the test pass", saved.read_text())
        self.assertEqual(len(self.calls()), 2)

    def test_empty_output_counts_as_failed_then_retry_succeeds(self):
        self.behave("write-empty", "write-output")
        last, _ = self.builder()
        self.assertEqual(last, f"DIRECTOR_JOB preflight-check-done {self.run_dir / 'preflight-check-2.md'}")
        self.assertEqual(self.st("RETRY_preflight-check"), "0")
        self.assertEqual(len(self.calls()), 2)

    def test_usage_limit_counts_as_failed(self):
        self.behave("usage", "usage")
        last, _ = self.builder()
        self.assertIn("failed: ", last)
        self.assertIn("5-hour limit", Path(last.split("failed: ")[1]).read_text())


class RangeSafety(Design):
    def safety_need(self, expect=(20, "DIRECTOR_JOB")):
        return self.need(f"NEED safety design.md {FINDINGS}", expect)

    def test_safety_launch_uses_role_safety(self):
        self.behave("write-output")
        last, _ = self.safety_need()
        call = self.calls()[0]
        self.assertEqual((call["model"], call["effort"]), ("g", "high"))
        out = self.run_dir / "safety-1.md"
        self.assertEqual(last, f"DIRECTOR_JOB safety-done {out}")
        self.assertEqual(out.read_text(), (self.docs_wt / FINDINGS).read_text())
        self.assertIn("Important: x.md:1 unclear.", out.read_text())
        self.assertEqual(self.st("SAFETY_SKILLS"), "/skills")
        self.assertIn("/skills/flight-readiness/SKILL.md", call["prompt"])
        self.assertEqual(self.docs_clean(), f"?? {FINDINGS}")

    def commit_findings(self):
        sh("git", "-C", str(self.docs_wt), "add", "-f", FINDINGS)
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "findings", "--allow-empty")

    def test_safety_third_run_sends_safety_limit(self):
        self.behave("write-output", "write-output")
        self.safety_need()
        self.commit_findings()
        self.safety_need()
        self.commit_findings()
        last, _ = self.safety_need()
        self.assertEqual(last, "DIRECTOR_JOB safety-limit none")
        self.assertEqual(self.st("SAFETY_RUNS"), "3")
        self.assertEqual(len(self.calls()), 2)

    def test_findings_path_is_taken_relative_to_the_docs_worktree(self):
        # check-tree.sh --allow matches git's porcelain path exactly: ./x and an absolute path must become x.
        for n, spelled in enumerate((f"./{FINDINGS}", str(self.docs_wt / FINDINGS),
                                     f"{self.docs_wt}/sub/../{FINDINGS}"), 1):
            with self.subTest(spelled=spelled):
                self.behave("write-output")
                last, _ = self.need(f"NEED safety design.md {spelled}")
                self.assertEqual(self.st("DESIGN_NEED"), f"safety\tdesign.md\t{FINDINGS}")
                self.assertEqual(last, f"DIRECTOR_JOB safety-done {self.run_dir / f'safety-{n}.md'}")
                self.assertEqual(self.docs_clean(), f"?? {FINDINGS}")  # kept, not discarded as a stray change
                (self.docs_wt / FINDINGS).unlink()
                s = self.st()
                s["SAFETY_RUNS"] = "0"  # below the run limit for the next spelling
                self.state.write_text(json.dumps(s))

    def test_findings_path_outside_the_docs_worktree_is_a_bad_need(self):
        for spelled in (str(self.tmp / "findings.md"), "../findings.md", "."):
            with self.subTest(spelled=spelled):
                last, _ = self.need(f"NEED safety design.md {spelled}", (2, "ERROR"))
                self.assertIn("BAD_NEED", last)
        self.assertEqual(self.calls(), [])

    def test_findings_path_through_a_symlink_out_of_the_docs_worktree_is_a_bad_need(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.docs_wt / "out").symlink_to(outside)
        for spelled in ("out/findings.md", str(self.docs_wt / "out" / "findings.md")):
            with self.subTest(spelled=spelled):
                last, _ = self.need(f"NEED safety design.md {spelled}", (2, "ERROR"))
                self.assertIn("BAD_NEED", last)
        self.assertEqual(self.calls(), [])

    def test_findings_path_through_a_symlink_to_the_docs_worktree_is_accepted(self):
        link = self.tmp / "docs-link"
        link.symlink_to(self.docs_wt)
        self.behave("write-output")
        last, _ = self.need(f"NEED safety design.md {link / FINDINGS}")
        self.assertEqual(self.st("DESIGN_NEED"), f"safety\tdesign.md\t{FINDINGS}")
        self.assertEqual(last, f"DIRECTOR_JOB safety-done {self.run_dir / 'safety-1.md'}")

    def test_only_the_safety_review_may_write_in_the_docs_worktree(self):
        run_dir = str(self.run_dir.resolve())
        self.behave("write-output")
        self.need(f"NEED safety design.md {FINDINGS}")
        self.assertEqual(self.write_dirs(self.calls()[0]), [run_dir, str((self.docs_wt / ".mission").resolve())])
        (self.docs_wt / FINDINGS).unlink()  # else the next worker waits for commit-docs
        for line in ("NEED preflight-check design.md antigravity:small@high", f"NEED lens {self.tmp / 'concepts.md'}",
                     f"NEED plan-review {self.tmp / 'concepts.md'} {self.tmp / 'context.md'}"):
            with self.subTest(line=line):
                self.behave("write-output")
                self.need(line)
                self.assertEqual(self.write_dirs(self.calls()[-1]), [run_dir])

    def test_a_consult_answer_keeps_the_write_rule(self):
        for line, extra in ((f"NEED safety design.md {FINDINGS}", [str((self.docs_wt / ".mission").resolve())]),
                            ("NEED preflight-check design.md antigravity:small@high", [])):
            with self.subTest(line=line):
                self.behave("ask", "write-output", "write-output")
                last, _ = self.need(line, (22, "CONSULT"))
                ans = self.tmp / "ans.md"
                ans.write_text("Go on.\n")
                self.expect(20, "DIRECTOR_JOB", "--answer", str(ans))
                resumed = [c for c in self.calls() if c["conversation"]][-1]  # the worker that got the answer
                self.assertEqual(self.write_dirs(resumed), [str(self.run_dir.resolve()), *extra])
                if (self.docs_wt / FINDINGS).exists():
                    (self.docs_wt / FINDINGS).unlink()

    def test_safety_retry_does_not_count_a_second_run(self):
        self.behave("fail", "write-output")
        self.safety_need()
        self.assertEqual(self.st("SAFETY_RUNS"), "1")

    def test_failed_safety_with_partial_output_is_cleaned_before_retry(self):
        partial = self.docs_wt / FINDINGS
        self.behave({"do": "response", "status": "ERROR", "rc": 1, "edit": str(partial), "edit_text": "partial\n"},
                    "write-output")
        last, _ = self.safety_need()
        self.assertEqual(last, f"DIRECTOR_JOB safety-done {self.run_dir / 'safety-1.md'}")
        self.assertEqual(len(self.calls()), 2)

    def test_resume_interrupted_dispatching_cleans_docs_worktree_and_restarts(self):
        self.state.write_text(json.dumps({
            **self.st(),
            "DESIGN_PHASE": "dispatching",
            "DESIGN_NEED": f"preflight-check design.md antigravity:small@high",
            "DOCS_HEAD": self.docs_head,
        }))
        (self.docs_wt / "stray_design.txt").write_text("partial\n")
        self.behave("write-output")
        last, _ = self.expect(20, "DIRECTOR_JOB")
        self.assertFalse((self.docs_wt / "stray_design.txt").exists())
        self.assertIn("preflight-check-done", last)

    def test_consult_silent_new_conversation_cleans_and_falls_back(self):
        stray = self.docs_wt / "stray_unresumed.txt"
        self.behave(
            "ask",
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "write-output",
        )
        self.safety_need(expect=(22, "CONSULT"))
        ans = self.tmp / "ans.md"
        ans.write_text("Clarified.\n")
        last, _ = self.expect(20, "DIRECTOR_JOB", "--answer", str(ans))
        self.assertEqual(last, f"DIRECTOR_JOB safety-done {self.run_dir / 'safety-1.md'}")
        self.assertFalse(stray.exists())
        self.assertEqual(self.calls()[2]["conversation"], "")
        self.assertIn("Clarified.", self.calls()[2]["prompt"])

    def test_safety_falls_back_to_bundled_skills_when_missing_from_catalog(self):
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"].pop("flight-readiness", None)
        cat_path = self.tmp / "no-safety-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))
        self.behave("write-output")
        last, _ = self.safety_need()
        self.assertEqual(last, f"DIRECTOR_JOB safety-done {self.run_dir / 'safety-1.md'}")
        self.assertEqual(self.st("SAFETY_SKILLS"), str(SKILL / "skills"))
        self.assertIn(str(SKILL / "skills" / "flight-readiness" / "SKILL.md"), self.calls()[0]["prompt"])

    def test_safety_falls_back_to_flat_harness_sibling_skills_when_missing_from_catalog(self):
        flat_root = self.tmp / "flat-skills"
        pkg = flat_root / "mission-control"
        shutil.copytree(SKILL / "scripts", pkg / "scripts")
        shutil.copytree(SKILL / "references", pkg / "references")
        (flat_root / "flight-readiness").mkdir(parents=True)
        (flat_root / "flight-readiness" / "SKILL.md").write_text("---\nname: flight-readiness\n---\n")

        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"].pop("flight-readiness", None)
        cat_path = self.tmp / "no-safety-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))

        self.SCRIPT = pkg / "scripts" / "run_design.py"
        self.behave("write-output")
        last, _ = self.safety_need()
        self.assertEqual(last, f"DIRECTOR_JOB safety-done {self.run_dir / 'safety-1.md'}")
        self.assertEqual(self.st("SAFETY_SKILLS"), str(flat_root))
        self.assertIn(str(flat_root / "flight-readiness" / "SKILL.md"), self.calls()[0]["prompt"])


class Lens(Design):
    def lens_need(self, expect=(20, "DIRECTOR_JOB")):
        return self.need(f"NEED lens {self.tmp / 'concepts.md'}", expect)

    def test_lens_second_run_reuses_first_findings(self):
        self.behave("write-output")
        last, _ = self.lens_need()
        first = self.run_dir / "lens-1.md"
        self.assertEqual(last, f"DIRECTOR_JOB lens-done {first}")
        self.assertIn("wind-tunnel", self.calls()[0]["prompt"])
        last, _ = self.lens_need()
        self.assertEqual(last, f"DIRECTOR_JOB lens-done {first}")
        self.assertEqual(len(self.calls()), 1)

    def test_lens_resolves_wind_tunnel_independently_of_flight_readiness_override(self):
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"]["flight-readiness"] = "/override/skills/flight-readiness"
        cat["harnesses"]["antigravity"]["skills"].pop("wind-tunnel", None)
        cat_path = self.tmp / "override-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))
        self.behave("write-output", "write-output")
        self.need(f"NEED safety design.md {FINDINGS}")
        self.assertEqual(self.st("SAFETY_SKILLS"), "/override/skills")
        sh("git", "-C", str(self.docs_wt), "add", "-f", FINDINGS)
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "findings")
        last, _ = self.lens_need()
        self.assertEqual(last, f"DIRECTOR_JOB lens-done {self.run_dir / 'lens-1.md'}")
        self.assertIn(str(SKILL / "skills" / "wind-tunnel" / "SKILL.md"), self.calls()[1]["prompt"])


class PlanReview(Design):
    def review_need(self, expect=(20, "DIRECTOR_JOB")):
        return self.need(f"NEED plan-review {self.tmp / 'plan.md'} {self.tmp / 'context.md'}", expect)

    def test_plan_review_paths_with_spaces(self):
        plan, context = self.tmp / "my plan.md", self.tmp / "my context.md"
        plan.write_text("the plan\n")
        context.write_text("the context\n")
        self.behave("write-output")
        last, _ = self.need(f'NEED plan-review "{plan}" "{context}"')
        self.assertEqual(last, f"DIRECTOR_JOB plan-review-done {self.run_dir / 'plan-review-1.md'}")
        prompt = self.calls()[0]["prompt"]
        self.assertIn(str(plan), prompt)
        self.assertIn("the context", prompt)

    def test_plan_review_limit_input(self):
        (self.tmp / "plan.md").write_text("plan\n")
        self.behave("write-output", "write-output")
        last, _ = self.review_need()
        self.assertEqual(last, f"DIRECTOR_JOB plan-review-done {self.run_dir / 'plan-review-1.md'}")
        self.assertIn("context", self.calls()[0]["prompt"])
        self.review_need()
        last, _ = self.review_need()
        self.assertEqual(last, "DIRECTOR_JOB plan-review-done Plan review limit reached; "
                               f"finish triage with {self.run_dir / 'plan-review-2.md'}")
        self.assertEqual(len(self.calls()), 2)

    def test_plan_review_resolves_cross_check_from_its_own_dir(self):
        (self.tmp / "plan.md").write_text("plan\n")
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["skills"]["flight-readiness"] = "/override/skills/flight-readiness"
        cat["harnesses"]["antigravity"]["skills"]["cross-check"] = "/other/skills/cross-check"
        cat_path = self.tmp / "split-cat.json"
        cat_path.write_text(json.dumps(cat))
        self.state.write_text(json.dumps({**self.st(), "CATALOG": str(cat_path)}))
        self.behave("write-output", "write-output")
        self.need(f"NEED safety design.md {FINDINGS}")
        sh("git", "-C", str(self.docs_wt), "add", "-f", FINDINGS)
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "findings")
        last, _ = self.review_need()
        self.assertEqual(last, f"DIRECTOR_JOB plan-review-done {self.run_dir / 'plan-review-1.md'}")
        self.assertIn("/other/skills/cross-check/SKILL.md", self.calls()[1]["prompt"])


if __name__ == "__main__":
    unittest.main()
