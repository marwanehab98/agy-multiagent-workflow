"""Run the coordinator's Step 8 items 1-3 (starting-state check, final review, final-fix rounds,
verification, drift check) synchronously via headless `agy -p`.

Usage:
  python3 -I run_finish.py --state <RUN_DIR>/state.json advance
  python3 -I run_finish.py --state <file> advance --architect-result <file with the RESULT line>
  python3 -I run_finish.py --state <file> advance --reply <the user's one-word reply>
  python3 -I run_finish.py --state <file> advance --answer <architect answer file after CONSULT>
  python3 -I run_finish.py --state <file> advance --docs-moved

The last line of stdout is the status.
Exit 0   VERIFIED <log>                    the VERIFY commands passed (or the user accepted a failing
                                           run); go to Step 8 item 4 with <log> as the verify log
Exit 20  ARCHITECT_JOB <job> <input>       send that architect job with INPUT=<input>. Jobs:
                                           final-review <verdict file>, verification-failed <log>,
                                           fix-meta <validate output>, drift-done <findings|failed: file>
Exit 21  RUN_QUEUE <F id>                  queue.txt now holds only that id: run run_task.py (Step 4);
                                           on QUEUE_EMPTY call advance again for a new final review
Exit 22  CONSULT <ask_file>                a worker exited with ASK:; run architect job `consult` with
                                           INPUT=<ask_file>, then call advance --answer <answer_file>
Exit 30  PROMPT <file> <reply> [<reply>]   show <file> to the user exactly; pass the reply back
Exit 40  STOP <reason>                     dispatch nothing more; coordinator Step 8 item 7
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import (CONSULT, ESCALATE, PROMPT, STOP, ERROR, AgyRunner, Done, Run, build, first_words, next_free,  # noqa: E402
                    pick_verdict, save_body, tool, verdict_line)
from probe import Prober, docs_moved, launch_problems, review_pairs  # noqa: E402
from pairs import parse_pair  # noqa: E402

VERIFIED, RUN_QUEUE = 0, 21


def ac_ids(*files):
    """Every AC-<n> id in the design and decisions log, in first-seen order."""
    seen = []
    for f in files:
        p = Path(f)
        for m in re.findall(r"\bAC-\d+\b", p.read_text(encoding="utf-8") if p.is_file() else ""):
            if m not in seen:
                seen.append(m)
    return " ".join(seen) or "none (the design names no AC ids; check its acceptance criteria as written)"


class Finish:
    def __init__(self, state_path):
        self.r = Run(state_path)
        self.r.require_new_format()
        self.runner = AgyRunner(self.r)

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

    def ask_consult(self, kind, pair, spec, conv_id, body):
        r = self.r
        ask = next_free(r.run_dir / "asks", f"finish-{kind}-{{n}}.md")
        save_body(body, ask, header=f"finish phase ({kind})")
        r.set(FIN_ASK_CONV=conv_id, FIN_ASK_KIND=kind, FIN_ASK_PAIR=pair, FIN_ASK_SPEC=spec, FIN_ASK_FILE=ask)
        self.set_phase("consult", WAITING_ON="architect")
        raise Done(CONSULT, f"CONSULT {ask}")

    # -- item 1: starting state
    def do_start(self):
        r = self.r
        r.set(PHASE="finish")
        for key in [k for k in r.s if k.startswith("IMPL_CONVERSATION_ID_")]:
            r.set(**{key: None})
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
                      "CONVENTION_PATHS": r.need("CONVENTION_PATHS"), "OUTPUT_FILE": out,
                      "AC_IDS": ac_ids(r.need("DESIGN_FILE"), r.need("DECISIONS_LOG"))},
                     {"DESIGN": r.need("DESIGN_FILE"), "DECISIONS_LOG": r.need("DECISIONS_LOG")})
        pair = r.need("ROLE_ARCHITECT")
        r.set(FIN_OUT=out, FIN_HEAD=head, FIN_SPEC=spec, FIN_PAIR=pair)
        result = self.runner.run(spec, pair=pair, worktree=r.impl)
        r.log(f"final review {n} dispatched {pair}")
        self.settle_review(result)

    def settle_review(self, result):
        r = self.r
        head = r.need("FIN_HEAD")
        if result[0] == "usage":
            tool("discard-changes.sh", r.impl, head)
            self.prompt("usage-limit",
                        f"The final reviewer hit its model's usage limit: {result[1]}\n"
                        "Reply \"retry\" once the limit has reset, or \"stop\" to stop the run.", ["retry", "stop"])
        if result[0] == "stopped":
            tool("discard-changes.sh", r.impl, head)
            return self.void("stopped without a verdict")
        if result[0] == "ask":
            rc, _ = tool("check-tree.sh", r.impl, head)
            if rc != 0:
                tool("discard-changes.sh", r.impl, head)
                return self.void("changed the worktree")
            return self.ask_consult("final-review", r.need("FIN_PAIR"), r.need("FIN_SPEC"), result[1], result[2])
        if result[1] != "succeeded":
            tool("discard-changes.sh", r.impl, head)
            return self.void("failed without a verdict")
        n = r.need("FINAL_N")
        out = Path(r.need("FIN_OUT"))
        settle = save_body(result[2], r.run_dir / "results" / f"final-settle-{n}.md")
        verdict = pick_verdict(out, settle)
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
            if r.get("DRIFT_CHECK") == "yes" and not r.get("DRIFT_DONE"):
                return self.set_phase("drift")
            return self.verified(log)
        if rc == 1:
            self.job("verification-failed", log)
        raise Done(ERROR, f"ERROR GATE {out}")

    # -- the optional drift check (DRIFT_CHECK=yes): once, after verification first passes
    def drift_skills(self):
        r = self.r
        harness = parse_pair(r.need("ROLE_EGM")).harness
        rc, out = tool("harnesses.py", "skill-dir", "--catalog", r.need("CATALOG"), "--harness", harness,
                       "--skill", "egm-drift-report")
        return str(Path(out).parent) if rc == 0 else None

    def do_drift(self):
        r = self.r
        skills = self.drift_skills()
        if not skills:
            note = r.run_dir / "results" / "drift-failed.md"
            note.write_text(f"The EGM pick's harness has no egm-drift-report skill ({r.need('ROLE_EGM')}).\n",
                            encoding="utf-8")
            r.set(DRIFT_DONE="yes")
            self.job("drift-done", f"failed: {note}")
        out = r.run_dir / "drift-1.md"
        spec = build("drift.txt", next_free(r.run_dir / "specs", "drift-{n}.txt"),
                     {"EGM_SKILLS": skills, "DESIGN_FILE": r.need("DESIGN_FILE"),
                      "DECISIONS_LOG": r.need("DECISIONS_LOG"), "OUTPUT_FILE": out})
        pair = r.need("ROLE_EGM")
        r.set(FIN_OUT=out, FIN_SPEC=spec, FIN_PAIR=pair)
        result = self.runner.run(spec, pair=pair, worktree=r.impl)
        r.log(f"drift check dispatched {pair}")
        self.settle_drift(result)

    def settle_drift(self, result):
        r = self.r
        head = r.need("LAST_MERGED")
        rc, _ = tool("check-tree.sh", r.impl, head)
        if rc != 0:
            tool("discard-changes.sh", r.impl, head)
            r.log("drift check changed the worktree; changes discarded")
            if result[0] == "ask":
                result = ("stopped", "drift check changed the worktree before asking", "", result[3])
        if result[0] == "ask":
            return self.ask_consult("drift", r.need("FIN_PAIR"), r.need("FIN_SPEC"), result[1], result[2])
        if result[0] == "usage":
            self.prompt("drift-usage-limit",
                        f"The drift check hit its model's usage limit: {result[1]}\n"
                        "Reply \"retry\" once the limit has reset, or \"stop\" to stop the run.",
                        ["retry", "stop"])
        out = Path(r.need("FIN_OUT"))
        ok = result[0] == "done" and result[1] == "succeeded" and out.is_file() and out.read_text().strip()
        if ok:
            r.set(DRIFT_DONE="yes", WAITING_ON=None)
            r.log(f"drift check settled succeeded {out}")
            self.job("drift-done", out)
        note = save_body(result[2], next_free(r.run_dir / "results", "drift-failed-{n}.md")) if result[0] == "done" \
            else next_free(r.run_dir / "results", "drift-failed-{n}.md")
        if result[0] != "done":
            note.write_text(f"{result[1]}\n", encoding="utf-8")
        r.log(f"drift check failed: {note}")
        if r.incr("DRIFT_RETRY") < 2:
            return self.set_phase("drift")
        r.set(DRIFT_DONE="yes", WAITING_ON=None)
        self.job("drift-done", f"failed: {note}")

    def take_answer(self, answer_path):
        r = self.r
        if self.phase() != "consult":
            raise Done(ERROR, f"ERROR NO_CONSULT_OPEN phase={self.phase()}")
        p = Path(answer_path)
        if not p.is_file():
            raise Done(ERROR, f"ERROR MISSING_FILE {p}")
        kind = r.need("FIN_ASK_KIND")
        pair = r.need("FIN_ASK_PAIR")
        spec = r.need("FIN_ASK_SPEC")
        conv = r.get("FIN_ASK_CONV", "")
        ans = p.read_text(encoding="utf-8")
        result = self.runner.try_resume(f"Architect answer:\n\n{ans}", pair, r.impl, conv)
        if result is None:
            spec_text = Path(spec).read_text(encoding="utf-8")
            result = self.runner.run(f"{spec_text}\n\nArchitect answer to your question:\n{ans}",
                                     pair=pair, worktree=r.impl)
        r.set(FIN_ASK_CONV=None, FIN_ASK_KIND=None, FIN_ASK_PAIR=None, FIN_ASK_SPEC=None, FIN_ASK_FILE=None)
        if kind == "drift":
            self.settle_drift(result)
        else:
            self.settle_review(result)

    def verified(self, log):
        self.set_phase("verified", FIN_VERIFY_LOG=log, WAITING_ON=None)
        raise Done(VERIFIED, f"VERIFIED {log}")

    # -- the architect's answers
    def take_result(self, path):
        r = self.r
        words = first_words(path)
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
        if job == "drift-done" and kind == "FINAL_FIX" and len(words) == 3:
            return self.validate_fix(words[2])
        if job == "drift-done" and kind == "FINAL_CLEAN" and len(words) == 2:
            return self.verified(r.need("FIN_VERIFY_LOG"))
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
            Prober(r, self.runner).advance(review_pairs(r, [fid]))
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
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
        if name == "drift-usage-limit":
            return self.set_phase("drift")
        self.set_phase("review")

    # -- driver
    def advance(self, architect_result=None, reply_word=None, answer_path=None, moved=False):
        if moved:
            docs_moved(self.r)
        if architect_result:
            self.take_result(architect_result)
        elif reply_word:
            self.reply(reply_word)
        elif answer_path:
            self.take_answer(answer_path)
        for _ in range(50):
            phase = self.phase()
            if phase == "verified":
                raise Done(VERIFIED, f"VERIFIED {self.r.need('FIN_VERIFY_LOG')}")
            if phase == "job":
                raise Done(ESCALATE, self.r.need("FIN_LINE"))
            if phase == "consult":
                raise Done(CONSULT, f"CONSULT {self.r.need('FIN_ASK_FILE')}")
            if phase.startswith("prompt:"):
                raise Done(PROMPT, f"PROMPT {self.r.need('FIN_PROMPT_FILE')} {self.r.need('FIN_PROMPT_ACCEPT')}")
            handler = {"start": self.do_start, "review": self.do_review, "verify": self.do_verify,
                       "probe": self.do_probe, "drift": self.do_drift}.get(phase)
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
    adv.add_argument("--answer")
    adv.add_argument("--docs-moved", action="store_true")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    try:
        Finish(a.state).advance(a.architect_result, a.reply.strip().strip('"').lower() if a.reply else None,
                                a.answer, a.docs_moved)
        code, line = ERROR, "ERROR NO_STATUS"
    except Done as d:
        code, line = d.code, d.line
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
