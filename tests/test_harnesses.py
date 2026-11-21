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
        for s in ("antigravity:gemini-3.8-flash@low", "antigravity:gemini-4-hydrogen@xhigh", "x:m:odd@default"):
            self.assertEqual(str(parse_pair(s)), s)
        self.assertEqual(parse_pair("x:m:odd@default").model, "m:odd")
        with self.assertRaises(ValueError):
            parse_pair("nocolon@high")
        with self.assertRaises(ValueError):
            parse_pair("a:b")

    def test_pair_key(self):
        self.assertEqual(pair_key(parse_pair("antigravity:gemini-3.8-flash@low")), "antigravity_gemini_3_8_flash_low")

    def test_ladder_variants(self):
        b, a = parse_pair("antigravity:g@high"), parse_pair("antigravity:big@high")
        self.assertEqual([str(p) for p in ladder(b, "xhigh", a, b)], ["antigravity:g@xhigh", "antigravity:big@high"])
        self.assertEqual([str(p) for p in ladder(parse_pair("antigravity:g@xhigh"), "xhigh", a, parse_pair("antigravity:g@xhigh"))],
                         ["antigravity:big@high"])
        self.assertEqual([str(p) for p in ladder(b, "xhigh", parse_pair("antigravity:g@xhigh"), b)],
                         ["antigravity:g@xhigh"])


class DetectTests(unittest.TestCase):
    def test_agy_models_split_effort_suffix(self):
        m = {x["model"]: x["efforts"] for x in detect_fixture()["harnesses"]["antigravity"]["models"]}
        self.assertEqual(m["gemini-4-hydrogen"], ["low", "medium", "high", "xhigh"])
        self.assertEqual(m["gemini-pro-orchestrator-g1-pro"], ["default"])

    def test_install_dirs_lists_antigravity_skills_folder(self):
        home = Path(tempfile.mkdtemp(prefix="home-"))
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "install-dirs", "--fixtures", str(FIXTURES),
                            "--home", str(home)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {l.split("\t")[0]: l.split("\t")[1:] for l in r.stdout.splitlines()}
        self.assertEqual(rows, {"antigravity": [str(home / ".gemini" / "config" / "skills"), "found"]})

    def test_signed_out_harness_is_broken_not_empty(self):
        h = detect_fixture(agy_models="agy_models_signed_out.txt")["harnesses"]["antigravity"]
        self.assertIn("Fetching available models", h["broken"])
        self.assertEqual(h["models"], [])

    def test_nonzero_models_exit_is_broken(self):
        base = fixture_runner()
        def runner(cmd):
            if tuple(cmd) == ("agy", "models"):
                return 1, "Unauthorized\n"
            return base(cmd)
        runner.fixtures = True
        h = detect(runner, FAKE_HOME, env={})["harnesses"]["antigravity"]
        self.assertEqual(h["models"], [])
        self.assertIn("Unauthorized", h["broken"])

    def test_missing_binary_is_left_out(self):
        cat = detect_fixture(agy_version="no_such_file.txt")
        self.assertNotIn("antigravity", cat["harnesses"])

    def test_requirements_inputs(self):
        h = detect_fixture()["harnesses"]
        self.assertIn("egm-review", h["antigravity"]["skills"])
        self.assertTrue(Path(h["antigravity"]["skills"]["egm-review"]).is_dir())
        self.assertTrue(h["antigravity"]["mcp"]["atlassian-mcp-server"])

    def test_where_requirements_belong(self):
        h = detect_fixture()["harnesses"]
        self.assertEqual(h["antigravity"]["skill_dirs"], [str(FAKE_HOME / ".gemini" / "config" / "skills")])
        self.assertEqual((h["antigravity"]["plugin_cmd"], h["antigravity"]["mcp_cmd"]),
                         ("agy plugin list", "agy mcp list"))

    def test_plugins_enabled_only_and_skills_follow_plugins(self):
        h = detect_fixture()["harnesses"]
        self.assertIn("egm", h["antigravity"]["plugins"])
        self.assertIn("explainer", h["antigravity"]["skills"])

    def test_cli_skill_dirs_scanned_with_priority(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cli_skill = home / ".gemini" / "antigravity-cli" / "skills" / "cli-only"
            shared_skill = home / ".gemini" / "skills" / "shared-only"
            override_cli = home / ".gemini" / "antigravity-cli" / "skills" / "egm-review"
            override_cfg = home / ".gemini" / "config" / "skills" / "egm-review"
            for d in (cli_skill, shared_skill, override_cli, override_cfg):
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text("---\nname: test\n---\n")
            h = detect(fixture_runner(), home, env={})["harnesses"]["antigravity"]
            self.assertEqual(h["skills"]["cli-only"], str(cli_skill))
            self.assertEqual(h["skills"]["shared-only"], str(shared_skill))
            self.assertEqual(h["skills"]["egm-review"], str(override_cli))
            self.assertEqual(h["skill_dirs"], [str(home / ".gemini" / "config" / "skills")])

    def test_launch_args_styles(self):
        cat = detect_fixture()
        p = parse_pair("antigravity:gemini-4-hydrogen@xhigh")
        self.assertEqual(launch_args(cat, p, "slug"), ["--model", "gemini-4-hydrogen-xhigh"])
        self.assertEqual(launch_args(cat, p, "flag"), ["--model", "gemini-4-hydrogen", "--effort", "xhigh"])
        d = parse_pair("antigravity:gemini-pro-orchestrator-g1-pro@default")
        self.assertEqual(launch_args(cat, d, "slug"), ["--model", "gemini-pro-orchestrator-g1-pro"])
        self.assertEqual(launch_args(cat, d, "flag"), ["--model", "gemini-pro-orchestrator-g1-pro"])

    def test_top_effort(self):
        cat = detect_fixture()
        self.assertEqual(top_effort(cat, "antigravity", "gemini-4-hydrogen"), "xhigh")
        self.assertEqual(top_effort(cat, "antigravity", "gemini-pro-orchestrator-g1-pro"), "default")


class RealRunnerTests(unittest.TestCase):
    def test_missing_binary_is_rc_127(self):
        rc, out = harnesses.real_runner(["no-such-binary-for-agy-tests"])
        self.assertEqual(rc, 127)
        self.assertIn("no-such-binary-for-agy-tests", out)

    def test_undecodable_output_is_replaced_not_raised(self):
        rc, out = harnesses.real_runner([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end')"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("ok ") and out.endswith(" end"), out)
        self.assertIn("\ufffd", out)


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
                             (0, "--model gemini-4-hydrogen-high"))
            state.write_text(json.dumps({"CATALOG": str(cat), "LAUNCH_STYLE_antigravity_gemini_4_argon_low": "flag"}))
            self.assertEqual(self.cli("args", "--state", str(state), "--pair", pair),
                             (0, "--model gemini-4-hydrogen --effort high"))
            state.write_text(json.dumps({"CATALOG": str(cat), "LAUNCH_STYLE_antigravity_gemini_4_hydrogen_high": "slug",
                                         "LAUNCH_STYLE_antigravity_gemini_4_argon_low": "flag"}))
            self.assertEqual(self.cli("args", "--state", str(state), "--pair", pair)[1],
                             "--model gemini-4-hydrogen-high")
            self.assertEqual(self.cli("args", "--catalog", str(cat), "--pair", pair),
                             (0, "--model gemini-4-hydrogen-high"))
            self.assertEqual(self.cli("args", "--pair", pair)[0], 64)
            self.assertEqual(self.cli("args", "--catalog", str(cat), "--state", str(state), "--pair", pair)[0], 64)
            rc, out = self.cli("skill-dir", "--catalog", str(cat), "--harness", "antigravity", "--skill", "egm-review")
            self.assertEqual(rc, 0)
            self.assertTrue(out.endswith("egm/skills/egm-review"))
            self.assertEqual(self.cli("skill-dir", "--catalog", str(cat), "--harness", "antigravity", "--skill", "nope")[0], 1)
            self.assertEqual(self.cli("top-effort", "--catalog", str(cat), "--harness", "antigravity", "--model", "gemini-4-hydrogen"),
                             (0, "xhigh"))


if __name__ == "__main__":
    unittest.main()
