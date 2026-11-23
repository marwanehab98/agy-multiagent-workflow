"""Plumbing shared by the run_*.py scripts: status codes, state file, headless CLI worker runner, helpers."""
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
from harnesses import _as_text, get_adapter, launch_args, pick_style  # noqa: E402
from pairs import Pair, parse_pair  # noqa: E402

QUEUE_EMPTY, WAIT, ESCALATE, FIX_META, CONSULT, PROMPT, STOP, ERROR = 0, 10, 20, 21, 22, 30, 40, 2

VERDICTS = ("VERDICT: PASS", "VERDICT: FIX")

USAGE_RE = re.compile(
    r"(?i)(usageLimitReached|usage\s*limit|rate[_\s]*limit|quota|resource_exhausted|\b429\b|"
    r"5-hour limit|overloaded_error|hit your limit|out of extra usage|credit balance is too low)"
)
RESUME_ERR_RE = re.compile(
    r"(?i)((conversation|session).*(not found|invalid|expired|does not exist|unknown|missing)|"
    r"no conversation found with session id|cannot resume|unknown (conversation|session))"
)


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
    """Write a file via temp file + rename, so a crash never leaves it half-written."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


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
        return self.need("CONVENTION_PATHS")

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


def _stop_child(proc, own_session):
    if proc.poll() is not None and not own_session:
        return
    try:
        if own_session:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if own_session:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass


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
        try:
            os.killpg(int(pid), signal.SIGKILL)
            note = f"stopped orphaned worker pid={pid} ({Path(exe).name}) left by an interrupted call"
        except (ProcessLookupError, PermissionError):
            pass
    r = Run(state_path)
    r.set(WORKER_PID=None, WORKER_EXE=None)
    if note and r.get("RUN_DIR"):
        r.log(note)
    return note


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
            reap_orphan_worker(state_path)
            res = body(events)
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


def worker_env(access, environ=None):
    env = dict(os.environ if environ is None else environ)
    if access != "read":
        return env
    keep = set(env.get("WORKER_ENV_KEEP", "").split())
    providers = [pat for switch, pat in _PROVIDER_ENV if str(env.get(switch, "")).strip().lower() not in ("", "0", "false")]
    return {k: v for k, v in env.items()
            if k in keep or _HARNESS_ENV.match(k) or any(p.match(k) for p in providers)
            or not _SECRET_ENV.search(k)}


MAX_INLINE_PROMPT_BYTES = 65536
DEFAULT_AGY_TIMEOUT = 1800
DEFAULT_HEARTBEAT = 30
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
    """Cost, turns and duration from a result envelope, when the harness reports them."""
    out = {}
    if not isinstance(data, dict):
        return out
    cost = data.get("total_cost_usd", data.get("cost_usd"))
    if isinstance(cost, (int, float)):
        out["cost"] = f"${cost:.2f}"
    if isinstance(data.get("num_turns"), int):
        out["turns"] = data["num_turns"]
    if isinstance(data.get("duration_ms"), (int, float)):
        out["model_time"] = f"{data['duration_ms'] / 1000:.0f}s"
    return out


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


class AgyRunner:
    """Run headless CLI workers synchronously with live `--output-format stream-json` progress logging."""

    def __init__(self, r):
        self.r = r

    def exe_for(self, harness):
        adapter = get_adapter(harness)
        return self.r.get(adapter.exe_state_key) or adapter.binary

    def _timeout(self):
        raw = (
            self.r.get("WORKER_TIMEOUT")
            or self.r.get("AGY_TIMEOUT")
            or os.environ.get("WORKER_TIMEOUT")
            or os.environ.get("AGY_TIMEOUT")
        )
        if raw is None or str(raw).strip() == "":
            return DEFAULT_AGY_TIMEOUT
        try:
            val = int(raw)
        except ValueError:
            return DEFAULT_AGY_TIMEOUT
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

    def _forget_worker(self, entry):
        if entry is None:
            return
        if entry in _CHILDREN:
            _CHILDREN.remove(entry)
        if self.r.get("WORKER_PID") == str(entry[0].pid):
            self.r.set(WORKER_PID=None, WORKER_EXE=None)

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

    def exec_prompt(
        self,
        prompt_text,
        args,
        worktree,
        conversation_id=None,
        spec_path=None,
        harness="antigravity",
        label=None,
        _stream_override=None,
        access="full",
        write_extra=(),
    ):
        """Invoke a headless worker and return (kind, detail_or_outcome, body, conversation_id).

        kind is one of:
          'done'          -> ('done', 'succeeded'|'failed', body, conv_id)
          'ask'           -> ('ask', conv_id, body, conv_id)
          'usage'         -> ('usage', detail, '', conv_id)
          'resume_failed' -> ('resume_failed', detail, '', '')
          'stopped'       -> ('stopped', detail, '', conv_id)
        """
        adapter = get_adapter(harness)
        exe = self.exe_for(harness)
        stream = self._stream_enabled() if _stream_override is None else bool(_stream_override)
        cli_prompt = self._prepare_prompt(prompt_text, spec_path=spec_path)
        write_dirs = ([str(Path(self.r.get("RUN_DIR")).resolve())] if self.r.get("RUN_DIR") else []) + list(write_extra)
        cmd = adapter.build_exec_cmd(exe, cli_prompt, args, conversation_id=conversation_id, stream=stream,
                                     access=access, write_dirs=write_dirs)
        timeout = self._timeout()
        heartbeat = self._heartbeat_interval()
        tag = label or (spec_path.stem if spec_path else harness)
        log_path, log_fh = self._open_worker_log(tag)
        start = time.monotonic()

        stats = {}

        def finish_log(summary):
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
                env=worker_env(access),
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
        if self.r.get("RUN_DIR"):
            self.r.set(WORKER_PID=proc.pid, WORKER_EXE=exe)

        log_hint = f" log={log_path}" if log_path else ""
        self._emit_progress(log_fh, f"[worker] start {tag} ({harness} pid={proc.pid}){log_hint}",
                            to_stderr=True, milestone=True)

        q = queue.Queue()
        t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, "out", q), daemon=True)
        t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, "err", q), daemon=True)
        t_out.start()
        t_err.start()

        stdout_lines = collections.deque(maxlen=2000)  # bounded: a long stream's events are not all needed
        stderr_lines = []
        stream_envelope = None
        open_streams = {"out", "err"}
        last_activity = start
        last_heartbeat = start
        timed_out = False
        exited_at = None

        while open_streams:
            now = time.monotonic()
            if exited_at is None and proc.poll() is not None:
                exited_at = now
            if exited_at is not None and now - exited_at > 3:
                # The worker exited but something it started still holds its output open; stop waiting.
                break
            waits = [1.0]
            if timeout is not None:
                rem = (start + timeout) - now
                if rem <= 0:
                    timed_out = True
                    break
                waits.append(rem)
            if heartbeat is not None:
                rem_hb = (last_heartbeat + heartbeat) - now
                waits.append(max(0.05, rem_hb))
            wait_s = min(waits)
            try:
                kind_ev, payload = q.get(timeout=wait_s)
            except queue.Empty:
                now = time.monotonic()
                if timeout is not None and (now - start) >= timeout:
                    timed_out = True
                    break
                if heartbeat is not None and (now - last_heartbeat) >= heartbeat:
                    elapsed = now - start
                    idle = now - last_activity
                    self._emit_progress(
                        log_fh,
                        f"[worker +{elapsed:.0f}s] {tag}: still running (no output for {idle:.0f}s)...",
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
                if isinstance(obj, dict):
                    summary, env_obj = adapter.parse_stream_event(obj)
                    if env_obj is not None:
                        stream_envelope = env_obj
                    if summary:
                        now = time.monotonic()
                        last_activity = now
                        last_heartbeat = now
                        self._emit_progress(log_fh, f"[worker +{now - start:.0f}s] {tag}: {summary}",
                                            to_stderr=True)
            elif kind_ev == "err":
                stderr_lines.append(payload)
                stripped = payload.strip()
                if stripped:
                    now = time.monotonic()
                    last_activity = now
                    self._emit_progress(
                        log_fh,
                        f"[worker +{now - start:.0f}s] {tag} stderr: {stripped}",
                        to_stderr=False,
                    )

        if timed_out:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            t_out.join(timeout=1)
            t_err.join(timeout=1)
            finish_log(f"timed out after {timeout}s")
            return ("stopped", f"{exe} timed out after {timeout}s", "", conversation_id or "")

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # Nothing the worker started may outlive it (dev servers, watchers holding its output open).
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=5)
        t_out.join(timeout=1)
        t_err.join(timeout=1)

        raw_out = "".join(stdout_lines).strip()
        raw_err = "".join(stderr_lines).strip()
        data = stream_envelope if isinstance(stream_envelope, dict) else _parse_json_envelope(raw_out, adapter)

        if not isinstance(data, dict):
            combined = "\n".join(x for x in (raw_out, raw_err) if x).strip()
            if stream and _stream_override is None and STREAM_UNSUPPORTED_RE.search(combined):
                finish_log("stream-json unsupported; retrying with --output-format json")
                return self.exec_prompt(
                    prompt_text,
                    args,
                    worktree,
                    conversation_id=conversation_id,
                    spec_path=spec_path,
                    harness=harness,
                    label=label,
                    _stream_override=False,
                    access=access,
                    write_extra=write_extra,
                )
            if USAGE_RE.search(combined):
                finish_log("usage limit")
                return ("usage", combined[:800], "", "")
            if conversation_id and RESUME_ERR_RE.search(combined):
                finish_log("resume failed")
                return ("resume_failed", combined[:800], "", "")
            finish_log(f"stopped rc={proc.returncode}")
            return ("stopped", f"{exe} exited {proc.returncode}: {combined[:800]}", "", "")

        stats.update(envelope_stats(data))
        env = adapter.extract_envelope(data, proc.returncode, exe, conversation_id=conversation_id, raw_err=raw_err)
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
        first = _strip_marker_formatting(first_nonempty_line(resp))
        if first.startswith("ASK:") and not _NO_QUESTION.match(first[4:]):
            finish_log("done (ask)")
            return ("ask", conv_id, resp, conv_id)
        if first.startswith("FAILED:"):
            finish_log("done (failed)")
            return ("done", "failed", resp, conv_id)
        finish_log("done (succeeded)")
        return ("done", "succeeded", resp, conv_id)

    def run(self, spec, pair=None, args=None, worktree=None, harness=None, label=None, access="full", write_extra=()):
        """access: "full" for workers that edit and commit (crew, flight director), "read" for the rest, which
        may write only under RUN_DIR and write_extra."""
        prompt_text, spec_path = _read_spec_or_text(spec)
        target_harness = harness or (_as_pair(pair).harness if pair else "antigravity")
        launch = list(args) if args is not None else self.launch(pair)
        return self.exec_prompt(prompt_text, launch, worktree, spec_path=spec_path, harness=target_harness, label=label,
                                access=access, write_extra=write_extra)

    def try_resume(self, spec_or_text, pair, worktree, conversation_id, label=None, access="full", write_extra=()):
        if not conversation_id:
            return None
        prompt_text, spec_path = _read_spec_or_text(spec_or_text)
        target_harness = _as_pair(pair).harness
        res = self.exec_prompt(prompt_text, self.launch(pair), worktree, conversation_id=conversation_id,
                               spec_path=spec_path, harness=target_harness, label=label, access=access,
                               write_extra=write_extra)
        if res[0] == "resume_failed":
            return None
        return res


WorkerRunner = AgyRunner


# ---------------------------------------------------------------- shared by run_task, run_design and run_finish

RETRY_OR_STOP = 'Reply "retry" once the limit has reset, or "stop" to stop the run.'


def usage_prompt(who, detail):
    """The fixed usage-limit prompt text (replies: retry, stop)."""
    return f"{who} hit its model's usage limit: {detail}\n{RETRY_OR_STOP}"


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
