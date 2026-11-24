"""Tests for Claude Code (`claude` CLI) runner dispatch and mixed cross-harness runs."""
import json
import os
import signal
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

from helpers import Base, SKILL, sh

sys.path.insert(0, str(SKILL / "scripts"))
from common import AgyRunner, Run  # noqa: E402

FAKE_CLAUDE = SKILL / "tests" / "fake_claude.py"
CATALOG_MULTI = SKILL / "tests" / "fixtures" / "catalog_multi.json"
RUN_DIRECTOR = SKILL / "scripts" / "run_director.py"
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

    def director(self, *args):
        r = subprocess.run(
            [sys.executable, "-I", str(RUN_DIRECTOR), *args],
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
        self.assertIn("stream-json", calls[0]["argv"])
        self.assertIn("--verbose", calls[0]["argv"])
        self.assertIn("--dangerously-skip-permissions", calls[0]["argv"])
        self.assertEqual((calls[0]["model"], calls[0]["effort"], calls[0]["resume"]), ("opus", "high", ""))
        self.assertIn("--resume", calls[1]["argv"])
        self.assertNotIn("--conversation", calls[1]["argv"])
        self.assertEqual(calls[1]["resume"], "sess_abc")
        self.assertNotIn("--effort", calls[2]["argv"])
        self.assertEqual(calls[2]["model"], "haiku")

    def test_stream_json_events_and_worker_logs_both_harnesses(self):
        self.behave({
            "do": "response",
            "text": "agy streamed ok\n",
            "conv_id": "conv_stream1",
            "wrap_event": "result",
            "stream_events": [
                {"event": "init", "init": {"conversation_id": "conv_stream1"}},
                {"event": "step_update", "step_update": {
                    "step_type": "tool", "state": "ACTIVE", "tool_name": "run_command",
                    "tool_info": {"command": "pytest -q"},
                }},
                {"event": "step_update", "step_update": {
                    "step_type": "tool", "state": "DONE", "tool_name": "run_command",
                    "duration_seconds": 1.2,
                }},
                {"event": "step_update", "step_update": {"step_type": "agent_response", "state": "DONE"}},
            ],
        })
        self.claude_behave({
            "do": "response",
            "text": "claude streamed ok\n",
            "session_id": "sess_stream1",
            "stream_events": [
                {"type": "system", "subtype": "init", "session_id": "sess_stream1"},
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}},
                ]}},
            ],
        })
        runner = self._runner()
        r_agy = runner.run("p_agy", pair="antigravity:gemini-4-hydrogen@high", worktree=self.impl, label="agy-stream")
        self.assertEqual(r_agy, ("done", "succeeded", "agy streamed ok\n", "conv_stream1"))
        r_cl = runner.run("p_cl", pair="claude:sonnet@high", worktree=self.impl, label="claude-stream")
        self.assertEqual(r_cl, ("done", "succeeded", "claude streamed ok\n", "sess_stream1"))

        agy_log = (self.run_dir / "logs" / "agy-stream-1.log").read_text(encoding="utf-8")
        self.assertIn("init conv=conv_stream1", agy_log)
        self.assertIn("tool: run_command (pytest -q)", agy_log)
        self.assertNotIn("tool done", agy_log)  # 1.2s: only calls of 10s or more get a "tool done" line
        self.assertIn("done (succeeded)", agy_log)

        cl_log = (self.run_dir / "logs" / "claude-stream-1.log").read_text(encoding="utf-8")
        self.assertIn("init session=sess_stream1", cl_log)
        self.assertIn("tool: Bash (git status)", cl_log)
        self.assertIn("done (succeeded)", cl_log)

    def test_heartbeat_emitted_during_silent_wait(self):
        s = self.st()
        s["WORKER_HEARTBEAT"] = "0.2"
        self.state.write_text(json.dumps(s))
        self.behave({"do": "response", "text": "slow ok\n", "sleep": 0.55})
        runner = self._runner()
        res = runner.run("slow", pair="antigravity:gemini-4-hydrogen@high", worktree=self.impl, label="slow-worker")
        self.assertEqual(res[0], "done")
        log_text = (self.run_dir / "logs" / "slow-worker-1.log").read_text(encoding="utf-8")
        self.assertIn("still running (no output for", log_text)

    def test_worker_stream_disabled_and_auto_fallback_to_json(self):
        # Explicit opt-out via WORKER_STREAM=0
        s = self.st()
        s["WORKER_STREAM"] = "0"
        self.state.write_text(json.dumps(s))
        self.claude_behave({"do": "response", "text": "json ok\n", "session_id": "s_json"})
        runner = self._runner()
        res = runner.run("p_json", pair="claude:sonnet@high", worktree=self.impl)
        self.assertEqual(res, ("done", "succeeded", "json ok\n", "s_json"))
        calls = self.claude_calls()
        self.assertEqual(calls[0]["output_format"], "json")
        self.assertFalse(calls[0]["verbose"])

        # Re-enable stream-json and verify auto-fallback if CLI rejects stream-json
        s.pop("WORKER_STREAM")
        self.state.write_text(json.dumps(s))
        self.claude_behave(
            {"do": "response", "raw_stderr": "error: unsupported value 'stream-json' for --output-format", "rc": 2},
            {"do": "response", "text": "fallback ok\n", "session_id": "s_fb"},
        )
        runner = self._runner()
        res_fb = runner.run("p_fb", pair="claude:sonnet@high", worktree=self.impl)
        self.assertEqual(res_fb, ("done", "succeeded", "fallback ok\n", "s_fb"))
        calls = self.claude_calls()
        self.assertEqual(calls[1]["output_format"], "stream-json")
        self.assertEqual(calls[2]["output_format"], "json")

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

    # Stream events a crashed worker printed before it died: they quote token counts and tool output.
    INNOCUOUS = "\n".join(json.dumps(e) for e in (
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking the quota code."}],
                                          "usage": {"input_tokens": 429, "output_tokens": 12}}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "rate limit: 429 per quota window; "
                                                  "session not found handling in api.py"}]}},
    ))

    def test_a_crash_is_not_a_usage_limit_because_the_stream_mentions_429(self):
        self.claude_behave(
            {"do": "response", "raw_stdout": self.INNOCUOUS + "\nfatal: worker crashed", "rc": 1},
            {"do": "response", "raw_stdout": self.INNOCUOUS + "\n" + json.dumps(
                {"type": "error", "error": {"code": 429, "message": "Too many requests"}}), "rc": 1},
            {"do": "response", "raw_stdout": self.INNOCUOUS, "raw_stderr": "Error: quota exceeded for this project",
             "rc": 1},
        )
        runner = self._runner()
        self.assertEqual(runner.run("p1", pair="claude:opus@high", worktree=self.impl)[0], "stopped")
        self.assertEqual(runner.run("p2", pair="claude:opus@high", worktree=self.impl)[0], "usage")
        self.assertEqual(runner.run("p3", pair="claude:opus@high", worktree=self.impl)[0], "usage")

    def test_a_crash_is_not_a_failed_resume_because_the_stream_mentions_a_session(self):
        self.claude_behave(
            {"do": "response", "raw_stdout": self.INNOCUOUS + "\nfatal: worker crashed", "rc": 1},
            {"do": "response", "raw_stdout": self.INNOCUOUS, "raw_stderr": "No conversation found with session ID: s1",
             "rc": 1},
        )
        runner = self._runner()
        res = runner.try_resume("hi", "claude:opus@high", self.impl, "s1")
        self.assertEqual(res[0], "stopped")
        self.assertIsNone(runner.try_resume("hi", "claude:opus@high", self.impl, "s1"))

    def test_a_result_is_used_when_the_cli_does_not_exit(self):
        s = self.st()
        s["WORKER_TIMEOUT"] = "60"
        self.state.write_text(json.dumps(s))
        self.claude_behave({"do": "response", "text": "finished\n", "session_id": "s_hang", "sleep_after": 60})
        with unittest.mock.patch("common.ENVELOPE_GRACE", 0.3):
            start = time.monotonic()
            res = self._runner().run("p", pair="claude:opus@high", worktree=self.impl)
        self.assertEqual(res, ("done", "succeeded", "finished\n", "s_hang"))
        self.assertLess(time.monotonic() - start, 15)

    def test_a_json_result_is_used_when_the_cli_does_not_exit_without_streaming(self):
        # WORKER_STREAM=0 (or the json fallback): the one result object, pretty or not, with or without a newline.
        s = self.st()
        s.update({"WORKER_TIMEOUT": "60", "WORKER_STREAM": "0"})
        self.state.write_text(json.dumps(s))
        self.claude_behave({"do": "response", "text": "c1\n", "session_id": "s1", "sleep_after": 60, "pretty": True},
                           {"do": "response", "text": "c2\n", "session_id": "s2", "sleep_after": 60,
                            "no_final_newline": True})
        self.behave({"do": "response", "text": "a1\n", "conv_id": "conv_a", "sleep_after": 60,
                     "no_final_newline": True})
        with unittest.mock.patch("common.ENVELOPE_GRACE", 0.3):
            for pair, want in (("claude:opus@high", ("done", "succeeded", "c1\n", "s1")),
                               ("claude:opus@high", ("done", "succeeded", "c2\n", "s2")),
                               ("antigravity:gemini-4-hydrogen@high", ("done", "succeeded", "a1\n", "conv_a"))):
                start = time.monotonic()
                self.assertEqual(self._runner().run("p", pair=pair, worktree=self.impl), want)
                self.assertLess(time.monotonic() - start, 15)
        self.assertTrue(all(c["output_format"] == "json" for c in self.claude_calls() + self.calls()))

    def test_a_result_held_when_the_timeout_fires_is_used(self):
        s = self.st()
        s["WORKER_TIMEOUT"] = "3"
        self.state.write_text(json.dumps(s))
        self.claude_behave({"do": "response", "text": "finished\n", "session_id": "s_late", "sleep_after": 60})
        with unittest.mock.patch("common.ENVELOPE_GRACE", 100):
            res = self._runner().run("p", pair="claude:opus@high", worktree=self.impl)
        self.assertEqual(res, ("done", "succeeded", "finished\n", "s_late"))


    # A Claude worker that ends its turn while a run_in_background Bash command runs (stream shape seen live).
    BG_ON = {"event": {"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "b1"}]}}
    BG_OFF = [{"event": {"type": "system", "subtype": "task_notification", "task_id": "b1"}},
              {"event": {"type": "system", "subtype": "background_tasks_changed", "tasks": []}},
              {"event": {"type": "system", "subtype": "init", "session_id": "s_bg"}},
              {"event": {"type": "assistant", "message": {"content": [{"type": "text", "text": "FINAL"}]}}}]

    def _timed_run(self, **patches):
        with unittest.mock.patch.multiple("common", **patches):
            start = time.monotonic()
            res = self._runner().run("p", pair="claude:opus@high", worktree=self.impl, label="bg")
        return res, time.monotonic() - start

    def test_a_worker_waiting_on_a_background_command_is_not_cut_off(self):
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            self.BG_ON, {"result": "WAITING\n", "extra": {"num_turns": 2, "duration_ms": 6000, "total_cost_usd": 0.10,
                                                         "usage": {"input_tokens": 10, "output_tokens": 5}}},
            {"sleep": 1.5}, *self.BG_OFF,
            {"result": "FINAL\n", "extra": {"num_turns": 1, "duration_ms": 4000, "total_cost_usd": 0.25,
                                            "usage": {"input_tokens": 20, "output_tokens": 1}}}]})
        res, took = self._timed_run(ENVELOPE_GRACE=1)
        self.assertEqual(res, ("done", "succeeded", "FINAL\n", "s_bg"))
        self.assertGreater(took, 1.5)
        # Stats cover both turn segments; total_cost_usd is already the process's running total.
        log = (self.run_dir / "logs" / "bg-1.log").read_text(encoding="utf-8")
        self.assertIn("done (succeeded) pair=claude:opus@high cost=$0.25 turns=3 model_time=10s tokens=36", log)

    def test_a_background_command_that_never_ends_is_waited_on_for_a_bounded_time(self):
        s = self.st()
        s["WORKER_BACKGROUND_WAIT"] = "1"
        self.state.write_text(json.dumps(s))
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            self.BG_ON, {"result": "WAITING\n"}, {"sleep": 60}]})
        res, took = self._timed_run(ENVELOPE_GRACE=0.5)
        self.assertEqual(res, ("done", "succeeded", "WAITING\n", "s_bg"))
        self.assertGreater(took, 1)
        self.assertLess(took, 15)

    def test_activity_after_a_result_holds_the_grace_until_the_next_result(self):
        # No background commands: the grace runs from the latest result, and a new turn in between stops it.
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            {"result": "FIRST\n"}, {"sleep": 0.2}, *self.BG_OFF[2:], {"sleep": 1.5}, {"result": "SECOND\n"},
            {"sleep": 60}]})
        res, took = self._timed_run(ENVELOPE_GRACE=1)
        self.assertEqual(res, ("done", "succeeded", "SECOND\n", "s_bg"))
        self.assertGreater(took, 2.5)
        self.assertLess(took, 15)

    def test_a_result_with_no_background_commands_is_used_after_the_grace(self):
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            self.BG_ON, *self.BG_OFF[:2], {"result": "DONE\n"}, {"sleep": 60}]})
        res, took = self._timed_run(ENVELOPE_GRACE=0.3, BACKGROUND_WAIT=100)
        self.assertEqual(res, ("done", "succeeded", "DONE\n", "s_bg"))
        self.assertLess(took, 15)

    def _set_state(self, **kv):
        s = self.st()
        s.update({k: str(v) for k, v in kv.items()})
        self.state.write_text(json.dumps(s))

    def test_a_new_turn_after_a_result_that_runs_into_the_timeout_is_a_timeout(self):
        # The interim result is stale once a new turn starts: that turn timing out is not a success.
        self._set_state(WORKER_TIMEOUT=3)
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            self.BG_ON, {"result": "WAITING\n"}, {"sleep": 0.3}, *self.BG_OFF, {"sleep": 60}]})
        res, took = self._timed_run(ENVELOPE_GRACE=100)
        self.assertEqual(res[0], "stopped")
        self.assertIn("timed out after 3s", res[1])
        self.assertLess(took, 15)

    def test_task_events_do_not_hold_a_worker_past_the_background_wait(self):
        # A background command that keeps reporting progress is still bounded by WORKER_BACKGROUND_WAIT.
        self._set_state(WORKER_BACKGROUND_WAIT=1.2, WORKER_TIMEOUT=30)
        tick = {"type": "system", "subtype": "task_updated", "task_id": "b1"}
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            self.BG_ON, {"result": "WAITING\n"}, {"every": 0.3, "times": 60, "event": tick}, {"sleep": 60}]})
        res, took = self._timed_run(ENVELOPE_GRACE=0.5)
        self.assertEqual(res, ("done", "succeeded", "WAITING\n", "s_bg"))
        self.assertGreater(took, 1.2)
        self.assertLess(took, 10)
        log = (self.run_dir / "logs" / "bg-1.log").read_text(encoding="utf-8")
        self.assertIn("background commands still running 1s after its result; stopping it", log)

    def test_the_grace_restarts_when_the_last_background_command_ends(self):
        # The list empties but no new turn comes and the CLI does not exit: stopped one grace after the list emptied.
        self._set_state(WORKER_TIMEOUT=30)
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            self.BG_ON, {"result": "WAITING\n"}, {"sleep": 1}, self.BG_OFF[1], {"sleep": 60}]})
        res, took = self._timed_run(ENVELOPE_GRACE=0.8, BACKGROUND_WAIT=100)
        self.assertEqual(res, ("done", "succeeded", "WAITING\n", "s_bg"))
        self.assertGreater(took, 1.75)  # 1 s to the empty list, then the full grace (not the grace from the result)
        self.assertLess(took, 10)

    def test_an_empty_task_list_during_a_turn_starts_no_clock(self):
        self._set_state(WORKER_TIMEOUT=30)
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            self.BG_ON, {"result": "FIRST\n"}, {"sleep": 0.2}, self.BG_OFF[2], self.BG_OFF[3], self.BG_OFF[1],
            {"sleep": 1.5}, {"result": "SECOND\n"}, {"sleep": 60}]})
        res, took = self._timed_run(ENVELOPE_GRACE=1, BACKGROUND_WAIT=100)
        self.assertEqual(res, ("done", "succeeded", "SECOND\n", "s_bg"))
        self.assertLess(took, 10)

    @staticmethod
    def _wait_gone(pid, limit=10):
        """Whether pid is gone (or a zombie) within limit seconds."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            if state[:1] in ("", "Z"):
                return True
            time.sleep(0.1)
        return False

    def _assert_child_stopped(self):
        pid, done = json.loads(self.claude_world.read_text())["child_pids"][0]
        self.assertTrue(self._wait_gone(pid))
        self.assertFalse(Path(done).exists())  # gone without finishing its work: stopped, not ended

    def test_a_stopped_worker_takes_its_detached_commands_with_it(self):
        # Claude Code runs a background command in a session of its own; stopping the worker must stop it too.
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            {"detached_child": 4}, {"result": "DONE\n"}, {"sleep": 60}]})
        res, _ = self._timed_run(ENVELOPE_GRACE=0.5)
        self.assertEqual(res[2], "DONE\n")
        self._assert_child_stopped()

    def test_a_worker_that_exits_by_itself_takes_its_detached_commands_with_it(self):
        # Once the worker has exited, its detached child belongs to init: it is found from the listing taken at
        # the result, while the worker still ran.
        self.claude_behave({"do": "script", "session_id": "s_bg", "steps": [
            {"detached_child": 4}, {"result": "DONE\n"}, {"sleep": 1}]})
        res, took = self._timed_run(ENVELOPE_GRACE=100)
        self.assertEqual(res, ("done", "succeeded", "DONE\n", "s_bg"))
        self.assertLess(took, 4)
        self._assert_child_stopped()

    def test_a_run_unwound_by_an_exception_stops_the_worker_and_lets_go_of_it(self):
        # Interrupted (the driver's signal handler, which stops the workers itself) or any other exception.
        import common
        import harnesses
        old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in old.items()])

        def other(obj, acc=None):
            raise RuntimeError("boom")

        def interrupt(obj, acc=None):
            common._on_signal(signal.SIGTERM, None)

        for seq, (label, effect, exc) in enumerate((("other", other, RuntimeError),
                                                    ("signal", interrupt, common.Interrupted))):
            with self.subTest(label):
                self.claude_world.write_text(json.dumps({"seq": seq * 10, "calls": [], "behaviors": [
                    {"do": "script", "session_id": "s_x", "steps": [
                        {"detached_child": 4}, {"event": {"type": "system", "subtype": "init"}}, {"sleep": 60}]}]}))
                with unittest.mock.patch.object(harnesses.ClaudeAdapter, "parse_stream_event", side_effect=effect):
                    with self.assertRaises(exc):
                        self._runner().run("p", pair="claude:opus@high", worktree=self.impl, label=label)
                self._assert_child_stopped()
                self.assertEqual(common._CHILDREN, [])
                self.assertIsNone(self.st("WORKER_PID"))
                log = self.run_dir / "logs" / f"{label}-1.log"
                self.assertIn(f"{label}: interrupted", log.read_text(encoding="utf-8"))

    # agy's result response joins every non-empty agent message of the turn (stream shapes seen live 2026-11-24, agy
    # 1.5.15): a message written before a background command ended, or narration before a tool call.
    @staticmethod
    def _agy_step(i, step_type, state="DONE", **kw):
        return {"event": "step_update", "step_update": {"step_index": i, "step_type": step_type, "state": state, **kw}}

    def _agy_bg_events(self, interim, final):
        """A worker that ends its turn while a background command runs; the command's end wakes it."""
        s = self._agy_step
        return [
            {"event": "init", "init": {"conversation_id": "conv_bg"}},
            s(0, "user_input"),
            s(1, "agent_response", text_delta=""),
            s(2, "tool", "ACTIVE", tool_name="run_command", tool_info={"parameters": {"CommandLine": "sleep 25"}}),
            s(2, "tool", tool_name="run_command"),
            s(3, "agent_response", "ACTIVE", text_delta=interim[:3]),
            s(3, "agent_response", text_delta=interim[3:]),
            s(4, "system_message"),
            s(5, "agent_response", "ACTIVE", text_delta=final[:4]),
            s(5, "agent_response", text_delta=final[4:] + "\n"),
        ]

    def _agy_bg_run(self, events, text):
        self.behave({"do": "response", "text": text, "conv_id": "conv_bg", "wrap_event": "result",
                     "stream_events": events})
        res = self._runner().run("p", pair="antigravity:gemini-4-hydrogen@high", worktree=self.impl, label="agybg")
        log = (self.run_dir / "logs" / "agybg-1.log").read_text(encoding="utf-8")
        return res, log

    def test_agy_final_message_after_a_background_wait_decides_failed(self):
        res, log = self._agy_bg_run(self._agy_bg_events("WAITING", "FAILED: could not finish"),
                                    "WAITING\nFAILED: could not finish\n")
        self.assertEqual(res[:2], ("done", "failed"))
        self.assertTrue(res[2].startswith("FAILED:"), res[2])
        self.assertEqual(res[2].strip(), "FAILED: could not finish")
        self.assertIn("dropped earlier messages before the final one: WAITING", log)
        self.assertIn("done (failed)", log)

    def test_agy_final_message_after_a_background_wait_decides_ask(self):
        res, _ = self._agy_bg_run(self._agy_bg_events("Started the build; waiting.", "ASK: which branch?"),
                                  "Started the build; waiting.\nASK: which branch?\n")
        self.assertEqual(res[0], "ask")
        self.assertEqual(res[2].strip(), "ASK: which branch?")

    def test_agy_tool_call_after_the_wake_up_still_decides_failed(self):
        # Probe 1, live 2026-11-24: after the system_message the worker reads its output, then answers.
        s = self._agy_step
        events = [
            s(0, "user_input"), s(1, "agent_response", text_delta=""),
            s(2, "tool", "ACTIVE", tool_name="run_command"), s(2, "tool", tool_name="run_command"),
            s(3, "agent_response", text_delta="WAITING\n"), s(4, "system_message"),
            s(5, "agent_response", text_delta=""),
            s(6, "tool", "ACTIVE", tool_name="run_command", tool_info={"parameters": {"CommandLine": "cat out"}}),
            s(6, "tool", tool_name="run_command"),
            s(7, "agent_response", text_delta="FAILED: test marker\n"),
        ]
        res, log = self._agy_bg_run(events, "WAITING\nFAILED: test marker\n")
        self.assertEqual(res, ("done", "failed", "FAILED: test marker\n", "conv_bg"))
        self.assertIn("dropped earlier messages before the final one: WAITING", log)

    def test_agy_narration_before_a_tool_call_does_not_hide_the_final_message(self):
        # Two agent messages with a tool call between them and no system_message: the last one is the response.
        s = self._agy_step
        events = [s(0, "user_input"), s(1, "agent_response", text_delta="Working on it.\n"),
                  s(2, "tool", tool_name="run_command"),
                  s(3, "agent_response", text_delta="FAILED: could not finish\n")]
        res, log = self._agy_bg_run(events, "Working on it.\nFAILED: could not finish\n")
        self.assertEqual(res, ("done", "failed", "FAILED: could not finish\n", "conv_bg"))
        self.assertIn("dropped earlier messages before the final one: Working on it.", log)

    def test_agy_the_last_non_empty_message_wins(self):
        s = self._agy_step
        events = [s(0, "user_input"), s(1, "agent_response", text_delta="Checked the tests.\n"),
                  s(2, "tool", tool_name="run_command"),
                  s(3, "agent_response", text_delta="FAILED: two tests fail\n"),
                  s(4, "agent_response", text_delta="")]
        res, _ = self._agy_bg_run(events, "Checked the tests.\nFAILED: two tests fail\n")
        self.assertEqual(res, ("done", "failed", "FAILED: two tests fail\n", "conv_bg"))

    def test_agy_single_message_result_is_unchanged(self):
        s = self._agy_step
        events = [s(0, "user_input"), s(1, "tool", tool_name="run_command"),
                  s(2, "agent_response", text_delta="All done.\n")]
        res, log = self._agy_bg_run(events, "All done.\n")
        self.assertEqual(res, ("done", "succeeded", "All done.\n", "conv_bg"))
        self.assertNotIn("dropped earlier messages", log)

    def test_agy_response_that_does_not_end_with_the_last_message_is_unchanged(self):
        text = "WAITING\nFAILED: could not finish\nSomething else.\n"
        res, log = self._agy_bg_run(self._agy_bg_events("WAITING", "FAILED: could not finish"), text)
        self.assertEqual(res, ("done", "succeeded", text, "conv_bg"))
        self.assertNotIn("dropped earlier messages", log)

    def test_agy_last_message_matching_mid_line_leaves_the_response_unchanged(self):
        # The stream missed the start of the last message: its text ends the response but starts mid-line.
        s = self._agy_step
        events = [s(0, "user_input"), s(1, "agent_response", text_delta="WAITING\n"),
                  s(2, "tool", tool_name="run_command"), s(3, "agent_response", text_delta="could not finish\n")]
        text = "WAITING\nFAILED: could not finish\n"
        res, log = self._agy_bg_run(events, text)
        self.assertEqual(res, ("done", "succeeded", text, "conv_bg"))
        self.assertNotIn("dropped earlier messages", log)

    def test_agy_without_stream_events_the_response_is_unchanged(self):
        s = self.st()
        s["WORKER_STREAM"] = "0"
        self.state.write_text(json.dumps(s))
        text = "Working on it.\nFAILED: could not finish\n"
        self.behave({"do": "response", "text": text, "conv_id": "conv_bg"})
        res = self._runner().run("p", pair="antigravity:gemini-4-hydrogen@high", worktree=self.impl, label="agybg")
        self.assertEqual(res, ("done", "succeeded", text, "conv_bg"))

    def test_timeout_defaults_by_access(self):
        runner = self._runner()
        with unittest.mock.patch.dict(os.environ, {"WORKER_TIMEOUT": "", "AGY_TIMEOUT": ""}):
            self.assertEqual((runner._timeout("full"), runner._timeout("read")), (3600, 1800))
            s = self.st()
            s["WORKER_TIMEOUT"] = "900"
            self.state.write_text(json.dumps(s))
            runner = self._runner()
            self.assertEqual((runner._timeout("full"), runner._timeout("read")), (900, 900))


class ClaudeDirectorAndTaskFallbackTests(MultiHarnessBase):
    def test_only_the_flight_director_keeps_mcp(self):
        spec = self.tmp / "director.txt"
        spec.write_text("Start job.\n")
        self.claude_behave({"do": "response", "text": "RESULT: COMMITTED\n", "session_id": "d1"})
        self.behave({"do": "response", "text": "RESULT: COMMITTED\n", "conv_id": "d2"})
        for pair in ("claude:opus@high", "antigravity:gemini-4-hydrogen@high"):
            rc, last = self.director("start", "--state", str(self.state), "--spec", str(spec), "--pair", pair)
            self.assertEqual(rc, 0, last)
        director_claude, director_agy = self.claude_calls()[0]["argv"], self.calls()[0]["argv"]
        self.assertIn("--dangerously-skip-permissions", director_claude)
        self.assertNotIn("--strict-mcp-config", director_claude)
        self.assertNotIn("--settings", director_agy)
        # A crew worker on each harness: full access, no MCP.
        (self.docs / "tasks" / "01.meta").write_text(
            "TITLE=Task 01\nTIER=standard\nSPEC_TYPE=checklist\nACS=AC-1\n"
            "CREW=claude:sonnet@high\nREVIEW_1=claude:sonnet@high\nREVIEW_2=claude:sonnet@high\n")
        self.claude_behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY")
        crew = self.claude_calls()[1]["argv"]
        self.assertIn("--dangerously-skip-permissions", crew)
        self.assertIn("--strict-mcp-config", crew)
        runner = AgyRunner(Run(self.state))
        with unittest.mock.patch.dict(os.environ, self._env()):
            runner.run("work", pair="antigravity:gemini-4-hydrogen@high", worktree=self.impl)
        crew_agy = self.calls()[-1]["argv"]
        self.assertIn("--dangerously-skip-permissions", crew_agy)
        self.assertEqual(json.loads(crew_agy[crew_agy.index("--settings") + 1]), {"permissions": {"deny": ["mcp(*)"]}})

    def test_director_lost_13_and_usage_limit_31(self):
        spec = self.tmp / "director.txt"
        spec.write_text("Start job.\n")
        self.claude_behave(
            {"do": "response", "text": "RESULT: COMMITTED\n", "session_id": "dir_c1"},
            {"do": "response", "text": "RESULT: COMMITTED\n", "session_id": "dir_unexpected"},
            "usage",
        )
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(spec),
                                 "--pair", "claude:opus@high", "--keep-open")
        self.assertEqual(rc, 0, last)
        self.assertEqual(self.st("DIRECTOR_CONVERSATION_ID"), "dir_c1")

        rc, last = self.director("send", "--state", str(self.state), "--spec", str(spec),
                                 "--pair", "claude:opus@high")
        self.assertEqual(rc, 13, last)
        self.assertTrue(last.startswith("LOST "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))

        rc, last = self.director("start", "--state", str(self.state), "--spec", str(spec),
                                 "--pair", "claude:opus@high")
        self.assertEqual(rc, 31, last)
        self.assertTrue(last.startswith("USAGE_LIMIT "), last)

    def test_run_task_usage_prompt_and_resume_fallback(self):
        self.task("01", "AC-1", extra="")
        meta = self.docs / "tasks" / "01.meta"
        meta.write_text(
            "TITLE=Task 01\nTIER=standard\nSPEC_TYPE=checklist\nACS=AC-1\n"
            "CREW=claude:sonnet@high\nREVIEW_1=claude:sonnet@high\nREVIEW_2=claude:sonnet@high\n"
        )
        # 1. Crew commits (sess_expired), stage 1 passes, stage 2 asks for fix.
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
            "CREW=claude:sonnet@high\nREVIEW_1=claude:sonnet@high\nREVIEW_2=claude:sonnet@high\n"
        )
        self.claude_behave("usage")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("retry pause stop", last)


class MixedCrossHarnessTests(MultiHarnessBase):
    def test_probe_step1_dispatches_to_both_harnesses(self):
        self.SCRIPT = PROBE
        s = self.st()
        s["ROLE_DIRECTOR"] = "claude:opus@high"
        s["ROLE_SAFETY"] = "antigravity:g@high"
        s["ROLE_CREW"] = "antigravity:g@high"
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
        # Test launches are read-only workers: the agy sandbox profile, never the skip-permissions flag.
        for c in self.calls():
            self.assertTrue(c["sandbox"])
            self.assertFalse(c["skip_permissions"])

    def test_run_task_mixed_agy_crew_and_claude_reviewers(self):
        meta = self.docs / "tasks" / "01.meta"
        meta.write_text(
            "TITLE=Task 01\nTIER=standard\nSPEC_TYPE=checklist\nACS=AC-1\n"
            "CREW=antigravity:g@high\nREVIEW_1=claude:sonnet@high\nREVIEW_2=claude:opus@high\n"
        )
        self.behave("commit")
        self.claude_behave("pass", "pass")
        last, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(any(l.startswith("MERGED 01 ") for l in lines), lines)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(self.claude_calls()), 2)
        self.assertEqual(self.claude_calls()[0]["model"], "sonnet")
        # Crew edits and commits: full access. Reviewers are read-only: no skip flag, a dontAsk allowlist
        # with read-only git and no general shell.
        self.assertTrue(self.calls()[0]["skip_permissions"])
        for c in self.claude_calls():
            self.assertFalse(c["skip_permissions"])
            self.assertEqual(c["permission_mode"], "dontAsk")
            self.assertIn("Bash(git diff *)", c["allowed_tools"].split(","))
            self.assertIn(f"Bash(git -C {self.impl} diff *)", c["allowed_tools"].split(","))
            self.assertNotIn("Bash", c["allowed_tools"].split(","))
            self.assertNotIn("Write", c["allowed_tools"].split(","))
        self.assertEqual(self.claude_calls()[1]["model"], "opus")

    def test_run_design_with_claude_safety_resolves_claude_skills_and_dispatches(self):
        self.SCRIPT = RUN_DESIGN
        (self.docs_wt / "design.md").write_text("design\n")
        sh("git", "-C", str(self.docs_wt), "add", "design.md")
        sh("git", "-C", str(self.docs_wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "design")
        s = self.st()
        s.update({"TOPIC": "demo", "ROLE_SAFETY": "claude:sonnet@high"})
        self.state.write_text(json.dumps(s))

        self.claude_behave("write-output")
        last, _ = self.expect(20, "DIRECTOR_JOB", "--result", self.result_file("RESULT: NEED safety design.md .mission/safety-findings.md"))
        self.assertTrue(last.startswith("DIRECTOR_JOB safety-done "), last)
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertIn("/claude-skills/", self.claude_calls()[0]["prompt"])
        self.assertEqual(self.claude_calls()[0]["model"], "sonnet")


if __name__ == "__main__":
    unittest.main()
