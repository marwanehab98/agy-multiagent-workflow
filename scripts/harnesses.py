"""Harness adapters for Antigravity (`agy` CLI) and Claude Code (`claude` CLI).

Usage:
  python3 -I harnesses.py detect --out <file> [--fixtures DIR --home DIR]
  python3 -I harnesses.py args (--state <state.json> | --catalog <file>) --pair <pair>   # CLI model/effort args
  python3 -I harnesses.py skill-dir --catalog <file> --harness H --skill NAME
  python3 -I harnesses.py top-effort --catalog <file> --harness H --model M
  python3 -I harnesses.py install-dirs [--fixtures DIR --home DIR]   # name<TAB>skills folder<TAB>found|missing

`detect` runs harness CLI commands and writes a catalog:
  {"detected_at": iso, "harnesses": {name: {binary, version, styles, models,
   skills: {name: folder}, plugins: [enabled], mcp: {name: ok}, broken: null|str,
   skill_dirs: [folder], plugin_cmd: str, mcp_cmd: str}}}
skill_dirs, plugin_cmd and mcp_cmd say where a missing requirement belongs (roles.py check prints them).
A harness whose binary is missing is left out; one whose model list parses empty is
recorded with `broken` set to the start of the raw output.
`args --state` reads CATALOG and the LAUNCH_STYLE_ keys from a run's state file; `args --catalog` (before
any run exists) uses the harness's first launch style.
Exit 0 ok. Exit 1 not found (skill-dir, top-effort) or bad input. Exit 64 bad usage.
"""
import argparse
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
    for key in ("command", "CommandLine", "file_path", "TargetFile", "AbsolutePath", "path", "pattern", "query", "url", "description"):
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            s = " ".join(val.split())
            return (s[: limit - 3] + "...") if len(s) > limit else s
    return ""


# Git subcommands a read-only Claude worker may run (reviewers read the task's diff and history).
READ_ONLY_GIT = ["diff", "log", "show", "status", "rev-parse", "ls-files", "grep", "blame"]


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


class Adapter:
    name = binary = ""
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
    def permission_args(cls, access, write_dirs=()):
        """CLI permission flags for a worker. access is "full" (crew, flight director: edits and commands
        in their worktree) or "read" (reviewers, design workers, probes), which may also write files only under
        write_dirs (its output file). Each harness defines its read-only profile; check-tree.sh also voids any
        worktree change afterwards."""
        raise NotImplementedError

    def read_sandbox(self):
        """Whether this harness version has the flags permission_args uses (recorded in the catalog as
        read_sandbox; roles.py and validate_meta.py refuse a harness without them)."""
        raise NotImplementedError

    @classmethod
    def build_exec_cmd(cls, exe, prompt, args, conversation_id=None, stream=True, access="full", write_dirs=()):
        fmt_args = cls.stream_output_args if stream else cls.json_output_args
        cmd = [exe, "-p", prompt, *fmt_args, *cls.permission_args(access, write_dirs), *args]
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
    def parse_stream_event(cls, data):
        """Parse one NDJSON event object. Return (progress_summary_or_None, final_envelope_or_None)."""
        if cls.is_envelope(data):
            return None, data
        return None, None


class AntigravityAdapter(Adapter):
    name, binary = "antigravity", "agy"
    styles = ["slug", "flag"]
    models_cmd = ("models",)
    exe_state_key = "AGY"
    resume_flag = "--conversation"

    def read_sandbox(self):
        rc, out = self.run("--help")
        return rc == 0 and all(f in out for f in ("--sandbox", "--settings", "--add-dir"))

    @classmethod
    def permission_args(cls, access, write_dirs=()):
        """Read-only workers: the terminal sandbox instead of --dangerously-skip-permissions (which by design also
        approves sandbox bypasses, antigravity-cli#36). Commands run without prompts inside the sandbox: no
        network, no writes outside the workspace and write_dirs. File tools may write only in the workspace and
        write_dirs (allowNonWorkspaceAccess=false plus --add-dir); reading stays open, for skill files. Headless
        agy soft-denies anything else, which ends the worker's run. Verified live 2026-11-23 with targets
        under $HOME (/tmp stays writable in the sandbox)."""
        if access != "read":
            return ["--dangerously-skip-permissions"]
        dirs = [str(Path(d).resolve()) for d in write_dirs]
        settings = {
            "toolPermission": "proceed-in-sandbox",
            "enableTerminalSandbox": True,
            "allowNonWorkspaceAccess": False,
            "permissions": {
                "allow": ["read_file(*)", *[f"write_file({d})" for d in dirs]],
                "deny": ["read_url(*)", "execute_url(*)"],
            },
        }
        add_dirs = [arg for d in dirs for arg in ("--add-dir", d)]
        return ["--sandbox", *add_dirs, "--settings", json.dumps(settings, separators=(",", ":"))]

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
    def parse_stream_event(cls, data):
        if not isinstance(data, dict):
            return None, None
        ev = data.get("event")
        if ev == "result" and isinstance(data.get("result"), dict):
            return None, data["result"]
        if cls.is_envelope(data):
            return None, data
        if ev == "init":
            init = data.get("init") if isinstance(data.get("init"), dict) else data
            cid = _as_text(init.get("conversation_id")).strip()
            return (f"init conv={cid}" if cid else "init"), None
        if ev == "step_update":
            su = data.get("step_update") if isinstance(data.get("step_update"), dict) else data
            step_type = _as_text(su.get("step_type")).strip()
            state = _as_text(su.get("state")).strip().upper()
            tool_name = _as_text(su.get("tool_name")).strip()
            if step_type == "tool" and tool_name:
                if state == "DONE":
                    dur = su.get("duration_seconds")
                    suffix = f" ({dur:.1f}s)" if isinstance(dur, (int, float)) else ""
                    return f"tool done: {tool_name}{suffix}", None
                hint = _summarize_tool_args(su.get("tool_info"))
                return (f"tool: {tool_name} ({hint})" if hint else f"tool: {tool_name}"), None
            if step_type == "agent_response" and state == "DONE":
                return "agent response complete", None
        return None, None


class ClaudeAdapter(Adapter):
    name, binary = "claude", "claude"
    styles = ["flag"]
    models_cmd = ("--help",)
    exe_state_key = "CLAUDE"
    resume_flag = "--resume"
    stream_output_args = ["--output-format", "stream-json", "--verbose"]
    # Read-only workers get no general shell and no network: reading, writing only under write_dirs (their
    # output file), subagents and skills for the review swarms, and read-only git. Anything else is denied
    # without a prompt (dontAsk). check-tree.sh still voids any change to the worktree.
    # Verified live: `git -C <repo> diff` (multi-repo reviewers) and `git diff | head` match; a redirect into a
    # file (`git diff > f`), `git push` and `cd x && ...` are denied.
    READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "LS", "NotebookRead", "TodoWrite", "Task", "Agent", "Skill",
                       *[f"Bash(git {c} *)" for c in READ_ONLY_GIT],
                       *[f"Bash(git -C * {c} *)" for c in READ_ONLY_GIT],
                       "Bash(head *)", "Bash(tail *)", "Bash(wc *)"]

    def read_sandbox(self):
        rc, out = self.run("--help")
        return rc == 0 and all(f in out for f in ("--permission-mode", "--allowedTools", "--strict-mcp-config"))

    @classmethod
    def permission_args(cls, access, write_dirs=()):
        if access == "read":
            # "//" marks an absolute path in a permission rule; Edit rules cover every file-editing tool.
            writes = [f"{tool}(/{Path(d).resolve()}/**)" for d in write_dirs for tool in ("Edit", "Write")]
            # One comma-joined value: --allowedTools is variadic and must not swallow later arguments.
            # --strict-mcp-config with no --mcp-config: no MCP servers (none of them is read-only by contract).
            return ["--permission-mode", "dontAsk", "--allowedTools", ",".join(cls.READ_ONLY_TOOLS + writes),
                    "--strict-mcp-config"]
        return ["--dangerously-skip-permissions"]

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
        return [
            {"model": "opus", "label": "Claude Opus", "efforts": list(efforts)},
            {"model": "sonnet", "label": "Claude Sonnet", "efforts": list(efforts)},
            {"model": "haiku", "label": "Claude Haiku", "efforts": ["default"]},
        ]

    def _parse_plugins(self):
        rc, raw = self.run("plugin", "list")
        if rc != 0:
            return [], []
        ids, paths = [], []
        for block in re.split(r"(?m)^\s*❯\s+", raw)[1:]:
            lines = block.splitlines()
            pid = lines[0].strip() if lines else ""
            if not pid:
                continue
            status = ""
            path = ""
            for line in lines[1:]:
                s = line.strip()
                if s.startswith("Status:"):
                    status = s.partition(":")[2].strip().lower()
                elif s.startswith("Path:"):
                    path = s.partition(":")[2].strip()
            if (
                re.search(r"\b(enabled|loaded)\b", status)
                and not re.search(r"\b(disabled|unloaded|not\s+(?:enabled|loaded))\b", status)
            ):
                if pid not in ids:
                    ids.append(pid)
                if path and path not in paths:
                    paths.append(path)
        return ids, paths

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
            m = re.match(r"^([^:\n]+?):\s+\S+.*\s+-\s+(.+)$", line.strip())
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
        for pid in self.plugins():
            if "@" not in pid:
                continue
            plugin, _, marketplace = pid.partition("@")
            plugin_cache = cache_root / marketplace / plugin
            if plugin_cache.is_dir():
                for skill_dir in sorted(
                    plugin_cache.glob("*/skills"),
                    key=lambda p: (tuple(int(x) for x in re.findall(r"\d+", p.parent.name)), p.parent.name),
                    reverse=True,
                ):
                    if skill_dir.is_dir():
                        roots.append(skill_dir)
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
    def parse_stream_event(cls, data):
        if not isinstance(data, dict):
            return None, None
        if cls.is_envelope(data):
            return None, data
        ev_type = data.get("type")
        if ev_type == "system" and data.get("subtype") == "init":
            sid = _as_text(data.get("session_id")).strip()
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


def detect(runner, home, env=None):
    cat = {"detected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "harnesses": {}}
    for name, cls in ADAPTERS.items():
        a = cls(runner, home, env)
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
