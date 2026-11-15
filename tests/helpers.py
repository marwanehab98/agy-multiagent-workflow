"""Shared fixtures for the script tests: a scratch git repo, a fake Orca, and a driver for the advance scripts."""
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
    SCRIPT = RUN_TASK  # the advance script that adv() runs; subclasses override it

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
        r = subprocess.run([sys.executable, "-I", str(self.SCRIPT), "--state", str(self.state), "advance", *extra],
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

    def until(self, code, prefix, *extra, limit=20):
        """Call adv while the status is WAIT; assert the first other status is (code, prefix)."""
        for _ in range(limit):
            rc, last, lines = self.adv(*extra)
            if rc != 10:
                self.assertEqual((rc, last.split()[0]), (code, prefix), f"got {rc} {lines}")
                return last, lines
        self.fail(f"still WAIT after {limit} steps")
