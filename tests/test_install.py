"""install.sh: releases a committed version into a skills folder, with tests, backups and rollback."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"
FIXTURES = REPO / "tests" / "fixtures" / "harness"


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})


@unittest.skipUnless((REPO / ".git").exists(), "install.sh releases from a git checkout; skipped in install.sh's own clean copy")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="install-"))
        self.target = self.d / "skills" / "agy-multiagent-workflow"
        self.backups = self.d / "backups"
        self.head = sh("git", "-C", str(REPO), "rev-parse", "HEAD").stdout.strip()

    def install(self, *extra):
        r = sh("bash", str(INSTALL), "--target", str(self.target), "--backups", str(self.backups),
               "--skip-tests", *extra)
        return r.returncode, (r.stdout + r.stderr).strip()

    def test_installs_runtime_files_and_version_only(self):
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.target / "plugin.json").is_file())
        self.assertTrue((self.target / "THIRD_PARTY_NOTICES.md").is_file())
        self.assertTrue((self.target / "skills" / "agy-multiagent-workflow" / "SKILL.md").is_file())
        self.assertTrue((self.target / "skills" / "execute-plan" / "SKILL.md").is_file())
        self.assertTrue((self.target / "skills" / "brainstorming" / "SKILL.md").is_file())
        self.assertTrue((self.target / "skills" / "egm-review" / "SKILL.md").is_file())
        self.assertTrue((self.target / "agents" / "zoom_out.md").is_file())
        self.assertTrue((self.target / "resources" / "document_standards" / "feature-spec-standard.md").is_file())
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
        kept = list((self.backups / 'custom').iterdir())
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
        self.assertEqual(len(list((self.backups / 'custom').iterdir())), 5)

    def harness_install(self, *extra):
        r = sh("bash", str(INSTALL), "--home", str(self.d / "home"), "--harness-fixtures", str(FIXTURES),
               "--backups", str(self.backups), "--skip-tests", *extra)
        return r.returncode, (r.stdout + r.stderr).strip()

    def test_harness_option_installs_into_antigravity_skills_folder(self):
        rc, out = self.harness_install("--harness", "antigravity")
        self.assertEqual(rc, 0, out)
        skills_root = self.d / "home" / ".gemini" / "config" / "skills"
        dest = skills_root / "agy-multiagent-workflow"
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertTrue((dest / "scripts" / "run_task.py").is_file())
        self.assertTrue((dest / "references" / "coordinator.md").is_file())
        self.assertTrue((dest / "agents" / "zoom_out.md").is_file())
        for name in (
            "execute-plan", "brainstorming", "writing-plans", "test-driven-development",
            "verification-before-completion", "finishing-a-development-branch",
            "egm-brainstorm", "egm-review", "egm-review-loop", "egm-drift-report",
        ):
            self.assertTrue((skills_root / name / "SKILL.md").is_file(), name)

    def test_no_option_updates_existing_antigravity_install(self):
        self.harness_install("--harness", "antigravity")
        skills_root = self.d / "home" / ".gemini" / "config" / "skills"
        (skills_root / "brainstorming" / "marker").write_text("old\n")
        rc, out = self.harness_install()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("INSTALLED"), 1)
        self.assertIn(".gemini/config/skills/agy-multiagent-workflow", out)
        kept = list((self.backups / "antigravity").iterdir())
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "brainstorming" / "marker").is_file())
        self.assertFalse((skills_root / "brainstorming" / "marker").exists())

    def test_no_option_and_no_existing_install_asks_for_a_harness(self):
        rc, out = self.harness_install()
        self.assertEqual(rc, 1)
        self.assertIn("--harness", out)

    def test_unknown_harness_is_refused(self):
        rc, out = self.harness_install("--harness", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("unknown harness nope", out)

    def test_unknown_ref_is_refused_and_nothing_changes(self):
        self.install()
        before = (self.target / "VERSION").read_text()
        rc, out = self.install("--ref", "no-such-ref")
        self.assertNotEqual(rc, 0)
        self.assertEqual((self.target / "VERSION").read_text(), before)


if __name__ == "__main__":
    unittest.main()
