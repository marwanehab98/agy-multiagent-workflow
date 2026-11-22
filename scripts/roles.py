"""Setup picks: menus, requirement checks and the saved picks file.

Usage:
  python3 -I roles.py menu --catalog C [--harness H [--model M]]
  python3 -I roles.py check --catalog C --jira yes|no --director P --safety P --crew P [--session-harness H]
  python3 -I roles.py remembered --roles F --catalog C
  python3 -I roles.py save --roles F --director P --safety P --crew P
  python3 -I roles.py to-state --roles F --state S --catalog C

`menu` prints `N<TAB>value<TAB>label` lines (harnesses, then that harness's models, then a
model's efforts); broken harnesses follow as `BROKEN <name>: <reason>`.
`check` verifies the capsule communicator session's required skills on the session harness plus each of the 3
worker roles (`director`, `safety`, `crew`), printing one `OK`, `UNKNOWN`, or `MISSING` line per finding.
A crew pick equal to the flight director pick is UNKNOWN: the flight director never implements first.
A NOTE follows when director and crew share harness and model, and another when the director is then
a lower effort than the crew (the ladder's last rung would be weaker). Exit 1 on UNKNOWN/MISSING.
`remembered` prints `role=pair` lines then `AVAILABLE yes`, or one `AVAILABLE no <role> <pair>` line per
remembered pick that isn't in the catalog any more; exit 1 with `NONE` when the file is missing.
`save` writes the picks file; `to-state` copies the picks, CREW_TOP and CATALOG into a state file.
Exit 0 ok. Exit 1 findings or bad input. Exit 64 bad usage.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from harnesses import detect_session_harness, top_effort  # noqa: E402
from pairs import EFFORTS, parse_pair  # noqa: E402
import state as state_file  # noqa: E402

ROLES = ("director", "safety", "crew")
SESSION_REQUIRES = ["mission", "gatecheck", "touchdown"]
REQUIRES = {
    "director": ["draft-spec", "flight-plan"],
    "safety": ["crucible", "gauntlet", "crossfire"],
    "crew": ["red-green", "gatecheck"],
}
EXECUTE_SESSION_REQUIRES = ["launch-plan", "gatecheck", "touchdown"]
EXECUTE_REQUIRES = {
    "director": [],
    "safety": [],
    "crew": ["red-green", "gatecheck"],
}


def load_catalog(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def unknown_why(catalog, pair):
    """None when the pair is launchable per the catalog, else why not."""
    h = catalog["harnesses"].get(pair.harness)
    if h is None:
        return f"harness {pair.harness} not detected"
    if h.get("broken"):
        return f"harness {pair.harness} is broken: {h['broken']}"
    for m in h["models"]:
        if m["model"] == pair.model:
            if pair.effort not in m["efforts"]:
                return f"effort {pair.effort} not detected for {pair.model}; use one of {' '.join(m['efforts'])}"
            return None
    return f"model {pair.model} not detected in {pair.harness}; use one of {' '.join(m['model'] for m in h['models'])}"


def has_atlassian(h):
    return any("atlassian" in name.lower() and ok for name, ok in h.get("mcp", {}).items())


def where(h, item):
    """Where a missing item should be, from the catalog's adapter fields; empty for an older catalog."""
    if item == "atlassian-mcp":
        return f": connect an Atlassian MCP server ({h['mcp_cmd']})" if h.get("mcp_cmd") else ""
    if not h.get("skill_dirs"):
        return ""
    plugin = f", or enable a plugin that has it ({h['plugin_cmd']})" if h.get("plugin_cmd") else ""
    return f": put it in {' or '.join(h['skill_dirs'])}{plugin}"


def session_harness_name(catalog, override=None):
    if override:
        return override
    return detect_session_harness(catalog)


def check(catalog, jira, picks, mode="full", session_harness=None):
    session_req = EXECUTE_SESSION_REQUIRES if mode == "launch-plan" else SESSION_REQUIRES
    role_reqs = EXECUTE_REQUIRES if mode == "launch-plan" else REQUIRES
    lines, bad = [], False
    sess_name = session_harness_name(catalog, override=session_harness)
    sess_h = catalog["harnesses"].get(sess_name)
    if sess_h is None:
        lines.append(f"UNKNOWN session {sess_name} harness {sess_name} not detected")
        bad = True
    elif sess_h.get("broken"):
        lines.append(f"UNKNOWN session {sess_name} harness {sess_name} is broken: {sess_h['broken']}")
        bad = True
    else:
        missing_sess = [s for s in session_req if s not in sess_h["skills"]]
        for item in missing_sess:
            lines.append(f"MISSING session {sess_name} {item} in {sess_name}{where(sess_h, item)}")
        bad = bad or bool(missing_sess)
        if not missing_sess:
            lines.append(f"OK session {sess_name}")

    d, c = parse_pair_or_none(picks["director"]), parse_pair_or_none(picks["crew"])
    for role in ROLES:
        text = picks[role]
        try:
            pair = parse_pair(text)
        except ValueError as e:
            lines.append(f"UNKNOWN {role} {text} {e}")
            bad = True
            continue
        why = unknown_why(catalog, pair)
        if not why and role == "crew" and pair == d:
            why = "is the flight director pick, which never implements first; pick another effort or model"
        if why:
            lines.append(f"UNKNOWN {role} {text} {why}")
            bad = True
            continue
        h = catalog["harnesses"][pair.harness]
        missing = [s for s in role_reqs[role] if s not in h["skills"]]
        if role == "director" and jira and not has_atlassian(h):
            missing.append("atlassian-mcp")
        for item in missing:
            lines.append(f"MISSING {role} {text} {item} in {pair.harness}{where(h, item)}")
        bad = bad or bool(missing)
        if not missing:
            lines.append(f"OK {role} {text}")
    if d and c and (d.harness, d.model) == (c.harness, c.model):
        lines.append(f"NOTE director and crew share {d.harness}:{d.model}; the ladder skips repeated rungs")
        if d.effort in EFFORTS and c.effort in EFFORTS and EFFORTS.index(d.effort) < EFFORTS.index(c.effort):
            lines.append(f"NOTE the ladder's last rung, director {d}, is a lower effort than crew {c} on the same "
                         "model, so escalating to it is weaker than the first attempt")
    return lines, bad


def parse_pair_or_none(text):
    try:
        return parse_pair(text)
    except ValueError:
        return None


class MenuError(Exception):
    """A --harness or --model the catalog doesn't list; the message is the line to print."""


def menu(catalog, harness, model):
    rows = []
    if harness is None:
        for name, h in catalog["harnesses"].items():
            if not h.get("broken"):
                rows.append((name, f"{name} {h['version']}".strip()))
        broken = [f"BROKEN {n}: {h['broken']}" for n, h in catalog["harnesses"].items() if h.get("broken")]
    else:
        h = catalog["harnesses"].get(harness)
        if h is None:
            raise MenuError(f"UNKNOWN harness {harness} (known: {', '.join(catalog['harnesses'])})")
        broken = []
        if model is None:
            rows = [(m["model"], m["label"]) for m in h["models"]]
        else:
            m = next((m for m in h["models"] if m["model"] == model), None)
            if m is None:
                raise MenuError(f"UNKNOWN model {model} for {harness} (known: {', '.join(x['model'] for x in h['models'])})")
            rows = [(e, e) for e in m["efforts"]]
    return [f"{i}\t{v}\t{label}" for i, (v, label) in enumerate(rows, 1)] + broken


def read_roles(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def add_picks(p):
    for r in ROLES:
        p.add_argument(f"--{r}", required=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="roles.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("menu")
    m.add_argument("--catalog", required=True)
    m.add_argument("--harness")
    m.add_argument("--model")
    c = sub.add_parser("check")
    c.add_argument("--catalog", required=True)
    c.add_argument("--jira", required=True, choices=["yes", "no"])
    c.add_argument("--mode", choices=["full", "launch-plan"], default="full")
    c.add_argument("--session-harness")
    add_picks(c)
    r = sub.add_parser("remembered")
    r.add_argument("--roles", required=True)
    r.add_argument("--catalog", required=True)
    s = sub.add_parser("save")
    s.add_argument("--roles", required=True)
    add_picks(s)
    t = sub.add_parser("to-state")
    t.add_argument("--roles", required=True)
    t.add_argument("--state", required=True)
    t.add_argument("--catalog", required=True)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 64 if e.code else 0
    if a.cmd == "menu" and a.model and not a.harness:
        print("--model needs --harness", file=sys.stderr)
        return 64
    try:
        if a.cmd == "menu":
            try:
                print("\n".join(menu(load_catalog(a.catalog), a.harness, a.model)))
            except MenuError as e:
                print(e)
                return 1
        elif a.cmd == "check":
            lines, bad = check(load_catalog(a.catalog), a.jira == "yes", {x: getattr(a, x) for x in ROLES},
                               mode=a.mode, session_harness=a.session_harness)
            print("\n".join(lines))
            return 1 if bad else 0
        elif a.cmd == "remembered":
            if not Path(a.roles).is_file():
                print("NONE")
                return 1
            picks, catalog = read_roles(a.roles), load_catalog(a.catalog)
            for role in ROLES:
                if role not in picks:
                    print("NONE")
                    return 1
            for role in ROLES:
                print(f"{role}={picks[role]}")
            gone = [(role, picks[role]) for role in ROLES
                    if (p := parse_pair_or_none(picks[role])) is None or unknown_why(catalog, p)]
            print("\n".join(f"AVAILABLE no {role} {pair}" for role, pair in gone) or "AVAILABLE yes")
        elif a.cmd == "save":
            for x in ROLES:
                parse_pair(getattr(a, x))
            path = Path(a.roles)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {x: getattr(a, x) for x in ROLES}
            data["saved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        else:
            picks, catalog = read_roles(a.roles), load_catalog(a.catalog)
            crew = parse_pair(picks["crew"])
            path = Path(a.state)
            data = state_file.load(path)
            for role in ROLES:
                data[f"ROLE_{role.upper()}"] = picks[role]
            data["CREW_TOP"] = top_effort(catalog, crew.harness, crew.model)
            data["CATALOG"] = a.catalog
            state_file.save(path, data)
    except (KeyError, ValueError, StopIteration, OSError) as e:
        print(f"ERROR {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
