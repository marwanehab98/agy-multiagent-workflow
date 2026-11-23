"""Unit tests for scripts/common.py and the harness permission profiles: RESULT-line parsing, state merging,
the driver lock, worker environments, envelope parsing and stream progress summaries."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import harnesses  # noqa: E402
from common import ERROR, Done, Run, result_words, rest_path  # noqa: E402


class ResultWords(unittest.TestCase):
    def test_table(self):
        cases = [
            ("RESULT: FLIGHT_MANIFEST_FIXED. The baseline failed", ["RESULT:", "FLIGHT_MANIFEST_FIXED"]),
            ("`RESULT: ESCALATE abc /n.md claude:o@high.` More prose.", ["RESULT:", "ESCALATE", "abc", "/n.md", "claude:o@high"]),
            ("RESULT: ESCALATE abc `/x/my note.md` claude:o@high", ["RESULT:", "ESCALATE", "abc", "/x/my note.md", "claude:o@high"]),
            ('RESULT: RELAY "/home/u/My Projects/q.md"', ["RESULT:", "RELAY", "/home/u/My Projects/q.md"]),
            ("**RESULT: NOTED** - and then some", ["RESULT:", "NOTED"]),
            ("RESULT: COMMITTED; done", ["RESULT:", "COMMITTED"]),
            ("RESULT: FINAL_CLEAN.", ["RESULT:", "FINAL_CLEAN"]),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(result_words(text), want)

    def test_rest_path_joins_unquoted_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="mc words ") as d:
            f = Path(d) / "q a.md"
            f.write_text("x")
            words = result_words(f"RESULT: RELAY {f}")
            self.assertGreater(len(words), 3)
            self.assertEqual(rest_path(words, 2), str(f))
            self.assertEqual(rest_path(["RESULT:", "RELAY", "/nope"], 2), "/nope")


class StateAndLock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.state = self.d / "state.json"
        self.state.write_text(json.dumps({"RUN_DIR": str(self.d), "A": "1"}))

    def test_set_merges_a_concurrent_write(self):
        r = Run(self.state)
        # Another process (the capsule communicator's STATE set) writes after we loaded.
        subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "state.py"), "--file", str(self.state),
                        "set", "B=2"], check=True, capture_output=True)
        r.set(C="3")
        s = json.loads(self.state.read_text())
        self.assertEqual((s["A"], s["B"], s["C"]), ("1", "2", "3"))

    def test_second_driver_is_refused(self):
        lock = self.state.parent / ".driver.lock"
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import fcntl,sys,time;f=open(sys.argv[1],'a+');fcntl.flock(f,fcntl.LOCK_EX);"
                                   "print('held',flush=True);time.sleep(30)", str(lock)], stdout=subprocess.PIPE,
                                  text=True)
        try:
            holder.stdout.readline()
            out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "run_task.py"), "--state",
                                  str(self.state), "advance"], capture_output=True, text=True)
            self.assertEqual(out.returncode, ERROR)
            self.assertIn("ERROR RUN_BUSY", out.stdout)
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

    def test_unexpected_exception_becomes_a_status_line(self):
        def body(events):
            raise KeyError("TITLE")
        code = common.drive(self.state, body)
        self.assertEqual(code, ERROR)

    def test_orphaned_worker_is_stopped(self):
        exe = self.d / "fake-worker"
        exe.write_text("#!/bin/sh\nsleep 60\n")
        exe.chmod(0o755)
        proc = subprocess.Popen([str(exe)], start_new_session=True)
        try:
            Run(self.state).set(WORKER_PID=proc.pid, WORKER_EXE=str(exe))
            note = common.reap_orphan_worker(self.state)
            self.assertIn("orphaned worker", note or "")
            proc.wait(timeout=5)
            self.assertNotIn("WORKER_PID", json.loads(self.state.read_text()))
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_reused_pid_is_not_killed(self):
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            Run(self.state).set(WORKER_PID=proc.pid, WORKER_EXE="/usr/bin/agy")
            self.assertIsNone(common.reap_orphan_worker(self.state))
            time.sleep(0.1)
            self.assertIsNone(proc.poll())
        finally:
            proc.kill()
            proc.wait()


class WorkerPermissions(unittest.TestCase):
    ENV = {"PATH": "/bin", "HOME": "/h", "GITHUB_TOKEN": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_PROFILE": "p",
           "ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t", "GEMINI_API_KEY": "g", "MY_PASSWORD": "x",
           "WORKER_ENV_KEEP": "AWS_PROFILE"}

    def test_read_workers_lose_secrets_but_keep_harness_sign_in(self):
        env = common.worker_env("read", self.ENV)
        self.assertEqual(sorted(env), sorted(["PATH", "HOME", "AWS_PROFILE", "ANTHROPIC_API_KEY",
                                              "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "WORKER_ENV_KEEP"]))
        self.assertEqual(common.worker_env("full", self.ENV), self.ENV)

    def test_cloud_provider_credentials_stay_when_the_harness_uses_them(self):
        env = {"PATH": "/bin", "AWS_REGION": "eu-west-1", "AWS_ACCESS_KEY_ID": "a", "GITHUB_TOKEN": "x"}
        self.assertNotIn("AWS_REGION", common.worker_env("read", env))
        bedrock = common.worker_env("read", {**env, "CLAUDE_CODE_USE_BEDROCK": "1"})
        self.assertEqual((bedrock.get("AWS_REGION"), bedrock.get("AWS_ACCESS_KEY_ID")), ("eu-west-1", "a"))
        self.assertNotIn("GITHUB_TOKEN", bedrock)
        vertex = common.worker_env("read", {"GOOGLE_APPLICATION_CREDENTIALS": "/k.json", "CLAUDE_CODE_USE_VERTEX": "1"})
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", vertex)

    def test_claude_read_profile(self):
        args = harnesses.ClaudeAdapter.permission_args("read", ["/tmp/run"])
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        tools = args[args.index("--allowedTools") + 1].split(",")
        # Writes only under the given folders; no bare Write/Edit, no general shell.
        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Bash", tools)
        self.assertIn(f"Write(/{Path('/tmp/run').resolve()}/**)", tools)
        self.assertIn("Bash(git -C * diff *)", tools)  # multi-repo reviewers diff each repo by path
        self.assertIn("--strict-mcp-config", args)
        self.assertEqual(harnesses.ClaudeAdapter.permission_args("full"), ["--dangerously-skip-permissions"])

    def test_antigravity_read_profile(self):
        run = str(Path("/run").resolve())
        args = harnesses.AntigravityAdapter.permission_args("read", ["/run"])
        # The skip flag would also approve sandbox bypasses (antigravity-cli#36), so never both.
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertIn("--sandbox", args)
        self.assertEqual(args[args.index("--add-dir") + 1], run)
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertEqual(settings["toolPermission"], "proceed-in-sandbox")
        self.assertIs(settings["allowNonWorkspaceAccess"], False)
        self.assertIn(f"write_file({run})", settings["permissions"]["allow"])
        self.assertIn("read_url(*)", settings["permissions"]["deny"])
        self.assertEqual(harnesses.AntigravityAdapter.permission_args("full"), ["--dangerously-skip-permissions"])

    def test_a_harness_without_the_read_only_flags_is_refused(self):
        import roles
        from pairs import parse_pair
        cat = {"harnesses": {"antigravity": {"binary": "agy", "version": "0.1", "read_sandbox": False,
                                             "models": [{"model": "g", "efforts": ["high"]}]}}}
        self.assertIn("lacks the flags", roles.unknown_why(cat, parse_pair("antigravity:g@high")))
        cat["harnesses"]["antigravity"]["read_sandbox"] = True
        self.assertIsNone(roles.unknown_why(cat, parse_pair("antigravity:g@high")))

    def test_denied_agy_run_is_a_stopped_worker(self):
        env = harnesses.AntigravityAdapter.extract_envelope(
            {"status": "SUCCESS", "response": "", "denied_actions": [{"action": "command"}]}, 0, "agy")
        self.assertFalse(env["ok"])
        self.assertIn("deny (command)", env["detail"])


class Envelopes(unittest.TestCase):
    def test_long_stream_without_result_is_linear(self):
        line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 2000}]}})
        raw = "\n".join([line] * 4000)
        start = time.monotonic()
        self.assertIsNone(common._parse_json_envelope(raw, harnesses.ClaudeAdapter))
        self.assertLess(time.monotonic() - start, 3)

    def test_envelope_after_banner(self):
        env = {"type": "result", "subtype": "success", "result": "ok", "session_id": "s", "is_error": False}
        raw = "banner line\n" + json.dumps(env, indent=2)
        self.assertEqual(common._parse_json_envelope(raw, harnesses.ClaudeAdapter)["result"], "ok")

    def test_stats(self):
        self.assertEqual(common.envelope_stats({"total_cost_usd": 0.1234, "num_turns": 7, "duration_ms": 61000}),
                         {"cost": "$0.12", "turns": 7, "model_time": "61s"})


class Costs(unittest.TestCase):
    def test_summary_by_role_and_pair(self):
        import costs
        d = Path(tempfile.mkdtemp())
        log = d / "progress.log"
        log.write_text("\n".join([
            "10:00:00 [worker +5s] 01-implement: done (succeeded) pair=claude:haiku@default cost=$0.10 turns=3",
            "10:00:01 [worker +4s] 01-s1-1: done (succeeded) pair=claude:haiku@default cost=$0.05 turns=2",
            "10:00:02 [worker +9s] director-start-1: done (succeeded) pair=claude:sonnet@medium cost=$0.30 turns=6",
            "10:00:03 [worker +2s] probe-x-0: done (succeeded) pair=antigravity:g@high turns=1 tokens=18004",
            "10:00:04 [progress] 01 merged abc",
        ]) + "\n")
        text = costs.summarize(log)
        self.assertIn("Total: $0.45 across 4 worker runs (1 reported no cost); 18,004 tokens where reported.", text)
        self.assertIn("| Flight Director | $0.30 | 0 | 1 | 6 |", text)
        self.assertIn("| Crew | $0.10 | 0 | 1 | 3 |", text)
        self.assertIn("| claude:haiku@default | $0.15 | 0 | 2 | 5 |", text)
        self.assertIn("| antigravity:g@high | $0.00 | 18,004 | 1 | 1 |", text)


class TurnStats(unittest.TestCase):
    def test_resumed_agy_turn_reports_only_its_share(self):
        # Live run 2026-11-23: first turn 185176 tokens / 379s; the resumed turn reported 199092 / 427s, turns=2.
        d = Path(tempfile.mkdtemp())
        st = d / "state.json"
        st.write_text(json.dumps({"RUN_DIR": str(d)}))
        runner = common.AgyRunner(Run(st))
        first = {"turns": 1, "model_time": "379s", "tokens": 185176}
        runner._turn_stats(harnesses.AntigravityAdapter, "c-1", False, first)
        self.assertEqual(first["tokens"], 185176)
        second = {"turns": 2, "model_time": "427s", "tokens": 199092}
        runner._turn_stats(harnesses.AntigravityAdapter, "c-1", True, second)
        self.assertEqual(second, {"turns": 1, "model_time": "48s", "tokens": 13916})
        claude = {"turns": 2, "model_time": "4s", "tokens": 900}
        runner._turn_stats(harnesses.ClaudeAdapter, "c-1", True, claude)  # Claude reports per invocation
        self.assertEqual(claude["tokens"], 900)

    def test_token_only_summary(self):
        import costs
        log = Path(tempfile.mkdtemp()) / "progress.log"
        log.write_text("1 [worker +5s] 01-implement: done (succeeded) pair=antigravity:g@high turns=1 tokens=40000\n")
        text = costs.summarize(log)
        self.assertIn("Total: 40,000 tokens across 1 worker runs (the harness reports tokens, not cost).", text)
        self.assertNotIn("$", text)
        self.assertIn("| Crew | 40,000 | 1 | 1 |", text)


class AntigravityProgress(unittest.TestCase):
    def test_real_stream_capture(self):
        # Captured from agy 2026-11-23 (paths anonymised): text arrives in pieces, a failed read is ERROR.
        acc, lines, env = {}, [], None
        for raw in (ROOT / "tests" / "fixtures" / "harness" / "agy_stream.ndjson").read_text().splitlines():
            summary, e = harnesses.AntigravityAdapter.parse_stream_event(json.loads(raw), acc)
            lines += [summary] if summary else []
            env = e or env
        self.assertEqual(lines, [
            "init",
            "says: I am going to list the workspace directory, read `notes.txt`, and attempt to read `missing.txt`.",
            "tool: run_command (ls)",
            "tool: read_file (/home/u/repo/notes.txt)",
            "tool: read_file (/home/u/repo/missing.txt)",
            "tool error: read_file: file does not exist",
            'says: [notes.txt](file:///home/u/repo/notes.txt) contains two lines: "hello" and "world".',
        ])
        self.assertEqual(env["status"], "SUCCESS")
        self.assertEqual(common.envelope_stats(env), {"turns": 1, "model_time": "7s", "tokens": 18004})


class AskMarker(unittest.TestCase):
    def test_ask_none_is_not_a_question(self):
        # Live run 2026-11-23: a reviewer began with "ASK: none. The review is written ..." after a denied tool.
        for rest in (" none. The review is written", " n/a", " No questions.", " nothing to ask"):
            self.assertTrue(common._NO_QUESTION.match(rest), rest)
        for rest in (" Should D-2 apply to stdin?", " Is none of the files tested?"):
            self.assertFalse(common._NO_QUESTION.match(rest), rest)


class ClaudeProgress(unittest.TestCase):
    def parse(self, ev):
        return harnesses.ClaudeAdapter.parse_stream_event(ev)[0]

    def test_summaries(self):
        said = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Running the tests now."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}}
        self.assertEqual(self.parse(said), "says: Running the tests now. | tool: Bash (pytest -q)")
        todo = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "TodoWrite", "input": {
            "todos": [{"content": "a", "status": "completed"}, {"content": "b", "activeForm": "Writing b",
                                                                 "status": "in_progress"}]}}]}}
        self.assertEqual(self.parse(todo), "todo 1/2 done, now: Writing b")
        err = {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                                                         "content": [{"type": "text", "text": "No such file"}]}]}}
        self.assertEqual(self.parse(err), "tool error: No such file")


if __name__ == "__main__":
    unittest.main()
