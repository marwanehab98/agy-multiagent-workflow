"""Run the run's check commands in a worktree, log every result, then put the worktree
back exactly at <head>, so build or test output never voids a later review.

Usage:
  python3 -I gate.py --run-values <DOCS_DIR>/run-values.txt --worktree <path> --head <sha>
      --log <file> [--prefix GATE|VERIFY] [--skip "<name> <name>..."]

--prefix GATE (the default) runs GATE_1, GATE_2, ... and falls back to VERIFY_1, ...
when run-values has no GATE_ lines. --prefix VERIFY runs only VERIFY_ lines.
--log may contain {n}: it becomes the first number with no existing file, so a rerun never
overwrites an earlier log. --skip names checks (e.g. "VERIFY_1 VERIFY_2", from run-values'
BASELINE_SKIP) that are logged as skipped and not run, when they are among the chosen checks;
a name no GATE_ or VERIFY_ line has is bad usage, so one --skip serves both baseline runs.
Each command runs with the shell, from the worktree root. All commands run even after
one fails, so the log shows every failure at once.
Exit 0: every command passed and the worktree is clean at <head>.
Exit 1: a command failed (the log has its output); the worktree is clean at <head>.
Exit 2: the worktree could not be restored to <head> (prints why).  Exit 64: bad usage.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

LINE = re.compile(r"(GATE|VERIFY)_(\d+)=(.*)")
DEFAULT_GATE_TIMEOUT = 1800


def _timeout():
    raw = os.environ.get("GATE_TIMEOUT")
    if raw is None or raw.strip() == "":
        return DEFAULT_GATE_TIMEOUT
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_GATE_TIMEOUT
    return None if val <= 0 else val


def _line_re(repo=None):
    if repo:
        return re.compile(rf"(GATE|VERIFY)_{re.escape(repo)}_(\d+)=(.*)")
    return LINE


def commands(run_values, prefix, repo=None):
    pat = _line_re(repo)
    found = {"GATE": [], "VERIFY": []}
    for line in Path(run_values).read_text(encoding="utf-8").splitlines():
        m = pat.fullmatch(line.strip())
        if m and m.group(3).strip():
            name = f"{m.group(1)}_{repo}_{m.group(2)}" if repo else f"{m.group(1)}_{m.group(2)}"
            found[m.group(1)].append((int(m.group(2)), name, m.group(3).strip()))
    chosen = found["GATE"] if prefix == "GATE" and found["GATE"] else found["VERIFY"]
    return sorted(chosen)


def commands_all(run_values, prefix, repo=None):
    """Every check with this prefix, with no fallback (for validating --skip names)."""
    tag = f"{prefix}_{repo}_" if repo else f"{prefix}_"
    return [c for c in commands(run_values, prefix, repo=repo) if c[1].startswith(tag)]


def git(wt, *args):
    return subprocess.run(["git", "-C", wt, *args], capture_output=True, text=True, errors="replace")


def _run_cmd(cmd, cwd, timeout):
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        out_s, err_s = proc.communicate(timeout=timeout)
        return proc.returncode, out_s, err_s
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            out_s, err_s = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_s, err_s = "", ""
        return None, out_s or "", err_s or ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-values", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--prefix", choices=["GATE", "VERIFY"], default="GATE")
    p.add_argument("--skip", default="")
    p.add_argument("--repo", default="")
    a = p.parse_args()
    repo = a.repo.strip() or None

    cmds = commands(a.run_values, a.prefix, repo=repo)
    if not cmds:
        tag = f"{a.prefix}_{repo}_n or VERIFY_{repo}_n" if repo else f"{a.prefix}_n or VERIFY_n"
        print(f"NO_COMMANDS no {tag} lines in {a.run_values}")
        return 64
    skip = a.skip.split()
    every = [name for prefix in ("GATE", "VERIFY") for _, name, _ in commands_all(a.run_values, prefix, repo=repo)]
    unknown = [s for s in skip if s not in every]
    if unknown:
        print(f"UNKNOWN_SKIP {' '.join(unknown)} (checks: {' '.join(every)})")
        return 64
    head = git(a.worktree, "rev-parse", "HEAD").stdout.strip()
    want = git(a.worktree, "rev-parse", f"{a.head}^{{commit}}").stdout.strip()
    if not want or head != want:
        print(f"HEAD_MOVED expected={a.head} actual={head}")
        return 2
    pre_dirty = git(a.worktree, "status", "--porcelain", "--untracked-files=all").stdout.strip()
    if pre_dirty:
        print(f"TREE_DIRTY\n{pre_dirty}")
        return 2

    log = Path(a.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    if "{n}" in log.name:
        n = 1
        while (log.parent / log.name.format(n=n)).exists():
            n += 1
        log = log.parent / log.name.format(n=n)
    timeout = _timeout()
    failed = []
    with log.open("w", encoding="utf-8") as f:
        f.write(f"worktree={a.worktree} head={want}\n")
        for _, name, cmd in cmds:
            if name in skip:
                f.write(f"\n=== {name} skipped (BASELINE_SKIP): {cmd}\n")
                print(f"{name} skipped")
                continue
            rc, out_s, err_s = _run_cmd(cmd, a.worktree, timeout)
            if rc is None:
                f.write(f"\n=== {name}: {cmd}\n{out_s}{err_s}\n=== {name} timed out after {timeout}s\n")
                print(f"{name} timed out")
                failed.append(name)
            else:
                f.write(f"\n=== {name}: {cmd}\n{out_s}{err_s}\n=== {name} exit={rc}\n")
                print(f"{name} exit={rc}")
                if rc != 0:
                    failed.append(name)

    # Build and test output that is not gitignored would look like a reviewer's edit later.
    git(a.worktree, "reset", "--hard", want)
    git(a.worktree, "clean", "-ffd")
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
