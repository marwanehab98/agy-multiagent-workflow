"""Plumbing shared by the run_*.py scripts: status codes, state file, headless `agy -p` runner, helpers."""
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
    """The reviewer's file when it ends in a VERDICT line; otherwise the settle text/file, copied there."""
    review = Path(review)
    if review.is_file() and last_line(review) in VERDICTS:
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


# ---------------------------------------------------------------- headless agy runner

class AgyRunner:
    """Run headless `agy -p` workers synchronously with `--output-format json`."""

    def __init__(self, r):
        self.r = r
        self.exe = r.get("AGY") or "agy"

    def launch(self, pair):
        """Model/effort CLI arguments for a pair, in its recorded launch style (or fallback)."""
        catalog = json.loads(Path(self.r.need("CATALOG")).read_text(encoding="utf-8"))
        parsed = parse_pair(pair)
        return launch_args(catalog, parsed, pick_style(catalog, self.r.s, parsed))

    def exec_prompt(self, prompt_text, args, worktree, conversation_id=None):
        """Invoke `agy -p` and return (kind, detail_or_outcome, body, conversation_id).

        kind is one of:
          'done'          -> ('done', 'succeeded'|'failed', body, conv_id)
          'ask'           -> ('ask', conv_id, body, conv_id)
          'usage'         -> ('usage', detail, '', conv_id)
          'resume_failed' -> ('resume_failed', detail, '', '')
          'stopped'       -> ('stopped', detail, '', conv_id)
        """
        cmd = [
            self.exe,
            "-p", prompt_text,
            "--output-format", "json",
            "--dangerously-skip-permissions",
            *args,
        ]
        if conversation_id:
            cmd += ["--conversation", conversation_id]
        try:
            proc = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, errors="replace")
        except OSError as e:
            return ("stopped", f"failed to launch {self.exe}: {e}", "", "")

        raw_out = (proc.stdout or "").strip()
        raw_err = (proc.stderr or "").strip()
        try:
            data = json.loads(raw_out) if raw_out else None
        except json.JSONDecodeError:
            data = None

        if not isinstance(data, dict):
            combined = "\n".join(x for x in (raw_out, raw_err) if x).strip()
            if USAGE_RE.search(combined):
                return ("usage", combined[:800], "", "")
            if conversation_id and RESUME_ERR_RE.search(combined):
                return ("resume_failed", combined[:800], "", "")
            return ("stopped", f"{self.exe} exited {proc.returncode}: {combined[:800]}", "", "")

        conv_id = data.get("conversation_id") or conversation_id or ""
        status = data.get("status", "")
        resp = data.get("response") or ""
        err = data.get("error") or ""

        if status != "SUCCESS" or proc.returncode != 0:
            detail = (err or resp or raw_err or f"{self.exe} exited {proc.returncode} (status={status})").strip()
            if USAGE_RE.search(detail):
                return ("usage", detail, "", conv_id)
            if conversation_id and RESUME_ERR_RE.search(detail):
                return ("resume_failed", detail, "", "")
            return ("stopped", detail, "", conv_id)

        first = first_nonempty_line(resp)
        if first.startswith("ASK:"):
            return ("ask", conv_id, resp, conv_id)
        if first.startswith("FAILED:"):
            return ("done", "failed", resp, conv_id)
        return ("done", "succeeded", resp, conv_id)

    def run(self, spec, pair=None, args=None, worktree=None):
        prompt_text = Path(spec).read_text(encoding="utf-8") if isinstance(spec, (Path, str)) and Path(spec).is_file() else str(spec)
        launch = list(args) if args is not None else self.launch(pair)
        return self.exec_prompt(prompt_text, launch, worktree)

    def try_resume(self, spec_or_text, pair, worktree, conversation_id):
        if not conversation_id:
            return None
        prompt_text = (
            Path(spec_or_text).read_text(encoding="utf-8")
            if isinstance(spec_or_text, Path) or (isinstance(spec_or_text, str) and "\n" not in spec_or_text and Path(spec_or_text).is_file())
            else str(spec_or_text)
        )
        res = self.exec_prompt(prompt_text, self.launch(pair), worktree, conversation_id=conversation_id)
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
