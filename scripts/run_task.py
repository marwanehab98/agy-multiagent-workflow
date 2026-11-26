"""Run the per-task loop (capsule communicator Step 4) synchronously via headless CLI workers.

Each call runs every deterministic action it can (start workers, save their results,
check the tree, run the gate, run both reviews, count rounds, merge) and exits at the first point
that needs the flight director or the user. All its state lives in the capsule communicator's state.json,
so a call after a crash or a context compaction carries on.

Usage:
  python3 -I run_task.py --state <RUN_DIR>/state.json advance
  python3 -I run_task.py --state <file> advance --director-result <file with the RESULT line>
  python3 -I run_task.py --state <file> advance --reply <the user's one-word reply>
  python3 -I run_task.py --state <file> advance --answer <flight director answer file after CONSULT>
  python3 -I run_task.py --state <file> advance --docs-moved

The last line of stdout is the status; earlier lines are events (MERGED ...).
Exit 0   QUEUE_EMPTY                       every queued task is merged
Exit 20  ESCALATE <input>                  send flight director job escalate with INPUT=<input>
Exit 21  FIX_META <validate output file>   send flight director job fix-meta with INPUT=<file>
Exit 22  CONSULT <ask_file>                a worker exited with ASK:; run flight director job `consult` with
                                           INPUT=<ask_file>, then call advance --answer <answer_file>
Exit 30  PROMPT <file> <reply> [<reply>]   show <file> to the user exactly; pass the reply back
Exit 40  STOP <reason>                     dispatch nothing more; capsule communicator Step 5 item 7
Exit 41  PAUSED <prompt file>              the user paused at a usage-limit prompt; nothing changed, and the next
                                           call shows the same prompt
Exit 42  SWITCH <prompt file>              the user asked at a usage-limit prompt to change the picks; nothing changed.
                                           After switch.py apply, the next call retries on the new picks
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage

Rules the loop enforces:
- Uncommitted files after a crew worker settles get one commit worker per implement or fix round
  (COMMIT_ROUNDS_<id>); whatever it leaves uncommitted is removed with discard-changes.sh and logged.
- One task at a time. A fresh crew worker per task, kept across fix/commit rounds via session resumption
  (falling back to a fresh worker run if resume fails).
- The gate (gate.py) runs before every review; a failing gate goes straight to a fix round with the log.
- Stage 1 review, then Stage 2. After a gate fix passes, the review resumes at REVIEW_STAGE_<id>.
- A review that follows a fix round of the same stage is a re-review: its spec adds re-review.txt with the previous
  verdict file and the fix range (PREV_VERDICT_<id>_S<n>, PREV_HEAD_<id>_S<n>; cleared when the stage passes, and
  when the task starts or an escalation attempt starts it over). A first review gets the shared empty
  specs/no-re-review.txt in its place.
- A fix round after a review's VERDICT: FIX that commits nothing escalates (a <id>-nocommit note) instead of
  rerunning the gate and the review on the same commit; after a gate failure the gate reruns.
- Escalate after two fix rounds for one stage (STAGE_FIX_ROUNDS_<id>_S<n>, reset when that stage passes),
  two gate fix rounds in a row (GATE_FIX_ROUNDS_<id>), or four fix rounds of any kind (TOTAL_FIX_ROUNDS_<id>).
  An escalation attempt resets all three.
- An escalation's history (verdicts/<id>-history.md) has the failure note, every review verdict in full, and each gate
  log in short (gate_summary): one line for a passing log, naming any checks skipped through BASELINE_SKIP; for a
  failing one, the failed checks (then any skipped), the tails of their output and the full log's path. Multi-repo,
  its first line names the task's repo.
- A reviewer that changes the worktree, or stops/fails without settling, is void and replaced; the third
  void in a row prompts retry or stop. A missing VERDICT line gets one new reviewer; a second counts as FIX.
- A usage limit (a worker's or a test launch's) pauses the task with a retry/switch/pause/stop prompt instead of
  counting as a failure.
- After a switch of picks (switch.py apply sets SWITCH_TASKS), the next call first makes what is left of the plan fit
  the new picks, before any worker starts (after_switch): an open usage-limit prompt is answered as "retry"; the queued
  tasks' .meta files and GROUND_CREW_PAIR are checked (FIX_META, with SWITCH_FIXMETA holding the file, until they
  pass; three failures in a row are an error) and their pairs test-launched; then the task in progress moves to its
  new Crew pair (its .meta CREW, or the same rung of the new ladder in an escalation attempt) with a fresh
  conversation, and continues from its last commit.
- The escalation pair must be the ladder's next rung (validate_meta.py pair) and its reset sha must name a commit
  between the task's base and the worktree's HEAD (a short sha is stored in full); two rejected answers prompt the
  user.
- REPLAN is checked the same way and validated (with fix-meta) before the queue changes.
- Merges go only through merge-task.sh (fast-forward, compare-and-swap on the feature branch).
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import (QUEUE_EMPTY, ESCALATE, FIX_META, CONSULT, PROMPT, STOP, ERROR, USAGE_REPLIES,  # noqa: E402
                    WorkerRunner, Done, Run, paused, prompt_replies, switch_requested,
                    answer_worker, build, drive, first_words, git, next_free, pick_verdict, read_answer, read_meta,
                    repo_key, rest_path, run, save_ask, save_body, tool, usage_prompt, verdict_line, void_prompt,
                    write_atomic, write_prompt)
from pairs import ladder, parse_pair  # noqa: E402
from probe import (USAGE_LIMIT, Prober, docs_moved, launch_problems, review_pairs,  # noqa: E402
                   step3_problems)


# ---------------------------------------------------------------- shared with run_finish.py

# What a re-review (re-review.txt) calls the findings that block, and what it does with anything else newly noticed in
# code the fix range did not change. Stage 2 and the final review grade findings by severity.
RE_REVIEW_WORDS = {1: ("missing or extra item", "list anything else newly noticed there (test-coverage or "
                           "convention gaps not raised before) as Minor, which does not call for VERDICT: FIX"),
                   2: ("Critical or Important finding", "list anything else newly noticed there (test-coverage or "
                           "convention gaps not raised before) as Minor")}


def no_re_review(r):
    """The one empty file every first review's spec takes for its {{FILE:RE_REVIEW}} placeholder."""
    f = r.run_dir / "specs" / "no-re-review.txt"
    if not f.is_file() or f.stat().st_size:
        write_atomic(f, "")
    return f


def re_review_section(out, followed, previous_head, fix_range, words, previous):
    """Write the re-review section (re-review.txt, after a line break) to `out` and return it. It goes to the review
    spec as a file: a previous verdict can exceed one argument's size limit."""
    blocking, other = words
    build("re-review.txt", out, {"FOLLOWED": followed, "PREVIOUS_HEAD": previous_head, "FIX_RANGE": fix_range,
                                 "BLOCKING": blocking, "OTHER": other}, {"PREVIOUS": previous})
    write_atomic(out, "\n" + out.read_text(encoding="utf-8"))
    return out


GATE_START = re.compile(r"=== ((?:GATE|VERIFY)_\w*\d+): ")
GATE_SKIP = re.compile(r"=== ((?:GATE|VERIFY)_\w*\d+) skipped ")
GATE_TAIL_LINES, GATE_TAIL_CHARS = 80, 8000


def gate_summary(log):
    """A gate.py log as the escalation history shows it. A log with no failed check is one line: "every check
    passed" when none was skipped (BASELINE_SKIP), "every check that ran passed (<n> skipped: <names>)" when some
    were, "every check skipped (<names>)" when none ran. Otherwise: the checks that failed (a non-zero exit, timed
    out, or never ended) and then any skipped ones, the full log's path, and the tail of each failed check's output
    (of the whole log when no check ran): its last GATE_TAIL_LINES lines, at most GATE_TAIL_CHARS characters. A
    passing test suite's output can run to hundreds of KB, and the history goes to the Flight Director whole."""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    output, failed, skipped, cur = {}, [], [], None
    for line in lines:
        if cur is None:
            m = GATE_START.match(line)
            s = GATE_SKIP.match(line)
            if m:
                cur = m.group(1)
                output[cur] = []
            elif s:
                skipped.append(s.group(1))
        elif line == f"=== {cur} exit=0":
            cur = None
        elif line.startswith(f"=== {cur} exit=") or line.startswith(f"=== {cur} timed out after "):
            failed.append(cur)
            cur = None
        else:
            output[cur].append(line)
    if cur:
        failed.append(cur)  # gate.py stopped before the check ended
    names = " ".join(skipped)
    if output and not failed:
        if skipped:
            return f"\n=== {log.name}: every check that ran passed ({len(skipped)} skipped: {names})\n"
        return f"\n=== {log.name}: every check passed\n"
    if skipped and not failed:
        return f"\n=== {log.name}: every check skipped ({names})\n"
    also = f"; skipped {names}" if skipped else ""
    parts = [f"\n=== {log.name}: FAILED {' '.join(failed) or '(no check ran)'}{also} (full log: {log})\n"]
    for name in failed or [None]:
        tail = "\n".join(output.get(name, lines)[-GATE_TAIL_LINES:])[-GATE_TAIL_CHARS:]
        parts.append(f"--- {name or log.name}, last lines:\n{tail}\n")
    return "".join(parts)


# ---------------------------------------------------------------- the loop

class Loop:
    def __init__(self, state_path):
        self.r = Run(state_path)
        self.r.require_new_format()
        self.runner = WorkerRunner(self.r)
        self.events = []

    # -- queue
    def queue_path(self):
        return self.r.run_dir / "queue.txt"

    def queue(self):
        p = self.queue_path()
        return p.read_text(encoding="utf-8").split() if p.exists() else []

    def write_queue(self, ids):
        write_atomic(self.queue_path(), " ".join(ids) + "\n")

    def phase(self, t):
        return self.r.get(f"LOOP_{t}", "start")

    def set_phase(self, t, phase, **kv):
        self.r.set(**{f"LOOP_{t}": phase, "STEP": f"run_task {t} {phase}"}, **kv)

    def meta(self, t):
        return read_meta(self.r.task_file(t, ".meta"))

    def task_repo(self, t):
        if not self.r.is_multi_repo:
            return None
        repo = self.meta(t).get("REPO")
        if not repo:
            raise Done(ERROR, f"ERROR MISSING_REPO task={t}")
        return repo

    def task_impl(self, t):
        return self.r.repo_impl(self.task_repo(t))

    def guard_other_repos(self, t):
        """Multi-repo: a worker on task t may only change t's repo. Put every other repo's implementation
        worktree back at its LAST_MERGED_<k>, so nothing unreviewed reaches a later task's merge."""
        if not self.r.is_multi_repo:
            return
        mine = self.task_repo(t)
        for repo in self.r.repos:
            if repo == mine:
                continue
            # A replan may have kept commits past LAST_MERGED for that repo's next task (REPLAN_HEAD_<k>).
            wt = self.r.repo_impl(repo)
            sha = self.r.get(f"REPLAN_HEAD_{repo_key(repo)}") or self.r.repo_last_merged(repo)
            rc, out = tool("check-tree.sh", wt, sha)
            if rc == 0:
                continue
            rc2, out2 = tool("discard-changes.sh", wt, sha)
            self.r.log(f"{t} worker changed repo {repo}, which is not its task's repo; reset to {sha}: "
                       + " ".join(out.split())[:300])
            if rc2 != 0:
                raise Done(ERROR, f"ERROR RESET [{repo}] {out2}")

    # -- crew
    def start_impl(self, t, spec, kind, title, reuse):
        pair = self.r.need(f"PAIR_{t}")
        wt = self.task_impl(t)
        self.r.set(**{f"LAST_IMPL_SPEC_{t}": spec, f"LAST_IMPL_KIND_{t}": kind})
        in_flight = "committing" if kind == "commit" else ("fixing" if kind == "fix" else "impl")
        self.set_phase(t, in_flight)
        result = None
        # Each line is logged before its worker starts, so the run log and a live watch show the worker as started.
        conv = self.r.get(f"IMPL_CONVERSATION_ID_{t}") if reuse else None
        if conv:
            self.r.log(f"{t} {kind} reused {pair}")
            result = self.runner.try_resume(spec, pair, wt, conv)
            if result is None:
                self.r.set(**{f"IMPL_CONVERSATION_ID_{t}": None})
                if kind == "fix":
                    self._discard_or_error(self.r.need(f"WORKER_HEAD_{t}"), t)
                self.r.log(f"{t} {kind} resume failed; a fresh worker is dispatched {pair}")
        if result is None:
            if not conv:
                self.r.log(f"{t} {kind} dispatched {pair}")
            result = self.runner.run(spec, pair=pair, worktree=wt)
        if result[3]:
            self.r.set(**{f"IMPL_CONVERSATION_ID_{t}": result[3]})
        self.after_impl(t, kind, result)

    def do_start(self, t):
        r = self.r
        repo = self.task_repo(t)
        wt = r.repo_impl(repo)
        last_merged = r.repo_last_merged(repo)
        rc, out = tool("branch-at.sh", r.repo_checkout(repo), r.repo_feature_branch(repo), last_merged)
        if rc == 1:
            self.prompt_not_ff(t, out, back_to="start")
        if rc != 0:
            raise Done(ERROR, f"ERROR BRANCH_AT {out}")
        head = git(wt, "rev-parse", "HEAD")
        rc, out = tool("check-tree.sh", wt, head)
        if rc != 0 or run(["git", "-C", wt, "merge-base", "--is-ancestor", last_merged, head]).returncode:
            raise Done(ERROR, f"ERROR IMPL_WORKTREE_NOT_READY head={head} last_merged={last_merged} {out}")
        keep_key = f"REPLAN_HEAD_{repo_key(repo)}" if repo else "REPLAN_HEAD"
        if head != last_merged and head != r.get(keep_key):
            # Commits past LAST_MERGED that no replan kept were never reviewed for this task: drop them.
            stray = run(["git", "-C", wt, "log", "--oneline", f"{last_merged}..{head}"]).stdout.split("\n")
            rc, out = tool("discard-changes.sh", wt, last_merged)
            if rc != 0:
                raise Done(ERROR, f"ERROR RESET {out}")
            r.log(f"{t} dropped unreviewed commits past {last_merged} before starting: "
                  + "; ".join(x for x in stray if x)[:300])
            head = last_merged
        meta = self.meta(t)
        spec = build("implement.txt", r.run_dir / "specs" / f"{t}-implement.txt",
                     {"TASK_TITLE": meta["TITLE"]},
                     {"TASK": r.task_file(t, ".md"), "DECISIONS": r.task_file(t, "-decisions.md")})
        # BASE_SHA is where this task's own work starts; its reviews start at LAST_MERGED, so commits a REPLAN kept
        # (head past LAST_MERGED) are reviewed with the first replacement task instead of only in the final review.
        r.set(CURRENT_TASK=t, **{keep_key: None, f"BASE_SHA_{t}": head, f"IMPL_START_SHA_{t}": head,
                                 f"REVIEW_BASE_SHA_{t}": last_merged,
                                 f"IMPLEMENT_SPEC_{t}": spec,
                                 f"PAIR_{t}": meta["CREW"], f"ATTEMPT_{t}": 0,
                                 f"REVIEW_STAGE_{t}": 1, f"GATE_FAILED_{t}": "no",
                                 f"STAGE_FIX_ROUNDS_{t}_S1": 0, f"STAGE_FIX_ROUNDS_{t}_S2": 0,
                                 f"GATE_FIX_ROUNDS_{t}": 0, f"TOTAL_FIX_ROUNDS_{t}": 0, f"FAIL_MSG_{t}": None,
                                 f"REJECTS_{t}": 0, f"IMPL_CONVERSATION_ID_{t}": None,
                                 f"NO_VERDICT_{t}_S1": 0, f"NO_VERDICT_{t}_S2": 0,
                                 f"VOIDS_{t}_S1": 0, f"VOIDS_{t}_S2": 0, **self.fresh_reviews(t)})
        self.start_impl(t, spec, "impl", meta["TITLE"], reuse=False)

    @staticmethod
    def fresh_reviews(t):
        """State keys that start task t's reviews over: no review counts as a re-review, no fix round is open."""
        return {f"FIX_FROM_{t}": None, **{f"{k}_{t}_S{n}": None for k in ("PREV_VERDICT", "PREV_HEAD") for n in (1, 2)}}

    def ask_consult(self, t, kind, pair, spec, conv_id, body, stage=None):
        r = self.r
        ask = save_ask(r, f"{t}-{kind}", body, header=f"task={t} {self.meta(t)['TITLE']}")
        r.set(**{f"TASK_ASK_CONV_{t}": conv_id, f"TASK_ASK_KIND_{t}": kind,
                 f"TASK_ASK_PAIR_{t}": pair, f"TASK_ASK_SPEC_{t}": spec,
                 f"TASK_ASK_FILE_{t}": ask, f"TASK_ASK_STAGE_{t}": stage})
        self.set_phase(t, "consult", WAITING_ON="director")
        raise Done(CONSULT, f"CONSULT {ask}")

    def after_impl(self, t, kind, result):
        r = self.r
        self.guard_other_repos(t)
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
        repo = self.task_repo(t)
        wt = r.repo_impl(repo)
        head = git(wt, "rev-parse", "HEAD")
        r.set(**{f"WORKER_HEAD_{t}": head})
        rc, out = tool("check-tree.sh", wt, head)
        if rc == 2 and int(r.get(f"COMMIT_ROUNDS_{t}", "0")) >= 1:
            rc, out = tool("discard-changes.sh", wt, head)
            r.log(f"{t} removed what the commit worker left uncommitted: {' '.join(out.split())}")
            if rc != 0:
                raise Done(ERROR, f"ERROR RESET {out}")
            rc, out = tool("check-tree.sh", wt, head)
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
        fix_from = (r.get(f"FIX_FROM_{t}") or "").split()
        if fix_from:
            r.set(**{f"FIX_FROM_{t}": None})
        if len(fix_from) == 2 and fix_from[0] != "gate" and head == fix_from[1]:
            # The same commit would get the same gate and the same review again. (After a gate failure the gate
            # does rerun: a flaky check may pass.)
            note = next_free(r.run_dir / "results", f"{t}-nocommit-{{n}}.md")
            note.write_text(f"the fix round after the stage {fix_from[0]} review's VERDICT: FIX made no commits; "
                            f"HEAD is still {head}\n", encoding="utf-8")
            r.set(**{f"FAIL_MSG_{t}": note})
            r.log(f"{t} fix round made no commits")
            return self.escalate(t)
        log = next_free(r.run_dir / "gates", f"{t}-{{n}}.log")
        gate_args = ["--flight-manifest", r.need("FLIGHT_MANIFEST"), "--worktree", wt, "--head", head, "--log", str(log)]
        if repo:
            gate_args += ["--repo", repo_key(repo)]
        rc, out = tool("gate.py", *gate_args)
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
        r.set(**{f"FIX_FROM_{t}": f"{kind} {r.need(f'WORKER_HEAD_{t}')}"})  # do_check: did the round commit?
        meta = self.meta(t)
        spec = build("fix.txt", next_free(r.run_dir / "specs", f"{t}-fix-{{n}}.txt"),
                     {"TASK_TITLE": meta["TITLE"], "BASE_SHA": self.review_base(t),
                      "WORKER_HEAD": r.need(f"WORKER_HEAD_{t}")},
                     {"FINDINGS": findings, "DECISIONS": r.task_file(t, "-decisions.md"),
                      "IMPLEMENT_SPEC": r.need(f"IMPLEMENT_SPEC_{t}")})
        self.start_impl(t, spec, "fix", f"Fix: {meta['TITLE']}", reuse=True)

    # -- reviewers
    def review_base(self, t):
        """Where task t's review range starts (BASE_SHA for a run started before REVIEW_BASE_SHA existed)."""
        return self.r.get(f"REVIEW_BASE_SHA_{t}") or self.r.need(f"BASE_SHA_{t}")

    def start_review(self, t, stage):
        r = self.r
        repo = self.task_repo(t)
        wt = r.repo_impl(repo)
        meta = self.meta(t)
        review = next_free(r.run_dir / "verdicts", f"{t}-s{stage}-{{n}}.md")
        r.set(**{f"REVIEW_OUT_{t}": review})
        out = next_free(r.run_dir / "specs", f"{t}-s{stage}-{{n}}.txt")
        common = {"BASE_SHA": self.review_base(t), "WORKER_HEAD": r.need(f"WORKER_HEAD_{t}"), "OUTPUT_FILE": review}
        files = {"RE_REVIEW": self.re_review(t, stage, out), "DECISIONS": r.task_file(t, "-decisions.md")}
        if stage == 1:
            spec = build("spec-review.txt", out, common, {**files, "TASK": r.task_file(t, ".md")})
        else:
            spec = build("quality-review.txt", out, {**common, "CONVENTION_PATHS": r.repo_convention_paths(repo)},
                         {**files, "SUMMARY": r.task_file(t, "-summary.md")})
        pair = meta[f"REVIEW_{stage}"]
        r.set(**{f"REVIEW_SPEC_{t}": spec, f"REVIEW_PAIR_{t}": pair})
        self.set_phase(t, f"reviewing:{stage}")
        r.log(f"{t} stage{stage} reviewer dispatched {pair}")
        result = self.runner.run(spec, pair=pair, worktree=wt, access="read")
        self.after_review(t, stage, result)

    def re_review(self, t, stage, spec_out):
        """A file holding the re-review section for a review that follows a fix round of the same stage
        (re-review.txt: the previous verdict file and the fix range), or the shared empty file for a first review."""
        r = self.r
        prev, prev_head = r.get(f"PREV_VERDICT_{t}_S{stage}"), r.get(f"PREV_HEAD_{t}_S{stage}")
        if not prev or not prev_head or not Path(prev).is_file():
            return no_re_review(r)
        return re_review_section(spec_out.with_name(f"{spec_out.stem}-re-review.txt"),
                                 "The previous review of this stage asked for fixes and a fix round followed.",
                                 prev_head, f"{prev_head}..{r.need(f'WORKER_HEAD_{t}')}", RE_REVIEW_WORDS[stage], prev)

    def _discard_or_error(self, head, t=None):
        wt = self.task_impl(t) if t is not None else self.r.impl
        rc, out = tool("discard-changes.sh", wt, head)
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")

    def after_review(self, t, stage, result):
        r = self.r
        self.guard_other_repos(t)
        wt = self.task_impl(t)
        head = r.need(f"WORKER_HEAD_{t}")
        if result[0] == "usage":
            self._discard_or_error(head, t)
            self.prompt_usage(t, f"review{stage}", result[1])
        if result[0] == "stopped":
            self._discard_or_error(head, t)
            return self.void_review(t, stage, "stopped without a verdict")
        if result[0] == "ask":
            rc, _ = tool("check-tree.sh", wt, head)
            if rc != 0:
                self._discard_or_error(head, t)
                return self.void_review(t, stage, "changed the worktree")
            self.ask_consult(t, f"review{stage}", r.need(f"REVIEW_PAIR_{t}"),
                             r.need(f"REVIEW_SPEC_{t}"), result[1], result[2], stage=str(stage))
        if result[1] != "succeeded":
            self._discard_or_error(head, t)
            return self.void_review(t, stage, "failed without a verdict")
        settle = save_body(result[2], next_free(r.run_dir / "results", f"{t}-s{stage}-settle-{{n}}.md"))
        verdict = pick_verdict(Path(r.need(f"REVIEW_OUT_{t}")), settle)
        rc, _ = tool("check-tree.sh", wt, head)
        if rc != 0:
            self._discard_or_error(head, t)
            verdict.rename(next_free(r.run_dir / "verdicts", f"{t}-void-s{stage}-{{n}}.md"))
            return self.void_review(t, stage, "changed the worktree")
        last = verdict_line(verdict)
        r.log(f"{t} stage{stage} {last or 'no verdict'} {verdict}")
        r.set(**{f"VOIDS_{t}_S{stage}": 0})
        if last:
            r.set(**{f"NO_VERDICT_{t}_S{stage}": 0})
        if last == "VERDICT: PASS":
            r.set(**{f"STAGE_FIX_ROUNDS_{t}_S{stage}": 0, f"PREV_VERDICT_{t}_S{stage}": None,
                     f"PREV_HEAD_{t}_S{stage}": None})
            if stage == 1:
                r.set(**{f"REVIEW_STAGE_{t}": 2})
                return self.start_review(t, 2)
            return self.set_phase(t, "merge")
        if last != "VERDICT: FIX" and r.incr(f"NO_VERDICT_{t}_S{stage}") < 2:
            return self.start_review(t, stage)
        # The next review of this stage is a re-review: it gets this verdict and the fix range from this head.
        r.set(**{f"REVIEW_STAGE_{t}": stage, f"PREV_VERDICT_{t}_S{stage}": verdict, f"PREV_HEAD_{t}_S{stage}": head})
        return self.fix(t, verdict, stage)

    def void_review(self, t, stage, why):
        n = self.r.incr(f"VOIDS_{t}_S{stage}")
        self.r.log(f"{t} stage{stage} reviewer void: {why}")
        if n >= 3:
            self.prompt(t, "void", void_prompt(f"Task {t} stage {stage}", why), ["retry", "stop"],
                        back_to=f"review{stage}")
        self.start_review(t, stage)

    # -- consult answer
    def take_answer(self, t, answer_path):
        r = self.r
        if self.phase(t) != "consult":
            raise Done(ERROR, f"ERROR NO_CONSULT_OPEN phase={self.phase(t)}")
        ans = read_answer(answer_path)
        kind = r.need(f"TASK_ASK_KIND_{t}")
        pair = r.need(f"TASK_ASK_PAIR_{t}")
        spec = r.need(f"TASK_ASK_SPEC_{t}")
        conv = r.get(f"TASK_ASK_CONV_{t}", "")
        stage = r.get(f"TASK_ASK_STAGE_{t}")
        r.set(**{f"TASK_ASK_CONV_{t}": None, f"TASK_ASK_KIND_{t}": None,
                 f"TASK_ASK_PAIR_{t}": None, f"TASK_ASK_SPEC_{t}": None,
                 f"TASK_ASK_FILE_{t}": None, f"TASK_ASK_STAGE_{t}": None})
        if kind.startswith("review"):
            review_stage = int(stage or kind[-1])
            self.set_phase(t, f"reviewing:{review_stage}", WAITING_ON=None)
        else:
            in_flight = "committing" if kind == "commit" else ("fixing" if kind == "fix" else "impl")
            self.set_phase(t, in_flight, WAITING_ON=None)
        def before_fresh():
            if kind.startswith("review") or kind == "fix":
                self._discard_or_error(r.need(f"WORKER_HEAD_{t}"), t)
            elif kind == "impl":
                self._discard_or_error(r.get(f"IMPL_START_SHA_{t}") or r.need(f"BASE_SHA_{t}"), t)
            if not kind.startswith("review"):
                r.set(**{f"IMPL_CONVERSATION_ID_{t}": None})

        result = answer_worker(self.runner, ans, pair, self.task_impl(t), conv, spec, before_fresh,
                               access="read" if kind.startswith("review") else "full")
        if kind.startswith("review"):
            self.after_review(t, review_stage, result)
        else:
            if result[3]:
                r.set(**{f"IMPL_CONVERSATION_ID_{t}": result[3]})
            self.after_impl(t, kind, result)

    # -- merge
    def do_merge(self, t):
        r = self.r
        repo = self.task_repo(t)
        rc, out = tool("merge-task.sh", r.repo_checkout(repo), r.repo_feature_branch(repo),
                       r.repo_last_merged(repo), r.need(f"WORKER_HEAD_{t}"))
        if rc == 2:
            self.prompt(t, "checked-out", f"{out}\nFix this, then tell me to continue.", ["continue"], back_to="merge")
        if rc == 3:
            self.prompt_not_ff(t, out, back_to="merge")
        if rc != 0:
            raise Done(ERROR, f"ERROR MERGE {out}")
        head = out.split("head=")[-1].strip()
        r.set(**{r.repo_last_merged_key(repo): head, "WAITING_ON": None, f"IMPL_CONVERSATION_ID_{t}": None})
        self.set_phase(t, "merged")
        q = self.queue()
        if q and q[0] == t:
            self.write_queue(q[1:])
        r.log(f"{t} merged {head}" + (f" ({repo})" if repo else ""))
        self.events.append(f"MERGED {t} {head}")

    # -- escalation
    def escalate(self, t):
        r = self.r
        wt = self.task_impl(t)
        r.set(**{f"IMPL_CONVERSATION_ID_{t}": None})
        a = r.incr(f"ATTEMPT_{t}")
        hist = r.run_dir / "verdicts" / f"{t}-history.md"
        repo = self.task_repo(t)
        parts = [f"task={t}" + (f" repo={repo}" if repo else "")
                 + f" base={r.need(f'BASE_SHA_{t}')} head={git(wt, 'rev-parse', 'HEAD')}\n"]
        if r.get(f"FAIL_MSG_{t}"):
            parts.append(Path(r.get(f"FAIL_MSG_{t}")).read_text(encoding="utf-8"))
        for f in sorted((r.run_dir / "verdicts").glob(f"{t}-s*.md")):
            parts.append(f"\n=== {f.name}\n" + f.read_text(encoding="utf-8"))
        for f in sorted((r.run_dir / "gates").glob(f"{t}-*.log")):
            parts.append(gate_summary(f))
        hist.write_text("".join(parts), encoding="utf-8")
        line, nxt = self.esc_input(t)
        self.set_phase(t, "escalating", **{f"ESC_INPUT_{t}": line, "WAITING_ON": "director"})
        r.log(f"{t} escalation attempt {a}: {nxt}")
        raise Done(ESCALATE, f"ESCALATE {line}")

    def esc_input(self, t):
        """The escalate job's input for task t's current attempt, and its ladder part (the next rung, from the
        picks as they are now)."""
        a = int(self.r.need(f"ATTEMPT_{t}"))
        meta = self.meta(t)
        rungs = self.rungs(t)
        nxt = f"next rung {rungs[a - 1]}" if a <= len(rungs) else "ladder used up"
        replan = "not allowed" if meta.get("REPLANNED_FROM") else "allowed"
        hist = self.r.run_dir / "verdicts" / f"{t}-history.md"
        return f"task {t}, tier {meta['TIER']}, attempt {a}, {nxt}, replan {replan}, history {hist}", nxt

    def rungs(self, t):
        """The escalation ladder for task t, computed from the role picks and the task's first crew pick."""
        r = self.r
        return ladder(parse_pair(r.need("ROLE_CREW")), r.need("CREW_TOP"), parse_pair(r.need("ROLE_DIRECTOR")),
                      parse_pair(self.meta(t)["CREW"]))

    def reject(self, t, why):
        r = self.r
        r.set(**{f"ESC_PENDING_{t}": None})
        self.set_phase(t, "escalating", WAITING_ON="director")
        hist = r.run_dir / "verdicts" / f"{t}-history.md"
        with hist.open("a", encoding="utf-8") as f:
            f.write(f"\n=== Rejected flight director answer\n{why}\n")
        if r.incr(f"REJECTS_{t}") >= 2:
            self.prompt(t, "rejected",
                        f"The flight director's escalation answers for task {t} were rejected twice:\n{why}\n"
                        "Reply \"retry\" to ask the flight director again, or \"stop\" to stop the run.",
                        ["retry", "stop"], back_to="escalating")
        raise Done(ESCALATE, f"ESCALATE {r.need(f'ESC_INPUT_{t}')}")

    def in_range(self, t, sha):
        """True when sha is a descendant of the task's base and an ancestor of the worktree head."""
        impl = self.task_impl(t)
        base = self.r.need(f"BASE_SHA_{t}")
        return (run(["git", "-C", impl, "merge-base", "--is-ancestor", base, sha]).returncode == 0
                and run(["git", "-C", impl, "merge-base", "--is-ancestor", sha, "HEAD"]).returncode == 0)

    def range_msg(self, t, sha):
        return (f"reset sha {sha} is not between the task's base {self.r.need(f'BASE_SHA_{t}')} "
                f"and head {git(self.task_impl(t), 'rev-parse', 'HEAD')}")

    def full_sha(self, t, sha):
        """The full sha of the commit `sha` names in task t's worktree (a short sha from `git log --oneline` too),
        or None when it names no commit. Stored shas are compared with `git rev-parse HEAD`, so they must be full."""
        if sha.startswith("-"):
            return None
        return git(self.task_impl(t), "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")

    def director_result(self, t, path):
        r = self.r
        words = first_words(path)
        phase = self.phase(t)
        if words[:2] == ["RESULT:", "STOP"] and phase in ("escalating", "fixmeta"):
            r.log(f"{t} flight director chose STOP")
            raise Done(STOP, f"STOP flight director stopped the run at task {t}")
        if phase == "fixmeta" and words[:2] == ["RESULT:", "META_FIXED"]:
            return self.apply_replan(t, r.need(f"PENDING_REPLAN_{t}"))
        if phase != "escalating" or len(words) < 3 or words[0] != "RESULT:":
            raise Done(ERROR, f"ERROR UNEXPECTED_DIRECTOR_RESULT phase={phase}: {' '.join(words)[:300]}")
        kind = words[1]
        meta = self.meta(t)
        a = int(r.need(f"ATTEMPT_{t}"))
        if kind == "REPLAN" and len(words) >= 4 and meta.get("REPLANNED_FROM"):
            return self.reject(t, "Replan is not allowed for this task")
        if kind in ("ESCALATE", "REPLAN") and len(words) >= 4:
            # Store the reset sha in full: do_start compares the stored REPLAN_HEAD with `git rev-parse HEAD`.
            full = self.full_sha(t, words[2])
            if full is None:
                return self.reject(t, self.range_msg(t, words[2]))
            words = words[:2] + [full] + words[3:]
        if kind == "ESCALATE" and len(words) >= 5:
            sha, pair = words[2], words[-1]
            note = rest_path(words[:-1], 3)
            if note != " ".join(words[3:-1]):
                return self.reject(t, f"unrecognised answer: {' '.join(words)[:300]}")
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
                r.set(**{f"ESC_PENDING_{t}": f"{sha}\t{note}\t{pair}"})
                return self.set_phase(t, f"probe:{a}")
            return self.start_attempt(t, sha, note, pair)
        if kind == "REPLAN" and len(words) >= 4:
            if not self.in_range(t, words[2]):
                return self.reject(t, self.range_msg(t, words[2]))
            r.set(**{f"PENDING_REPLAN_{t}": " ".join(words[2:])})
            return self.apply_replan(t, " ".join(words[2:]))
        return self.reject(t, f"unrecognised answer: {' '.join(words)[:300]}")

    def start_attempt(self, t, sha, note, pair):
        """Reset to the flight director's sha and start the escalation attempt on `pair`."""
        r = self.r
        a = int(r.need(f"ATTEMPT_{t}"))
        meta = self.meta(t)
        if not self.in_range(t, sha):
            return self.reject(t, self.range_msg(t, sha))
        rc, out = tool("discard-changes.sh", self.task_impl(t), sha)
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")
        spec = build("escalate.txt", r.run_dir / "specs" / f"{t}-attempt{a}.txt",
                     {"TASK_TITLE": meta["TITLE"], "RESET_SHA": sha},
                     {"NOTE": note, "DECISIONS": r.task_file(t, "-decisions.md"),
                      "IMPLEMENT_SPEC": r.run_dir / "specs" / f"{t}-implement.txt"})
        r.set(**{f"IMPL_START_SHA_{t}": sha, f"IMPLEMENT_SPEC_{t}": spec, f"PAIR_{t}": pair, f"ESC_PENDING_{t}": None,
                 f"IMPL_CONVERSATION_ID_{t}": None,
                 f"REVIEW_STAGE_{t}": 1, f"GATE_FAILED_{t}": "no",
                 f"STAGE_FIX_ROUNDS_{t}_S1": 0, f"STAGE_FIX_ROUNDS_{t}_S2": 0,
                 f"NO_VERDICT_{t}_S1": 0, f"NO_VERDICT_{t}_S2": 0,
                 f"VOIDS_{t}_S1": 0, f"VOIDS_{t}_S2": 0,
                 f"GATE_FIX_ROUNDS_{t}": 0, f"TOTAL_FIX_ROUNDS_{t}": 0, f"FAIL_MSG_{t}": None, f"REJECTS_{t}": 0,
                 **self.fresh_reviews(t)})
        r.log(f"{t} escalation attempt {a} on {pair} from {sha}")
        return self.start_impl(t, spec, "impl", meta["TITLE"], reuse=False)

    def do_probe(self, t):
        """Test-launch the pair the flight director chose past the ladder, then start the attempt."""
        r = self.r
        sha, note, pair = r.need(f"ESC_PENDING_{t}").split("\t")
        try:
            Prober(r, self.runner).advance([pair])
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
                return self.reject(t, f"launch test for {pair} failed; see {d.line.split(' ', 2)[-1]}")
            self.probe_usage(t, d)
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
        self.set_phase(t, "fixmeta", WAITING_ON="director", **{f"FIXMETA_FILE_{t}": out_file})
        raise Done(FIX_META, f"FIX_META {out_file}")

    def do_replan_probe(self, t):
        """Test-launch the new tasks' reviewer pairs; a failed launch goes to the flight director as fix-meta."""
        r = self.r
        sha, *ids = r.need(f"PENDING_REPLAN_{t}").split()
        try:
            Prober(r, self.runner).advance(review_pairs(r, ids))
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
                out_file = next_free(r.run_dir, "validate-{n}.txt")
                out_file.write_text(launch_problems(r, ids, d.line), encoding="utf-8")
                self.fix_meta(t, out_file)
            self.probe_usage(t, d)
            raise
        rc, out = tool("discard-changes.sh", self.task_impl(t), sha)
        if rc != 0:
            raise Done(ERROR, f"ERROR RESET {out}")
        repo = self.task_repo(t)
        r.set(**{(f"REPLAN_HEAD_{repo_key(repo)}" if repo else "REPLAN_HEAD"): sha})
        # Queue first, then mark t replaced: a call cut off in between redoes this step, and the queue write
        # is idempotent (the new ids are already at its head, t is gone).
        q = [x for x in self.queue() if x != t]
        if q[:len(ids)] != ids:
            q = ids + q
        self.write_queue(q)
        r.set(META_FAILS=0, **{f"PENDING_REPLAN_{t}": None})
        self.set_phase(t, "replaced")
        r.log(f"{t} replanned into {' '.join(ids)} from {sha}")

    # -- prompts
    def prompt(self, t, name, text, accepted, back_to):
        f = write_prompt(self.r, text)
        self.set_phase(t, f"prompt:{name}", **{f"PROMPT_FILE_{t}": f, f"PROMPT_ACCEPT_{t}": " ".join(accepted),
                                                f"PROMPT_BACK_{t}": back_to, "WAITING_ON": "user"})
        raise Done(PROMPT, f"PROMPT {f} {' '.join(accepted)}")

    def prompt_not_ff(self, t, out, back_to):
        moved = "\n".join(l.strip() for l in out.splitlines() if "moved by:" in l)
        branch = self.r.repo_feature_branch(self.task_repo(t))
        self.prompt(t, "not-ff",
                    f"The feature branch `{branch}` moved outside this run:\n{moved}\n"
                    "(a) Move or revert those commits, then tell me \"retry\".\n"
                    "(b) Tell me \"rebuild\" and I'll reset the implementation worktree to the new branch head "
                    "and redo the current task from scratch, with fresh reviews.",
                    ["retry", "rebuild"], back_to=back_to)

    def probe_usage(self, t, done):
        """A test launch hit a usage limit: the same usage prompt as a worker's; "retry" redoes the probe phase."""
        if done.code == USAGE_LIMIT:
            _, pair, detail = (done.line + " ").split(" ", 2)
            self.r.set(**{f"PROMPT_KIND_{t}": "probe"})
            self.prompt(t, "usage-limit", usage_prompt(f"The test launch of {pair} for task {t}", detail.strip()),
                        USAGE_REPLIES, back_to=self.phase(t))

    def prompt_usage(self, t, kind, detail):
        self.r.set(**{f"PROMPT_KIND_{t}": kind})
        self.prompt(t, "usage-limit", usage_prompt(f"A worker on task {t}", detail), USAGE_REPLIES,
                    back_to="usage")

    def reply(self, t, word):
        r = self.r
        phase = self.phase(t)
        if not phase.startswith("prompt:"):
            raise Done(ERROR, f"ERROR NO_PROMPT_OPEN phase={phase}")
        accepted = prompt_replies(phase, r.need(f"PROMPT_ACCEPT_{t}"))
        if word not in accepted:
            raise Done(PROMPT, f"PROMPT {r.need(f'PROMPT_FILE_{t}')} {' '.join(accepted)}")
        name, back = phase.split(":", 1)[1], r.need(f"PROMPT_BACK_{t}")
        r.log(f"{t} prompt {name}: {word}")
        if word == "stop":
            raise Done(STOP, f"STOP user stopped the run at task {t}")
        if word == "pause":
            paused(r, r.need(f"PROMPT_FILE_{t}"))
        if word == "switch":
            switch_requested(r, r.need(f"PROMPT_FILE_{t}"))
        if name == "not-ff" and word == "rebuild":
            repo = self.task_repo(t)
            new = git(r.repo_checkout(repo), "rev-parse", f"refs/heads/{r.repo_feature_branch(repo)}")
            rc, out = tool("discard-changes.sh", r.repo_impl(repo), new)
            if rc != 0:
                raise Done(ERROR, f"ERROR RESET {out}")
            r.set(**{r.repo_last_merged_key(repo): new, f"IMPL_CONVERSATION_ID_{t}": None})
            return self.set_phase(t, "start")
        if name == "rejected":
            r.set(**{f"REJECTS_{t}": 0})
            self.set_phase(t, "escalating", WAITING_ON="director")
            raise Done(ESCALATE, f"ESCALATE {r.need(f'ESC_INPUT_{t}')}")
        if name == "void":
            stage = int(back[-1])
            r.set(**{f"VOIDS_{t}_S{stage}": 0})
            return self.start_review(t, stage)
        if name == "usage-limit":
            kind = r.need(f"PROMPT_KIND_{t}")
            if kind == "probe":
                return self.set_phase(t, back)
            if kind.startswith("review"):
                return self.start_review(t, int(kind[-1]))
            if kind != "commit":
                wt = self.task_impl(t)
                cur_head = git(wt, "rev-parse", "HEAD")
                rc, out = tool("discard-changes.sh", wt, cur_head)
                if rc != 0:
                    raise Done(ERROR, f"ERROR RESET {out}")
                r.set(**{f"IMPL_CONVERSATION_ID_{t}": None})
            return self.start_impl(t, r.need(f"LAST_IMPL_SPEC_{t}"), kind, self.meta(t)["TITLE"], reuse=False)
        return self.set_phase(t, back)

    # -- after a switch of picks (switch.py apply)
    def after_switch(self, director_result):
        """SWITCH_TASKS is set: make what is left of the plan fit the new picks before anything else runs (module
        docstring). Returns what it used up of the call's arguments: "result" (director_result was the fix-meta
        answer) and "reply" (the usage-limit prompt a reply would answer is answered)."""
        r = self.r
        used = set()
        if not r.get("SWITCH_TASKS"):
            return used
        if r.get("SWITCH_FIXMETA"):
            if not director_result:
                raise Done(FIX_META, f"FIX_META {r.need('SWITCH_FIXMETA')}")
            words = first_words(director_result)
            if words[:2] == ["RESULT:", "STOP"]:
                r.log("flight director chose STOP after a switch of picks")
                raise Done(STOP, "STOP flight director stopped the run after a switch of picks")
            if words[:2] != ["RESULT:", "META_FIXED"]:
                raise Done(ERROR, f"ERROR UNEXPECTED_DIRECTOR_RESULT phase=switch: {' '.join(words)[:300]}")
            r.set(SWITCH_FIXMETA=None, WAITING_ON=None)
            self.reread_ground_crew()
            used.add("result")
        ids = self.queue()
        if not ids:
            # Nothing left to build: ground-crew jobs stay on ROLE_DIRECTOR (STEP3_PROBED is unset by the switch).
            r.set(SWITCH_TASKS=None)
            return used
        t = ids[0]
        if self.phase(t) == "prompt:usage-limit":
            kind = r.need(f"PROMPT_KIND_{t}")
            if kind == "probe":
                back = r.need(f"PROMPT_BACK_{t}")
            elif kind.startswith("review"):
                back = f"reviewing:{kind[-1]}"
            else:
                back = {"commit": "committing", "fix": "fixing"}.get(kind, "impl")
            self.set_phase(t, back, WAITING_ON=None)
            r.log(f"{t} usage-limit prompt answered by the switch of picks: retrying")
            used.add("reply")
        state = ("--state", str(r.path))
        out_file = next_free(r.run_dir, "validate-{n}.txt")
        rc, out = tool("validate_meta.py", "tasks", *state, "--tasks-dir", str(r.docs / "tasks"),
                       "--ids", " ".join(ids))
        ground = r.get("GROUND_CREW_PAIR")
        if ground:
            rc2, out2 = tool("validate_meta.py", "harness-pair", *state, "--pair", ground)
            rc, out = rc or rc2, f"{out}\n{out2}"
        out_file.write_text(out + "\n", encoding="utf-8")
        if rc != 0:
            self.switch_fix_meta(out_file)
        try:
            Prober(r, self.runner).advance(review_pairs(r, ids) + ([ground] if ground else []))
        except Done as d:
            if d.code == STOP and d.line.startswith("PROBE_FAILED"):
                self.switch_fix_meta(step3_problems(r, d.line))
            self.probe_usage(t, d)
            raise
        self.move_to_new_pairs(t)
        r.set(SWITCH_TASKS=None, META_FAILS=0, STEP3_PROBED="yes")
        r.log(f"tasks {' '.join(ids)} fit the new picks")
        return used

    def switch_fix_meta(self, out_file):
        r = self.r
        if r.incr("META_FAILS") >= 3:
            raise Done(ERROR, f"ERROR META_INVALID_3_TIMES {out_file}")
        r.set(SWITCH_FIXMETA=out_file, WAITING_ON="director")
        r.log(f"the plan does not fit the new picks: {out_file}")
        raise Done(FIX_META, f"FIX_META {out_file}")

    def reread_ground_crew(self):
        """GROUND_CREW_PAIR as the flight manifest has it now (a fix-meta job may have changed it there)."""
        r = self.r
        manifest = r.get("FLIGHT_MANIFEST")
        if not manifest or not Path(manifest).is_file():
            return
        pair = read_meta(Path(manifest)).get("GROUND_CREW_PAIR")
        if pair and pair != r.get("GROUND_CREW_PAIR"):
            r.set(GROUND_CREW_PAIR=pair)
            r.log(f"GROUND_CREW_PAIR is now {pair}")

    def escalation_pending(self, t):
        """Whether task t waits on an escalation answer (or its replan): PAIR_<t> is then the last attempt's pair,
        which nothing uses again."""
        phase = self.phase(t)
        if phase.startswith("prompt:"):
            phase = self.r.get(f"PROMPT_BACK_{t}", "")
        return phase in ("escalating", "fixmeta", "replan-probe") or phase.startswith("probe:")

    def move_to_new_pairs(self, t):
        """Task t is in progress and its .meta fits the new picks: move its attempt to the pair it would have
        started on, and give a worker waiting on a consult answer its new pair. A changed pair gets a fresh
        conversation; the work it continues is what is committed in the worktree."""
        r = self.r
        if self.phase(t) == "start" or not r.get(f"PAIR_{t}"):
            return
        meta = self.meta(t)
        if self.escalation_pending(t):
            if r.get(f"ESC_INPUT_{t}"):
                r.set(**{f"ESC_INPUT_{t}": self.esc_input(t)[0]})
            return
        old, a = r.need(f"PAIR_{t}"), int(r.get(f"ATTEMPT_{t}", "0"))
        rungs = self.rungs(t)
        if a == 0:
            new = meta["CREW"]
        elif a <= len(rungs):
            new = str(rungs[a - 1])
        else:
            # Past the ladder, on a pair the user approved: kept while the catalog still has it.
            rc, _ = tool("validate_meta.py", "pair", "--state", str(r.path), "--task-meta",
                         str(r.task_file(t, ".meta")), "--pair", old, "--attempt", str(a), "--past-ladder", "yes")
            new = old if rc == 0 else r.need("ROLE_DIRECTOR")
        if new != old:
            r.set(**{f"PAIR_{t}": new, f"IMPL_CONVERSATION_ID_{t}": None})
            r.log(f"{t} crew pair {old} -> {new}")
        if self.phase(t) == "consult":
            kind = r.need(f"TASK_ASK_KIND_{t}")
            stage = r.get(f"TASK_ASK_STAGE_{t}") or kind[-1]
            want = meta[f"REVIEW_{stage}"] if kind.startswith("review") else new
            if want != r.get(f"TASK_ASK_PAIR_{t}"):
                r.set(**{f"TASK_ASK_PAIR_{t}": want, f"TASK_ASK_CONV_{t}": None})

    # -- driver
    def current(self):
        t = self.r.get("CURRENT_TASK")
        if t and self.phase(t) not in ("merged", "replaced"):
            return t
        q = self.queue()
        if q and t and q[0] == t and self.phase(t) == "merged":
            # Merged, but the queue write was interrupted: finish it.
            q = q[1:]
            self.write_queue(q)
        if not q:
            return None
        if q[0] != t:
            fresh = self.phase(q[0]) in ("merged", "replaced")
            self.r.set(CURRENT_TASK=q[0], **({f"LOOP_{q[0]}": "start"} if fresh else {}))
        return q[0]

    def advance(self, director_result=None, reply_word=None, answer_path=None, moved=False):
        if moved:
            docs_moved(self.r)
        t = self.current()
        if t and reply_word in ("stop", "pause", "switch"):
            self.reply(t, reply_word)  # these change no pair and start nothing, so they come before a pending switch
        used = self.after_switch(director_result)
        if "result" in used:
            director_result = None
        if "reply" in used:
            reply_word = None
        if director_result:
            if not t:
                raise Done(ERROR, "ERROR NO_CURRENT_TASK")
            self.director_result(t, director_result)
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
                wt = self.task_impl(t)
                cur_head = git(wt, "rev-parse", "HEAD")
                rc, out = tool("discard-changes.sh", wt, cur_head)
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
                rc, out = tool("discard-changes.sh", self.task_impl(t), self.r.need(f"WORKER_HEAD_{t}"))
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
                acc = " ".join(prompt_replies(phase, self.r.need(f"PROMPT_ACCEPT_{t}")))
                raise Done(PROMPT, f"PROMPT {self.r.need(f'PROMPT_FILE_{t}')} {acc}")
            else:
                raise Done(ERROR, f"ERROR UNKNOWN_PHASE {t} {phase}")
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
    def body(events):
        loop = Loop(a.state)
        loop.events = events
        loop.advance(a.director_result, a.reply.strip().strip('"').lower() if a.reply else None,
                     a.answer, a.docs_moved)

    return drive(a.state, body)


if __name__ == "__main__":
    sys.exit(main())
