"""gate.py and save_message.py: numbered output files, never overwriting, and baseline skips."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
GATE = SKILL / "scripts" / "gate.py"
SAVE = SKILL / "scripts" / "save_message.py"
FAKE = SKILL / "tests" / "fake_orca.py"


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def py(script, *args, env=None):
    r = subprocess.run([sys.executable, "-I", str(script), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    return r.returncode, r.stdout.strip()


class GateTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="gate-"))
        self.wt = self.d / "wt"
        self.wt.mkdir()
        sh("git", "-C", str(self.wt), "init", "-q", "-b", "main")
        (self.wt / "README.md").write_text("x\n")
        sh("git", "-C", str(self.wt), "add", ".")
        sh("git", "-C", str(self.wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        self.head = sh("git", "-C", str(self.wt), "rev-parse", "HEAD")
        self.rv = self.d / "run-values.txt"
        self.rv.write_text("VERIFY_1=test -e add.py\nVERIFY_2=true\n")

    def gate(self, *extra, log=None):
        return py(GATE, "--run-values", str(self.rv), "--worktree", str(self.wt), "--head", self.head,
                  "--log", log or str(self.d / "gates" / "baseline-{n}.log"), "--prefix", "VERIFY", *extra)

    def test_log_placeholder_takes_the_next_free_number(self):
        (self.d / "gates").mkdir()
        (self.d / "gates" / "baseline-1.log").write_text("earlier run\n")
        rc, out = self.gate()
        self.assertEqual(rc, 1)
        self.assertIn(f"log={self.d / 'gates' / 'baseline-2.log'}", out)
        self.assertEqual((self.d / "gates" / "baseline-1.log").read_text(), "earlier run\n")

    def test_skip_leaves_out_named_checks_and_logs_them(self):
        rc, out = self.gate("--skip", "VERIFY_1")
        self.assertEqual(rc, 0, out)
        log = (self.d / "gates" / "baseline-1.log").read_text()
        self.assertIn("=== VERIFY_1 skipped (BASELINE_SKIP)", log)
        self.assertIn("=== VERIFY_2 exit=0", log)

    def test_skip_of_an_unknown_check_is_bad_usage(self):
        rc, out = self.gate("--skip", "VERIFY_9")
        self.assertEqual(rc, 64)
        self.assertIn("UNKNOWN_SKIP VERIFY_9", out)

    def test_empty_skip_skips_nothing(self):
        self.assertEqual(self.gate("--skip", "")[0], 1)


class SaveMessageTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="save-"))
        self.world = self.d / "world.json"
        msg = {"id": "msg_1", "type": "worker_done", "sequence": 1, "subject": "done",
               "body": "RESULT: NOTED\nsummary", "payload": json.dumps({"dispatchId": "ctx_1", "outcome": "succeeded"})}
        self.world.write_text(json.dumps({"seq": 1, "messages": [msg], "tasks": {}, "dispatches": {},
                                          "behaviors": [], "calls": []}))
        self.orca = self.d / "orca"
        self.orca.write_text(f'#!/bin/sh\nexec {sys.executable} -I {FAKE} "$@"\n')
        self.orca.chmod(0o755)
        self.results = self.d / "results"

    def save(self, out, *extra):
        return py(SAVE, "--orca", str(self.orca), "--run", "run_fake", "--out", out,
                  "--dispatch", "ctx_1", "--type", "worker_done", *extra, env={"FAKE_ORCA_WORLD": str(self.world)})

    def test_placeholder_takes_the_next_free_number(self):
        self.results.mkdir()
        (self.results / "architect-1.md").write_text("first job\n")
        rc, out = self.save(str(self.results / "architect-{n}.md"))
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith(f"SAVED {self.results / 'architect-2.md'} "))
        self.assertEqual((self.results / "architect-1.md").read_text(), "first job\n")

    def test_existing_file_is_never_overwritten(self):
        self.results.mkdir()
        (self.results / "architect-1.md").write_text("first job\n")
        rc, out = self.save(str(self.results / "architect-1.md"))
        self.assertEqual((rc, out), (3, f"EXISTS {self.results / 'architect-1.md'}"))
        self.assertEqual((self.results / "architect-1.md").read_text(), "first job\n")

    def test_append_still_adds_to_an_existing_file(self):
        self.results.mkdir()
        (self.results / "log.md").write_text("first\n")
        self.assertEqual(self.save(str(self.results / "log.md"), "--append")[0], 0)
        self.assertEqual((self.results / "log.md").read_text(), "first\nRESULT: NOTED\nsummary\n")


if __name__ == "__main__":
    unittest.main()
