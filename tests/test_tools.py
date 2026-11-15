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

    def test_skip_names_of_the_other_prefix_are_fine(self):
        self.rv.write_text("VERIFY_1=true\nGATE_1=test -e add.py\n")
        rc, out = py(GATE, "--run-values", str(self.rv), "--worktree", str(self.wt), "--head", self.head,
                     "--log", str(self.d / "g-{n}.log"), "--skip", "VERIFY_1 GATE_1")
        self.assertEqual(rc, 0, out)
        self.assertIn("GATE_1 skipped", out)

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


MERGE_LOCAL = SKILL / "scripts" / "merge-local.sh"


class MergeLocalTests(unittest.TestCase):
    """The user's "merge locally" choice at the end of a run: fast-forward only, never losing their work."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="mergelocal-"))
        self.main = self.d / "main"
        self.main.mkdir()
        g = self.g
        g("init", "-q", "-b", "main")
        (self.main / "README.md").write_text("x\n")
        g("add", ".")
        g("commit", "-qm", "init")
        g("branch", "feature")
        g("worktree", "add", "-q", str(self.d / "impl"), "feature")
        (self.d / "impl" / "a.txt").write_text("a\n")
        sh("git", "-C", str(self.d / "impl"), "add", ".")
        sh("git", "-C", str(self.d / "impl"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a")
        self.tip = sh("git", "-C", str(self.main), "rev-parse", "feature")

    def g(self, *a):
        return sh("git", "-C", str(self.main), "-c", "user.email=t@t", "-c", "user.name=t", *a)

    def merge(self):
        r = subprocess.run(["bash", str(MERGE_LOCAL), str(self.main), "main", "feature"], capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def test_checked_out_and_clean_fast_forwards_the_checkout(self):
        rc, out = self.merge()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out, f"MERGED main={self.tip}")
        self.assertEqual(self.g("rev-parse", "HEAD"), self.tip)
        self.assertTrue((self.main / "a.txt").is_file())

    def test_uncommitted_changes_in_the_checkout_refuse(self):
        (self.main / "README.md").write_text("edited\n")
        rc, out = self.merge()
        self.assertEqual(rc, 2)
        self.assertTrue(out.startswith("CHECKOUT_DIRTY"), out)
        self.assertIn("README.md", out)
        self.assertNotEqual(self.g("rev-parse", "main"), self.tip)

    def test_untracked_files_alone_do_not_block(self):
        (self.main / "notes.txt").write_text("mine\n")
        rc, out = self.merge()
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.main / "notes.txt").read_text(), "mine\n")

    def test_target_not_checked_out_moves_by_compare_and_swap(self):
        self.g("switch", "-q", "-c", "other")
        rc, out = self.merge()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.g("rev-parse", "main"), self.tip)
        self.assertEqual(self.g("rev-parse", "--abbrev-ref", "HEAD"), "other")

    def test_diverged_target_refuses(self):
        (self.main / "b.txt").write_text("b\n")
        self.g("add", ".")
        self.g("commit", "-qm", "b on main")
        rc, out = self.merge()
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("NOT_FAST_FORWARD"), out)


if __name__ == "__main__":
    unittest.main()
