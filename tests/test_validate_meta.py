import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_meta.py"
CATALOG = Path(__file__).resolve().parent / "fixtures" / "catalog.json"
DEFAULT = dict(TITLE="t", TIER="standard", SPEC_TYPE="checklist", ACS="AC-1",
               IMPLEMENTER="ag:g@high", REVIEW_1="cl:small@high", REVIEW_2="cl:small@high")


class ValidateMetaTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="vmeta-"))
        self.tasks = self.d / "tasks"
        self.tasks.mkdir()
        self.state_path = self.d / "state.json"
        self.state()

    def state(self, **over):
        s = {"CATALOG": str(CATALOG), "ROLE_ARCHITECT": "cl:big@high", "ROLE_BASE": "ag:g@high",
             "BASE_TOP": "xhigh", **over}
        self.state_path.write_text(json.dumps(s))

    def meta(self, tid, acs=None, extra="", **over):
        v = {**DEFAULT, **over}
        if acs is not None:
            v["ACS"] = acs
        text = "".join(f"{k}={x}\n" for k, x in v.items()) + extra
        (self.tasks / f"{tid}.meta").write_text(text)
        for s in (".md", "-summary.md", "-decisions.md"):
            (self.tasks / f"{tid}{s}").write_text("x\n")

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True)
        return r.returncode, r.stdout

    def tasks_cmd(self, ids="01", *extra):
        return self.run_cli("tasks", "--state", str(self.state_path), "--tasks-dir", str(self.tasks), "--ids", ids, *extra)

    def assertOK(self, ids="01", *extra):
        rc, out = self.tasks_cmd(ids, *extra)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("PROBLEM", out)

    def assertProblem(self, *subs, ids="01", extra=()):
        rc, out = self.tasks_cmd(ids, *extra)
        self.assertEqual(rc, 1, out)
        lines = [l for l in out.splitlines() if l.startswith("PROBLEM")]
        self.assertTrue(any(all(s in l for s in subs) for l in lines), f"{subs} not in one line of {out}")

    def assertPair(self, pair, attempt, past="no", ok=True):
        self.meta("01")
        rc, out = self.run_cli("pair", "--state", str(self.state_path), "--task-meta", str(self.tasks / "01.meta"),
                               "--pair", pair, "--attempt", str(attempt), "--past-ladder", past)
        self.assertEqual(rc, 0 if ok else 1, out)
        self.assertIn("OK" if ok else "PROBLEM", out)

    def test_default_meta_ok(self):
        self.meta("01")
        self.assertOK()

    def test_missing_key(self):
        (self.tasks / "01.meta").write_text("TITLE=t\n")
        self.assertProblem("01: missing TIER")

    def test_reviewer_effort_not_detected_is_rejected(self):
        self.meta("01", REVIEW_1="cl:small@ultra")
        self.assertProblem("REVIEW_1 'cl:small@ultra'", "low medium high xhigh max")

    def test_reviewer_model_not_detected(self):
        self.meta("01", REVIEW_2="cl:tiny@high")
        self.assertProblem("REVIEW_2 'cl:tiny@high'", "small")

    def test_reviewer_from_other_harness_rejected(self):
        self.meta("01", REVIEW_1="ag:g@high")
        self.assertProblem("architect's harness")

    def test_bad_pair_shape(self):
        self.meta("01", REVIEW_1="nonsense")
        self.assertProblem("REVIEW_1", "bad pair")

    def test_complex_review2_matches_architect_model_any_effort(self):
        self.meta("01", TIER="complex", REVIEW_2="cl:big@low")
        self.assertOK()
        self.meta("01", TIER="complex", REVIEW_2="cl:small@max")
        self.assertProblem("REVIEW_2")

    def test_complex_judgment_needs_review1_on_architect_model(self):
        self.meta("01", TIER="complex", SPEC_TYPE="judgment", REVIEW_1="cl:small@max", REVIEW_2="cl:big@high")
        self.assertProblem("REVIEW_1")
        self.meta("01", TIER="complex", SPEC_TYPE="judgment", REVIEW_1="cl:big@medium", REVIEW_2="cl:big@high")
        self.assertOK()

    def test_standard_may_use_any_reviewer(self):
        self.meta("01", REVIEW_1="cl:big@max", REVIEW_2="cl:small@low")
        self.assertOK()

    def test_first_implementer_options(self):
        self.meta("01", IMPLEMENTER="ag:g@xhigh")
        self.assertOK()
        self.meta("01", IMPLEMENTER="ag:g@low")
        self.assertProblem("IMPLEMENTER")
        self.meta("01", IMPLEMENTER="cl:big@high")
        self.assertProblem("never implements first")

    def test_shared_model_blocks_top_start(self):
        self.state(ROLE_ARCHITECT="ag:g@xhigh")
        self.meta("01", IMPLEMENTER="ag:g@xhigh")
        self.assertProblem("never implements first")
        self.meta("01", IMPLEMENTER="ag:g@high", REVIEW_1="ag:g@low", REVIEW_2="ag:g@low")
        self.assertOK()

    def test_ladder_key_rejected(self):
        self.meta("01", extra="LADDER=x\n")
        self.assertProblem("LADDER is no longer used; delete it")

    def test_bad_tier_and_spec(self):
        self.meta("01", TIER="huge", SPEC_TYPE="vibes")
        self.assertProblem("TIER")
        self.assertProblem("SPEC_TYPE")

    def test_missing_companion_file(self):
        self.meta("01")
        (self.tasks / "01-summary.md").unlink()
        self.assertProblem("01-summary.md not found")

    def test_escalation_pair_must_be_rung(self):
        self.assertPair("ag:g@xhigh", attempt=1, ok=True)
        self.assertPair("cl:big@high", attempt=1, ok=False)
        self.assertPair("cl:big@high", attempt=2, ok=True)
        self.assertPair("cl:small@low", attempt=3, ok=False)
        self.assertPair("cl:small@low", attempt=3, past="yes", ok=True)
        self.assertPair("cl:small@ultra", attempt=3, past="yes", ok=False)
        self.assertPair("zz:g@high", attempt=3, past="yes", ok=False)

    def test_escalation_rung_depends_on_first_implementer(self):
        self.meta("01", IMPLEMENTER="ag:g@xhigh")
        rc, out = self.run_cli("pair", "--state", str(self.state_path), "--task-meta", str(self.tasks / "01.meta"),
                               "--pair", "cl:big@high", "--attempt", "1", "--past-ladder", "no")
        self.assertEqual(rc, 0, out)

    def test_harness_pair(self):
        def hp(pair):
            return self.run_cli("harness-pair", "--state", str(self.state_path), "--pair", pair)
        self.assertEqual(hp("cl:small@low")[0], 0)
        self.assertEqual(hp("cl:small@ultra")[0], 1)
        rc, out = hp("ag:g@high")
        self.assertEqual(rc, 1)
        self.assertIn("architect's harness", out)

    # coverage logic (ported from tests/test_scripts.sh)
    def design(self):
        f = self.d / "design.md"
        f.write_text("AC-1 does x\nAC-2 does y\nAC-3 does z\n")
        return ("--design", str(f))

    def test_full_coverage_passes(self):
        self.meta("01", "AC-1")
        self.meta("02", "AC-2 AC-3")
        self.assertOK("01 02", *self.design())

    def test_missing_ac_fails_and_is_named(self):
        self.meta("01", "AC-1")
        self.meta("02", "AC-2")
        self.assertProblem("AC-3 is in the design but in no task", ids="01 02", extra=self.design())

    def test_unknown_ac_flagged(self):
        self.meta("01", "AC-1")
        self.meta("02", "AC-2 AC-3 AC-9")
        self.assertProblem("AC-9 is in a task's ACS but not in the design", ids="01 02", extra=self.design())

    def test_replan_must_keep_acs(self):
        self.meta("02", "AC-2 AC-3")
        self.meta("02a", "AC-2", extra="REPLANNED_FROM=02\n")
        self.assertProblem("AC-3 was in 02's ACS", ids="02a", extra=("--replanned-from", "02"))

    def test_replan_needs_replanned_from_key(self):
        self.meta("02", "AC-2")
        self.meta("02a", "AC-2")
        self.assertProblem("needs REPLANNED_FROM=02", ids="02a", extra=("--replanned-from", "02"))

    def test_acs_none_allowed(self):
        self.meta("F1", "none")
        self.assertOK("F1")

    def test_empty_acs_rejected(self):
        self.meta("03", "")
        self.assertProblem("ACS must be", ids="03")

    def test_legacy_gemini_model_mode_still_works(self):
        (self.tasks / "05.meta").write_text(
            "TITLE=t\nTIER=standard\nSPEC_TYPE=checklist\nACS=AC-1\nIMPLEMENTER=antigravity:g-high\n"
            "REVIEW_1=claude:sonnet\nREVIEW_2=claude:sonnet\nLADDER=antigravity:g-xhigh claude:sonnet\n")
        for s in (".md", "-summary.md", "-decisions.md"):
            (self.tasks / f"05{s}").write_text("x\n")
        rc, out = self.run_cli("tasks", "--tasks-dir", str(self.tasks), "--gemini-model", "g", "--ids", "05")
        self.assertEqual((rc, out.strip()), (0, "OK 05"))
        rc, out = self.run_cli("pair", "--pair", "claude:opus", "--tier", "complex", "--gemini-model", "g", "--allow-opus", "no")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
