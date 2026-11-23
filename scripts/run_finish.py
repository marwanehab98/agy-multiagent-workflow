"""Run the capsule communicator's Step 5 items 1-3 (starting-state check, final review, final-fix rounds,
verification, drift check) synchronously via headless CLI workers.

Usage:
  python3 -I run_finish.py --state <RUN_DIR>/state.json advance
  python3 -I run_finish.py --state <file> advance --director-result <file with the RESULT line>
  python3 -I run_finish.py --state <file> advance --reply <the user's one-word reply>
  python3 -I run_finish.py --state <file> advance --answer <flight director answer file after CONSULT>
  python3 -I run_finish.py --state <file> advance --docs-moved

The last line of stdout is the status.
Exit 0   VERIFIED <log>                    the VERIFY commands passed (or the user accepted a failing
                                           run); go to Step 5 item 4 with <log> as the verify log
Exit 20  DIRECTOR_JOB <job> <input>        send that flight director job with INPUT=<input>. Jobs:
                                           final-review <verdict file>, verification-failed <log>,
                                           fix-meta <validate output>, drift-done <findings|failed: file>
Exit 21  RUN_QUEUE <F id>                  queue.txt now holds only that id: run run_task.py (Step 4);
                                           on QUEUE_EMPTY call advance again for a new final review
Exit 22  CONSULT <ask_file>                a worker exited with ASK:; run flight director job `consult` with
                                           INPUT=<ask_file>, then call advance --answer <answer_file>
Exit 30  PROMPT <file> <reply> [<reply>]   show <file> to the user exactly; pass the reply back
Exit 40  STOP <reason>                     dispatch nothing more; capsule communicator Step 5 item 7
Exit 41  PAUSED <prompt file>              the user paused at a usage-limit prompt; nothing changed, and the next
                                           call shows the same prompt
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import (CONSULT, ESCALATE, PROMPT, STOP, ERROR, USAGE_REPLIES, AgyRunner, Done, Run, paused,  # noqa: E402
                    answer_worker, build,
                    bundled_skills_dir, check_repo_trees, discard_repo_trees, drive, first_words, next_free,
                    pick_verdict, read_answer, repo_key, save_ask, save_body, tool, usage_prompt, verdict_line,
                    void_prompt, write_atomic, write_prompt)
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
        line = f"DIRECTOR_JOB {name} {text}"
        self.set_phase("job", FIN_JOB=name, FIN_LINE=line, WAITING_ON="director")
        raise Done(ESCALATE, line)

    def prompt(self, name, text, accepted):
        f = write_prompt(self.r, text)
        self.set_phase(f"prompt:{name}", FIN_PROMPT_FILE=f, FIN_PROMPT_ACCEPT=" ".join(accepted), WAITING_ON="user")
        raise Done(PROMPT, f"PROMPT {f} {' '.join(accepted)}")

    def ask_consult(self, kind, pair, spec, conv_id, body):
        r = self.r
        ask = save_ask(r, f"finish-{kind}", body, header=f"finish phase ({kind})")
        r.set(FIN_ASK_CONV=conv_id, FIN_ASK_KIND=kind, FIN_ASK_PAIR=pair, FIN_ASK_SPEC=spec, FIN_ASK_FILE=ask)
        self.set_phase("consult", WAITING_ON="director")
        raise Done(CONSULT, f"CONSULT {ask}")

    def _check_impl_trees(self, head=None):
        if self.r.is_multi_repo:
            return check_repo_trees(self.r, "impl")
        return tool("check-tree.sh", self.r.impl, head or self.r.need("LAST_MERGED"))

    # -- item 1: starting state
    def do_start(self):
        r = self.r
        r.set(PHASE="finish")
        for key in [k for k in r.s if k.startswith("IMPL_CONVERSATION_ID_")]:
            r.set(**{key: None})
        rc, out = self._check_impl_trees()
        if rc != 0:
            raise Done(ERROR, f"ERROR TREE_NOT_CLEAN {out}")
        self.set_phase("review")

    def _discard_or_error(self, head=None):
        if self.r.is_multi_repo:
            rc, out = discard_repo_trees(self.r, "impl")
        else:
            rc, out = tool("discard-changes.sh", self.r.impl, head or self.r.need("LAST_MERGED"))
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")

    # -- item 2: the final review
    def do_review(self):
        r = self.r
        n = r.incr("FINAL_N")
        out = r.run_dir / "verdicts" / f"final-{n}.md"
        if r.is_multi_repo:
            repos = r.touched_repos() or r.repos
            review_range = ("; ".join(f"{repo}: {r.repo_initial_base(repo)}..{r.repo_last_merged(repo)}" for repo in repos)
                            + " (run git -C <repo> diff <base>..<head> in each repo directory)")
            conventions = "; ".join(f"{repo}: {r.repo_convention_paths(repo)}" for repo in repos)
            head = "multi"
        else:
            head = r.need("LAST_MERGED")
            review_range = f"{r.need('INITIAL_BASE_SHA')}..{head}"
            conventions = r.need("CONVENTION_PATHS")
        spec = build("final-review.txt", next_free(r.run_dir / "specs", "final-review-{n}.txt"),
                     {"REVIEW_RANGE": review_range, "CONVENTION_PATHS": conventions, "OUTPUT_FILE": out,
                      "AC_IDS": ac_ids(r.need("DESIGN_FILE"), r.need("DECISIONS_LOG"))},
                     {"DESIGN": r.need("DESIGN_FILE"), "DECISIONS_LOG": r.need("DECISIONS_LOG")})
        pair = r.need("ROLE_DIRECTOR")
        self.set_phase("reviewing", FIN_OUT=out, FIN_HEAD=head, FIN_SPEC=spec, FIN_PAIR=pair)
        r.log(f"final review {n} dispatched {pair}")
        result = self.runner.run(spec, pair=pair, worktree=r.impl, access="read")
        self.settle_review(result)

    def settle_review(self, result):
        r = self.r
        head = None if r.is_multi_repo else r.need("FIN_HEAD")
        if result[0] == "usage":
            self._discard_or_error(head)
            self.prompt("usage-limit", usage_prompt("The final reviewer", result[1]), USAGE_REPLIES)
        if result[0] == "stopped":
            self._discard_or_error(head)
            return self.void("stopped without a verdict")
        if result[0] == "ask":
            rc, _ = self._check_impl_trees(head)
            if rc != 0:
                self._discard_or_error(head)
                return self.void("changed the worktree")
            return self.ask_consult("final-review", r.need("FIN_PAIR"), r.need("FIN_SPEC"), result[1], result[2])
        if result[1] != "succeeded":
            self._discard_or_error(head)
            return self.void("failed without a verdict")
        n = r.need("FINAL_N")
        out = Path(r.need("FIN_OUT"))
        settle = save_body(result[2], r.run_dir / "results" / f"final-settle-{n}.md")
        verdict = pick_verdict(out, settle)
        rc, _ = self._check_impl_trees(head)
        if rc != 0:
            self._discard_or_error(head)
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
            self.prompt("void", void_prompt("The final review", why), ["retry", "stop"])
        self.set_phase("review")

    # -- item 3: verification
    def do_verify(self):
        r = self.r
        k = r.incr("VERIFY_K")
        log = r.run_dir / f"verify-{k}.log"
        if r.is_multi_repo:
            repos = r.touched_repos() or r.repos
            any_failed = False
            sections = []
            for repo in repos:
                rk = repo_key(repo)
                sub_log = r.run_dir / f"verify-{k}-{rk}.log"
                rc, out = tool("gate.py", "--flight-manifest", r.need("FLIGHT_MANIFEST"), "--worktree", r.repo_impl(repo),
                               "--head", r.repo_last_merged(repo), "--log", str(sub_log), "--prefix", "VERIFY",
                               "--repo", rk)
                r.log(f"verify {k} [{repo}] exit={rc} {sub_log}")
                if rc not in (0, 1):
                    raise Done(ERROR, f"ERROR GATE [{repo}] {out}")
                if rc == 1:
                    any_failed = True
                body = sub_log.read_text(encoding="utf-8") if sub_log.is_file() else out
                sections.append(f"=== repo: {repo} (exit={rc}) ===\n{body}\n")
            log.write_text("\n".join(sections), encoding="utf-8")
            rc = 1 if any_failed else 0
            out = ""
        else:
            rc, out = tool("gate.py", "--flight-manifest", r.need("FLIGHT_MANIFEST"), "--worktree", r.impl,
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
        harness = parse_pair(r.need("ROLE_SAFETY")).harness
        rc, out = tool("harnesses.py", "skill-dir", "--catalog", r.need("CATALOG"), "--harness", harness,
                       "--skill", "spec-drift")
        if rc == 0:
            return str(Path(out).parent)
        return bundled_skills_dir("spec-drift")

    def do_drift(self):
        r = self.r
        skills = self.drift_skills()
        if not skills:
            note = r.run_dir / "results" / "drift-failed.md"
            note.write_text(f"The Range Safety pick's harness has no spec-drift skill ({r.need('ROLE_SAFETY')}).\n",
                            encoding="utf-8")
            r.set(DRIFT_DONE="yes")
            self.job("drift-done", f"failed: {note}")
        out = next_free(r.run_dir, "drift-{n}.md")
        spec = build("drift.txt", next_free(r.run_dir / "specs", "drift-{n}.txt"),
                     {"SAFETY_SKILLS": skills, "DESIGN_FILE": r.need("DESIGN_FILE"),
                      "DECISIONS_LOG": r.need("DECISIONS_LOG"), "OUTPUT_FILE": out})
        pair = r.need("ROLE_SAFETY")
        self.set_phase("drifting", FIN_OUT=out, FIN_SPEC=spec, FIN_PAIR=pair)
        r.log(f"drift check dispatched {pair}")
        result = self.runner.run(spec, pair=pair, worktree=r.impl, access="read")
        self.settle_drift(result)

    def settle_drift(self, result):
        r = self.r
        head = None if r.is_multi_repo else r.need("LAST_MERGED")
        rc, _ = self._check_impl_trees(head)
        if rc != 0:
            self._discard_or_error(head)
            r.log("drift check changed the worktree; changes discarded")
            if result[0] == "ask":
                result = ("stopped", "drift check changed the worktree before asking", "", result[3])
        if result[0] == "ask":
            return self.ask_consult("drift", r.need("FIN_PAIR"), r.need("FIN_SPEC"), result[1], result[2])
        if result[0] == "usage":
            self.prompt("drift-usage-limit", usage_prompt("The drift check", result[1]), USAGE_REPLIES)
        out = Path(r.need("FIN_OUT"))
        ok = result[0] == "done" and result[1] == "succeeded" and out.is_file() and out.read_text(encoding="utf-8").strip()
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
        ans = read_answer(answer_path)
        kind = r.need("FIN_ASK_KIND")
        pair = r.need("FIN_ASK_PAIR")
        spec = r.need("FIN_ASK_SPEC")
        conv = r.get("FIN_ASK_CONV", "")
        r.set(FIN_ASK_CONV=None, FIN_ASK_KIND=None, FIN_ASK_PAIR=None, FIN_ASK_SPEC=None, FIN_ASK_FILE=None)
        self.set_phase("drifting" if kind == "drift" else "reviewing", WAITING_ON=None)
        head = None if r.is_multi_repo else (r.need("LAST_MERGED") if kind == "drift" else r.need("FIN_HEAD"))
        result = answer_worker(self.runner, ans, pair, r.impl, conv, spec,
                               lambda: self._discard_or_error(head), access="read")
        if kind == "drift":
            self.settle_drift(result)
        else:
            self.settle_review(result)

    def verified(self, log):
        self.set_phase("verified", FIN_VERIFY_LOG=log, WAITING_ON=None)
        raise Done(VERIFIED, f"VERIFIED {log}")

    # -- the flight director's answers
    def take_result(self, path):
        r = self.r
        words = first_words(path)
        phase, job = self.phase(), r.get("FIN_JOB")
        if phase != "job" or words[:1] != ["RESULT:"] or len(words) < 2:
            raise Done(ERROR, f"ERROR UNEXPECTED_DIRECTOR_RESULT phase={phase}: {' '.join(words)[:300]}")
        kind = words[1]
        r.set(WAITING_ON=None)
        if kind == "STOP":
            r.log(f"final {job}: flight director chose STOP")
            raise Done(STOP, "STOP flight director stopped the run during the final review")
        if job in ("final-review", "verification-failed"):
            if kind == "FINAL_FIX" and len(words) == 3:
                return self.validate_fix(words[2])
            if kind == "FINAL_CLEAN" and len(words) == 2:
                if job == "final-review":
                    return self.set_phase("verify")
                log = r.need("FIN_VERIFY_LOG")
                self.prompt("accept-failing",
                            f"Verification still fails, and the flight director found nothing to fix: {log}. "
                            "Reply \"continue\" to open the PR anyway, or \"stop\" to stop the run.",
                            ["continue", "stop"])
        if job == "drift-done" and kind == "FINAL_FIX" and len(words) == 3:
            return self.validate_fix(words[2])
        if job == "drift-done" and kind == "FINAL_CLEAN" and len(words) == 2:
            return self.verified(r.need("FIN_VERIFY_LOG"))
        if job == "fix-meta" and kind == "META_FIXED" and len(words) == 2:
            return self.validate_fix(r.need("FIN_FIX"))
        raise Done(ERROR, f"ERROR UNEXPECTED_DIRECTOR_RESULT job={job}: {' '.join(words)[:300]}")

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
        """Test-launch the final-fix task's reviewer pairs; a failed launch goes to the flight director as fix-meta."""
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
        write_atomic(r.run_dir / "queue.txt", f"{fid}\n")
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
        if word == "pause":
            paused(r, r.need("FIN_PROMPT_FILE"))
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
    def advance(self, director_result=None, reply_word=None, answer_path=None, moved=False):
        if moved:
            docs_moved(self.r)
        if director_result:
            self.take_result(director_result)
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
            if phase == "reviewing":
                self._discard_or_error()
                self.do_review()
                continue
            if phase == "drifting":
                self._discard_or_error()
                self.do_drift()
                continue
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
    adv.add_argument("--director-result")
    adv.add_argument("--reply")
    adv.add_argument("--answer")
    adv.add_argument("--docs-moved", action="store_true")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    return drive(a.state, lambda events: Finish(a.state).advance(
        a.director_result, a.reply.strip().strip('"').lower() if a.reply else None, a.answer, a.docs_moved))


if __name__ == "__main__":
    sys.exit(main())
