"""Tests for scripts/run_task.py against fake_agy.py. Run: bash tests/run_all.sh"""
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
        _, lines = self.expect(0, "QUEUE_EMPTY")
        merged = [l for l in lines if l.startswith("MERGED ")]
        self.assertEqual(len(merged), 2, lines)
        self.assertTrue(merged[0].startswith("MERGED 01 "))
        self.assertTrue(merged[1].startswith("MERGED 02 "))
        self.assertEqual(self.feature(), self.impl_head())
        self.assertEqual(sh("git", "-C", str(self.main), "branch", "--show-current"), "main")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), [])
        calls = self.calls()
        impl = [c for c in calls if c["model"] == "g-high"]
        self.assertEqual(len(impl), 2)
        reviews = [c for c in calls if c["model"] == "small-high"]
        self.assertEqual(len(reviews), 4)
        self.assertIsNone(self.st("WAITING_ON"))
        self.assertIsNone(self.st("IMPL_CONVERSATION_ID_01"))
        self.assertIsNone(self.st("IMPL_CONVERSATION_ID_02"))


class GateAndFixes(Base):
    def test_stage2_counter_survives_gate_pass(self):
        # impl, s1 PASS, s2 FIX, fix fails gate, fix passes gate, s2 FIX, fix, s2 FIX -> escalate
        self.behave("commit", "pass", "fix", "commit-broken", "unbreak", "fix", "commit", "fix")
        self.expect(20, "ESCALATE")
        self.assertEqual(self.st("STAGE_FIX_ROUNDS_01_S2"), "2")
        self.assertEqual(len(self.calls()), 8)

    def test_total_fix_cap_escalates(self):
        # s1 FIX(t1), gate fail(t2), gate pass, s1 PASS, s2 FIX(t3), gate fail(t4), gate pass, s2 FIX -> cap
        self.behave("commit", "fix", "commit-broken", "unbreak", "pass", "fix", "commit-broken", "unbreak", "fix")
        self.expect(20, "ESCALATE")
        self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "4")
        self.assertEqual(self.st("STAGE_FIX_ROUNDS_01_S2"), "1")

    def test_gate_failure_reuses_same_conversation_then_merges(self):
        self.behave("commit-broken", "unbreak", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        calls = self.calls()
        self.assertEqual(calls[0]["conversation"], "")
        self.assertEqual(calls[1]["conversation"], "conv_1")  # fix round resumed same conversation
        self.assertTrue(list((self.run_dir / "gates").glob("01-*.log")))
        self.assertEqual(self.st("GATE_FIX_ROUNDS_01"), "0")
        self.assertEqual(self.st("LOOP_01"), "merged")

    def test_fix_falls_back_to_fresh_worker_when_conversation_expired(self):
        self.behave({"do": "commit", "conv_id": "conv_impl_1"}, "fix", "commit", "pass", "pass")
        w = json.loads(self.world.read_text())
        w["expired_conversations"] = ["conv_impl_1"]
        self.world.write_text(json.dumps(w))
        self.expect(0, "QUEUE_EMPTY")
        calls = self.calls()
        # Call 0: impl (conv_impl_1), Call 1: s1 fix, Call 2: fix resume attempt (fails), Call 3: fresh fix, Call 4: s1 pass, Call 5: s2 pass
        self.assertEqual(calls[2]["conversation"], "conv_impl_1")
        self.assertEqual(calls[3]["conversation"], "")

    def test_fix_silent_new_conversation_cleans_stray_edits_before_fallback(self):
        stray = self.impl / "stray_fix.txt"
        self.behave(
            {"do": "commit", "conv_id": "conv_impl_1"},
            "fix",
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "commit",
            "pass",
            "pass",
        )
        self.expect(0, "QUEUE_EMPTY")
        self.assertFalse(stray.exists())
        calls = self.calls()
        self.assertEqual(calls[2]["conversation"], "conv_impl_1")
        self.assertEqual(calls[3]["conversation"], "")
        changed = sh("git", "-C", str(self.impl), "show", "--stat", "--format=", self.st("LAST_MERGED"))
        self.assertNotIn("stray_fix.txt", changed)

    def test_two_fix_rounds_then_escalate_then_next_rung(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("task 01, tier standard, attempt 1, next rung antigravity:g@xhigh, replan allowed", last)
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn(f"task=01 base={self.base}", hist)
        self.assertEqual(hist.count("VERDICT: FIX"), 3)
        self.expect(20, "ESCALATE")
        note = self.tmp / "note.md"
        note.write_text("steps 1-2 done\n")
        self.behave("commit", "pass", "pass")
        _, lines = self.expect(0, "QUEUE_EMPTY", "--director-result",
                               self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:g@xhigh"))
        self.assertTrue(lines[0].startswith("MERGED 01 "))
        impl_calls = [c for c in self.calls() if c["model"] == "g-xhigh"]
        self.assertEqual(len(impl_calls), 1)
        spec = Path(self.st("IMPLEMENT_SPEC_01")).read_text()
        self.assertIn("steps 1-2 done", spec)
        self.assertEqual(self.st("LOOP_01"), "merged")

    def test_result_line_with_trailing_prose_is_accepted(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.expect(20, "ESCALATE")
        note = self.tmp / "note.md"
        note.write_text("steps 1-2 done\n")
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--director-result", self.result_file(
            f"`RESULT: ESCALATE {self.base} {note} antigravity:g@xhigh.` The next rung should manage; see the note.\nMore."))
        self.assertEqual(self.st("PAIR_01"), "antigravity:g@xhigh")

    def test_verdict_falls_back_to_the_settle_message(self):
        self.behave("commit", "pass-body-only", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertIn("VERDICT: PASS", (self.run_dir / "verdicts" / "01-s1-1.md").read_text())

    def test_prose_settle_message_uses_the_review_file(self):
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("NO_VERDICT_01_S1"), "0")
        self.assertTrue(list((self.run_dir / "results").glob("01-s1-settle-*.md")))

    def test_no_verdict_twice_counts_as_fix(self):
        self.behave("commit", "noverdict", "noverdict", "commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "1")

    def test_stage2_fix_rechecks_then_returns_to_stage2(self):
        self.behave("commit", "pass", "fix", "commit", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(list((self.run_dir / "gates").glob("01-2.log")))
        self.assertEqual(len([c for c in self.calls() if c["model"] == "small-high"]), 3)


class TreeAndWorkers(Base):
    def test_reviewer_that_edits_is_void_and_replaced(self):
        self.behave("commit", "edit-and-pass", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(list((self.run_dir / "verdicts").glob("01-void-s1-*.md")))
        self.assertEqual(sh("git", "-C", str(self.impl), "status", "--porcelain"), "")
        self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "0")

    def test_stopped_reviewer_is_replaced(self):
        self.behave("commit", "stopped", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(len(self.calls()), 4)

    def test_uncommitted_work_gets_a_commit_worker_reusing_conversation(self):
        self.behave("dirty", "commit-all", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.calls()[1]["conversation"], "conv_1")
        self.assertIn("left uncommitted changes", self.calls()[1]["prompt"])

    def test_leftovers_after_one_commit_round_are_removed_not_looped(self):
        self.behave("commit-and-junk", "nocommit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        commit_calls = [c for c in self.calls() if "left uncommitted changes" in c["prompt"]]
        self.assertEqual(len(commit_calls), 1)
        log = (self.run_dir / "mission-log.md").read_text()
        self.assertIn("removed what the commit worker left uncommitted", log)
        self.assertIn("__pycache__/work.cpython-313.pyc", log)

    def test_a_new_round_gets_its_own_commit_round(self):
        self.behave("dirty", "commit-all", "fix", "commit-and-junk", "nocommit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        commit_calls = [c for c in self.calls() if "left uncommitted changes" in c["prompt"]]
        self.assertEqual(len(commit_calls), 2)

    def test_no_commits_escalates(self):
        self.behave("nocommit")
        self.expect(20, "ESCALATE")
        self.assertIn("settled succeeded with no commits",
                      (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_failed_implementer_message_goes_into_history(self):
        self.behave("fail")
        self.expect(20, "ESCALATE")
        self.assertIn("AssertionError: 1 != 2", (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_consult_on_impl_ask_resumes_and_completes(self):
        self.behave("ask", "commit", "pass", "pass")
        last, _ = self.expect(22, "CONSULT")
        ask_file = Path(last.split()[1])
        self.assertIn("Which edge case", ask_file.read_text())
        ans = self.tmp / "ans.md"
        ans.write_text("Return 42.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        self.assertEqual(self.calls()[1]["conversation"], "conv_1")
        self.assertIn("Return 42.", self.calls()[1]["prompt"])

    def test_consult_on_reviewer_ask_resumes_and_completes(self):
        self.behave("commit", "ask", "pass", "pass")
        last, _ = self.expect(22, "CONSULT")
        ans = self.tmp / "ans.md"
        ans.write_text("That behavior is intended.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        self.assertEqual(self.calls()[2]["conversation"], "conv_2")

    def test_consult_expired_conversation_falls_back_cleanly(self):
        self.behave("ask", "commit", "pass", "pass")
        self.expect(22, "CONSULT")
        w = json.loads(self.world.read_text())
        w["expired_conversations"] = ["conv_1"]
        self.world.write_text(json.dumps(w))
        ans = self.tmp / "ans.md"
        ans.write_text("Return 42.\n" + "z" * 400 + "\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        self.assertEqual(self.calls()[2]["conversation"], "")
        self.assertIn("Return 42.", self.calls()[2]["prompt"])

    def test_large_prompt_spills_to_pointer_file(self):
        (self.docs / "tasks" / "01.md").write_text("Task 01\n" + ("long detail line\n" * 5000))
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(self.calls()[0]["prompt"].startswith("Read and follow all instructions in "))

    def test_leading_stdout_prefix_and_formatted_markers(self):
        self.behave(
            {"do": "response", "stdout_prefix": "Fetching workspace...\nWarning: cache miss",
             "text": "`ASK: Formatted question?`", "conv_id": "conv_fmt"},
            "commit",
            "fix-formatted",
            "commit",
            "pass",
            "pass",
        )
        self.expect(22, "CONSULT")
        ans = self.tmp / "ans.md"
        ans.write_text("Proceed.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        # fix-formatted review file preserved its Critical finding instead of being overwritten by settle summary
        verdict_file = self.run_dir / "verdicts" / "01-s1-1.md"
        self.assertIn("Critical: bug in foo.py:1.", verdict_file.read_text())

    def test_formatted_failed_marker_escalates(self):
        self.behave("fail-formatted")
        self.expect(20, "ESCALATE")
        self.assertIn("**FAILED:** missing tool xyz", (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_colon_outside_bold_markers_and_pretty_envelope(self):
        self.behave(
            {"do": "response", "stdout_prefix": "Banner line", "pretty": True,
             "text": "**ASK**: Colon outside bold?", "conv_id": "conv_pretty"},
            "commit",
            "pass",
            "pass",
        )
        self.expect(22, "CONSULT")
        ans = self.tmp / "ans.md"
        ans.write_text("Yes.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))

        self.behave({"do": "response", "text": "**FAILED**: missing colon-outside tool\n", "pretty": True})
        (self.run_dir / "queue.txt").write_text("02\n")
        self.expect(20, "ESCALATE")
        self.assertIn("**FAILED**: missing colon-outside tool",
                      (self.run_dir / "verdicts" / "02-history.md").read_text())

    def test_non_json_error_with_trailing_json_preserves_usage_detection(self):
        self.behave(
            {"do": "response", "raw_stdout": "Fatal: usageLimitReached\n{\"code\": 429}", "rc": 1},
            "commit", "pass", "pass",
        )
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("usageLimitReached", Path(last.split()[1]).read_text())
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")

    def test_non_json_error_with_trailing_status_or_error_json_not_misparsed(self):
        self.behave(
            {"do": "response", "raw_stdout": "Fatal: usageLimitReached\n{\"status\": 500}", "rc": 1},
            "commit", "pass", "pass",
        )
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("usageLimitReached", Path(last.split()[1]).read_text())
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")

        self.behave(
            {"do": "response", "raw_stdout": "Fatal: usageLimitReached\n{\"error\": \"bad request\"}", "rc": 1},
            "commit", "pass", "pass",
        )
        (self.run_dir / "queue.txt").write_text("02\n")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("usageLimitReached", Path(last.split()[1]).read_text())
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")

    def test_structured_error_dict_does_not_crash(self):
        self.behave({"do": "response", "status": "ERROR", "rc": 1, "error": {"code": 500, "msg": "boom"}})
        self.expect(20, "ESCALATE")
        self.assertIn("boom", (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_agy_timeout_triggers_stopped_and_escalates(self):
        import time as _time
        s = self.st()
        s["AGY_TIMEOUT"] = "1"
        self.state.write_text(json.dumps(s))
        self.behave({"do": "response", "spawn_child_sleep": 10, "sleep": 10, "text": "late\n"})
        t0 = _time.monotonic()
        self.expect(20, "ESCALATE")
        self.assertLess(_time.monotonic() - t0, 4.0)
        self.assertIn("timed out after 1s", (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_take_answer_transitions_out_of_consult_before_resuming(self):
        self.behave("ask", "commit", "pass", "pass")
        self.expect(22, "CONSULT")
        snap = self.tmp / "mid-state.json"
        orig_agy = self.st("AGY")
        wrapper = self.tmp / "check-agy"
        wrapper.write_text(f"#!/bin/sh\n[ -e {snap} ] || cp {self.state} {snap}\nexec {orig_agy} \"$@\"\n")
        wrapper.chmod(0o755)
        self.state.write_text(json.dumps({**self.st(), "AGY": str(wrapper)}))
        ans = self.tmp / "ans.md"
        ans.write_text("Return 42.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        mid = json.loads(snap.read_text())
        self.assertEqual(mid["LOOP_01"], "impl")
        self.assertNotIn("TASK_ASK_FILE_01", mid)

    def test_reviewer_consult_silent_new_conversation_cleans_and_falls_back(self):
        stray = self.impl / "stray_reviewer.txt"
        self.behave(
            "commit",
            "ask",
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "pass",
            "pass",
        )
        self.expect(22, "CONSULT")
        ans = self.tmp / "ans.md"
        ans.write_text("Expected behavior.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        self.assertFalse(stray.exists())
        self.assertEqual(self.calls()[3]["conversation"], "")
        self.assertIn("Expected behavior.", self.calls()[3]["prompt"])

    def test_impl_consult_silent_new_conversation_cleans_and_falls_back(self):
        stray = self.impl / "stray_impl.txt"
        self.behave(
            {"do": "ask", "conv_id": "conv_impl_1"},
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "commit",
            "pass",
            "pass",
        )
        self.expect(22, "CONSULT")
        ans = self.tmp / "ans.md"
        ans.write_text("Return 42.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        self.assertFalse(stray.exists())
        calls = self.calls()
        self.assertEqual(calls[1]["conversation"], "conv_impl_1")
        self.assertEqual(calls[2]["conversation"], "")
        self.assertIn("Return 42.", calls[2]["prompt"])
        changed = sh("git", "-C", str(self.impl), "show", "--stat", "--format=", self.st("LAST_MERGED"))
        self.assertNotIn("stray_impl.txt", changed)

    def test_fix_consult_silent_new_conversation_cleans_and_falls_back(self):
        stray = self.impl / "stray_fix_ask.txt"
        self.behave(
            {"do": "commit", "conv_id": "conv_impl_1"},
            "fix",
            {"do": "ask", "conv_id": "conv_impl_1"},
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "commit",
            "pass",
            "pass",
        )
        self.expect(22, "CONSULT")
        ans = self.tmp / "ans.md"
        ans.write_text("Fix module X.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))
        self.assertFalse(stray.exists())
        calls = self.calls()
        self.assertEqual(calls[3]["conversation"], "conv_impl_1")
        self.assertEqual(calls[4]["conversation"], "")
        self.assertIn("Fix module X.", calls[4]["prompt"])
        changed = sh("git", "-C", str(self.impl), "diff", "--stat", f"{self.base}..{self.st('LAST_MERGED')}")
        self.assertIn("work_1.txt", changed)
        self.assertIn("work_5.txt", changed)
        self.assertNotIn("stray_fix_ask.txt", changed)

    def test_commit_resume_failure_clears_stale_conversation_id_before_fallback(self):
        self.behave(
            {"do": "dirty", "conv_id": "conv_impl_1"},
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n"},
            {"do": "response", "raw_stderr": "usageLimitReached: 5-hour limit", "rc": 1},
            "commit-all",
            "pass",
            "pass",
        )
        self.expect(30, "PROMPT")
        self.assertIsNone(self.st().get("IMPL_CONVERSATION_ID_01"))
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")
        self.assertEqual(self.calls()[3]["conversation"], "")

    def test_escalation_impl_consult_fallback_resets_to_reset_sha_not_base(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.expect(20, "ESCALATE")
        step_b = sh("git", "-C", str(self.impl), "rev-parse", "HEAD~2")
        self.assertNotEqual(step_b, self.base)

        note = self.tmp / "note.md"
        note.write_text("keep step B\n")
        stray = self.impl / "stray_esc_impl.txt"
        self.behave(
            {"do": "ask", "conv_id": "conv_esc_1"},
            {"do": "response", "conv_id": "conv_fresh", "text": "contextless\n", "edit": str(stray)},
            "commit",
            "pass",
            "pass",
        )
        self.expect(22, "CONSULT", "--director-result",
                    self.result_file(f"RESULT: ESCALATE {step_b} {note} antigravity:g@xhigh"))
        self.assertEqual(self.st("BASE_SHA_01"), self.base)
        self.assertEqual(self.st("IMPL_START_SHA_01"), step_b)
        self.assertEqual(self.impl_head(), step_b)

        head_snap = self.tmp / "fallback-head.txt"
        orig_agy = self.st("AGY")
        wrapper = self.tmp / "capture-head-agy"
        wrapper.write_text(
            "#!/bin/sh\n"
            "case \" $* \" in *\" --conversation \"*) ;; *)\n"
            f"  [ -e {head_snap} ] || {{ git -C {self.impl} rev-parse HEAD; "
            f"git -C {self.impl} status --porcelain --untracked-files=all; }} > {head_snap}\n"
            ";; esac\n"
            f"exec {orig_agy} \"$@\"\n"
        )
        wrapper.chmod(0o755)
        self.state.write_text(json.dumps({**self.st(), "AGY": str(wrapper)}))

        ans = self.tmp / "ans.md"
        ans.write_text("Continue from step B.\n")
        self.expect(0, "QUEUE_EMPTY", "--answer", str(ans))

        self.assertEqual(head_snap.read_text().splitlines(), [step_b])
        self.assertFalse(stray.exists())
        calls = self.calls()
        self.assertEqual(calls[7]["conversation"], "conv_esc_1")
        self.assertEqual(calls[8]["conversation"], "")
        self.assertIn("Continue from step B.", calls[8]["prompt"])
        merged = self.st("LAST_MERGED")
        self.assertEqual(sh("git", "-C", str(self.impl), "rev-parse", f"{merged}~1"), step_b)
        changed = sh("git", "-C", str(self.impl), "diff", "--stat", f"{self.base}..{merged}")
        self.assertIn("work_1.txt", changed)
        self.assertNotIn("work_3.txt", changed)
        self.assertNotIn("stray_esc_impl.txt", changed)


class Prompts(Base):
    def test_usage_limit_prompts_then_retry_restarts(self):
        self.behave("usage")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("5-hour limit", Path(last.split()[1]).read_text())
        self.assertEqual(self.st("ATTEMPT_01"), "0")
        self.expect(30, "PROMPT", "--reply", "maybe")
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")

    def test_rejected_pair_twice_prompts_then_architect_stops(self):
        self.behave("fail")
        self.expect(20, "ESCALATE")
        bad = self.result_file(f"RESULT: ESCALATE {self.base} /dev/null antigravity:big@high")
        self.expect(20, "ESCALATE", "--director-result", bad)
        last, _ = self.expect(30, "PROMPT", "--director-result", bad)
        self.assertIn("rejected twice", Path(last.split()[1]).read_text())
        self.expect(20, "ESCALATE", "--reply", "retry")
        self.expect(40, "STOP", "--director-result", self.result_file("RESULT: STOP"))

    def test_reset_sha_before_base_is_rejected(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.expect(20, "ESCALATE")
        note = self.tmp / "note.md"
        note.write_text("n\n")
        self.expect(20, "ESCALATE", "--director-result",
                    self.result_file(f"RESULT: ESCALATE {self.older} {note} antigravity:g@xhigh"))
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn("not between the task's base", hist)
        self.assertEqual(self.impl_head(), self.st("WORKER_HEAD_01"))

    def test_replan_sha_before_base_is_rejected(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.expect(20, "ESCALATE")
        queue = (self.run_dir / "queue.txt").read_text()
        self.expect(20, "ESCALATE", "--director-result", self.result_file(f"RESULT: REPLAN {self.older} 01b"))
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn("not between the task's base", hist)
        self.assertEqual(self.impl_head(), self.st("WORKER_HEAD_01"))
        self.assertEqual((self.run_dir / "queue.txt").read_text(), queue)

    def test_escalate_sha_off_the_head_line_is_rejected(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.expect(20, "ESCALATE")
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
        self.expect(20, "ESCALATE", "--director-result",
                    self.result_file(f"RESULT: ESCALATE {side} {note} antigravity:g@xhigh"))
        self.assertIn("not between the task's base", (self.run_dir / "verdicts" / "01-history.md").read_text())
        self.assertEqual(self.impl_head(), head)

    def test_three_void_reviewers_prompt_retry_or_stop(self):
        self.behave("commit", "stopped", "stopped", "stopped")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith("retry stop"))
        text = Path(last.split()[1]).read_text()
        self.assertIn("Task 01 stage 1: three reviewers in a row were void", text)
        self.behave("pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")
        self.assertEqual(self.st("VOIDS_01_S1"), "0")

    def test_two_gate_failures_escalate(self):
        self.behave("commit-broken", "commit", "commit")
        self.expect(20, "ESCALATE")
        self.assertEqual(self.st("GATE_FIX_ROUNDS_01"), "2")

    def test_escalation_attempt_resets_fix_counters(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        self.expect(20, "ESCALATE")
        note = self.tmp / "note.md"
        note.write_text("n\n")
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--director-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:g@xhigh"))
        for k in ("STAGE_FIX_ROUNDS_01_S1", "STAGE_FIX_ROUNDS_01_S2", "GATE_FIX_ROUNDS_01", "TOTAL_FIX_ROUNDS_01"):
            self.assertEqual(self.st(k), "0", k)

    def test_drift_before_task_prompts_and_rebuild_resets(self):
        tree = sh("git", "-C", str(self.main), "rev-parse", "feature^{tree}")
        moved = sh("git", "-C", str(self.main), "-c", "user.email=t@t", "-c", "user.name=t",
                   "commit-tree", tree, "-p", self.base, "-m", "outside")
        sh("git", "-C", str(self.main), "update-ref", "refs/heads/feature", moved)
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("moved by:", Path(last.split()[1]).read_text())
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--reply", "rebuild")
        self.assertEqual(self.st("LAST_MERGED"), self.impl_head())
        self.assertEqual(self.st("BASE_SHA_01"), moved)

    def test_branch_checked_out_blocks_merge(self):
        self.behave("commit", "pass", "pass")
        sh("git", "-C", str(self.main), "switch", "-q", "feature")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("BRANCH_CHECKED_OUT", Path(last.split()[1]).read_text())
        sh("git", "-C", str(self.main), "switch", "-q", "main")
        _, lines = self.expect(0, "QUEUE_EMPTY", "--reply", "continue")
        self.assertTrue(lines[0].startswith("MERGED 01"))

    def test_usage_limit_on_commit_worker_preserves_uncommitted_work(self):
        self.behave("dirty", "usage", "commit-all", "pass", "pass")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith(" retry pause stop"), last)
        self.expect(41, "PAUSED", "--reply", "pause")
        self.assertEqual(self.expect(30, "PROMPT")[0], last)
        dirty_files = list(self.impl.glob("dirty_*.txt"))
        self.assertEqual(len(dirty_files), 1)
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")
        changed = sh("git", "-C", str(self.impl), "show", "--stat", "--format=", self.st("LAST_MERGED"))
        self.assertIn(dirty_files[0].name, changed)

    def test_usage_limit_on_reviewer_cleans_stray_edits(self):
        self.behave("commit", "usage-edit", "pass", "pass")
        self.expect(30, "PROMPT")
        self.assertFalse((self.impl / "stray_usage_edit.txt").exists())
        self.expect(0, "QUEUE_EMPTY", "--reply", "retry")

    def test_non_consecutive_voids_do_not_trigger_three_in_a_row(self):
        self.behave(
            "commit",
            "stopped", "stopped", "fix",
            "commit",
            "stopped", "pass", "pass",
        )
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("VOIDS_01_S1"), "0")

    def test_resume_interrupted_fixing_does_not_double_count_rounds(self):
        s = self.st()
        spec = self.run_dir / "specs" / "01-fix-manual.txt"
        spec.write_text("Fix task 01.\n")
        s.update({
            "CURRENT_TASK": "01",
            "LOOP_01": "fixing",
            "PAIR_01": "antigravity:g@high",
            "BASE_SHA_01": self.base,
            "LAST_IMPL_SPEC_01": str(spec),
            "IMPLEMENT_SPEC_01": str(self.run_dir / "specs" / "01-implement.txt"),
            "TOTAL_FIX_ROUNDS_01": "1",
            "STAGE_FIX_ROUNDS_01_S1": "1",
        })
        (self.run_dir / "specs" / "01-implement.txt").write_text("Impl 01.\n")
        self.state.write_text(json.dumps(s))
        (self.impl / "half_written.txt").write_text("partial\n")
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "1")
        self.assertFalse((self.impl / "half_written.txt").exists())

    def test_resume_interrupted_reviewing_cleans_and_retries(self):
        s = self.st()
        s.update({
            "CURRENT_TASK": "01",
            "LOOP_01": "reviewing:1",
            "PAIR_01": "antigravity:g@high",
            "BASE_SHA_01": self.base,
            "WORKER_HEAD_01": self.base,
        })
        self.state.write_text(json.dumps(s))
        (self.impl / "stray_mid_review.txt").write_text("oops\n")
        self.behave("pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertFalse((self.impl / "stray_mid_review.txt").exists())
        self.assertEqual(self.st("VOIDS_01_S1"), "0")

    def test_resume_interrupted_impl_cleans_and_restarts(self):
        s = self.st()
        spec = self.run_dir / "specs" / "01-implement.txt"
        spec.write_text("Impl task 01.\n")
        s.update({
            "CURRENT_TASK": "01",
            "LOOP_01": "impl",
            "PAIR_01": "antigravity:g@high",
            "BASE_SHA_01": self.base,
            "LAST_IMPL_SPEC_01": str(spec),
            "IMPLEMENT_SPEC_01": str(spec),
            "IMPL_CONVERSATION_ID_01": "stale_impl_conv",
        })
        self.state.write_text(json.dumps(s))
        (self.impl / "half_impl.txt").write_text("partial\n")
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertFalse((self.impl / "half_impl.txt").exists())
        self.assertEqual(self.calls()[0]["conversation"], "")

    def test_resume_interrupted_committing_preserves_dirty_files_and_reuses(self):
        s = self.st()
        spec = self.run_dir / "specs" / "01-commit-1.txt"
        spec.write_text("Commit uncommitted files.\n")
        s.update({
            "CURRENT_TASK": "01",
            "LOOP_01": "committing",
            "PAIR_01": "antigravity:g@high",
            "BASE_SHA_01": self.base,
            "LAST_IMPL_SPEC_01": str(spec),
            "IMPL_CONVERSATION_ID_01": "conv_commit_resume",
            "COMMIT_ROUNDS_01": "1",
        })
        self.state.write_text(json.dumps(s))
        (self.impl / "dirty_keep.txt").write_text("keep me\n")
        self.behave("commit-all", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.calls()[0]["conversation"], "conv_commit_resume")
        changed = sh("git", "-C", str(self.impl), "show", "--stat", "--format=", self.st("LAST_MERGED"))
        self.assertIn("dirty_keep.txt", changed)

    def test_escalation_attempt_resets_no_verdict_counter(self):
        s = self.st()
        s.update({
            "CURRENT_TASK": "01",
            "LOOP_01": "escalating",
            "ATTEMPT_01": "1",
            "PAIR_01": "antigravity:g@high",
            "BASE_SHA_01": self.base,
            "NO_VERDICT_01_S1": "2",
            "VOIDS_01_S1": "2",
            "ESC_INPUT_01": "task 01",
        })
        (self.run_dir / "specs" / "01-implement.txt").write_text("Impl 01.\n")
        self.state.write_text(json.dumps(s))
        note = self.tmp / "note.md"
        note.write_text("n\n")
        # Attempt 2: commit, then first noverdict should get a retry (pass) rather than immediate fix
        self.behave("commit", "noverdict", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--director-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:g@xhigh"))
        self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "0")
        self.assertEqual(self.st("NO_VERDICT_01_S1"), "0")


class PicksAndLadder(Base):
    def use_up_ladder(self):
        """Fail the first attempt and both rungs; stop at the third escalation."""
        self.behave("fail", "fail", "fail")
        note = self.tmp / "note.md"
        note.write_text("n\n")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("next rung antigravity:g@xhigh", last)
        last, _ = self.expect(20, "ESCALATE", "--director-result",
                               self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:g@xhigh"))
        self.assertIn("attempt 2, next rung antigravity:big@high", last)
        last, _ = self.expect(20, "ESCALATE", "--director-result",
                               self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:big@high"))
        self.assertIn("attempt 3, ladder used up, replan allowed", last)
        return note

    def test_old_format_state_refused(self):
        s = self.st()
        del s["ROLE_DIRECTOR"]
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
        self.assertEqual(self.st("PAIR_01"), "antigravity:big@high")

    def test_past_ladder_pair_is_probed_first(self):
        note = self.use_up_ladder()
        self.behave("ok", "commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--director-result",
                    self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:small@low"))
        self.assertEqual(self.st("PAIR_01"), "antigravity:small@low")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_small_low"), "slug")

    def test_failed_probe_rejects_the_answer(self):
        note = self.use_up_ladder()
        self.behave("fail", "fail")
        last, _ = self.expect(20, "ESCALATE", "--director-result",
                               self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:small@low"))
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn("launch test for antigravity:small@low failed; see", hist)
        self.assertIn("probe-antigravity_small_low.md", hist)
        self.assertEqual(self.st("LOOP_01"), "escalating")
        self.assertEqual(self.st("PAIR_01"), "antigravity:big@high")

    def test_launch_uses_recorded_style(self):
        s = self.st()
        s["LAUNCH_STYLE_antigravity_g_high"] = "flag"
        self.state.write_text(json.dumps(s))
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        call = self.calls()[0]
        self.assertEqual((call["model"], call["effort"]), ("g", "high"))


class Replan(Base):
    def setUp(self):
        super().setUp()
        self.state.write_text(json.dumps({**self.st(), "LAUNCH_STYLE_antigravity_small_high": "flag"}))

    def replan_to(self, tid, reviews="antigravity:small@low"):
        (self.run_dir / "queue.txt").write_text("01 02\n")
        self.behave("fail")
        self.expect(20, "ESCALATE")
        self.task(tid, "AC-1", "REPLANNED_FROM=01")
        meta = self.docs / "tasks" / f"{tid}.meta"
        meta.write_text(meta.read_text().replace("antigravity:small@high", reviews))

    def test_replan_review_pairs_are_probed_and_merged(self):
        self.replan_to("01a")
        # probe 01a reviewer, then 01a (commit, pass, pass), then 02 (commit, pass, pass)
        self.behave("ok", "commit", "pass", "pass", "commit", "pass", "pass")
        _, lines = self.expect(0, "QUEUE_EMPTY", "--director-result",
                               self.result_file(f"RESULT: REPLAN {self.base} 01a"))
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_small_low"), "slug")
        self.assertTrue(any(l.startswith("MERGED 01a ") for l in lines))
        self.assertTrue(any(l.startswith("MERGED 02 ") for l in lines))

    def test_replan_probe_failure_goes_to_fix_meta(self):
        self.replan_to("01a")
        self.behave("fail", "fail")
        last, _ = self.expect(21, "FIX_META", "--director-result",
                               self.result_file(f"RESULT: REPLAN {self.base} 01a"))
        text = Path(last.split()[1]).read_text()
        self.assertIn("PROBLEM 01a: REVIEW_1 antigravity:small@low failed its test launch; see ", text)
        self.assertIn("PROBLEM 01a: REVIEW_2 antigravity:small@low failed its test launch; see ", text)
        self.assertIn("probe-antigravity_small_low.md", text)
        self.assertEqual(self.st("META_FAILS"), "1")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01", "02"])
        self.task("01a", "AC-1", "REPLANNED_FROM=01")
        self.behave("commit", "pass", "pass", "commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--director-result", self.result_file("RESULT: META_FIXED"))
        self.assertEqual(self.st("META_FAILS"), "0")

    def test_replan_runs_fix_meta_then_replaces_task(self):
        (self.run_dir / "queue.txt").write_text("01 02\n")
        self.behave("fail")
        self.expect(20, "ESCALATE")
        self.task("01a", "AC-1")  # missing REPLANNED_FROM
        last, _ = self.expect(21, "FIX_META", "--director-result",
                              self.result_file(f"RESULT: REPLAN {self.base} 01a"))
        self.assertIn("needs REPLANNED_FROM=01", Path(last.split()[1]).read_text())
        self.expect(21, "FIX_META")
        self.task("01a", "AC-1", "REPLANNED_FROM=01")
        self.behave("commit", "pass", "pass", "commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--director-result", self.result_file("RESULT: META_FIXED"))

    def test_replan_of_a_replanned_task_is_rejected(self):
        self.task("01", "AC-1", "REPLANNED_FROM=00")
        self.behave("fail")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("replan not allowed", last)
        self.expect(20, "ESCALATE", "--director-result", self.result_file(f"RESULT: REPLAN {self.base} 01b"))
        self.assertIn("Replan is not allowed", (self.run_dir / "verdicts" / "01-history.md").read_text())


class FinalFix(Base):
    def test_final_fix_task_runs_after_queue_empty(self):
        (self.run_dir / "queue.txt").write_text("01\n")
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        self.task("F1", "none")
        (self.run_dir / "queue.txt").write_text("F1\n")
        head = self.feature()
        self.behave("commit", "pass", "pass")
        _, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("BASE_SHA_F1"), head)
        self.assertTrue(lines[0].startswith("MERGED F1"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
