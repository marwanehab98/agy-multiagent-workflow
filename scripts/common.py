"""Plumbing shared by the run_*.py scripts: status codes, state file, Orca CLI wrapper, helpers."""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I drops the script dir from sys.path
from harnesses import launch_args, pick_style  # noqa: E402
from pairs import parse_pair  # noqa: E402

QUEUE_EMPTY, WAIT, ESCALATE, FIX_META, PROMPT, STOP, ERROR = 0, 10, 20, 21, 30, 40, 2

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


def pick_verdict(review, settle):
    """The reviewer's file when it ends in a VERDICT line; otherwise the settle message, copied there."""
    review, settle = Path(review), Path(settle)
    if review.is_file() and last_line(review) in VERDICTS:
        return review
    review.write_text(settle.read_text(encoding="utf-8"), encoding="utf-8")
    return review


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

    def require_new_format(self):
        if "ROLE_ARCHITECT" not in self.s:
            raise Done(ERROR, f"ERROR OLD_FORMAT_RUN remove {self.get('RUN_DIR', self.path.parent)} and start a new run")

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

    def start(self, spec, title, pair, worktree, keep_open=False, terminal=None):
        if not terminal:
            return self.start_args(spec, title, self.launch(pair), worktree, keep_open)
        task = self.call("task-create", "--spec-file", str(spec), "--task-title", title)["result"]["taskId"]
        res = self.call("worker-start", "--task", task, "--worktree", f"path:{worktree}",
                        "--terminal", terminal)["result"]
        return res["dispatchId"], res.get("terminal", "")

    def launch(self, pair):
        """Agent arguments for a pair, in the launch style recorded for it (or the fallback style)."""
        catalog = json.loads(Path(self.r.need("CATALOG")).read_text(encoding="utf-8"))
        parsed = parse_pair(pair)
        return launch_args(catalog, parsed, pick_style(catalog, self.r.s, parsed))

    def start_args(self, spec, title, args, worktree, keep_open=False):
        """Create a task and start a worker with the given agent args (e.g. from launch_args)."""
        task = self.call("task-create", "--spec-file", str(spec), "--task-title", title)["result"]["taskId"]
        cmd = ["worker-start", "--task", task, "--worktree", f"path:{worktree}", *args]
        if keep_open:
            cmd.append("--keep-open")
        res = self.call(*cmd)["result"]
        return res["dispatchId"], res.get("terminal", "")

    def try_reuse(self, spec, title, terminal, worktree):
        task = self.call("task-create", "--spec-file", str(spec), "--task-title", title)["result"]["taskId"]
        data = self.call("worker-start", "--task", task, "--worktree", f"path:{worktree}",
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


def build(template, out, sets=None, files=None):
    args = ["--template", str(HERE.parent / "references" / "templates" / template), "--out", str(out)]
    for k, v in (sets or {}).items():
        args += ["--set", f"{k}={v}"]
    for k, v in (files or {}).items():
        args += ["--file", f"{k}={v}"]
    rc, out_text = tool("build_spec.py", *args)
    if rc != 0:
        raise Done(ERROR, f"ERROR BUILD_SPEC {template}: {out_text}")
    return Path(out)


def poll(orca, run, dispatch):
    """('done', outcome, msg) | ('usage', detail) | ('idle', seq) | ('stopped', note) | None while working."""
    msgs = sorted(orca.inbox(), key=lambda m: m.get("sequence", 0), reverse=True)
    mine = [m for m in msgs if payload(m).get("dispatchId") == dispatch]
    for m in mine:
        if m["type"] == "worker_done":
            return ("done", payload(m).get("outcome", "failed"), m)
    handled = int(run.get(f"HANDLED_SEQ_{dispatch}", "0"))
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
    show = orca.show(dispatch)
    if show and show.get("liveness") in ("exited", "lost") and show.get("stage") != "settled":
        return ("stopped", f"worker {dispatch} is {show.get('liveness')} without settling")
    return None
