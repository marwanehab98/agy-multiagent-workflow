"""Run the per-task loop (coordinator Steps 4-7) as a resumable state machine.

Each call does every deterministic action it can (start workers, save their results,
check the tree, run the gate, branch on verdicts, count rounds, merge) and exits at the
first point that needs a wait, the architect, or the user. All its state lives in the
coordinator's state.json, so a call after a crash or a context compaction carries on.
It never polls: it reads Orca's inbox without consuming mail, and exits when it must wait.

Usage:
  python3 -I run_task.py --state <RUN_DIR>/state.json advance
  python3 -I run_task.py --state <file> advance --architect-result <file with the RESULT line>
  python3 -I run_task.py --state <file> advance --reply <the user's one-word reply>

The last line of stdout is the status; earlier lines are events (MERGED ...).
Exit 0   QUEUE_EMPTY                       every queued task is merged
Exit 10  WAIT <dispatch> <kind>            end your turn; call advance again when woken
Exit 20  ESCALATE <input>                  send architect job escalate with INPUT=<input>
Exit 21  FIX_META <validate output file>   send architect job fix-meta with INPUT=<file>
Exit 30  PROMPT <file> <reply> [<reply>]   show <file> to the user exactly; pass the reply back
Exit 40  STOP <reason>                     dispatch nothing more; coordinator Step 8 item 7
Exit 2   ERROR <what>                      show the user and stop
Exit 64  bad usage
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

QUEUE_EMPTY, WAIT, ESCALATE, FIX_META, PROMPT, STOP, ERROR = 0, 10, 20, 21, 30, 40, 2
HERE = Path(__file__).resolve().parent


VERDICTS = ("VERDICT: PASS", "VERDICT: FIX")


def last_line(path):
    lines = [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def verdict_line(path):
    """The verdict a review file gives: its last line, or else the last VERDICT line anywhere in it."""
    if last_line(path) in VERDICTS:
        return last_line(path)
    found = re.findall(r"VERDICT: (?:PASS|FIX)\b", Path(path).read_text(encoding="utf-8"))
    return found[-1] if found else ""


class Done(Exception):
    def __init__(self, code, line):
        super().__init__(line)
        self.code, self.line = code, line


# ---------------------------------------------------------------- state and helpers

class Run:
    def __init__(self, path):
        self.path = Path(path)
        self.s = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key, default=None):
        return self.s.get(key, default)

    def need(self, key):
        if key not in self.s:
            raise Done(ERROR, f"ERROR MISSING_STATE {key}")
        return self.s[key]

    def set(self, **kv):
        for k, v in kv.items():
            if v is None:
                self.s.pop(k, None)
            else:
                self.s[k] = str(v)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.s, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path)

    def incr(self, key):
        n = int(self.get(key, "0")) + 1
        self.set(**{key: n})
        return n

    # paths
    @property
    def run_dir(self):
        return Path(self.need("RUN_DIR"))

    @property
    def docs(self):
        return Path(self.need("DOCS_DIR"))

    @property
    def impl(self):
        return self.need("IMPL_WORKTREE")

    def task_file(self, t, suffix):
        return self.docs / "tasks" / f"{t}{suffix}"

    def log(self, event):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (self.run_dir / "run-log.md").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {event}\n")


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def git(wt, *args):
    r = run(["git", "-C", wt, *args])
    return r.stdout.strip() if r.returncode == 0 else None


def tool(name, *args):
    """Run a sibling script through its interpreter. Returns (exit code, stdout)."""
    path = HERE / name
    cmd = ["bash", str(path), *args] if name.endswith(".sh") else [sys.executable, "-I", str(path), *args]
    r = run(cmd)
    return r.returncode, (r.stdout + r.stderr).strip()


def next_free(folder, pattern):
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    while (folder / pattern.format(n=n)).exists():
        n += 1
    return folder / pattern.format(n=n)


def read_meta(path):
    meta = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            meta[k.strip()] = v.strip()
    return meta


def split_pair(pair):
    agent, model = pair.split(":", 1)
    return agent, model


# ---------------------------------------------------------------- orca

class Orca:
    def __init__(self, r):
        self.r = r
        self.exe = r.need("ORCA")

    def call(self, *args, ok_required=True):
        res = run([self.exe, "orchestration", *args, "--json"])
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
            data = {"ok": False, "raw": (res.stdout + res.stderr)[:800]}
        if ok_required and not data.get("ok"):
            raise Done(ERROR, f"ERROR ORCA_FAILED {' '.join(args[:1])}: {json.dumps(data)[:800]}")
        return data

    def start(self, spec, title, pair=None, terminal=None, keep_open=False):
        task = self.call("task-create", "--spec-file", str(spec), "--task-title", title)["result"]["taskId"]
        args = ["worker-start", "--task", task, "--worktree", f"path:{self.r.impl}"]
        if terminal:
            args += ["--terminal", terminal]
        else:
            agent, model = split_pair(pair)
            args += ["--agent", agent, "--model", model]
            if keep_open:
                args.append("--keep-open")
        res = self.call(*args)["result"]
        return res["dispatchId"], res.get("terminal", "")

    def try_reuse(self, spec, title, terminal):
        task = self.call("task-create", "--spec-file", str(spec), "--task-title", title)["result"]["taskId"]
        data = self.call("worker-start", "--task", task, "--worktree", f"path:{self.r.impl}",
                         "--terminal", terminal, ok_required=False)
        if not data.get("ok"):
            return None
        return data["result"]["dispatchId"], data["result"].get("terminal", terminal)

    def send(self, dispatch, subject, body):
        return self.call("send", "--to", f"dispatch:{dispatch}", "--subject", subject, "--body", body)["result"]

    def show(self, dispatch):
        data = self.call("worker-show", "--dispatch", dispatch, ok_required=False)
        return data.get("result") if data.get("ok") else None

    def release(self, dispatch):
        if dispatch:
            self.call("worker-release", "--dispatch", dispatch, ok_required=False)

    def inbox(self):
        run_id = self.r.need("RUN_ID")
        return self.call("inbox", "--terminal", f"run:{run_id}", "--limit", "500", "--full")["result"]["messages"]


def payload(m):
    """`inbox --full` gives the payload as a JSON string; the concise view gives an object."""
    p = m.get("payload") or {}
    if isinstance(p, dict):
        return p
    try:
        return json.loads(p)
    except (json.JSONDecodeError, TypeError):
        return {}


def save_body(msg, out, header=None, append=False):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = (header + "\n" if header else "") + msg["body"].rstrip("\n") + "\n"
    with out.open("a" if append else "w", encoding="utf-8") as f:
        f.write(text)
    return out


# ---------------------------------------------------------------- the loop

class Loop:
    def __init__(self, state_path):
        self.r = Run(state_path)
        self.o = Orca(self.r)
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

    # -- spec building
    def build(self, template, out, sets=None, files=None):
        args = ["--template", str(HERE.parent / "references" / "templates" / template), "--out", str(out)]
        for k, v in (sets or {}).items():
            args += ["--set", f"{k}={v}"]
        for k, v in (files or {}).items():
            args += ["--file", f"{k}={v}"]
        rc, out_text = tool("build_spec.py", *args)
        if rc != 0:
            raise Done(ERROR, f"ERROR BUILD_SPEC {template}: {out_text}")
        return Path(out)

    # -- waiting
    def wait(self, t, kind, dispatch):
        self.set_phase(t, f"wait:{kind}", LOOP_WAIT=dispatch, WAITING_ON=dispatch)
        raise Done(WAIT, f"WAIT {dispatch} {kind}")

    def poll(self, dispatch):
        """('done', outcome, msg) | ('usage', detail) | ('idle', seq) | ('stopped', note) | None while working."""
        msgs = sorted(self.o.inbox(), key=lambda m: m.get("sequence", 0), reverse=True)
        mine = [m for m in msgs if payload(m).get("dispatchId") == dispatch]
        for m in mine:
            if m["type"] == "worker_done":
                return ("done", payload(m).get("outcome", "failed"), m)
        handled = int(self.r.get(f"HANDLED_SEQ_{dispatch}", "0"))
        for m in mine:
            if m["type"] != "escalation" or m.get("sequence", 0) <= handled:
                continue
            p = payload(m)
            if p.get("failureKind") == "usageLimitReached":
                return ("usage", p.get("detail") or p.get("error") or m["subject"])
            if p.get("stopReason") == "finished":
                # Ended its turn without worker_done; it may be waiting on a background command.
                return ("idle", m.get("sequence", 0))
            if p.get("stopReason") or p.get("turnOutcome") == "failure":
                return ("stopped", f"{m['subject']}\n{m['body']}")
        show = self.o.show(dispatch)
        if show and show.get("liveness") in ("exited", "lost") and show.get("stage") != "settled":
            return ("stopped", f"worker {dispatch} is {show.get('liveness')} without settling")
        return None

    NUDGE = ("You ended your turn without settling this task. If a command you started is still running, "
             "wait for it in the foreground until it finishes. Then finish the task and settle worker_done, "
             "with outcome succeeded or failed.")

    def nudge(self, t, kind, dispatch, seq):
        """A worker ended its turn without worker_done. Nudge it twice, then treat it as stopped."""
        self.r.set(**{f"HANDLED_SEQ_{dispatch}": seq})
        n = self.r.incr(f"NUDGES_{dispatch}")
        if n > 2:
            return ("stopped", f"worker {dispatch} ended its turn {n} times without settling")
        res = self.o.send(dispatch, "Finish and settle this task", self.NUDGE)
        new = res.get("dispatchId") if isinstance(res, dict) else None
        if new and new != dispatch:
            self.r.set(**{f"IMPL_DISPATCH_{t}": new} if not kind.startswith("review") else {})
            dispatch = new
        self.r.log(f"{t} {kind} worker {dispatch} idle without settling; nudge {n}")
        self.wait(t, kind, dispatch)

    # -- implementers
    def start_impl(self, t, spec, kind, title, reuse):
        if reuse:
            d = self.r.get(f"IMPL_DISPATCH_{t}")
            term = self.r.get(f"IMPL_TERMINAL_{t}")
            show = self.o.show(d) if d else None
            if show and term and show.get("terminalState") not in (None, "released"):
                got = self.o.try_reuse(spec, title, term)
                if got:
                    self.r.set(**{f"IMPL_DISPATCH_{t}": got[0], f"IMPL_TERMINAL_{t}": got[1],
                                  f"LAST_IMPL_SPEC_{t}": spec, f"LAST_IMPL_KIND_{t}": kind})
                    self.r.log(f"{t} {kind} reused {got[0]}")
                    self.wait(t, kind, got[0])
        pair = self.r.need(f"PAIR_{t}")
        d, term = self.o.start(spec, title, pair=pair, keep_open=True)
        self.r.set(**{f"IMPL_DISPATCH_{t}": d, f"IMPL_TERMINAL_{t}": term,
                      f"LAST_IMPL_SPEC_{t}": spec, f"LAST_IMPL_KIND_{t}": kind})
        self.r.log(f"{t} {kind} dispatched {d} {pair}")
        self.wait(t, kind, d)

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
        spec = self.build("implement.txt", r.run_dir / "specs" / f"{t}-implement.txt",
                          {"TASK_TITLE": meta["TITLE"]},
                          {"TASK": r.task_file(t, ".md"), "DECISIONS": r.task_file(t, "-decisions.md")})
        r.set(CURRENT_TASK=t, **{f"BASE_SHA_{t}": head, f"IMPLEMENT_SPEC_{t}": spec,
                                 f"PAIR_{t}": meta["IMPLEMENTER"], f"ATTEMPT_{t}": 0, f"FIX_ROUNDS_{t}": 0,
                                 f"FAILED_STAGE_{t}": None, f"FAIL_MSG_{t}": None, f"REJECTS_{t}": 0,
                                 f"NO_VERDICT_{t}_S1": 0, f"NO_VERDICT_{t}_S2": 0,
                                 f"VOIDS_{t}_S1": 0, f"VOIDS_{t}_S2": 0})
        self.start_impl(t, spec, "impl", meta["TITLE"], reuse=False)

    def after_impl(self, t, kind, result):
        r = self.r
        if result[0] == "usage":
            self.prompt_usage(t, kind, result[1])
        if result[0] == "stopped":
            note = next_free(r.run_dir / "results", f"{t}-stopped-{{n}}.md")
            note.write_text(result[1] + "\n", encoding="utf-8")
            r.set(**{f"FAIL_MSG_{t}": note})
            return self.escalate(t)
        _, outcome, msg = result
        if outcome != "succeeded":
            r.set(**{f"FAIL_MSG_{t}": save_body(msg, next_free(r.run_dir / "results", f"{t}-failed-{{n}}.md"))})
            r.log(f"{t} {kind} settled failed")
            return self.escalate(t)
        r.log(f"{t} {kind} settled succeeded")
        self.set_phase(t, "check")

    def do_check(self, t):
        r = self.r
        head = git(r.impl, "rev-parse", "HEAD")
        r.set(**{f"WORKER_HEAD_{t}": head})
        rc, out = tool("check-tree.sh", r.impl, head)
        if rc == 2:
            meta = self.meta(t)
            spec = self.build("commit-uncommitted.txt", next_free(r.run_dir / "specs", f"{t}-commit-{{n}}.txt"),
                              {"TASK_TITLE": meta["TITLE"]}, {"TASK": r.task_file(t, ".md")})
            self.start_impl(t, spec, "commit", f"Commit: {meta['TITLE']}", reuse=True)
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
            r.set(**{f"FAILED_STAGE_{t}": "gate"})
            return self.fix(t, log)
        if rc != 0:
            raise Done(ERROR, f"ERROR GATE {out}")
        failed = r.get(f"FAILED_STAGE_{t}")
        if failed == "gate":
            r.set(**{f"FIX_ROUNDS_{t}": 0})
        self.start_review(t, 2 if failed == "2" else 1)

    def fix(self, t, findings):
        r = self.r
        if int(r.get(f"FIX_ROUNDS_{t}", "0")) >= 2:
            return self.escalate(t)
        r.incr(f"FIX_ROUNDS_{t}")
        meta = self.meta(t)
        spec = self.build("fix.txt", next_free(r.run_dir / "specs", f"{t}-fix-{{n}}.txt"),
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
            spec = self.build("spec-review.txt", out, common,
                              {"TASK": r.task_file(t, ".md"), "DECISIONS": r.task_file(t, "-decisions.md")})
        else:
            spec = self.build("quality-review.txt", out, {**common, "CONVENTION_PATHS": r.need("CONVENTION_PATHS")},
                              {"SUMMARY": r.task_file(t, "-summary.md"), "DECISIONS": r.task_file(t, "-decisions.md")})
        pair = meta[f"REVIEW_{stage}"]
        d, _ = self.o.start(spec, f"Stage {stage} review: {meta['TITLE']}", pair=pair)
        r.log(f"{t} stage{stage} reviewer dispatched {d} {pair}")
        self.wait(t, f"review{stage}", d)

    def after_review(self, t, stage, result):
        r = self.r
        d = r.need("LOOP_WAIT")
        if result[0] == "usage":
            self.o.release(d)
            self.prompt_usage(t, f"review{stage}", result[1])
        if result[0] == "stopped":
            self.o.release(d)
            return self.void_review(t, stage, "stopped without a verdict")
        settle = save_body(result[2], next_free(r.run_dir / "results", f"{t}-s{stage}-settle-{{n}}.md"))
        verdict = self.pick_verdict(Path(r.need(f"REVIEW_OUT_{t}")), settle)
        self.o.release(d)
        head = r.need(f"WORKER_HEAD_{t}")
        rc, _ = tool("check-tree.sh", r.impl, head)
        if rc != 0:
            tool("discard-changes.sh", r.impl, head)
            verdict.rename(next_free(r.run_dir / "verdicts", f"{t}-void-s{stage}-{{n}}.md"))
            return self.void_review(t, stage, "changed the worktree")
        last = verdict_line(verdict)
        r.log(f"{t} stage{stage} {last or 'no verdict'} {verdict}")
        if last:
            r.set(**{f"NO_VERDICT_{t}_S{stage}": 0})
        if last == "VERDICT: PASS":
            if stage == 1:
                if r.get(f"FAILED_STAGE_{t}") == "1":
                    r.set(**{f"FIX_ROUNDS_{t}": 0})
                return self.start_review(t, 2)
            return self.set_phase(t, "merge")
        if last != "VERDICT: FIX" and r.incr(f"NO_VERDICT_{t}_S{stage}") < 2:
            return self.start_review(t, stage)
        r.set(**{f"FAILED_STAGE_{t}": stage})
        return self.fix(t, verdict)

    @staticmethod
    def pick_verdict(review, settle):
        """The reviewer's file when it ends in a VERDICT line; otherwise the settle message, copied there."""
        if review.is_file() and last_line(review) in VERDICTS:
            return review
        review.write_text(settle.read_text(encoding="utf-8"), encoding="utf-8")
        return review

    def void_review(self, t, stage, why):
        n = self.r.incr(f"VOIDS_{t}_S{stage}")
        self.r.log(f"{t} stage{stage} reviewer void: {why}")
        if n >= 3:
            raise Done(ERROR, f"ERROR REVIEWER_VOID_3_TIMES task {t} stage {stage}: {why}")
        self.start_review(t, stage)

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
        self.o.release(r.get(f"IMPL_DISPATCH_{t}"))
        r.set(LAST_MERGED=head, WAITING_ON=None, LOOP_WAIT=None)
        self.set_phase(t, "merged")
        r.log(f"{t} merged {head}")
        self.events.append(f"MERGED {t} {head}")

    # -- escalation
    def escalate(self, t):
        r = self.r
        self.o.release(r.get(f"IMPL_DISPATCH_{t}"))
        a = r.incr(f"ATTEMPT_{t}")
        meta = self.meta(t)
        ladder = meta.get("LADDER", "").split()
        nxt = f"next rung {ladder[a - 1]}" if a <= len(ladder) else "ladder used up"
        replan = "not allowed" if meta.get("REPLANNED_FROM") else "allowed"
        hist = r.run_dir / "verdicts" / f"{t}-history.md"
        parts = [f"task={t} base={r.need(f'BASE_SHA_{t}')} head={git(r.impl, 'rev-parse', 'HEAD')}\n"]
        if r.get(f"FAIL_MSG_{t}"):
            parts.append(Path(r.get(f"FAIL_MSG_{t}")).read_text(encoding="utf-8"))
        for f in sorted((r.run_dir / "verdicts").glob(f"{t}-s*.md")) + sorted((r.run_dir / "gates").glob(f"{t}-*.log")):
            parts.append(f"\n=== {f.name}\n" + f.read_text(encoding="utf-8"))
        hist.write_text("".join(parts), encoding="utf-8")
        line = f"task {t}, tier {meta['TIER']}, attempt {a}, {nxt}, replan {replan}, history {hist}"
        self.set_phase(t, "escalating", **{f"ESC_INPUT_{t}": line, "WAITING_ON": "architect", "LOOP_WAIT": None})
        r.log(f"{t} escalation attempt {a}: {nxt}")
        raise Done(ESCALATE, f"ESCALATE {line}")

    def reject(self, t, why):
        r = self.r
        hist = r.run_dir / "verdicts" / f"{t}-history.md"
        with hist.open("a", encoding="utf-8") as f:
            f.write(f"\n=== Rejected architect answer\n{why}\n")
        if r.incr(f"REJECTS_{t}") >= 2:
            self.prompt(t, "rejected",
                        f"The architect's escalation answers for task {t} were rejected twice:\n{why}\n"
                        "Reply \"retry\" to ask the architect again, or \"stop\" to stop the run.",
                        ["retry", "stop"], back_to="escalating")
        raise Done(ESCALATE, f"ESCALATE {r.need(f'ESC_INPUT_{t}')}")

    def architect_result(self, t, path):
        r = self.r
        text = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else ""
        words = next((l.split() for l in text.splitlines() if l.strip()), [])
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
            rc, out = tool("validate_meta.py", "tasks", "--tasks-dir", str(self.r.docs / "tasks"),
                           "--gemini-model", r.need("GEMINI_MODEL"), "--ids", t)
            if rc != 0:
                return self.reject(t, out)
            meta = self.meta(t)
            ladder = meta.get("LADDER", "").split()
            used_up = a > len(ladder)
            if not used_up and pair != ladder[a - 1]:
                return self.reject(t, f"pair {pair} is not rung {a} of LADDER ({' '.join(ladder)})")
            allow = "yes" if used_up or pair == ladder[a - 1] else "no"
            rc, out = tool("validate_meta.py", "pair", "--pair", pair, "--tier", meta["TIER"],
                           "--gemini-model", r.need("GEMINI_MODEL"), "--allow-opus", allow)
            if rc != 0:
                return self.reject(t, out)
            rc, out = tool("discard-changes.sh", r.impl, sha)
            if rc != 0:
                raise Done(ERROR, f"ERROR RESET {out}")
            spec = self.build("escalate.txt", r.run_dir / "specs" / f"{t}-attempt{a}.txt",
                              {"TASK_TITLE": meta["TITLE"], "RESET_SHA": sha},
                              {"NOTE": note, "DECISIONS": r.task_file(t, "-decisions.md"),
                               "IMPLEMENT_SPEC": r.run_dir / "specs" / f"{t}-implement.txt"})
            r.set(**{f"IMPLEMENT_SPEC_{t}": spec, f"PAIR_{t}": pair, f"FIX_ROUNDS_{t}": 0,
                     f"FAILED_STAGE_{t}": None, f"FAIL_MSG_{t}": None, f"REJECTS_{t}": 0})
            r.log(f"{t} escalation attempt {a} on {pair} from {sha}")
            return self.start_impl(t, spec, "impl", meta["TITLE"], reuse=False)
        if kind == "REPLAN" and len(words) >= 4:
            if meta.get("REPLANNED_FROM"):
                return self.reject(t, "Replan is not allowed for this task")
            r.set(**{f"PENDING_REPLAN_{t}": " ".join(words[2:])})
            return self.apply_replan(t, " ".join(words[2:]))
        return self.reject(t, f"unrecognised answer: {' '.join(words)[:300]}")

    def apply_replan(self, t, pending):
        r = self.r
        sha, *ids = pending.split()
        out_file = next_free(r.run_dir, "validate-{n}.txt")
        rc, out = tool("validate_meta.py", "tasks", "--tasks-dir", str(r.docs / "tasks"), "--gemini-model",
                       r.need("GEMINI_MODEL"), "--ids", " ".join(ids), "--replanned-from", t)
        out_file.write_text(out + "\n", encoding="utf-8")
        if rc != 0:
            if r.incr("META_FAILS") >= 3:
                raise Done(ERROR, f"ERROR META_INVALID_3_TIMES {out_file}")
            self.set_phase(t, "fixmeta", WAITING_ON="architect", **{f"FIXMETA_FILE_{t}": out_file})
            raise Done(FIX_META, f"FIX_META {out_file}")
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
            self.o.release(r.get(f"IMPL_DISPATCH_{t}"))
            r.set(LAST_MERGED=new)
            return self.set_phase(t, "start")
        if name == "rejected":
            r.set(**{f"REJECTS_{t}": 0})
            self.set_phase(t, "escalating", WAITING_ON="architect")
            raise Done(ESCALATE, f"ESCALATE {r.need(f'ESC_INPUT_{t}')}")
        if name == "usage-limit":
            kind = r.need(f"PROMPT_KIND_{t}")
            if kind.startswith("review"):
                return self.start_review(t, int(kind[-1]))
            run(["git", "-C", r.impl, "reset", "--hard", "HEAD"])
            run(["git", "-C", r.impl, "clean", "-fd"])
            self.o.release(r.get(f"IMPL_DISPATCH_{t}"))
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

    def advance(self, architect_result=None, reply_word=None):
        t = self.current()
        if architect_result:
            if not t:
                raise Done(ERROR, "ERROR NO_CURRENT_TASK")
            self.architect_result(t, architect_result)
        elif reply_word:
            if not t:
                raise Done(ERROR, "ERROR NO_CURRENT_TASK")
            self.reply(t, reply_word)
        for _ in range(200):
            t = self.current()
            if t is None:
                self.r.set(WAITING_ON=None, LOOP_WAIT=None, STEP="run_task queue empty")
                raise Done(QUEUE_EMPTY, "QUEUE_EMPTY")
            phase = self.phase(t)
            if phase == "start":
                self.do_start(t)
            elif phase.startswith("wait:"):
                kind = phase.split(":", 1)[1]
                result = self.poll(self.r.need("LOOP_WAIT"))
                if result is None:
                    raise Done(WAIT, f"WAIT {self.r.need('LOOP_WAIT')} {kind}")
                if result[0] == "idle":
                    result = self.nudge(t, kind, self.r.need("LOOP_WAIT"), result[1])
                if kind.startswith("review"):
                    self.after_review(t, int(kind[-1]), result)
                else:
                    self.after_impl(t, kind, result)
            elif phase == "check":
                self.do_check(t)
            elif phase == "merge":
                self.do_merge(t)
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
    a = p.parse_args()
    loop = Loop(a.state)
    try:
        loop.advance(a.architect_result, a.reply.strip().strip('"').lower() if a.reply else None)
        code, line = ERROR, "ERROR NO_STATUS"
    except Done as d:
        code, line = d.code, d.line
    for e in loop.events:
        print(e)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
