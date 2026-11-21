"""Tests for Claude Code (`claude` CLI) runner dispatch and mixed cross-harness runs."""
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

from helpers import Base, SKILL, sh

sys.path.insert(0, str(SKILL / "scripts"))
from common import AgyRunner, Run  # noqa: E402

FAKE_CLAUDE = SKILL / "tests" / "fake_claude.py"
CATALOG_MULTI = SKILL / "tests" / "fixtures" / "catalog_multi.json"
RUN_ARCH = SKILL / "scripts" / "run_architect.py"
RUN_DESIGN = SKILL / "scripts" / "run_design.py"
RUN_TASK = SKILL / "scripts" / "run_task.py"
PROBE = SKILL / "scripts" / "probe.py"


class MultiHarnessBase(Base):
    def setUp(self):
        super().setUp()
        self.fake_claude = self.tmp / "fake-claude"
        self.fake_claude.write_text(f'#!/bin/sh\nexec {sys.executable} -I {FAKE_CLAUDE} "$@"\n')
        self.fake_claude.chmod(0o755)
        self.claude_world = self.tmp / "claude_world.json"
        self.claude_world.write_text(json.dumps({"seq": 0, "behaviors": [], "calls": []}))
        s = self.st()
        s["CLAUDE"] = str(self.fake_claude)
        s["CATALOG"] = str(CATALOG_MULTI)
        self.state.write_text(json.dumps(s))

    def claude_behave(self, *items):
        w = json.loads(self.claude_world.read_text())
        w["behaviors"] += [{"do": i} if isinstance(i, str) else i for i in items]
        self.claude_world.write_text(json.dumps(w))

    def claude_calls(self):
        return json.loads(self.claude_world.read_text())["calls"]

    def _env(self):
        return {
            **os.environ,
            "FAKE_AGY_WORLD": str(self.world),
            "FAKE_CLAUDE_WORLD": str(self.claude_world),
        }

    def adv(self, *extra):
        r = subprocess.run(
            [sys.executable, "-I", str(self.SCRIPT), "--state", str(self.state), "advance", *extra],
            capture_output=True, text=True, env=self._env(),
        )
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines, f"no output; stderr={r.stderr}")
        return r.returncode, lines[-1], lines

    def arch(self, *args):
        r = subprocess.run(
            [sys.executable, "-I", str(RUN_ARCH), *args],
            capture_output=True, text=True, env=self._env(),
        )
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines, f"no output; stderr={r.stderr}")
        return r.returncode, lines[-1]


class ClaudeRunnerUnitTests(MultiHarnessBase):
    def setUp(self):
        super().setUp()
        p = unittest.mock.patch.dict(os.environ, {
            "FAKE_AGY_WORLD": str(self.world),
            "FAKE_CLAUDE_WORLD": str(self.claude_world),
        })
        p.start()
        self.addCleanup(p.stop)

    def _runner(self):
        return AgyRunner(Run(self.state))

    def test_claude_run_and_resume_flags(self):
        self.claude_behave(
            {"do": "response", "text": "hello\n", "session_id": "sess_abc"},
            {"do": "response", "text": "resumed\n", "session_id": "sess_abc"},
            {"do": "ok"},
        )
        runner = self._runner()
        res = runner.run("Say hi", pair="claude:opus@high", worktree=self.impl)
        self.assertEqual(res, ("done", "succeeded", "hello\n", "sess_abc"))

        res2 = runner.try_resume("Follow up", "claude:opus@high", self.impl, "sess_abc")
        self.assertEqual(res2, ("done", "succeeded", "resumed\n", "sess_abc"))

        res3 = runner.run("Haiku", pair="claude:haiku@default", worktree=self.impl)
        self.assertEqual(res3[0], "done")

        calls = self.claude_calls()
        self.assertEqual(len(calls), 3)
        self.assertIn("--output-format", calls[0]["argv"])
        self.assertIn("json", calls[0]["argv"])
        self.assertIn("--dangerously-skip-permissions", calls[0]["argv"])
        self.assertEqual((calls[0]["model"], calls[0]["effort"], calls[0]["resume"]), ("opus", "high", ""))
        self.assertIn("--resume", calls[1]["argv"])
        self.assertNotIn("--conversation", calls[1]["argv"])
        self.assertEqual(calls[1]["resume"], "sess_abc")
        self.assertNotIn("--effort", calls[2]["argv"])
        self.assertEqual(calls[2]["model"], "haiku")

    def test_envelope_parsing_variants(self):
        self.claude_behave(
            {"do": "response", "text": "banner ok\n", "stdout_prefix": "Loading plugins...\nReady.", "pretty": True, "session_id": "s1"},
            {"do": "response", "is_error": True, "errors": ["err one", "err two"], "session_id": "s2", "rc": 1},
            {"do": "response", "is_error": False, "subtype": "error_max_turns", "text": "partial", "session_id": "s3", "rc": 0},
            {"do": "response", "raw_stdout": "fatal: unexpected panic", "raw_stderr": "traceback", "rc": 2},
        )
        runner = self._runner()
        r1 = runner.run("p1", pair="claude:sonnet@high", worktree=self.impl)
        self.assertEqual(r1, ("done", "succeeded", "banner ok\n", "s1"))

        r2 = runner.run("p2", pair="claude:sonnet@high", worktree=self.impl)
        self.assertEqual(r2, ("stopped", "err one; err two", "", "s2"))

        r3 = runner.run("p3", pair="claude:sonnet@high", worktree=self.impl)
        self.assertEqual(r3[0], "stopped")
        self.assertEqual(r3[3], "s3")

        r4 = runner.run("p4", pair="claude:sonnet@high", worktree=self.impl)
        self.assertEqual(r4[0], "stopped")
        self.assertIn("fatal: unexpected panic", r4[1])

    def test_silent_new_session_and_expired_session_detection(self):
        self.claude_behave(
            {"do": "response", "text": "started fresh instead\n", "session_id": "sess_different"},
        )
        runner = self._runner()
        self.assertIsNone(runner.try_resume("hi", "claude:opus@high", self.impl, "sess_orig"))

        w = json.loads(self.claude_world.read_text())
        w["expired_sessions"] = ["sess_dead"]
        self.claude_world.write_text(json.dumps(w))
        self.assertIsNone(runner.try_resume("hi", "claude:opus@high", self.impl, "sess_dead"))

    def test_quota_classification_for_claude_errors(self):
        self.claude_behave(
            "usage",
            {"do": "response", "is_error": True, "errors": ["API Error: 429 overloaded_error"], "rc": 1},
        )
        runner = self._runner()
        self.assertEqual(runner.run("p1", pair="claude:opus@high", worktree=self.impl)[0], "usage")
        self.assertEqual(runner.run("p2", pair="claude:opus@high", worktree=self.impl)[0], "usage")


class ClaudeArchitectAndTaskFallbackTests(MultiHarnessBase):
    def test_architect_lost_13_and_usage_limit_31(self):
        spec = self.tmp / "arch.txt"
        spec.write_text("Start job.\n")
        self.claude_behave(
            {"do": "response", "text": "RESULT: COMMITTED\n", "session_id": "arch_c1"},
            {"do": "response", "text": "RESULT: COMMITTED\n", "session_id": "arch_unexpected"},
            "usage",
        )
        rc, last = self.arch("start", "--state", str(self.state), "--spec", str(spec),
                             "--pair", "claude:opus@high", "--keep-open")
        self.assertEqual(rc, 0, last)
        self.assertEqual(self.st("ARCHITECT_CONVERSATION_ID"), "arch_c1")

        rc, last = self.arch("send", "--state", str(self.state), "--spec", str(spec),
                             "--pair", "claude:opus@high")
        self.assertEqual(rc, 13, last)
        self.assertTrue(last.startswith("LOST "), last)
        self.assertIsNone(self.st("ARCHITECT_CONVERSATION_ID"))

        rc, last = self.arch("start", "--state", str(self.state), "--spec", str(spec),
                             "--pair", "claude:opus@high")
        self.assertEqual(rc, 31, last)
        self.assertTrue(last.startswith("USAGE_LIMIT "), last)

    def test_run_task_usage_prompt_and_resume_fallback(self):
        self.task("01", "AC-1", extra="")
        meta = self.docs / "tasks" / "01.meta"
        meta.write_text(
            "TITLE=Task 01\nTIER=standard\nSPEC_TYPE=checklist\nACS=AC-1\n"
            "IMPLEMENTER=claude:sonnet@high\nREVIEW_1=claude:sonnet@high\nREVIEW_2=claude:sonnet@high\n"
        )
        # 1. Implementer commits (sess_expired), stage 1 passes, stage 2 asks for fix.
        # 2. Fix round: resume gets expired-session error -> resets worktree & falls back to fresh claude run & commits.
        # 3. Stage 2 review passes -> merged.
        w = json.loads(self.claude_world.read_text())
        w["expired_sessions"] = ["sess_expired"]
        self.claude_world.write_text(json.dumps(w))
        self.claude_behave(
            {"do": "commit", "session_id": "sess_expired"},
            "pass",
            "fix",
            {"do": "commit", "session_id": "sess_fresh"},
            "pass",
        )
        last, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(any(l.startswith("MERGED 01 ") for l in lines), lines)
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(self.claude_calls()), 6)

    def test_run_task_usage_limit_exits_30_prompt(self):
        meta = self.docs / "tasks" / "01.meta"
        meta.write_text(
            "TITLE=Task 01\nTIER=standard\nSPEC_TYPE=checklist\nACS=AC-1\n"
            "IMPLEMENTER=claude:sonnet@high\nREVIEW_1=claude:sonnet@high\nREVIEW_2=claude:sonnet@high\n"
        )
        self.claude_behave("usage")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("retry stop", last)


class MixedCrossHarnessTests(MultiHarnessBase):
    def test_probe_step1_dispatches_to_both_harnesses(self):
        self.SCRIPT = PROBE
        s = self.st()
        s["ROLE_ARCHITECT"] = "claude:opus@high"
        s["ROLE_EGM"] = "antigravity:g@high"
        s["ROLE_BASE"] = "antigravity:g@high"
        self.state.write_text(json.dumps(s))

        self.claude_behave("ok")
        self.behave("ok", "ok")
        self.expect(0, "PROBED", "--set", "step1")
        self.assertEqual(self.st("LAUNCH_STYLE_claude_opus_high"), "flag")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_g_high"), "slug")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_g_xhigh"), "slug")
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.claude_calls()[0]["model"], "opus")
        self.assertEqual(self.claude_calls()[0]["effort"], "high")
        self.assertEqual(self.calls()[0]["model"], "g-high")

    def test_run_task_mixed_agy_implementer_and_claude_reviewers(self):
        meta = self.docs / "tasks" / "01.meta"
        meta.write_text(
            "TITLE=Task 01\nTIER=standard\nSPEC_TYPE=checklist\nACS=AC-1\n"
            "IMPLEMENTER=antigravity:g@high\nREVIEW_1=claude:sonnet@high\nREVIEW_2=claude:opus@high\n"
        )
        self.behave("commit")
        self.claude_behave("pass", "pass")
        last, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(any(l.startswith("MERGED 01 ") for l in lines), lines)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(self.claude_calls()), 2)
        self.assertEqual(self.claude_calls()[0]["model"], "sonnet")
        self.assertEqual(self.claude_calls()[1]["model"], "opus")

    def test_run_design_with_claude_egm_resolves_claude_skills_and_dispatches(self):
        self.SCRIPT = RUN_DESIGN
        (self.docs_wt / "design.md").write_text("design\n")
        sh("git", "-C", str(self.docs_wt), "add", "design.md")
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "design")
        s = self.st()
        s.update({"TOPIC": "demo", "ROLE_EGM": "claude:sonnet@high"})
        self.state.write_text(json.dumps(s))

        self.claude_behave("write-output")
        last, _ = self.expect(20, "ARCHITECT_JOB", "--result", self.result_file("RESULT: NEED egm design.md .run/egm-findings.md"))
        self.assertTrue(last.startswith("ARCHITECT_JOB egm-done "), last)
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertIn("/claude-skills/", self.claude_calls()[0]["prompt"])
        self.assertEqual(self.claude_calls()[0]["model"], "sonnet")


if __name__ == "__main__":
    unittest.main()
