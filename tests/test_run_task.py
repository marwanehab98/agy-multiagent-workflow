"""Tests for scripts/run_task.py against the fake Orca CLI. Run: python3 -I tests/test_run_task.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
RUN_TASK = SKILL / "scripts" / "run_task.py"
FAKE = SKILL / "tests" / "fake_orca.py"

META = """TITLE={title}
TIER=standard
SPEC_TYPE=checklist
ACS={acs}
IMPLEMENTER=antigravity:g-high
REVIEW_1=claude:sonnet
REVIEW_2=claude:sonnet
LADDER=antigravity:g-xhigh claude:sonnet
{extra}"""


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="runtask-"))
        t = self.tmp
        self.main = t / "repo"
        self.main.mkdir()
        g = lambda *a: sh("git", "-C", str(self.main), "-c", "user.email=t@t", "-c", "user.name=t", *a)
        g("init", "-q", "-b", "main")
        (self.main / "a.txt").write_text("a\n")
        (self.main / ".gitignore").write_text("cache/\n")
        g("add", ".")
        g("commit", "-qm", "init")
        self.base = g("rev-parse", "HEAD")
        g("branch", "feature", self.base)
        self.impl = t / "impl"
        g("worktree", "add", "-q", "-b", "impl", str(self.impl), "feature")
        self.docs = t / "docs"
        (self.docs / "tasks").mkdir(parents=True)
        self.run_dir = t / "run"
        for d in ("specs", "verdicts", "gates", "results"):
            (self.run_dir / d).mkdir(parents=True)
        self.task("01", "AC-1")
        self.task("02", "AC-2")
        (self.run_dir / "queue.txt").write_text("01\n")
        rv = self.docs / "run-values.txt"
        rv.write_text("FEATURE_BRANCH=feature\nVERIFY_1=test ! -e BROKEN\n")
        orca = t / "fake-orca"
        orca.write_text(f'#!/bin/sh\nexec {sys.executable} -I {FAKE} "$@"\n')
        orca.chmod(0o755)
        self.world = t / "world.json"
        self.world.write_text(json.dumps({"seq": 0, "messages": [], "tasks": {}, "dispatches": {},
                                          "behaviors": [], "calls": []}))
        self.state = self.run_dir / "state.json"
        self.state.write_text(json.dumps({
            "ORCA": str(orca), "RUN_ID": "run_fake", "RUN_DIR": str(self.run_dir), "DOCS_DIR": str(self.docs),
            "IMPL_WORKTREE": str(self.impl), "MAIN_CHECKOUT": str(self.main), "FEATURE_BRANCH": "feature",
            "LAST_MERGED": self.base, "GEMINI_MODEL": "g", "RUN_VALUES": str(rv),
            "CONVENTION_PATHS": "CONTRIBUTING.md"}))

    def task(self, tid, acs, extra=""):
        d = self.docs / "tasks"
        (d / f"{tid}.md").write_text(f"Task {tid}\nObservable Acceptance: delivers {acs}\n")
        (d / f"{tid}.meta").write_text(META.format(title=f"Task {tid}", acs=acs, extra=extra))
        (d / f"{tid}-summary.md").write_text(f"Summary {tid}\n")
        (d / f"{tid}-decisions.md").write_text("none\n")

    def behave(self, *items):
        w = json.loads(self.world.read_text())
        w["behaviors"] += [{"do": i} if isinstance(i, str) else i for i in items]
        self.world.write_text(json.dumps(w))

    def adv(self, *extra):
        r = subprocess.run([sys.executable, "-I", str(RUN_TASK), "--state", str(self.state), "advance", *extra],
                           capture_output=True, text=True, env={**os.environ, "FAKE_ORCA_WORLD": str(self.world)})
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines, f"no output; stderr={r.stderr}")
        return r.returncode, lines[-1], lines

    def expect(self, code, prefix, *extra):
        rc, last, lines = self.adv(*extra)
        self.assertEqual((rc, last.split()[0]), (code, prefix), f"got {rc} {lines}")
        return last, lines

    def st(self, key=None):
        s = json.loads(self.state.read_text())
        return s.get(key) if key else s

    def calls(self, cmd):
        return [c for c in json.loads(self.world.read_text())["calls"] if c[0] == cmd]

    def result_file(self, text):
        f = self.tmp / f"result-{len(list(self.tmp.glob('result-*')))}.md"
        f.write_text(text + "\n")
        return str(f)

    def feature(self):
        return sh("git", "-C", str(self.main), "rev-parse", "feature")

    def impl_head(self):
        return sh("git", "-C", str(self.impl), "rev-parse", "HEAD")

    def run_one_task_clean(self):
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")


class HappyPath(Base):
    def test_two_tasks_merge_in_order(self):
        (self.run_dir / "queue.txt").write_text("01 02\n")
        self.behave("commit", "pass", "pass", "commit", "pass", "pass")
        self.assertIn(" impl", self.expect(10, "WAIT")[0])
        self.assertIn(" review1", self.expect(10, "WAIT")[0])
        self.assertIn(" review2", self.expect(10, "WAIT")[0])
        last, lines = self.expect(10, "WAIT")
        self.assertTrue(lines[0].startswith("MERGED 01 "), lines)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        _, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(lines[0].startswith("MERGED 02 "))
        self.assertEqual(self.feature(), self.impl_head())
        self.assertEqual(sh("git", "-C", str(self.main), "branch", "--show-current"), "main")
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), [])
        starts = self.calls("worker-start")
        impl = [c for c in starts if "antigravity" in c]
        self.assertEqual(len(impl), 2)
        self.assertTrue(all("--keep-open" in c and "g-high" in c for c in impl))
        reviews = [c for c in starts if "claude" in c]
        self.assertEqual(len(reviews), 4)
        self.assertTrue(all("--keep-open" not in c and "sonnet" in c for c in reviews))
        self.assertEqual(self.st("WAITING_ON"), None)

    def test_wait_is_idempotent_while_worker_runs(self):
        self.behave("commit", "pending")
        self.expect(10, "WAIT")
        first, _ = self.expect(10, "WAIT")
        again, _ = self.expect(10, "WAIT")
        self.assertEqual(first, again)
        self.assertEqual(len(self.calls("worker-start")), 2)


class GateAndFixes(Base):
    def test_gate_failure_goes_to_fix_on_same_terminal(self):
        self.behave("commit-broken", "unbreak", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        fix_start = self.calls("worker-start")[1]
        self.assertIn("--terminal", fix_start)
        self.assertTrue(list((self.run_dir / "gates").glob("01-*.log")))
        self.assertIn(" review1", self.expect(10, "WAIT")[0])
        self.assertEqual(self.st("FIX_ROUNDS_01"), "0")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("LOOP_01"), "merged")

    def test_fix_falls_back_to_fresh_worker_when_released(self):
        self.behave("commit", "fix", "commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        w = json.loads(self.world.read_text())
        w["dispatches"]["ctx_1"]["terminalState"] = "released"
        self.world.write_text(json.dumps(w))
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        fix_start = self.calls("worker-start")[2]
        self.assertNotIn("--terminal", fix_start)
        self.assertIn("--keep-open", fix_start)

    def test_two_fix_rounds_then_escalate_then_next_rung(self):
        self.behave("commit", "fix", "commit", "fix", "commit", "fix")
        for _ in range(6):
            self.expect(10, "WAIT")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("task 01, tier standard, attempt 1, next rung antigravity:g-xhigh, replan allowed", last)
        hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
        self.assertIn(f"task=01 base={self.base}", hist)
        self.assertEqual(hist.count("VERDICT: FIX"), 3)
        self.expect(20, "ESCALATE")  # re-emitted while waiting on the architect
        note = self.tmp / "note.md"
        note.write_text("steps 1-2 done\n")
        self.behave("commit", "pass", "pass")
        last, _ = self.expect(10, "WAIT", "--architect-result",
                              self.result_file(f"RESULT: ESCALATE {self.base} {note} antigravity:g-xhigh"))
        self.assertIn("g-xhigh", self.calls("worker-start")[-1])
        spec = Path(self.st("IMPLEMENT_SPEC_01")).read_text()
        self.assertIn("steps 1-2 done", spec)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("LOOP_01"), "merged")

    def test_verdict_falls_back_to_the_settle_message(self):
        self.behave("commit", "pass-body-only", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertIn(" review2", self.expect(10, "WAIT")[0])
        self.assertIn("VERDICT: PASS", (self.run_dir / "verdicts" / "01-s1-1.md").read_text())
        self.expect(0, "QUEUE_EMPTY")

    def test_prose_settle_message_uses_the_review_file(self):
        self.behave("commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertEqual(self.st("NO_VERDICT_01_S1"), "0")
        self.assertTrue(list((self.run_dir / "results").glob("01-s1-settle-*.md")))
        self.expect(0, "QUEUE_EMPTY")

    def test_no_verdict_twice_counts_as_fix(self):
        self.behave("commit", "noverdict", "noverdict", "pending")
        self.expect(10, "WAIT")
        self.assertIn("ctx_2 review1", self.expect(10, "WAIT")[0])
        self.assertIn("ctx_3 review1", self.expect(10, "WAIT")[0])
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        self.assertEqual(self.st("FAILED_STAGE_01"), "1")

    def test_stage2_fix_rechecks_then_returns_to_stage2(self):
        self.behave("commit", "pass", "fix", "commit", "pass")
        for _ in range(3):
            self.expect(10, "WAIT")
        self.assertIn(" fix", self.expect(10, "WAIT")[0])
        self.assertIn(" review2", self.expect(10, "WAIT")[0])
        self.assertTrue(list((self.run_dir / "gates").glob("01-2.log")))
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(len([c for c in self.calls("worker-start") if "claude" in c]), 3)


class TreeAndWorkers(Base):
    def test_reviewer_that_edits_is_void_and_replaced(self):
        self.behave("commit", "edit-and-pass", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertIn("ctx_3 review1", self.expect(10, "WAIT")[0])
        self.assertTrue(list((self.run_dir / "verdicts").glob("01-void-s1-*.md")))
        self.assertEqual(sh("git", "-C", str(self.impl), "status", "--porcelain"), "")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("FIX_ROUNDS_01"), "0")

    def test_stopped_reviewer_is_replaced(self):
        self.behave("commit", "stopped", "pass", "pass")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.assertIn("ctx_3 review1", self.expect(10, "WAIT")[0])
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")

    def test_idle_implementer_is_nudged_and_finishes(self):
        self.behave("idle-then-commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertIn(" impl", self.expect(10, "WAIT")[0])
        self.assertEqual(len(self.calls("send")), 1)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")
        self.assertEqual(self.st("ATTEMPT_01"), "0")

    def test_idle_forever_implementer_escalates_after_two_nudges(self):
        self.behave("idle-forever")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.assertEqual(len(self.calls("send")), 2)
        self.assertIn("ended its turn 3 times", (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_uncommitted_work_gets_a_commit_worker(self):
        self.behave("dirty", "commit-all", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertIn(" commit", self.expect(10, "WAIT")[0])
        self.assertIn("--terminal", self.calls("worker-start")[1])
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")

    def test_no_commits_escalates(self):
        self.behave("nocommit")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.assertIn("settled succeeded with no commits",
                      (self.run_dir / "verdicts" / "01-history.md").read_text())

    def test_failed_implementer_message_goes_into_history(self):
        self.behave("fail")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.assertIn("AssertionError: 1 != 2", (self.run_dir / "verdicts" / "01-history.md").read_text())


class Prompts(Base):
    def test_usage_limit_prompts_then_retry_restarts(self):
        self.behave("usage")
        self.expect(10, "WAIT")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("5-hour limit", Path(last.split()[1]).read_text())
        self.assertEqual(self.st("ATTEMPT_01"), "0")
        self.expect(30, "PROMPT", "--reply", "maybe")
        self.behave("commit", "pass", "pass")
        self.assertIn(" impl", self.expect(10, "WAIT", "--reply", "retry")[0])
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        self.expect(0, "QUEUE_EMPTY")

    def test_rejected_pair_twice_prompts_then_architect_stops(self):
        self.behave("fail")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        bad = self.result_file(f"RESULT: ESCALATE {self.base} /dev/null claude:sonnet")
        self.expect(20, "ESCALATE", "--architect-result", bad)
        last, _ = self.expect(30, "PROMPT", "--architect-result", bad)
        self.assertIn("rejected twice", Path(last.split()[1]).read_text())
        self.expect(20, "ESCALATE", "--reply", "retry")
        self.expect(40, "STOP", "--architect-result", self.result_file("RESULT: STOP"))

    def test_drift_before_task_prompts_and_rebuild_resets(self):
        tree = sh("git", "-C", str(self.main), "rev-parse", "feature^{tree}")
        moved = sh("git", "-C", str(self.main), "-c", "user.email=t@t", "-c", "user.name=t",
                   "commit-tree", tree, "-p", self.base, "-m", "outside")
        sh("git", "-C", str(self.main), "update-ref", "refs/heads/feature", moved)
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("moved by:", Path(last.split()[1]).read_text())
        self.behave("pending")
        self.expect(10, "WAIT", "--reply", "rebuild")
        self.assertEqual(self.impl_head(), moved)
        self.assertEqual(self.st("LAST_MERGED"), moved)
        self.assertEqual(self.st("BASE_SHA_01"), moved)

    def test_branch_checked_out_blocks_merge(self):
        self.behave("commit", "pass", "pass")
        self.run_one_task_clean()
        sh("git", "-C", str(self.main), "switch", "-q", "feature")
        last, _ = self.expect(30, "PROMPT")
        self.assertIn("BRANCH_CHECKED_OUT", Path(last.split()[1]).read_text())
        sh("git", "-C", str(self.main), "switch", "-q", "main")
        _, lines = self.expect(0, "QUEUE_EMPTY", "--reply", "continue")
        self.assertTrue(lines[0].startswith("MERGED 01"))


class Replan(Base):
    def test_replan_runs_fix_meta_then_replaces_task(self):
        (self.run_dir / "queue.txt").write_text("01 02\n")
        self.behave("fail")
        self.expect(10, "WAIT")
        self.expect(20, "ESCALATE")
        self.task("01a", "AC-1")  # missing REPLANNED_FROM
        last, _ = self.expect(21, "FIX_META", "--architect-result",
                              self.result_file(f"RESULT: REPLAN {self.base} 01a"))
        self.assertIn("needs REPLANNED_FROM=01", Path(last.split()[1]).read_text())
        self.expect(21, "FIX_META")
        self.task("01a", "AC-1", "REPLANNED_FROM=01")
        self.behave("pending")
        self.assertIn(" impl", self.expect(10, "WAIT", "--architect-result",
                                           self.result_file("RESULT: META_FIXED"))[0])
        self.assertEqual((self.run_dir / "queue.txt").read_text().split(), ["01a", "02"])
        self.assertEqual(self.st("CURRENT_TASK"), "01a")

    def test_replan_of_a_replanned_task_is_rejected(self):
        self.task("01", "AC-1", "REPLANNED_FROM=00")
        self.behave("fail")
        self.expect(10, "WAIT")
        last, _ = self.expect(20, "ESCALATE")
        self.assertIn("replan not allowed", last)
        self.expect(20, "ESCALATE", "--architect-result", self.result_file(f"RESULT: REPLAN {self.base} 01b"))
        self.assertIn("Replan is not allowed", (self.run_dir / "verdicts" / "01-history.md").read_text())


class FinalFix(Base):
    def test_final_fix_task_runs_after_queue_empty(self):
        (self.run_dir / "queue.txt").write_text("01\n")
        self.behave("commit", "pass", "pass")
        self.run_one_task_clean()
        self.expect(0, "QUEUE_EMPTY")
        self.task("F1", "none")
        (self.run_dir / "queue.txt").write_text("F1\n")
        head = self.feature()
        self.behave("commit", "pass", "pass")
        self.expect(10, "WAIT")
        self.assertEqual(self.st("BASE_SHA_F1"), head)
        self.expect(10, "WAIT")
        self.expect(10, "WAIT")
        _, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(lines[0].startswith("MERGED F1"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
