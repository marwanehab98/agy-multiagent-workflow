"""Manage multi-repo workspaces for mission-control.

Usage:
  python3 -I workspace.py detect --dir <cwd>
  python3 -I workspace.py check-topic --workspace <dir> --topic <topic> --repo <name>=<path> [...]
  python3 -I workspace.py init-docs --workspace <dir> --topic <topic> --repo <name>=<path>@<sha> [...]
  python3 -I workspace.py init-impl --state <RUN_DIR>/state.json
  python3 -I workspace.py remove-impl --state <RUN_DIR>/state.json
  python3 -I workspace.py remove-docs --state <RUN_DIR>/state.json
  python3 -I workspace.py list-runs --workspace <dir>

Exit 0: ok.  Exit 10: EXISTING run found (check-topic).  Exit 1: git/workspace failure.  Exit 64: bad usage.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import Run, git, repo_key, run, tool  # noqa: E402

EXCLUDES = [
    ".run/",
    ".worktrees/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "node_modules/",
    ".DS_Store",
]


def _default_branch(repo_path):
    r = run(["git", "-C", str(repo_path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if r.returncode == 0 and r.stdout.strip():
        ref = r.stdout.strip()
        return ref[len("origin/"):] if ref.startswith("origin/") else ref
    return git(str(repo_path), "branch", "--show-current") or ""


def _current_branch(repo_path):
    return git(str(repo_path), "branch", "--show-current") or ""


def _append_excludes(git_common_dir, lines):
    exclude = Path(git_common_dir) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = set(exclude.read_text(encoding="utf-8").splitlines()) if exclude.exists() else set()
    to_add = [line for line in lines if line not in existing]
    if to_add:
        with exclude.open("a", encoding="utf-8") as f:
            if existing and not exclude.read_text(encoding="utf-8").endswith("\n"):
                f.write("\n")
            for line in to_add:
                f.write(line + "\n")


def _read_kv(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


SKIP_DIRS = {"node_modules", "vendor", "target", "dist", "build", "venv", "__pycache__"}
DEFAULT_MAX_DEPTH = 4


def _discover_repos(root, max_depth=DEFAULT_MAX_DEPTH):
    """Find git repos under `root` up to `max_depth`, pruning descent into discovered repos."""
    found = []

    def walk(cur, depth):
        if depth > max_depth:
            return
        try:
            children = sorted(cur.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in SKIP_DIRS or child.is_symlink() or not child.is_dir():
                continue
            if (child / ".git").exists():
                child_top = git(str(child), "rev-parse", "--show-toplevel")
                if child_top and Path(child_top).resolve() == child.resolve():
                    rel = child.relative_to(root).as_posix()
                    found.append((rel, child.resolve()))
                    continue  # don't descend into nested repos/submodules
            walk(child, depth + 1)

    walk(root, 1)
    return found


def cmd_detect(args):
    cwd = Path(args.dir).resolve()
    top = git(str(cwd), "rev-parse", "--show-toplevel")
    if top:
        top_path = Path(top).resolve()
        print("MODE single")
        print(f"MAIN_CHECKOUT {top_path}")
        print(f"DEFAULT_BRANCH {_default_branch(top_path)}")
        print(f"CURRENT_BRANCH {_current_branch(top_path)}")
        return 0

    repos = []
    seen_keys = {}
    for rel, path in _discover_repos(cwd, max_depth=args.max_depth):
        k = repo_key(rel)
        if k in seen_keys:
            print(f"ERROR repo key collision: {seen_keys[k]} and {rel} both map to {k}", file=sys.stderr)
            return 1
        seen_keys[k] = rel
        repos.append((rel, k, path, _default_branch(path), _current_branch(path)))

    if not repos:
        print("MODE none")
        return 1

    print("MODE multi")
    print(f"WORKSPACE_DIR {cwd}")
    for name, k, path, default_b, cur_b in repos:
        print(f"REPO {name} {k} {path} {default_b or '-'} {cur_b or '-'}")
    return 0


def _parse_repo_specs(specs, require_sha=False):
    parsed = []
    seen_keys = set()
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"bad --repo spec {spec!r}; expected name=path" + ("@sha" if require_sha else ""))
        name, rest = spec.split("=", 1)
        if require_sha:
            if "@" not in rest:
                raise ValueError(f"bad --repo spec {spec!r}; expected name=path@sha")
            path_s, sha = rest.rsplit("@", 1)
        else:
            path_s, sha = rest, None
        k = repo_key(name)
        if k in seen_keys:
            raise ValueError(f"duplicate repo key {k!r} for {name!r}")
        seen_keys.add(k)
        parsed.append((name, k, Path(path_s).resolve(), sha))
    return parsed


def _branch_exists(repo_path, branch):
    return run(["git", "-C", str(repo_path), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0


def cmd_check_topic(args):
    ws = Path(args.workspace).resolve()
    mc = ws / ".mission-control"
    try:
        repos = _parse_repo_specs(args.repo, require_sha=False)
    except ValueError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 64

    base_docs = mc / f"{args.topic}-docs"
    if not args.force_new and (base_docs / ".run" / "state.json").is_file():
        print(f"EXISTING {base_docs}")
        return 10

    def topic_taken(t):
        if (mc / f"{t}-docs").exists() or (mc / f"{t}-impl").exists():
            return True
        for _, _, path, _ in repos:
            if _branch_exists(path, f"{t}-docs") or _branch_exists(path, f"{t}-impl"):
                return True
        return False

    chosen = args.topic
    if args.force_new or topic_taken(chosen):
        k = 2
        while topic_taken(f"{args.topic}-{k}"):
            k += 1
        chosen = f"{args.topic}-{k}"
    print(f"TOPIC {chosen}")
    return 0


def cmd_init_docs(args):
    ws = Path(args.workspace).resolve()
    try:
        repos = _parse_repo_specs(args.repo, require_sha=True)
    except ValueError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 64
    if not repos:
        print("ERROR at least one --repo is required", file=sys.stderr)
        return 64

    docs_root = ws / ".mission-control" / f"{args.topic}-docs"
    if docs_root.exists():
        print(f"ERROR docs workspace already exists: {docs_root}", file=sys.stderr)
        return 1
    docs_root.mkdir(parents=True, exist_ok=True)

    r = run(["git", "init", "-q", "-b", f"{args.topic}-docs", str(docs_root)])
    if r.returncode != 0:
        print(f"ERROR git init failed: {r.stderr.strip()}", file=sys.stderr)
        return 1

    # Ensure initial commit succeeds even if global git identity is unset.
    r = run([
        "git", "-C", str(docs_root),
        "-c", "user.name=mission-control", "-c", "user.email=mission-control@local",
        "commit", "-q", "--allow-empty", "-m", "init docs workspace",
    ])
    if r.returncode != 0:
        print(f"ERROR initial commit failed: {r.stderr.strip()}", file=sys.stderr)
        return 1

    docs_common = git(str(docs_root), "rev-parse", "--path-format=absolute", "--git-common-dir")
    _append_excludes(docs_common, EXCLUDES + [f"{name}/" for name, _, _, _ in repos])

    created_wts = []
    for name, k, repo_path, sha in repos:
        wt_path = docs_root / name
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        r = run(["git", "-C", str(repo_path), "worktree", "add", "-b", f"{args.topic}-docs", str(wt_path), sha])
        if r.returncode != 0:
            # Best-effort rollback of already-created repo worktrees.
            for prev_repo, prev_wt in created_wts:
                run(["git", "-C", str(prev_repo), "worktree", "remove", "--force", str(prev_wt)])
                run(["git", "-C", str(prev_repo), "branch", "-D", f"{args.topic}-docs"])
            shutil.rmtree(docs_root, ignore_errors=True)
            print(f"ERROR worktree add for {name}: {(r.stdout + r.stderr).strip()}", file=sys.stderr)
            return 1
        created_wts.append((repo_path, wt_path))
        rc, out = tool("check-tree.sh", str(wt_path), sha)
        if rc == 1:
            tool("discard-changes.sh", str(wt_path), sha)
        elif rc != 0:
            print(f"ERROR check-tree for {name}: {out}", file=sys.stderr)
            return 1
        common_dir = git(str(repo_path), "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common_dir:
            _append_excludes(common_dir, EXCLUDES)

    docs_dir = docs_root / "docs" / "plans"
    run_dir = docs_root / ".run"
    relay_dir = run_dir / "relay"
    for d in (docs_dir, relay_dir, run_dir / "specs", run_dir / "verdicts",
              run_dir / "asks", run_dir / "gates", run_dir / "results"):
        d.mkdir(parents=True, exist_ok=True)

    print(f"DOCS_WORKTREE {docs_root}")
    print(f"DOCS_DIR {docs_dir}")
    print(f"RUN_DIR {run_dir}")
    print(f"RELAY_DIR {relay_dir}")
    for name, k, _, _ in repos:
        print(f"DOCS_WT_{k} {docs_root / name}")
    return 0


def cmd_init_impl(args):
    r = Run(args.state)
    if not r.is_multi_repo:
        print("ERROR state is not a multi-repo run", file=sys.stderr)
        return 64

    ws = Path(r.need("WORKSPACE_DIR")).resolve()
    topic = r.need("TOPIC")
    rv = _read_kv(r.need("FLIGHT_MANIFEST"))
    impl_root = ws / ".mission-control" / f"{topic}-impl"
    impl_root.mkdir(parents=True, exist_ok=True)

    updates = {"IMPL_WORKTREE": str(impl_root)}
    for name in r.repos:
        k = repo_key(name)
        repo_path = r.need(f"REPO_PATH_{k}")
        start_sha = r.need(f"START_SHA_{k}")
        fb = rv.get(f"FEATURE_BRANCH_{k}") or rv.get("FEATURE_BRANCH")
        if not fb:
            print(f"ERROR missing FEATURE_BRANCH (or FEATURE_BRANCH_{k}) in {r.need('FLIGHT_MANIFEST')}", file=sys.stderr)
            return 1
        pt = rv.get(f"PR_TARGET_{k}") or r.need(f"BASE_BRANCH_{k}")

        res = run(["git", "-C", repo_path, "branch", fb, start_sha])
        if res.returncode != 0:
            print(f"ERROR git branch {fb} in {name}: {(res.stdout + res.stderr).strip()}", file=sys.stderr)
            return 1

        wt_path = impl_root / name
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        res = run(["git", "-C", repo_path, "worktree", "add", "-b", f"{topic}-impl", str(wt_path), fb])
        if res.returncode != 0:
            print(f"ERROR git worktree add in {name}: {(res.stdout + res.stderr).strip()}", file=sys.stderr)
            return 1

        rc, out = tool("check-tree.sh", str(wt_path), start_sha)
        if rc != 0:
            print(f"ERROR check-tree for {name}: {out}", file=sys.stderr)
            return 1

        updates[f"IMPL_WT_{k}"] = str(wt_path)
        updates[f"FEATURE_BRANCH_{k}"] = fb
        updates[f"PR_TARGET_{k}"] = pt
        updates[f"INITIAL_BASE_SHA_{k}"] = start_sha
        updates[f"LAST_MERGED_{k}"] = start_sha
        if f"CONVENTION_PATHS_{k}" in rv:
            updates[f"CONVENTION_PATHS_{k}"] = rv[f"CONVENTION_PATHS_{k}"]
        if f"BASELINE_SKIP_{k}" in rv:
            updates[f"BASELINE_SKIP_{k}"] = rv[f"BASELINE_SKIP_{k}"]

    r.set(**updates)
    print(f"IMPL_WORKTREE {impl_root}")
    return 0


def _prune_empty_parents(path, stop_at):
    cur = path
    while cur != stop_at and cur.is_relative_to(stop_at):
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def cmd_remove_impl(args):
    r = Run(args.state)
    if not r.is_multi_repo:
        print("ERROR state is not a multi-repo run", file=sys.stderr)
        return 64
    failed = False
    impl_root = Path(r.need("IMPL_WORKTREE")).resolve()
    for name in r.repos:
        k = repo_key(name)
        wt = r.get(f"IMPL_WT_{k}")
        if not wt or not Path(wt).exists():
            continue
        rc, out = tool("worktree.sh", "remove", r.need(f"REPO_PATH_{k}"), wt,
                       "--delete-branch", "--merged-into", r.repo_feature_branch(name))
        if rc != 0:
            print(f"ERROR removing {name} impl worktree: {out}", file=sys.stderr)
            failed = True
        else:
            _prune_empty_parents(Path(wt).resolve().parent, impl_root)
    if not failed and impl_root.exists():
        try:
            impl_root.rmdir()
        except OSError:
            pass
    return 1 if failed else 0


def cmd_remove_docs(args):
    r = Run(args.state)
    if not r.is_multi_repo:
        print("ERROR state is not a multi-repo run", file=sys.stderr)
        return 64
    failed = False
    for name in r.repos:
        k = repo_key(name)
        wt = r.get(f"DOCS_WT_{k}")
        if not wt or not Path(wt).exists():
            continue
        rc, out = tool("worktree.sh", "remove", r.need(f"REPO_PATH_{k}"), wt,
                       "--delete-branch", "--merged-into", r.need(f"START_SHA_{k}"))
        if rc != 0:
            print(f"ERROR removing {name} docs worktree: {out}", file=sys.stderr)
            failed = True
    docs_root = Path(r.need("DOCS_WORKTREE"))
    if not failed and docs_root.exists():
        shutil.rmtree(docs_root)
    return 1 if failed else 0


def cmd_list_runs(args):
    mc = Path(args.workspace).resolve() / ".mission-control"
    if not mc.is_dir():
        return 0
    for d in sorted(mc.iterdir(), key=lambda p: p.name):
        st = d / ".run" / "state.json"
        if d.is_dir() and d.name.endswith("-docs") and st.is_file():
            try:
                topic = json.loads(st.read_text(encoding="utf-8")).get("TOPIC", d.name[:-5])
            except (OSError, json.JSONDecodeError):
                topic = d.name[:-5]
            print(f"{topic} {st}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_det = sub.add_parser("detect")
    p_det.add_argument("--dir", required=True)
    p_det.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)

    p_chk = sub.add_parser("check-topic")
    p_chk.add_argument("--workspace", required=True)
    p_chk.add_argument("--topic", required=True)
    p_chk.add_argument("--repo", action="append", default=[])
    p_chk.add_argument("--force-new", action="store_true")

    p_id = sub.add_parser("init-docs")
    p_id.add_argument("--workspace", required=True)
    p_id.add_argument("--topic", required=True)
    p_id.add_argument("--repo", action="append", required=True)

    p_ii = sub.add_parser("init-impl")
    p_ii.add_argument("--state", required=True)

    p_ri = sub.add_parser("remove-impl")
    p_ri.add_argument("--state", required=True)

    p_rd = sub.add_parser("remove-docs")
    p_rd.add_argument("--state", required=True)

    p_lr = sub.add_parser("list-runs")
    p_lr.add_argument("--workspace", required=True)

    try:
        a = ap.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0

    handlers = {
        "detect": cmd_detect,
        "check-topic": cmd_check_topic,
        "init-docs": cmd_init_docs,
        "init-impl": cmd_init_impl,
        "remove-impl": cmd_remove_impl,
        "remove-docs": cmd_remove_docs,
        "list-runs": cmd_list_runs,
    }
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
