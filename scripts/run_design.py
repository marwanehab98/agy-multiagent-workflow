"""Run the capsule communicator's Step 2 read-only design workers synchronously via headless CLI workers.

The flight director asks for a worker with a NEED result: a preflight check, a Range Safety design review,
a Range Safety lens pass or a Range Safety plan review. Each call runs every deterministic action it can
(check the docs worktree, test-launch the preflight pair, run the worker, save its response, restore the
docs worktree, retry once, count runs) and exits at the first point that needs the flight director.

Usage:
  python3 -I run_design.py --state <RUN_DIR>/state.json advance --result <file whose first line is RESULT: ...>
  python3 -I run_design.py --state <file> advance --answer <flight director answer file after CONSULT>
  python3 -I run_design.py --state <file> advance            # continue (re-prints a pending job or consult)
  python3 -I run_design.py --state <file> advance --docs-moved

Results it accepts (the first non-empty line of the file):
  RESULT: NEED preflight-check <design rel> <pair>
  RESULT: NEED safety <design rel> <findings rel>
  RESULT: NEED lens <concepts abs>
  RESULT: NEED plan-review <plan abs> <context abs>
  RESULT: COMMITTED                          the flight director committed its docs files (after commit-docs)
Anything else is ERROR UNEXPECTED_RESULT.

The last line of stdout is the status.
Exit 20  DIRECTOR_JOB <job> <input>        send that flight director job with INPUT=<everything after the job name>.
                                           Jobs: commit-docs, preflight-check-done, lens-done, safety-done,
                                           safety-limit, plan-review-done. Save its RESULT line to a file and
                                           call advance --result <file>
Exit 22  CONSULT <ask_file>                a worker exited with ASK:; run flight director job `consult` with
                                           INPUT=<ask_file>, then call advance --answer <answer_file>
Exit 31  USAGE_LIMIT <pair> <detail>       the preflight pair's test launch hit its model's usage limit; nothing is
                                           recorded and the phase stays at the test launch, so the next call
                                           (advance, no arguments) tries it again once the limit has reset
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage

The preflight check runs on the pair in its NEED line (checked with validate_meta.py harness-pair and
test-launched first); lens, Range Safety design review and plan review run on ROLE_SAFETY. A worker fails
when it exits failed, is stopped, hits a usage limit, changes the docs worktree (beyond the Range Safety
findings file), or leaves its output file missing or empty. A failure
is retried once (RETRY_<kind>), then the done job gets "failed: <saved file>". Run limits (counted per NEED in
SAFETY_RUNS, LENS_RUNS, PLAN_REVIEW_RUNS): at most two Range Safety design reviews, one lens pass and two plan
reviews run; the next request gets safety-limit, lens-done with the first lens file, or plan-review-done with the
last plan review instead. The findings path of NEED safety is taken relative to DOCS_WORKTREE (an absolute path
inside it, or one starting with ./, is rewritten so); one outside it is ERROR BAD_NEED. After each worker the docs
worktree is put back to DOCS_HEAD, its HEAD when the worker started. Every design worker is read-only: it may write
only under RUN_DIR, and the Range Safety design review also in the folder that holds its findings file.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import (CONSULT, ESCALATE, STOP, ERROR, WorkerRunner, Done, Run, answer_worker, build,  # noqa: E402
                    bundled_skills_dir, check_repo_trees, discard_repo_trees, drive, first_words, git, next_free,
                    read_answer, save_ask, save_body, tool)
from pairs import parse_pair  # noqa: E402
from probe import Prober, docs_moved  # noqa: E402

NEED_ARGS = {"preflight-check": 2, "safety": 2, "lens": 1, "plan-review": 2}
DONE_JOB = {"preflight-check": "preflight-check-done", "safety": "safety-done", "lens": "lens-done",
            "plan-review": "plan-review-done"}


class Design:
    def __init__(self, state_path):
        self.r = Run(state_path)
        self.r.require_new_format()
        self.runner = WorkerRunner(self.r)

    # -- state helpers
    def phase(self):
        return self.r.get("DESIGN_PHASE", "idle")

    def set_phase(self, phase, **kv):
        self.r.set(DESIGN_PHASE=phase, STEP=f"run_design {phase}", **kv)

    def job(self, name, text, phase="job"):
        line = f"DIRECTOR_JOB {name} {text}"
        self.set_phase(phase, DESIGN_LINE=line, WAITING_ON="director")
        raise Done(ESCALATE, line)

    def need(self):
        # Tab-separated, so a path may hold spaces; a value without a tab was written space-separated by an
        # earlier version (a run in flight).
        value = self.r.need("DESIGN_NEED")
        words = value.split("\t") if "\t" in value else value.split()
        return words[0], words[1:]

    @property
    def docs_wt(self):
        return self.r.need("DOCS_WORKTREE")

    # -- results from the flight director
    def take_result(self, path):
        words = first_words(path)
        line = " ".join(words)
        if words == ["RESULT:", "COMMITTED"] and self.phase() == "commit-docs":
            self.set_phase("start")
        elif words[:2] == ["RESULT:", "NEED"] and len(words) >= 3 and words[2] in NEED_ARGS:
            kind, args = words[2], words[3:]
            if len(args) != NEED_ARGS[kind]:
                raise Done(ERROR, f"ERROR BAD_NEED {line[:300]}")
            if kind == "safety":
                args[1] = self.docs_rel(args[1], line)
            self.r.set(DESIGN_NEED="\t".join([kind, *args]), **{f"RETRY_{kind}": 0})
            if kind == "safety":
                self.r.set(SAFETY_REQUESTED="yes")  # the user chose Range Safety review in a relay; state shows what ran
            self.count_run(kind)
            self.set_phase("start")
        else:
            raise Done(ERROR, f"ERROR UNEXPECTED_RESULT phase={self.phase()}: {line[:300]}")

    def docs_rel(self, path, line):
        """path relative to DOCS_WORKTREE, in the form git status prints it (check-tree.sh --allow matches it
        exactly): no ./, no .., not absolute. A path outside the docs worktree, also through a symlink inside it, or
        the worktree root itself (also through a symlink) is ERROR BAD_NEED."""
        root = Path(self.docs_wt).resolve()
        full = Path(path) if Path(path).is_absolute() else root / path
        try:
            rel = Path(os.path.normpath(full)).relative_to(root)
        except ValueError:
            try:
                rel = full.resolve().relative_to(root)  # an absolute path spelled through a symlink of the worktree
            except ValueError:
                raise Done(ERROR, f"ERROR BAD_NEED findings path outside the docs worktree: {line[:300]}")
        if rel == Path("."):
            raise Done(ERROR, f"ERROR BAD_NEED {line[:300]}")
        try:
            resolved = (root / rel).resolve().relative_to(root)  # a symlink in the worktree may lead outside it
        except ValueError:
            raise Done(ERROR, f"ERROR BAD_NEED findings path outside the docs worktree: {line[:300]}")
        if resolved == Path("."):  # a symlink in the worktree that leads to its root
            raise Done(ERROR, f"ERROR BAD_NEED {line[:300]}")
        return rel.as_posix()

    def count_run(self, kind):
        """Count the run a NEED asks for; at the limit, send the flight director the limit job. Not repeated on a retry."""
        r = self.r
        if kind == "safety" and r.incr("SAFETY_RUNS") >= 3:
            self.job("safety-limit", "none")
        if kind == "lens" and r.incr("LENS_RUNS") >= 2:
            self.job("lens-done", r.get("LENS_FIRST", str(r.run_dir / "lens-1.md")))
        if kind == "plan-review" and r.incr("PLAN_REVIEW_RUNS") >= 3:
            self.job("plan-review-done", "Plan review limit reached; finish triage with "
                     + r.get("PLAN_REVIEW_LAST", str(r.run_dir / "plan-review-1.md")))

    # -- starting a worker
    def do_start(self):
        r = self.r
        head = git(self.docs_wt, "rev-parse", "HEAD")
        r.set(DOCS_HEAD=head)
        check = next_free(r.run_dir / "results", "docs-check-{n}.txt")
        rc, out = tool("check-tree.sh", self.docs_wt, head)
        check.write_text(out + "\n", encoding="utf-8")
        # 2: uncommitted changes for the flight director to commit. 1 (HEAD moved since it was read just above:
        # something committed meanwhile) goes the same way; the next start reads the new HEAD. Anything else
        # (3 STATUS_FAILED: git status itself failed) is not a dirty tree.
        if rc not in (0, 1, 2):
            raise Done(ERROR, f"ERROR CHECK_TREE {' '.join(out.split())[:300]}")
        if rc != 0:
            r.log(f"docs worktree dirty before a design worker: {check}")
            self.job("commit-docs", str(check), phase="commit-docs")
        kind, args = self.need()
        if kind != "preflight-check":
            return self.dispatch()
        pair = args[1]
        rc, out = tool("validate_meta.py", "harness-pair", "--state", str(r.path), "--pair", pair)
        if rc != 0:
            bad = next_free(r.run_dir / "results", "preflight-pair-{n}.txt")
            bad.write_text(out + "\n", encoding="utf-8")
            self.job(DONE_JOB[kind], f"failed: {bad}")
        self.set_phase("probe")

    def do_probe(self):
        pair = self.need()[1][1]
        try:
            Prober(self.r, self.runner).advance([pair])
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
                self.job(DONE_JOB["preflight-check"], f"failed: {d.line.split(' ', 2)[-1]}")
            raise
        self.set_phase("dispatch")

    def safety_skills(self, skill_name="flight-readiness"):
        r = self.r
        harness = parse_pair(r.need("ROLE_SAFETY")).harness
        rc, out = tool("harnesses.py", "skill-dir", "--catalog", r.need("CATALOG"), "--harness", harness,
                       "--skill", skill_name)
        if rc == 0:
            skills_dir = str(Path(out).parent)
        else:
            skills_dir = bundled_skills_dir(skill_name)
            if not skills_dir:
                raise Done(ERROR, f"ERROR SAFETY_SKILLS {out}")
        if skill_name == "flight-readiness":
            r.set(SAFETY_SKILLS=skills_dir)
        return skills_dir

    def dispatch(self):
        r = self.r
        kind, args = self.need()
        spec = next_free(r.run_dir / "specs", kind + "-{n}.txt")
        names = {"preflight-check": "preflight-check-{n}.md", "safety": "safety-{n}.md", "lens": "lens-{n}.md",
                 "plan-review": "plan-review-{n}.md"}
        out = next_free(r.run_dir, names[kind])
        if kind == "preflight-check":
            build("preflight-check.txt", spec, {"DESIGN_FILE": args[0], "OUTPUT_FILE": out})
            pair = args[1]
        else:
            skill_map = {"safety": "flight-readiness", "lens": "wind-tunnel", "plan-review": "cross-check"}
            skills = self.safety_skills(skill_map[kind])
            pair = r.need("ROLE_SAFETY")
            if kind == "safety":
                build("range-safety.txt", spec, {"SAFETY_SKILLS": skills, "DESIGN_FILE": args[0], "OUTPUT_FILE": args[1]})
            elif kind == "lens":
                build("lens.txt", spec, {"SAFETY_SKILLS": skills, "CONCEPTS_FILE": args[0], "OUTPUT_FILE": out})
            else:
                build("plan-review.txt", spec, {"SAFETY_SKILLS": skills, "PLAN_FILE": args[0], "OUTPUT_FILE": out},
                      {"CONTEXT": args[1]})
        r.set(DESIGN_SPEC=spec, DESIGN_OUT=out, DESIGN_PAIR=pair)
        self.set_phase("dispatching")
        r.log(f"design {kind} dispatched {pair}")
        result = self.runner.run(spec, pair=pair, worktree=self.docs_wt, access="read",
                                 write_extra=self.write_extra(kind, args))
        self.handle_result(kind, result)

    def write_extra(self, kind, args):
        """Where a design worker may write besides RUN_DIR: only the Range Safety design review writes in the docs
        worktree, and only in the folder that holds its findings file (args[1], relative to DOCS_WORKTREE)."""
        if kind != "safety":
            return []
        return [str((Path(self.docs_wt) / args[1]).parent)]

    def _discard_or_error(self, head):
        rc, out = tool("discard-changes.sh", self.docs_wt, head)
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")
        if self.r.is_multi_repo:
            rc2, out2 = discard_repo_trees(self.r, "docs")
            if rc2 != 0:
                raise Done(ERROR, f"ERROR RESET {out2}")

    def _restore_tree(self, kind, allow_safety_out=True):
        _, args = self.need()
        allow = ["--allow", args[1]] if kind == "safety" and allow_safety_out else []
        head = self.r.need("DOCS_HEAD")
        rc, why = tool("check-tree.sh", self.docs_wt, head, *allow)
        if rc == 0 and self.r.is_multi_repo:
            rc, why = check_repo_trees(self.r, "docs")
        if rc != 0:
            self._discard_or_error(head)
            self.r.log(f"design {kind} worker changed the docs worktree ({why}); discarded")
        return rc == 0

    def handle_result(self, kind, result):
        r = self.r
        if result[0] == "ask":
            if self._restore_tree(kind):
                ask = save_ask(r, "design", result[2], header=f"design phase ({kind})")
                self.set_phase("consult", DESIGN_ASK_CONV=result[1], DESIGN_ASK_FILE=ask, WAITING_ON="director")
                raise Done(CONSULT, f"CONSULT {ask}")
            # Read-only worker changed the docs worktree before asking; treat as failed attempt
            result = ("stopped", "worker changed the docs worktree before asking", "", result[3])
        self.settle(kind, result)

    def take_answer(self, answer_path):
        r = self.r
        if self.phase() != "consult":
            raise Done(ERROR, f"ERROR NO_CONSULT_OPEN phase={self.phase()}")
        ans = read_answer(answer_path)
        kind, args = self.need()
        pair = r.need("DESIGN_PAIR")
        conv = r.get("DESIGN_ASK_CONV", "")
        r.set(DESIGN_ASK_CONV=None, DESIGN_ASK_FILE=None)
        self.set_phase("dispatching", WAITING_ON=None)
        result = answer_worker(self.runner, ans, pair, self.docs_wt, conv, r.need("DESIGN_SPEC"),
                               lambda: self._restore_tree(kind, allow_safety_out=False),
                               access="read", write_extra=self.write_extra(kind, args))
        self.handle_result(kind, result)

    def settle(self, kind, result):
        r = self.r
        _, args = self.need()
        out = Path(r.need("DESIGN_OUT"))
        saved = next_free(r.run_dir / "results", kind + "-{n}.md")
        failed = result[0] != "done" or result[1] != "succeeded"
        if result[0] == "done":
            save_body(result[2], saved)
        else:
            saved.write_text(f"{result[0]}: {result[1]}\n", encoding="utf-8")
        if not failed and kind == "safety":
            src = Path(self.docs_wt) / args[1]
            if src.is_file():
                shutil.copyfile(src, out)
        if not failed and not (out.is_file() and out.read_text(encoding="utf-8").strip()):
            failed = True
        if not self._restore_tree(kind, allow_safety_out=not failed) and not failed:
            # A read-only worker that changed the docs worktree is void: its changes are gone, and it counts as failed.
            failed = True
            saved.write_text("failed: the worker changed the docs worktree, which it may only read; its changes were "
                             "discarded\n\n" + saved.read_text(encoding="utf-8"), encoding="utf-8")
        if failed:
            r.log(f"design {kind} failed: {saved}")
            if r.incr(f"RETRY_{kind}") < 2:
                return self.set_phase("start")
            r.set(**{f"RETRY_{kind}": 0})
            self.job(DONE_JOB[kind], f"failed: {saved}")
        r.set(**{f"RETRY_{kind}": 0})
        r.log(f"design {kind} settled succeeded {out}")
        if kind == "lens" and not r.get("LENS_FIRST"):
            r.set(LENS_FIRST=out)
        if kind == "plan-review":
            r.set(PLAN_REVIEW_LAST=out)
        self.job(DONE_JOB[kind], str(out))

    # -- the loop
    def advance(self, result_path=None, answer_path=None, moved=False):
        if moved:
            docs_moved(self.r)
        if result_path:
            self.take_result(result_path)
        elif answer_path:
            self.take_answer(answer_path)
        for _ in range(50):
            phase = self.phase()
            if phase == "idle":
                raise Done(ERROR, "ERROR NO_PENDING_NEED")
            if phase in ("job", "commit-docs"):
                raise Done(ESCALATE, self.r.need("DESIGN_LINE"))
            if phase == "consult":
                raise Done(CONSULT, f"CONSULT {self.r.need('DESIGN_ASK_FILE')}")
            if phase == "dispatching":
                self._restore_tree(self.need()[0], allow_safety_out=False)
                self.set_phase("start")
                continue
            {"start": self.do_start, "probe": self.do_probe, "dispatch": self.dispatch}[phase]()
        raise Done(ERROR, "ERROR LOOP_LIMIT")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    sub = p.add_subparsers(dest="op", required=True)
    adv = sub.add_parser("advance")
    adv.add_argument("--result")
    adv.add_argument("--answer")
    adv.add_argument("--docs-moved", action="store_true")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    return drive(a.state, lambda events: Design(a.state).advance(a.result, a.answer, a.docs_moved))


if __name__ == "__main__":
    sys.exit(main())
