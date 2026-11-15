import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "roles.py"
CATALOG = Path(__file__).resolve().parent / "fixtures" / "catalog.json"
GOOD = dict(coordinator="cl:big@high", architect="cl:big@max", egm="ag:g@high", base="ag:g@high")


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
        self.assertEqual(rc, 0)
        self.assertIn("OK coordinator cl:big@high", out)
        self.assertIn("OK base ag:g@high", out)

    def test_check_reports_missing_skill_with_harness(self):
        self.edit_catalog(lambda c: c["harnesses"]["ag"]["skills"].pop("egm-review"))
        rc, out = self.roles("check", egm="ag:g@high")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING egm ag:g@high egm-review in ag", out)

    def test_jira_needs_connected_atlassian(self):
        self.assertEqual(self.roles("check", jira="yes")[0], 0)
        self.edit_catalog(lambda c: c["harnesses"]["cl"]["mcp"].update({"claude_ai_Atlassian": False}))
        rc, out = self.roles("check", jira="yes")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING architect cl:big@max atlassian-mcp in cl", out)
        self.assertEqual(self.roles("check", jira="no")[0], 0)

    def test_unknown_cases(self):
        def broke(c):
            c["harnesses"]["ag"]["broken"] = "no models"
        for pick, why in [("zz:g@high", "not detected"), ("cl:nope@high", "model nope not detected"),
                          ("cl:big@ultra", "effort ultra not detected"), ("junk", "bad pair")]:
            rc, out = self.roles("check", base=pick)
            self.assertEqual(rc, 1)
            self.assertIn(f"UNKNOWN base {pick} ", out)
            self.assertIn(why, out)
        self.edit_catalog(broke)
        rc, out = self.roles("check")
        self.assertEqual(rc, 1)
        self.assertIn("UNKNOWN egm ag:g@high harness ag is broken: no models", out)

    def test_shared_model_note(self):
        rc, out = self.roles("check", architect="ag:g@xhigh")
        self.assertEqual(rc, 0)
        self.assertIn("NOTE architect and base share ag:g; the ladder skips repeated rungs", out)
        self.assertNotIn("NOTE", self.roles("check")[1])

    def test_remembered_none_then_available(self):
        self.assertEqual(self.roles("remembered"), (1, "NONE"))
        self.save()
        rc, out = self.roles("remembered")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[0], "coordinator=cl:big@high")
        self.assertEqual(out.splitlines()[-1], "AVAILABLE yes")
        self.assertEqual(len(out.splitlines()), 5)

    def test_remembered_pick_vanished(self):
        self.save(base="ag:gone@high")
        rc, out = self.roles("remembered")
        self.assertEqual(out.splitlines()[-1], "AVAILABLE no base ag:gone@high")

    def test_save_writes_json_and_creates_folder(self):
        self.save()
        data = json.loads(self.roles_file.read_text())
        self.assertEqual({k: data[k] for k in GOOD}, GOOD)
        self.assertIn("saved_at", data)
        self.assertEqual(self.roles("save", base="bad")[0], 1)

    def test_menu_lists_broken_after_numbered(self):
        def broke(c):
            c["harnesses"]["xx"] = {**copy.deepcopy(c["harnesses"]["ag"]), "models": [], "broken": "garbled"}
        self.edit_catalog(broke)
        rc, out = self.roles("menu")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split("\t")[:2], ["1", "ag"])
        self.assertEqual(lines[1].split("\t")[:2], ["2", "cl"])
        self.assertEqual(lines[2], "BROKEN xx: garbled")

    def test_menu_models_and_efforts(self):
        rc, out = self.roles("menu", "--harness", "cl")
        self.assertEqual([l.split("\t")[:2] for l in out.splitlines()], [["1", "big"], ["2", "small"]])
        rc, out = self.roles("menu", "--harness", "cl", "--model", "big")
        self.assertEqual([l.split("\t")[1] for l in out.splitlines()], ["low", "medium", "high", "xhigh", "max"])

    def test_to_state_sets_roles_and_base_top(self):
        self.save()
        state = self.d / "state.json"
        state.write_text(json.dumps({"KEEP": "1"}))
        rc, _ = self.roles("to-state", "--state", str(state))
        self.assertEqual(rc, 0)
        s = json.loads(state.read_text())
        self.assertEqual((s["ROLE_COORDINATOR"], s["ROLE_ARCHITECT"], s["ROLE_EGM"], s["ROLE_BASE"]),
                         ("cl:big@high", "cl:big@max", "ag:g@high", "ag:g@high"))
        self.assertEqual((s["BASE_TOP"], s["CATALOG"], s["KEEP"]), ("xhigh", str(self.catalog), "1"))


if __name__ == "__main__":
    unittest.main()
