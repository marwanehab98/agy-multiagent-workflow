"""Plumbing shared by the run_*.py scripts: status codes, state file, headless CLI worker runner, helpers."""
import datetime
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


def result_words(text):
    """The words of a flight director's RESULT line, without what it may add around the line itself.

    Flight directors sometimes wrap the line in backticks or bold, end it with a period, or carry on with
    prose on the same line ("RESULT: FLIGHT_MANIFEST_FIXED. The baseline failed because ..."; live test
    2026-11-15). Take the first non-empty line from "RESULT:", cut it at the first sentence break
    (". ", "; ", " - ", a backtick), and drop trailing punctuation from each argument.
    """
    line = next((l for l in text.splitlines() if l.strip()), "")
    m = re.search(r"RESULT:.*", line)
    line = (m.group(0) if m else line.strip()).replace("*", "")
    line = re.split(r"[.;]\s|\s[-\u2013\u2014]+\s|`", line, maxsplit=1)[0]
    words = line.split()
    return words[:1] + [w.rstrip(".,;:") for w in words[1:]]


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


# ---------------------------------------------------------------- state and helpers

_STATE_ALIASES = {
    "FLIGHT_MANIFEST": "RUN_VALUES",
    "GROUND_CREW_PAIR": "CLERICAL_PAIR",
}


class Run:
    def __init__(self, path):
        self.path = Path(path)
        self.s = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key, default=None):
        if key in self.s:
            return self.s[key]
        legacy = _STATE_ALIASES.get(key)
        if legacy and legacy in self.s:
            return self.s[legacy]
        return default

    def need(self, key):
        if key in self.s:
            return self.s[key]
        legacy = _STATE_ALIASES.get(key)
        if legacy and legacy in self.s:
            return self.s[legacy]
        raise Done(ERROR, f"ERROR MISSING_STATE {key}")

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
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (self.run_dir / "mission-log.md").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {event}\n")


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


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")


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


def _parse_json_envelope(raw_out, adapter):
    """Parse JSON envelope from stdout, tolerating leading status/banner lines."""
    if not raw_out:
        return None
    try:
        data = json.loads(raw_out)
        if isinstance(data, dict):
            _, env = adapter.parse_stream_event(data)
            if env is not None:
                return env
        return data if adapter.is_envelope(data) else None
    except json.JSONDecodeError:
        pass
    lines = raw_out.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("{"):
            candidate = "\n".join(lines[i:])
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    _, env = adapter.parse_stream_event(data)
                    if env is not None:
                        return env
                if adapter.is_envelope(data):
                    return data
            except json.JSONDecodeError:
                continue
    return None


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
        self.exe = self.exe_for("antigravity")

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

    def _emit_progress(self, log_fh, line, to_stderr=True):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if log_fh is not None:
            log_fh.write(f"{stamp} {line}\n")
            log_fh.flush()
        if to_stderr:
            sys.stderr.write(f"{line}\n")
            sys.stderr.flush()

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
        cmd = adapter.build_exec_cmd(exe, cli_prompt, args, conversation_id=conversation_id, stream=stream)
        timeout = self._timeout()
        heartbeat = self._heartbeat_interval()
        tag = label or (spec_path.stem if spec_path else harness)
        log_path, log_fh = self._open_worker_log(tag)
        start = time.monotonic()

        def finish_log(summary):
            if log_fh is not None:
                elapsed = time.monotonic() - start
                self._emit_progress(log_fh, f"[worker +{elapsed:.0f}s] {tag}: {summary}", to_stderr=False)
                log_fh.close()

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=worktree,
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

        log_hint = f" log={log_path}" if log_path else ""
        self._emit_progress(log_fh, f"[worker] start {tag} ({harness} pid={proc.pid}){log_hint}", to_stderr=True)

        q = queue.Queue()
        t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, "out", q), daemon=True)
        t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, "err", q), daemon=True)
        t_out.start()
        t_err.start()

        stdout_lines = []
        stderr_lines = []
        stream_envelope = None
        open_streams = {"out", "err"}
        last_activity = start
        last_heartbeat = start
        timed_out = False

        while open_streams:
            now = time.monotonic()
            waits = []
            if timeout is not None:
                rem = (start + timeout) - now
                if rem <= 0:
                    timed_out = True
                    break
                waits.append(rem)
            if heartbeat is not None:
                rem_hb = (last_heartbeat + heartbeat) - now
                waits.append(max(0.05, rem_hb))
            wait_s = min(waits) if waits else 1.0
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
                stdout_lines.append(payload)
                stripped = payload.strip()
                if stripped.startswith("{"):
                    try:
                        obj = json.loads(stripped)
                    except json.JSONDecodeError:
                        obj = None
                    if isinstance(obj, dict):
                        progress, env_obj = adapter.parse_stream_event(obj)
                        if env_obj is not None:
                            stream_envelope = env_obj
                        if progress:
                            now = time.monotonic()
                            last_activity = now
                            last_heartbeat = now
                            self._emit_progress(
                                log_fh,
                                f"[worker +{now - start:.0f}s] {tag}: {progress}",
                                to_stderr=True,
                            )
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
            elapsed = time.monotonic() - start
            self._emit_progress(log_fh, f"[worker +{elapsed:.0f}s] {tag}: timed out after {timeout}s", to_stderr=True)
            if log_fh is not None:
                log_fh.close()
            return ("stopped", f"{exe} timed out after {timeout}s", "", conversation_id or "")

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
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
                )
            if USAGE_RE.search(combined):
                finish_log("usage limit")
                return ("usage", combined[:800], "", "")
            if conversation_id and RESUME_ERR_RE.search(combined):
                finish_log("resume failed")
                return ("resume_failed", combined[:800], "", "")
            finish_log(f"stopped rc={proc.returncode}")
            return ("stopped", f"{exe} exited {proc.returncode}: {combined[:800]}", "", "")

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
        if first.startswith("ASK:"):
            finish_log("done (ask)")
            return ("ask", conv_id, resp, conv_id)
        if first.startswith("FAILED:"):
            finish_log("done (failed)")
            return ("done", "failed", resp, conv_id)
        finish_log("done (succeeded)")
        return ("done", "succeeded", resp, conv_id)

    def run(self, spec, pair=None, args=None, worktree=None, harness=None, label=None):
        prompt_text, spec_path = _read_spec_or_text(spec)
        target_harness = harness or (_as_pair(pair).harness if pair else "antigravity")
        launch = list(args) if args is not None else self.launch(pair)
        return self.exec_prompt(prompt_text, launch, worktree, spec_path=spec_path, harness=target_harness, label=label)

    def try_resume(self, spec_or_text, pair, worktree, conversation_id, label=None):
        if not conversation_id:
            return None
        prompt_text, spec_path = _read_spec_or_text(spec_or_text)
        target_harness = _as_pair(pair).harness
        res = self.exec_prompt(prompt_text, self.launch(pair), worktree, conversation_id=conversation_id,
                               spec_path=spec_path, harness=target_harness, label=label)
        if res[0] == "resume_failed":
            return None
        return res


WorkerRunner = AgyRunner


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
