"""Check the flight director's per-task model choices against the run's allowlist and floors.

Usage (picks and catalog, read from the run's state file: CATALOG, ROLE_DIRECTOR, ROLE_CREW, CREW_TOP):
  python3 -I validate_meta.py tasks --state <S> --tasks-dir <DOCS_DIR>/tasks --ids "01 02 03" [--design <DESIGN_FILE>] [--replanned-from <id>]
  python3 -I validate_meta.py pair --state <S> --task-meta <F> --pair <P> --attempt <N> --past-ladder <yes|no>
  python3 -I validate_meta.py harness-pair --state <S> --pair <P>

`tasks` checks each <id>.meta. `pair` checks one escalation attempt: with `no` the pair must be rung N of
ladder(ROLE_CREW, CREW_TOP, ROLE_DIRECTOR, <CREW from F>); with `yes` any pair in the catalog passes.
`harness-pair` checks a pair is in the flight director's harness with a detected model and effort (preflight check,
GROUND_CREW_PAIR, reviewers).
Exit 0: all valid (prints OK lines).  Exit 1: problems (prints one PROBLEM line each).  Exit 64: bad usage.

Rules enforced by the --state modes (design 2.1):
- Required keys: TITLE, TIER, SPEC_TYPE, ACS, CREW, REVIEW_1, REVIEW_2. A LADDER key is a problem.
- CREW is ROLE_CREW or its model at CREW_TOP, and never the exact ROLE_DIRECTOR pair.
- Reviewers are in the flight director's harness, with a detected model and effort.
- complex: REVIEW_2 has the flight director's harness and model (any effort); complex judgment: REVIEW_1 too.
- ACS lists the acceptance criteria the task delivers (e.g. "AC-1 AC-3"), or "none".
- With --design, every AC-<n> in the design must be in some checked task's ACS, and every
  ACS entry must exist in the design. Pass it only when checking the whole queue.
- With --replanned-from <id>, every checked task needs REPLANNED_FROM=<id>, and together
  they must cover every AC in the replaced task's ACS.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from pairs import Pair, ladder, parse_pair  # noqa: E402
from roles import unknown_why  # noqa: E402

REQUIRED = ["TITLE", "TIER", "SPEC_TYPE", "ACS", "CREW", "REVIEW_1", "REVIEW_2"]
AC = re.compile(r"\bAC-\d+\b")


def read_meta(path):
    values = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if "=" not in line:
            raise ValueError(f"line {n} is not KEY=VALUE: {line!r}")
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


class Picks:
    """The run's catalog and role picks, read from a state file."""

    def __init__(self, state_path):
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        try:
            self.catalog = json.loads(Path(state["CATALOG"]).read_text(encoding="utf-8"))
            self.director = parse_pair(state["ROLE_DIRECTOR"])
            self.crew = parse_pair(state["ROLE_CREW"])
            self.crew_top = state["CREW_TOP"]
        except KeyError as e:
            raise ValueError(f"state file lacks {e.args[0]}")
        raw_repos = (state.get("REPOS") or "").strip()
        self.repos = [] if not raw_repos or raw_repos == "none" else raw_repos.split()


def own_harness_problem(picks, label, text):
    """None when `text` is a pair in the flight director's harness with a detected model and effort."""
    try:
        pair = parse_pair(text)
    except ValueError as e:
        return f"{label} {text!r}: {e}"
    if pair.harness != picks.director.harness:
        return f"{label} {text!r} must be in the flight director's harness ({picks.director.harness})"
    why = unknown_why(picks.catalog, pair)
    return f"{label} {text!r}: {why}" if why else None


def check_task(tid, meta, picks):
    p = [f"{tid}: missing {k}" for k in REQUIRED if k not in meta]
    if picks.repos:
        if "REPO" not in meta or not meta["REPO"]:
            p.append(f"{tid}: missing REPO (multi-repo run; must be one of {' '.join(picks.repos)})")
        elif meta["REPO"] not in picks.repos:
            p.append(f"{tid}: REPO {meta['REPO']!r} not in run REPOS ({' '.join(picks.repos)})")
    if "LADDER" in meta:
        p.append(f"{tid}: LADDER is no longer used; delete it")
    if p and any("missing" in x for x in p):
        return p
    tier, spec = meta["TIER"], meta["SPEC_TYPE"]
    if tier not in ("standard", "complex"):
        p.append(f"{tid}: TIER must be standard or complex, got {tier!r}")
    if spec not in ("checklist", "judgment"):
        p.append(f"{tid}: SPEC_TYPE must be checklist or judgment, got {spec!r}")
    director, crew = picks.director, picks.crew
    crew_val = meta["CREW"]
    top = Pair(crew.harness, crew.model, picks.crew_top)
    try:
        cpair = parse_pair(crew_val)
    except ValueError as e:
        p.append(f"{tid}: CREW {crew_val!r}: {e}")
    else:
        if cpair == director:
            p.append(f"{tid}: CREW {crew_val!r} is the flight director pair, which never implements first")
        elif cpair not in (crew, top):
            p.append(f"{tid}: CREW {crew_val!r} not allowed; use {crew} or {top}")
    for k in ("REVIEW_1", "REVIEW_2"):
        msg = own_harness_problem(picks, k, meta[k])
        if msg:
            p.append(f"{tid}: {msg}")
    director_model = f"{director.harness}:{director.model}"

    def needs_director_model(k):
        try:
            rp = parse_pair(meta[k])
        except ValueError:
            return  # already reported
        if (rp.harness, rp.model) != (director.harness, director.model):
            p.append(f"{tid}: {k} {meta[k]!r} must use the flight director's model {director_model} (any effort)")

    if tier == "complex":
        needs_director_model("REVIEW_2")
        if spec == "judgment":
            needs_director_model("REVIEW_1")
    acs = meta["ACS"].split()
    if acs != ["none"] and (not acs or any(not AC.fullmatch(x) for x in acs)):
        p.append(f"{tid}: ACS must be AC-<n> ids separated by spaces, or none; got {meta['ACS']!r}")
    return p


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("tasks")
    t.add_argument("--state", required=True)
    t.add_argument("--tasks-dir", required=True)
    t.add_argument("--ids", required=True)
    t.add_argument("--replanned-from")
    t.add_argument("--design")
    q = sub.add_parser("pair")
    q.add_argument("--state", required=True)
    q.add_argument("--task-meta", required=True)
    q.add_argument("--pair", required=True)
    q.add_argument("--attempt", type=int, required=True)
    q.add_argument("--past-ladder", choices=["yes", "no"], required=True)
    h = sub.add_parser("harness-pair")
    h.add_argument("--state", required=True)
    h.add_argument("--pair", required=True)
    try:
        a = ap.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    try:
        picks = Picks(a.state)
    except (ValueError, OSError) as e:
        print(f"PROBLEM state: {e}")
        return 1

    problems = []
    if a.mode == "tasks":
        covered = set()
        for tid in a.ids.split():
            path = Path(a.tasks_dir) / f"{tid}.meta"
            if not path.is_file():
                problems.append(f"{tid}: {path} not found")
                continue
            for other in (f"{tid}.md", f"{tid}-summary.md", f"{tid}-decisions.md"):
                if not (Path(a.tasks_dir) / other).is_file():
                    problems.append(f"{tid}: {other} not found")
            try:
                meta = read_meta(path)
                problems += check_task(tid, meta, picks)
                covered |= {x for x in meta.get("ACS", "").split() if AC.fullmatch(x)}
                if a.replanned_from and meta.get("REPLANNED_FROM") != a.replanned_from:
                    problems.append(f"{tid}: needs REPLANNED_FROM={a.replanned_from}")
            except ValueError as e:
                problems.append(f"{tid}: {e}")
            if not any(x.startswith(f"{tid}:") for x in problems):
                print(f"OK {tid}")
        if a.design:
            design = set(AC.findall(Path(a.design).read_text(encoding="utf-8")))
            if not design:
                problems.append(f"design: no AC-<n> ids found in {a.design}")
            for ac in sorted(design - covered, key=lambda x: int(x[3:])):
                problems.append(f"coverage: {ac} is in the design but in no task's ACS")
            for ac in sorted(covered - design, key=lambda x: int(x[3:])):
                problems.append(f"coverage: {ac} is in a task's ACS but not in the design")
        if a.replanned_from:
            orig = Path(a.tasks_dir) / f"{a.replanned_from}.meta"
            if orig.is_file():
                before = {x for x in read_meta(orig).get("ACS", "").split() if AC.fullmatch(x)}
                for ac in sorted(before - covered, key=lambda x: int(x[3:])):
                    problems.append(f"coverage: {ac} was in {a.replanned_from}'s ACS but in none of the new tasks")
    elif a.mode == "harness-pair":
        msg = own_harness_problem(picks, "pair", a.pair)
        if msg:
            problems.append(msg)
        else:
            print(f"OK {a.pair}")
    else:
        try:
            pair = parse_pair(a.pair)
            first = parse_pair(read_meta(Path(a.task_meta))["CREW"])
        except (ValueError, KeyError, OSError) as e:
            problems.append(f"pair {a.pair!r}: {e}")
        else:
            if a.past_ladder == "yes":
                why = unknown_why(picks.catalog, pair)
                if why:
                    problems.append(f"pair {a.pair!r}: {why}")
            else:
                rungs = ladder(picks.crew, picks.crew_top, picks.director, first)
                if a.attempt < 1 or a.attempt > len(rungs):
                    problems.append(f"attempt {a.attempt} is past the ladder ({' '.join(map(str, rungs))}); "
                                    "only a user-approved pair may follow (--past-ladder yes)")
                elif pair != rungs[a.attempt - 1]:
                    problems.append(f"pair {a.pair} is not rung {a.attempt} of the ladder ({' '.join(map(str, rungs))})")
        if not problems:
            print(f"OK {a.pair}")

    for x in problems:
        print(f"PROBLEM {x}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
