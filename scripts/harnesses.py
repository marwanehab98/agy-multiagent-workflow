"""Harness adapters: the one place that knows how each harness's CLI behaves (design 2.4).

Usage:
  python3 -I harnesses.py detect --out <file> [--fixtures DIR --home DIR]
  python3 -I harnesses.py args (--state <state.json> | --catalog <file>) --pair <pair>   # Orca launch args, one line
  python3 -I harnesses.py skill-dir --catalog <file> --harness H --skill NAME
  python3 -I harnesses.py top-effort --catalog <file> --harness H --model M

`detect` runs each harness's own CLI and writes a catalog:
  {"detected_at": iso, "harnesses": {name: {orca_agent, binary, version, styles, models,
   skills: {name: folder}, plugins: [enabled], mcp: {name: ok}, broken: null|str,
   skill_dirs: [folder], plugin_cmd: str, mcp_cmd: str}}}
skill_dirs, plugin_cmd and mcp_cmd say where a missing requirement belongs (roles.py check prints them).
A harness whose binary is missing is left out; one whose model list parses empty is
recorded with `broken` set to the start of the raw output.
`args --state` reads CATALOG and the LAUNCH_STYLE_ keys from a run's state file; `args --catalog` (before
any run exists, e.g. the setup handoff) uses the pair's harness's first launch style.
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

EXTRA_MODELS = ["haiku"]  # Claude Code has no command that lists models; the test launch proves each one


def real_runner(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, r.stdout + (r.stderr if r.returncode else "")


FIXTURE_FILES = {
    ("agy", "models"): "agy_models.txt",
    ("agy", "plugin", "list"): "agy_plugin_list.txt",
    ("agy", "mcp", "list"): "agy_mcp_list.txt",
    ("agy", "--version"): "agy_version.txt",
    ("claude", "--help"): "claude_help.txt",
    ("claude", "plugin", "list"): "claude_plugin_list.txt",
    ("claude", "mcp", "list"): "claude_mcp_list.txt",
    ("claude", "--version"): "claude_version.txt",
}


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


class Adapter:
    name = orca_agent = binary = ""
    styles = []
    version_cmd = ["--version"]

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


class ClaudeAdapter(Adapter):
    name, orca_agent, binary = "claude", "claude", "claude"
    styles = ["flag"]
    models_cmd = ("--help",)

    def _option_text(self, flag):
        """The help text of one option: its line plus the continuation lines up to the next option."""
        lines, out, on = self.run("--help")[1].splitlines(), [], False
        for line in lines:
            if re.match(rf"\s+(?:-\w, )?{re.escape(flag)}\b", line):
                on = True
            elif on and re.match(r"\s+-", line):
                break
            if on:
                out.append(line)
        return " ".join(out)

    def models(self):
        aliases = re.findall(r"'([A-Za-z0-9][\w.\-]*)'", self._option_text("--model"))
        names = list(dict.fromkeys(aliases + EXTRA_MODELS)) if aliases else []
        m = re.search(r"\(([^)]*)\)", self._option_text("--effort"))
        efforts = [e.strip() for e in m.group(1).split(",") if e.strip() in EFFORTS] if m else []
        return [{"model": n, "label": n, "efforts": efforts or ["default"]} for n in names]

    def plugins(self):
        names, cur = [], None
        for line in self.run("plugin", "list")[1].splitlines():
            m = re.match(r"\s*❯\s*([^@\s]+)@", line)
            if m:
                cur = m.group(1)
            elif cur and re.search(r"Status:\s*✔\s*(enabled|loaded)", line) and cur not in names:
                names.append(cur)
        return names

    def mcp(self):
        out = {}
        for line in self.run("mcp", "list")[1].splitlines():
            m = re.match(r"(.+?):\s.* - ", line)
            if m:
                out[m.group(1).strip()] = "✔ Connected" in line
        return out

    def homes(self):
        homes = [Path(self.env["CLAUDE_CONFIG_DIR"])] if self.env.get("CLAUDE_CONFIG_DIR") else []
        homes.append(self.home / ".claude")
        return list(dict.fromkeys(homes))

    def user_skill_dirs(self):
        return [h / "skills" for h in self.homes()][:1]

    def skill_roots(self):
        homes = [h for h in self.homes() if h.is_dir()]
        roots = [h / "skills" for h in homes]
        enabled = self.plugins()
        for h in homes:
            for p in enabled:
                roots += sorted(h.glob(f"plugins/cache/*/{p}/*/skills"))
        return roots


class AntigravityAdapter(Adapter):
    name, orca_agent, binary = "antigravity", "antigravity", "agy"
    styles = ["slug", "flag"]
    models_cmd = ("models",)

    def models(self):
        by_model = {}
        for line in self.raw_models().splitlines():
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
        return self.user_skill_dirs() + [base / "plugins" / p / "skills" for p in self.plugins()]


ADAPTERS = {"claude": ClaudeAdapter, "antigravity": AntigravityAdapter}


def detect(runner, home, env=None):
    cat = {"detected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "harnesses": {}}
    for name, cls in ADAPTERS.items():
        a = cls(runner, home, env)
        found = a.find()
        if not found:
            continue
        models = a.models()
        cat["harnesses"][name] = {
            "orca_agent": a.orca_agent, "binary": found["binary"], "version": found["version"],
            "styles": list(a.styles), "models": models, "skills": a.skills(), "plugins": a.plugins(),
            "mcp": a.mcp(), "broken": None if models else (a.raw_models()[:400] or "no output"),
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
    agent = catalog["harnesses"][pair.harness]["orca_agent"]
    default = pair.effort == "default"
    if style == "slug":
        return ["--agent", agent, "--model", pair.model if default else f"{pair.model}-{pair.effort}"]
    if style == "flag":
        return ["--agent", agent, "--model", pair.model] + ([] if default else ["--effort", pair.effort])
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
