"""Harness adapters for Antigravity (`agy` CLI) and Claude Code (`claude` CLI).

Usage:
  python3 -I harnesses.py detect --out <file> [--fixtures DIR --home DIR]
  python3 -I harnesses.py args (--state <state.json> | --catalog <file>) --pair <pair>   # CLI model/effort args
  python3 -I harnesses.py skill-dir --catalog <file> --harness H --skill NAME
  python3 -I harnesses.py top-effort --catalog <file> --harness H --model M
  python3 -I harnesses.py install-dirs [--fixtures DIR --home DIR]   # name<TAB>skills folder<TAB>found|missing

`detect` runs harness CLI commands and writes a catalog:
  {"detected_at": iso, "harnesses": {name: {binary, version, styles, models,
   skills: {name: folder}, plugins: [enabled], mcp: {name: ok}, broken: null|str, read_sandbox: bool,
   skill_dirs: [folder], plugin_cmd: str, mcp_cmd: str}}}
skill_dirs, plugin_cmd and mcp_cmd say where a missing requirement belongs (roles.py check prints them).
A harness whose binary is missing is left out; one whose model list parses empty is
recorded with `broken` set to the start of the raw output.
`args --state` reads CATALOG and the LAUNCH_STYLE_ keys from a run's state file; `args --catalog` (before
any run exists) uses the harness's first launch style.
Exit 0 ok. Exit 1 not found (skill-dir, top-effort) or bad input. Exit 64 bad usage.
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from pairs import EFFORTS, parse_pair, pair_key  # noqa: E402


def real_runner(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    # Some CLIs print help to stderr and exit 0 (agy --help): use stderr when stdout is empty.
    return r.returncode, r.stdout + (r.stderr if r.returncode or not r.stdout.strip() else "")


FIXTURE_FILES = {
    ("agy", "models"): "agy_models.txt",
    ("agy", "plugin", "list"): "agy_plugin_list.txt",
    ("agy", "mcp", "list"): "agy_mcp_list.txt",
    ("agy", "--version"): "agy_version.txt",
    ("agy", "--help"): "agy_help.txt",
    ("claude", "--version"): "claude_version.txt",
    ("claude", "--help"): "claude_help.txt",
    ("claude", "plugin", "list"): "claude_plugin_list.txt",
    ("claude", "mcp", "list"): "claude_mcp_list.txt",
}


def _as_text(val):
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


def fixture_runner(fixtures, overrides=None):
    """Map each command to a captured file; a missing file means command not found (rc 127)."""
    names = {**FIXTURE_FILES, **(overrides or {})}
    def run(cmd):
        name = names.get(tuple(cmd))
        path = Path(fixtures) / name if name else None
        if not path or not path.is_file():
            return 127, f"{cmd[0]}: command not found"
        return 0, path.read_text(encoding="utf-8")
    run.fixtures = True
    return run


def _summarize_tool_args(info, limit=80):
    """Return a short single-line summary of a tool call's arguments, or ''."""
    if isinstance(info, str):
        s = " ".join(info.split())
        return (s[: limit - 3] + "...") if len(s) > limit else s
    if not isinstance(info, dict):
        return ""
    for key in ("command", "CommandLine", "file_path", "TargetFile", "AbsolutePath", "Path", "path", "pattern", "query",
                "url", "description"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            s = " ".join(val.split())
            return (s[: limit - 3] + "...") if len(s) > limit else s
    return ""


# Git subcommands a read-only Claude worker may run (reviewers read the task's diff and history). Not grep: its
# -O<pager> runs a program even bundled behind other flags (`git grep -nO<cmd>`, verified live), which no rule
# pattern can single out; the Grep tool searches the tree.
READ_ONLY_GIT = ["diff", "log", "show", "status", "rev-parse", "ls-files", "blame"]


def _one_line(text, limit):
    s = " ".join(_as_text(text).split())
    return (s[: limit - 3] + "...") if len(s) > limit else s


def _block_text(content):
    """Plain text of a tool_result's content (a string, or a list of {type: text, text} blocks)."""
    if isinstance(content, list):
        return " ".join(_as_text(c.get("text")) if isinstance(c, dict) else _as_text(c) for c in content)
    return _as_text(content)


def _todo_summary(info):
    """'todo 2/5 done, now: <the in-progress item>' from a TodoWrite call's input."""
    todos = info.get("todos") if isinstance(info, dict) else None
    if not isinstance(todos, list) or not todos:
        return "todo updated"
    done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
    now = next((t.get("activeForm") or t.get("content") for t in todos
                if isinstance(t, dict) and t.get("status") == "in_progress"), "")
    return f"todo {done}/{len(todos)} done" + (f", now: {_one_line(now, 80)}" if now else "")


# Characters a path in a Claude permission rule must not contain: "(" or ")" could end the rule early inside the
# comma-joined --allowedTools value and turn what follows into a rule of its own (a bare "Bash"); "*", "?", "[" and
# "]" are wildcards in rule patterns, so the rule would cover other paths; "\" escapes, and control characters
# (newline, tab) split the value. Commas and spaces inside the parentheses are kept (verified live 2026-11-24, 2.1.331).
_RULE_UNSAFE = re.compile(r"[()*?\[\]\\\x00-\x1f\x7f]")


def _shell_spellings(path):
    """The ways a worker writes path in a shell command that a Claude Code Bash rule can name. Rules match the
    command's literal text (verified live 2026-11-24, 2.1.331): `git -C "/a b/r" log` matches only a rule written
    with the same double quotes, `git -C '/a b/r' log` only one with single quotes, and a backslash-escaped
    `/a\\ b/r` matches no rule at all. So every path gets its double- and single-quoted forms (each only when the
    shell would take it literally: no " $ ` ! inside double quotes, no ' inside single quotes), and one that needs
    no quoting also its bare form, first: a worker may quote a plain path too (`git -C "/repo" diff`). A path with a
    _RULE_UNSAFE character gets none: such a worker cannot use `git -C` on it (backslash-escaped spellings are never
    allowed either)."""
    if _RULE_UNSAFE.search(path):
        return []
    return (([path] if shlex.quote(path) == path else [])
            + ([f'"{path}"'] if not re.search(r'["$`!]', path) else []) + ([f"'{path}'"] if "'" not in path else []))


def _git_dir_spellings(d, workdir):
    """How a worker may name git dir d after `git -C`: as given and resolved, and, when d is the worker's folder or
    lies under it, relative to that folder (`.`, `api`, `./api`). A relative d is taken from workdir, never from
    this process's cwd."""
    base = Path(workdir).resolve() if workdir else None
    p = Path(d)
    if not p.is_absolute():
        if base is None:
            return []
        p = base / p
    full = p.resolve()
    forms = [str(d)] if Path(d).is_absolute() else []
    forms.append(str(full))
    if base is not None and full.is_relative_to(base):
        rel = full.relative_to(base).as_posix()
        forms += ["."] if rel == "." else [rel, f"./{rel}"]
    return forms


class Adapter:
    name = binary = ""
    # True when a resumed conversation's result reports the whole conversation's usage so far, not the turn's.
    cumulative_usage = False
    # True when a resumed conversation's result reports the whole conversation's cost so far (total_cost_usd), not
    # the turn's; its tokens, turns and time may still be the turn's own (cumulative_usage).
    cumulative_cost = False
    # Environment variables every worker of this harness gets on top of worker_env (they win over the user's).
    extra_env = {}
    styles = []
    version_cmd = ["--version"]
    exe_state_key = ""
    resume_flag = ""
    json_output_args = ["--output-format", "json"]
    stream_output_args = ["--output-format", "stream-json"]

    def __init__(self, runner, home, env=None):
        self.runner, self.home = runner, Path(home)
        self.env = os.environ if env is None else env
        self._cache = {}

    def run(self, *args):
        if args not in self._cache:
            self._cache[args] = self.runner([self.binary, *args])
        return self._cache[args]

    def find(self):
        if not getattr(self.runner, "fixtures", False) and not shutil.which(self.binary):
            return None
        rc, out = self.run(*self.version_cmd)
        if rc != 0:
            return None
        m = re.search(r"\d+(?:\.\d+)+", out)
        return {"binary": self.binary, "version": m.group(0) if m else out.strip()}

    def models(self):
        raise NotImplementedError

    def raw_models(self):
        return self.run(*self.models_cmd)[1]

    def plugins(self):
        raise NotImplementedError

    def mcp(self):
        raise NotImplementedError

    def user_skill_dirs(self):
        """The folders a user puts skills in by hand (plugin skill folders come on top in skill_roots)."""
        raise NotImplementedError

    def skill_roots(self):
        raise NotImplementedError

    def skills(self):
        found = {}
        for root in self.skill_roots():
            for f in sorted(root.glob("*/SKILL.md")):
                found.setdefault(f.parent.name, str(f.parent))
        return found

    @classmethod
    def permission_args(cls, access, write_dirs=(), git_dirs=(), workdir=None, mcp=False):
        """CLI permission flags for a worker. access is "full" (crew, flight director: edits and commands
        in their worktree; mcp=True keeps the user's MCP servers, for the flight director only) or "read"
        (reviewers, design workers, probes), which may also write files only under write_dirs (its output file) and run read-only git in git_dirs (its worktree and the run's repo worktrees;
        workdir is the folder it runs in, for the relative spellings of those under it).
        Each harness defines its read-only profile; check-tree.sh also voids any worktree change afterwards."""
        raise NotImplementedError

    def read_sandbox(self):
        """Whether this harness version has the flags permission_args uses (recorded in the catalog as
        read_sandbox; roles.py and validate_meta.py refuse a harness without them)."""
        raise NotImplementedError

    @classmethod
    def build_exec_cmd(cls, exe, prompt, args, conversation_id=None, stream=True, access="full", write_dirs=(),
                       git_dirs=(), workdir=None, mcp=False):
        fmt_args = cls.stream_output_args if stream else cls.json_output_args
        cmd = [exe, "-p", prompt, *fmt_args, *cls.permission_args(access, write_dirs, git_dirs, workdir, mcp), *args]
        if conversation_id:
            cmd += [cls.resume_flag, conversation_id]
        return cmd

    @classmethod
    def is_envelope(cls, data):
        raise NotImplementedError

    @classmethod
    def extract_envelope(cls, data, returncode, exe, conversation_id=None, raw_err=""):
        raise NotImplementedError

    @classmethod
    def parse_stream_event(cls, data, acc=None):
        """Parse one NDJSON event object. Return (progress_summary_or_None, final_envelope_or_None). acc is a
        dict kept for one worker run, for harnesses that stream a message in pieces. An adapter sets acc["active"]
        on every event except those it knows are not the worker at work (exec_prompt's silence limit counts the
        active ones, and results: an event a CLI update adds counts, so it cannot make a busy worker look stuck), and
        acc["conversation_id"] to the conversation the stream names."""
        if cls.is_envelope(data):
            return None, data
        return None, None

    @classmethod
    def parse_stderr_line(cls, line, acc):
        """A stderr line, for what it says about the worker's state: an adapter sets acc["waiting"] while the
        worker has ended its turn with no result and waits on background commands (exec_prompt then applies the
        longer wait limit instead of the silence limit)."""


class AntigravityAdapter(Adapter):
    name, binary = "antigravity", "agy"
    cumulative_usage = True  # live run 2026-11-23: a resumed turn reported turns=2 and the first turn's tokens too
    styles = ["slug", "flag"]
    models_cmd = ("models",)
    exe_state_key = "AGY"
    resume_flag = "--conversation"

    def read_sandbox(self):
        rc, out = self.run("--help")
        return rc == 0 and all(f in out for f in ("--sandbox", "--settings"))

    @classmethod
    def permission_args(cls, access, write_dirs=(), git_dirs=(), workdir=None, mcp=False):
        """Read-only workers: the terminal sandbox instead of --dangerously-skip-permissions (which by design also
        approves sandbox bypasses, antigravity-cli#36). Commands run without prompts inside the sandbox: no
        network, no writes outside the workspace. File tools may write only in the workspace and write_dirs
        (allowNonWorkspaceAccess=false plus a write_file allow rule per dir); reading stays open, for skill files.
        Headless agy soft-denies anything else, which ends the worker's run. Verified live 2026-11-23 with targets
        under $HOME (/tmp stays writable in the sandbox).
        No --add-dir for write_dirs: when the added folder lies in a worktree of the same repository (RUN_DIR in the
        docs worktree), the shell starts there instead of in the worker's worktree. Without it the shell starts in
        the worker's own folder, and both the file tool and the shell can still write the output folder (the
        write_file rule covers both; verified live 2026-11-24).
        agy has no flag that keeps MCP servers from starting; a session-only deny rule (`mcp(*)`, which the
        changelog says holds even under --dangerously-skip-permissions) refuses every MCP tool call instead.
        Verified live 2026-11-24 with the crew profile: the call was refused ("Permission denied for
        mcp(atlassian-mcp-server/atlassianUserInfo)")."""
        if access != "read":
            no_mcp = {"permissions": {"deny": ["mcp(*)"]}}
            return ["--dangerously-skip-permissions"] + ([] if mcp else
                                                          ["--settings", json.dumps(no_mcp, separators=(",", ":"))])
        dirs = [str(Path(d).resolve()) for d in write_dirs]
        bad = [d for d in dirs if _RULE_UNSAFE.search(d)]
        if bad:
            raise ValueError(f"output folder {bad[0]!r} has a character a permission rule cannot hold "
                             "(one of ( ) * ? [ ] \\ or a control character); use a folder without it")
        settings = {
            "toolPermission": "proceed-in-sandbox",
            "enableTerminalSandbox": True,
            "allowNonWorkspaceAccess": False,
            "permissions": {
                "allow": ["read_file(*)", *[f"write_file({d})" for d in dirs]],
                "deny": ["read_url(*)", "execute_url(*)", "mcp(*)"],
            },
        }
        return ["--sandbox", "--settings", json.dumps(settings, separators=(",", ":"))]

    def models(self):
        rc, raw = self.run(*self.models_cmd)
        if rc != 0:
            return []
        by_model = {}
        for line in raw.splitlines():
            slug, _, label = line.partition("\t")
            slug = slug.strip()
            if not re.fullmatch(r"[A-Za-z0-9][\w.\-]*", slug):
                continue
            base, _, suffix = slug.rpartition("-")
            if base and suffix in EFFORTS:
                entry = by_model.setdefault(base, {"model": base, "label": re.sub(r"\s*\([^)]*\)$", "", label.strip()), "efforts": []})
                entry["efforts"].append(suffix)
            else:
                by_model.setdefault(slug, {"model": slug, "label": label.strip() or slug, "efforts": ["default"]})
        for e in by_model.values():
            e["efforts"].sort(key=lambda x: EFFORTS.index(x) if x in EFFORTS else -1)
        return list(by_model.values())

    def plugins(self):
        return re.findall(r"^- (\S+) \(enabled\b", self.run("plugin", "list")[1], re.M)

    def mcp(self):
        out = {}
        for line in self.run("mcp", "list")[1].splitlines():
            cols = line.split()
            if len(cols) >= 3 and cols[0] != "NAME" and cols[1] in ("stdio", "http", "sse"):
                out[cols[0]] = cols[2] == "enabled"
        return out

    def user_skill_dirs(self):
        return [self.home / ".gemini" / "config" / "skills"]

    def skill_roots(self):
        base = self.home / ".gemini" / "config"
        cli_dirs = [self.home / ".gemini" / "antigravity-cli" / "skills", self.home / ".gemini" / "skills"]
        plugin_dirs = [
            d
            for p in self.plugins()
            for d in (base / "plugins" / p / "skills", self.home / ".gemini" / "antigravity-cli" / "plugins" / p / "skills")
        ]
        roots = cli_dirs + self.user_skill_dirs() + plugin_dirs
        bundled = Path(__file__).resolve().parent.parent / "skills"
        if not getattr(self.runner, "fixtures", False) and bundled.is_dir():
            roots.append(bundled)
        return roots

    @classmethod
    def is_envelope(cls, data):
        return (
            isinstance(data, dict)
            and isinstance(data.get("status"), str)
            and bool({"response", "error", "conversation_id"} & data.keys())
        )

    @classmethod
    def extract_envelope(cls, data, returncode, exe, conversation_id=None, raw_err=""):
        returned_id = _as_text(data.get("conversation_id")).strip()
        conv_id = returned_id or conversation_id or ""
        status = _as_text(data.get("status")).strip()
        resp = _as_text(data.get("response"))
        err = _as_text(data.get("error"))
        ok = (status == "SUCCESS" and returncode == 0)
        denied = data.get("denied_actions") if isinstance(data.get("denied_actions"), list) else []
        if ok and denied and not resp.strip():
            # Headless agy ends the run, with no response, at the first action it could not get approved.
            names = ", ".join(_as_text(d.get("action") if isinstance(d, dict) else d) for d in denied)
            ok, err = False, f"the worker stopped at an action its permissions deny ({names}); no response"
        detail = "" if ok else (err or resp or raw_err or f"{exe} exited {returncode} (status={status})").strip()
        return {
            "ok": ok,
            "returned_id": returned_id,
            "conv_id": conv_id,
            "response": resp,
            "detail": detail,
        }

    @classmethod
    def _final_message(cls, last_text, response):
        """The worker's final message when the result's response joins earlier messages to it, else None.

        agy's response is every non-empty agent message of the turn joined with "\n": narration written before a
        tool call, and a message written before a background command ended (live runs 2026-11-24, agy 1.5.15). So
        a marker line (ASK:, FAILED:, RESULT:) in the last message can sit behind earlier text. last_text is the
        text of the turn's last non-empty agent_response step; it is the final message only when the stripped
        response ends with it and has something in front of it that ends with a line break (agy's separator; a
        match that starts mid-line means the stream missed part of the message, and the response is kept whole)."""
        final = (last_text or "").strip()
        whole = response.strip()
        if not (final and whole.endswith(final)):
            return None
        before = whole[:-len(final)]
        if not before.strip() or not re.search(r"\n[ \t]*\Z", before):
            return None
        return final

    @classmethod
    def parse_stderr_line(cls, line, acc):
        """agy prints "root agent idle; waiting for N background task(s) to finish" when the worker ends its turn
        while a command it moved to the background still runs (cli-facts.md): no result and no steps come until
        that command ends, so the worker is waiting, not stuck."""
        if _AGY_WAITING_RE.search(line):
            acc["waiting"] = True

    @classmethod
    def parse_stream_event(cls, data, acc=None):
        """agy stream-json (captured 2026-11-23): step_update events; an agent_response streams its text as
        text_delta pieces until its DONE event; a tool step is ACTIVE (tool_info.parameters), then DONE or ERROR
        (tool_info.error.message). acc["last_text"] keeps the text of the turn's last non-empty agent_response; at
        the result event, acc["final_response"] is set to that message when the result's response joins earlier
        messages to it (see _final_message), and removed otherwise."""
        if not isinstance(data, dict):
            return None, None
        acc = {} if acc is None else acc
        ev = data.get("event")
        if not isinstance(ev, str):
            ev = ""
        if ev == "result" and isinstance(data.get("result"), dict):
            result = data["result"]
            final = cls._final_message(acc.pop("last_text", ""), _as_text(result.get("response")))
            if final is None:
                acc.pop("final_response", None)
            else:
                acc["final_response"] = final
            return None, result
        if cls.is_envelope(data):
            return None, data
        if ev:
            # Every agy event but the result shows the worker at work (a result counts anyway); an error object
            # (no "event") does not.
            acc["active"] = True
        nested = data.get(ev) if isinstance(data.get(ev), dict) else {}
        # The captured stream (tests/fixtures/harness/agy_stream.ndjson) names the conversation at the top level of
        # the init event and inside every step_update.
        cid = _as_text(data.get("conversation_id") or nested.get("conversation_id")).strip()
        if cid:
            acc.setdefault("conversation_id", cid)  # for exec_prompt: resume a worker stopped before its result
        if ev == "init":
            return (f"init conv={cid}" if cid else "init"), None
        if ev == "step_update":
            su = nested or data
            step_type = _as_text(su.get("step_type")).strip()
            if step_type == "system_message":
                # The step that wakes the agent when a background command ends (cli-facts.md). Only this ends
                # "waiting": stdout and stderr are read apart, so a step printed just before the waiting line may be
                # seen after it. A stale "waiting" only lengthens the silence limit to the wait limit.
                acc.pop("waiting", None)
            state = _as_text(su.get("state")).strip().upper()
            tool_name = _as_text(su.get("tool_name")).strip()
            info = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
            if step_type == "tool" and tool_name:
                if state == "ERROR":
                    err = info.get("error") if isinstance(info.get("error"), dict) else {}
                    msg = _one_line(err.get("message") or err.get("type") or "failed", 160)
                    return f"tool error: {tool_name}: {msg}", None
                if state == "DONE":
                    # The ACTIVE event already showed the call; only a slow one is worth a second line.
                    dur = su.get("duration_seconds")
                    slow = isinstance(dur, (int, float)) and dur >= 10
                    return (f"tool done: {tool_name} ({dur:.0f}s)" if slow else None), None
                hint = _summarize_tool_args(info.get("parameters", info))
                return (f"tool: {tool_name} ({hint})" if hint else f"tool: {tool_name}"), None
            if step_type == "agent_response":
                key = f"text-{su.get('step_index')}"
                acc[key] = acc.get(key, "") + _as_text(su.get("text_delta"))
                if state == "DONE":
                    text = acc.pop(key)
                    if text.strip():
                        acc["last_text"] = text
                    said = _one_line(text, 160)
                    return (f"says: {said}" if said else None), None
        return None, None


_AGY_WAITING_RE = re.compile(r"waiting for \d+ background task")


class ClaudeAdapter(Adapter):
    name, binary = "claude", "claude"
    styles = ["flag"]
    models_cmd = ("--help",)
    exe_state_key = "CLAUDE"
    resume_flag = "--resume"
    stream_output_args = ["--output-format", "stream-json", "--verbose"]
    # Live 2026-11-24 (2.1.331): three calls on one conversation (the first, then twice --resume) reported
    # total_cost_usd 0.00378, 0.00695, 0.00736 while num_turns, duration_ms and usage were each call's own.
    cumulative_cost = True
    # A worker must not read or write the user's saved memory (~/.claude/projects/<project>/memory): a reviewer
    # once quoted a memory from an unrelated ticket over the run's own decisions. The variable turns auto-memory off
    # whatever the settings say (a settings key, autoMemoryEnabled, would lose to the variable set to 0). Verified
    # live 2026-11-24, 2.1.331: a worker that named a word from the project's MEMORY.md without it did not with it,
    # and wrote no memory file; CLAUDE.md discovery runs the same with it (its own switch is
    # CLAUDE_CODE_DISABLE_CLAUDE_MDS).
    extra_env = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    # Read-only workers get no general shell and no network: reading, writing only under write_dirs (their
    # output file), subagents and skills for the review swarms, and read-only git. Anything else is denied
    # without a prompt (dontAsk). check-tree.sh still voids any change to the worktree.
    # Verified live: `git diff | head` matches; a redirect into a file (`git diff > f`), `git push`, `$(...)` and
    # `cd x && ...` are denied. A `*` in a rule spans words, so `git -C` is allowed only with the worker's own repo
    # paths (permission_args): `git -C * diff *` also let `git -C . update-ref -m diff ...` run (live, 2.1.331). The
    # allowed subcommands' own options that write files or run programs are denied (READ_ONLY_DENY; deny beats allow).
    READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "LS", "NotebookRead", "TodoWrite", "Task", "Agent", "Skill",
                       *[f"Bash(git {c} *)" for c in READ_ONLY_GIT],
                       "Bash(head *)", "Bash(tail *)", "Bash(wc *)"]
    # Config on the command line (-c, --config-env: diff.external, core.pager ... run commands); --output writes a
    # file (diff, log, show; git takes no shorter spelling); --ext-diff and --textconv run configured programs;
    # git grep -O / --open-files-in-pager (or any prefix down to --op) runs its argument as the pager.
    READ_ONLY_DENY = ["Bash(git -c *)", "Bash(git -C * -c *)", "Bash(git *--config-env*)",
                      "Bash(git * --out*)", "Bash(git * --ext*)", "Bash(git * --textc*)",
                      "Bash(git * -O*)", "Bash(git * --op*)"]

    # Events that are not the worker at work, for the silence limit: background command updates, hooks, summaries
    # after a result, rate-limit notices and API retries (a CLI retrying forever is stuck, not busy), and a tool's
    # heartbeat (tool_progress with heartbeat: true, every 30 s while a tool runs, whether or not it gets anywhere).
    # Checked against a live stream (tests/fixtures/harness/claude_stream.ndjson, 2.1.331, 2026-11-25): a 75 s
    # foreground Bash call gave only heartbeats; thinking gives thinking_tokens events (work); a foreground
    # subagent's messages come with parent_tool_use_id (work), its Bash calls without heartbeats. A foreground
    # command is bounded by Claude Code's own Bash timeout (10 minutes at most by default), below the silence limit.
    NOISE_SUBTYPES = ("background_tasks_changed", "post_turn_summary", "api_retry")
    NOISE_PREFIXES = ("task_", "hook_")

    @classmethod
    def _is_noise(cls, data):
        if data.get("type") == "rate_limit_event":
            return True
        if data.get("type") == "tool_progress" and data.get("heartbeat"):
            return True
        sub = _as_text(data.get("subtype"))
        return data.get("type") == "system" and (sub in cls.NOISE_SUBTYPES or sub.startswith(cls.NOISE_PREFIXES))

    def read_sandbox(self):
        rc, out = self.run("--help")
        return rc == 0 and all(f in out for f in ("--permission-mode", "--allowedTools", "--strict-mcp-config"))

    @classmethod
    def permission_args(cls, access, write_dirs=(), git_dirs=(), workdir=None, mcp=False):
        """Read-only: `git -C` is allowed only on the git_dirs, in the spellings _shell_spellings gives (a path it
        cannot spell safely is left out: `git -C` on it is denied). A write dir that cannot be named safely in a rule
        is refused (ValueError) rather than left out, since the worker could not write its output."""
        if access == "read":
            # "//" marks an absolute path in a permission rule; Edit rules cover every file-editing tool.
            bad = [str(d) for d in write_dirs if _RULE_UNSAFE.search(str(Path(d).resolve()))]
            if bad:
                raise ValueError(f"output folder {bad[0]!r} has a character a permission rule cannot hold "
                                 "(one of ( ) * ? [ ] \\ or a control character); use a folder without it")
            writes = [f"{tool}(/{Path(d).resolve()}/**)" for d in write_dirs for tool in ("Edit", "Write")]
            paths = dict.fromkeys(s for d in git_dirs for p in _git_dir_spellings(d, workdir)
                                  for s in _shell_spellings(p))
            dash_c = [f"Bash(git -C {p} {c} *)" for p in paths for c in READ_ONLY_GIT]
            # One comma-joined value: --allowedTools is variadic and must not swallow later arguments.
            # --strict-mcp-config with no --mcp-config: no MCP servers (none of them is read-only by contract).
            return ["--permission-mode", "dontAsk", "--allowedTools", ",".join(cls.READ_ONLY_TOOLS + dash_c + writes),
                    "--disallowedTools", ",".join(cls.READ_ONLY_DENY), "--strict-mcp-config"]
        # Crew need no MCP server (only the flight director's JIRA jobs do): --strict-mcp-config starts none.
        return ["--dangerously-skip-permissions"] + ([] if mcp else ["--strict-mcp-config"])

    def models(self):
        rc, raw = self.run(*self.models_cmd)
        if rc != 0 or "--model" not in raw:
            return []
        efforts = ["default"]
        m_flag = re.search(r"(?m)^\s*--effort\b[^\n]*(?:\n(?!\s*-)[^\n]*)*", raw)
        if m_flag:
            block = m_flag.group(0)
            parsed = []
            for grp in re.findall(r"\(([^)]+)\)", block):
                items = [x.strip(" \"'") for x in grp.split(",")]
                cand = [x for x in items if x in EFFORTS]
                if cand:
                    parsed = cand
                    break
            efforts = parsed or ["low", "medium", "high"]
        models = [
            {"model": "opus", "label": "Claude Opus", "efforts": list(efforts)},
            {"model": "sonnet", "label": "Claude Sonnet", "efforts": list(efforts)},
            {"model": "haiku", "label": "Claude Haiku", "efforts": ["default"]},
        ]
        # --help names the current aliases ("e.g. 'fable', 'opus', or 'sonnet'"); add any new one after the base three
        # (catalog order is only menu order). A quoted name with a digit or dash is a full model id, not an alias.
        m_model = re.search(r"(?m)^\s*--model\b[^\n]*(?:\n(?!\s*-)[^\n]*)*", raw)
        for alias in re.findall(r"'([a-z]+)'", m_model.group(0) if m_model else ""):
            if all(m["model"] != alias for m in models):
                models.append({"model": alias, "label": f"Claude {alias.capitalize()}", "efforts": list(efforts)})
        return models

    def _parse_plugins(self):
        rc, raw = self.run("plugin", "list")
        if rc != 0:
            return [], [], {}
        ids, paths, versions = [], [], {}
        for block in re.split(r"(?m)^\s*❯\s+", raw)[1:]:
            lines = block.splitlines()
            pid = lines[0].strip() if lines else ""
            if not pid:
                continue
            status = ""
            path = ""
            version = ""
            for line in lines[1:]:
                s = line.strip()
                if s.startswith("Status:"):
                    status = s.partition(":")[2].strip().lower()
                elif s.startswith("Path:"):
                    path = s.partition(":")[2].strip()
                elif s.startswith("Version:"):
                    version = s.partition(":")[2].strip()
            if (
                re.search(r"\b(enabled|loaded)\b", status)
                and not re.search(r"\b(disabled|unloaded|not\s+(?:enabled|loaded))\b", status)
            ):
                if pid not in ids:
                    ids.append(pid)
                if path and path not in paths:
                    paths.append(path)
                if version and version not in versions.setdefault(pid, []):
                    versions[pid].append(version)
        return ids, paths, versions

    def plugins(self):
        return self._parse_plugins()[0]

    def _plugin_paths(self):
        return self._parse_plugins()[1]

    def mcp(self):
        rc, raw = self.run("mcp", "list")
        if rc != 0:
            return {}
        out = {}
        for line in raw.splitlines():
            # The name runs to the first colon followed by whitespace (plugin servers: plugin:<plugin>:<name>).
            m = re.match(r"^([^\n]+?):\s+\S+.*\s+-\s+(.+)$", line.strip())
            if m:
                status = m.group(2).lower()
                out[m.group(1).strip()] = (
                    bool(re.search(r"\bconnected\b", status))
                    and not re.search(r"\b(?:dis|not\s+)connected\b", status)
                )
        return out

    def user_skill_dirs(self):
        return [self.home / ".claude" / "skills"]

    def skill_roots(self):
        roots = list(self.user_skill_dirs())
        cache_root = self.home / ".claude" / "plugins" / "cache"
        ids, _, versions = self._parse_plugins()
        for pid in ids:
            if "@" not in pid:
                continue
            plugin, _, marketplace = pid.partition("@")
            plugin_cache = cache_root / marketplace / plugin
            if plugin_cache.is_dir():
                # Only the installed version: the cache keeps older ones, whose removed skills must not count.
                installed = [plugin_cache / v / "skills" for v in versions.get(pid, [])]
                installed = [d for d in installed if d.is_dir()]
                newest = sorted(
                    (d for d in plugin_cache.glob("*/skills") if d.is_dir()),
                    key=lambda p: (tuple(int(x) for x in re.findall(r"\d+", p.parent.name)), p.parent.name),
                    reverse=True,
                )[:1]
                roots.extend(installed or newest)
        for p in self._plugin_paths():
            sdir = Path(p) / "skills"
            if sdir.is_dir():
                roots.append(sdir)
        bundled = Path(__file__).resolve().parent.parent / "skills"
        if not getattr(self.runner, "fixtures", False) and bundled.is_dir():
            roots.append(bundled)
        return roots

    @classmethod
    def is_envelope(cls, data):
        return (
            isinstance(data, dict)
            and data.get("type") == "result"
            and bool({"session_id", "result", "is_error"} & data.keys())
        )

    @classmethod
    def extract_envelope(cls, data, returncode, exe, conversation_id=None, raw_err=""):
        returned_id = _as_text(data.get("session_id")).strip()
        conv_id = returned_id or conversation_id or ""
        subtype = _as_text(data.get("subtype")).strip()
        is_error = bool(data.get("is_error"))
        resp = _as_text(data.get("result"))
        raw_errs = data.get("errors")
        errs = "; ".join(_as_text(x) for x in raw_errs if x is not None) if isinstance(raw_errs, list) else _as_text(raw_errs)
        ok = (returncode == 0 and not is_error and (not subtype or subtype == "success"))
        detail = (
            ""
            if ok
            else (errs or _as_text(data.get("error")) or resp or raw_err or f"{exe} exited {returncode} (subtype={subtype or 'unknown'})").strip()
        )
        return {
            "ok": ok,
            "returned_id": returned_id,
            "conv_id": conv_id,
            "response": resp,
            "detail": detail,
        }

    @classmethod
    def parse_stream_event(cls, data, acc=None):
        """Also records in acc, for exec_prompt: "turn" (and "active") when the event belongs to a turn (system/init,
        assistant, user), and "background", the number of background commands (run_in_background Bash) the latest
        background_tasks_changed listed. Claude Code prints a result when the model ends its turn even while one
        runs, stays alive, and starts a new turn (system/init) with a second result when it ends (live 2026-11-24).
        Task events (task_started, task_updated, task_notification, ...) are not turn activity: they only come with
        the background count. Nor are hook_*, task_summary, post_turn_summary and rate_limit_event, which also come
        after a result."""
        if not isinstance(data, dict):
            return None, None
        if cls.is_envelope(data):
            return None, data
        acc = {} if acc is None else acc
        ev_type = data.get("type")
        if ev_type in ("assistant", "user") or (ev_type == "system" and data.get("subtype") == "init"):
            acc["turn"] = True
        if ev_type and not cls._is_noise(data):
            acc["active"] = True
        if ev_type == "system" and data.get("subtype") == "background_tasks_changed":
            acc["background"] = len(data["tasks"]) if isinstance(data.get("tasks"), list) else 0
        if ev_type == "system" and data.get("subtype") == "init":
            sid = _as_text(data.get("session_id")).strip()
            if sid:
                acc["conversation_id"] = sid  # for exec_prompt: resume a worker stopped before its result
            return (f"init session={sid}" if sid else "init"), None
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        blocks = [b for b in content if isinstance(b, dict)]
        if ev_type == "assistant":
            parts = []
            texts = [_as_text(b.get("text")) for b in blocks if b.get("type") == "text"]
            said = _one_line(" ".join(texts), 160)
            if said:
                parts.append(f"says: {said}")
            for block in blocks:
                if block.get("type") != "tool_use":
                    continue
                name = _as_text(block.get("name")).strip() or "tool"
                if name == "TodoWrite":
                    parts.append(_todo_summary(block.get("input")))
                    continue
                hint = _summarize_tool_args(block.get("input"))
                parts.append(f"tool: {name} ({hint})" if hint else f"tool: {name}")
            return (" | ".join(p for p in parts if p) or None), None
        if ev_type == "user":
            errors = [_one_line(_block_text(b.get("content")), 160) for b in blocks
                      if b.get("type") == "tool_result" and b.get("is_error")]
            if errors:
                return f"tool error: {errors[0]}", None
        return None, None


ADAPTERS = {"antigravity": AntigravityAdapter, "claude": ClaudeAdapter}


def get_adapter(harness):
    return ADAPTERS[harness]


def detect_session_harness(catalog, env=None):
    env = os.environ if env is None else env
    harnesses = catalog.get("harnesses", {})
    if (env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT")) and "claude" in harnesses:
        return "claude"
    if (env.get("ANTIGRAVITY_CLI_ALIAS") or any(k.startswith(("ANTIGRAVITY_", "AGY_")) and env.get(k) for k in env)) and "antigravity" in harnesses:
        return "antigravity"
    if "antigravity" in harnesses:
        return "antigravity"
    if len(harnesses) == 1:
        return next(iter(harnesses))
    return "antigravity"


def _prefetch(adapters, runner):
    """Run every command detect will ask of the adapters at once (some CLIs take seconds per command), filling
    each adapter's run cache; detect then reads the same answers it would have got one by one. Only for adapters
    whose binary is on PATH (or fixtures)."""
    fixtures = getattr(runner, "fixtures", False)
    jobs = [(a, args) for a in adapters if fixtures or shutil.which(a.binary)
            for args in dict.fromkeys((tuple(a.version_cmd), tuple(a.models_cmd), ("--help",), ("plugin", "list"),
                                       ("mcp", "list")))]
    if not jobs:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [(a, args, pool.submit(runner, [a.binary, *args])) for a, args in jobs]
        for a, args, fut in futures:
            a._cache[args] = fut.result()


def detect(runner, home, env=None):
    cat = {"detected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "harnesses": {}}
    adapters = {name: cls(runner, home, env) for name, cls in ADAPTERS.items()}
    _prefetch(adapters.values(), runner)
    for name, a in adapters.items():
        found = a.find()
        if not found:
            continue
        models = a.models()
        cat["harnesses"][name] = {
            "binary": found["binary"], "version": found["version"],
            "styles": list(a.styles), "models": models, "skills": a.skills(), "plugins": a.plugins(),
            "mcp": a.mcp(), "broken": None if models else (a.raw_models()[:400] or "no output"),
            "read_sandbox": a.read_sandbox(),
            "skill_dirs": [str(d) for d in a.user_skill_dirs()],
            "plugin_cmd": f"{a.binary} plugin list", "mcp_cmd": f"{a.binary} mcp list",
        }
    return cat


def _model(catalog, harness, model):
    for m in catalog["harnesses"][harness]["models"]:
        if m["model"] == model:
            return m
    raise KeyError(f"{harness}:{model} not in catalog")


def launch_args(catalog, pair, style):
    if pair.harness not in catalog["harnesses"]:
        raise KeyError(f"harness {pair.harness} not in catalog")
    default = pair.effort == "default"
    if style == "slug":
        return ["--model", pair.model if default else f"{pair.model}-{pair.effort}"]
    if style == "flag":
        return ["--model", pair.model] + ([] if default else ["--effort", pair.effort])
    raise ValueError(f"unknown launch style {style!r}")


def top_effort(catalog, harness, model):
    efforts = _model(catalog, harness, model)["efforts"]
    known = [e for e in EFFORTS if e in efforts]
    return known[-1] if known else "default"


def pick_style(catalog, state, pair):
    own = state.get(f"LAUNCH_STYLE_{pair_key(pair)}")
    if own:
        return own
    prefix = f"LAUNCH_STYLE_{pair_key(pair.harness + ':')}"
    for k, v in sorted(state.items()):
        if k.startswith(prefix):
            return v
    return catalog["harnesses"][pair.harness]["styles"][0]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harnesses.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("--out", required=True)
    d.add_argument("--fixtures")
    d.add_argument("--home")
    a = sub.add_parser("args")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--state")
    src.add_argument("--catalog")
    a.add_argument("--pair", required=True)
    s = sub.add_parser("skill-dir")
    s.add_argument("--catalog", required=True)
    s.add_argument("--harness", required=True)
    s.add_argument("--skill", required=True)
    i = sub.add_parser("install-dirs")
    i.add_argument("--fixtures")
    i.add_argument("--home")
    t = sub.add_parser("top-effort")
    t.add_argument("--catalog", required=True)
    t.add_argument("--harness", required=True)
    t.add_argument("--model", required=True)
    try:
        ns = ap.parse_args(argv)
    except SystemExit as e:
        return 64 if e.code else 0
    try:
        if ns.cmd == "detect":
            home = ns.home or os.path.expanduser("~")
            runner = fixture_runner(ns.fixtures) if ns.fixtures else real_runner
            env = {} if ns.fixtures else None
            Path(ns.out).write_text(json.dumps(detect(runner, home, env), indent=1) + "\n", encoding="utf-8")
        elif ns.cmd == "args":
            state = json.loads(Path(ns.state).read_text(encoding="utf-8")) if ns.state else {}
            catalog = json.loads(Path(ns.catalog or state["CATALOG"]).read_text(encoding="utf-8"))
            pair = parse_pair(ns.pair)
            print(shlex.join(launch_args(catalog, pair, pick_style(catalog, state, pair))))
        elif ns.cmd == "install-dirs":
            home = ns.home or os.path.expanduser("~")
            runner = fixture_runner(ns.fixtures) if ns.fixtures else real_runner
            env = {} if ns.fixtures else None
            for name, cls in ADAPTERS.items():
                a = cls(runner, home, env)
                print(f"{name}\t{a.user_skill_dirs()[0]}\t{'found' if a.find() else 'missing'}")
        elif ns.cmd == "skill-dir":
            cat = json.loads(Path(ns.catalog).read_text(encoding="utf-8"))
            folder = cat["harnesses"][ns.harness]["skills"].get(ns.skill)
            if not folder:
                print(f"ERROR skill {ns.skill} not found for {ns.harness}")
                return 1
            print(folder)
        else:
            cat = json.loads(Path(ns.catalog).read_text(encoding="utf-8"))
            print(top_effort(cat, ns.harness, ns.model))
    except (KeyError, ValueError, OSError) as e:
        print(f"ERROR {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
