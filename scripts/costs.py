"""Total a run's worker costs from <RUN_DIR>/progress.log, for the closing report.

Usage:
  python3 -I costs.py --state <RUN_DIR>/state.json [--out <file>]

Reads every worker's finish line ("[worker +Ns] <tag>: done (...) pair=<pair> cost=$<x> turns=<n> ...") and writes a
markdown summary: the total, then by role and by pair, with tokens where the harness reports them (agy reports
tokens but no cost). Roles come from the worker's tag: crew (implement, fix,
commit, attempt), reviewers (stage 1 and 2), Flight Director, Range Safety and design workers, final review, drift
check, test launches; a resumed worker is "resumed". A resumed worker's finish line carries only that turn's share of
the conversation's cost (common.py), so the total is the real total. Harnesses that report no cost count as unknown.
Prints the summary; with --out also writes it there. Exit 0, or 64 on bad usage.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

LINE = re.compile(r"\[worker \+\d+s\] (?P<tag>\S+): "
                  r"(?P<what>done|stopped|timed out|paused|launch failed|usage limit|resume failed)(?P<rest>.*)$")
# First match wins: the fixed prefixes before the task patterns, which accept any task id (01, F1, 01a).
ROLES = [
    (r"^director-", "Flight Director"),
    (r"^(preflight-check|safety|lens|plan-review)-\d+$", "Range Safety and design reviews"),
    (r"^final-review-\d+$", "Final review"),
    (r"^drift-\d+$", "Drift check"),
    (r"^probe-", "Test launches"),
    (r"^[A-Za-z0-9][A-Za-z0-9_-]*-(implement|fix-\d+|commit-\d+|attempt\d+)$", "Crew"),
    (r"^[A-Za-z0-9][A-Za-z0-9_-]*-s[12]-\d+$", "Task reviewers"),
    (r"-resume(-\d+)?$", "Resumed workers"),
]


def role(tag):
    for pat, name in ROLES:
        if re.search(pat, tag):
            return name
    return "Other"


def summarize(progress_log):
    # Per row: cost, runs, turns, tokens, and how many of its runs reported a cost.
    by_role, by_pair = defaultdict(lambda: [0.0, 0, 0, 0, 0]), defaultdict(lambda: [0.0, 0, 0, 0, 0])
    total, workers, unknown, total_tokens = 0.0, 0, 0, 0
    for line in Path(progress_log).read_text(encoding="utf-8").splitlines():
        m = LINE.search(line)
        if not m:
            continue
        rest = m.group("rest")
        cost = re.search(r"cost=\$([0-9.]+)", rest)
        turns = re.search(r"turns=(\d+)", rest)
        pair = re.search(r"pair=(\S+)", rest)
        tokens = re.search(r"tokens=(\d+)", rest)
        tk = int(tokens.group(1)) if tokens else 0
        total_tokens += tk
        workers += 1
        c = float(cost.group(1)) if cost else 0.0
        unknown += 0 if cost else 1
        total += c
        for key, table in ((role(m.group("tag")), by_role), (pair.group(1) if pair else "unknown", by_pair)):
            table[key][0] += c
            table[key][1] += 1
            table[key][2] += int(turns.group(1)) if turns else 0
            table[key][3] += tk
            table[key][4] += 1 if cost else 0
    if unknown == workers and total_tokens:
        out = [f"Total: {total_tokens:,} tokens across {workers} worker runs (the harness reports tokens, not cost).", ""]
    else:
        out = [f"Total: ${total:.2f} across {workers} worker runs"
               + (f" ({unknown} reported no cost)" if unknown else "")
               + (f"; {total_tokens:,} tokens where reported" if total_tokens else "") + ".", ""]
    show_cost = unknown < workers
    for name, table in (("Role", by_role), ("Pair", by_pair)):
        out += [f"| {name} |" + (" Cost |" if show_cost else "") + " Tokens | Runs | Turns |",
                "| --- |" + (" ---: |" if show_cost else "") + " ---: | ---: | ---: |"]
        # A row none of whose runs reported a cost shows n/a, not $0.00: a run that moved to a harness that
        # reports only tokens was not free.
        out += [f"| {k} |" + ((f" ${v[0]:.2f} |" if v[4] else " n/a |") if show_cost else "")
                + f" {v[3]:,} | {v[1]} | {v[2]} |"
                for k, v in sorted(table.items(), key=lambda kv: (-kv[1][0], -kv[1][3]))]
        out.append("")
    out = out[:-1]
    return "\n".join(out) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--out")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    run_dir = Path(json.loads(Path(a.state).read_text(encoding="utf-8"))["RUN_DIR"])
    log = run_dir / "progress.log"
    text = summarize(log) if log.is_file() else "No worker costs recorded.\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
