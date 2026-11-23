import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import removed_after

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_meta.py"
CATALOG = Path(__file__).resolve().parent / "fixtures" / "catalog.json"
DEFAULT = dict(TITLE="t", TIER="standard", SPEC_TYPE="checklist", ACS="AC-1",
               CREW="antigravity:g@high", REVIEW_1="antigravity:small@high", REVIEW_2="antigravity:small@high")


class ValidateMetaTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(removed_after(self, tempfile.mkdtemp(prefix="vmeta-")))
        self.tasks = self.d / "tasks"
        self.tasks.mkdir()
        self.state_path = self.d / "state.json"
        self.state()

    def state(self, **over):
        s = {"CATALOG": str(CATALOG), "ROLE_DIRECTOR": "antigravity:big@high", "ROLE_CREW": "antigravity:g@high",
             "CREW_TOP": "xhigh", **over}
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
        self.meta("01", REVIEW_1="antigravity:small@ultra")
        self.assertProblem("REVIEW_1 'antigravity:small@ultra'", "low medium high xhigh max")

    def test_reviewer_model_not_detected(self):
        self.meta("01", REVIEW_2="antigravity:tiny@high")
        self.assertProblem("REVIEW_2 'antigravity:tiny@high'", "small")

    def test_reviewer_from_other_harness_rejected(self):
        self.meta("01", REVIEW_1="other:g@high")
        self.assertProblem("flight director's harness")

    def test_bad_pair_shape(self):
        self.meta("01", REVIEW_1="nonsense")
        self.assertProblem("REVIEW_1", "bad pair")

    def test_complex_review2_matches_director_model_any_effort(self):
        self.meta("01", TIER="complex", REVIEW_2="antigravity:big@low")
        self.assertOK()
        self.meta("01", TIER="complex", REVIEW_2="antigravity:small@max")
        self.assertProblem("REVIEW_2")

    def test_complex_judgment_needs_review1_on_director_model(self):
        self.meta("01", TIER="complex", SPEC_TYPE="judgment", REVIEW_1="antigravity:small@max", REVIEW_2="antigravity:big@high")
        self.assertProblem("REVIEW_1")
        self.meta("01", TIER="complex", SPEC_TYPE="judgment", REVIEW_1="antigravity:big@medium", REVIEW_2="antigravity:big@high")
        self.assertOK()

    def test_standard_may_use_any_reviewer(self):
        self.meta("01", REVIEW_1="antigravity:big@max", REVIEW_2="antigravity:small@low")
        self.assertOK()

    def test_first_crew_options(self):
        self.meta("01", CREW="antigravity:g@xhigh")
        self.assertOK()
        self.meta("01", CREW="antigravity:g@low")
        self.assertProblem("CREW")
        self.meta("01", CREW="antigravity:big@high")
        self.assertProblem("never implements first")

    def test_shared_model_blocks_top_start(self):
        self.state(ROLE_DIRECTOR="antigravity:g@xhigh")
        self.meta("01", CREW="antigravity:g@xhigh")
        self.assertProblem("never implements first")
        self.meta("01", CREW="antigravity:g@high", REVIEW_1="antigravity:g@low", REVIEW_2="antigravity:g@low")
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
        self.assertPair("antigravity:g@xhigh", attempt=1, ok=True)
        self.assertPair("antigravity:big@high", attempt=1, ok=False)
        self.assertPair("antigravity:big@high", attempt=2, ok=True)
        self.assertPair("antigravity:small@low", attempt=3, ok=False)
        self.assertPair("antigravity:small@low", attempt=3, past="yes", ok=True)
        self.assertPair("antigravity:small@ultra", attempt=3, past="yes", ok=False)
        self.assertPair("zz:g@high", attempt=3, past="yes", ok=False)

    def test_escalation_rung_depends_on_first_crew(self):
        self.meta("01", CREW="antigravity:g@xhigh")
        rc, out = self.run_cli("pair", "--state", str(self.state_path), "--task-meta", str(self.tasks / "01.meta"),
                               "--pair", "antigravity:big@high", "--attempt", "1", "--past-ladder", "no")
        self.assertEqual(rc, 0, out)

    def test_first_rung_is_crew_top_unless_director_shares_it(self):
        self.state(ROLE_DIRECTOR="antigravity:g@xhigh", ROLE_CREW="antigravity:g@high")
        self.assertPair("antigravity:g@xhigh", attempt=1, ok=True)
        self.assertPair("antigravity:g@high", attempt=1, ok=False)

    def test_harness_pair(self):
        def hp(pair):
            return self.run_cli("harness-pair", "--state", str(self.state_path), "--pair", pair)
        self.assertEqual(hp("antigravity:small@low")[0], 0)
        self.assertEqual(hp("antigravity:small@ultra")[0], 1)
        rc, out = hp("other:g@high")
        self.assertEqual(rc, 1)
        self.assertIn("flight director's harness", out)

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


if __name__ == "__main__":
    unittest.main()
