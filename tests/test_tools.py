"""gate.py and merge-local.sh: numbered output files, never overwriting, baseline skips, and local merge."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
GATE = SKILL / "scripts" / "gate.py"
MERGE_LOCAL = SKILL / "scripts" / "merge-local.sh"


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
        self.rv = self.d / "flight-manifest.txt"
        self.rv.write_text("VERIFY_1=test -e add.py\nVERIFY_2=true\n")

    def gate(self, *extra, log=None):
        return py(GATE, "--flight-manifest", str(self.rv), "--worktree", str(self.wt), "--head", self.head,
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
        rc, out = py(GATE, "--flight-manifest", str(self.rv), "--worktree", str(self.wt), "--head", self.head,
                     "--log", str(self.d / "g-{n}.log"), "--skip", "VERIFY_1 GATE_1")
        self.assertEqual(rc, 0, out)
        self.assertIn("GATE_1 skipped", out)

    def test_empty_skip_skips_nothing(self):
        self.assertEqual(self.gate("--skip", "")[0], 1)

    def test_dirty_worktree_refused_without_discarding(self):
        (self.wt / "uncommitted.txt").write_text("keep me\n")
        rc, out = self.gate()
        self.assertEqual(rc, 2)
        self.assertIn("TREE_DIRTY", out)
        self.assertEqual((self.wt / "uncommitted.txt").read_text(), "keep me\n")

    def test_non_utf8_command_output_is_replaced(self):
        self.rv.write_text(f"VERIFY_1={sys.executable} -c \"import sys; sys.stdout.buffer.write(b'hi \\\\xff\\\\xfe ok')\"\n")
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)
        log = (self.d / "gates" / "baseline-1.log").read_text()
        self.assertIn("\ufffd", log)

    def test_nested_git_repo_created_by_command_is_cleaned(self):
        self.rv.write_text("VERIFY_1=mkdir -p sub && git -C sub init -q\n")
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.wt / "sub").exists())

    def test_gate_timeout_is_logged_and_fails(self):
        self.rv.write_text(f"VERIFY_1={sys.executable} -c \"import time; time.sleep(5)\"\n")
        rc, out = py(GATE, "--flight-manifest", str(self.rv), "--worktree", str(self.wt), "--head", self.head,
                     "--log", str(self.d / "gates" / "baseline-{n}.log"), "--prefix", "VERIFY",
                     env={"GATE_TIMEOUT": "1"})
        self.assertEqual(rc, 1, out)
        self.assertIn("VERIFY_1 timed out", out)
        log = (self.d / "gates" / "baseline-1.log").read_text()
        self.assertIn("timed out after 1s", log)

    def test_gate_timeout_kills_child_process_holding_stdout(self):
        import time as _time
        child_cmd = (
            f"{sys.executable} -c \"import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
            "time.sleep(10)\""
        )
        self.rv.write_text(f"VERIFY_1={child_cmd}\n")
        t0 = _time.monotonic()
        rc, out = py(GATE, "--flight-manifest", str(self.rv), "--worktree", str(self.wt), "--head", self.head,
                     "--log", str(self.d / "gates" / "baseline-{n}.log"), "--prefix", "VERIFY",
                     env={"GATE_TIMEOUT": "1"})
        elapsed = _time.monotonic() - t0
        self.assertEqual(rc, 1, out)
        self.assertLess(elapsed, 4.0)
        self.assertIn("VERIFY_1 timed out", out)


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
