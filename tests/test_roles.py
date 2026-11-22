import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "roles.py"
CATALOG = Path(__file__).resolve().parent / "fixtures" / "catalog.json"
CATALOG_MULTI = Path(__file__).resolve().parent / "fixtures" / "catalog_multi.json"
GOOD = dict(director="antigravity:big@max", safety="antigravity:g@high", crew="antigravity:g@high")


class RolesTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="roles-"))
        self.catalog = self.d / "catalog.json"
        self.catalog.write_text(CATALOG.read_text())
        self.roles_file = self.d / "cfg" / "roles.json"

    def edit_catalog(self, fn):
        cat = json.loads(CATALOG.read_text())
        fn(cat)
        self.catalog.write_text(json.dumps(cat))

    def roles(self, cmd, *extra, jira="no", **picks):
        picks = {**GOOD, **picks}
        args = {"menu": ["--catalog", str(self.catalog)],
                "check": ["--catalog", str(self.catalog), "--jira", jira] + [x for k, v in picks.items() for x in (f"--{k}", v)],
                "remembered": ["--roles", str(self.roles_file), "--catalog", str(self.catalog)],
                "save": ["--roles", str(self.roles_file)] + [x for k, v in picks.items() for x in (f"--{k}", v)],
                "to-state": ["--roles", str(self.roles_file), "--catalog", str(self.catalog)]}[cmd]
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), cmd, *args, *extra], capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    def save(self, **picks):
        self.assertEqual(self.roles("save", **picks)[0], 0)

    def test_check_all_ok(self):
        rc, out = self.roles("check")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK session antigravity", out)
        self.assertIn("OK director antigravity:big@max", out)
        self.assertIn("OK crew antigravity:g@high", out)

    def test_check_reports_missing_session_and_role_skills(self):
        self.edit_catalog(lambda c: c["harnesses"]["antigravity"]["skills"].pop("gauntlet"))
        rc, out = self.roles("check", safety="antigravity:g@high")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING safety antigravity:g@high gauntlet in antigravity: put it in /home/u/.gemini/config/skills, "
                      "or enable a plugin that has it (agy plugin list)", out)
        self.edit_catalog(lambda c: c["harnesses"]["antigravity"]["skills"].pop("mission"))
        rc, out = self.roles("check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING session antigravity mission in antigravity:", out)

    def test_missing_without_where_in_an_old_catalog(self):
        def strip(c):
            c["harnesses"]["antigravity"]["skills"].pop("gauntlet")
            for k in ("skill_dirs", "plugin_cmd", "mcp_cmd"):
                c["harnesses"]["antigravity"].pop(k)
        self.edit_catalog(strip)
        rc, out = self.roles("check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING safety antigravity:g@high gauntlet in antigravity\n", out + "\n")

    def test_jira_needs_connected_atlassian(self):
        self.assertEqual(self.roles("check", jira="yes")[0], 0)
        self.edit_catalog(lambda c: c["harnesses"]["antigravity"]["mcp"].update({"atlassian-mcp-server": False}))
        rc, out = self.roles("check", jira="yes")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING director antigravity:big@max atlassian-mcp in antigravity: connect an Atlassian MCP server "
                      "(agy mcp list)", out)
        self.assertEqual(self.roles("check", jira="no")[0], 0)

    def test_unknown_cases(self):
        def broke(c):
            c["harnesses"]["antigravity"]["broken"] = "no models"
        for pick, why in [("zz:g@high", "not detected"), ("antigravity:nope@high", "model nope not detected"),
                          ("antigravity:big@ultra", "effort ultra not detected"), ("junk", "bad pair")]:
            rc, out = self.roles("check", crew=pick)
            self.assertEqual(rc, 1)
            self.assertIn(f"UNKNOWN crew {pick} ", out)
            self.assertIn(why, out)
        self.edit_catalog(broke)
        rc, out = self.roles("check")
        self.assertEqual(rc, 1)
        self.assertIn("UNKNOWN safety antigravity:g@high harness antigravity is broken: no models", out)

    def test_shared_model_note(self):
        rc, out = self.roles("check", director="antigravity:g@xhigh")
        self.assertEqual(rc, 0)
        self.assertIn("NOTE director and crew share antigravity:g; the ladder skips repeated rungs", out)
        self.assertNotIn("NOTE", self.roles("check")[1])

    def test_crew_equal_to_director_is_unknown(self):
        rc, out = self.roles("check", director="antigravity:g@high", crew="antigravity:g@high")
        self.assertEqual(rc, 1)
        self.assertIn("UNKNOWN crew antigravity:g@high is the flight director pick, which never implements first; "
                      "pick another effort or model", out)
        self.assertNotIn("OK crew", out)
        self.assertIn("OK director antigravity:g@high", out)

    def test_same_model_other_effort_is_ok_with_note(self):
        rc, out = self.roles("check", director="antigravity:g@xhigh", crew="antigravity:g@medium")
        self.assertEqual(rc, 0)
        self.assertIn("OK crew antigravity:g@medium", out)
        self.assertIn("NOTE director and crew share antigravity:g", out)
        self.assertNotIn("weaker", out)

    def test_director_weaker_than_crew_on_same_model_notes(self):
        rc, out = self.roles("check", director="antigravity:g@medium", crew="antigravity:g@high")
        self.assertEqual(rc, 0)
        self.assertIn("NOTE the ladder's last rung, director antigravity:g@medium, is a lower effort than crew antigravity:g@high "
                      "on the same model, so escalating to it is weaker than the first attempt", out)
        self.assertNotIn("weaker", self.roles("check", director="antigravity:g@xhigh")[1])

    def test_remembered_none_then_available(self):
        self.assertEqual(self.roles("remembered"), (1, "NONE"))
        self.save()
        rc, out = self.roles("remembered")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[0], "director=antigravity:big@max")
        self.assertEqual(out.splitlines()[-1], "AVAILABLE yes")
        self.assertEqual(len(out.splitlines()), 4)

    def test_remembered_pick_vanished(self):
        self.save(crew="antigravity:gone@high")
        rc, out = self.roles("remembered")
        self.assertEqual(out.splitlines()[-1], "AVAILABLE no crew antigravity:gone@high")

    def test_remembered_lists_every_vanished_pick(self):
        self.save(safety="antigravity:gone@high", crew="antigravity:g@nope")
        rc, out = self.roles("remembered")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[-2:], ["AVAILABLE no safety antigravity:gone@high", "AVAILABLE no crew antigravity:g@nope"])

    def test_save_writes_json_and_creates_folder(self):
        self.save()
        data = json.loads(self.roles_file.read_text())
        self.assertEqual({k: data[k] for k in GOOD}, GOOD)
        self.assertIn("saved_at", data)
        self.assertEqual(self.roles("save", crew="bad")[0], 1)

    def test_menu_lists_broken_after_numbered(self):
        def broke(c):
            c["harnesses"]["xx"] = {**copy.deepcopy(c["harnesses"]["antigravity"]), "models": [], "broken": "garbled"}
        self.edit_catalog(broke)
        rc, out = self.roles("menu")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split("\t")[:2], ["1", "antigravity"])
        self.assertEqual(lines[1], "BROKEN xx: garbled")

    def test_menu_models_and_efforts(self):
        rc, out = self.roles("menu", "--harness", "antigravity")
        self.assertEqual([l.split("\t")[:2] for l in out.splitlines()], [["1", "g"], ["2", "big"], ["3", "small"]])
        rc, out = self.roles("menu", "--harness", "antigravity", "--model", "big")
        self.assertEqual([l.split("\t")[1] for l in out.splitlines()], ["low", "medium", "high", "xhigh", "max"])

    def test_menu_unknown_harness_or_model_names_the_known_ones(self):
        rc, out = self.roles("menu", "--harness", "agy")
        self.assertEqual((rc, out), (1, "UNKNOWN harness agy (known: antigravity)"))
        rc, out = self.roles("menu", "--harness", "antigravity", "--model", "nope")
        self.assertEqual((rc, out), (1, "UNKNOWN model nope for antigravity (known: g, big, small)"))

    def test_to_state_sets_roles_and_crew_top(self):
        self.save()
        state = self.d / "state.json"
        state.write_text(json.dumps({"KEEP": "1"}))
        rc, _ = self.roles("to-state", "--state", str(state))
        self.assertEqual(rc, 0)
        s = json.loads(state.read_text())
        self.assertNotIn("ROLE_CAPCOM", s)
        self.assertEqual((s["ROLE_DIRECTOR"], s["ROLE_SAFETY"], s["ROLE_CREW"]),
                         ("antigravity:big@max", "antigravity:g@high", "antigravity:g@high"))
        self.assertEqual((s["CREW_TOP"], s["CATALOG"], s["KEEP"]), ("xhigh", str(self.catalog), "1"))

    def test_execute_plan_mode_requires_execution_skills_only(self):
        def strip_design(c):
            for s in ("mission", "draft-spec", "flight-plan", "crucible", "gauntlet", "crossfire"):
                c["harnesses"]["antigravity"]["skills"].pop(s, None)
        self.edit_catalog(strip_design)
        self.assertEqual(self.roles("check")[0], 1)
        rc, out = self.roles("check", "--mode", "launch-plan")
        self.assertEqual(rc, 0, out)
        self.edit_catalog(lambda c: (strip_design(c), c["harnesses"]["antigravity"]["skills"].pop("launch-plan")))
        rc, out = self.roles("check", "--mode", "launch-plan")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING session antigravity launch-plan", out)

    def test_multi_harness_menu_and_mixed_check(self):
        self.catalog.write_text(CATALOG_MULTI.read_text())
        rc, out = self.roles("menu")
        self.assertEqual(rc, 0)
        self.assertEqual([l.split("\t")[:2] for l in out.splitlines()], [["1", "antigravity"], ["2", "claude"]])

        rc, out = self.roles(
            "check", "--session-harness", "claude",
            jira="yes", director="claude:opus@high", safety="antigravity:g@high", crew="antigravity:g@high",
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("OK session claude", out)
        self.assertIn("OK director claude:opus@high", out)
        self.assertIn("OK safety antigravity:g@high", out)
        self.assertIn("OK crew antigravity:g@high", out)

        cat = json.loads(CATALOG_MULTI.read_text())
        cat["harnesses"]["claude"]["skills"].pop("mission")
        self.catalog.write_text(json.dumps(cat))
        rc, out = self.roles(
            "check", "--session-harness", "claude",
            jira="no", director="claude:opus@high", safety="antigravity:g@high", crew="antigravity:g@high",
        )
        self.assertEqual(rc, 1)
        self.assertIn("MISSING session claude mission in claude: put it in /home/u/.claude/skills", out)


if __name__ == "__main__":
    unittest.main()
