"""Run the coordinator's Step 8 items 1-3 (starting-state check, final review, final-fix rounds,
verification) as a resumable state machine.

Each call does every deterministic action it can (release the implementers, check the tree, start the
final reviewer, save its settle, pick its verdict, count voids and rounds, run the VERIFY commands) and
exits at the first point that needs a wait, the architect, the queue, or the user. All its state lives
in the coordinator's state.json. It sets PHASE=finish when it starts, and never polls.

Usage:
  python3 -I run_finish.py --state <RUN_DIR>/state.json advance
  python3 -I run_finish.py --state <file> advance --architect-result <file with the RESULT line>
  python3 -I run_finish.py --state <file> advance --reply <the user's one-word reply>
  python3 -I run_finish.py --state <file> advance --docs-moved   # continue, after an architect job
                                                   # (consult, user-message) committed in DOCS_WORKTREE

The last line of stdout is the status.
Exit 0   VERIFIED <log>                    the VERIFY commands passed (or the user accepted a failing
                                           run); go to Step 8 item 4 with <log> as the verify log
Exit 10  WAIT <dispatch> <kind>            end your turn; call advance again when woken. kind is
                                           final-review, or probe (a final-fix task's reviewer pairs)
Exit 20  ARCHITECT_JOB <job> <input>       send that architect job with INPUT=<input>. Jobs:
                                           final-review <verdict file>, verification-failed <log>,
                                           fix-meta <validate output>. Save its RESULT line to a file and
                                           call advance --architect-result <file>
Exit 21  RUN_QUEUE <F id>                  queue.txt now holds only that id: run run_task.py (Step 4);
                                           on QUEUE_EMPTY call advance again for a new final review
Exit 30  PROMPT <file> <reply> [<reply>]   show <file> to the user exactly; pass the reply back
Exit 40  STOP <reason>                     dispatch nothing more; coordinator Step 8 item 7
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage

Architect results it accepts: RESULT: FINAL_FIX <F id>, RESULT: FINAL_CLEAN, RESULT: STOP (to a
final-review or verification-failed job) and RESULT: META_FIXED (to fix-meta). FINAL_CLEAN after a
final review runs verification; after a failed verification it asks the user whether to open the PR anyway.
A final reviewer that is stopped or lost, or that changes the worktree, is void and replaced; the third
void in a row prompts. Run limits: FINAL_FIX_ROUNDS 3 (prompt), META_FAILS 3 (error).
A FINAL_FIX task's REVIEW_1/REVIEW_2 pairs are test-launched (phase probe, in DOCS_WORKTREE) after it
validates and before the queue is written; a failed test goes to the architect as job fix-meta with
PROBLEM lines naming the pair, and counts toward META_FAILS like a failed validation. --docs-moved
(ruling R13) first moves PROBE_HEAD to the docs worktree's current HEAD, so an architect's commits made
during that probe survive its tree check.
State keys: FIN_PHASE, FIN_DISPATCH, FIN_OUT, FIN_HEAD, FIN_JOB, FIN_LINE, FIN_FIX, FIN_LAST_VERDICT,
FIN_VERIFY_LOG, FINAL_N, VERIFY_K, VOIDS_FINAL, NO_VERDICT_FINAL, FIN_PROMPT_FILE, FIN_PROMPT_ACCEPT.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import (WAIT, ESCALATE, PROMPT, STOP, ERROR, Done, Orca, Run, build, next_free,  # noqa: E402
                    pick_verdict, poll, save_body, tool, verdict_line)
from probe import Prober, docs_moved, launch_problems, review_pairs  # noqa: E402

VERIFIED, RUN_QUEUE = 0, 21
NUDGE = ("You ended your turn without settling this task. If a command you started is still running, "
         "wait for it in the foreground until it finishes. Then finish the task and settle worker_done, "
         "with outcome succeeded or failed.")


class Finish:
    def __init__(self, state_path):
        self.r = Run(state_path)
        self.r.require_new_format()
        self.o = Orca(self.r)

    # -- state helpers
    def phase(self):
        return self.r.get("FIN_PHASE", "start")

    def set_phase(self, phase, **kv):
        self.r.set(FIN_PHASE=phase, STEP=f"run_finish {phase}", **kv)

    def job(self, name, text):
        line = f"ARCHITECT_JOB {name} {text}"
        self.set_phase("job", FIN_JOB=name, FIN_LINE=line, WAITING_ON="architect")
        raise Done(ESCALATE, line)

    def prompt(self, name, text, accepted):
        f = next_free(self.r.run_dir / "results", "prompt-{n}.md")
        f.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        self.set_phase(f"prompt:{name}", FIN_PROMPT_FILE=f, FIN_PROMPT_ACCEPT=" ".join(accepted), WAITING_ON="user")
        raise Done(PROMPT, f"PROMPT {f} {' '.join(accepted)}")

    # -- item 1: starting state
    def do_start(self):
        r = self.r
        r.set(PHASE="finish")
        for key in [k for k in r.s if k.startswith("IMPL_DISPATCH_")]:
            self.o.release(r.s[key])
        rc, out = tool("check-tree.sh", r.impl, r.need("LAST_MERGED"))
        if rc != 0:
            raise Done(ERROR, f"ERROR TREE_NOT_CLEAN {out}")
        self.set_phase("review")

    # -- item 2: the final review
    def do_review(self):
        r = self.r
        n = r.incr("FINAL_N")
        head = r.need("LAST_MERGED")
        out = r.run_dir / "verdicts" / f"final-{n}.md"
        spec = build("final-review.txt", next_free(r.run_dir / "specs", "final-review-{n}.txt"),
                     {"BASE_SHA": r.need("INITIAL_BASE_SHA"), "WORKER_HEAD": head,
                      "CONVENTION_PATHS": r.need("CONVENTION_PATHS"), "OUTPUT_FILE": out},
                     {"DESIGN": r.need("DESIGN_FILE"), "DECISIONS_LOG": r.need("DECISIONS_LOG")})
        pair = r.need("ROLE_ARCHITECT")
        dispatch, _ = self.o.start(spec, f"Final review: {r.need('TOPIC')}", pair, r.impl)
        r.log(f"final review {n} dispatched {dispatch} {pair}")
        self.set_phase("wait", FIN_DISPATCH=dispatch, FIN_OUT=out, FIN_HEAD=head, WAITING_ON=dispatch)
        raise Done(WAIT, f"WAIT {dispatch} final-review")

    def do_wait(self):
        r = self.r
        dispatch = r.need("FIN_DISPATCH")
        result = poll(self.o, r, dispatch)
        if result is None:
            raise Done(WAIT, f"WAIT {dispatch} final-review")
        if result[0] == "idle":
            r.set(**{f"HANDLED_SEQ_{dispatch}": result[1]})
            n = r.incr(f"NUDGES_{dispatch}")
            if n > 2:
                result = ("stopped", f"worker {dispatch} ended its turn {n} times without settling")
            else:
                res = self.o.send(dispatch, "Finish and settle this task", NUDGE)
                new = res.get("dispatchId") if isinstance(res, dict) else None
                if new and new != dispatch:
                    r.set(FIN_DISPATCH=new, WAITING_ON=new)
                    dispatch = new
                r.log(f"final reviewer {dispatch} idle without settling; nudge {n}")
                raise Done(WAIT, f"WAIT {dispatch} final-review")
        self.settle(dispatch, result)

    def settle(self, dispatch, result):
        r = self.r
        if result[0] == "usage":
            self.o.release(dispatch)
            self.prompt("usage-limit",
                        f"The final reviewer hit its model's usage limit: {result[1]}\n"
                        "Reply \"retry\" once the limit has reset, or \"stop\" to stop the run.", ["retry", "stop"])
        if result[0] == "stopped":
            self.o.release(dispatch)
            return self.void("stopped without a verdict")
        n = r.need("FINAL_N")
        out = Path(r.need("FIN_OUT"))
        settle = save_body(result[2], r.run_dir / "results" / f"final-settle-{n}.md")
        verdict = pick_verdict(out, settle)
        self.o.release(dispatch)
        head = r.need("FIN_HEAD")
        rc, _ = tool("check-tree.sh", r.impl, head)
        if rc != 0:
            tool("discard-changes.sh", r.impl, head)
            verdict.rename(next_free(r.run_dir / "verdicts", "final-void-{n}.md"))
            return self.void("changed the worktree")
        last = verdict_line(verdict)
        r.log(f"final review {n} {last or 'no verdict'} {verdict}")
        r.set(VOIDS_FINAL=0, FIN_LAST_VERDICT=verdict, WAITING_ON=None)
        if last:
            r.set(NO_VERDICT_FINAL=0)
        if last == "VERDICT: PASS":
            return self.set_phase("verify")
        if last != "VERDICT: FIX" and r.incr("NO_VERDICT_FINAL") < 2:
            return self.set_phase("review")
        self.job("final-review", verdict)

    def void(self, why):
        r = self.r
        n = r.incr("VOIDS_FINAL")
        r.log(f"final reviewer void: {why}")
        if n >= 3:
            self.prompt("void", f"Three final reviewers in a row were void ({why}).\n"
                                "Reply \"retry\" to start a new reviewer, or \"stop\" to stop the run.",
                        ["retry", "stop"])
        self.set_phase("review")

    # -- item 3: verification
    def do_verify(self):
        r = self.r
        k = r.incr("VERIFY_K")
        log = r.run_dir / f"verify-{k}.log"
        rc, out = tool("gate.py", "--run-values", r.need("RUN_VALUES"), "--worktree", r.impl,
                       "--head", r.need("LAST_MERGED"), "--log", str(log), "--prefix", "VERIFY")
        r.log(f"verify {k} exit={rc} {log}")
        r.set(FIN_VERIFY_LOG=log)
        if rc == 0:
            return self.verified(log)
        if rc == 1:
            self.job("verification-failed", log)
        raise Done(ERROR, f"ERROR GATE {out}")

    def verified(self, log):
        self.set_phase("verified", FIN_VERIFY_LOG=log, WAITING_ON=None)
        raise Done(VERIFIED, f"VERIFIED {log}")

    # -- the architect's answers
    def take_result(self, path):
        r = self.r
        text = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else ""
        words = next((l.split() for l in text.splitlines() if l.strip()), [])
        phase, job = self.phase(), r.get("FIN_JOB")
        if phase != "job" or words[:1] != ["RESULT:"] or len(words) < 2:
            raise Done(ERROR, f"ERROR UNEXPECTED_ARCHITECT_RESULT phase={phase}: {' '.join(words)[:300]}")
        kind = words[1]
        r.set(WAITING_ON=None)
        if kind == "STOP":
            r.log(f"final {job}: architect chose STOP")
            raise Done(STOP, "STOP architect stopped the run during the final review")
        if job in ("final-review", "verification-failed"):
            if kind == "FINAL_FIX" and len(words) == 3:
                return self.validate_fix(words[2])
            if kind == "FINAL_CLEAN" and len(words) == 2:
                if job == "final-review":
                    return self.set_phase("verify")
                log = r.need("FIN_VERIFY_LOG")
                self.prompt("accept-failing",
                            f"Verification still fails, and the architect found nothing to fix: {log}. "
                            "Reply \"continue\" to open the PR anyway, or \"stop\" to stop the run.",
                            ["continue", "stop"])
        if job == "fix-meta" and kind == "META_FIXED" and len(words) == 2:
            return self.validate_fix(r.need("FIN_FIX"))
        raise Done(ERROR, f"ERROR UNEXPECTED_ARCHITECT_RESULT job={job}: {' '.join(words)[:300]}")

    def validate_fix(self, fid):
        r = self.r
        out_file = next_free(r.run_dir, "validate-{n}.txt")
        rc, out = tool("validate_meta.py", "tasks", "--state", str(r.path), "--tasks-dir", str(r.docs / "tasks"),
                       "--ids", fid)
        out_file.write_text(out + "\n", encoding="utf-8")
        r.set(FIN_FIX=fid)
        if rc != 0:
            self.fix_meta(out_file)
        self.set_phase("probe")

    def fix_meta(self, out_file):
        if self.r.incr("META_FAILS") >= 3:
            raise Done(ERROR, f"ERROR META_INVALID_3_TIMES {out_file}")
        self.job("fix-meta", out_file)

    def do_probe(self):
        """Test-launch the final-fix task's reviewer pairs; a failed launch goes to the architect as fix-meta."""
        r = self.r
        fid = r.need("FIN_FIX")
        try:
            Prober(r, self.o).advance(review_pairs(r, [fid]))
        except Done as d:
            if d.code == WAIT:
                r.set(WAITING_ON=d.line.split()[1])
            elif d.code == STOP and d.line.startswith("PROBE_FAILED"):
                out_file = next_free(r.run_dir, "validate-{n}.txt")
                out_file.write_text(launch_problems(r, [fid], d.line), encoding="utf-8")
                self.fix_meta(out_file)
            raise
        r.set(META_FAILS=0)
        if r.incr("FINAL_FIX_ROUNDS") >= 3:
            latest = r.get("FIN_LAST_VERDICT") or r.get("FIN_VERIFY_LOG")
            self.prompt("rounds", "The final review still finds issues after two final-fix rounds: "
                                  f"{latest}. Reply \"continue\" for one more round, or \"stop\" to stop the run.",
                        ["continue", "stop"])
        self.run_queue(fid)

    def run_queue(self, fid):
        r = self.r
        (r.run_dir / "queue.txt").write_text(f"{fid}\n", encoding="utf-8")
        r.log(f"final fix {fid} queued")
        self.set_phase("review", WAITING_ON=None)  # the next advance, after QUEUE_EMPTY, starts a new review
        raise Done(RUN_QUEUE, f"RUN_QUEUE {fid}")

    # -- the user's answers
    def reply(self, word):
        r = self.r
        phase = self.phase()
        if not phase.startswith("prompt:"):
            raise Done(ERROR, f"ERROR NO_PROMPT_OPEN phase={phase}")
        accepted = r.need("FIN_PROMPT_ACCEPT").split()
        if word not in accepted:
            raise Done(PROMPT, f"PROMPT {r.need('FIN_PROMPT_FILE')} {' '.join(accepted)}")
        name = phase.split(":", 1)[1]
        r.log(f"final prompt {name}: {word}")
        r.set(WAITING_ON=None)
        if word == "stop":
            raise Done(STOP, "STOP user stopped the run during the final review")
        if name == "void":
            r.set(VOIDS_FINAL=0)
        if name == "rounds":
            r.set(FINAL_FIX_ROUNDS=2)
            return self.run_queue(r.need("FIN_FIX"))
        if name == "accept-failing":
            r.log("user accepted a failing verification")
            return self.verified(r.need("FIN_VERIFY_LOG"))
        self.set_phase("review")

    # -- driver
    def advance(self, architect_result=None, reply_word=None, moved=False):
        if moved:
            docs_moved(self.r)
        if architect_result:
            self.take_result(architect_result)
        elif reply_word:
            self.reply(reply_word)
        for _ in range(50):
            phase = self.phase()
            if phase == "verified":
                raise Done(VERIFIED, f"VERIFIED {self.r.need('FIN_VERIFY_LOG')}")
            if phase == "job":
                raise Done(ESCALATE, self.r.need("FIN_LINE"))
            if phase.startswith("prompt:"):
                raise Done(PROMPT, f"PROMPT {self.r.need('FIN_PROMPT_FILE')} {self.r.need('FIN_PROMPT_ACCEPT')}")
            handler = {"start": self.do_start, "review": self.do_review, "wait": self.do_wait,
                       "verify": self.do_verify, "probe": self.do_probe}.get(phase)
            if handler is None:
                raise Done(ERROR, f"ERROR UNKNOWN_PHASE {phase}")
            handler()
        raise Done(ERROR, "ERROR LOOP_LIMIT")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    sub = p.add_subparsers(dest="op", required=True)
    adv = sub.add_parser("advance")
    adv.add_argument("--architect-result")
    adv.add_argument("--reply")
    adv.add_argument("--docs-moved", action="store_true")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    try:
        Finish(a.state).advance(a.architect_result, a.reply.strip().strip('"').lower() if a.reply else None,
                                a.docs_moved)
        code, line = ERROR, "ERROR NO_STATUS"
    except Done as d:
        code, line = d.code, d.line
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
