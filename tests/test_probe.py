"""probe.py: synchronous test launches with launch-style fallback against fake_agy."""
import json
import unittest
from pathlib import Path

from helpers import Base, SKILL, sh

RULE = lambda model, effort: {"model": model, "effort": effort}
BIG_OK = RULE("big", "high")
G_FLAG = [RULE("g", "high"), RULE("g", "xhigh")]


class ProbeTest(Base):
    SCRIPT = SKILL / "scripts" / "probe.py"

    def setUp(self):
        super().setUp()
        self.docswt = self.tmp / "docswt"
        sh("git", "-C", str(self.main), "worktree", "add", "-q", "-b", "docsbr", str(self.docswt), "feature")
        s = self.st()
        s.update({"ROLE_DIRECTOR": "antigravity:big@high", "ROLE_SAFETY": "antigravity:g@high",
                  "ROLE_CREW": "antigravity:g@high", "CREW_TOP": "xhigh",
                  "CATALOG": str(SKILL / "tests" / "fixtures" / "catalog.json"),
                  "DOCS_WORKTREE": str(self.docswt)})
        self.state.write_text(json.dumps(s))

    def rules(self, rules):
        w = json.loads(self.world.read_text())
        w["launch_rules"] = rules
        self.world.write_text(json.dumps(w))

    def expect_probe(self, code, prefix, *extra):
        return self.expect(code, prefix, *extra)[0]

    def test_all_pass_records_styles(self):
        self.behave("ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "step1")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_g_high"), "slug")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_big_high"), "slug")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_g_xhigh"), "slug")
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c["prompt"].startswith("This is a launch test.") for c in calls))
        # Test launches are read-only workers: the sandbox profile, never the skip-permissions flag.
        self.assertTrue(all("--sandbox" in c["argv"] and "--dangerously-skip-permissions" not in c["argv"]
                            for c in calls))

    def test_slug_rejected_falls_back_to_flag(self):
        self.rules([RULE("big-high", "default")] + G_FLAG)
        self.behave("ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "step1")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_g_high"), "flag")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_g_xhigh"), "flag")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_big_high"), "slug")
        self.assertIn("is not available", (self.run_dir / "results" / "probe-antigravity_g_high.md").read_text())

    def test_every_style_rejected_fails_with_reasons(self):
        self.rules([RULE("big-high", "default"), RULE("g", "xhigh")])
        self.behave("ok", "ok")
        last = self.expect_probe(40, "PROBE_FAILED", "--set", "step1")
        self.assertTrue(last.startswith("PROBE_FAILED antigravity:g@high "), last)
        text = Path(last.split()[-1]).read_text()
        self.assertIn("is not available", text)
        self.assertIn("slug", text)
        self.assertIn("flag", text)

    def test_agy_refusing_a_launch_counts_as_that_style_failing(self):
        w = json.loads(self.world.read_text())
        w["reject_rules"] = [RULE("big-high", "default"), RULE("big", "high")]
        self.world.write_text(json.dumps(w))
        self.behave("ok", "ok", "ok")
        last = self.expect_probe(40, "PROBE_FAILED", "--set", "step1")
        self.assertTrue(last.startswith("PROBE_FAILED antigravity:big@high "), last)
        self.assertIn("does not support effort high", Path(last.split()[-1]).read_text())

    def test_step3_failure_of_an_architect_chosen_pair_goes_to_fix_meta(self):
        w = json.loads(self.world.read_text())
        w["reject_rules"] = [RULE("small-high", "default"), RULE("small", "high")]
        self.world.write_text(json.dumps(w))
        s = self.st()
        s["GROUND_CREW_PAIR"] = "antigravity:small@high"
        self.state.write_text(json.dumps(s))
        last = self.expect_probe(21, "FIX_META", "--set", "step3")
        text = Path(last.split()[1]).read_text()
        self.assertIn("PROBLEM 01: REVIEW_1 antigravity:small@high failed its test launch; see ", text)
        self.assertIn("PROBLEM pair 'antigravity:small@high': GROUND_CREW_PAIR failed its test launch; see ", text)
        for t in ("01", "02"):
            m = self.docs / "tasks" / f"{t}.meta"
            m.write_text(m.read_text().replace("antigravity:small@high", "antigravity:big@high"))
        s = self.st()
        s["GROUND_CREW_PAIR"] = "antigravity:big@high"
        self.state.write_text(json.dumps(s))
        self.behave("ok")
        self.expect_probe(0, "PROBED", "--set", "step3")

    def test_probe_that_edits_docs_is_discarded(self):
        self.behave("ok-edit", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "step1")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_big_high"), "slug")
        self.assertEqual(sh("git", "-C", str(self.docswt), "status", "--porcelain"), "")
        self.assertIn("discarded", (self.run_dir / "mission-log.md").read_text())

    def test_pair_set_and_skip_known(self):
        self.behave("ok")
        self.expect_probe(0, "PROBED", "--set", "pair", "--pair", "antigravity:g@low")
        self.expect_probe(0, "PROBED", "--set", "pair", "--pair", "antigravity:g@low")
        self.assertEqual(len(self.calls()), 1)

    def test_resume_reprobes_step1_and_step3_pairs(self):
        self.behave("ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "step1")
        s = self.st()
        s["GROUND_CREW_PAIR"] = "antigravity:big@low"
        self.state.write_text(json.dumps(s))
        self.behave("ok", "ok", "ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "resume")
        self.assertEqual(len(self.calls()), 8)
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_big_low"), "slug")
        self.expect_probe(0, "PROBED")
        self.assertEqual(len(self.calls()), 8)

    def test_resume_overwrites_or_fails_a_recorded_style(self):
        s = self.st()
        s.update({"LAUNCH_STYLE_antigravity_g_high": "flag", "LAUNCH_STYLE_antigravity_g_xhigh": "flag",
                  "LAUNCH_STYLE_antigravity_big_high": "flag", "LAUNCH_STYLE_antigravity_small_high": "flag"})
        self.state.write_text(json.dumps(s))
        self.behave("ok", "ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "resume")
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_g_high"), "slug")
        self.rules([RULE("big-high", "default"), RULE("small-high", "default")])
        self.behave("ok")
        last = self.expect_probe(40, "PROBE_FAILED", "--set", "resume")
        self.assertTrue(last.startswith("PROBE_FAILED antigravity:g@"))

    def test_resume_never_discards_the_directors_work(self):
        (self.docswt / "notes.md").write_text("uncommitted flight director notes\n")
        self.behave("ok", "ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "resume")
        self.assertEqual((self.docswt / "notes.md").read_text(), "uncommitted flight director notes\n")
        self.assertIn("left as is", (self.run_dir / "mission-log.md").read_text())
        self.assertIn("left as is", (self.run_dir / "results" / "probe-antigravity_big_high.md").read_text())

    def test_old_format_state_is_refused(self):
        s = self.st()
        del s["ROLE_DIRECTOR"]
        self.state.write_text(json.dumps(s))
        self.expect_probe(2, "ERROR", "--set", "step1")

    def test_resume_if_changed_skips_when_versions_and_pairs_unchanged(self):
        self.behave("ok", "ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "step1")
        self.expect_probe(0, "PROBED", "--set", "step3")
        before = len(self.calls())
        self.expect_probe(0, "PROBED", "--set", "resume", "--if-changed")
        self.assertEqual(len(self.calls()), before)
        self.assertIn("reusing recorded launch styles", (self.run_dir / "mission-log.md").read_text())

    def test_resume_if_changed_probes_only_new_pair_when_version_unchanged(self):
        self.behave("ok", "ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "step1")
        self.expect_probe(0, "PROBED", "--set", "step3")
        before = len(self.calls())
        s = self.st()
        s["GROUND_CREW_PAIR"] = "antigravity:big@low"
        self.state.write_text(json.dumps(s))
        self.behave("ok")
        self.expect_probe(0, "PROBED", "--set", "resume", "--if-changed")
        self.assertEqual(len(self.calls()), before + 1)
        self.assertEqual(self.st("LAUNCH_STYLE_antigravity_big_low"), "slug")

    def test_resume_if_changed_reprobes_when_harness_version_changes(self):
        self.behave("ok", "ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "step1")
        self.expect_probe(0, "PROBED", "--set", "step3")
        before = len(self.calls())
        cat_path = self.run_dir / "catalog-bumped.json"
        cat = json.loads(Path(self.st("CATALOG")).read_text())
        cat["harnesses"]["antigravity"]["version"] = "9.9.9"
        cat_path.write_text(json.dumps(cat))
        s = self.st()
        s["CATALOG"] = str(cat_path)
        self.state.write_text(json.dumps(s))
        self.behave("ok", "ok", "ok", "ok")
        self.expect_probe(0, "PROBED", "--set", "resume", "--if-changed")
        self.assertEqual(len(self.calls()), before + 4)
        self.assertEqual(self.st("PROBE_HARNESS_VERSIONS"), "antigravity:9.9.9")


if __name__ == "__main__":
    unittest.main()
