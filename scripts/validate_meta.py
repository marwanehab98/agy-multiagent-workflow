"""Check the architect's per-task model choices against the run's allowlist and floors.

Usage:
  python3 -I validate_meta.py tasks --tasks-dir <DOCS_DIR>/tasks --gemini-model <GEMINI_MODEL> --ids "01 02 03" [--design <DESIGN_FILE>] [--replanned-from <id>]
  python3 -I validate_meta.py pair --pair <agent:model> --tier <standard|complex> --gemini-model <GEMINI_MODEL> --allow-opus <yes|no>

`tasks` checks each <id>.meta. `pair` checks one escalation attempt's implementer.
Exit 0: all valid (prints OK lines).  Exit 1: problems (prints one PROBLEM line each).  Exit 64: bad usage.

Rules enforced:
- Required keys: TITLE, TIER, SPEC_TYPE, ACS, IMPLEMENTER, REVIEW_1, REVIEW_2, LADDER (LADDER may be empty).
- ACS lists the acceptance criteria the task delivers (e.g. "AC-1 AC-3"), or "none".
- With --design, every AC-<n> in the design must be in some checked task's ACS, and every
  ACS entry must exist in the design. Pass it only when checking the whole queue.
- Implementer pairs: antigravity:<gemini>-high, antigravity:<gemini>-xhigh, claude:sonnet, claude:opus.
- claude:opus never as IMPLEMENTER; in LADDER only as the last rung and only for TIER=complex.
- Reviewer pairs: claude:sonnet or claude:opus.
- Review floors: complex needs REVIEW_2=claude:opus; complex + judgment also needs REVIEW_1=claude:opus.
- LADDER has at most 3 rungs.
- With --replanned-from <id>, every checked task needs REPLANNED_FROM=<id>, and together
  they must cover every AC in the replaced task's ACS.
"""
import argparse
import re
import sys
from pathlib import Path

REQUIRED = ["TITLE", "TIER", "SPEC_TYPE", "ACS", "IMPLEMENTER", "REVIEW_1", "REVIEW_2", "LADDER"]
AC = re.compile(r"\bAC-\d+\b")
REVIEWERS = {"claude:sonnet", "claude:opus"}
OPUS = "claude:opus"


def implementers(gemini):
    return {f"antigravity:{gemini}-high", f"antigravity:{gemini}-xhigh", "claude:sonnet", OPUS}


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


def check_task(tid, meta, gemini):
    p = []
    for k in REQUIRED:
        if k not in meta:
            p.append(f"{tid}: missing {k}")
    if p:
        return p
    tier, spec = meta["TIER"], meta["SPEC_TYPE"]
    if tier not in ("standard", "complex"):
        p.append(f"{tid}: TIER must be standard or complex, got {tier!r}")
    if spec not in ("checklist", "judgment"):
        p.append(f"{tid}: SPEC_TYPE must be checklist or judgment, got {spec!r}")
    allowed = implementers(gemini)
    impl = meta["IMPLEMENTER"]
    if impl not in allowed:
        p.append(f"{tid}: IMPLEMENTER {impl!r} not allowed; use one of {sorted(allowed - {OPUS})}")
    elif impl == OPUS:
        p.append(f"{tid}: IMPLEMENTER may not be {OPUS}; Opus implements only as the last LADDER rung")
    for k in ("REVIEW_1", "REVIEW_2"):
        if meta[k] not in REVIEWERS:
            p.append(f"{tid}: {k} {meta[k]!r} not allowed; use claude:sonnet or claude:opus")
    if tier == "complex" and meta["REVIEW_2"] != OPUS:
        p.append(f"{tid}: complex tasks need REVIEW_2={OPUS}")
    if tier == "complex" and spec == "judgment" and meta["REVIEW_1"] != OPUS:
        p.append(f"{tid}: complex judgment tasks need REVIEW_1={OPUS}")
    acs = meta["ACS"].split()
    if acs != ["none"] and (not acs or any(not AC.fullmatch(x) for x in acs)):
        p.append(f"{tid}: ACS must be AC-<n> ids separated by spaces, or none; got {meta['ACS']!r}")
    ladder = meta["LADDER"].split()
    if len(ladder) > 3:
        p.append(f"{tid}: LADDER has {len(ladder)} rungs; at most 3")
    for i, rung in enumerate(ladder):
        if rung not in allowed:
            p.append(f"{tid}: LADDER rung {i + 1} {rung!r} not allowed")
        elif rung == OPUS and (i != len(ladder) - 1 or tier != "complex"):
            p.append(f"{tid}: {OPUS} may only be the last LADDER rung of a complex task")
    return p


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("tasks")
    t.add_argument("--tasks-dir", required=True)
    t.add_argument("--gemini-model", required=True)
    t.add_argument("--ids", required=True)
    t.add_argument("--replanned-from")
    t.add_argument("--design")
    q = sub.add_parser("pair")
    q.add_argument("--pair", required=True)
    q.add_argument("--tier", required=True, choices=["standard", "complex"])
    q.add_argument("--gemini-model", required=True)
    q.add_argument("--allow-opus", required=True, choices=["yes", "no"])
    a = ap.parse_args()

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
                problems += check_task(tid, meta, a.gemini_model)
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
    else:
        allowed = implementers(a.gemini_model)
        if a.pair not in allowed:
            problems.append(f"pair {a.pair!r} not allowed; use one of {sorted(allowed)}")
        elif a.pair == OPUS and a.allow_opus != "yes":
            problems.append(f"{OPUS} is only allowed as a complex task's last LADDER rung, or after the user approved it")
        if not problems:
            print(f"OK {a.pair}")

    for x in problems:
        print(f"PROBLEM {x}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
