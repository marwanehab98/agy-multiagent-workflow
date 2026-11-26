"""Tests for scripts/run_director.py against fake_agy.py. Run: bash tests/run_all.sh"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import Base, SKILL

SCRIPT = SKILL / "scripts" / "run_director.py"


class RunDirectorTests(Base):
    def setUp(self):
        super().setUp()
        self.spec = self.tmp / "director-spec.txt"
        self.spec.write_text("Start job.\n")

    def director(self, *args):
        r = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            capture_output=True, text=True,
            env={**os.environ, "FAKE_AGY_WORLD": str(self.world)},
        )
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines, f"no output; stderr={r.stderr}")
        return r.returncode, lines[-1]

    def test_start_relay_reply_and_result(self):
        relay = self.run_dir / "relay-001.md"
        relay.write_text("Pick an approach.\n")
        self.behave(
            {"do": "response", "text": f"RESULT: RELAY {relay}\nWaiting on user.", "conv_id": "dir_1"},
            {"do": "response", "text": "RESULT: COMMITTED\nDone.", "conv_id": "dir_1"},
        )
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 11)
        self.assertTrue(last.startswith(f"RELAY {relay} "), last)
        self.assertEqual(self.st("DIRECTOR_CONVERSATION_ID"), "dir_1")
        self.assertEqual(self.st("DIRECTOR_KEEP_OPEN"), "yes")

        reply = self.tmp / "001-from-user.md"
        reply.write_text("Approach A\n")
        rc, last = self.director("reply", "--state", str(self.state), "--file", str(reply),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 0)
        self.assertTrue(last.startswith("RESULT "), last)
        saved = Path(last.split()[1])
        self.assertIn("RESULT: COMMITTED", saved.read_text())
        self.assertEqual(self.calls()[1]["conversation"], "dir_1")
        self.assertIn("Approach A", self.calls()[1]["prompt"])
        # Every turn is tagged as the Flight Director's, in its log name and so in the cost report.
        logs = sorted(p.name for p in (self.run_dir / "logs").glob("*.log"))
        self.assertTrue(all(n.startswith("director-") for n in logs), logs)
        self.assertIn("director-reply-1.log", logs)

    def test_notify_and_notify_ack(self):
        notify = self.run_dir / "notify-001.md"
        notify.write_text("Starting review.\n")
        self.behave(
            {"do": "response", "text": f"RESULT: NOTIFY {notify}\n", "conv_id": "dir_2"},
            {"do": "response", "text": "RESULT: NEED preflight-check design.md antigravity:small@high\n", "conv_id": "dir_2"},
        )
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 12)
        self.assertTrue(last.startswith(f"NOTIFY {notify} "), last)

        rc, last = self.director("notify-ack", "--state", str(self.state), "--pair", "antigravity:big@high")
        self.assertEqual(rc, 0)
        self.assertIn("SHOWN.", self.calls()[1]["prompt"])
        self.assertEqual(self.calls()[1]["conversation"], "dir_2")

    def test_fresh_job_clears_conversation_id_when_settled(self):
        self.behave({"do": "response", "text": "RESULT: META_FIXED\n", "conv_id": "dir_3"})
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 0)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))

    def test_a_kept_open_director_whose_silent_conversation_cannot_be_resumed_is_lost(self):
        s = self.st()
        s["WORKER_IDLE_TIMEOUT"] = "1"
        self.state.write_text(json.dumps(s))
        init = {"event": {"event": "init", "conversation_id": "dir_s", "init": {}}}
        self.behave({"do": "script", "conv_id": "dir_s", "steps": [init, {"sleep": 60}]})
        w = json.loads(self.world.read_text())
        w["expired_conversations"] = ["dir_s"]  # the resume finds the conversation gone
        self.world.write_text(json.dumps(w))
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 13)
        self.assertTrue(last.startswith("LOST "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))

    def test_expired_conversation_returns_lost_13(self):
        self.behave({"do": "response", "text": "RESULT: COMMITTED\n", "conv_id": "dir_dead"})
        self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                      "--pair", "antigravity:big@high", "--keep-open")
        w = json.loads(self.world.read_text())
        w["expired_conversations"] = ["dir_dead"]
        self.world.write_text(json.dumps(w))
        rc, last = self.director("send", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 13)
        self.assertTrue(last.startswith("LOST "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))

    def test_usage_limit_returns_31(self):
        self.behave("usage")
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 31)
        self.assertTrue(last.startswith("USAGE_LIMIT "), last)

    def test_missing_result_line_returns_failed_1(self):
        self.behave({"do": "response", "text": "Just prose without a result line.\n"})
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 1)
        self.assertTrue(last.startswith("FAILED "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))

    def test_a_result_line_written_last_is_read_and_saved_first(self):
        # Both replies a sonnet@low Flight Director gave to its start job on 2026-11-26 had this shape.
        relay = self.run_dir / "relay" / "001-to-user.md"
        relay.parent.mkdir(parents=True, exist_ok=True)
        relay.write_text("Do you sign off?\n")
        text = ("I've written the short design for the two counting functions, and I'm now sending it to you for "
                f"sign-off.\n\nRESULT: RELAY {relay}\n")
        self.behave({"do": "response", "text": text, "conv_id": "dir_last"})
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 11, last)
        self.assertTrue(last.startswith(f"RELAY {relay} "), last)
        saved = Path(last.split()[-1]).read_text()
        self.assertEqual(saved.splitlines()[0], f"RESULT: RELAY {relay}")
        self.assertIn("I've written the short design for the two counting functions", saved)
        self.assertEqual((self.st("DIRECTOR_CONVERSATION_ID"), self.st("OPEN_RELAY")), ("dir_last", str(relay)))
        self.assertIn("flight director wrote its RESULT line last", (self.run_dir / "mission-log.md").read_text())

    def test_a_result_line_that_is_neither_first_nor_last_is_still_a_failure(self):
        for text in ("I did it.\n\nRESULT: COMMITTED\n\nAnd then some more.\n",
                     "Thinking.\n\nRESULT: COMMITTED\n\nRESULT: PLAN_APPROVED /x\n",
                     "Not done yet.\n\nThe job says to give `RESULT: COMMITTED` when it is.\n",
                     "The job says to end with `RESULT: COMMITTED` but I am not done.\n"):
            self.behave({"do": "response", "text": text})
            rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                     "--pair", "antigravity:big@high")
            self.assertEqual(rc, 1, text)
            self.assertTrue(last.startswith("FAILED "), last)

    def test_missing_relay_target_file_returns_failed_1(self):
        missing = self.run_dir / "does-not-exist.md"
        self.behave({"do": "response", "text": f"RESULT: RELAY {missing}\n", "conv_id": "dir_missing"})
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 1)
        self.assertTrue(last.startswith("FAILED "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))
        self.assertIn("does not exist", Path(last.split()[1]).read_text())

    def test_missing_notify_target_file_returns_failed_1(self):
        missing = self.run_dir / "missing-notify.md"
        self.behave({"do": "response", "text": f"RESULT: NOTIFY {missing}\n", "conv_id": "dir_notify"})
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 1)
        self.assertTrue(last.startswith("FAILED "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))
        self.assertIn("does not exist", Path(last.split()[1]).read_text())

    def test_stopped_on_fresh_job_clears_stale_conversation_id(self):
        s = self.st()
        s["DIRECTOR_CONVERSATION_ID"] = "stale_conv"
        self.state.write_text(json.dumps(s))
        self.behave("stopped")
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 1)
        self.assertTrue(last.startswith("FAILED "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))

    def test_relay_without_conversation_id_returns_failed_1(self):
        relay = self.run_dir / "relay-empty.md"
        relay.write_text("Pick.\n")
        # Use raw_stdout so fake_agy emits empty conversation_id instead of conv_seq
        self.behave({"do": "response", "raw_stdout": json.dumps({
            "conversation_id": "", "status": "SUCCESS", "response": f"RESULT: RELAY {relay}\n", "error": ""
        }), "rc": 0})
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 1)
        self.assertTrue(last.startswith("FAILED "), last)
        self.assertIn("requires a conversation_id", Path(last.split()[1]).read_text())

    def test_send_silent_new_conversation_returns_lost_13(self):
        self.behave(
            {"do": "response", "text": "RESULT: RESUMED\n", "conv_id": "dir_orig"},
            {"do": "response", "text": "RESULT: OK\n", "conv_id": "dir_fresh"},
        )
        rc, _ = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                              "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 0)
        self.assertEqual(self.st("DIRECTOR_CONVERSATION_ID"), "dir_orig")
        rc, last = self.director("send", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 13)
        self.assertTrue(last.startswith("LOST "), last)
        self.assertIsNone(self.st("DIRECTOR_CONVERSATION_ID"))
        self.assertIsNone(self.st("LAST_DIRECTOR_RESULT"))

    def test_records_last_director_result_and_open_relay_and_clears_on_failure(self):
        relay = self.run_dir / "relay-state.md"
        relay.write_text("Confirm plan?\n")
        self.behave(
            {"do": "response", "text": f"RESULT: RELAY {relay}\n", "conv_id": "dir_r1"},
            {"do": "response", "text": "RESULT: COMMITTED\n", "conv_id": "dir_r1"},
            {"do": "response", "text": "No result header here.\n", "conv_id": "dir_r1"},
        )
        rc, last = self.director("start", "--state", str(self.state), "--spec", str(self.spec),
                                 "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 11)
        self.assertEqual(self.st("OPEN_RELAY"), str(relay))
        self.assertEqual(self.st("LAST_DIRECTOR_RESULT"), last)

        reply = self.tmp / "reply-state.md"
        reply.write_text("Yes\n")
        rc, last = self.director("reply", "--state", str(self.state), "--file", str(reply),
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 0)
        self.assertEqual(self.st("LAST_DIRECTOR_RESULT"), last)

        rc, _ = self.director("send", "--state", str(self.state), "--spec", str(self.spec),
                              "--pair", "antigravity:big@high")
        self.assertEqual(rc, 1)
        self.assertIsNone(self.st("LAST_DIRECTOR_RESULT"))


class JobSpecs(Base):
    """start/send --job --input: the script fills director-start.txt / director-job.txt itself."""
    director = RunDirectorTests.director
    RUN_VALUES = {"TOPIC": "demo", "DATE": "2026-11-24", "REQUIREMENTS_SOURCE": "written", "JIRA_KEY": "none",
                  "REQUIREMENTS_FILE": "none", "MISSION_NOTES": "none", "SAFETY_REQUESTED": "no",
                  "ROLE_CREW": "antigravity:g@high", "REPOS": "api web", "DEFAULT_BRANCH": "api=main web=trunk",
                  "BASE_BRANCH": "api=main web=release 2", "START_SHA": "api=abc web=def",
                  "START_HOW": "api=local web=origin", "RELAY_DIR": "/r/relay", "SKILL_DIR": "/old/plugin/1.2.0"}

    def setUp(self):
        super().setUp()
        s = self.st()
        s.update(self.RUN_VALUES)
        self.state.write_text(json.dumps(s))
        self.spec = self.tmp / "director-spec.txt"
        self.spec.write_text("Start job.\n")

    def test_start_fills_the_start_template_from_the_state(self):
        self.behave({"do": "response", "text": "RESULT: RESUMED\n", "conv_id": "d1"})
        rc, last = self.director("start", "--state", str(self.state), "--job", "start",
                                 "--input", "a file with spaces.md", "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 0, last)
        spec = self.run_dir / "specs" / "director-start-1.txt"
        text = spec.read_text()
        self.assertEqual(self.calls()[0]["prompt"], text)
        for line in ("JOB: start", "Input: a file with spaces.md", "BASE_BRANCH=api=main web=release 2",
                     "REPOS=api web", f"SKILL_DIR={SKILL}", f"DOCS_WORKTREE={self.docs_wt}"):
            self.assertIn(line, text)
        self.assertNotIn("/old/plugin", text)  # SKILL_DIR is where this script runs from, never the stale state
        self.assertEqual((self.st("LAST_JOB"), self.st("LAST_JOB_INPUT"), self.st("WAITING_ON")),
                         ("start", "a file with spaces.md", "director"))
        self.assertEqual(self.st("LAST_DIRECTOR_SPEC"), str(spec))
        self.assertEqual(self.st("DIRECTOR_CONVERSATION_ID"), "d1")  # --keep-open still applies
        self.assertTrue((self.run_dir / "logs" / "director-start-1-1.log").is_file())

    def test_send_fills_the_job_template(self):
        self.behave({"do": "response", "text": "RESULT: RESUMED\n", "conv_id": "d1"},
                    {"do": "response", "text": "RESULT: ANSWER /x\n", "conv_id": "d1"})
        self.director("start", "--state", str(self.state), "--spec", str(self.spec), "--pair", "antigravity:big@high",
                      "--keep-open")
        rc, last = self.director("send", "--state", str(self.state), "--job", "safety-done",
                                 "--input", "failed: /r/results/safety 2.md", "--pair", "antigravity:big@high")
        self.assertEqual(rc, 0, last)
        text = (self.run_dir / "specs" / "director-safety-done-1.txt").read_text()
        self.assertIn("Flight Director job: safety-done", text)
        self.assertIn(f"{SKILL}/references/flight-director.md", text)
        self.assertIn("Input: failed: /r/results/safety 2.md", text)
        self.assertEqual(self.calls()[1]["conversation"], "d1")
        self.assertEqual((self.st("LAST_JOB"), self.st("LAST_JOB_INPUT")),
                         ("safety-done", "failed: /r/results/safety 2.md"))

    def test_a_fallback_start_keeps_the_lost_job(self):
        s = self.st()
        s.update(LAST_JOB="consult", LAST_JOB_INPUT="/r/asks/01-impl-1.md")
        self.state.write_text(json.dumps(s))
        self.behave({"do": "response", "text": "RESULT: RESUMED\n", "conv_id": "d2"})
        rc, _ = self.director("start", "--state", str(self.state), "--job", "resume", "--input", "none",
                              "--pair", "antigravity:big@high", "--keep-open")
        self.assertEqual(rc, 0)
        self.assertEqual((self.st("LAST_JOB"), self.st("LAST_JOB_INPUT")), ("consult", "/r/asks/01-impl-1.md"))
        self.assertIn("JOB: resume", (self.run_dir / "specs" / "director-resume-1.txt").read_text())

    def test_single_repo_values_fill_the_template(self):
        s = self.st()
        s.update(REPOS="none", DEFAULT_BRANCH="main", BASE_BRANCH="main", START_SHA=self.base, START_HOW="local")
        self.state.write_text(json.dumps(s))
        self.behave({"do": "response", "text": "RESULT: META_FIXED\n"})
        rc, last = self.director("start", "--state", str(self.state), "--job", "fix-meta", "--input", "/r/v.txt",
                                 "--pair", "antigravity:big@high")
        self.assertEqual(rc, 0, last)
        self.assertIn("REPOS=none", (self.run_dir / "specs" / "director-fix-meta-1.txt").read_text())

    def test_a_missing_run_value_is_one_error_line(self):
        s = self.st()
        del s["START_HOW"]
        self.state.write_text(json.dumps(s))
        rc, last = self.director("start", "--state", str(self.state), "--job", "start", "--input", "none",
                                 "--pair", "antigravity:big@high")
        self.assertEqual((rc, last), (2, "ERROR MISSING_STATE START_HOW"))
        self.assertEqual(self.calls(), [])

    def test_bad_usage(self):
        for args in (["--job", "start"], ["--input", "x"], ["--job", "start", "--input", "x", "--spec", "f"], [],
                     ["--job", "../x", "--input", "x"]):
            with self.subTest(args=args):
                rc, last = self.director("start", "--state", str(self.state), "--pair", "antigravity:big@high", *args)
                self.assertEqual(rc, 2 if "../x" in args else 64, last)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
