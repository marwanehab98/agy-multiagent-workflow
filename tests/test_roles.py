import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from helpers import removed_after

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "roles.py"
sys.path.insert(0, str(SCRIPT.parent))
import roles as roles_mod  # noqa: E402
CATALOG = Path(__file__).resolve().parent / "fixtures" / "catalog.json"
CATALOG_MULTI = Path(__file__).resolve().parent / "fixtures" / "catalog_multi.json"
GOOD = dict(director="antigravity:big@max", safety="antigravity:g@high", crew="antigravity:g@high")


class RolesTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(removed_after(self, tempfile.mkdtemp(prefix="roles-")))
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
        # The session's harness is named outright: what these tests detect must not depend on where they run.
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), cmd, *args, *extra], capture_output=True, text=True,
                           env={**os.environ, "MISSION_CONTROL_SESSION_HARNESS": "antigravity"})
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
        self.edit_catalog(lambda c: c["harnesses"]["antigravity"]["skills"].pop("flight-readiness"))
        rc, out = self.roles("check", safety="antigravity:g@high")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING safety antigravity:g@high flight-readiness in antigravity: put it in /home/u/.gemini/config/skills, "
                      "or enable a plugin that has it (agy plugin list)", out)
        self.edit_catalog(lambda c: c["harnesses"]["antigravity"]["skills"].pop("mission"))
        rc, out = self.roles("check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING session antigravity mission in antigravity:", out)

    def test_missing_without_where_in_an_old_catalog(self):
        def strip(c):
            c["harnesses"]["antigravity"]["skills"].pop("flight-readiness")
            for k in ("skill_dirs", "plugin_cmd", "mcp_cmd"):
                c["harnesses"]["antigravity"].pop(k)
        self.edit_catalog(strip)
        rc, out = self.roles("check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING safety antigravity:g@high flight-readiness in antigravity\n", out + "\n")

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

    def selections(self):
        return [{k: x[k] for k in GOOD} for x in json.loads(self.roles_file.read_text())["selections"]]

    def remembered(self, *extra):
        rc, out = self.roles("remembered", *extra)
        return rc, [l.split("\t") for l in out.splitlines()]

    A = GOOD
    B = {**GOOD, "crew": "antigravity:small@high"}
    C = {**GOOD, "safety": "antigravity:small@low"}
    D = {**GOOD, "director": "antigravity:big@high"}

    def test_remembered_none_then_a_selection_and_new(self):
        self.assertEqual(self.roles("remembered"), (1, "NONE"))
        self.save()
        rc, rows = self.remembered()
        self.assertEqual(rc, 0)
        self.assertEqual(rows, [
            ["1", "director=antigravity:big@max safety=antigravity:g@high crew=antigravity:g@high", "Selection 1",
             "Flight Director antigravity:big@max, Range Safety antigravity:g@high, Crew antigravity:g@high"],
            ["2", "new", "New", "Pick each role from the menus"]])

    def test_the_last_three_selections_are_kept_newest_first(self):
        for picks in (self.A, self.B, self.C):
            self.save(**picks)
        self.assertEqual(self.selections(), [self.C, self.B, self.A])
        rc, rows = self.remembered()
        self.assertEqual([r[1] for r in rows][-1], "new")
        self.assertEqual(len(rows), 4)  # three selections and "new": one question
        self.assertEqual([r[2] for r in rows], ["Selection 1", "Selection 2", "Selection 3", "New"])
        self.assertIn("crew=antigravity:small@high", rows[1][1])
        # A fourth, new selection drops the least recently used one.
        self.save(**self.D)
        self.assertEqual(self.selections(), [self.D, self.C, self.B])

    def test_saving_a_selection_already_kept_only_moves_it_up(self):
        for picks in (self.A, self.B, self.C):
            self.save(**picks)
        before = {json.dumps(x, sort_keys=True) for x in json.loads(self.roles_file.read_text())["selections"]}
        self.save(**self.A)
        after = json.loads(self.roles_file.read_text())["selections"]
        self.assertEqual(self.selections(), [self.A, self.C, self.B])
        self.assertEqual({json.dumps(x, sort_keys=True) for x in after}, before)  # no entry changed, added or lost
        # So the one used again is no longer the one a new selection drops.
        self.save(**self.D)
        self.assertEqual(self.selections(), [self.D, self.A, self.C])

    def test_a_selection_with_a_pick_the_catalog_lost_is_not_offered_but_stays_saved(self):
        self.save(crew="antigravity:gone@high")
        self.assertEqual(self.roles("remembered"), (1, "NONE"))
        self.save()
        rc, rows = self.remembered()
        self.assertEqual([r[1] for r in rows], [" ".join(f"{k}={v}" for k, v in GOOD.items()), "new"])
        self.assertEqual(len(self.selections()), 2)

    def test_a_picks_file_from_before_selections_is_one_selection(self):
        self.roles_file.parent.mkdir(parents=True)
        self.roles_file.write_text(json.dumps({**GOOD, "saved_at": "2026-11-20T10:00:00+00:00"}))
        rc, rows = self.remembered()
        self.assertEqual((rc, len(rows)), (0, 2))
        self.save(**self.B)
        self.assertEqual(self.selections(), [self.B, GOOD])
        self.assertEqual(json.loads(self.roles_file.read_text())["selections"][1]["saved_at"],
                         "2026-11-20T10:00:00+00:00")

    def test_the_latest_selection_is_also_where_an_older_version_reads_its_picks(self):
        self.save(**self.B)
        self.save()
        top = json.loads(self.roles_file.read_text())
        self.assertEqual({k: top[k] for k in GOOD}, GOOD)  # the file's top level, as before selections were kept
        self.assertEqual(top["saved_at"], top["selections"][0]["saved_at"])
        self.assertEqual(self.selections(), [GOOD, self.B])

    def test_an_unreadable_picks_file_offers_nothing_and_is_replaced_on_save(self):
        self.roles_file.parent.mkdir(parents=True)
        self.roles_file.write_text("{not json")
        self.assertEqual(self.roles("remembered"), (1, "NONE"))
        self.save()
        self.assertEqual(self.selections(), [GOOD])

    def test_save_writes_json_and_creates_folder(self):
        self.save()
        data = json.loads(self.roles_file.read_text())["selections"]
        self.assertEqual([{k: x[k] for k in GOOD} for x in data], [GOOD])
        self.assertIn("saved_at", data[0])
        self.assertEqual(self.roles("save", crew="bad")[0], 1)
        self.assertEqual(self.selections(), [GOOD])

    def test_menu_lists_broken_after_numbered(self):
        def broke(c):
            c["harnesses"]["xx"] = {**copy.deepcopy(c["harnesses"]["antigravity"]), "models": [], "broken": "garbled"}
        self.edit_catalog(broke)
        rc, out = self.roles("menu")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split("\t")[:2], ["1", "antigravity"])
        self.assertEqual(lines[1], "BROKEN xx: garbled")

    def test_harness_menu_shows_the_name_in_title_case_and_the_version_as_a_subtitle(self):
        self.catalog.write_text(CATALOG_MULTI.read_text())
        cat = json.loads(CATALOG_MULTI.read_text())
        rc, out = self.roles("menu")
        self.assertEqual(rc, 0)
        self.assertEqual([l.split("\t") for l in out.splitlines()],
                         [["1", "antigravity", "Antigravity", "v" + cat["harnesses"]["antigravity"]["version"]],
                          ["2", "claude", "Claude", "v" + cat["harnesses"]["claude"]["version"]]])
        # Model and effort rows have no subtitle; nor does a harness whose version is unknown.
        self.assertTrue(all(len(l.split("\t")) == 3 for l in self.roles("menu", "--harness", "claude")[1].splitlines()))
        cat["harnesses"]["claude"]["version"] = ""
        self.catalog.write_text(json.dumps(cat))
        self.assertEqual(self.roles("menu")[1].splitlines()[1].split("\t"), ["2", "claude", "Claude"])

    def multi_catalog(self, **auth):
        cat = json.loads(CATALOG_MULTI.read_text())
        for name, how in auth.items():
            cat["harnesses"][name]["auth"] = how
        self.catalog.write_text(json.dumps(cat))

    def test_menu_lists_only_the_session_harness_while_any_is_on_oauth(self):
        self.multi_catalog(antigravity="api-key", claude="oauth")
        for sess in ("antigravity", "claude"):
            rc, out = self.roles("menu", "--session-harness", sess)
            self.assertEqual(rc, 0)
            lines = out.splitlines()
            self.assertEqual(lines[0].split("\t")[:3], ["1", sess, sess.title()])
            self.assertEqual(lines[1], f"LOCKED {sess}: claude is signed in with OAuth, so every role runs on this "
                                       "session's harness")
            self.assertEqual(len(lines), 2)
        self.multi_catalog(antigravity="oauth", claude="oauth")
        self.assertIn("LOCKED claude: antigravity and claude are signed in with OAuth,",
                      self.roles("menu", "--session-harness", "claude")[1])

    def test_unknown_session_harness_is_never_guessed(self):
        # No override, and a harness that is neither an ancestor process nor in the environment.
        self.multi_catalog(antigravity="oauth", claude="oauth")
        picks = [x for k, v in GOOD.items() for x in (f"--{k}", v)]

        def run(*args):
            with unittest.mock.patch.dict(os.environ, {"MISSION_CONTROL_SESSION_HARNESS": ""}), \
                    unittest.mock.patch("harnesses._ancestor_names", return_value=["bash", "sshd"]), \
                    unittest.mock.patch.dict(os.environ, {k: "" for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                rc = roles_mod.main(list(args))
            return rc, out.getvalue().strip()
        rc, out = run("menu", "--catalog", str(self.catalog))
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("UNKNOWN session ? cannot tell which harness this session runs in; run this "
                                       "again with --session-harness"), out)
        rc, out = run("check", "--catalog", str(self.catalog), "--jira", "no", *picks)
        self.assertEqual(rc, 1)
        self.assertIn("UNKNOWN session ? cannot tell", out)
        self.assertEqual(out.count("cannot be checked: antigravity and claude are signed in with OAuth"), 3)
        self.assertEqual(run("check", "--catalog", str(self.catalog), "--jira", "no", *picks,
                             "--session-harness", "antigravity")[0], 0)

    def test_on_oauth_only_selections_on_the_session_harness_are_offered(self):
        on_claude = dict(director="claude:opus@high", safety="claude:sonnet@high", crew="claude:sonnet@medium")
        mixed = {**GOOD, "director": "claude:opus@high"}
        self.multi_catalog(antigravity="oauth", claude="oauth")
        for picks in (GOOD, on_claude, mixed):
            self.save(**picks)

        def offered(sess):
            return [r[1] for r in self.remembered("--session-harness", sess)[1]]
        want = lambda picks: " ".join(f"{k}={v}" for k, v in picks.items())
        self.assertEqual(offered("antigravity"), [want(GOOD), "new"])
        self.assertEqual(offered("claude"), [want(on_claude), "new"])
        # Without an OAuth sign-in all three are offered, the cross-harness one too.
        self.multi_catalog(antigravity="api-key", claude="api-key")
        self.assertEqual(offered("claude"), [want(mixed), want(on_claude), want(GOOD), "new"])
        self.assertEqual(len(self.selections()), 3)  # what a session can't use stays saved for the one that can

    def test_to_state_takes_the_selection_saved_last(self):
        self.save(**self.B)
        self.save()
        state = self.d / "state.json"
        state.write_text("{}")
        self.assertEqual(self.roles("to-state", "--state", str(state))[0], 0)
        self.assertEqual(json.loads(state.read_text())["ROLE_CREW"], GOOD["crew"])

    def test_menu_is_whole_without_an_oauth_sign_in(self):
        for auth in (dict(antigravity="api-key", claude="cloud"), {}):  # {}: a catalog from before auth was recorded
            self.multi_catalog(**auth)
            rc, out = self.roles("menu", "--session-harness", "claude")
            self.assertEqual([l.split("\t")[1] for l in out.splitlines()], ["antigravity", "claude"])

    def test_one_harness_on_oauth_needs_no_locked_line(self):
        self.edit_catalog(lambda c: c["harnesses"]["antigravity"].update(auth="oauth"))
        rc, out = self.roles("menu", "--session-harness", "antigravity")
        self.assertEqual((rc, len(out.splitlines())), (0, 1))

    def test_a_broken_harness_on_oauth_does_not_lock(self):
        def broke(c):
            c["harnesses"]["xx"] = {**copy.deepcopy(c["harnesses"]["antigravity"]), "models": [], "broken": "garbled",
                                    "auth": "oauth"}
        self.edit_catalog(broke)
        self.assertNotIn("LOCKED", self.roles("menu", "--session-harness", "antigravity")[1])

    def test_menu_with_a_session_harness_the_lock_cannot_use(self):
        self.multi_catalog(claude="oauth")
        rc, out = self.roles("menu", "--session-harness", "agy")
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("UNKNOWN session harness agy (usable: antigravity, claude); claude is signed "
                                       "in with OAuth"), out)

    def test_check_keeps_every_role_on_the_session_harness_while_any_is_on_oauth(self):
        mixed = dict(director="claude:opus@high", safety="antigravity:g@high", crew="antigravity:g@medium")
        self.multi_catalog(antigravity="api-key", claude="oauth")
        rc, out = self.roles("check", "--session-harness", "claude", **mixed)
        self.assertEqual(rc, 1)
        self.assertIn("OK director claude:opus@high", out)
        for role, pair in (("safety", "antigravity:g@high"), ("crew", "antigravity:g@medium")):
            self.assertIn(f"UNKNOWN {role} {pair} is not on this session's harness (claude), and claude is signed in "
                          "with OAuth, so every role runs on the session's harness; pick it on claude", out)
        self.assertNotIn("MISSING", out)
        same = dict(director="claude:opus@high", safety="claude:sonnet@high", crew="claude:sonnet@medium")
        self.assertEqual(self.roles("check", "--session-harness", "claude", **same)[0], 0)
        # Picks that agree with each other but not with the session: a run continued from the other harness.
        rc, out = self.roles("check", "--session-harness", "antigravity", **same)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("is not on this session's harness (antigravity)"), 3)
        self.multi_catalog(antigravity="api-key", claude="api-key")
        self.assertEqual(self.roles("check", "--session-harness", "claude", **mixed)[0], 0)

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

    def test_safety_needs_spec_drift_in_a_full_run_only(self):
        # The drift check (run_finish.py) runs on the Range Safety pair.
        self.edit_catalog(lambda c: c["harnesses"]["antigravity"]["skills"].pop("spec-drift"))
        rc, out = self.roles("check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING safety antigravity:g@high spec-drift in antigravity", out)
        self.assertEqual(self.roles("check", "--mode", "launch-plan")[0], 0)

    def test_execute_plan_mode_requires_execution_skills_only(self):
        def strip_design(c):
            for s in ("mission", "mission-brief", "flight-plan", "wind-tunnel", "flight-readiness", "cross-check",
                      "spec-drift"):
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
