"""Tests for self-contained multi-skill Antigravity plugin packaging."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_SKILLS = {
    "mission",
    "launch-plan",
    "draft-spec",
    "flight-plan",
    "red-green",
    "gatecheck",
    "touchdown",
    "crucible",
    "gauntlet",
    "crossfire",
    "spec-drift",
}

EXPECTED_AGENTS = {
    "codebase_details.md",
    "comprehension.md",
    "design_critic.md",
    "implementation_readiness.md",
    "inventive_catalyst.md",
    "structural.md",
    "style.md",
    "synthesizer.md",
    "zoom_out.md",
}

EXPECTED_STANDARDS = {
    "feature-spec-standard.md",
    "heilmeier-standard.md",
    "implementation-readiness-standard.md",
    "re-implementation-standard.md",
    "zoom-out-standard.md",
}

FORBIDDEN_REFS = re.compile(
    r"(superpowers:|/egm-draft\b|/egm-resolve\b|docs/codex\.md|docs/claude\.md|docs/harnesses\.md|CLAUDE_PLUGIN_ROOT|visual-companion\.md)"
)


class PluginBundleTests(unittest.TestCase):
    def test_manifest_and_notices_exist(self):
        manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "mission-control")
        self.assertTrue((ROOT / manifest["logo"]).is_file())
        self.assertTrue((ROOT / "THIRD_PARTY_NOTICES.md").is_file())
        self.assertFalse((ROOT / "SKILL.md").exists(), "root SKILL.md should live under skills/mission/")

    def test_claude_plugin_manifest_matches_root_manifest(self):
        root_manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        claude_path = ROOT / ".claude-plugin" / "plugin.json"
        self.assertTrue(claude_path.is_file(), ".claude-plugin/plugin.json missing")
        claude_manifest = json.loads(claude_path.read_text(encoding="utf-8"))
        self.assertEqual(claude_manifest["name"], "mission-control")
        for key in ("version", "license", "repository", "author"):
            self.assertEqual(claude_manifest[key], root_manifest[key], key)

    def test_claude_marketplace_manifest_matches_plugin_manifest(self):
        claude_manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        mp_path = ROOT / ".claude-plugin" / "marketplace.json"
        self.assertTrue(mp_path.is_file(), ".claude-plugin/marketplace.json missing")
        mp = json.loads(mp_path.read_text(encoding="utf-8"))
        self.assertEqual(mp["name"], "mission-control")
        self.assertTrue(mp.get("description"))
        self.assertEqual(mp["owner"]["name"], claude_manifest["author"]["name"])
        self.assertEqual(len(mp["plugins"]), 1)
        entry = mp["plugins"][0]
        self.assertEqual(entry["name"], claude_manifest["name"])
        self.assertEqual(entry["source"], "./")
        self.assertTrue(entry.get("description"))
        self.assertNotIn("version", entry, "omit version on entry so plugin.json stays the single source of truth")

    def test_all_expected_skills_and_frontmatter(self):
        skills_dir = ROOT / "skills"
        found = {p.name for p in skills_dir.iterdir() if p.is_dir()}
        self.assertEqual(found, EXPECTED_SKILLS)
        for name in sorted(EXPECTED_SKILLS):
            skill_md = skills_dir / name / "SKILL.md"
            self.assertTrue(skill_md.is_file(), name)
            text = skill_md.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), name)
            fm = text.split("\n---\n", 1)[0]
            self.assertIn(f"name: {name}", fm)
            self.assertIn("description:", fm)
            self.assertLessEqual(len(text.encode("utf-8")), 24000, f"{name}/SKILL.md exceeds agy 24KB cap")

    def test_vendored_agents_and_standards_exist(self):
        agents = {p.name for p in (ROOT / "agents").iterdir() if p.is_file()}
        self.assertEqual(agents, EXPECTED_AGENTS)
        standards = {p.name for p in (ROOT / "resources" / "document_standards").iterdir() if p.is_file()}
        self.assertEqual(standards, EXPECTED_STANDARDS)
        self.assertTrue((ROOT / "resources" / "execution_instructions.md").is_file())

    def test_no_dangling_external_skill_or_doc_references(self):
        hits = []
        for folder in ("skills", "references", "scripts", "agents", "resources"):
            for p in sorted((ROOT / folder).rglob("*")):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if FORBIDDEN_REFS.search(line):
                        hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
        self.assertEqual(hits, [], "\n".join(hits))

    @unittest.skipUnless(shutil.which("agy"), "agy CLI not installed")
    def test_agy_plugin_validate_strict(self):
        r = subprocess.run(["agy", "plugin", "validate", "--strict", "--json", str(ROOT)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["components"]["skills"], len(EXPECTED_SKILLS))
        self.assertEqual(data["components"]["agents"], len(EXPECTED_AGENTS))
        self.assertEqual(data["findings"], [])

    @unittest.skipUnless(shutil.which("claude"), "claude CLI not installed")
    def test_claude_plugin_validate(self):
        r = subprocess.run(["claude", "plugin", "validate", str(ROOT)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
