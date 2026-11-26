"""Unit tests for scripts/common.py and the harness permission profiles: RESULT-line parsing, state merging,
the driver lock, worker environments, envelope parsing and stream progress summaries."""
import json
import os
import re
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
from helpers import removed_after  # noqa: E402


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
            ("- RESULT: COMMITTED", ["RESULT:", "COMMITTED"]),
            ("> **RESULT: NOTED**", ["RESULT:", "NOTED"]),
            ("\n\n  RESULT: META_FIXED\nSecond line.", ["RESULT:", "META_FIXED"]),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(result_words(text), want)

    def test_a_sentence_that_mentions_a_result_line_is_not_one(self):
        for text in ("The job says to end with `RESULT: COMMITTED` but I am not done.",
                     "I will send RESULT: PLAN_APPROVED /x once the user agrees.",
                     "Final answer: RESULT: COMMITTED",
                     "Just prose.", ""):
            with self.subTest(text=text):
                self.assertNotEqual(result_words(text)[:1], ["RESULT:"])

    def test_rest_path_joins_unquoted_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="mc words ") as d:
            f = Path(d) / "q a.md"
            f.write_text("x")
            words = result_words(f"RESULT: RELAY {f}")
            self.assertGreater(len(words), 3)
            self.assertEqual(rest_path(words, 2), str(f))
            self.assertEqual(rest_path(["RESULT:", "RELAY", "/nope"], 2), "/nope")


class UsageLimitText(unittest.TestCase):
    def test_provider_usage_messages_match(self):
        for text in ("Quota reached for model X", "You have reached your daily quota", "out of quota",
                     "quota limit", "over quota", "got 429 from the API", "429: rate limited",
                     "Error: 429", '{"error": {"code": 429}}', "quota exceeded", "exceeded your current quota",
                     "429 Too Many Requests", "usageLimitReached"):
            with self.subTest(text=text):
                self.assertTrue(common.USAGE_RE.search(text))

    def test_ordinary_mentions_do_not_match(self):
        for text in ('"input_tokens": 429', "the function returns 429 items", "Checking the quota code.",
                     "the quota field in config"):
            with self.subTest(text=text):
                self.assertIsNone(common.USAGE_RE.search(text))


class StateAndLock(unittest.TestCase):
    def setUp(self):
        self.d = Path(removed_after(self, tempfile.mkdtemp()))
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

    def test_convention_paths_default_to_none(self):
        # CONVENTION_PATHS is optional in the flight manifest, in a single-repo run as in a multi-repo one.
        self.assertEqual(Run(self.state).repo_convention_paths(), "none")
        Run(self.state).set(CONVENTION_PATHS="CONTRIBUTING.md")
        self.assertEqual(Run(self.state).repo_convention_paths(), "CONTRIBUTING.md")

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

    def test_an_earlier_listing_kills_only_what_still_matches_it(self):
        # What a worker started, listed while it ran, is stopped after the worker exited; a pid now in another
        # process group (reused since the listing) is left alone.
        gone = subprocess.Popen(["true"], start_new_session=True)
        gone.wait()
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            common._kill_tree(gone.pid, earlier=[(proc.pid, proc.pid + 100000)], alive=False)
            time.sleep(0.1)
            self.assertIsNone(proc.poll())
            common._kill_tree(gone.pid, earlier=[(proc.pid, proc.pid)], alive=False)
            self.assertEqual(proc.wait(timeout=5), -9)
        finally:
            proc.kill()
            proc.wait()


class WorkerPermissions(unittest.TestCase):
    ENV = {"PATH": "/bin", "HOME": "/h", "GITHUB_TOKEN": "x", "AWS_SECRET_ACCESS_KEY": "x", "AWS_PROFILE": "p",
           "ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t", "GEMINI_API_KEY": "g", "MY_PASSWORD": "x",
           "WORKER_ENV_KEEP": "AWS_PROFILE", "MAX_THINKING_TOKENS": "8000", "MAX_MCP_OUTPUT_TOKENS": "9000",
           "MY_TOKENS": "x"}

    def test_read_workers_lose_secrets_but_keep_harness_sign_in(self):
        env = common.worker_env("read", self.ENV)
        # The two harness limits named *TOKENS* are settings, not secrets; the pattern still drops MY_TOKENS.
        self.assertEqual(sorted(env), sorted(["PATH", "HOME", "AWS_PROFILE", "ANTHROPIC_API_KEY",
                                              "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "WORKER_ENV_KEEP",
                                              "MAX_THINKING_TOKENS", "MAX_MCP_OUTPUT_TOKENS"]))
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
        repo = str(Path("/tmp/repo").resolve())
        args = harnesses.ClaudeAdapter.permission_args("read", ["/tmp/run"], [repo])
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        tools = args[args.index("--allowedTools") + 1].split(",")
        # Writes only under the given folders; no bare Write/Edit, no general shell.
        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Bash", tools)
        self.assertIn(f"Write(/{Path('/tmp/run').resolve()}/**)", tools)
        # `git -C` only on the worker's repos: a wildcard path let other subcommands through (live, 2.1.331).
        self.assertIn(f"Bash(git -C {repo} diff *)", tools)
        self.assertFalse([t for t in tools if t.startswith("Bash(git -C *")], tools)
        self.assertFalse([t for t in tools if "grep" in t and t.startswith("Bash(")], tools)
        # Options that write files or run programs are denied, one comma-joined value like --allowedTools.
        deny = args[args.index("--disallowedTools") + 1].split(",")
        for rule in ("Bash(git -c *)", "Bash(git -C * -c *)", "Bash(git *--config-env*)", "Bash(git * --out*)",
                     "Bash(git * --ext*)", "Bash(git * --textc*)", "Bash(git * -O*)", "Bash(git * --op*)"):
            self.assertIn(rule, deny)
        self.assertIn("--strict-mcp-config", args)
        # Crew: full access with no MCP servers; the flight director (mcp=True) keeps the user's servers.
        self.assertEqual(harnesses.ClaudeAdapter.permission_args("full"),
                         ["--dangerously-skip-permissions", "--strict-mcp-config"])
        self.assertEqual(harnesses.ClaudeAdapter.permission_args("full", ["/tmp/run"], [repo]),
                         ["--dangerously-skip-permissions", "--strict-mcp-config"])
        self.assertEqual(harnesses.ClaudeAdapter.permission_args("full", mcp=True), ["--dangerously-skip-permissions"])
        self.assertEqual(harnesses.ClaudeAdapter.permission_args("read", ["/tmp/run"], [repo], mcp=True), args)

    def test_claude_read_profile_names_repo_folders_relative_to_the_worker(self):
        # A multi-repo final reviewer runs in the folder holding the repo worktrees and is told `git -C <repo>`.
        root = Path(removed_after(self, tempfile.mkdtemp())).resolve()
        outside = Path(removed_after(self, tempfile.mkdtemp())).resolve() / "docs-api"
        dirs = [str(root), str(root / "api"), str(root / "web"), str(outside)]
        args = harnesses.ClaudeAdapter.permission_args("read", [], dirs, workdir=str(root))
        tools = args[args.index("--allowedTools") + 1].split(",")
        got = [t for t in tools if t.startswith("Bash(git -C ") and t.endswith(" diff *)")]
        self.assertEqual(got, [f"Bash(git -C {q} diff *)" for p in (
            root, ".", root / "api", "api", "./api", root / "web", "web", "./web", outside)
            for q in (p, f'"{p}"', f"'{p}'")])
        # Each rule is a fixed path followed by one allowed subcommand: no wildcard before the subcommand.
        self.assertFalse([t for t in tools if t.startswith("Bash(git -C") and "*" in t[:-3]], tools)
        # A relative git dir is read from the worker's folder, never from this process's cwd.
        rel = harnesses.ClaudeAdapter.permission_args("read", [], ["api"], workdir=str(root))
        self.assertIn(f"Bash(git -C {root / 'api'} log *)", rel[rel.index("--allowedTools") + 1].split(","))
        self.assertEqual(args[args.index("--disallowedTools") + 1],
                         ",".join(harnesses.ClaudeAdapter.READ_ONLY_DENY))  # the deny list does not change

    def test_claude_read_profile_quotes_paths_that_need_it(self):
        # Rules match the command's literal text (live, 2.1.331): a path with a space is allowed in the quoted
        # spellings a worker types, never bare; a path no rule can hold safely gets no rule at all.
        root = Path("/srv/my repos").resolve()
        dirs = [str(root / "a b"), "/srv/c,d", "/srv/x(y", "/srv/st*r", "/srv/it's \"q\"", "/srv/$HOME x"]
        args = harnesses.ClaudeAdapter.permission_args("read", ["/srv/out"], dirs, workdir=str(root))
        value = args[args.index("--allowedTools") + 1]
        got = re.findall(r"Bash\(git -C ((?:(?!Bash\().)*?) diff \*\)", value)
        cd = str(Path("/srv/c,d").resolve())
        self.assertEqual(got, [f'"{root}/a b"', f"'{root}/a b'", '"a b"', "'a b'", '"./a b"', "'./a b'",
                               cd, f'"{cd}"', f"'{cd}'", f"'{Path('/srv/$HOME x').resolve()}'"])
        # An output folder a rule cannot name safely is refused, not left out.
        for bad in ("/srv/out)", "/srv/o*t", "/srv/o\nt"):
            with self.assertRaises(ValueError):
                harnesses.ClaudeAdapter.permission_args("read", [bad], [])

    def test_claude_read_profile_allows_quoted_plain_paths(self):
        # Rules match the command as typed (live, 2.1.331): `git -C "/repo" diff` needs a rule with the quotes
        # even though the path needs none. Bare first, no duplicates, nothing for an unsafe path.
        self.assertEqual(harnesses._shell_spellings("/srv/repo"), ["/srv/repo", '"/srv/repo"', "'/srv/repo'"])
        self.assertEqual(harnesses._shell_spellings("."), [".", '"."', "'.'"])
        self.assertEqual(harnesses._shell_spellings("/srv/a(b"), [])
        self.assertEqual(harnesses._shell_spellings("/srv/a b"), ['"/srv/a b"', "'/srv/a b'"])
        root = Path(removed_after(self, tempfile.mkdtemp())).resolve()
        args = harnesses.ClaudeAdapter.permission_args("read", [str(root)], [str(root), str(root)], workdir=str(root))
        tools = args[args.index("--allowedTools") + 1].split(",")
        self.assertEqual(len(tools), len(set(tools)))
        for q in (str(root), f'"{root}"', f"'{root}'", ".", '"."', "'.'"):
            self.assertIn(f"Bash(git -C {q} log *)", tools)

    def test_claude_read_profile_stays_far_below_the_argument_limit(self):
        # A four-repo run: the worker's worktree plus an impl and a docs worktree per repo, every one under the
        # run's folder (relative spellings too). Linux caps one argument at 128 KiB (MAX_ARG_STRLEN).
        base = "/home/someone/.local/state/mission-control/worktrees/project-0123abcd-PROJ-1234"
        impl = f"{base}-impl"
        dirs = [impl] + [f"{impl}/repos/service-name-{i}" for i in range(4)] + \
            [f"{base}-docs/repos/service-name-{i}" for i in range(4)]
        args = harnesses.ClaudeAdapter.permission_args("read", [f"{base}-docs/run"], dirs, workdir=impl)
        value = args[args.index("--allowedTools") + 1]
        rules = value.split(",")
        self.assertEqual(len(rules), len(set(rules)))
        # 399 rules, 32,412 bytes here (2026-11-24): a quarter of the limit, with room for longer paths.
        self.assertLess(len(value.encode()), 128 * 1024 // 3, (len(rules), len(value.encode())))

    def test_antigravity_read_profile_refuses_an_output_folder_a_rule_cannot_hold(self):
        for bad in ("/srv/out)", "/srv/o*t", "/srv/o?t", "/srv/o[t]", "/srv/o\\t", "/srv/o\nt"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                harnesses.AntigravityAdapter.permission_args("read", [bad])
        self.assertTrue(harnesses.AntigravityAdapter.permission_args("read", ["/srv/a b,c"]))

    def test_antigravity_read_profile(self):
        run = str(Path("/run").resolve())
        args = harnesses.AntigravityAdapter.permission_args("read", ["/run"])
        # The skip flag would also approve sandbox bypasses (antigravity-cli#36), so never both.
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertIn("--sandbox", args)
        # No --add-dir: it moved the shell into RUN_DIR (live, 2026-11-24); the write_file rule lets the file tool
        # write the output.
        self.assertEqual(args[:2], ["--sandbox", "--settings"])
        self.assertEqual(len(args), 3)
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertEqual(settings["toolPermission"], "proceed-in-sandbox")
        self.assertIs(settings["allowNonWorkspaceAccess"], False)
        self.assertIn(f"write_file({run})", settings["permissions"]["allow"])
        self.assertIn("read_url(*)", settings["permissions"]["deny"])
        self.assertIn("mcp(*)", settings["permissions"]["deny"])
        # Crew: a session-only rule denies every MCP tool call; the flight director (mcp=True) keeps them.
        self.assertEqual(harnesses.AntigravityAdapter.permission_args("full"),
                         ["--dangerously-skip-permissions", "--settings", '{"permissions":{"deny":["mcp(*)"]}}'])
        self.assertEqual(harnesses.AntigravityAdapter.permission_args("full", mcp=True),
                         ["--dangerously-skip-permissions"])
        self.assertEqual(harnesses.AntigravityAdapter.permission_args("read", ["/run"], mcp=True), args)

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

    def test_stats_of_several_results_of_one_process(self):
        # A Claude worker that waited on a background command: one result per turn segment. Turns, time and usage
        # are per segment; total_cost_usd is the running total (live 2026-11-24), so it is not added up.
        envs = [{"total_cost_usd": 0.0037, "num_turns": 2, "duration_ms": 6338,
                 "usage": {"input_tokens": 4, "cache_creation_input_tokens": 14802, "cache_read_input_tokens": 64258,
                           "output_tokens": 128}},
                {"total_cost_usd": 0.0242, "num_turns": 1, "duration_ms": 3970,
                 "usage": {"input_tokens": 4, "cache_creation_input_tokens": 440, "cache_read_input_tokens": 39694,
                           "output_tokens": 6}}]
        self.assertEqual(common.envelope_stats(envs),
                         {"cost": "$0.02", "turns": 3, "model_time": "10s", "tokens": 79192 + 40144})
        self.assertEqual(common.envelope_stats([]), {})


class AtomicWrites(unittest.TestCase):
    def test_a_failed_write_leaves_no_temp_file(self):
        d = Path(removed_after(self, tempfile.mkdtemp()))
        target = d / "f.txt"
        target.write_text("old")
        target.chmod(0o644)
        common.write_atomic(target, "new")
        self.assertEqual((target.read_text(), target.stat().st_mode & 0o777), ("new", 0o644))
        with self.assertRaises(IsADirectoryError):
            common.write_atomic(d, "x")  # os.replace onto a directory fails
        with self.assertRaises(TypeError):
            common.write_atomic(target, None)  # the write itself fails
        self.assertEqual(sorted(p.name for p in d.parent.glob(f".{d.name}-*")) + sorted(x.name for x in d.iterdir()),
                         ["f.txt"])
        self.assertEqual(target.read_text(), "new")

    def test_watch_writes_through_its_own_temp_file(self):
        import watch
        d = Path(removed_after(self, tempfile.mkdtemp()))
        watch.write_json(d / "pos.json", {"a": 1})
        self.assertEqual(json.loads((d / "pos.json").read_text()), {"a": 1})
        with self.assertRaises(TypeError):
            watch.write_json(d / "pos.json", {"a": object()})
        self.assertEqual([x.name for x in d.iterdir()], ["pos.json"])



class LeadWithResult(unittest.TestCase):
    """common.lead_with_result: a flight director's RESULT line written last moves to line 1."""

    REAL = [  # the two replies run_director.py rejected in live runs on 2026-11-26, paths shortened
        "I've written and committed the short design. The sign-off request is ready, and I'm sending it to you "
        "now.\n\nRESULT: RELAY /run/relay/001-to-user.md\n",
        "I've written the short design for the two counting functions, and I'm now sending it to you for "
        "sign-off.\n\nRESULT: RELAY /run/relay/001-to-user.md\n",
    ]

    def test_the_two_rejected_live_replies_are_read(self):
        for text in self.REAL:
            fixed, moved = common.lead_with_result(text)
            self.assertTrue(moved)
            self.assertEqual(common.result_words(fixed), ["RESULT:", "RELAY", "/run/relay/001-to-user.md"])
            self.assertEqual(fixed.splitlines()[2], text.splitlines()[0])  # the sentence is kept, under the line

    def test_a_reply_that_starts_with_the_line_is_left_alone(self):
        for text in ("RESULT: COMMITTED\nDone.\n", "\n  RESULT: RELAY /r/1.md\n\nAsked the user.\n",
                     "**RESULT: META_FIXED**\n", "Just prose.\n", ""):
            self.assertEqual(common.lead_with_result(text), (text, False))

    def test_markup_around_a_last_line_is_allowed(self):
        fixed, moved = common.lead_with_result("All fixed.\n\n`RESULT: META_FIXED`\n\n")
        self.assertTrue(moved)
        self.assertEqual(common.result_words(fixed), ["RESULT:", "META_FIXED"])

    def test_a_first_line_that_only_mentions_the_line_does_not_block_a_real_last_one(self):
        text = "The job says to give `RESULT: COMMITTED` when done, and it is.\n\nRESULT: COMMITTED\n"
        fixed, moved = common.lead_with_result(text)
        self.assertTrue(moved)
        self.assertEqual(common.result_words(fixed), ["RESULT:", "COMMITTED"])

    def test_only_one_line_and_only_when_it_is_last(self):
        for text in ("Did it.\nRESULT: COMMITTED\nMore words.\n",                # in the middle
                     "Hm.\nRESULT: COMMITTED\nRESULT: PLAN_APPROVED /x\n",        # two lines
                     "Hm.\nRESULT: COMMITTED\n\nRESULT: COMMITTED\n",            # the same line twice
                     "I should end with `RESULT: COMMITTED` later.\n"):            # only quoted in a sentence
            self.assertEqual(common.lead_with_result(text), (text, False), text)


class Costs(unittest.TestCase):
    def test_summary_by_role_and_pair(self):
        import costs
        d = Path(removed_after(self, tempfile.mkdtemp()))
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
        # A harness that reports only tokens is n/a, not free (a run can move to one mid-run).
        self.assertIn("| antigravity:g@high | n/a | 18,004 | 1 | 1 |", text)
        self.assertIn("| Test launches | n/a | 18,004 | 1 | 1 |", text)

    def test_a_paused_call_counts_as_a_run(self):
        import costs
        d = Path(removed_after(self, tempfile.mkdtemp()))
        log = d / "progress.log"
        log.write_text("10:00:00 [worker +900s] 01-implement: paused: no activity for 900s; resuming its conversation "
                       "pair=claude:haiku@default\n"
                       "10:20:00 [worker +60s] 01-implement: done (succeeded) pair=claude:haiku@default cost=$0.40\n")
        self.assertIn("Total: $0.40 across 2 worker runs (1 reported no cost).", costs.summarize(log))


class TurnStats(unittest.TestCase):
    def test_resumed_agy_turn_reports_only_its_share(self):
        # Live run 2026-11-23: first turn 185176 tokens / 379s; the resumed turn reported 199092 / 427s, turns=2.
        d = Path(removed_after(self, tempfile.mkdtemp()))
        st = d / "state.json"
        st.write_text(json.dumps({"RUN_DIR": str(d)}))
        runner = common.WorkerRunner(Run(st))
        first = {"turns": 1, "model_time": "379s", "tokens": 185176}
        runner._turn_stats(harnesses.AntigravityAdapter, "c-1", False, first)
        self.assertEqual(first["tokens"], 185176)
        second = {"turns": 2, "model_time": "427s", "tokens": 199092}
        runner._turn_stats(harnesses.AntigravityAdapter, "c-1", True, second)
        self.assertEqual(second, {"turns": 1, "model_time": "48s", "tokens": 13916})
        claude = {"turns": 2, "model_time": "4s", "tokens": 900}
        runner._turn_stats(harnesses.ClaudeAdapter, "c-1", True, claude)  # Claude reports per invocation
        self.assertEqual(claude["tokens"], 900)
        # The running totals live beside the worker logs, not in state.json.
        self.assertFalse([k for k in json.loads(st.read_text()) if k.startswith("USAGE_SO_FAR")])
        self.assertEqual(json.loads((d / "logs" / "usage-so-far.json").read_text()), {"c-1": "199092,2,427"})

    def test_resumed_claude_turn_logs_only_its_share_of_the_cost(self):
        # Live 2026-11-24: one conversation, three calls, total_cost_usd 0.00378, 0.00695, 0.00736 (running total);
        # turns, time and tokens were each call's own.
        d = Path(removed_after(self, tempfile.mkdtemp()))
        st = d / "state.json"
        st.write_text(json.dumps({"RUN_DIR": str(d)}))
        runner = common.WorkerRunner(Run(st))
        record = d / "logs" / "usage-so-far.json"
        seen = []
        for resumed, cost in ((False, 3.32), (True, 7.32), (True, 11.83)):
            stats = {"cost": f"${cost:.2f}", "turns": 3, "model_time": "40s", "tokens": 5000}
            runner._turn_stats(harnesses.ClaudeAdapter, "s-1", resumed, stats, cost=cost)
            seen.append(stats)
        self.assertEqual([x["cost"] for x in seen], ["$3.32", "$4.00", "$4.51"])
        self.assertEqual({(x["turns"], x["model_time"], x["tokens"]) for x in seen}, {(3, "40s", 5000)})
        self.assertEqual(json.loads(record.read_text()), {"s-1": "5000,3,40,11.83"})
        # Full precision, not the two-decimal text: fractions of a cent still split.
        runner._turn_stats(harnesses.ClaudeAdapter, "s-2", False, {}, cost=0.00378)
        small = {"cost": "$0.01"}
        runner._turn_stats(harnesses.ClaudeAdapter, "s-2", True, small, cost=0.00695)
        self.assertEqual(small["cost"], "$0.00")
        self.assertEqual(json.loads(record.read_text())["s-2"], ",,,0.00695")
        # A new conversation, or one recorded before costs were (three fields), logs the cost as reported.
        fresh = {"cost": "$2.00"}
        runner._turn_stats(harnesses.ClaudeAdapter, "s-1", False, fresh, cost=2.0)
        self.assertEqual(fresh["cost"], "$2.00")
        record.write_text(json.dumps({"s-3": "900,2,4", "s-4": "1,1,1,junk"}))
        for cid in ("s-3", "s-4"):
            old = {"cost": "$1.50"}
            runner._turn_stats(harnesses.ClaudeAdapter, cid, True, old, cost=1.5)
            self.assertEqual(old["cost"], "$1.50")
        # No cost reported: nothing recorded, nothing changed.
        none = {"turns": 2}
        runner._turn_stats(harnesses.ClaudeAdapter, "s-5", True, none)
        self.assertEqual(none, {"turns": 2})
        self.assertNotIn("s-5", json.loads(record.read_text()))

    def test_agy_record_stays_three_fields_without_a_cost(self):
        d = Path(removed_after(self, tempfile.mkdtemp()))
        st = d / "state.json"
        st.write_text(json.dumps({"RUN_DIR": str(d)}))
        (d / "logs").mkdir()
        # A record a newer runner wrote with a cost field still splits Antigravity's tokens.
        (d / "logs" / "usage-so-far.json").write_text(json.dumps({"c-1": "185176,1,379,0.5"}))
        stats = {"turns": 2, "model_time": "427s", "tokens": 199092}
        common.WorkerRunner(Run(st))._turn_stats(harnesses.AntigravityAdapter, "c-1", True, stats)
        self.assertEqual(stats, {"turns": 1, "model_time": "48s", "tokens": 13916})
        self.assertEqual(json.loads((d / "logs" / "usage-so-far.json").read_text()), {"c-1": "199092,2,427"})

    def test_corrupt_running_totals_count_as_none(self):
        d = Path(removed_after(self, tempfile.mkdtemp()))
        st = d / "state.json"
        st.write_text(json.dumps({"RUN_DIR": str(d)}))
        (d / "logs").mkdir()
        (d / "logs" / "usage-so-far.json").write_text("{not json")
        stats = {"turns": 2, "model_time": "427s", "tokens": 199092}
        common.WorkerRunner(Run(st))._turn_stats(harnesses.AntigravityAdapter, "c-1", True, stats)
        self.assertEqual(stats["tokens"], 199092)
        self.assertEqual(json.loads((d / "logs" / "usage-so-far.json").read_text()), {"c-1": "199092,2,427"})

    def test_roles_of_final_fix_tasks_and_labelled_workers(self):
        import costs
        for tag, want in [("F1-implement", "Crew"), ("F1-fix-2", "Crew"), ("01a-attempt2", "Crew"),
                          ("F1-s1-1", "Task reviewers"), ("01-s2-3", "Task reviewers"),
                          ("director-start", "Flight Director"), ("director-reply", "Flight Director"),
                          ("director-notify-ack", "Flight Director"), ("director-fix-meta-1", "Flight Director"),
                          ("safety-1", "Range Safety and design reviews"),
                          ("lens-2", "Range Safety and design reviews"),
                          ("plan-review-1", "Range Safety and design reviews"),
                          ("preflight-check-1", "Range Safety and design reviews"),
                          ("final-review-2", "Final review"), ("drift-1", "Drift check"),
                          ("probe-claude_x_high-0", "Test launches"), ("claude-resume", "Resumed workers")]:
            with self.subTest(tag=tag):
                self.assertEqual(costs.role(tag), want)

    def test_director_turns_and_answered_workers_get_role_labels(self):
        calls = []

        class Runner:
            def try_resume(self, text, pair, wt, conv, **launch):
                calls.append(launch.get("label"))

            def run(self, text, **launch):
                calls.append(launch.get("label"))
                return ("done", "succeeded", "", "")

        spec = Path(removed_after(self, tempfile.mkdtemp())) / "F1-implement.txt"
        spec.write_text("do it\n")
        common.answer_worker(Runner(), "answer", "claude:x@high", "/wt", "c1", spec)
        self.assertEqual(calls, ["F1-implement", "F1-implement"])

    def test_token_only_summary(self):
        import costs
        log = Path(removed_after(self, tempfile.mkdtemp())) / "progress.log"
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
            "init conv=edceea8c-a944-44e7-aa19-50e8d50df5d2",
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

    def test_busy_and_background_signals(self):
        def acc_after(*events):
            acc = {}
            for ev in events:
                harnesses.ClaudeAdapter.parse_stream_event(ev, acc)
            return acc
        bg = lambda n: {"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "t"}] * n}
        self.assertEqual(acc_after(bg(2)), {"background": 2})
        self.assertEqual(acc_after(bg(1), bg(0)), {"background": 0})
        for ev in ({"type": "system", "subtype": "init"}, {"type": "assistant", "message": {"content": []}},
                   {"type": "user", "message": {"content": []}}):
            self.assertEqual(acc_after(ev), {"turn": True, "active": True})
        self.assertEqual(acc_after({"type": "system", "subtype": "init", "session_id": "s1"}),
                         {"turn": True, "active": True, "conversation_id": "s1"})
        self.assertEqual(acc_after({"type": "system", "subtype": "a_new_subtype"}), {"active": True})
        # Task events and events that also follow a result are not turn activity, and a result is none either.
        for ev in ({"type": "system", "subtype": "task_started"}, {"type": "system", "subtype": "task_updated"},
                   {"type": "system", "subtype": "task_progress"}, {"type": "system", "subtype": "task_notification"},
                   {"type": "system", "subtype": "task_summary"}, {"type": "system", "subtype": "post_turn_summary"},
                   {"type": "rate_limit_event"}, {"type": "system", "subtype": "hook_response"},
                   {"type": "result", "subtype": "success", "result": "x", "session_id": "s", "is_error": False}):
            self.assertEqual(acc_after(ev), {})

    def test_the_captured_claude_stream_marks_work_and_noise(self):
        # A live run (2.1.331, 2026-11-25): a 75 s foreground Bash call, a foreground subagent, a background command.
        active, quiet = set(), set()
        acc = {}
        for raw in (ROOT / "tests" / "fixtures" / "harness" / "claude_stream.ndjson").read_text().splitlines():
            ev = json.loads(raw)
            _, env = harnesses.ClaudeAdapter.parse_stream_event(ev, acc)
            kind = ev["type"] + ("/" + ev["subtype"] if ev.get("subtype") and ev["type"] == "system" else "")
            if env is None:
                (active if acc.pop("active", False) else quiet).add(kind)
            acc.pop("turn", None)
        self.assertEqual(active, {"assistant", "user", "system/init", "system/thinking_tokens"})
        self.assertEqual(quiet, {"system/hook_started", "system/hook_response", "system/task_started",
                                 "system/task_progress", "system/task_updated", "system/task_notification",
                                 "system/background_tasks_changed", "rate_limit_event", "tool_progress"})
        self.assertEqual(acc["conversation_id"], "s-capture-1")

    def test_antigravity_activity_signals(self):
        def acc_after(ev):
            acc = {}
            harnesses.AntigravityAdapter.parse_stream_event(ev, acc)
            return acc
        # The captured shape: the id at the top level of init, and inside every step_update.
        self.assertEqual(acc_after({"event": "init", "conversation_id": "c1", "init": {"cwd": "/x"}}),
                         {"active": True, "conversation_id": "c1"})
        step = {"conversation_id": "c2", "step_type": "tool", "tool_name": "x", "state": "ACTIVE"}
        self.assertEqual(acc_after({"event": "step_update", "step_update": step}),
                         {"active": True, "conversation_id": "c2"})
        self.assertEqual(acc_after({"event": "a_new_event"}), {"active": True})  # unknown events count as work
        self.assertEqual(acc_after({"error": {"code": 429}}), {})
        acc = {}
        harnesses.AntigravityAdapter.parse_stderr_line("root agent idle; waiting for 1 background task(s) to finish",
                                                       acc)
        self.assertEqual(acc, {"waiting": True})
        harnesses.AntigravityAdapter.parse_stream_event({"event": "step_update", "step_update": {}}, acc)
        self.assertEqual(acc.get("waiting"), True)  # a step printed before the waiting line may be read after it
        harnesses.AntigravityAdapter.parse_stream_event(
            {"event": "step_update", "step_update": {"step_type": "system_message"}}, acc)
        self.assertNotIn("waiting", acc)  # the step that wakes the agent
        self.assertEqual(harnesses.AntigravityAdapter.parse_stream_event({"event": {"odd": 1}}, {}), (None, None))

    def test_the_captured_agy_stream_names_its_conversation(self):
        acc = {}
        for raw in (ROOT / "tests" / "fixtures" / "harness" / "agy_stream.ndjson").read_text().splitlines():
            harnesses.AntigravityAdapter.parse_stream_event(json.loads(raw), acc)
        self.assertEqual(acc["conversation_id"], "edceea8c-a944-44e7-aa19-50e8d50df5d2")


if __name__ == "__main__":
    unittest.main()
