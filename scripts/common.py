"""Plumbing shared by the run_*.py scripts: status codes, state file, headless `agy -p` runner, helpers."""
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I drops the script dir from sys.path
from harnesses import launch_args, pick_style  # noqa: E402
from pairs import parse_pair  # noqa: E402

QUEUE_EMPTY, WAIT, ESCALATE, FIX_META, CONSULT, PROMPT, STOP, ERROR = 0, 10, 20, 21, 22, 30, 40, 2

VERDICTS = ("VERDICT: PASS", "VERDICT: FIX")

USAGE_RE = re.compile(r"(?i)(usageLimitReached|usage\s*limit|rate\s*limit|quota|resource_exhausted|\b429\b|5-hour limit)")
RESUME_ERR_RE = re.compile(
    r"(?i)(conversation.*(not found|invalid|expired|does not exist|unknown|missing)|cannot resume|unknown conversation)"
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
    """The words of an architect's RESULT line, without what it may add around the line itself.

    Architects sometimes wrap the line in backticks or bold, end it with a period, or carry on with
    prose on the same line ("RESULT: RUN_VALUES_FIXED. The baseline failed because ..."; live test
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


# ---------------------------------------------------------------- headless agy runner

MAX_INLINE_PROMPT_BYTES = 65536
DEFAULT_AGY_TIMEOUT = 1800


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


ENVELOPE_PAYLOAD_KEYS = {"response", "error", "conversation_id"}


def _is_envelope(data):
    return (
        isinstance(data, dict)
        and isinstance(data.get("status"), str)
        and bool(ENVELOPE_PAYLOAD_KEYS & data.keys())
    )


def _parse_json_envelope(raw_out):
    """Parse JSON envelope from stdout, tolerating leading status/banner lines."""
    if not raw_out:
        return None
    try:
        data = json.loads(raw_out)
        return data if _is_envelope(data) else None
    except json.JSONDecodeError:
        pass
    lines = raw_out.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("{"):
            candidate = "\n".join(lines[i:])
            try:
                data = json.loads(candidate)
                if _is_envelope(data):
                    return data
            except json.JSONDecodeError:
                continue
    return None


def _strip_marker_formatting(line):
    cleaned = line.strip().lstrip("`* \t")
    return re.sub(r"^(ASK|FAILED)[`*]+\s*:", r"\1:", cleaned)


def _as_text(val):
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


class AgyRunner:
    """Run headless `agy -p` workers synchronously with `--output-format json`."""

    def __init__(self, r):
        self.r = r
        self.exe = r.get("AGY") or "agy"

    def _timeout(self):
        raw = self.r.get("AGY_TIMEOUT") or os.environ.get("AGY_TIMEOUT")
        if raw is None or str(raw).strip() == "":
            return DEFAULT_AGY_TIMEOUT
        try:
            val = int(raw)
        except ValueError:
            return DEFAULT_AGY_TIMEOUT
        return None if val <= 0 else val

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
        parsed = parse_pair(pair)
        return launch_args(catalog, parsed, pick_style(catalog, self.r.s, parsed))

    def exec_prompt(self, prompt_text, args, worktree, conversation_id=None, spec_path=None):
        """Invoke `agy -p` and return (kind, detail_or_outcome, body, conversation_id).

        kind is one of:
          'done'          -> ('done', 'succeeded'|'failed', body, conv_id)
          'ask'           -> ('ask', conv_id, body, conv_id)
          'usage'         -> ('usage', detail, '', conv_id)
          'resume_failed' -> ('resume_failed', detail, '', '')
          'stopped'       -> ('stopped', detail, '', conv_id)
        """
        cli_prompt = self._prepare_prompt(prompt_text, spec_path=spec_path)
        cmd = [
            self.exe,
            "-p", cli_prompt,
            "--output-format", "json",
            "--dangerously-skip-permissions",
            *args,
        ]
        if conversation_id:
            cmd += ["--conversation", conversation_id]
        timeout = self._timeout()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=worktree,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                start_new_session=True,
            )
            try:
                stdout_s, stderr_s = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return ("stopped", f"{self.exe} timed out after {timeout}s", "", conversation_id or "")
        except OSError as e:
            return ("stopped", f"failed to launch {self.exe}: {e}", "", "")

        raw_out = (stdout_s or "").strip()
        raw_err = (stderr_s or "").strip()
        data = _parse_json_envelope(raw_out)

        if not isinstance(data, dict):
            combined = "\n".join(x for x in (raw_out, raw_err) if x).strip()
            if USAGE_RE.search(combined):
                return ("usage", combined[:800], "", "")
            if conversation_id and RESUME_ERR_RE.search(combined):
                return ("resume_failed", combined[:800], "", "")
            return ("stopped", f"{self.exe} exited {proc.returncode}: {combined[:800]}", "", "")

        returned_conv_id = _as_text(data.get("conversation_id")).strip()
        conv_id = returned_conv_id or conversation_id or ""
        status = _as_text(data.get("status")).strip()
        resp = _as_text(data.get("response"))
        err = _as_text(data.get("error"))

        if status != "SUCCESS" or proc.returncode != 0:
            detail = (err or resp or raw_err or f"{self.exe} exited {proc.returncode} (status={status})").strip()
            if USAGE_RE.search(detail):
                return ("usage", detail, "", conv_id)
            if conversation_id and RESUME_ERR_RE.search(detail):
                return ("resume_failed", detail, "", "")
            return ("stopped", detail, "", conv_id)

        if conversation_id and returned_conv_id and returned_conv_id != conversation_id:
            return (
                "resume_failed",
                f"conversation {conversation_id} was not resumed (started {returned_conv_id})",
                "",
                "",
            )

        first = _strip_marker_formatting(first_nonempty_line(resp))
        if first.startswith("ASK:"):
            return ("ask", conv_id, resp, conv_id)
        if first.startswith("FAILED:"):
            return ("done", "failed", resp, conv_id)
        return ("done", "succeeded", resp, conv_id)

    def run(self, spec, pair=None, args=None, worktree=None):
        prompt_text, spec_path = _read_spec_or_text(spec)
        launch = list(args) if args is not None else self.launch(pair)
        return self.exec_prompt(prompt_text, launch, worktree, spec_path=spec_path)

    def try_resume(self, spec_or_text, pair, worktree, conversation_id):
        if not conversation_id:
            return None
        prompt_text, spec_path = _read_spec_or_text(spec_or_text)
        res = self.exec_prompt(prompt_text, self.launch(pair), worktree, conversation_id=conversation_id,
                               spec_path=spec_path)
        if res[0] == "resume_failed":
            return None
        return res


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
