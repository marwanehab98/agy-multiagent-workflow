"""Run the coordinator's Step 2 read-only design workers synchronously via headless `agy -p`.

The architect asks for a worker with a NEED result: a builder check, an EGM design review, an EGM
lens pass or an EGM plan review. Each call runs every deterministic action it can (check the docs
worktree, test-launch the builder pair, run the worker via `agy -p`, save its response, restore the
docs worktree, retry once, count runs) and exits at the first point that needs the architect.

Usage:
  python3 -I run_design.py --state <RUN_DIR>/state.json advance --result <file whose first line is RESULT: ...>
  python3 -I run_design.py --state <file> advance --answer <architect answer file after CONSULT>
  python3 -I run_design.py --state <file> advance            # continue (re-prints a pending job or consult)
  python3 -I run_design.py --state <file> advance --docs-moved

Results it accepts (the first non-empty line of the file):
  RESULT: NEED builder-check <design rel> <pair>
  RESULT: NEED egm <design rel> <findings rel>
  RESULT: NEED lens <concepts abs>
  RESULT: NEED plan-review <plan abs> <context abs>
  RESULT: COMMITTED                          the architect committed its docs files (after commit-docs)
Anything else is ERROR UNEXPECTED_RESULT.

The last line of stdout is the status.
Exit 20  ARCHITECT_JOB <job> <input>       send that architect job with INPUT=<everything after the job name>.
                                           Jobs: commit-docs, builder-check-done, lens-done, egm-done,
                                           egm-limit, plan-review-done. Save its RESULT line to a file and
                                           call advance --result <file>
Exit 22  CONSULT <ask_file>                a worker exited with ASK:; run architect job `consult` with
                                           INPUT=<ask_file>, then call advance --answer <answer_file>
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage

The builder check runs on the pair in its NEED line (checked with validate_meta.py harness-pair and
test-launched first); lens, EGM and plan review run on ROLE_EGM. A worker fails when it exits failed,
is stopped, hits a usage limit, or leaves its output file missing or empty. A failure is retried once
(RETRY_<kind>), then the done job gets "failed: <saved file>". Run limits: EGM_RUNS 3, LENS_RUNS 2,
PLAN_REVIEW_RUNS 3. After each worker the docs worktree is put back to DOCS_HEAD, its HEAD when the
worker started.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import (CONSULT, ESCALATE, STOP, ERROR, AgyRunner, Done, Run, build, first_words, git, next_free,  # noqa: E402
                    save_body, tool)
from pairs import parse_pair  # noqa: E402
from probe import Prober, docs_moved  # noqa: E402

NEED_ARGS = {"builder-check": 2, "egm": 2, "lens": 1, "plan-review": 2}
DONE_JOB = {"builder-check": "builder-check-done", "egm": "egm-done", "lens": "lens-done",
            "plan-review": "plan-review-done"}


class Design:
    def __init__(self, state_path):
        self.r = Run(state_path)
        self.r.require_new_format()
        self.runner = AgyRunner(self.r)

    # -- state helpers
    def phase(self):
        return self.r.get("DESIGN_PHASE", "idle")

    def set_phase(self, phase, **kv):
        self.r.set(DESIGN_PHASE=phase, STEP=f"run_design {phase}", **kv)

    def job(self, name, text, phase="job"):
        line = f"ARCHITECT_JOB {name} {text}"
        self.set_phase(phase, DESIGN_LINE=line, WAITING_ON="architect")
        raise Done(ESCALATE, line)

    def need(self):
        words = self.r.need("DESIGN_NEED").split()
        return words[0], words[1:]

    @property
    def docs_wt(self):
        return self.r.need("DOCS_WORKTREE")

    # -- results from the architect
    def take_result(self, path):
        words = first_words(path)
        line = " ".join(words)
        if words == ["RESULT:", "COMMITTED"] and self.phase() == "commit-docs":
            self.set_phase("start")
        elif words[:2] == ["RESULT:", "NEED"] and len(words) >= 3 and words[2] in NEED_ARGS:
            kind, args = words[2], words[3:]
            if len(args) != NEED_ARGS[kind]:
                raise Done(ERROR, f"ERROR BAD_NEED {line[:300]}")
            self.r.set(DESIGN_NEED=" ".join(words[2:]), **{f"RETRY_{kind}": 0})
            if kind == "egm":
                self.r.set(EGM_REQUESTED="yes")  # the user chose EGM in a relay; state shows what ran
            self.count_run(kind)
            self.set_phase("start")
        else:
            raise Done(ERROR, f"ERROR UNEXPECTED_RESULT phase={self.phase()}: {line[:300]}")

    def count_run(self, kind):
        """Count the run a NEED asks for; at the limit, send the architect the limit job. Not repeated on a retry."""
        r = self.r
        if kind == "egm" and r.incr("EGM_RUNS") >= 3:
            self.job("egm-limit", "none")
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
        if rc != 0:
            r.log(f"docs worktree dirty before a design worker: {check}")
            self.job("commit-docs", str(check), phase="commit-docs")
        kind, args = self.need()
        if kind != "builder-check":
            return self.dispatch()
        pair = args[1]
        rc, out = tool("validate_meta.py", "harness-pair", "--state", str(r.path), "--pair", pair)
        if rc != 0:
            bad = next_free(r.run_dir / "results", "builder-pair-{n}.txt")
            bad.write_text(out + "\n", encoding="utf-8")
            self.job(DONE_JOB[kind], f"failed: {bad}")
        self.set_phase("probe")

    def do_probe(self):
        pair = self.need()[1][1]
        try:
            Prober(self.r, self.runner).advance([pair])
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
                self.job(DONE_JOB["builder-check"], f"failed: {d.line.split()[-1]}")
            raise
        self.set_phase("dispatch")

    def egm_skills(self):
        r = self.r
        if not r.get("EGM_SKILLS"):
            harness = parse_pair(r.need("ROLE_EGM")).harness
            rc, out = tool("harnesses.py", "skill-dir", "--catalog", r.need("CATALOG"), "--harness", harness,
                           "--skill", "egm-review")
            if rc != 0:
                raise Done(ERROR, f"ERROR EGM_SKILLS {out}")
            r.set(EGM_SKILLS=str(Path(out).parent))
        return r.need("EGM_SKILLS")

    def dispatch(self):
        r = self.r
        kind, args = self.need()
        spec = next_free(r.run_dir / "specs", kind + "-{n}.txt")
        names = {"builder-check": "builder-check-{n}.md", "egm": "egm-{n}.md", "lens": "lens-{n}.md",
                 "plan-review": "plan-review-{n}.md"}
        out = next_free(r.run_dir, names[kind])
        if kind == "builder-check":
            build("builder-check.txt", spec, {"DESIGN_FILE": args[0], "OUTPUT_FILE": out})
            pair = args[1]
        else:
            skills = self.egm_skills()
            pair = r.need("ROLE_EGM")
            if kind == "egm":
                build("egm.txt", spec, {"EGM_SKILLS": skills, "DESIGN_FILE": args[0], "OUTPUT_FILE": args[1]})
            elif kind == "lens":
                build("lens.txt", spec, {"EGM_SKILLS": skills, "CONCEPTS_FILE": args[0], "OUTPUT_FILE": out})
            else:
                build("plan-review.txt", spec, {"EGM_SKILLS": skills, "PLAN_FILE": args[0], "OUTPUT_FILE": out},
                      {"CONTEXT": args[1]})
        r.set(DESIGN_SPEC=spec, DESIGN_OUT=out, DESIGN_PAIR=pair)
        result = self.runner.run(spec, pair=pair, worktree=self.docs_wt)
        r.log(f"design {kind} dispatched {pair}")
        self.handle_result(kind, result)

    def _restore_tree(self, kind):
        _, args = self.need()
        allow = ["--allow", args[1]] if kind == "egm" else []
        rc, why = tool("check-tree.sh", self.docs_wt, self.r.need("DOCS_HEAD"), *allow)
        if rc != 0:
            rc2, out2 = tool("discard-changes.sh", self.docs_wt, self.r.need("DOCS_HEAD"))
            self.r.log(f"design {kind} worker changed the docs worktree ({why}); discarded: {out2} (exit {rc2})")
        return rc == 0

    def handle_result(self, kind, result):
        r = self.r
        if result[0] == "ask":
            if self._restore_tree(kind):
                ask = next_free(r.run_dir / "asks", "design-{n}.md")
                save_body(result[2], ask, header=f"design phase ({kind})")
                self.set_phase("consult", DESIGN_ASK_CONV=result[1], DESIGN_ASK_FILE=ask, WAITING_ON="architect")
                raise Done(CONSULT, f"CONSULT {ask}")
            # Read-only worker changed the docs worktree before asking; treat as failed attempt
            result = ("stopped", "worker changed the docs worktree before asking", "", result[3])
        self.settle(kind, result)

    def take_answer(self, answer_path):
        r = self.r
        if self.phase() != "consult":
            raise Done(ERROR, f"ERROR NO_CONSULT_OPEN phase={self.phase()}")
        p = Path(answer_path)
        if not p.is_file():
            raise Done(ERROR, f"ERROR MISSING_FILE {p}")
        kind, _ = self.need()
        pair = r.need("DESIGN_PAIR")
        conv = r.get("DESIGN_ASK_CONV", "")
        ans = p.read_text(encoding="utf-8")
        result = self.runner.try_resume(f"Architect answer:\n\n{ans}", pair, self.docs_wt, conv)
        if result is None:
            spec_text = Path(r.need("DESIGN_SPEC")).read_text(encoding="utf-8")
            result = self.runner.run(f"{spec_text}\n\nArchitect answer to your question:\n{ans}",
                                     pair=pair, worktree=self.docs_wt)
        r.set(DESIGN_ASK_CONV=None, DESIGN_ASK_FILE=None)
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
        if kind == "egm":
            src = Path(self.docs_wt) / args[1]
            if src.is_file():
                shutil.copyfile(src, out)
        self._restore_tree(kind)
        if not failed and not (out.is_file() and out.read_text(encoding="utf-8").strip()):
            failed = True
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
    try:
        Design(a.state).advance(a.result, a.answer, a.docs_moved)
        code, line = ERROR, "ERROR NO_STATUS"
    except Done as d:
        code, line = d.code, d.line
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
