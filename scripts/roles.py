"""Setup picks: menus, requirement checks and the saved picks file.

Usage:
  python3 -I roles.py menu --catalog C [--session-harness H] [--harness H [--model M]]
  python3 -I roles.py check --catalog C --jira yes|no --director P --safety P --crew P [--mode full|launch-plan]
      [--session-harness H]
  python3 -I roles.py remembered --roles F --catalog C [--session-harness H]
  python3 -I roles.py save --roles F --director P --safety P --crew P
  python3 -I roles.py to-state --roles F --state S --catalog C

`menu` prints `N<TAB>value<TAB>label` lines (harnesses, then that harness's models, then a
model's efforts); a harness line ends with `<TAB>subtitle` (its version); broken harnesses follow as
`BROKEN <name>: <reason>`. While any harness is signed in with OAuth (catalog `auth`), every role runs on the
harness of the session that runs the mission (harnesses.py session_evidence, or --session-harness): the harness
menu then lists only that one, followed by a `LOCKED <harness>: <reason>` line when it left another one out. A
session whose harness cannot be told is `UNKNOWN session`: run the command again with --session-harness.
`check` verifies the capsule communicator session's required skills on the session harness plus each of the 3
worker roles (`director`, `safety`, `crew`), printing one `OK`, `UNKNOWN`, or `MISSING` line per finding.
A crew pick equal to the flight director pick is UNKNOWN: the flight director never implements first.
So is any pick on another harness than the session's while any harness is signed in with OAuth.
A NOTE follows when director and crew share harness and model, and another when the director is then
a lower effort than the crew (the ladder's last rung would be weaker). Exit 1 on UNKNOWN/MISSING.
The picks file keeps the last 3 selections (a selection is the three picks of one run), most recently used first.
`remembered` prints the ones this session can still use as menu lines, in that order,
`N<TAB>director=<pair> safety=<pair> crew=<pair><TAB>Selection N<TAB><the picks in words>`, then a last line
`N<TAB>new<TAB>New<TAB>...`. A selection with a pick the catalog no longer has, or that the OAuth rule above does
not allow in this session, is left out (and stays in the file). Exit 1 with `NONE` when none is left.
`save` puts a selection first in the picks file: one already there only moves up, nothing else changes; a new one
is added, and beyond 3 the least recently used is dropped. `to-state` copies the first selection, CREW_TOP and
CATALOG into a state file.
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
    "director": ["mission-brief", "flight-plan"],
    "safety": ["wind-tunnel", "flight-readiness", "cross-check", "spec-drift"],  # spec-drift: run_finish's drift check
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
    if not h.get("read_sandbox"):
        return (f"harness {pair.harness} {h.get('version', '')} lacks the flags read-only workers run with "
                f"(see references/cli-facts.md); update {h.get('binary', pair.harness)}")
    for m in h["models"]:
        if m["model"] == pair.model:
            if pair.effort not in m["efforts"]:
                return f"effort {pair.effort} not detected for {pair.model}; use one of {' '.join(m['efforts'])}"
            return None
    return f"model {pair.model} not detected in {pair.harness}; use one of {' '.join(m['model'] for m in h['models'])}"


def oauth_harnesses(catalog):
    """The usable harnesses signed in with OAuth; any one of them keeps every role on the session's harness."""
    return [n for n, h in catalog["harnesses"].items() if not h.get("broken") and h.get("auth") == "oauth"]


def oauth_reason(names):
    return f"{' and '.join(names)} {'is' if len(names) == 1 else 'are'} signed in with OAuth"


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


NO_SESSION = ("cannot tell which harness this session runs in; run this again with --session-harness <the harness "
              "you are running in>")


def session_harness_name(override=None):
    """The session's harness: the override, else what harnesses.py detects, else None (never a guess)."""
    return override or detect_session_harness()


def lock_why(catalog, pair, sess_name):
    """Why the OAuth rule refuses pair for a role, or None: with a usable harness signed in with OAuth, every role
    runs on the session's harness."""
    oauth = oauth_harnesses(catalog)
    if not oauth or pair.harness == sess_name:
        return None
    if sess_name is None:
        return f"cannot be checked: {oauth_reason(oauth)}, so every role runs on the session's harness, and I {NO_SESSION}"
    return (f"is not on this session's harness ({sess_name}), and {oauth_reason(oauth)}, so every role runs on the "
            f"session's harness; pick it on {sess_name}")


def check(catalog, jira, picks, mode="full", session_harness=None):
    session_req = EXECUTE_SESSION_REQUIRES if mode == "launch-plan" else SESSION_REQUIRES
    role_reqs = EXECUTE_REQUIRES if mode == "launch-plan" else REQUIRES
    lines, bad = [], False
    sess_name = session_harness_name(session_harness)
    sess_h = catalog["harnesses"].get(sess_name)
    if sess_name is None:
        lines.append(f"UNKNOWN session ? {NO_SESSION}")
        bad = True
    elif sess_h is None:
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
        why = why or lock_why(catalog, pair, sess_name)
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


def harness_row(name, h):
    """(value, label, subtitle): the name in title case, and the version under it."""
    version = str(h.get("version") or "").strip()
    return name, name.replace("-", " ").replace("_", " ").title(), f"v{version}" if version[:1].isdigit() else version


def menu(catalog, harness, model, session_harness=None):
    rows = []
    if harness is None:
        usable = {n: h for n, h in catalog["harnesses"].items() if not h.get("broken")}
        broken = [f"BROKEN {n}: {h['broken']}" for n, h in catalog["harnesses"].items() if h.get("broken")]
        oauth = oauth_harnesses(catalog)
        if oauth:
            sess = session_harness_name(session_harness)
            if sess is None:
                raise MenuError(f"UNKNOWN session ? {NO_SESSION}")
            if sess not in usable:
                raise MenuError(f"UNKNOWN session harness {sess} (usable: {', '.join(usable)}); {oauth_reason(oauth)}, "
                                "so every role runs on the session's harness")
            if len(usable) > 1:
                broken.append(f"LOCKED {sess}: {oauth_reason(oauth)}, so every role runs on this session's harness")
            usable = {sess: usable[sess]}
        rows = [harness_row(n, h) for n, h in usable.items()]
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
    return ["\t".join([str(i), *(col for col in row if col)]) for i, row in enumerate(rows, 1)] + broken


MAX_SELECTIONS = 3
ROLE_TITLES = {"director": "Flight Director", "safety": "Range Safety", "crew": "Crew"}


def read_selections(path):
    """The picks file's selections, most recently used first. A file from before it kept more than one holds the
    three picks at its top level: that is one selection."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("selections") if isinstance(data, dict) and isinstance(data.get("selections"), list) else [data]
    return [s for s in raw if isinstance(s, dict) and all(isinstance(s.get(r), str) for r in ROLES)]


def read_roles(path):
    """The latest selection: the picks of the run being set up (`save` put them first)."""
    found = read_selections(path)
    if not found:
        raise KeyError(f"no selection in {path}")
    return found[0]


def save_selection(path, picks):
    """Put picks first in the picks file. A selection already there keeps its entry and only moves up; a new one
    is added, and the least recently used beyond MAX_SELECTIONS is dropped. The first selection is also written at
    the file's top level, where a version from before selections were kept reads its one set of picks."""
    path = Path(path)
    try:
        old = read_selections(path) if path.is_file() else []
    except (OSError, ValueError):
        old = []  # an unreadable file is replaced

    def same(s):
        return all(s[r] == picks[r] for r in ROLES)
    entry = next((s for s in old if same(s)), None) or {
        **{r: picks[r] for r in ROLES},
        "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    kept = [entry] + [s for s in old if not same(s)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**entry, "selections": kept[:MAX_SELECTIONS]}, indent=1) + "\n", encoding="utf-8")


def usable_selections(path, catalog, sess_name):
    """The saved selections whose three picks the catalog has and the OAuth rule allows in this session."""
    def ok(text):
        p = parse_pair_or_none(text)
        return p is not None and not unknown_why(catalog, p) and not lock_why(catalog, p, sess_name)
    try:
        found = read_selections(path) if Path(path).is_file() else []
    except (OSError, ValueError):
        found = []
    return [s for s in found if all(ok(s[r]) for r in ROLES)]


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
    m.add_argument("--session-harness")
    c = sub.add_parser("check")
    c.add_argument("--catalog", required=True)
    c.add_argument("--jira", required=True, choices=["yes", "no"])
    c.add_argument("--mode", choices=["full", "launch-plan"], default="full")
    c.add_argument("--session-harness")
    add_picks(c)
    r = sub.add_parser("remembered")
    r.add_argument("--roles", required=True)
    r.add_argument("--catalog", required=True)
    r.add_argument("--session-harness")
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
                print("\n".join(menu(load_catalog(a.catalog), a.harness, a.model, a.session_harness)))
            except MenuError as e:
                print(e)
                return 1
        elif a.cmd == "check":
            lines, bad = check(load_catalog(a.catalog), a.jira == "yes", {x: getattr(a, x) for x in ROLES},
                               mode=a.mode, session_harness=a.session_harness)
            print("\n".join(lines))
            return 1 if bad else 0
        elif a.cmd == "remembered":
            found = usable_selections(a.roles, load_catalog(a.catalog), session_harness_name(a.session_harness))
            if not found:
                print("NONE")
                return 1
            rows = [(" ".join(f"{r}={s[r]}" for r in ROLES), f"Selection {i}",
                     ", ".join(f"{ROLE_TITLES[r]} {s[r]}" for r in ROLES)) for i, s in enumerate(found, 1)]
            rows.append(("new", "New", "Pick each role from the menus"))
            print("\n".join("\t".join([str(i), *row]) for i, row in enumerate(rows, 1)))
        elif a.cmd == "save":
            for x in ROLES:
                parse_pair(getattr(a, x))
            save_selection(a.roles, {x: getattr(a, x) for x in ROLES})
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
