"""probe.py: test launches with launch-style fallback, against the fake Orca."""
import json
import unittest
from pathlib import Path

from helpers import Base, SKILL, sh

RULE = lambda agent, model, effort: {"agent": agent, "model": model, "effort": effort}
CL_OK = RULE("cl-agent", "big", "high")
AG_FLAG = [RULE("ag-agent", "g", "high"), RULE("ag-agent", "g", "xhigh")]


class ProbeTest(Base):
    SCRIPT = SKILL / "scripts" / "probe.py"

    def setUp(self):
        super().setUp()
        self.docswt = self.tmp / "docswt"
        sh("git", "-C", str(self.main), "worktree", "add", "-q", "-b", "docsbr", str(self.docswt), "feature")
        s = self.st()
        s.update({"ROLE_ARCHITECT": "cl:big@high", "ROLE_EGM": "ag:g@high", "ROLE_BASE": "ag:g@high",
                  "BASE_TOP": "xhigh", "CATALOG": str(SKILL / "tests" / "fixtures" / "catalog.json"),
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
        self.expect_probe(10, "WAIT", "--set", "step1")
        self.expect_probe(0, "PROBED")
        self.assertEqual(self.st("LAUNCH_STYLE_ag_g_high"), "slug")
        self.assertEqual(self.st("LAUNCH_STYLE_cl_big_high"), "flag")
        self.assertEqual(self.st("LAUNCH_STYLE_ag_g_xhigh"), "slug")
        self.assertEqual(len(self.calls("worker-start")), 3)
        spec = next(iter(json.loads(self.world.read_text())["tasks"].values()))
        self.assertEqual(spec["title"], "Launch test: cl:big@high")
        self.assertTrue(spec["spec"].startswith("This is a launch test."))

    def test_slug_rejected_falls_back_to_flag(self):
        self.rules([CL_OK] + AG_FLAG)
        self.behave("ok", "ok", "ok")
        self.expect_probe(10, "WAIT", "--set", "step1")
        self.until(0, "PROBED")
        self.assertEqual(self.st("LAUNCH_STYLE_ag_g_high"), "flag")
        self.assertEqual(self.st("LAUNCH_STYLE_ag_g_xhigh"), "flag")
        self.assertEqual(self.st("LAUNCH_STYLE_cl_big_high"), "flag")
        self.assertIn("is not available", (self.run_dir / "results" / "probe-ag_g_high.md").read_text())

    def test_every_style_rejected_fails_with_reasons(self):
        self.rules([CL_OK, RULE("ag-agent", "g", "xhigh")])
        self.behave("ok", "ok")
        self.expect_probe(10, "WAIT", "--set", "step1")
        last, _ = self.until(40, "PROBE_FAILED")
        self.assertTrue(last.startswith("PROBE_FAILED ag:g@high "))
        text = Path(last.split()[-1]).read_text()
        self.assertIn("is not available", text)
        self.assertIn("slug", text)
        self.assertIn("flag", text)

    def test_advance_is_idempotent_while_waiting(self):
        self.behave("pending", "ok", "ok")
        first = self.expect_probe(10, "WAIT", "--set", "step1")
        again = self.expect_probe(10, "WAIT")
        self.assertEqual(first, again)
        self.assertEqual(len(self.calls("worker-start")), 3)

    def test_probe_that_edits_docs_is_discarded(self):
        self.behave("ok-edit", "ok", "ok")
        self.expect_probe(10, "WAIT", "--set", "step1")
        self.expect_probe(0, "PROBED")
        self.assertEqual(self.st("LAUNCH_STYLE_cl_big_high"), "flag")
        self.assertEqual(sh("git", "-C", str(self.docswt), "status", "--porcelain"), "")
        self.assertIn("discarded", (self.run_dir / "run-log.md").read_text())

    def test_idle_nudged_once_then_counts_as_failure(self):
        self.rules([CL_OK] + AG_FLAG)
        self.behave("ok", "ok", "idle-forever")
        self.expect_probe(10, "WAIT", "--set", "step1")
        last, _ = self.until(40, "PROBE_FAILED")
        self.assertTrue(last.startswith("PROBE_FAILED ag:g@xhigh "))
        self.assertEqual(len(self.calls("send")), 1)
        self.assertEqual(self.st("LAUNCH_STYLE_ag_g_high"), "flag")

    def test_pair_set_and_skip_known(self):
        self.behave("ok")
        self.expect_probe(10, "WAIT", "--set", "pair", "--pair", "ag:g@low")
        self.expect_probe(0, "PROBED")
        self.expect_probe(0, "PROBED", "--set", "pair", "--pair", "ag:g@low")
        self.assertEqual(len(self.calls("worker-start")), 1)

    def test_old_format_state_is_refused(self):
        s = self.st()
        del s["ROLE_ARCHITECT"]
        self.state.write_text(json.dumps(s))
        self.expect_probe(2, "ERROR", "--set", "step1")


if __name__ == "__main__":
    unittest.main()
