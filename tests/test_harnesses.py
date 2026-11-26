import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import harnesses  # noqa: E402
from harnesses import detect, launch_args, top_effort  # noqa: E402
from pairs import ladder, pair_key, parse_pair  # noqa: E402
from helpers import removed_after  # noqa: E402

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

    def test_install_dirs_lists_antigravity_and_claude_skills_folders(self):
        home = Path(removed_after(self, tempfile.mkdtemp(prefix="home-")))
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "install-dirs", "--fixtures", str(FIXTURES),
                            "--home", str(home)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {l.split("\t")[0]: l.split("\t")[1:] for l in r.stdout.splitlines()}
        self.assertEqual(rows, {
            "antigravity": [str(home / ".gemini" / "config" / "skills"), "found"],
            "claude": [str(home / ".claude" / "skills"), "found"],
        })

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

    def test_agy_api_key_alone_does_not_change_the_sign_in(self):
        # Live 2026-11-26: with only the key exported, agy still listed the saved sign-in's models.
        def auth(env):
            return detect(fixture_runner(), FAKE_HOME, env=env)["harnesses"]["antigravity"]["auth"]
        self.assertEqual(auth({}), "oauth")
        self.assertEqual(auth({"GEMINI_API_KEY": "k"}), "oauth")
        self.assertEqual(auth({"AGY_ADC_AUTH": "1"}), "cloud")
        self.assertEqual(auth({"AGY_ADC_AUTH": "false"}), "oauth")

    def agy_auth(self, env=None, files=(), keyring=None):
        """auth_evidence() for a home holding files ({relative path: text}) and a keyring command that succeeds
        ("gdbus" or "security") or none."""
        home = Path(removed_after(self, tempfile.mkdtemp(prefix="agy-home-")))
        for rel, text in dict(files).items():
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text(text)
        asked = []

        def runner(cmd):
            asked.append(cmd)
            if cmd[0] == keyring == "gdbus":
                return 0, "([objectpath '/org/freedesktop/secrets/collection/login/7'], @ao [])"
            if cmd[0] == keyring == "security":
                return 0, 'keychain: "login.keychain-db"\nattributes:\n    "svce"<blob>="gemini"'
            if cmd[0] == "gdbus":
                return 0, "(@ao [], @ao [])"  # the keyring answers, and has no such entry
            return 127, f"{cmd[0]}: command not found"
        how = harnesses.AntigravityAdapter(runner, home, env=env or {}).auth_evidence()
        return how, asked

    def test_agy_saved_sign_in_is_found_where_agy_keeps_it(self):
        token = {".gemini/antigravity-cli/antigravity-oauth-token": "x"}
        how, asked = self.agy_auth(files=token)
        self.assertEqual(how, ("oauth", "saved Google sign-in: token file ~/.gemini/antigravity-cli/"
                                        "antigravity-oauth-token"))
        self.assertEqual(asked, [])  # the file settles it: no keyring is asked
        how, asked = self.agy_auth(keyring="gdbus")
        self.assertEqual(how, ("oauth", "saved Google sign-in: keyring entry antigravity on gemini"))
        # The keyring is asked which entries exist for that service and account, never for a secret.
        self.assertEqual(asked[0][-2:], ["org.freedesktop.Secret.Service.SearchItems",
                                         "{'service': 'gemini', 'username': 'antigravity'}"])
        how, asked = self.agy_auth(keyring="security")
        self.assertEqual(how, ("oauth", "saved Google sign-in: Keychain entry antigravity on gemini"))
        self.assertEqual(asked[-1], ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity"])
        self.assertFalse({"-g", "-w"} & set(asked[-1]))

    def test_agy_sign_in_nothing_confirms_is_assumed_oauth_and_says_so(self):
        how, _ = self.agy_auth()
        self.assertEqual(how[0], "oauth")
        self.assertTrue(how[1].startswith("assumed: "), how)

    def test_agy_adc_and_the_gemini_api_come_before_a_saved_sign_in(self):
        token = {".gemini/antigravity-cli/antigravity-oauth-token": "x"}
        adc = {".gemini/config/config.json": json.dumps({"userSettings": {"enableAdc": True}})}
        self.assertEqual(self.agy_auth(files={**token, **adc})[0][0], "cloud")
        self.assertIn("enableAdc", self.agy_auth(files=adc)[0][1])
        off = {".gemini/config/config.json": json.dumps({"userSettings": {"enableAdc": False}})}
        self.assertEqual(self.agy_auth(files={**token, **off})[0][0], "oauth")
        self.assertEqual(self.agy_auth(files={".gemini/config/config.json": "{not json"})[0][0], "oauth")
        self.assertEqual(self.agy_auth(env={"AGY_ADC_AUTH": "true"}, files=token)[0][0], "cloud")
        # The variable that only says which credentials ADC would use is not a way to connect.
        self.assertEqual(self.agy_auth(env={"GOOGLE_APPLICATION_CREDENTIALS": "/sa.json"}, files=token)[0][0], "oauth")

    def test_agy_is_on_the_gemini_api_only_with_the_provider_setting(self):
        token = {".gemini/antigravity-cli/antigravity-oauth-token": "x"}
        gemini = {".gemini/antigravity-cli/settings.json": json.dumps({"model": "m", "modelProvider": "gemini"})}
        how, asked = self.agy_auth(env={"GEMINI_API_KEY": "k"}, files={**token, **gemini}, keyring="gdbus")
        self.assertEqual(how[0], "api-key")
        self.assertIn('modelProvider is "gemini"', how[1])
        self.assertEqual(asked, [])
        # The setting decides, not the key: without the key agy refuses to run instead of using the sign-in.
        self.assertEqual(self.agy_auth(files={**token, **gemini})[0][0], "api-key")
        # The key without the setting is ignored by agy, and the reason says so.
        how, _ = self.agy_auth(env={"GEMINI_API_KEY": "k"}, files=token)
        self.assertEqual(how[0], "oauth")
        self.assertIn("GEMINI_API_KEY is set, but agy uses it only with modelProvider", how[1])
        self.assertNotIn("GEMINI_API_KEY", self.agy_auth(files=token)[0][1])
        # A provider this code does not know is treated as the restricted kind.
        other = {".gemini/antigravity-cli/settings.json": json.dumps({"modelProvider": "something-new"})}
        how, _ = self.agy_auth(files={**token, **other})
        self.assertEqual(how[0], "oauth")
        self.assertTrue(how[1].startswith("assumed: "), how)
        self.assertEqual(self.agy_auth(files={".gemini/antigravity-cli/settings.json": "{bad"})[0][0], "oauth")

    def test_the_catalog_records_what_showed_the_sign_in(self):
        h = detect_fixture()["harnesses"]
        self.assertEqual(h["claude"]["auth_why"], "claude auth status: authMethod claude.ai, apiProvider firstParty")
        self.assertTrue(h["antigravity"]["auth_why"].startswith("assumed: "))

    def test_detect_runs_its_commands_at_once_with_the_same_result(self):
        # Some CLI commands take seconds each: detect starts them together, and the catalog is what it was.
        base = fixture_runner()
        lock, state = threading.Lock(), {"now": 0, "most": 0, "calls": []}
        two_in_flight = threading.Event()

        def slow(cmd):
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
                state["calls"].append(tuple(cmd))
                if state["now"] >= 2:
                    two_in_flight.set()
            two_in_flight.wait(2)  # only a runner called one command at a time waits this out
            with lock:
                state["now"] -= 1
            return base(cmd)
        slow.fixtures = True
        with unittest.mock.patch("harnesses.datetime") as dt:
            dt.datetime.now.return_value.isoformat.return_value = "T"
            got = detect(slow, FAKE_HOME, env={})
            want = detect(base, FAKE_HOME, env={})
        self.assertGreater(state["most"], 1)
        self.assertEqual(json.dumps(got, indent=1), json.dumps(want, indent=1))
        self.assertEqual(len(state["calls"]), len(set(state["calls"])))  # each command once

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

    def test_live_detect_discovers_bundled_skills_as_fallback(self):
        base = fixture_runner()
        def live_like_runner(cmd):
            return base(cmd)
        with tempfile.TemporaryDirectory() as td, unittest.mock.patch.object(
            harnesses.shutil, "which", return_value="/usr/bin/agy"
        ):
            home = Path(td)
            override = home / ".gemini" / "config" / "skills" / "flight-readiness"
            override.mkdir(parents=True)
            (override / "SKILL.md").write_text("---\nname: flight-readiness\n---\n")
            h = detect(live_like_runner, home, env={})["harnesses"]["antigravity"]
            self.assertEqual(h["skills"]["flight-readiness"], str(override))
            self.assertEqual(h["skills"]["mission-brief"], str(SKILL / "skills" / "mission-brief"))
            self.assertEqual(h["skills"]["launch-plan"], str(SKILL / "skills" / "launch-plan"))
            self.assertEqual(h["skills"]["spec-drift"], str(SKILL / "skills" / "spec-drift"))

    def test_antigravity_cli_plugins_dir_scanned(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cli_plug = home / ".gemini" / "antigravity-cli" / "plugins" / "egm" / "skills" / "custom-cli-plug"
            cli_plug.mkdir(parents=True)
            (cli_plug / "SKILL.md").write_text("---\nname: custom-cli-plug\n---\n")
            h = detect(fixture_runner(), home, env={})["harnesses"]["antigravity"]
            self.assertEqual(h["skills"]["custom-cli-plug"], str(cli_plug))

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


class ClaudeAdapterTests(unittest.TestCase):
    def test_detect_claude_version_models_plugins_mcp(self):
        cat = detect_fixture()
        self.assertIn("claude", cat["harnesses"])
        c = cat["harnesses"]["claude"]
        self.assertEqual(c["binary"], "claude")
        self.assertEqual(c["version"], "2.1.323")
        self.assertEqual(c["styles"], ["flag"])
        self.assertIsNone(c["broken"])
        models = {m["model"]: m["efforts"] for m in c["models"]}
        self.assertEqual(models["opus"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(models["sonnet"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(models["haiku"], ["default"])
        self.assertEqual(c["plugins"], ["superpowers@claude-plugins-official", "engineering@synced"])
        self.assertEqual(c["mcp"], {
            "claude.ai Atlassian MCP": True,
            "claude.ai Context7": True,
            "claude.ai Linear": False,
        })
        self.assertEqual(c["skill_dirs"], [str(FAKE_HOME / ".claude" / "skills")])
        self.assertEqual((c["plugin_cmd"], c["mcp_cmd"]), ("claude plugin list", "claude mcp list"))
        self.assertEqual(c["auth"], "oauth")  # the fixture is a subscription sign-in

    def claude_auth(self, status, env=None):
        """auth() with `claude auth status` printing status (a dict as JSON, a string as is, None: no command)."""
        def runner(cmd):
            if status is None:
                return 127, "claude: command not found"
            return 0, status if isinstance(status, str) else json.dumps(status)
        return harnesses.ClaudeAdapter(runner, FAKE_HOME, env=env or {}).auth()

    def test_claude_auth_from_auth_status(self):
        signed_in = {"loggedIn": True, "apiProvider": "firstParty"}
        self.assertEqual(self.claude_auth({**signed_in, "authMethod": "claude.ai"}), "oauth")
        self.assertEqual(self.claude_auth({**signed_in, "authMethod": "api_key"}), "api-key")
        self.assertEqual(self.claude_auth({**signed_in, "authMethod": "api_key_helper"}), "api-key")
        self.assertEqual(self.claude_auth({"loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock"}),
                         "cloud")
        # A method this code does not know is treated as the restricted one.
        self.assertEqual(self.claude_auth({**signed_in, "authMethod": "something_new"}), "oauth")
        # The key in the environment does not override what the CLI itself reports.
        self.assertEqual(self.claude_auth({**signed_in, "authMethod": "claude.ai"}, env={"ANTHROPIC_API_KEY": "k"}),
                         "oauth")

    def test_claude_auth_falls_back_to_the_environment(self):
        for status in (None, "not json", {"loggedIn": False, "authMethod": "none"}):
            self.assertEqual(self.claude_auth(status), "oauth")
            self.assertEqual(self.claude_auth(status, env={"ANTHROPIC_API_KEY": "k"}), "api-key")
            self.assertEqual(self.claude_auth(status, env={"ANTHROPIC_API_KEY": " "}), "oauth")
            self.assertEqual(self.claude_auth(status, env={"CLAUDE_CODE_USE_BEDROCK": "1"}), "cloud")
            self.assertEqual(self.claude_auth(status, env={"CLAUDE_CODE_USE_VERTEX": "0"}), "oauth")

    def test_nonzero_help_marks_claude_broken(self):
        base = fixture_runner()
        def runner(cmd):
            if tuple(cmd) == ("claude", "--help"):
                return 1, "Auth required\n"
            return base(cmd)
        runner.fixtures = True
        c = detect(runner, FAKE_HOME, env={})["harnesses"]["claude"]
        self.assertEqual(c["models"], [])
        self.assertIn("Auth required", c["broken"])

    def test_claude_skill_roots_priority(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            user_dir = home / ".claude" / "skills" / "brainstorming"
            old_cache = home / ".claude" / "plugins" / "cache" / "claude-plugins-official" / "superpowers" / "7.0.2" / "skills"
            new_cache = home / ".claude" / "plugins" / "cache" / "claude-plugins-official" / "superpowers" / "7.0.10" / "skills"
            for d in (
                user_dir,
                old_cache / "brainstorming",
                old_cache / "tdd",
                new_cache / "brainstorming",
                new_cache / "tdd",
                new_cache / "newer-only",
            ):
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text("---\nname: test\n---\n")
            with unittest.mock.patch.object(harnesses.shutil, "which", return_value="/usr/bin/claude"):
                base = fixture_runner()
                def live_like_runner(cmd):
                    return base(cmd)
                c = detect(live_like_runner, home, env={})["harnesses"]["claude"]
            self.assertEqual(c["skills"]["brainstorming"], str(user_dir))
            # `claude plugin list` says 7.0.2 is installed: a newer cached copy is not read.
            self.assertEqual(c["skills"]["tdd"], str(old_cache / "tdd"))
            self.assertNotIn("newer-only", c["skills"])
            self.assertEqual(c["skills"]["mission"], str(SKILL / "skills" / "mission"))

    def test_claude_skill_roots_without_a_version_use_only_the_newest_cache(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cache = home / ".claude" / "plugins" / "cache" / "official"
            for d in (cache / "p" / "1.9.0" / "skills" / "removed", cache / "p" / "1.10.0" / "skills" / "kept"):
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text("---\nname: test\n---\n")
            base = fixture_runner()
            def runner(cmd):
                if tuple(cmd) == ("claude", "plugin", "list"):
                    return 0, "Installed plugins:\n\n  \u276f p@official\n    Status: \u2714 enabled\n"
                return base(cmd)
            runner.fixtures = True
            skills = harnesses.ClaudeAdapter(runner, home, env={}).skills()
            self.assertEqual(skills["kept"], str(cache / "p" / "1.10.0" / "skills" / "kept"))
            self.assertNotIn("removed", skills)

    def test_claude_models_add_new_aliases_from_help(self):
        base = fixture_runner()
        def runner_with(help_text):
            def runner(cmd):
                return (0, help_text) if tuple(cmd) == ("claude", "--help") else base(cmd)
            runner.fixtures = True
            return runner
        effort = "  --effort <level>      Effort level (low, medium, high, xhigh, max)\n"
        named = ("  --model <model>       Model for the current session. Use an alias for the latest model (e.g.\n"
                 "                        'fable', 'opus', or 'sonnet') or a model's full name (e.g.\n"
                 "                        'claude-sonnet-4-6').\n  -n, --name <name>  Name\n")
        models = harnesses.ClaudeAdapter(runner_with(effort + named), FAKE_HOME, env={}).models()
        self.assertEqual([m["model"] for m in models], ["opus", "sonnet", "haiku", "fable"])
        self.assertEqual(models[3], {"model": "fable", "label": "Claude Fable",
                                     "efforts": ["low", "medium", "high", "xhigh", "max"]})
        plain = "  --model <model>       Model for the current session ('opus' or 'sonnet').\n"
        self.assertEqual([m["model"] for m in harnesses.ClaudeAdapter(runner_with(effort + plain), FAKE_HOME,
                                                                      env={}).models()], ["opus", "sonnet", "haiku"])

    def test_claude_launch_args_and_top_effort(self):
        cat = detect_fixture()
        self.assertEqual(launch_args(cat, parse_pair("claude:opus@max"), "flag"), ["--model", "opus", "--effort", "max"])
        self.assertEqual(launch_args(cat, parse_pair("claude:haiku@default"), "flag"), ["--model", "haiku"])
        self.assertEqual(top_effort(cat, "claude", "opus"), "max")
        self.assertEqual(top_effort(cat, "claude", "haiku"), "default")

    def test_session_harness_is_the_nearest_harness_cli_among_the_ancestors(self):
        found = harnesses.session_evidence
        self.assertEqual(found(env={}, ancestors=["bash", "claude", "gnome-shell"]),
                         ("claude", "ancestor process claude"))
        self.assertEqual(found(env={}, ancestors=["zsh", "agy", "tmux"])[0], "antigravity")
        # A CLI started from inside the other harness's shell: the nearest one runs the session, whatever the
        # variables it inherited say.
        self.assertEqual(found(env={"CLAUDECODE": "1"}, ancestors=["bash", "agy", "bash", "claude"])[0], "antigravity")
        self.assertEqual(found(env={}, ancestors=["bash", "claude", "bash", "agy"])[0], "claude")

    def test_session_harness_from_the_variables_a_cli_sets_when_no_ancestor_is_one(self):
        found = harnesses.session_evidence
        self.assertEqual(found(env={"CLAUDECODE": "1"}, ancestors=["bash", "node"]),
                         ("claude", "environment CLAUDECODE"))
        self.assertEqual(found(env={"CLAUDE_CODE_ENTRYPOINT": "cli"}, ancestors=[])[0], "claude")
        # Settings a user exports for agy are in every shell, so they mark nothing.
        self.assertIsNone(found(env={"AGY_ADC_AUTH": "1", "ANTIGRAVITY_PROJECT_ID": "p"}, ancestors=["bash"])[0])

    def test_session_harness_is_never_guessed(self):
        name, why = harnesses.session_evidence(env={}, ancestors=["bash", "sshd", "systemd"])
        self.assertIsNone(name)
        self.assertIn("no harness CLI among the ancestor processes", why)
        self.assertIsNone(harnesses.detect_session_harness(env={}, ancestors=[]))

    def test_session_harness_override_names_it_outright(self):
        env = {harnesses.SESSION_OVERRIDE: "antigravity", "CLAUDECODE": "1"}
        self.assertEqual(harnesses.detect_session_harness(env=env, ancestors=["bash", "claude"]), "antigravity")
        # A name that is no harness is ignored, not trusted.
        self.assertEqual(harnesses.detect_session_harness(env={harnesses.SESSION_OVERRIDE: "nope"},
                                                          ancestors=["claude"]), "claude")

    def test_ancestor_names_start_at_this_process_parent(self):
        names = harnesses._ancestor_names()
        me = subprocess.run(["ps", "-o", "comm=", "-p", str(os.getppid())], capture_output=True, text=True).stdout
        self.assertEqual(names[0], os.path.basename(me.strip()))

    def test_claude_mcp_status_parsing_and_nonzero_rc(self):
        base = fixture_runner()
        def runner(cmd):
            if tuple(cmd) == ("claude", "mcp", "list"):
                return 0, (
                    "srv-ok: https://example.com/1 - ✔ Connected\n"
                    "srv-dis: https://example.com/2 - ✗ Disconnected\n"
                    "srv-not: https://example.com/3 - ! Not connected\n"
                    "plugin:engineering:atlassian: https://mcp.atlassian.com/v1/sse (SSE) - ! Needs authentication\n"
                    "plugin:x:docs: https://example.com/4 - \u2714 Connected\n"
                )
            return base(cmd)
        adapter = harnesses.ClaudeAdapter(runner, FAKE_HOME, env={})
        self.assertEqual(adapter.mcp(), {
            "srv-ok": True,
            "srv-dis": False,
            "srv-not": False,
            "plugin:engineering:atlassian": False,
            "plugin:x:docs": True,
        })

        def failing_runner(cmd):
            if tuple(cmd) == ("claude", "mcp", "list"):
                return 1, "srv-ok: https://example.com/1 - ✔ Connected\n"
            return base(cmd)
        self.assertEqual(harnesses.ClaudeAdapter(failing_runner, FAKE_HOME, env={}).mcp(), {})

    def test_claude_plugins_excludes_negated_statuses(self):
        base = fixture_runner()
        def runner(cmd):
            if tuple(cmd) == ("claude", "plugin", "list"):
                return 0, (
                    "Installed plugins:\n\n"
                    "  ❯ active-one@official\n"
                    "    Status: ✔ enabled\n\n"
                    "  ❯ active-two@synced\n"
                    "    Status: ✔ loaded\n\n"
                    "  ❯ off-unloaded@official\n"
                    "    Status: ✗ unloaded\n\n"
                    "  ❯ off-not-loaded@official\n"
                    "    Status: ! not loaded\n\n"
                    "  ❯ off-disabled@official\n"
                    "    Status: ✗ disabled\n\n"
                    "  ❯ off-not-enabled@official\n"
                    "    Status: ! not enabled\n"
                )
            return base(cmd)
        self.assertEqual(
            harnesses.ClaudeAdapter(runner, FAKE_HOME, env={}).plugins(),
            ["active-one@official", "active-two@synced"],
        )


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
