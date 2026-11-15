import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import harnesses  # noqa: E402
from harnesses import detect, launch_args, top_effort  # noqa: E402
from pairs import ladder, pair_key, parse_pair  # noqa: E402

FIXTURES = SKILL / "tests" / "fixtures" / "harness"
FAKE_HOME = FIXTURES / "home"
SCRIPT = SKILL / "scripts" / "harnesses.py"


def fixture_runner(**overrides):
    """Fixture runner; overrides map a fixture stem (agy_models=) to another file name."""
    files = {}
    for stem, name in overrides.items():
        cmd = next(c for c, f in harnesses.FIXTURE_FILES.items() if f == f"{stem}.txt")
        files[cmd] = name
    return harnesses.fixture_runner(FIXTURES, files)


def detect_fixture(**overrides):
    return detect(fixture_runner(**overrides), FAKE_HOME, env={})


class PairTests(unittest.TestCase):
    def test_parse_pair_roundtrip(self):
        for s in ("claude:claude-opus-5-5@high", "antigravity:gemini-3.8-flash@low", "x:m:odd@default"):
            self.assertEqual(str(parse_pair(s)), s)
        self.assertEqual(parse_pair("x:m:odd@default").model, "m:odd")
        with self.assertRaises(ValueError):
            parse_pair("nocolon@high")
        with self.assertRaises(ValueError):
            parse_pair("a:b")

    def test_pair_key(self):
        self.assertEqual(pair_key(parse_pair("antigravity:gemini-3.8-flash@low")), "antigravity_gemini_3_8_flash_low")

    def test_ladder_variants(self):
        b, a = parse_pair("ag:g@high"), parse_pair("cl:big@high")
        self.assertEqual([str(p) for p in ladder(b, "xhigh", a, b)], ["ag:g@xhigh", "cl:big@high"])
        self.assertEqual([str(p) for p in ladder(parse_pair("ag:g@xhigh"), "xhigh", a, parse_pair("ag:g@xhigh"))], ["cl:big@high"])
        self.assertEqual([str(p) for p in ladder(b, "xhigh", parse_pair("ag:g@xhigh"), b)], ["ag:g@xhigh"])


class DetectTests(unittest.TestCase):
    def test_agy_models_split_effort_suffix(self):
        m = {x["model"]: x["efforts"] for x in detect_fixture()["harnesses"]["antigravity"]["models"]}
        self.assertEqual(m["gemini-4-hydrogen"], ["low", "medium", "high", "xhigh"])
        self.assertEqual(m["gemini-pro-orchestrator-g1-pro"], ["default"])

    def test_claude_models_and_efforts(self):
        h = detect_fixture()["harnesses"]["claude"]
        names = [x["model"] for x in h["models"]]
        self.assertIn("opus", names)
        self.assertIn("haiku", names)
        self.assertEqual(h["models"][0]["efforts"], ["low", "medium", "high", "xhigh", "max"])

    def test_signed_out_harness_is_broken_not_empty(self):
        h = detect_fixture(agy_models="agy_models_signed_out.txt")["harnesses"]["antigravity"]
        self.assertIn("Fetching available models", h["broken"])
        self.assertEqual(h["models"], [])

    def test_missing_binary_is_left_out(self):
        cat = detect_fixture(agy_version="no_such_file.txt")
        self.assertNotIn("antigravity", cat["harnesses"])
        self.assertIn("claude", cat["harnesses"])

    def test_requirements_inputs(self):
        h = detect_fixture()["harnesses"]
        self.assertIn("egm-review", h["antigravity"]["skills"])
        self.assertTrue(Path(h["antigravity"]["skills"]["egm-review"]).is_dir())
        self.assertTrue(any(ok for n, ok in h["claude"]["mcp"].items() if "atlassian" in n.lower()))
        self.assertTrue(h["antigravity"]["mcp"]["atlassian-mcp-server"])

    def test_plugins_enabled_only_and_skills_follow_plugins(self):
        h = detect_fixture()["harnesses"]
        self.assertEqual(h["claude"]["plugins"], ["superpowers", "engineering"])
        self.assertIn("egm", h["antigravity"]["plugins"])
        self.assertIn("test-driven-development", h["claude"]["skills"])
        self.assertIn("my-skill", h["claude"]["skills"])
        self.assertIn("explainer", h["antigravity"]["skills"])

    def test_claude_config_dir_is_a_skill_root(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "skills" / "extra").mkdir(parents=True)
            (Path(d) / "skills" / "extra" / "SKILL.md").write_text("x")
            cat = detect(fixture_runner(), FAKE_HOME, env={"CLAUDE_CONFIG_DIR": d})
            self.assertEqual(cat["harnesses"]["claude"]["skills"]["extra"], str(Path(d) / "skills" / "extra"))

    def test_launch_args_styles(self):
        cat = detect_fixture()
        p = parse_pair("antigravity:gemini-4-hydrogen@xhigh")
        self.assertEqual(launch_args(cat, p, "slug"), ["--agent", "antigravity", "--model", "gemini-4-hydrogen-xhigh"])
        self.assertEqual(launch_args(cat, p, "flag")[-2:], ["--effort", "xhigh"])
        d = parse_pair("antigravity:gemini-pro-orchestrator-g1-pro@default")
        self.assertEqual(launch_args(cat, d, "slug"), ["--agent", "antigravity", "--model", "gemini-pro-orchestrator-g1-pro"])
        self.assertEqual(launch_args(cat, d, "flag"), ["--agent", "antigravity", "--model", "gemini-pro-orchestrator-g1-pro"])

    def test_top_effort(self):
        cat = detect_fixture()
        self.assertEqual(top_effort(cat, "antigravity", "gemini-4-hydrogen"), "xhigh")
        self.assertEqual(top_effort(cat, "antigravity", "gemini-pro-orchestrator-g1-pro"), "default")
        self.assertEqual(top_effort(cat, "claude", "opus"), "max")


class CliTests(unittest.TestCase):
    def cli(self, *args):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def test_detect_args_skill_dir_top_effort(self):
        with tempfile.TemporaryDirectory() as d:
            cat = Path(d) / "catalog.json"
            rc, _ = self.cli("detect", "--out", str(cat), "--fixtures", str(FIXTURES), "--home", str(FAKE_HOME))
            self.assertEqual(rc, 0)
            self.assertIn("antigravity", json.loads(cat.read_text())["harnesses"])
            state = Path(d) / "state.json"
            state.write_text(json.dumps({"CATALOG": str(cat)}))
            pair = "antigravity:gemini-4-hydrogen@high"
            self.assertEqual(self.cli("args", "--state", str(state), "--pair", pair),
                             (0, "--agent antigravity --model gemini-4-hydrogen-high"))
            state.write_text(json.dumps({"CATALOG": str(cat), "LAUNCH_STYLE_antigravity_gemini_4_argon_low": "flag"}))
            self.assertEqual(self.cli("args", "--state", str(state), "--pair", pair),
                             (0, "--agent antigravity --model gemini-4-hydrogen --effort high"))
            state.write_text(json.dumps({"CATALOG": str(cat), "LAUNCH_STYLE_antigravity_gemini_4_hydrogen_high": "slug",
                                         "LAUNCH_STYLE_antigravity_gemini_4_argon_low": "flag"}))
            self.assertEqual(self.cli("args", "--state", str(state), "--pair", pair)[1],
                             "--agent antigravity --model gemini-4-hydrogen-high")
            self.assertEqual(self.cli("args", "--catalog", str(cat), "--pair", pair),
                             (0, "--agent antigravity --model gemini-4-hydrogen-high"))
            self.assertEqual(self.cli("args", "--catalog", str(cat), "--pair", "claude:opus@max"),
                             (0, "--agent claude --model opus --effort max"))
            self.assertEqual(self.cli("args", "--pair", pair)[0], 64)
            self.assertEqual(self.cli("args", "--catalog", str(cat), "--state", str(state), "--pair", pair)[0], 64)
            rc, out = self.cli("skill-dir", "--catalog", str(cat), "--harness", "antigravity", "--skill", "egm-review")
            self.assertEqual(rc, 0)
            self.assertTrue(out.endswith("egm/skills/egm-review"))
            self.assertEqual(self.cli("skill-dir", "--catalog", str(cat), "--harness", "claude", "--skill", "nope")[0], 1)
            self.assertEqual(self.cli("top-effort", "--catalog", str(cat), "--harness", "claude", "--model", "opus"), (0, "max"))


if __name__ == "__main__":
    unittest.main()
