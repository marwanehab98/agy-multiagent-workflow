"""install.sh: releases a committed version into a skills folder, with tests, backups and rollback."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="install-"))
        self.target = self.d / "skills" / "orca-multiagent-workflow"
        self.backups = self.d / "backups"
        self.head = sh("git", "-C", str(REPO), "rev-parse", "HEAD").stdout.strip()

    def install(self, *extra):
        r = sh("bash", str(INSTALL), "--target", str(self.target), "--backups", str(self.backups),
               "--skip-tests", *extra)
        return r.returncode, (r.stdout + r.stderr).strip()

    def test_installs_runtime_files_and_version_only(self):
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.target / "SKILL.md").is_file())
        self.assertTrue((self.target / "scripts" / "run_task.py").is_file())
        self.assertTrue((self.target / "references" / "coordinator.md").is_file())
        self.assertFalse((self.target / "tests").exists())
        self.assertFalse((self.target / "install.sh").exists())
        self.assertFalse((self.target / ".git").exists())
        self.assertIn(self.head, (self.target / "VERSION").read_text())
        self.assertIn(f"INSTALLED {self.head[:12]}", out)

    def test_second_install_keeps_the_previous_one_as_a_backup(self):
        self.install()
        (self.target / "marker").write_text("old\n")
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        kept = list(self.backups.iterdir())
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "marker").is_file())
        self.assertFalse((self.target / "marker").exists())

    def test_an_older_ref_can_be_installed_for_rollback(self):
        older = sh("git", "-C", str(REPO), "rev-parse", "HEAD~1").stdout.strip()
        rc, out = self.install("--ref", "HEAD~1")
        self.assertEqual(rc, 0, out)
        self.assertIn(older, (self.target / "VERSION").read_text())

    def test_backups_are_capped_at_five(self):
        for _ in range(7):
            self.install()
        self.assertEqual(len(list(self.backups.iterdir())), 5)

    def test_unknown_ref_is_refused_and_nothing_changes(self):
        self.install()
        before = (self.target / "VERSION").read_text()
        rc, out = self.install("--ref", "no-such-ref")
        self.assertNotEqual(rc, 0)
        self.assertEqual((self.target / "VERSION").read_text(), before)


if __name__ == "__main__":
    unittest.main()
