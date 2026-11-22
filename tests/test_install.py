"""install.sh: releases a committed version into a skills folder, with tests, backups and rollback."""
import os
import shutil
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
        self.target = self.d / "skills" / "mission-control"
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
        self.assertTrue((self.target / "skills" / "mission" / "SKILL.md").is_file())
        self.assertTrue((self.target / "skills" / "launch-plan" / "SKILL.md").is_file())
        self.assertTrue((self.target / "skills" / "draft-spec" / "SKILL.md").is_file())
        self.assertTrue((self.target / "skills" / "gauntlet" / "SKILL.md").is_file())
        self.assertTrue((self.target / "agents" / "zoom_out.md").is_file())
        self.assertTrue((self.target / "resources" / "document_standards" / "feature-spec-standard.md").is_file())
        self.assertTrue((self.target / "scripts" / "run_task.py").is_file())
        self.assertTrue((self.target / "references" / "capsule-communicator.md").is_file())
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
        self.assertTrue(
            (self.target / "SKILL.md").is_file()
            or (self.target / "skills" / "mission" / "SKILL.md").is_file()
            or (self.target / "skills" / "mission-control" / "SKILL.md").is_file()
        )

    def _legacy_skill_commit(self):
        return sh("git", "-C", str(REPO), "log", "-n", "1", "--format=%H", "--diff-filter=AM", "--", "SKILL.md").stdout.strip()

    def test_legacy_pre_plugin_rollback_works_with_target_and_harness_modes(self):
        legacy = self._legacy_skill_commit()
        if not legacy:
            self.skipTest("shallow checkout lacks a pre-plugin commit with root SKILL.md")

        # --target rollback to legacy single-skill layout
        rc, out = self.install("--ref", legacy)
        self.assertEqual(rc, 0, out)
        self.assertIn(legacy, (self.target / "VERSION").read_text())
        self.assertTrue((self.target / "SKILL.md").is_file())

        # --harness antigravity rollback to legacy single-skill layout preserves backup, cleans managed siblings, and keeps custom skills
        rc, out = self.harness_install("--harness", "antigravity")
        self.assertEqual(rc, 0, out)
        skills_root = self.d / "home" / ".gemini" / "config" / "skills"
        modern_dest = skills_root / "mission"
        (modern_dest / "marker").write_text("new\n")
        custom = skills_root / "my-custom-skill"
        custom.mkdir()
        (custom / "SKILL.md").write_text("---\nname: my-custom-skill\n---\n")
        rc, out = self.harness_install("--ref", legacy, "--harness", "antigravity")
        self.assertEqual(rc, 0, out)
        legacy_dest = skills_root / "mission-control"
        self.assertIn(legacy, (legacy_dest / "VERSION").read_text())
        self.assertTrue((legacy_dest / "SKILL.md").is_file())
        self.assertFalse(modern_dest.exists())
        self.assertFalse((legacy_dest / ".managed-skills").exists())
        self.assertTrue((custom / "SKILL.md").is_file())
        kept = list((self.backups / "antigravity").iterdir())
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "mission" / "marker").is_file())
        for name in (
            "launch-plan", "draft-spec", "flight-plan", "red-green",
            "gatecheck", "touchdown",
            "crucible", "gauntlet", "crossfire", "spec-drift",
        ):
            self.assertFalse((skills_root / name).exists(), name)
            self.assertTrue((kept[0] / name / "SKILL.md").is_file(), name)

    def test_backups_are_capped_at_five(self):
        for _ in range(7):
            self.install()
        self.assertEqual(len(list((self.backups / 'custom').iterdir())), 5)

    def test_empty_existing_backup_dirs_do_not_abort_fresh_install(self):
        (self.backups / "custom").mkdir(parents=True)
        (self.backups / "antigravity").mkdir(parents=True)

        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        self.assertIn("INSTALLED", out)

        rc, out = self.harness_install("--harness", "antigravity")
        self.assertEqual(rc, 0, out)
        self.assertIn("INSTALLED", out)

    def test_cp_a_failure_falls_back_to_cp_r(self):
        real_cp = shutil.which("cp")
        fake_bin = self.d / "fake-bin"
        fake_bin.mkdir()
        cp_shim = fake_bin / "cp"
        # Simulate virtiofs/FUSE: `cp -a` copies partially then fails on chmod/utimensat
        cp_shim.write_text(
            "#!/usr/bin/env bash\n"
            f'if [ "${{1:-}}" = "-a" ]; then\n'
            f'  "{real_cp}" -R "${{@:2}}" 2>/dev/null || true\n'
            '  echo "cp: preserving times: Operation not permitted" >&2\n'
            '  exit 1\n'
            'fi\n'
            f'exec "{real_cp}" "$@"\n',
            encoding="utf-8",
        )
        cp_shim.chmod(0o755)
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}

        r1 = subprocess.run(
            ["bash", str(INSTALL), "--target", str(self.target), "--backups", str(self.backups), "--skip-tests"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertIn("INSTALLED", r1.stdout)
        self.assertTrue((self.target / "skills" / "mission" / "SKILL.md").is_file())
        self.assertFalse((self.target / "release").exists(), "fallback must not nest release/ inside target")

        r2 = subprocess.run(
            ["bash", str(INSTALL), "--home", str(self.d / "home"), "--harness-fixtures", str(FIXTURES),
             "--backups", str(self.backups), "--skip-tests", "--harness", "antigravity"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("INSTALLED", r2.stdout)
        skills_root = self.d / "home" / ".gemini" / "config" / "skills"
        self.assertTrue((skills_root / "mission" / "SKILL.md").is_file())
        self.assertTrue((skills_root / "gauntlet" / "SKILL.md").is_file())
        self.assertFalse((skills_root / "gauntlet" / "gauntlet").exists(), "fallback must not nest skill dir")

    def harness_install(self, *extra):
        r = sh("bash", str(INSTALL), "--home", str(self.d / "home"), "--harness-fixtures", str(FIXTURES),
               "--backups", str(self.backups), "--skip-tests", *extra)
        return r.returncode, (r.stdout + r.stderr).strip()

    def test_harness_option_installs_into_antigravity_skills_folder(self):
        rc, out = self.harness_install("--harness", "antigravity")
        self.assertEqual(rc, 0, out)
        skills_root = self.d / "home" / ".gemini" / "config" / "skills"
        dest = skills_root / "mission"
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertTrue((dest / "scripts" / "run_task.py").is_file())
        self.assertTrue((dest / "references" / "capsule-communicator.md").is_file())
        self.assertTrue((dest / "agents" / "zoom_out.md").is_file())
        for name in (
            "launch-plan", "draft-spec", "flight-plan", "red-green",
            "gatecheck", "touchdown",
            "crucible", "gauntlet", "crossfire", "spec-drift",
        ):
            self.assertTrue((skills_root / name / "SKILL.md").is_file(), name)

    def test_no_option_updates_existing_antigravity_install(self):
        self.harness_install("--harness", "antigravity")
        skills_root = self.d / "home" / ".gemini" / "config" / "skills"
        (skills_root / "draft-spec" / "marker").write_text("old\n")
        rc, out = self.harness_install()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("INSTALLED"), 1)
        self.assertIn(".gemini/config/skills/mission", out)
        kept = list((self.backups / "antigravity").iterdir())
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / "draft-spec" / "marker").is_file())
        self.assertFalse((skills_root / "draft-spec" / "marker").exists())

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

    def test_harness_option_installs_into_claude_skills_folder(self):
        rc, out = self.harness_install("--harness", "claude")
        self.assertEqual(rc, 0, out)
        skills_root = self.d / "home" / ".claude" / "skills"
        dest = skills_root / "mission"
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertTrue((dest / "scripts" / "run_task.py").is_file())
        self.assertTrue((dest / "references" / "capsule-communicator.md").is_file())
        self.assertTrue((dest / "agents" / "zoom_out.md").is_file())
        self.assertTrue((dest / ".managed-skills").is_file())
        for name in (
            "launch-plan", "draft-spec", "flight-plan", "red-green",
            "gatecheck", "touchdown",
            "crucible", "gauntlet", "crossfire", "spec-drift",
        ):
            self.assertTrue((skills_root / name / "SKILL.md").is_file(), name)

    def test_no_option_updates_both_antigravity_and_claude_when_both_installed(self):
        self.assertEqual(self.harness_install("--harness", "antigravity")[0], 0)
        self.assertEqual(self.harness_install("--harness", "claude")[0], 0)
        agy_root = self.d / "home" / ".gemini" / "config" / "skills"
        claude_root = self.d / "home" / ".claude" / "skills"
        (agy_root / "draft-spec" / "marker").write_text("old-agy\n")
        (claude_root / "draft-spec" / "marker").write_text("old-claude\n")
        rc, out = self.harness_install()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("INSTALLED"), 2)
        self.assertIn(".gemini/config/skills/mission", out)
        self.assertIn(".claude/skills/mission", out)
        agy_backups = list((self.backups / "antigravity").iterdir())
        claude_backups = list((self.backups / "claude").iterdir())
        self.assertEqual(len(agy_backups), 1)
        self.assertEqual(len(claude_backups), 1)
        self.assertTrue((agy_backups[0] / "draft-spec" / "marker").is_file())
        self.assertTrue((claude_backups[0] / "draft-spec" / "marker").is_file())
        self.assertFalse((agy_root / "draft-spec" / "marker").exists())
        self.assertFalse((claude_root / "draft-spec" / "marker").exists())

    def test_target_install_includes_claude_plugin_manifest_when_committed(self):
        # Verify install.sh's --target bundle copy includes .claude-plugin/plugin.json from a committed ref.
        mini = self.d / "mini-repo"
        mini.mkdir()
        shutil.copy2(INSTALL, mini / "install.sh")
        (mini / "plugin.json").write_text((REPO / "plugin.json").read_text(encoding="utf-8"))
        (mini / ".claude-plugin").mkdir()
        (mini / ".claude-plugin" / "plugin.json").write_text((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        (mini / "skills" / "mission").mkdir(parents=True)
        (mini / "skills" / "mission" / "SKILL.md").write_text("---\nname: mission\ndescription: x\n---\n")
        g = lambda *a: sh("git", "-C", str(mini), "-c", "user.email=t@t", "-c", "user.name=t", *a)
        g("init", "-q", "-b", "main")
        g("add", ".")
        g("commit", "-qm", "init")
        target2 = self.d / "target2"
        r = sh("bash", str(mini / "install.sh"), "--target", str(target2), "--backups", str(self.backups), "--skip-tests")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((target2 / ".claude-plugin" / "plugin.json").is_file())
        self.assertTrue((target2 / "plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
