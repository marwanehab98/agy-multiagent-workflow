"""Run the run's check commands in a worktree, log every result, then put the worktree
back exactly at <head>, so build or test output never voids a later review.

Usage:
  python3 -I gate.py --run-values <DOCS_DIR>/run-values.txt --worktree <path> --head <sha>
      --log <file> [--prefix GATE|VERIFY]

--prefix GATE (the default) runs GATE_1, GATE_2, ... and falls back to VERIFY_1, ...
when run-values has no GATE_ lines. --prefix VERIFY runs only VERIFY_ lines.
Each command runs with the shell, from the worktree root. All commands run even after
one fails, so the log shows every failure at once.
Exit 0: every command passed and the worktree is clean at <head>.
Exit 1: a command failed (the log has its output); the worktree is clean at <head>.
Exit 2: the worktree could not be restored to <head> (prints why).  Exit 64: bad usage.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

LINE = re.compile(r"(GATE|VERIFY)_(\d+)=(.*)")


def commands(run_values, prefix):
    found = {"GATE": [], "VERIFY": []}
    for line in Path(run_values).read_text(encoding="utf-8").splitlines():
        m = LINE.fullmatch(line.strip())
        if m and m.group(3).strip():
            found[m.group(1)].append((int(m.group(2)), f"{m.group(1)}_{m.group(2)}", m.group(3).strip()))
    chosen = found["GATE"] if prefix == "GATE" and found["GATE"] else found["VERIFY"]
    return sorted(chosen)


def git(wt, *args):
    return subprocess.run(["git", "-C", wt, *args], capture_output=True, text=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-values", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--prefix", choices=["GATE", "VERIFY"], default="GATE")
    a = p.parse_args()

    cmds = commands(a.run_values, a.prefix)
    if not cmds:
        print(f"NO_COMMANDS no {a.prefix}_n or VERIFY_n lines in {a.run_values}")
        return 64
    head = git(a.worktree, "rev-parse", "HEAD").stdout.strip()
    want = git(a.worktree, "rev-parse", f"{a.head}^{{commit}}").stdout.strip()
    if not want or head != want:
        print(f"HEAD_MOVED expected={a.head} actual={head}")
        return 2

    log = Path(a.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    failed = []
    with log.open("w", encoding="utf-8") as f:
        f.write(f"worktree={a.worktree} head={want}\n")
        for _, name, cmd in cmds:
            r = subprocess.run(cmd, shell=True, cwd=a.worktree, capture_output=True, text=True)
            f.write(f"\n=== {name}: {cmd}\n{r.stdout}{r.stderr}\n=== {name} exit={r.returncode}\n")
            print(f"{name} exit={r.returncode}")
            if r.returncode != 0:
                failed.append(name)

    # Build and test output that is not gitignored would look like a reviewer's edit later.
    git(a.worktree, "reset", "--hard", want)
    git(a.worktree, "clean", "-fd")
    dirty = git(a.worktree, "status", "--porcelain", "--untracked-files=all").stdout.strip()
    if dirty or git(a.worktree, "rev-parse", "HEAD").stdout.strip() != want:
        print(f"NOT_RESTORED\n{dirty}")
        return 2
    if failed:
        print(f"FAILED {' '.join(failed)} log={log}")
        return 1
    print(f"PASSED log={log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
