"""Plumbing shared by the run_*.py scripts: status codes, state file, headless CLI worker runner, helpers."""
import codecs
import collections
import datetime
import fcntl
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I drops the script dir from sys.path
from harnesses import _as_text, _one_line, get_adapter, launch_args, pick_style  # noqa: E402
from pairs import Pair, parse_pair  # noqa: E402

QUEUE_EMPTY, WAIT, ESCALATE, FIX_META, CONSULT, PROMPT, STOP, PAUSED, ERROR = 0, 10, 20, 21, 22, 30, 40, 41, 2

VERDICTS = ("VERDICT: PASS", "VERDICT: FIX")

# A bare 429 or "quota" turns up in ordinary text (token counts, code under review), so both need a word of error
# context next to them, in either order ("quota reached", "out of quota", "got 429 from the API", "Error: 429").
_429_CTX = r"(?:rate|limit|too many|resource|api|request|response|status|code|error|http)"
USAGE_RE = re.compile(
    r"(?i)(usageLimitReached|usage\s*limit|rate[_\s]*limit|resource_exhausted|"
    r"quota\s+(?:exceeded|exhausted|reached|limit)|"
    r"(?:exceeded|exhausted|insufficient|reached|out\s+of|over)\s+(?:\w+\s+){0,2}quota|"
    rf"\b{_429_CTX}[^\n]{{0,12}}?\b429\b|\b429\b[^\n]{{0,12}}?\b{_429_CTX}|"
    r"5-hour limit|overloaded_error|hit your limit|out of extra usage|credit balance is too low)"
)
RESUME_ERR_RE = re.compile(
    r"(?i)((conversation|session).*(not found|invalid|expired|does not exist|unknown|missing)|"
    r"no conversation found with session id|cannot resume|unknown (conversation|session))"
)


def error_text(stdout_lines, stderr_text):
    """The parts of a worker's output that can report an error, for classifying a run without a result: all of
    stderr, stdout lines that are not JSON objects, and JSON objects that are errors. Ordinary stream events
    (assistant text, tool calls and results) are left out: they quote code, logs and token counts."""
    parts = [stderr_text]
    for line in stdout_lines:
        s = line.strip()
        try:
            obj = json.loads(s) if s.startswith("{") else None
        except json.JSONDecodeError:
            obj = None
        if not isinstance(obj, dict) or "error" in obj or obj.get("type") == "error" or obj.get("is_error") is True:
            parts.append(s)
    return "\n".join(x for x in parts if x).strip()


def last_line(path):
    lines = [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def first_nonempty_line(text):
    return next((l.strip() for l in text.splitlines() if l.strip()), "")


def verdict_line(path):
    """The verdict a review file gives: its last line, or else the last VERDICT line anywhere in it."""
    if last_line(path) in VERDICTS:
        return last_line(path)
    found = re.findall(r"VERDICT: (?:PASS|FIX)\b", Path(path).read_text(encoding="utf-8"))
    return found[-1] if found else ""


def pick_verdict(review, settle):
    """The reviewer's file when it gives a VERDICT line; otherwise the settle text/file, copied there."""
    review = Path(review)
    if review.is_file() and verdict_line(review) in VERDICTS:
        return review
    text = settle.read_text(encoding="utf-8") if isinstance(settle, Path) else str(settle)
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    return review


_RESULT_TOKEN = re.compile(r'`([^`]*)`|"([^"]*)"|(\S+)')


def result_words(text):
    """The words of a flight director's RESULT line, without what it may add around the line itself.

    Flight directors sometimes wrap the line in backticks or bold, end it with a period, or carry on with
    prose on the same line ("RESULT: FLIGHT_MANIFEST_FIXED. The baseline failed because ..."; live test
    2026-11-15). Take the first non-empty line from "RESULT:", stop at the first sentence break
    (a word ending in "." or ";" with more after it, a dash, an unpaired backtick), and drop trailing
    punctuation from each argument. A whole argument in backticks or double quotes is one word, so a
    path with spaces survives (`RESULT: RELAY "/home/u/My Projects/q.md"`).
    """
    line = next((l for l in text.splitlines() if l.strip()), "")
    m = re.search(r"RESULT:.*", line)
    line = (m.group(0) if m else line.strip()).replace("*", "")
    words = []
    for tok in _RESULT_TOKEN.finditer(line):
        quoted = tok.group(1) if tok.group(1) is not None else tok.group(2)
        if quoted is not None:
            if quoted.strip():
                words.append(quoted.strip())
            continue
        w = tok.group(3)
        if words and re.fullmatch(r"[-\u2013\u2014]+", w):
            break
        stop = "`" in w
        w = w.split("`", 1)[0]
        last = tok.end() >= len(line.rstrip())
        if not stop and not last and w.endswith((".", ";")):
            stop = True
        w = w if not words else w.rstrip(".,;:")
        if w:
            words.append(w)
        if stop:
            break
    return words


def rest_path(words, start):
    """The argument at words[start], or, when that is not an existing path, words[start:] joined with
    spaces when that is (an unquoted path containing spaces)."""
    if start >= len(words):
        return ""
    if Path(words[start]).exists():
        return words[start]
    joined = " ".join(words[start:])
    return joined if Path(joined).exists() else words[start]


def first_words(path):
    """result_words of a file, or [] when it is missing."""
    p = Path(path)
    return result_words(p.read_text(encoding="utf-8")) if p.is_file() else []


class Done(Exception):
    def __init__(self, code, line):
        super().__init__(line)
        self.code, self.line = code, line


def repo_key(name):
    """Normalize a repo directory name into a safe state/flight-manifest key suffix."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def read_meta(path, strict=False):
    """KEY=VALUE lines of a .meta or flight-manifest file. strict: a non-blank line without '=' is a ValueError."""
    values = {}
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if "=" not in line:
            if strict and line.strip():
                raise ValueError(f"line {n} is not KEY=VALUE: {line!r}")
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def write_atomic(path, text):
    """Write a file via temp file + rename, so a crash never leaves it half-written. A failed write or rename
    removes the temp file and raises. An existing file keeps its permissions."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            if path.exists():
                os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _FileLock:
    """An flock on <path>; blocking by default, or Done(ERROR, busy_line) when non-blocking and held."""

    def __init__(self, path, blocking=True, busy_line=None):
        self.path, self.blocking, self.busy_line, self.fh = Path(path), blocking, busy_line, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a+", encoding="utf-8")
        flags = fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(self.fh, flags)
        except BlockingIOError:
            self.fh.seek(0)
            holder = self.fh.read().strip()
            self.fh.close()
            raise Done(ERROR, (self.busy_line or "ERROR LOCKED {holder}").format(holder=holder or "?"))
        if not self.blocking:
            self.fh.seek(0)
            self.fh.truncate()
            self.fh.write(f"pid={os.getpid()}\n")
            self.fh.flush()
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


def state_lock(state_path):
    """The short lock every state.json write takes (Run.set and state.py)."""
    p = Path(state_path)
    return _FileLock(p.with_name(f".{p.name}.lock"))


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
        """Apply kv to the state file under its lock, re-reading it first so a concurrent `STATE set` from
        the capsule communicator is merged instead of overwritten."""
        with state_lock(self.path):
            try:
                self.s = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                pass
            for k, v in kv.items():
                if v is None:
                    self.s.pop(k, None)
                else:
                    self.s[k] = str(v)
            write_atomic(self.path, json.dumps(self.s, indent=1, sort_keys=True) + "\n")

    def incr(self, key):
        n = int(self.get(key, "0")) + 1
        self.set(**{key: n})
        return n

    # paths & multi-repo helpers
    @property
    def run_dir(self):
        return Path(self.need("RUN_DIR"))

    @property
    def docs(self):
        return Path(self.need("DOCS_DIR"))

    @property
    def impl(self):
        return self.need("IMPL_WORKTREE")

    @property
    def repos(self):
        raw = (self.get("REPOS") or "").strip()
        return [] if not raw or raw == "none" else raw.split()

    @property
    def is_multi_repo(self):
        return bool(self.repos)

    def repo_checkout(self, repo=None):
        if repo:
            return self.need(f"REPO_PATH_{repo_key(repo)}")
        return self.need("MAIN_CHECKOUT")

    def repo_impl(self, repo=None):
        if repo:
            return self.need(f"IMPL_WT_{repo_key(repo)}")
        return self.need("IMPL_WORKTREE")

    def repo_docs(self, repo):
        return self.need(f"DOCS_WT_{repo_key(repo)}")

    def repo_feature_branch(self, repo=None):
        if repo:
            return self.get(f"FEATURE_BRANCH_{repo_key(repo)}") or self.need("FEATURE_BRANCH")
        return self.need("FEATURE_BRANCH")

    def repo_last_merged_key(self, repo=None):
        return f"LAST_MERGED_{repo_key(repo)}" if repo else "LAST_MERGED"

    def repo_last_merged(self, repo=None):
        return self.need(self.repo_last_merged_key(repo))

    def repo_initial_base(self, repo=None):
        if repo:
            return self.need(f"INITIAL_BASE_SHA_{repo_key(repo)}")
        return self.need("INITIAL_BASE_SHA")

    def repo_convention_paths(self, repo=None):
        if repo:
            return self.get(f"CONVENTION_PATHS_{repo_key(repo)}") or self.get("CONVENTION_PATHS", "none")
        return self.get("CONVENTION_PATHS", "none")  # optional in the flight manifest

    def touched_repos(self):
        """Repos that have at least one merged commit since INITIAL_BASE_SHA_<k>."""
        if not self.is_multi_repo:
            return []
        return [repo for repo in self.repos if self.repo_last_merged(repo) != self.repo_initial_base(repo)]

    def task_file(self, t, suffix):
        return self.docs / "tasks" / f"{t}{suffix}"

    def require_new_format(self):
        if "ROLE_DIRECTOR" not in self.s:
            raise Done(ERROR, f"ERROR OLD_FORMAT_RUN remove {self.get('RUN_DIR', self.path.parent)} and start a new run")

    def log(self, event):
        """Record a run event in mission-log.md, and as a milestone in progress.log and on stderr."""
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (self.run_dir / "mission-log.md").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {event}\n")
        progress(self, f"[progress] {event}", milestone=True)


def progress(r, line, milestone=False):
    """Append a line to <RUN_DIR>/progress.log, the one file to watch for a live view of a run. Milestones
    (run events, worker start/finish) also go to stderr; per-tool worker lines go there only when
    PROGRESS_STDERR=all, so a foreground call's output stays short."""
    run_dir = r.get("RUN_DIR") if r is not None else None
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    if run_dir:
        try:
            with (Path(run_dir) / "progress.log").open("a", encoding="utf-8") as f:
                f.write(f"{stamp} {line}\n")
        except OSError:
            pass
    if milestone or os.environ.get("PROGRESS_STDERR", "").strip().lower() == "all":
        sys.stderr.write(f"{line}\n")
        sys.stderr.flush()


def check_repo_trees(r, which):
    """In multi-repo mode, check every mounted repo worktree ('docs' or 'impl'). Returns (rc, out)."""
    for repo in r.repos:
        k = repo_key(repo)
        wt = r.need(f"DOCS_WT_{k}") if which == "docs" else r.need(f"IMPL_WT_{k}")
        sha = r.need(f"START_SHA_{k}") if which == "docs" else r.need(f"LAST_MERGED_{k}")
        rc, out = tool("check-tree.sh", wt, sha)
        if rc != 0:
            return rc, f"[{repo}] {out}"
    return 0, ""


def discard_repo_trees(r, which):
    """In multi-repo mode, restore every mounted repo worktree ('docs' or 'impl'). Returns (rc, out)."""
    for repo in r.repos:
        k = repo_key(repo)
        wt = r.need(f"DOCS_WT_{k}") if which == "docs" else r.need(f"IMPL_WT_{k}")
        sha = r.need(f"START_SHA_{k}") if which == "docs" else r.need(f"LAST_MERGED_{k}")
        rc, out = tool("discard-changes.sh", wt, sha)
        if rc != 0:
            return rc, f"[{repo}] {out}"
    return 0, ""


# Child processes this driver is waiting on: (Popen, own_session). The signal handler stops them all.
_CHILDREN = []


def run(cmd, cwd=None):
    """subprocess.run(capture_output) that the driver's signal handler can stop (gate.py, git, scripts)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace")
    entry = (proc, False)
    _CHILDREN.append(entry)
    try:
        out, err = proc.communicate()
    finally:
        _CHILDREN.remove(entry)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _stop_gently(proc, earlier=()):
    """Stop a worker (own session) at a limit: SIGTERM its group, give it 5 seconds to save and exit, then kill its
    group and everything listed below it (now, and earlier while it ran), and reap it."""
    kids = _descendants(proc.pid) if proc.poll() is None else []
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass
    _kill_tree(proc.pid, kids, earlier=earlier, alive=proc.poll() is None)
    _reap(proc)


def _stop_child(proc, own_session):
    if proc.poll() is not None and not own_session:
        return
    # A worker (own session) gets SIGTERM first; then it and whatever it started, listed while it still ran, are killed.
    kids = _descendants(proc.pid) if own_session and proc.poll() is None else []
    try:
        if own_session:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        if own_session:
            _kill_tree(proc.pid, kids)
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if not own_session:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
    if own_session:
        _kill_tree(proc.pid, kids)


class Interrupted(Exception):
    pass


def _on_signal(signum, _frame):
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):  # a second signal must not cut this short
        signal.signal(sig, signal.SIG_IGN)
    for proc, own in list(_CHILDREN):
        _stop_child(proc, own)
    raise Interrupted(signal.Signals(signum).name)


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _ps_table():
    """(pid, ppid, pgid) of every process, from one `ps` listing (Linux and macOS). Empty when ps fails."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid="], capture_output=True, text=True,
                             errors="replace", timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.splitlines():
        f = line.split()
        if len(f) == 3 and all(x.isdigit() for x in f):
            rows.append((int(f[0]), int(f[1]), int(f[2])))
    return rows


def _descendants(pid, table=None):
    """(pid, pgid) of every process below pid, from one `ps` listing (table: a _ps_table() already taken)."""
    children = collections.defaultdict(list)
    for p, ppid, g in (_ps_table() if table is None else table):
        children[ppid].append((p, g))
    found, todo = [], [int(pid)]
    while todo:
        for kid in children.get(todo.pop(), []):
            if kid not in found:
                found.append(kid)
                todo.append(kid[0])
    return found


def _kill_tree(pid, kids=None, earlier=(), alive=True):
    """SIGKILL a worker's process group and everything below it (kids: a _descendants listing taken earlier).
    A worker's tools may start commands in a session and process group of their own (a background shell
    command of one harness does: live 2026-11-24), which a signal to the worker's group misses; while the worker
    runs they are still its descendants, so list those first. Once the worker has exited they belong to init and
    cannot be told apart, so stop the worker this way, not after it, or pass what was listed while it ran.

    earlier: (pid, pgid) pairs listed while the worker still ran, which may be stale by now. Each is killed (with
    whatever runs below it now) only when a fresh listing still shows that pid in the same process group, so a
    pid reused since is never hit. alive=False: the worker is known to have exited, so a fresh listing could find
    nothing below it; ps then runs only when there is an earlier listing to check."""
    earlier = list(dict.fromkeys(earlier))
    table = _ps_table() if (kids is None and alive) or earlier else []
    kids = list(_descendants(pid, table) if kids is None else kids)
    if earlier:
        now = {(p, g) for p, _, g in table}
        for k in earlier:
            if k in now:
                kids += [k] + _descendants(k[0], table)
        kids = list(dict.fromkeys(kids))
    for target, group in [(int(pid), True)] + [(g, True) for g in {g for _, g in kids} if g != int(pid)] + \
            [(p, False) for p, _ in kids]:
        try:
            (os.killpg if group else os.kill)(target, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _reap(proc):
    """Wait for a worker that was sent SIGKILL; a slow exit gets a direct kill, and never raises."""
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _pid_command(pid):
    r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def reap_orphan_worker(state_path):
    """A worker recorded in state (WORKER_PID) that still runs while we hold the driver lock lost its driver
    (killed with SIGKILL, or a crash): stop its process group before starting anything new in its worktree.
    The recorded executable must appear in the process's command line, so a reused pid is never killed."""
    try:
        s = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid, exe = s.get("WORKER_PID"), s.get("WORKER_EXE", "")
    if not pid or not str(pid).isdigit():
        return None
    cmd = _pid_command(pid)
    note = None
    # The worker leads its own process group (start_new_session). With the leader alive, its command line
    # must name the worker's executable (a reused pid is never killed). With the leader gone, a group with
    # that id can only hold what the worker started (a new process with that pid would lead it), so stop it.
    if (cmd and exe and Path(exe).name in cmd) or (not cmd and _group_alive(int(pid))):
        kids = _descendants(pid) if cmd else []  # what it started in a session of its own (see _kill_tree)
        try:
            os.killpg(int(pid), signal.SIGKILL)
            note = f"stopped orphaned worker pid={pid} ({Path(exe).name}) left by an interrupted call"
        except (ProcessLookupError, PermissionError):
            pass
        _kill_tree(pid, kids)
    r = Run(state_path)
    r.set(WORKER_PID=None, WORKER_EXE=None)
    if note and r.get("RUN_DIR"):
        r.log(note)
    return note


def _record_call(run_dir, call=None):
    """Record the call holding the driver lock in <RUN_DIR>/.call for watch.py: a new call (id, pid, where
    progress.log ends now), or, given the call, mark it ended. Never fails the call."""
    try:
        if call is None:
            log = Path(run_dir) / "progress.log"
            call = {"id": f"{os.getpid()}-{time.time_ns()}", "pid": os.getpid(),
                    "offset": log.stat().st_size if log.exists() else 0, "ended": False}
        else:
            call = {**call, "ended": True}
        write_atomic(Path(run_dir) / ".call", json.dumps(call))
    except OSError:
        pass
    return call


def drive(state_path, body):
    """Run one `advance` (or director command): hold the run's driver lock so two calls never overlap, stop
    every child on SIGINT/SIGTERM/SIGHUP, stop an orphaned worker first, and turn any exception into a status
    line. body(events) raises Done (or returns (code, line)); events are printed before the status line."""
    events = []
    old = {}
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            old[sig] = signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass
    try:
        if not Path(state_path).is_file():
            raise Done(ERROR, f"ERROR MISSING_FILE {state_path}")
        lock = Path(state_path).resolve().parent / ".driver.lock"
        with _FileLock(lock, blocking=False,
                       busy_line="ERROR RUN_BUSY another mission-control script is running on this run ({holder}); "
                                 "wait for it to finish"):
            call = _record_call(lock.parent)
            try:
                reap_orphan_worker(state_path)
                res = body(events)
            finally:
                _record_call(lock.parent, call)
        code, line = res if res else (ERROR, "ERROR NO_STATUS")
    except Done as d:
        code, line = d.code, d.line
    except Interrupted as e:
        code, line = ERROR, f"ERROR INTERRUPTED by {e}; workers stopped. Run the same command again to resume"
    except Exception as e:  # noqa: BLE001 - every failure must still end in one status line
        code, line = ERROR, f"ERROR {type(e).__name__}: {' '.join(str(e).split())[:500]}"
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)
    for e in events:
        print(e)
    print(line)
    return code


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


def bundled_skills_dir(skill_name):
    """Return the bundled skills root containing `<skill_name>/SKILL.md`, or None."""
    root = HERE.parent
    for candidate in (root / "skills", root.parent):
        if (candidate / skill_name / "SKILL.md").is_file():
            return str(candidate)
    return None


# ---------------------------------------------------------------- headless worker runner

# Read-only workers do not need the orchestrator's credentials; a prompt injection in repo or ticket text
# should find nothing to exfiltrate. The harness's own sign-in variables are kept, and WORKER_ENV_KEEP
# (space-separated names) keeps more. Crew and flight director workers run builds and tests, so they keep all.
_SECRET_ENV = re.compile(r"(?i)(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_KEY|API_KEY|ACCESS_KEY|SESSION_KEY)"
                         r"|^(AWS|AZURE|GCP|GOOGLE_APPLICATION|GH|GITHUB|GITLAB|NPM|PYPI|DOCKER|KUBE|VAULT)_")
_HARNESS_ENV = re.compile(r"^(ANTHROPIC_|CLAUDE_|CLAUDECODE$|GEMINI_|ANTIGRAVITY_|AGY_|GOOGLE_CLOUD_PROJECT$|"
                          r"GOOGLE_API_KEY$|GOOGLE_GENAI_)")
# A harness signed in through a cloud provider needs that provider's credentials too.
_PROVIDER_ENV = [
    ("CLAUDE_CODE_USE_BEDROCK", re.compile(r"^AWS_")),
    ("CLAUDE_CODE_USE_VERTEX", re.compile(r"^(GOOGLE_|GCLOUD_|CLOUDSDK_|CLOUD_ML_|VERTEX_)")),
    ("AGY_ADC_AUTH", re.compile(r"^(GOOGLE_|GCLOUD_|CLOUDSDK_)")),
]


# Harness settings whose names match the secret pattern (TOKEN) but hold no secret.
_NOT_SECRET_ENV = {"MAX_THINKING_TOKENS", "MAX_MCP_OUTPUT_TOKENS"}


def worker_env(access, environ=None):
    env = dict(os.environ if environ is None else environ)
    if access != "read":
        return env
    keep = set(env.get("WORKER_ENV_KEEP", "").split())
    providers = [pat for switch, pat in _PROVIDER_ENV if str(env.get(switch, "")).strip().lower() not in ("", "0", "false")]
    return {k: v for k, v in env.items()
            if k in keep or k in _NOT_SECRET_ENV or _HARNESS_ENV.match(k) or any(p.match(k) for p in providers)
            or not _SECRET_ENV.search(k)}


MAX_INLINE_PROMPT_BYTES = 65536
# Seconds a worker call may run in all, busy or not, a resume after silence included: the backstop on cost
# (WORKER_TIMEOUT, when set, applies to every worker). Workers that edit (crew, flight director) get longer than
# read-only ones.
LEGACY_TIMEOUT_KEY = "AGY_TIMEOUT"  # WORKER_TIMEOUT's old name (see WorkerRunner._timeout)
DEFAULT_TIMEOUT_FULL = 14400
DEFAULT_TIMEOUT_READ = 7200
# Without stream-json (WORKER_STREAM=0, or a CLI that falls back to plain JSON) there is no activity to see and so no
# silence limit: the run limit is the only one, and keeps its tighter defaults.
DEFAULT_TIMEOUT_FULL_NO_STREAM = 3600
DEFAULT_TIMEOUT_READ_NO_STREAM = 1800
# Seconds a streaming worker may go without activity before it is stopped as stuck (WORKER_IDLE_TIMEOUT): any stream
# event except those its adapter knows are not the worker at work (background command updates, hooks, rate-limit
# notices, API retries), and not stderr. Not applied while the worker holds a result or waits on background commands
# with no result (agy). As long as GATE_TIMEOUT, so a check the run itself allows can run in a worker.
DEFAULT_IDLE_TIMEOUT = 1800
# A worker stopped for silence is resumed once in its own conversation, with this prompt and what is left of
# WORKER_TIMEOUT, so its work is not thrown away; only when the stream showed that conversation, and at least
# MIN_RESUME_SECONDS of the budget is left. {last}: what the stream last showed it doing; {prompt}: the message it was
# working on.
MIN_RESUME_SECONDS = 300
RECENT_MARGIN = 1.0
SILENCE_RESUME_PROMPT = (
    "The run stopped you ({why}) and stopped every command you had running.{last} Your files are as you left them. "
    "If you had started on your task, continue from where you left off without redoing finished work; otherwise do "
    "it now. Give a command that may run long a time limit. End exactly as your instructions say. {task}"
)
# {task} for a worker given a spec file, and for one given a message (repeated: it may not have reached the
# conversation before the worker went silent).
RESUME_TASK_SPEC = "Your instructions are in {spec}: read them again."
RESUME_TASK_TEXT = ("Your task is the message below, repeated in case it had not reached you.\n\n"
                    "--- The message you were working on ---\n{prompt}")

DEFAULT_HEARTBEAT = 30
# Seconds a worker may run on after its latest result, with nothing showing it is still busy, before it is stopped
# and the result used (a CLI that never exits would otherwise hold a finished result until WORKER_TIMEOUT and lose
# it; the silence limit does not apply while a result is held). Turn activity after a result (a new turn, a
# message, a tool result) stops this clock until the next result; background task events do not. When the last
# background command ends, the grace starts again from then.
ENVELOPE_GRACE = 10
# The same, counted from the result, while the worker reports background commands still running (it may start a
# new turn when they end): a dev server left in the background must not hold a finished worker until WORKER_TIMEOUT.
# WORKER_BACKGROUND_WAIT (state key or environment) overrides it; 0 waits until WORKER_TIMEOUT.
BACKGROUND_WAIT = 900
STREAM_UNSUPPORTED_RE = re.compile(
    r"(?i)(unknown|invalid|unrecognized|unsupported|unexpected).*(stream-json|--verbose|output-format)"
)


def _read_spec_or_text(spec_or_text):
    """Return (text, path_or_none) without hitting ENAMETOOLONG on multi-line or long prompt strings."""
    if isinstance(spec_or_text, Path):
        return spec_or_text.read_text(encoding="utf-8"), spec_or_text
    if isinstance(spec_or_text, str) and "\n" not in spec_or_text and len(spec_or_text) < 4096:
        p = Path(spec_or_text)
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8"), p
        except OSError:
            pass
    return str(spec_or_text), None


def envelope_stats(data):
    """Cost, turns, duration and tokens from a result envelope, or from every result event of one process (a list).

    A worker that waits on a background command may print a result per turn segment (live 2026-11-24):
    num_turns, duration_ms and usage are then that segment's own, so they are summed; total_cost_usd is the
    process's running total, so the latest one counts. Values a harness does not report are left out."""
    envs = [d for d in (data if isinstance(data, list) else [data]) if isinstance(d, dict)]
    out, turns, secs, tokens = {}, None, None, None
    cost = envelope_cost(envs)
    if cost is not None:
        out["cost"] = f"${cost:.2f}"
    for d in envs:
        if isinstance(d.get("num_turns"), int):
            turns = (turns or 0) + d["num_turns"]
        if isinstance(d.get("duration_ms"), (int, float)):
            secs = (secs or 0) + d["duration_ms"] / 1000
        elif isinstance(d.get("duration_seconds"), (int, float)):
            secs = (secs or 0) + d["duration_seconds"]
        usage = d.get("usage") if isinstance(d.get("usage"), dict) else {}
        n = usage.get("total_tokens")
        if not isinstance(n, int) and any(isinstance(usage.get(k), int) for k in ("input_tokens", "output_tokens")):
            n = sum(usage.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                              "cache_creation_input_tokens") if isinstance(usage.get(k), int))
        if isinstance(n, int):
            tokens = (tokens or 0) + n
    if turns is not None:
        out["turns"] = turns
    if secs is not None:
        out["model_time"] = f"{secs:.0f}s"
    if tokens is not None:
        out["tokens"] = tokens
    return out


def envelope_cost(data):
    """The latest cost a result envelope, or a list of one process's result events, reports, at full precision
    (the process's running total; see envelope_stats); None when none reports one."""
    cost = None
    for d in (data if isinstance(data, list) else [data]):
        c = d.get("total_cost_usd", d.get("cost_usd")) if isinstance(d, dict) else None
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            cost = float(c)
    return cost


def _parse_json_envelope(raw_out, adapter):
    """Parse JSON envelope from stdout, tolerating leading status/banner lines.

    Tries one object at each line that starts with "{", last line first; raw_decode stops at the end of
    that object, so a long stream without a result event is scanned in linear time."""
    if not raw_out:
        return None

    def as_env(data):
        if isinstance(data, dict):
            _, env = adapter.parse_stream_event(data)
            if env is not None:
                return env
        return data if adapter.is_envelope(data) else None

    dec = json.JSONDecoder()
    starts, pos = [], 0
    for line in raw_out.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("{"):
            starts.append(pos + len(line) - len(stripped))
        pos += len(line)
    for off in reversed(starts):
        try:
            data, _ = dec.raw_decode(raw_out, off)
        except json.JSONDecodeError:
            continue
        env = as_env(data)
        if env is not None:
            return env
    return None


# "ASK: none", "ASK: n/a", "ASK: no question" is a worker saying it has nothing to ask (live run 2026-11-23).
_NO_QUESTION = re.compile(r"(?i)\s*(none|n/?a|no(ne| questions?)?|nothing)\b")


def _strip_marker_formatting(line):
    cleaned = line.strip().lstrip("`* \t")
    return re.sub(r"^(ASK|FAILED)[`*]+\s*:", r"\1:", cleaned)


def _as_pair(pair):
    return pair if isinstance(pair, Pair) else parse_pair(pair)


class _Paused(tuple):
    """A ('stopped', detail, '', conv_id) result from a worker stopped for silence whose conversation the stream
    showed (conv_id), so exec_prompt can resume it; why: the limit, in words; last: what the stream last showed it
    doing (or ""). Equal to the plain tuple."""

    def __new__(cls, result, why, last=""):
        obj = super().__new__(cls, result)
        obj.why, obj.last = why, last
        return obj


class ConversationLost(tuple):
    """A ('stopped', detail, '', '') result from exec_prompt when a worker stopped for silence could not be resumed
    in its conversation: that conversation is gone for later calls too (run_director reports LOST for a director
    kept open). Equal to the plain tuple."""


def _usage_line(errors):
    """The line of errors (error_text output) that reports a usage limit, or ""."""
    m = USAGE_RE.search(errors)
    if not m:
        return ""
    begin = errors.rfind("\n", 0, m.start()) + 1
    end = errors.find("\n", m.end())
    return _one_line(errors[begin:None if end < 0 else end], 800)


def _drain_chunks(stream, tag, out_q):
    """Without stream-json: pass stdout on as it arrives, not by line, so a result printed without a final newline
    by a CLI that then does not exit is still seen before the timeout."""
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while data := stream.buffer.read1(65536):
            out_q.put((tag, dec.decode(data)))
    finally:
        try:
            stream.close()
        except OSError:
            pass
        out_q.put(("eof", tag))


def _drain_stream(stream, tag, out_q):
    try:
        for line in stream:
            out_q.put((tag, line))
    finally:
        try:
            stream.close()
        except OSError:
            pass
        out_q.put(("eof", tag))


class WorkerRunner:
    """Run headless CLI workers synchronously with live `--output-format stream-json` progress logging."""

    def __init__(self, r):
        self.r = r

    def exe_for(self, harness):
        adapter = get_adapter(harness)
        return self.r.get(adapter.exe_state_key) or adapter.binary

    def _timeout(self, access="full", stream=True):
        """The run limit of a worker call: WORKER_TIMEOUT (state, then environment) for every worker; otherwise
        DEFAULT_TIMEOUT_FULL for workers that edit, DEFAULT_TIMEOUT_READ for read-only ones (the _NO_STREAM ones
        without stream-json, which has no silence limit). 0 or less: no limit (None)."""
        if stream:
            default = DEFAULT_TIMEOUT_READ if access == "read" else DEFAULT_TIMEOUT_FULL
        else:
            default = DEFAULT_TIMEOUT_READ_NO_STREAM if access == "read" else DEFAULT_TIMEOUT_FULL_NO_STREAM
        # AGY_TIMEOUT: the setting's old name, from when only agy workers ran; still read so existing state files
        # and environments keep their limit.
        raw = (
            self.r.get("WORKER_TIMEOUT")
            or self.r.get(LEGACY_TIMEOUT_KEY)
            or os.environ.get("WORKER_TIMEOUT")
            or os.environ.get(LEGACY_TIMEOUT_KEY)
        )
        if raw is None or str(raw).strip() == "":
            return default
        try:
            val = int(raw)
        except ValueError:
            return default
        return None if val <= 0 else val

    def _idle_timeout(self):
        """WORKER_IDLE_TIMEOUT (state, then environment), else DEFAULT_IDLE_TIMEOUT. 0 or less: no limit (None)."""
        raw = self.r.get("WORKER_IDLE_TIMEOUT") or os.environ.get("WORKER_IDLE_TIMEOUT")
        try:
            val = DEFAULT_IDLE_TIMEOUT if raw is None or str(raw).strip() == "" else float(raw)
        except ValueError:
            val = DEFAULT_IDLE_TIMEOUT
        return None if val <= 0 else val

    def _background_wait(self):
        """WORKER_BACKGROUND_WAIT (state, then environment), else BACKGROUND_WAIT. 0 or less: no bound (None)."""
        raw = self.r.get("WORKER_BACKGROUND_WAIT") or os.environ.get("WORKER_BACKGROUND_WAIT")
        try:
            val = BACKGROUND_WAIT if raw is None or str(raw).strip() == "" else float(raw)
        except ValueError:
            val = BACKGROUND_WAIT
        return None if val <= 0 else val

    def _heartbeat_interval(self):
        raw = self.r.get("WORKER_HEARTBEAT") or os.environ.get("WORKER_HEARTBEAT")
        if raw is None or str(raw).strip() == "":
            return DEFAULT_HEARTBEAT
        try:
            val = float(raw)
        except ValueError:
            return DEFAULT_HEARTBEAT
        return None if val <= 0 else val

    def _stream_enabled(self):
        raw = self.r.get("WORKER_STREAM")
        if raw is None or str(raw).strip() == "":
            raw = os.environ.get("WORKER_STREAM")
        if raw is None or str(raw).strip() == "":
            return True
        return str(raw).strip().lower() not in ("0", "false", "no", "off")

    def _open_worker_log(self, tag):
        run_dir = self.r.get("RUN_DIR")
        if not run_dir:
            return None, None
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(tag)).strip("-") or "worker"
        path = next_free(Path(run_dir) / "logs", f"{safe}-{{n}}.log")
        return path, path.open("w", encoding="utf-8")

    def _emit_progress(self, log_fh, line, to_stderr=True, milestone=False):
        """Write a worker line to its log; to_stderr lines also go to progress.log (milestone: and stderr)."""
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if log_fh is not None:
            log_fh.write(f"{stamp} {line}\n")
            log_fh.flush()
        if to_stderr:
            progress(self.r, line, milestone=milestone)

    def _turn_stats(self, adapter, conv_id, resumed, stats, cost=None):
        """For a harness that reports a resumed conversation's running totals, log this turn's share: subtract the
        totals recorded after the conversation's previous turn (<RUN_DIR>/logs/usage-so-far.json, by conversation;
        kept out of state.json, which every set rewrites whole; a missing or corrupt file counts as empty).
        cumulative_usage covers tokens, turns and model time; cumulative_cost the cost, given here at full precision
        (cost: the process's latest total_cost_usd, not the two-decimal text). A record is "tokens,turns,secs" with
        ",cost" after it when a cost was reported (readers of the three-field form ignore the fourth). A conversation
        whose previous turn left no cost in its record (it ran before costs were recorded) cannot be split: its
        first resumed turn logs the running total."""
        cost = cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
        if not conv_id or not self.r.get("RUN_DIR"):
            return
        if not (adapter.cumulative_usage or (getattr(adapter, "cumulative_cost", False) and cost is not None)):
            return
        path = Path(self.r.get("RUN_DIR")) / "logs" / "usage-so-far.json"
        try:
            totals = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            totals = {}
        totals = totals if isinstance(totals, dict) else {}
        now = {"tokens": stats.get("tokens"), "turns": stats.get("turns"),
               "secs": int(stats["model_time"][:-1]) if str(stats.get("model_time", "")).endswith("s") else None}
        saved = str(totals.get(conv_id, "")).split(",") if resumed else []
        prev = dict(zip(("tokens", "turns", "secs"), (int(x) if x.isdigit() else None for x in saved)))
        if adapter.cumulative_usage:
            for k, field in (("tokens", "tokens"), ("turns", "turns")):
                if isinstance(now[k], int) and isinstance(prev.get(k), int) and now[k] >= prev[k]:
                    stats[field] = now[k] - prev[k]
            if isinstance(now["secs"], int) and isinstance(prev.get("secs"), int) and now["secs"] >= prev["secs"]:
                stats["model_time"] = f"{now['secs'] - prev['secs']}s"
        if getattr(adapter, "cumulative_cost", False) and cost is not None and len(saved) > 3:
            try:
                prev_cost = float(saved[3])
            except ValueError:
                prev_cost = None
            if prev_cost is not None and 0 <= prev_cost <= cost:
                stats["cost"] = f"${cost - prev_cost:.2f}"
        totals[conv_id] = ",".join("" if now[k] is None else str(now[k]) for k in ("tokens", "turns", "secs")) + (
            "" if cost is None else f",{cost!r}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, json.dumps(totals))
        except OSError:
            pass

    def _forget_worker(self, entry):
        if entry is None:
            return
        if entry in _CHILDREN:
            _CHILDREN.remove(entry)
        if self.r.get("WORKER_PID") == str(entry[0].pid):
            self.r.set(WORKER_PID=None, WORKER_EXE=None)

    def _abandon_worker(self, entry, kids, log_fh, line):
        """Clean up after exec_prompt is unwound by an exception: stop the worker and what it started (kids: listed
        while it ran), close its log, and drop it from _CHILDREN; WORKER_PID is cleared only once it has exited,
        so reap_orphan_worker still finds one that could not be stopped. Never raises: the exception goes on."""
        proc = entry[0]
        try:
            if proc.poll() is None or kids:
                _kill_tree(proc.pid, earlier=kids, alive=proc.poll() is None)
                _reap(proc)
        except Exception:  # noqa: BLE001
            pass
        try:
            if entry in _CHILDREN:
                _CHILDREN.remove(entry)
            if proc.poll() is not None:
                self._forget_worker(entry)
        except Exception:  # noqa: BLE001
            pass
        try:
            # Like every other way a worker ends (timed out, stopped): in its log, progress.log and on stderr.
            self._emit_progress(log_fh, line, to_stderr=True, milestone=True)
        except Exception:  # noqa: BLE001
            pass
        if log_fh is not None and not log_fh.closed:
            log_fh.close()

    def _prepare_prompt(self, prompt_text, spec_path=None):
        if len(prompt_text.encode("utf-8")) <= MAX_INLINE_PROMPT_BYTES:
            return prompt_text
        if spec_path is not None and spec_path.is_file():
            target = spec_path.resolve()
        else:
            target = next_free(self.r.run_dir / "specs", "large-prompt-{n}.txt").resolve()
            target.write_text(prompt_text, encoding="utf-8")
        return f"Read and follow all instructions in {target} exactly."

    def launch(self, pair):
        """Model/effort CLI arguments for a pair, in its recorded launch style (or fallback)."""
        catalog = json.loads(Path(self.r.need("CATALOG")).read_text(encoding="utf-8"))
        parsed = _as_pair(pair)
        return launch_args(catalog, parsed, pick_style(catalog, self.r.s, parsed))

    def exec_prompt(self, prompt_text, args, worktree, conversation_id=None, spec_path=None, harness=None,
                    label=None, access="full", write_extra=(), pair_text="", mcp=False, resume_on_silence=True):
        """Invoke a headless worker and return (kind, detail_or_outcome, body, conversation_id).

        kind is one of:
          'done'          -> ('done', 'succeeded'|'failed', body, conv_id)
          'ask'           -> ('ask', conv_id, body, conv_id)
          'usage'         -> ('usage', detail, '', conv_id)
          'resume_failed' -> ('resume_failed', detail, '', '')
          'stopped'       -> ('stopped', detail, '', conv_id)

        WORKER_TIMEOUT bounds the whole call. A streaming worker that shows no turn activity for WORKER_IDLE_TIMEOUT
        is stopped; when the stream showed its conversation and resume_on_silence is set (not for probes), it is
        resumed once in that conversation with the message it was working on (SILENCE_RESUME_PROMPT) and what is
        left of WORKER_TIMEOUT. The caller sees the resumed call's result, or the first stop when the conversation
        cannot be resumed.

        harness (a key of harnesses.ADAPTERS) is required: there is no default CLI.
        """
        if harness is None:
            raise ValueError("exec_prompt needs a harness")
        total = self._timeout(access, self._stream_enabled())
        deadline = None if total is None else time.monotonic() + total
        launch = dict(args=args, worktree=worktree, harness=harness,
                      label=label or (spec_path.stem if spec_path else None), access=access, write_extra=write_extra,
                      pair_text=pair_text, mcp=mcp, total=total, deadline=deadline)
        res = self._exec_once(prompt_text, conversation_id=conversation_id, spec_path=spec_path,
                              may_pause=resume_on_silence, **launch)
        if not isinstance(res, _Paused):
            return res
        last = f" The last thing the run saw from you: {res.last}." if res.last else ""
        task = (RESUME_TASK_SPEC.format(spec=Path(spec_path).resolve()) if spec_path is not None
                and Path(spec_path).is_file() else RESUME_TASK_TEXT.format(prompt=prompt_text))
        resumed = self._exec_once(SILENCE_RESUME_PROMPT.format(why=res.why, last=last, task=task),
                                  conversation_id=res[3], **launch)
        if resumed[0] == "resume_failed":
            return ConversationLost(("stopped", f"{res[1]}; its conversation could not be resumed: "
                                                f"{_one_line(resumed[1], 300)}", "", ""))
        if resumed[0] == "stopped":
            return ("stopped", f"{resumed[1]} (after one resume: it was first stopped for {res.why})",
                    "", resumed[3] or res[3])
        return tuple(resumed)

    def _exec_once(
        self,
        prompt_text,
        args,
        worktree,
        conversation_id=None,
        spec_path=None,
        harness=None,
        label=None,
        _stream_override=None,
        access="full",
        write_extra=(),
        pair_text="",
        mcp=False,
        total=None,
        deadline=None,
        may_pause=False,
    ):
        """One worker call (see exec_prompt), stopped at deadline (WORKER_TIMEOUT, total seconds, counted from the
        start of exec_prompt). may_pause: a stop for silence in a conversation the stream showed is a _Paused, left
        for exec_prompt to resume, when at least MIN_RESUME_SECONDS are left before the deadline."""
        adapter = get_adapter(harness)
        exe = self.exe_for(harness)
        stream = self._stream_enabled() if _stream_override is None else bool(_stream_override)
        cli_prompt = self._prepare_prompt(prompt_text, spec_path=spec_path)
        write_dirs = ([str(Path(self.r.get("RUN_DIR")).resolve())] if self.r.get("RUN_DIR") else []) + list(write_extra)
        # Repos a read-only worker may name with `git -C`: its worktree, and in multi-repo runs every repo worktree.
        repo_wts = [self.r.get(f"{kind}_{repo_key(n)}") for n in self.r.repos for kind in ("IMPL_WT", "DOCS_WT")]
        git_dirs = [worktree] + [d for d in repo_wts if d]
        cmd = adapter.build_exec_cmd(exe, cli_prompt, args, conversation_id=conversation_id, stream=stream,
                                     access=access, write_dirs=write_dirs, git_dirs=git_dirs,
                                     workdir=worktree, mcp=mcp)
        idle = self._idle_timeout() if stream else None  # without stream-json there is no activity to see
        background_wait = self._background_wait()
        # The silence limit while an agy worker reports waiting on background commands with no result: the longer of
        # the silence limit and WORKER_BACKGROUND_WAIT, so a long build is not cut short, nor waited on forever.
        wait_limit = None if idle is None or background_wait is None else max(idle, background_wait)
        heartbeat = self._heartbeat_interval()
        tag = label or (spec_path.stem if spec_path else harness)
        log_path, log_fh = self._open_worker_log(tag)
        start = time.monotonic()

        stats = {"pair": pair_text} if pair_text else {}
        finished = []  # set by finish_log: every way out of a call that does not raise goes through it

        def finish_log(summary):
            finished.append(summary)
            self._forget_worker(entry)
            elapsed = time.monotonic() - start
            extra = "".join(f" {k}={v}" for k, v in stats.items())
            self._emit_progress(log_fh, f"[worker +{elapsed:.0f}s] {tag}: {summary}{extra}",
                                to_stderr=True, milestone=True)
            if log_fh is not None and not log_fh.closed:
                log_fh.close()

        entry = None

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=worktree,
                env={**worker_env(access), **adapter.extra_env},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as e:
            finish_log(f"launch failed: {e}")
            return ("stopped", f"failed to launch {exe}: {e}", "", "")
        entry = (proc, True)
        _CHILDREN.append(entry)
        # (pid, pgid) of what the worker ran below it, listed while it still ran (see _kill_tree).
        seen_kids = []
        try:
            if self.r.get("RUN_DIR"):
                self.r.set(WORKER_PID=proc.pid, WORKER_EXE=exe)

            log_hint = f" log={log_path}" if log_path else ""
            self._emit_progress(log_fh, f"[worker] start {tag} ({harness} pid={proc.pid}){log_hint}",
                                to_stderr=True, milestone=True)

            q = queue.Queue()
            t_out = threading.Thread(target=_drain_stream if stream else _drain_chunks, args=(proc.stdout, "out", q),
                                     daemon=True)
            t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, "err", q), daemon=True)
            t_out.start()
            t_err.start()

            # Per-run parser state: agy streams a message in pieces; the adapter also records there whether the latest
            # event belongs to a turn ("turn": the worker is at work) and how many background commands it reports
            # ("background").
            stream_acc = {}
            stdout_lines = collections.deque(maxlen=2000)  # bounded: a long stream's events are not all needed
            # (without stream-json, pieces as they arrived)
            stderr_lines = collections.deque(maxlen=2000)  # bounded the same way; error checks read its tail
            # (time read, "out"/"err", text) of recent output: a limit stop looks for a usage limit only in what came
            # after the worker's latest activity (an old, transient "429, retrying" it got past is not why it is
            # stopped), less RECENT_MARGIN: stdout and stderr are read apart, so a line printed just after the
            # activity may be read a moment before it.
            recent = collections.deque(maxlen=400)
            stream_envelope = None
            results = []  # every result event of this process, for its stats
            open_streams = {"out", "err"}
            last_activity = start  # any output, for the heartbeat line
            last_seen = ""  # the latest progress summary (what the worker was last seen doing), for a resume
            last_turn = start  # turn activity (the adapter's "active"), for the silence limit
            last_heartbeat = start
            timed_out = None  # why the worker is stopped by a limit: "deadline" or "idle"
            exited_at = None
            # The clocks after a result; both None while a turn runs. result_at: when the latest result came, with no
            # turn activity since (only then is the worker idle on that result). grace_at: where ENVELOPE_GRACE runs
            # from while no background command is reported: the result, or the moment the last one ended (a new turn
            # usually follows at once, and must not be cut).
            result_at = grace_at = None
            stopped_after_result = False  # we stop a worker that holds its result open; its exit code is ours

            def stop_at():
                """When the worker is stopped and its latest result used; None: not before the timeout."""
                if result_at is None:
                    return None
                if stream_acc.get("background"):
                    return None if background_wait is None else result_at + background_wait
                return grace_at + ENVELOPE_GRACE

            def limit_hit(now):
                """Which limit stops the worker now: "deadline" (WORKER_TIMEOUT), "idle" (no activity for
                silence_limit()), or None. None once the worker has exited by itself: its own result or error decides
                then."""
                if proc.poll() is not None:
                    return None
                if deadline is not None and now >= deadline:
                    return "deadline"
                lim = silence_limit()
                if lim is not None and now - last_turn >= lim:
                    return "idle"
                return None

            def silence_limit():
                """The silence limit now: none while a result is held (the post-result clocks apply), wait_limit while
                the worker reports waiting on background commands, else idle."""
                if result_at is not None:
                    return None
                return wait_limit if stream_acc.get("waiting") else idle

            def note_result(env_obj):
                """The latest result wins (a worker may start a new turn when a background command ends)."""
                nonlocal stream_envelope, result_at, grace_at
                stream_envelope = env_obj
                result_at = grace_at = time.monotonic()
                if proc.poll() is None:
                    seen_kids.extend(k for k in _descendants(proc.pid) if k not in seen_kids)

            while open_streams:
                now = time.monotonic()
                if exited_at is None and proc.poll() is not None:
                    exited_at = now
                if exited_at is not None and now - exited_at > 3:
                    # The worker exited but something it started still holds its output open; stop waiting.
                    break
                result_deadline = stop_at()
                if result_deadline is not None and exited_at is None and now >= result_deadline:
                    stopped_after_result = True
                    if stream_acc.get("background"):
                        self._emit_progress(log_fh, f"[worker +{now - start:.0f}s] {tag}: background commands still "
                                            f"running {background_wait:.0f}s after its result; stopping it",
                                            to_stderr=True)
                    break
                waits = [1.0]
                if result_deadline is not None:
                    waits.append(max(0.05, result_deadline - now))
                timed_out = limit_hit(now)
                if timed_out:
                    break
                if deadline is not None:
                    waits.append(max(0.05, deadline - now))
                if silence_limit() is not None:
                    waits.append(max(0.05, (last_turn + silence_limit()) - now))
                if heartbeat is not None:
                    rem_hb = (last_heartbeat + heartbeat) - now
                    waits.append(max(0.05, rem_hb))
                wait_s = min(waits)
                try:
                    kind_ev, payload = q.get(timeout=wait_s)
                except queue.Empty:
                    now = time.monotonic()
                    timed_out = limit_hit(now)
                    if timed_out:
                        break
                    if heartbeat is not None and (now - last_heartbeat) >= heartbeat:
                        elapsed = now - start
                        quiet = now - last_activity
                        self._emit_progress(
                            log_fh,
                            f"[worker +{elapsed:.0f}s] {tag}: still running (no output for {quiet:.0f}s)...",
                            to_stderr=True,
                        )
                        last_heartbeat = now
                    continue

                if kind_ev == "eof":
                    open_streams.discard(payload)
                    continue

                if kind_ev == "out":
                    stripped = payload.strip()
                    obj = None
                    if stream and stripped.startswith("{"):
                        try:
                            obj = json.loads(stripped)
                        except json.JSONDecodeError:
                            obj = None
                    stdout_lines.append(payload)  # error lines may be JSON too ({"error": {"code": 429, ...}})
                    recent.append((time.monotonic(), "out", payload))
                    if not stream and stream_envelope is None and stripped.endswith("}"):
                        # The one JSON result may be complete now: hold it, so a CLI that does not exit cannot lose it.
                        env_obj = _parse_json_envelope("".join(stdout_lines), adapter)
                        if env_obj is not None:
                            note_result(env_obj)
                    if isinstance(obj, dict):
                        background_before = stream_acc.get("background")
                        summary, env_obj = adapter.parse_stream_event(obj, stream_acc)
                        if stream_acc.pop("active", False) or env_obj is not None:
                            last_turn = time.monotonic()
                        if env_obj is not None:
                            note_result(env_obj)
                            results.append(env_obj)
                        elif stream_acc.pop("turn", False):
                            result_at = grace_at = None
                        elif result_at is not None and background_before and not stream_acc.get("background"):
                            grace_at = time.monotonic()
                        if summary:
                            now = time.monotonic()
                            last_activity = now
                            last_heartbeat = now
                            if not summary.startswith("init"):
                                last_seen = summary
                            self._emit_progress(log_fh, f"[worker +{now - start:.0f}s] {tag}: {summary}",
                                                to_stderr=True)
                elif kind_ev == "err":
                    stderr_lines.append(payload)
                    recent.append((time.monotonic(), "err", payload))
                    stripped = payload.strip()
                    if stripped:
                        now = time.monotonic()
                        last_activity = now
                        adapter.parse_stderr_line(stripped, stream_acc)  # "waiting": see silence_limit
                        self._emit_progress(
                            log_fh,
                            f"[worker +{now - start:.0f}s] {tag} stderr: {stripped}",
                            to_stderr=False,
                        )

            if timed_out and result_at is not None:
                # A result came in before the limit and the worker has been idle on it since: use it rather than
                # report a timeout. (A result followed by a new turn that ran into the limit is a timeout.)
                timed_out, stopped_after_result = False, proc.poll() is None
            if timed_out:
                # SIGTERM first, so the CLI can save its conversation (it may be resumed), then kill what is left.
                _stop_gently(proc, seen_kids)
                t_out.join(timeout=1)
                t_err.join(timeout=1)
                # What it printed while it was being stopped: for its stats, conversation and errors, not its result.
                while True:
                    try:
                        kind_ev, payload = q.get_nowait()
                    except queue.Empty:
                        break
                    if kind_ev in ("out", "err"):
                        recent.append((time.monotonic(), kind_ev, payload))
                    if kind_ev == "err":
                        stderr_lines.append(payload)
                    elif kind_ev == "out":
                        stdout_lines.append(payload)
                        try:
                            obj = json.loads(payload.strip()) if stream and payload.strip().startswith("{") else None
                        except json.JSONDecodeError:
                            obj = None
                        if isinstance(obj, dict):
                            env_obj = adapter.parse_stream_event(obj, stream_acc)[1]
                            if env_obj is not None:
                                results.append(env_obj)
                seen = stream_acc.get("conversation_id", "")
                if results:
                    # Interim results (a turn ended, then a new one ran into the limit) carry what was spent so far.
                    stats.update(envelope_stats(results))
                    self._turn_stats(adapter, seen or conversation_id, bool(conversation_id) and
                                     seen in ("", conversation_id), stats, cost=envelope_cost(results))
                since = [(kind, text) for t, kind, text in recent if t >= last_turn - RECENT_MARGIN]
                usage = _usage_line(error_text("".join(x for k, x in since if k == "out").splitlines(),
                                               "".join(x for k, x in since if k == "err")))
                if usage:
                    # Held up by a usage limit since its latest activity, not stuck: the caller's usage handling.
                    finish_log("usage limit")
                    return ("usage", usage, "", seen or conversation_id or "")
                if timed_out == "deadline":
                    finish_log(f"timed out after {total}s")
                    return ("stopped", f"{exe} timed out after {total}s", "", seen or conversation_id or "")
                # The conversation the stream showed: resumable only when it is the one asked for.
                if conversation_id and seen and seen != conversation_id:
                    finish_log("resume failed (new conversation started, then no activity)")
                    return ("resume_failed", f"conversation {conversation_id} was not resumed (started {seen})", "",
                            "")
                left = None if deadline is None else deadline - time.monotonic()
                if stream_acc.get("waiting"):
                    why = f"waiting {wait_limit:g}s on background commands that did not finish"
                else:
                    why = f"no activity for {idle:g}s"
                detail = f"{exe} was stopped: {why}"
                if may_pause and seen and (left is None or left >= MIN_RESUME_SECONDS):
                    finish_log(f"paused: {why}; resuming its conversation")
                    return _Paused(("stopped", detail, "", seen), why, last_seen)
                finish_log(f"timed out: {why}")
                return ("stopped", detail, "", seen or conversation_id or "")

            if not stopped_after_result and proc.poll() is None:
                seen_kids.extend(k for k in _descendants(proc.pid) if k not in seen_kids)
            try:
                proc.wait(timeout=0 if stopped_after_result else 5)
            except subprocess.TimeoutExpired:
                pass
            # Nothing the worker started may outlive it (dev servers, watchers holding its output open, background
            # commands): its process group, every process below it now, and what was below it while it ran (see
            # _kill_tree).
            _kill_tree(proc.pid, earlier=seen_kids, alive=proc.poll() is None)
            _reap(proc)
            t_out.join(timeout=1)
            t_err.join(timeout=1)

            raw_out = "".join(stdout_lines).strip()
            raw_err = "".join(stderr_lines).strip()
            data = stream_envelope if isinstance(stream_envelope, dict) else _parse_json_envelope(raw_out, adapter)

            if not isinstance(data, dict):
                combined = "\n".join(x for x in (raw_out, raw_err) if x).strip()
                errors = error_text("".join(stdout_lines).splitlines(), raw_err)
                if stream and _stream_override is None and STREAM_UNSUPPORTED_RE.search(combined):
                    finish_log("stream-json unsupported; retrying with --output-format json")
                    # No silence limit without stream-json: the run limit takes its tighter default (unless set).
                    total_ns = self._timeout(access, stream=False)
                    if total_ns is not None and (deadline is None or time.monotonic() + total_ns < deadline):
                        total, deadline = total_ns, time.monotonic() + total_ns
                    return self._exec_once(
                        prompt_text,
                        args,
                        worktree,
                        total=total,
                        deadline=deadline,
                        may_pause=may_pause,
                        conversation_id=conversation_id,
                        spec_path=spec_path,
                        harness=harness,
                        label=label,
                        _stream_override=False,
                        access=access,
                        write_extra=write_extra,
                        pair_text=pair_text,
                        mcp=mcp,
                    )
                if USAGE_RE.search(errors):
                    finish_log("usage limit")
                    return ("usage", errors[:800], "", "")
                if conversation_id and RESUME_ERR_RE.search(errors):
                    finish_log("resume failed")
                    return ("resume_failed", errors[:800], "", "")
                finish_log(f"stopped rc={proc.returncode}")
                return ("stopped", f"{exe} exited {proc.returncode}: {combined[:800]}", "", "")

            stats.update(envelope_stats(results or data))
            rc = 0 if stopped_after_result else proc.returncode
            env = adapter.extract_envelope(data, rc, exe, conversation_id=conversation_id, raw_err=raw_err)
            self._turn_stats(adapter, env["conv_id"], bool(conversation_id) and env["returned_id"] in ("", conversation_id),
                             stats, cost=envelope_cost(results or data))
            conv_id = env["conv_id"]

            if not env["ok"]:
                detail = env["detail"]
                if USAGE_RE.search(detail):
                    finish_log("usage limit")
                    return ("usage", detail, "", conv_id)
                if conversation_id and RESUME_ERR_RE.search(detail):
                    finish_log("resume failed")
                    return ("resume_failed", detail, "", "")
                finish_log(f"stopped: {detail[:120]}")
                return ("stopped", detail, "", conv_id)

            if conversation_id and env["returned_id"] and env["returned_id"] != conversation_id:
                finish_log("resume failed (new conversation started)")
                return (
                    "resume_failed",
                    f"conversation {conversation_id} was not resumed (started {env['returned_id']})",
                    "",
                    "",
                )

            resp = env["response"]
            final = stream_acc.get("final_response")
            if final and data is stream_envelope:
                # The result joins earlier messages to the final one (agy joins every agent message of the turn; see
                # AntigravityAdapter._final_message): the final message is the response, for the marker check and for
                # every first-line reader of the body.
                earlier = resp.strip()[:-len(final)].strip()
                self._emit_progress(log_fh, f"[worker +{time.monotonic() - start:.0f}s] {tag}: dropped earlier "
                                    f"messages before the final one: {_one_line(earlier, 120)}", to_stderr=True)
                resp = final + "\n"
            first = _strip_marker_formatting(first_nonempty_line(resp))
            if first.startswith("ASK:") and not _NO_QUESTION.match(first[4:]):
                finish_log("done (ask)")
                return ("ask", conv_id, resp, conv_id)
            if first.startswith("FAILED:"):
                finish_log("done (failed)")
                return ("done", "failed", resp, conv_id)
            finish_log("done (succeeded)")
            return ("done", "succeeded", resp, conv_id)
        finally:
            if not finished:
                # Unwound by an exception: Interrupted from the driver's signal handler (which has stopped the
                # worker already) or anything else. Stop what still runs and let go of the worker.
                self._abandon_worker(entry, seen_kids, log_fh,
                                     f"[worker +{time.monotonic() - start:.0f}s] {tag}: interrupted")

    def run(self, spec, pair=None, args=None, worktree=None, harness=None, label=None, access="full", write_extra=(),
            mcp=False, resume_on_silence=True):
        """access: "full" for workers that edit and commit (crew, flight director), "read" for the rest, which
        may write only under RUN_DIR and write_extra. mcp: keep the user's MCP servers (the flight director only).
        resume_on_silence: see exec_prompt (off for test launches, where a resume is no use)."""
        prompt_text, spec_path = _read_spec_or_text(spec)
        target_harness = harness or (_as_pair(pair).harness if pair else None)
        if target_harness is None:
            raise ValueError("run needs a pair or a harness")
        launch = list(args) if args is not None else self.launch(pair)
        return self.exec_prompt(prompt_text, launch, worktree, spec_path=spec_path, harness=target_harness, label=label,
                                access=access, write_extra=write_extra, pair_text=str(_as_pair(pair)) if pair else "",
                                mcp=mcp, resume_on_silence=resume_on_silence)

    def try_resume(self, spec_or_text, pair, worktree, conversation_id, label=None, access="full", write_extra=(),
                   mcp=False):
        if not conversation_id:
            return None
        prompt_text, spec_path = _read_spec_or_text(spec_or_text)
        target_harness = _as_pair(pair).harness
        res = self.exec_prompt(prompt_text, self.launch(pair), worktree, conversation_id=conversation_id,
                               spec_path=spec_path, harness=target_harness,
                               label=label or (None if spec_path else f"{target_harness}-resume"), access=access,
                               write_extra=write_extra, pair_text=str(_as_pair(pair)), mcp=mcp)
        if res[0] == "resume_failed" or isinstance(res, ConversationLost):
            # Not resumed (or resumed, went silent, and then could not be resumed again): the caller falls back to a
            # fresh worker, as for any conversation it cannot resume.
            return None
        return res


# ---------------------------------------------------------------- shared by run_task, run_design and run_finish

USAGE_REPLIES = ["retry", "pause", "stop"]
RETRY_PAUSE_STOP = ('Reply "retry" once the limit has reset, "pause" to leave the run exactly here and come back '
                    'later, or "stop" to end the run now (tasks not merged yet are not built).')


def usage_prompt(who, detail):
    """The fixed usage-limit prompt text (replies: USAGE_REPLIES)."""
    return f"{who} hit its model's usage limit: {detail}\n{RETRY_PAUSE_STOP}"


def paused(r, prompt_file):
    """The user paused at a usage-limit prompt: change nothing, so the next call shows the same prompt again."""
    r.log("paused at a usage-limit prompt; the next call shows it again")
    raise Done(PAUSED, f"PAUSED {prompt_file}")


def void_prompt(what, why):
    """The fixed prompt after three void reviewers in a row (replies: retry, stop)."""
    return (f"{what}: three reviewers in a row were void ({why}).\n"
            'Reply "retry" to start a new reviewer, or "stop" to stop the run.')


def write_prompt(r, text):
    """Save a fixed prompt to <RUN_DIR>/results/prompt-<n>.md and return its path."""
    f = next_free(r.run_dir / "results", "prompt-{n}.md")
    f.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    return f


def save_ask(r, name, body, header):
    """Save a worker's ASK: body to <RUN_DIR>/asks/<name>-<n>.md and return its path."""
    return save_body(body, next_free(r.run_dir / "asks", f"{name}-{{n}}.md"), header=header)


def read_answer(path):
    """The Flight Director's answer file after CONSULT, or Done(ERROR MISSING_FILE)."""
    p = Path(path)
    if not p.is_file():
        raise Done(ERROR, f"ERROR MISSING_FILE {p}")
    return p.read_text(encoding="utf-8")


def answer_worker(runner, answer, pair, worktree, conversation_id, spec, before_fresh=None, **launch):
    """Give a worker that asked a question (ASK:) the Flight Director's answer: resume its session, or, when
    that fails, run before_fresh() (put the tree back) and start a fresh worker on its spec plus the answer.
    launch: access / write_extra for the worker. Returns the worker result."""
    launch.setdefault("label", Path(spec).stem)  # the asking worker's role, for its log name and the cost report
    result = runner.try_resume(f"Flight Director answer:\n\n{answer}", pair, worktree, conversation_id, **launch)
    if result is None:
        if before_fresh:
            before_fresh()
        spec_text = Path(spec).read_text(encoding="utf-8")
        result = runner.run(f"{spec_text}\n\nFlight Director answer to your question:\n{answer}",
                            pair=pair, worktree=worktree, **launch)
    return result


def save_body(body_or_msg, out, header=None, append=False):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = body_or_msg["body"] if isinstance(body_or_msg, dict) else str(body_or_msg)
    text = (header + "\n" if header else "") + body.rstrip("\n") + "\n"
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
