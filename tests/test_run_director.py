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

    def test_notify_and_notify_ack(self):
        notify = self.run_dir / "notify-001.md"
        notify.write_text("Starting review.\n")
        self.behave(
            {"do": "response", "text": f"RESULT: NOTIFY {notify}\n", "conv_id": "dir_2"},
            {"do": "response", "text": "RESULT: NEED builder-check design.md antigravity:small@high\n", "conv_id": "dir_2"},
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


if __name__ == "__main__":
    unittest.main()
