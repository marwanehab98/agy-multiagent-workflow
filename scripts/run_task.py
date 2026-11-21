"""Run the per-task loop (coordinator Steps 4-7) synchronously via headless `agy -p`.

Each call runs every deterministic action it can (start workers via `agy -p`, save their results,
check the tree, run the gate, run both reviews, count rounds, merge) and exits at the first point
that needs the architect or the user. All its state lives in the coordinator's state.json, so a
call after a crash or a context compaction carries on.

Usage:
  python3 -I run_task.py --state <RUN_DIR>/state.json advance
  python3 -I run_task.py --state <file> advance --architect-result <file with the RESULT line>
  python3 -I run_task.py --state <file> advance --reply <the user's one-word reply>
  python3 -I run_task.py --state <file> advance --answer <architect answer file after CONSULT>
  python3 -I run_task.py --state <file> advance --docs-moved

The last line of stdout is the status; earlier lines are events (MERGED ...).
Exit 0   QUEUE_EMPTY                       every queued task is merged
Exit 20  ESCALATE <input>                  send architect job escalate with INPUT=<input>
Exit 21  FIX_META <validate output file>   send architect job fix-meta with INPUT=<file>
Exit 22  CONSULT <ask_file>                a worker exited with ASK:; run architect job `consult` with
                                           INPUT=<ask_file>, then call advance --answer <answer_file>
Exit 30  PROMPT <file> <reply> [<reply>]   show <file> to the user exactly; pass the reply back
Exit 40  STOP <reason>                     dispatch nothing more; coordinator Step 8 item 7
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage

Rules the loop enforces:
- Uncommitted files after an implementer settles get one commit worker per implement or fix round
  (COMMIT_ROUNDS_<id>); whatever it leaves uncommitted is removed with discard-changes.sh and logged.
- One task at a time. A fresh implementer per task, kept across fix/commit rounds via `--conversation`
  (falling back to a fresh `agy -p` run if resume fails).
- The gate (gate.py) runs before every review; a failing gate goes straight to a fix round with the log.
- Stage 1 review, then Stage 2. After a gate fix passes, the review resumes at REVIEW_STAGE_<id>.
- Escalate after two fix rounds for one stage (STAGE_FIX_ROUNDS_<id>_S<n>, reset when that stage passes),
  two gate fix rounds in a row (GATE_FIX_ROUNDS_<id>), or four fix rounds of any kind (TOTAL_FIX_ROUNDS_<id>).
  An escalation attempt resets all three.
- A reviewer that changes the worktree, or stops/fails without settling, is void and replaced; the third
  void in a row prompts retry or stop. A missing VERDICT line gets one new reviewer; a second counts as FIX.
- A usage limit pauses the task with a retry/stop prompt instead of counting as a failure.
- The escalation pair must be the ladder's next rung (validate_meta.py pair) and its reset sha must lie
  between the task's base and the worktree's HEAD; two rejected answers prompt the user.
- REPLAN is checked the same way and validated (with fix-meta) before the queue changes.
- Merges go only through merge-task.sh (fast-forward, compare-and-swap on the feature branch).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import (QUEUE_EMPTY, ESCALATE, FIX_META, CONSULT, PROMPT, STOP, ERROR, AgyRunner, Done, Run,  # noqa: E402
                    build, first_words, git, next_free, pick_verdict, run, save_body, tool, verdict_line)
from pairs import ladder, parse_pair  # noqa: E402
from probe import Prober, docs_moved, launch_problems, review_pairs  # noqa: E402


def read_meta(path):
    meta = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            meta[k.strip()] = v.strip()
    return meta


# ---------------------------------------------------------------- the loop

class Loop:
    def __init__(self, state_path):
        self.r = Run(state_path)
        self.r.require_new_format()
        self.runner = AgyRunner(self.r)
        self.events = []

    # -- queue
    def queue_path(self):
        return self.r.run_dir / "queue.txt"

    def queue(self):
        p = self.queue_path()
        return p.read_text(encoding="utf-8").split() if p.exists() else []

    def write_queue(self, ids):
        self.queue_path().write_text(" ".join(ids) + "\n", encoding="utf-8")

    def phase(self, t):
        return self.r.get(f"LOOP_{t}", "start")

    def set_phase(self, t, phase, **kv):
        self.r.set(**{f"LOOP_{t}": phase, "STEP": f"run_task {t} {phase}"}, **kv)

    def meta(self, t):
        return read_meta(self.r.task_file(t, ".meta"))

    # -- implementers
    def start_impl(self, t, spec, kind, title, reuse):
        pair = self.r.need(f"PAIR_{t}")
        self.r.set(**{f"LAST_IMPL_SPEC_{t}": spec, f"LAST_IMPL_KIND_{t}": kind})
        in_flight = "committing" if kind == "commit" else ("fixing" if kind == "fix" else "impl")
        self.set_phase(t, in_flight)
        result = None
        reused = False
        if reuse:
            conv = self.r.get(f"IMPL_CONVERSATION_ID_{t}")
            if conv:
                result = self.runner.try_resume(spec, pair, self.r.impl, conv)
                if result is not None:
                    reused = True
        if result is None:
            result = self.runner.run(spec, pair=pair, worktree=self.r.impl)
        if result[3]:
            self.r.set(**{f"IMPL_CONVERSATION_ID_{t}": result[3]})
        self.r.log(f"{t} {kind} {'reused' if reused else 'dispatched'} {pair}")
        self.after_impl(t, kind, result)

    def do_start(self, t):
        r = self.r
        rc, out = tool("branch-at.sh", r.need("MAIN_CHECKOUT"), r.need("FEATURE_BRANCH"), r.need("LAST_MERGED"))
        if rc == 1:
            self.prompt_not_ff(t, out, back_to="start")
        if rc != 0:
            raise Done(ERROR, f"ERROR BRANCH_AT {out}")
        head = git(r.impl, "rev-parse", "HEAD")
        rc, out = tool("check-tree.sh", r.impl, head)
        if rc != 0 or run(["git", "-C", r.impl, "merge-base", "--is-ancestor", r.need("LAST_MERGED"), head]).returncode:
            raise Done(ERROR, f"ERROR IMPL_WORKTREE_NOT_READY head={head} last_merged={r.need('LAST_MERGED')} {out}")
        meta = self.meta(t)
        spec = build("implement.txt", r.run_dir / "specs" / f"{t}-implement.txt",
                     {"TASK_TITLE": meta["TITLE"]},
                     {"TASK": r.task_file(t, ".md"), "DECISIONS": r.task_file(t, "-decisions.md")})
        r.set(CURRENT_TASK=t, **{f"BASE_SHA_{t}": head, f"IMPLEMENT_SPEC_{t}": spec,
                                 f"PAIR_{t}": meta["IMPLEMENTER"], f"ATTEMPT_{t}": 0,
                                 f"REVIEW_STAGE_{t}": 1, f"GATE_FAILED_{t}": "no",
                                 f"STAGE_FIX_ROUNDS_{t}_S1": 0, f"STAGE_FIX_ROUNDS_{t}_S2": 0,
                                 f"GATE_FIX_ROUNDS_{t}": 0, f"TOTAL_FIX_ROUNDS_{t}": 0, f"FAIL_MSG_{t}": None,
                                 f"REJECTS_{t}": 0, f"IMPL_CONVERSATION_ID_{t}": None,
                                 f"NO_VERDICT_{t}_S1": 0, f"NO_VERDICT_{t}_S2": 0,
                                 f"VOIDS_{t}_S1": 0, f"VOIDS_{t}_S2": 0})
        self.start_impl(t, spec, "impl", meta["TITLE"], reuse=False)

    def ask_consult(self, t, kind, pair, spec, conv_id, body, stage=None):
        r = self.r
        ask = next_free(r.run_dir / "asks", f"{t}-{kind}-{{n}}.md")
        save_body(body, ask, header=f"task={t} {self.meta(t)['TITLE']}")
        r.set(**{f"TASK_ASK_CONV_{t}": conv_id, f"TASK_ASK_KIND_{t}": kind,
                 f"TASK_ASK_PAIR_{t}": pair, f"TASK_ASK_SPEC_{t}": spec,
                 f"TASK_ASK_FILE_{t}": ask, f"TASK_ASK_STAGE_{t}": stage})
        self.set_phase(t, "consult", WAITING_ON="architect")
        raise Done(CONSULT, f"CONSULT {ask}")

    def after_impl(self, t, kind, result):
        r = self.r
        if result[0] == "usage":
            self.prompt_usage(t, kind, result[1])
        if result[0] == "ask":
            self.ask_consult(t, kind, r.need(f"PAIR_{t}"), r.need(f"LAST_IMPL_SPEC_{t}"), result[1], result[2])
        if result[0] == "stopped":
            note = next_free(r.run_dir / "results", f"{t}-stopped-{{n}}.md")
            note.write_text(result[1] + "\n", encoding="utf-8")
            r.set(**{f"FAIL_MSG_{t}": note})
            return self.escalate(t)
        _, outcome, body, _ = result
        if outcome != "succeeded":
            r.set(**{f"FAIL_MSG_{t}": save_body(body, next_free(r.run_dir / "results", f"{t}-failed-{{n}}.md"))})
            r.log(f"{t} {kind} settled failed")
            return self.escalate(t)
        r.log(f"{t} {kind} settled succeeded")
        if kind != "commit":
            r.set(**{f"COMMIT_ROUNDS_{t}": 0})  # each implement or fix round gets one commit round
        self.set_phase(t, "check")

    def do_check(self, t):
        r = self.r
        head = git(r.impl, "rev-parse", "HEAD")
        r.set(**{f"WORKER_HEAD_{t}": head})
        rc, out = tool("check-tree.sh", r.impl, head)
        if rc == 2 and int(r.get(f"COMMIT_ROUNDS_{t}", "0")) >= 1:
            rc, out = tool("discard-changes.sh", r.impl, head)
            r.log(f"{t} removed what the commit worker left uncommitted: {' '.join(out.split())}")
            if rc != 0:
                raise Done(ERROR, f"ERROR RESET {out}")
            rc, out = tool("check-tree.sh", r.impl, head)
        if rc == 2:
            r.incr(f"COMMIT_ROUNDS_{t}")
            meta = self.meta(t)
            spec = build("commit-uncommitted.txt", next_free(r.run_dir / "specs", f"{t}-commit-{{n}}.txt"),
                         {"TASK_TITLE": meta["TITLE"]}, {"TASK": r.task_file(t, ".md")})
            return self.start_impl(t, spec, "commit", f"Commit: {meta['TITLE']}", reuse=True)
        if rc != 0:
            raise Done(ERROR, f"ERROR CHECK_TREE {out}")
        if head == r.need(f"BASE_SHA_{t}"):
            note = next_free(r.run_dir / "results", f"{t}-nocommit-{{n}}.md")
            note.write_text("settled succeeded with no commits\n", encoding="utf-8")
            r.set(**{f"FAIL_MSG_{t}": note})
            return self.escalate(t)
        log = next_free(r.run_dir / "gates", f"{t}-{{n}}.log")
        rc, out = tool("gate.py", "--run-values", r.need("RUN_VALUES"), "--worktree", r.impl,
                       "--head", head, "--log", str(log))
        r.log(f"{t} gate exit={rc} {log}")
        if rc == 1:
            r.set(**{f"GATE_FAILED_{t}": "yes"})
            return self.fix(t, log, "gate")
        if rc != 0:
            raise Done(ERROR, f"ERROR GATE {out}")
        r.set(**{f"GATE_FAILED_{t}": "no", f"GATE_FIX_ROUNDS_{t}": 0})
        self.start_review(t, int(r.get(f"REVIEW_STAGE_{t}", "1")))

    def fix(self, t, findings, kind):
        """kind is 'gate' or the review stage number (1 or 2) whose FIX verdict started this round."""
        r = self.r
        counter = f"GATE_FIX_ROUNDS_{t}" if kind == "gate" else f"STAGE_FIX_ROUNDS_{t}_S{kind}"
        if int(r.get(counter, "0")) >= 2 or int(r.get(f"TOTAL_FIX_ROUNDS_{t}", "0")) >= 4:
            return self.escalate(t)
        r.incr(counter)
        r.incr(f"TOTAL_FIX_ROUNDS_{t}")
        meta = self.meta(t)
        spec = build("fix.txt", next_free(r.run_dir / "specs", f"{t}-fix-{{n}}.txt"),
                     {"TASK_TITLE": meta["TITLE"], "BASE_SHA": r.need(f"BASE_SHA_{t}"),
                      "WORKER_HEAD": r.need(f"WORKER_HEAD_{t}")},
                     {"FINDINGS": findings, "DECISIONS": r.task_file(t, "-decisions.md"),
                      "IMPLEMENT_SPEC": r.need(f"IMPLEMENT_SPEC_{t}")})
        self.start_impl(t, spec, "fix", f"Fix: {meta['TITLE']}", reuse=True)

    # -- reviewers
    def start_review(self, t, stage):
        r = self.r
        meta = self.meta(t)
        review = next_free(r.run_dir / "verdicts", f"{t}-s{stage}-{{n}}.md")
        r.set(**{f"REVIEW_OUT_{t}": review})
        common = {"BASE_SHA": r.need(f"BASE_SHA_{t}"), "WORKER_HEAD": r.need(f"WORKER_HEAD_{t}"),
                  "OUTPUT_FILE": review}
        out = next_free(r.run_dir / "specs", f"{t}-s{stage}-{{n}}.txt")
        if stage == 1:
            spec = build("spec-review.txt", out, common,
                         {"TASK": r.task_file(t, ".md"), "DECISIONS": r.task_file(t, "-decisions.md")})
        else:
            spec = build("quality-review.txt", out, {**common, "CONVENTION_PATHS": r.need("CONVENTION_PATHS")},
                         {"SUMMARY": r.task_file(t, "-summary.md"), "DECISIONS": r.task_file(t, "-decisions.md")})
        pair = meta[f"REVIEW_{stage}"]
        r.set(**{f"REVIEW_SPEC_{t}": spec, f"REVIEW_PAIR_{t}": pair})
        self.set_phase(t, f"reviewing:{stage}")
        result = self.runner.run(spec, pair=pair, worktree=r.impl)
        r.log(f"{t} stage{stage} reviewer dispatched {pair}")
        self.after_review(t, stage, result)

    def _discard_or_error(self, head):
        rc, out = tool("discard-changes.sh", self.r.impl, head)
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")

    def after_review(self, t, stage, result):
        r = self.r
        head = r.need(f"WORKER_HEAD_{t}")
        if result[0] == "usage":
            self._discard_or_error(head)
            self.prompt_usage(t, f"review{stage}", result[1])
        if result[0] == "stopped":
            self._discard_or_error(head)
            return self.void_review(t, stage, "stopped without a verdict")
        if result[0] == "ask":
            rc, _ = tool("check-tree.sh", r.impl, head)
            if rc != 0:
                self._discard_or_error(head)
                return self.void_review(t, stage, "changed the worktree")
            self.ask_consult(t, f"review{stage}", r.need(f"REVIEW_PAIR_{t}"),
                             r.need(f"REVIEW_SPEC_{t}"), result[1], result[2], stage=str(stage))
        if result[1] != "succeeded":
            self._discard_or_error(head)
            return self.void_review(t, stage, "failed without a verdict")
        settle = save_body(result[2], next_free(r.run_dir / "results", f"{t}-s{stage}-settle-{{n}}.md"))
        verdict = pick_verdict(Path(r.need(f"REVIEW_OUT_{t}")), settle)
        rc, _ = tool("check-tree.sh", r.impl, head)
        if rc != 0:
            self._discard_or_error(head)
            verdict.rename(next_free(r.run_dir / "verdicts", f"{t}-void-s{stage}-{{n}}.md"))
            return self.void_review(t, stage, "changed the worktree")
        last = verdict_line(verdict)
        r.log(f"{t} stage{stage} {last or 'no verdict'} {verdict}")
        r.set(**{f"VOIDS_{t}_S{stage}": 0})
        if last:
            r.set(**{f"NO_VERDICT_{t}_S{stage}": 0})
        if last == "VERDICT: PASS":
            r.set(**{f"STAGE_FIX_ROUNDS_{t}_S{stage}": 0})
            if stage == 1:
                r.set(**{f"REVIEW_STAGE_{t}": 2})
                return self.start_review(t, 2)
            return self.set_phase(t, "merge")
        if last != "VERDICT: FIX" and r.incr(f"NO_VERDICT_{t}_S{stage}") < 2:
            return self.start_review(t, stage)
        r.set(**{f"REVIEW_STAGE_{t}": stage})
        return self.fix(t, verdict, stage)

    def void_review(self, t, stage, why):
        n = self.r.incr(f"VOIDS_{t}_S{stage}")
        self.r.log(f"{t} stage{stage} reviewer void: {why}")
        if n >= 3:
            self.prompt(t, "void",
                        f"Task {t} stage {stage}: three reviewers in a row were void ({why}).\n"
                        "Reply \"retry\" to start a new reviewer, or \"stop\" to stop the run.",
                        ["retry", "stop"], back_to=f"review{stage}")
        self.start_review(t, stage)

    # -- consult answer
    def take_answer(self, t, answer_path):
        r = self.r
        if self.phase(t) != "consult":
            raise Done(ERROR, f"ERROR NO_CONSULT_OPEN phase={self.phase(t)}")
        p = Path(answer_path)
        if not p.is_file():
            raise Done(ERROR, f"ERROR MISSING_FILE {p}")
        kind = r.need(f"TASK_ASK_KIND_{t}")
        pair = r.need(f"TASK_ASK_PAIR_{t}")
        spec = r.need(f"TASK_ASK_SPEC_{t}")
        conv = r.get(f"TASK_ASK_CONV_{t}", "")
        stage = r.get(f"TASK_ASK_STAGE_{t}")
        ans = p.read_text(encoding="utf-8")
        r.set(**{f"TASK_ASK_CONV_{t}": None, f"TASK_ASK_KIND_{t}": None,
                 f"TASK_ASK_PAIR_{t}": None, f"TASK_ASK_SPEC_{t}": None,
                 f"TASK_ASK_FILE_{t}": None, f"TASK_ASK_STAGE_{t}": None})
        if kind.startswith("review"):
            review_stage = int(stage or kind[-1])
            self.set_phase(t, f"reviewing:{review_stage}", WAITING_ON=None)
        else:
            in_flight = "committing" if kind == "commit" else ("fixing" if kind == "fix" else "impl")
            self.set_phase(t, in_flight, WAITING_ON=None)
        result = self.runner.try_resume(f"Architect answer:\n\n{ans}", pair, r.impl, conv)
        if result is None:
            spec_text = Path(spec).read_text(encoding="utf-8")
            result = self.runner.run(f"{spec_text}\n\nArchitect answer to your question:\n{ans}",
                                     pair=pair, worktree=r.impl)
        if kind.startswith("review"):
            self.after_review(t, review_stage, result)
        else:
            if result[3]:
                r.set(**{f"IMPL_CONVERSATION_ID_{t}": result[3]})
            self.after_impl(t, kind, result)

    # -- merge
    def do_merge(self, t):
        r = self.r
        rc, out = tool("merge-task.sh", r.need("MAIN_CHECKOUT"), r.need("FEATURE_BRANCH"),
                       r.need("LAST_MERGED"), r.need(f"WORKER_HEAD_{t}"))
        if rc == 2:
            self.prompt(t, "checked-out", f"{out}\nFix this, then tell me to continue.", ["continue"], back_to="merge")
        if rc == 3:
            self.prompt_not_ff(t, out, back_to="merge")
        if rc != 0:
            raise Done(ERROR, f"ERROR MERGE {out}")
        head = out.split("head=")[-1].strip()
        q = self.queue()
        if q and q[0] == t:
            self.write_queue(q[1:])
        r.set(LAST_MERGED=head, WAITING_ON=None, **{f"IMPL_CONVERSATION_ID_{t}": None})
        self.set_phase(t, "merged")
        r.log(f"{t} merged {head}")
        self.events.append(f"MERGED {t} {head}")

    # -- escalation
    def escalate(self, t):
        r = self.r
        r.set(**{f"IMPL_CONVERSATION_ID_{t}": None})
        a = r.incr(f"ATTEMPT_{t}")
        meta = self.meta(t)
        rungs = self.rungs(t)
        nxt = f"next rung {rungs[a - 1]}" if a <= len(rungs) else "ladder used up"
        replan = "not allowed" if meta.get("REPLANNED_FROM") else "allowed"
        hist = r.run_dir / "verdicts" / f"{t}-history.md"
        parts = [f"task={t} base={r.need(f'BASE_SHA_{t}')} head={git(r.impl, 'rev-parse', 'HEAD')}\n"]
        if r.get(f"FAIL_MSG_{t}"):
            parts.append(Path(r.get(f"FAIL_MSG_{t}")).read_text(encoding="utf-8"))
        for f in sorted((r.run_dir / "verdicts").glob(f"{t}-s*.md")) + sorted((r.run_dir / "gates").glob(f"{t}-*.log")):
            parts.append(f"\n=== {f.name}\n" + f.read_text(encoding="utf-8"))
        hist.write_text("".join(parts), encoding="utf-8")
        line = f"task {t}, tier {meta['TIER']}, attempt {a}, {nxt}, replan {replan}, history {hist}"
        self.set_phase(t, "escalating", **{f"ESC_INPUT_{t}": line, "WAITING_ON": "architect"})
        r.log(f"{t} escalation attempt {a}: {nxt}")
        raise Done(ESCALATE, f"ESCALATE {line}")

    def rungs(self, t):
        """The escalation ladder for task t, computed from the role picks and the task's first implementer."""
        r = self.r
        return ladder(parse_pair(r.need("ROLE_BASE")), r.need("BASE_TOP"), parse_pair(r.need("ROLE_ARCHITECT")),
                      parse_pair(self.meta(t)["IMPLEMENTER"]))

    def reject(self, t, why):
        r = self.r
        r.set(**{f"ESC_PENDING_{t}": None})
        self.set_phase(t, "escalating", WAITING_ON="architect")
        hist = r.run_dir / "verdicts" / f"{t}-history.md"
        with hist.open("a", encoding="utf-8") as f:
            f.write(f"\n=== Rejected architect answer\n{why}\n")
        if r.incr(f"REJECTS_{t}") >= 2:
            self.prompt(t, "rejected",
                        f"The architect's escalation answers for task {t} were rejected twice:\n{why}\n"
                        "Reply \"retry\" to ask the architect again, or \"stop\" to stop the run.",
                        ["retry", "stop"], back_to="escalating")
        raise Done(ESCALATE, f"ESCALATE {r.need(f'ESC_INPUT_{t}')}")

    def in_range(self, t, sha):
        """True when sha is a descendant of the task's base and an ancestor of the worktree head."""
        impl = self.r.impl
        base = self.r.need(f"BASE_SHA_{t}")
        return (run(["git", "-C", impl, "merge-base", "--is-ancestor", base, sha]).returncode == 0
                and run(["git", "-C", impl, "merge-base", "--is-ancestor", sha, "HEAD"]).returncode == 0)

    def range_msg(self, t, sha):
        return (f"reset sha {sha} is not between the task's base {self.r.need(f'BASE_SHA_{t}')} "
                f"and head {git(self.r.impl, 'rev-parse', 'HEAD')}")

    def architect_result(self, t, path):
        r = self.r
        words = first_words(path)
        phase = self.phase(t)
        if words[:2] == ["RESULT:", "STOP"] and phase in ("escalating", "fixmeta"):
            r.log(f"{t} architect chose STOP")
            raise Done(STOP, f"STOP architect stopped the run at task {t}")
        if phase == "fixmeta" and words[:2] == ["RESULT:", "META_FIXED"]:
            return self.apply_replan(t, r.need(f"PENDING_REPLAN_{t}"))
        if phase != "escalating" or len(words) < 3 or words[0] != "RESULT:":
            raise Done(ERROR, f"ERROR UNEXPECTED_ARCHITECT_RESULT phase={phase}: {' '.join(words)[:300]}")
        kind = words[1]
        meta = self.meta(t)
        a = int(r.need(f"ATTEMPT_{t}"))
        if kind == "ESCALATE" and len(words) == 5:
            sha, note, pair = words[2:5]
            state = ("--state", str(r.path))
            rc, out = tool("validate_meta.py", "tasks", *state, "--tasks-dir", str(r.docs / "tasks"), "--ids", t)
            if rc != 0:
                return self.reject(t, out)
            past = a > len(self.rungs(t))
            rc, out = tool("validate_meta.py", "pair", *state, "--task-meta", str(r.task_file(t, ".meta")),
                           "--pair", pair, "--attempt", str(a), "--past-ladder", "yes" if past else "no")
            if rc != 0:
                return self.reject(t, out)
            if past:
                r.set(**{f"ESC_PENDING_{t}": f"{sha} {note} {pair}"})
                return self.set_phase(t, f"probe:{a}")
            return self.start_attempt(t, sha, note, pair)
        if kind == "REPLAN" and len(words) >= 4:
            if meta.get("REPLANNED_FROM"):
                return self.reject(t, "Replan is not allowed for this task")
            if not self.in_range(t, words[2]):
                return self.reject(t, self.range_msg(t, words[2]))
            r.set(**{f"PENDING_REPLAN_{t}": " ".join(words[2:])})
            return self.apply_replan(t, " ".join(words[2:]))
        return self.reject(t, f"unrecognised answer: {' '.join(words)[:300]}")

    def start_attempt(self, t, sha, note, pair):
        """Reset to the architect's sha and start the escalation attempt on `pair`."""
        r = self.r
        a = int(r.need(f"ATTEMPT_{t}"))
        meta = self.meta(t)
        if not self.in_range(t, sha):
            return self.reject(t, self.range_msg(t, sha))
        rc, out = tool("discard-changes.sh", r.impl, sha)
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")
        spec = build("escalate.txt", r.run_dir / "specs" / f"{t}-attempt{a}.txt",
                     {"TASK_TITLE": meta["TITLE"], "RESET_SHA": sha},
                     {"NOTE": note, "DECISIONS": r.task_file(t, "-decisions.md"),
                      "IMPLEMENT_SPEC": r.run_dir / "specs" / f"{t}-implement.txt"})
        r.set(**{f"IMPLEMENT_SPEC_{t}": spec, f"PAIR_{t}": pair, f"ESC_PENDING_{t}": None,
                 f"IMPL_CONVERSATION_ID_{t}": None,
                 f"REVIEW_STAGE_{t}": 1, f"GATE_FAILED_{t}": "no",
                 f"STAGE_FIX_ROUNDS_{t}_S1": 0, f"STAGE_FIX_ROUNDS_{t}_S2": 0,
                 f"NO_VERDICT_{t}_S1": 0, f"NO_VERDICT_{t}_S2": 0,
                 f"VOIDS_{t}_S1": 0, f"VOIDS_{t}_S2": 0,
                 f"GATE_FIX_ROUNDS_{t}": 0, f"TOTAL_FIX_ROUNDS_{t}": 0, f"FAIL_MSG_{t}": None, f"REJECTS_{t}": 0})
        r.log(f"{t} escalation attempt {a} on {pair} from {sha}")
        return self.start_impl(t, spec, "impl", meta["TITLE"], reuse=False)

    def do_probe(self, t):
        """Test-launch the pair the architect chose past the ladder, then start the attempt."""
        r = self.r
        sha, note, pair = r.need(f"ESC_PENDING_{t}").split()
        try:
            Prober(r, self.runner).advance([pair])
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
                return self.reject(t, f"launch test for {pair} failed; see {d.line.split()[-1]}")
            raise
        self.start_attempt(t, sha, note, pair)

    def apply_replan(self, t, pending):
        """Validate the REPLAN's tasks; on success test-launch their reviewer pairs (phase replan-probe)."""
        r = self.r
        sha, *ids = pending.split()
        out_file = next_free(r.run_dir, "validate-{n}.txt")
        rc, out = tool("validate_meta.py", "tasks", "--state", str(r.path), "--tasks-dir", str(r.docs / "tasks"),
                       "--ids", " ".join(ids), "--replanned-from", t)
        out_file.write_text(out + "\n", encoding="utf-8")
        if rc != 0:
            self.fix_meta(t, out_file)
        self.set_phase(t, "replan-probe")

    def fix_meta(self, t, out_file):
        r = self.r
        if r.incr("META_FAILS") >= 3:
            raise Done(ERROR, f"ERROR META_INVALID_3_TIMES {out_file}")
        self.set_phase(t, "fixmeta", WAITING_ON="architect", **{f"FIXMETA_FILE_{t}": out_file})
        raise Done(FIX_META, f"FIX_META {out_file}")

    def do_replan_probe(self, t):
        """Test-launch the new tasks' reviewer pairs; a failed launch goes to the architect as fix-meta."""
        r = self.r
        sha, *ids = r.need(f"PENDING_REPLAN_{t}").split()
        try:
            Prober(r, self.runner).advance(review_pairs(r, ids))
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
                out_file = next_free(r.run_dir, "validate-{n}.txt")
                out_file.write_text(launch_problems(r, ids, d.line), encoding="utf-8")
                self.fix_meta(t, out_file)
            raise
        rc, out = tool("discard-changes.sh", r.impl, sha)
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")
        q = self.queue()
        if q and q[0] == t:
            q = q[1:]
        self.write_queue(ids + q)
        r.set(META_FAILS=0, **{f"PENDING_REPLAN_{t}": None})
        self.set_phase(t, "replaced")
        r.log(f"{t} replanned into {' '.join(ids)} from {sha}")

    # -- prompts
    def prompt(self, t, name, text, accepted, back_to):
        f = next_free(self.r.run_dir / "results", "prompt-{n}.md")
        f.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        self.set_phase(t, f"prompt:{name}", **{f"PROMPT_FILE_{t}": f, f"PROMPT_ACCEPT_{t}": " ".join(accepted),
                                                f"PROMPT_BACK_{t}": back_to, "WAITING_ON": "user"})
        raise Done(PROMPT, f"PROMPT {f} {' '.join(accepted)}")

    def prompt_not_ff(self, t, out, back_to):
        moved = "\n".join(l.strip() for l in out.splitlines() if "moved by:" in l)
        branch = self.r.need("FEATURE_BRANCH")
        self.prompt(t, "not-ff",
                    f"The feature branch `{branch}` moved outside this run:\n{moved}\n"
                    "(a) Move or revert those commits, then tell me \"retry\".\n"
                    "(b) Tell me \"rebuild\" and I'll reset the implementation worktree to the new branch head "
                    "and redo the current task from scratch, with fresh reviews.",
                    ["retry", "rebuild"], back_to=back_to)

    def prompt_usage(self, t, kind, detail):
        self.r.set(**{f"PROMPT_KIND_{t}": kind})
        self.prompt(t, "usage-limit",
                    f"A worker on task {t} hit its model's usage limit: {detail}\n"
                    "Reply \"retry\" once the limit has reset, or \"stop\" to stop the run.",
                    ["retry", "stop"], back_to="usage")

    def reply(self, t, word):
        r = self.r
        phase = self.phase(t)
        if not phase.startswith("prompt:"):
            raise Done(ERROR, f"ERROR NO_PROMPT_OPEN phase={phase}")
        accepted = r.need(f"PROMPT_ACCEPT_{t}").split()
        if word not in accepted:
            raise Done(PROMPT, f"PROMPT {r.need(f'PROMPT_FILE_{t}')} {' '.join(accepted)}")
        name, back = phase.split(":", 1)[1], r.need(f"PROMPT_BACK_{t}")
        r.log(f"{t} prompt {name}: {word}")
        if word == "stop":
            raise Done(STOP, f"STOP user stopped the run at task {t}")
        if name == "not-ff" and word == "rebuild":
            new = git(r.need("MAIN_CHECKOUT"), "rev-parse", f"refs/heads/{r.need('FEATURE_BRANCH')}")
            rc, out = tool("discard-changes.sh", r.impl, new)
            if rc != 0:
                raise Done(ERROR, f"ERROR RESET {out}")
            r.set(LAST_MERGED=new, **{f"IMPL_CONVERSATION_ID_{t}": None})
            return self.set_phase(t, "start")
        if name == "rejected":
            r.set(**{f"REJECTS_{t}": 0})
            self.set_phase(t, "escalating", WAITING_ON="architect")
            raise Done(ESCALATE, f"ESCALATE {r.need(f'ESC_INPUT_{t}')}")
        if name == "void":
            stage = int(back[-1])
            r.set(**{f"VOIDS_{t}_S{stage}": 0})
            return self.start_review(t, stage)
        if name == "usage-limit":
            kind = r.need(f"PROMPT_KIND_{t}")
            if kind.startswith("review"):
                return self.start_review(t, int(kind[-1]))
            if kind != "commit":
                cur_head = git(r.impl, "rev-parse", "HEAD")
                rc, out = tool("discard-changes.sh", r.impl, cur_head)
                if rc != 0:
                    raise Done(ERROR, f"ERROR RESET {out}")
                r.set(**{f"IMPL_CONVERSATION_ID_{t}": None})
            return self.start_impl(t, r.need(f"LAST_IMPL_SPEC_{t}"), kind, self.meta(t)["TITLE"], reuse=False)
        return self.set_phase(t, back)

    # -- driver
    def current(self):
        t = self.r.get("CURRENT_TASK")
        if t and self.phase(t) not in ("merged", "replaced"):
            return t
        q = self.queue()
        if not q:
            return None
        if q[0] != t:
            fresh = self.phase(q[0]) in ("merged", "replaced")
            self.r.set(CURRENT_TASK=q[0], **({f"LOOP_{q[0]}": "start"} if fresh else {}))
        return q[0]

    def advance(self, architect_result=None, reply_word=None, answer_path=None, moved=False):
        if moved:
            docs_moved(self.r)
        t = self.current()
        if architect_result:
            if not t:
                raise Done(ERROR, "ERROR NO_CURRENT_TASK")
            self.architect_result(t, architect_result)
        elif reply_word:
            if not t:
                raise Done(ERROR, "ERROR NO_CURRENT_TASK")
            self.reply(t, reply_word)
        elif answer_path:
            if not t:
                raise Done(ERROR, "ERROR NO_CURRENT_TASK")
            self.take_answer(t, answer_path)
        for _ in range(1000):
            t = self.current()
            if t is None:
                self.r.set(WAITING_ON=None, STEP="run_task queue empty")
                raise Done(QUEUE_EMPTY, "QUEUE_EMPTY")
            phase = self.phase(t)
            if phase == "start":
                self.do_start(t)
            elif phase in ("impl", "fixing"):
                cur_head = git(self.r.impl, "rev-parse", "HEAD")
                rc, out = tool("discard-changes.sh", self.r.impl, cur_head)
                if rc != 0:
                    raise Done(ERROR, f"ERROR RESET {out}")
                self.r.set(**{f"IMPL_CONVERSATION_ID_{t}": None})
                kind = "fix" if phase == "fixing" else "impl"
                self.start_impl(t, self.r.need(f"LAST_IMPL_SPEC_{t}"), kind, self.meta(t)["TITLE"], reuse=False)
            elif phase == "committing":
                self.start_impl(t, self.r.need(f"LAST_IMPL_SPEC_{t}"), "commit", f"Commit: {self.meta(t)['TITLE']}",
                                reuse=True)
            elif phase.startswith("reviewing:"):
                stage = int(phase.split(":", 1)[1])
                rc, out = tool("discard-changes.sh", self.r.impl, self.r.need(f"WORKER_HEAD_{t}"))
                if rc != 0:
                    raise Done(ERROR, f"ERROR RESET {out}")
                self.start_review(t, stage)
            elif phase == "check":
                self.do_check(t)
            elif phase == "merge":
                self.do_merge(t)
            elif phase.startswith("probe:"):
                self.do_probe(t)
            elif phase == "replan-probe":
                self.do_replan_probe(t)
            elif phase == "consult":
                raise Done(CONSULT, f"CONSULT {self.r.need(f'TASK_ASK_FILE_{t}')}")
            elif phase == "escalating":
                raise Done(ESCALATE, f"ESCALATE {self.r.need(f'ESC_INPUT_{t}')}")
            elif phase == "fixmeta":
                raise Done(FIX_META, f"FIX_META {self.r.need(f'FIXMETA_FILE_{t}')}")
            elif phase.startswith("prompt:"):
                acc = self.r.need(f"PROMPT_ACCEPT_{t}")
                raise Done(PROMPT, f"PROMPT {self.r.need(f'PROMPT_FILE_{t}')} {acc}")
            else:
                raise Done(ERROR, f"ERROR UNKNOWN_PHASE {t} {phase}")
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
    loop = None
    try:
        loop = Loop(a.state)
        loop.advance(a.architect_result, a.reply.strip().strip('"').lower() if a.reply else None,
                     a.answer, a.docs_moved)
        code, line = ERROR, "ERROR NO_STATUS"
    except Done as d:
        code, line = d.code, d.line
    for e in loop.events if loop else []:
        print(e)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
