"""Create, find and remove a run's worktrees and branches, in a single repo or a multi-repo workspace.

Every run keeps its worktrees outside the user's checkout, under
  ${MISSION_CONTROL_WORKTREES:-${XDG_STATE_HOME:-~/.local/state}/mission-control/worktrees}/<name>-<hash>/
where <name>-<hash> names the main checkout (single repo) or the workspace folder (multi-repo), so test runners,
linters, file watchers and IDEs working in the checkout never see a second copy of the code. Run branches are
mission/<topic>-docs and mission/<topic>-impl.

Usage:
  python3 -I workspace.py detect --dir <cwd> [--max-depth <n>]
  python3 -I workspace.py worktrees-root --anchor <main checkout or workspace dir>
  python3 -I workspace.py check-topic --main-checkout <repo> --topic <topic> [--force-new]
  python3 -I workspace.py check-topic --workspace <dir> --topic <topic> --repo <name>=<path> [...] [--force-new]
  python3 -I workspace.py init-docs --main-checkout <repo> --topic <topic> --start-sha <sha>
  python3 -I workspace.py init-docs --workspace <dir> --topic <topic> --repo <name>=<path>@<sha> [...]
  python3 -I workspace.py init-impl --state <RUN_DIR>/state.json
  python3 -I workspace.py remove-impl --state <RUN_DIR>/state.json
  python3 -I workspace.py remove-docs --state <RUN_DIR>/state.json
  python3 -I workspace.py list-runs (--main-checkout <repo> | --workspace <dir>)
  python3 -I workspace.py prune (--main-checkout <repo> | --workspace <dir> | --all) [--apply]

init-impl reads FEATURE_BRANCH(_<k>) from FLIGHT_MANIFEST (multi-repo) or state (single repo); it can be run
again after a failure: a feature branch already at the start commit and a worktree already on the impl branch
are reused, and anything a failed call created is removed. remove-docs deletes the docs branch too (it holds only
the run's documents) and prints its last commit, so `git branch <name> <sha>` brings it back.
init-docs adds the run's ignore patterns to each repo's .git/info/exclude as one marked block; remove-impl and
remove-docs take the block out again once no mission-control worktree of that repo is left.

prune lists finished runs (DONE: PHASE=done) and orphaned ones (ORPHANED: their repo or workspace no longer exists)
under the worktrees folder, for one checkout, one workspace, or every one (--all). With --apply it removes them: a
finished run as remove-impl + remove-docs do, an orphaned one by deleting its folder. Runs in progress (KEEP) and
runs whose state.json is missing or unreadable (UNREADABLE) are never touched.

A topic (check-topic, init-docs) is a JIRA key or a slug: letters, digits, ".", "_" and "-", starting with a letter
or digit, with no ".."; anything else is bad usage. init-docs leaves nothing behind when it fails: it removes the
worktrees, branches and folder it created. list-runs and prune fall back to the run folder's name when a state file
has no TOPIC or a malformed one, and prune never removes a run whose TOPIC is malformed (UNREADABLE).

Exit 0: ok.  Exit 10: EXISTING run found (check-topic).  Exit 1: git/workspace failure.  Exit 64: bad usage.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import Run, _FileLock, git, read_meta, repo_key, run, tool, write_atomic  # noqa: E402

EXCLUDES = [
    ".mission/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "node_modules/",
    ".DS_Store",
]
BRANCH_PREFIX = "mission/"
# The run's lines in a repo's .git/info/exclude sit between these, so the last run's cleanup can take them out again.
EXCLUDE_BEGIN = "# >>> mission-control: run worktree ignores (removed with the repo's last mission-control worktree)"
EXCLUDE_END = "# <<< mission-control"
TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def topic_ok(topic):
    """A topic names branches (mission/<topic>-docs) and folders (<topic>-docs): a JIRA key or a slug."""
    return isinstance(topic, str) and bool(TOPIC.fullmatch(topic)) and ".." not in topic


def _bad_topic(topic):
    """Print the usage error for a malformed --topic and return 64, or return None when it is fine."""
    if topic_ok(topic):
        return None
    print(f"ERROR bad --topic {topic!r}: use letters, digits, '.', '_' and '-' (starting with a letter or digit, "
          "no '..'), such as a JIRA key or a kebab-case slug", file=sys.stderr)
    return 64


def docs_branch(topic):
    return f"{BRANCH_PREFIX}{topic}-docs"


def impl_branch(topic):
    return f"{BRANCH_PREFIX}{topic}-impl"


def worktrees_base():
    """The folder holding every checkout's and workspace's worktrees folder."""
    base = os.environ.get("MISSION_CONTROL_WORKTREES")
    if not base:
        state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        base = str(Path(state_home) / "mission-control" / "worktrees")
    return Path(base)


def worktrees_root(anchor):
    """The folder holding this checkout's (or workspace's) run worktrees, outside it."""
    anchor = Path(anchor).resolve()
    digest = hashlib.sha1(str(anchor).encode("utf-8")).hexdigest()[:8]
    return worktrees_base() / f"{anchor.name or 'root'}-{digest}"


def _default_branch(repo_path):
    """origin/HEAD when set; else init.defaultBranch, main, master or trunk if it exists locally; else ''."""
    r = run(["git", "-C", str(repo_path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if r.returncode == 0 and r.stdout.strip():
        ref = r.stdout.strip()
        return ref[len("origin/"):] if ref.startswith("origin/") else ref
    configured = git(str(repo_path), "config", "--get", "init.defaultBranch") or ""
    for name in [configured, "main", "master", "trunk"]:
        if name and _branch_exists(repo_path, name):
            return name
    return ""


def _current_branch(repo_path):
    return git(str(repo_path), "branch", "--show-current") or ""


def _branch_exists(repo_path, branch):
    return run(["git", "-C", str(repo_path), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0


def _exclude_lock(exclude):
    """The lock both exclude-file edits hold, so two runs starting or ending in one repo never lose each other's
    lines (a file next to it; the exclude file itself is replaced by write_atomic)."""
    return _FileLock(exclude.with_name(".exclude.mission-control.lock"))


def _append_excludes(git_common_dir, lines):
    """Add the lines the exclude file lacks (older versions wrote them unmarked) inside the mission-control block."""
    exclude = Path(git_common_dir) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with _exclude_lock(exclude):
        old = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        new = [l for l in dict.fromkeys(lines) if l not in old]
        if not new:
            return
        if EXCLUDE_BEGIN in old and EXCLUDE_END in old[old.index(EXCLUDE_BEGIN):]:
            end = old.index(EXCLUDE_END, old.index(EXCLUDE_BEGIN))
            text = old[:end] + new + old[end:]
        else:
            text = old + [EXCLUDE_BEGIN] + new + [EXCLUDE_END]
        write_atomic(exclude, "\n".join(text) + "\n")


def _remove_excludes(repo_path):
    """Drop the mission-control block from the repo's exclude file once no mission-control worktree of the repo is
    left (none on a mission/ branch, none under the worktrees folder). Lines outside the block are never touched.
    The worktree check runs under the exclude lock: another run that adds its worktree and its lines meanwhile
    keeps them."""
    common_dir = git(str(repo_path), "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common_dir:
        return
    exclude = Path(common_dir) / "info" / "exclude"
    if not exclude.exists():
        return
    with _exclude_lock(exclude):
        out = git(str(repo_path), "worktree", "list", "--porcelain")
        if out is None:
            return
        base = worktrees_base().resolve()
        for line in out.splitlines():
            if line.startswith(f"branch refs/heads/{BRANCH_PREFIX}") or (
                    line.startswith("worktree ") and Path(line[len("worktree "):]).resolve().is_relative_to(base)):
                return
        old = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        if EXCLUDE_BEGIN not in old or EXCLUDE_END not in old[old.index(EXCLUDE_BEGIN):]:
            return
        start = old.index(EXCLUDE_BEGIN)
        end = old.index(EXCLUDE_END, start)
        rest = old[:start] + old[end + 1:]
        write_atomic(exclude, "\n".join(rest) + ("\n" if rest else ""))


def _exclude_in(repo_path, extra=()):
    common_dir = git(str(repo_path), "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common_dir:
        _append_excludes(common_dir, EXCLUDES + list(extra))


def _add_worktree(repo_path, wt_path, branch, start, new_branch=True):
    """git worktree add (after pruning stale registrations, e.g. a worktree folder someone deleted).
    Returns (ok, output)."""
    run(["git", "-C", str(repo_path), "worktree", "prune"])
    Path(wt_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "-C", str(repo_path), "worktree", "add"]
    cmd += ["-b", branch, str(wt_path), start] if new_branch else [str(wt_path), branch]
    r = run(cmd)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _worktree_on(repo_path, branch):
    """The path of the worktree that has refs/heads/<branch> checked out, or None."""
    out = git(str(repo_path), "worktree", "list", "--porcelain") or ""
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def _make_run_dirs(docs_root):
    docs_dir = docs_root / "docs" / "plans"
    run_dir = docs_root / ".mission"
    relay_dir = run_dir / "relay"
    for d in (docs_dir, relay_dir, run_dir / "specs", run_dir / "verdicts",
              run_dir / "asks", run_dir / "gates", run_dir / "results", run_dir / "logs"):
        d.mkdir(parents=True, exist_ok=True)
    print(f"DOCS_WORKTREE {docs_root}")
    print(f"DOCS_DIR {docs_dir}")
    print(f"RUN_DIR {run_dir}")
    print(f"RELAY_DIR {relay_dir}")


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
        print(f"DEFAULT_BRANCH {_default_branch(top_path) or '-'}")
        print(f"CURRENT_BRANCH {_current_branch(top_path) or '-'}")
        return 0

    repos = []
    seen_keys = {}
    for rel, path in _discover_repos(cwd, max_depth=args.max_depth):
        if any(c.isspace() for c in rel):
            print(f"ERROR repo folder {rel!r} has whitespace in its name; rename it or run from inside it",
                  file=sys.stderr)
            return 1
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


def cmd_worktrees_root(args):
    print(worktrees_root(args.anchor))
    return 0


def _parse_repo_specs(specs, require_sha=False):
    parsed = []
    seen_keys = set()
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"bad --repo spec {spec!r}; expected name=path" + ("@sha" if require_sha else ""))
        name, rest = spec.split("=", 1)
        if any(c.isspace() for c in name):
            raise ValueError(f"repo name {name!r} has whitespace")
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


def _single_docs_state(main, topic):
    """The docs worktree of an existing single-repo run on this topic, or None."""
    wt = _worktree_on(main, docs_branch(topic))
    return Path(wt) if wt and (Path(wt) / ".mission" / "state.json").is_file() else None


def cmd_check_topic(args):
    if bool(args.main_checkout) == bool(args.workspace):
        print("ERROR pass exactly one of --main-checkout, --workspace", file=sys.stderr)
        return 64
    if _bad_topic(args.topic):
        return 64
    if args.main_checkout:
        main = Path(args.main_checkout).resolve()
        repos = [(main.name, None, main, None)]
        root = worktrees_root(main)
        existing = None if args.force_new else _single_docs_state(main, args.topic)
    else:
        try:
            repos = _parse_repo_specs(args.repo, require_sha=False)
        except ValueError as e:
            print(f"ERROR {e}", file=sys.stderr)
            return 64
        ws = Path(args.workspace).resolve()
        root = worktrees_root(ws)
        cand = root / f"{args.topic}-docs"
        existing = cand if not args.force_new and (cand / ".mission" / "state.json").is_file() else None
    if existing:
        print(f"EXISTING {existing}")
        return 10

    def topic_taken(t):
        if (root / f"{t}-docs").exists() or (root / f"{t}-impl").exists():
            return True
        names = (docs_branch(t), impl_branch(t))
        return any(_branch_exists(path, b) for _, _, path, _ in repos for b in names)

    chosen = args.topic
    if args.force_new or topic_taken(chosen):
        k = 2
        while topic_taken(f"{args.topic}-{k}"):
            k += 1
        chosen = f"{args.topic}-{k}"
    print(f"TOPIC {chosen}")
    return 0


def _init_docs_single(args):
    main = Path(args.main_checkout).resolve()
    if not args.start_sha:
        print("ERROR --start-sha is required with --main-checkout", file=sys.stderr)
        return 64
    wt = worktrees_root(main) / f"{args.topic}-docs"
    if wt.exists():
        print(f"ERROR docs worktree already exists: {wt}", file=sys.stderr)
        return 1
    ok, out = _add_worktree(main, wt, docs_branch(args.topic), args.start_sha)
    if not ok:
        print(f"ERROR worktree add: {out}", file=sys.stderr)
        return 1
    created = [(main, wt, docs_branch(args.topic))]
    try:
        err = _fresh_tree(wt, args.start_sha)
        if err:
            _undo_docs(created)
            print(f"ERROR {err}", file=sys.stderr)
            return 1
        _exclude_in(main)
        _make_run_dirs(wt)
    except BaseException:
        _undo_docs(created)
        raise
    return 0


def _fresh_tree(wt, sha):
    """None when the new worktree is clean at sha (after discarding what a checkout hook may have changed: the
    branch is new, so nothing is lost), else what is wrong."""
    rc, out = tool("check-tree.sh", str(wt), sha)
    if rc == 1:
        rc2, out2 = tool("discard-changes.sh", str(wt), sha)
        return None if rc2 == 0 else f"discard-changes: {out2}"
    return None if rc == 0 else f"check-tree: {out}"


def _undo_docs(created):
    """Remove the docs worktrees and branches a failed init-docs call created, newest first, and the exclude block the
    call added to each repo (_remove_excludes keeps it while another mission-control worktree of the repo is left)."""
    for repo_path, wt_path, branch in reversed(created):
        run(["git", "-C", str(repo_path), "worktree", "remove", "--force", str(wt_path)])
        if Path(wt_path).exists():  # `worktree remove` refused (e.g. git cannot read its index): delete it by hand
            shutil.rmtree(wt_path, ignore_errors=True)
            run(["git", "-C", str(repo_path), "worktree", "prune"])
        run(["git", "-C", str(repo_path), "branch", "-D", branch])
        _remove_excludes(repo_path)


def _init_docs_multi(args):
    ws = Path(args.workspace).resolve()
    try:
        repos = _parse_repo_specs(args.repo, require_sha=True)
    except ValueError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 64
    if not repos:
        print("ERROR at least one --repo is required", file=sys.stderr)
        return 64

    docs_root = worktrees_root(ws) / f"{args.topic}-docs"
    if docs_root.exists():
        print(f"ERROR docs workspace already exists: {docs_root}", file=sys.stderr)
        return 1
    docs_root.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        err = _init_docs_multi_in(args, repos, docs_root, created)
    except BaseException:
        _undo_docs(created)
        shutil.rmtree(docs_root, ignore_errors=True)
        raise
    if err:
        _undo_docs(created)
        shutil.rmtree(docs_root, ignore_errors=True)
        print(f"ERROR {err}", file=sys.stderr)
        return 1
    _make_run_dirs(docs_root)
    for name, k, _, _ in repos:
        print(f"DOCS_WT_{k} {docs_root / name}")
    return 0


def _init_docs_multi_in(args, repos, docs_root, created):
    """Set up the docs workspace in docs_root; append each (repo, worktree, branch) created to `created`.
    Returns an error string or None."""
    r = run(["git", "init", "-q", "-b", docs_branch(args.topic), str(docs_root)])
    if r.returncode != 0:
        return f"git init failed: {r.stderr.strip()}"

    # Ensure initial commit succeeds even if global git identity is unset.
    r = run([
        "git", "-C", str(docs_root),
        "-c", "user.name=mission-control", "-c", "user.email=mission-control@local",
        "commit", "-q", "--allow-empty", "-m", "init docs workspace",
    ])
    if r.returncode != 0:
        return f"initial commit failed: {r.stderr.strip()}"

    docs_common = git(str(docs_root), "rev-parse", "--path-format=absolute", "--git-common-dir")
    _append_excludes(docs_common, EXCLUDES + [f"{name}/" for name, _, _, _ in repos])

    for name, k, repo_path, sha in repos:
        wt_path = docs_root / name
        ok, out = _add_worktree(repo_path, wt_path, docs_branch(args.topic), sha)
        if not ok:
            return f"worktree add for {name}: {out}"
        created.append((repo_path, wt_path, docs_branch(args.topic)))
        err = _fresh_tree(wt_path, sha)
        if err:
            return f"{err} (repo {name})"
        _exclude_in(repo_path)
    return None


def cmd_init_docs(args):
    if bool(args.main_checkout) == bool(args.workspace):
        print("ERROR pass exactly one of --main-checkout, --workspace", file=sys.stderr)
        return 64
    if _bad_topic(args.topic):
        return 64
    return _init_docs_single(args) if args.main_checkout else _init_docs_multi(args)


def _make_impl(repo_path, fb, start_sha, wt_path, impl_name, created, ours=False):
    """Create feature branch fb at start_sha and the impl worktree on impl_name. An existing fb is reused only
    when an earlier call of this run created it (ours), never a branch the user already had. Appends what it
    created to `created` for rollback. Returns an error string or None."""
    if _branch_exists(repo_path, fb):
        if not ours:
            return (f"feature branch {fb} already exists; the run needs a new branch name "
                    "(FEATURE_BRANCH in flight-manifest.txt)")
        if git(str(repo_path), "rev-parse", f"refs/heads/{fb}") != git(str(repo_path), "rev-parse", f"{start_sha}^{{commit}}"):
            return f"feature branch {fb} already exists and is not at the run's start commit {start_sha}"
    else:
        res = run(["git", "-C", str(repo_path), "branch", fb, start_sha])
        if res.returncode != 0:
            return f"git branch {fb}: {(res.stdout + res.stderr).strip()}"
        created.append(("branch", repo_path, fb))
    run(["git", "-C", str(repo_path), "worktree", "prune"])  # forget worktree folders deleted by hand
    holder = _worktree_on(repo_path, impl_name)
    if holder and Path(holder).resolve() == Path(wt_path).resolve():
        return None  # an earlier call created it
    if holder:
        return f"branch {impl_name} is checked out at {holder}"
    if _branch_exists(repo_path, impl_name):
        ok, out = _add_worktree(repo_path, wt_path, impl_name, None, new_branch=False)
    else:
        ok, out = _add_worktree(repo_path, wt_path, impl_name, fb)
        if ok:
            created.append(("branch", repo_path, impl_name))
    if not ok:
        return f"git worktree add: {out}"
    created.append(("worktree", repo_path, str(wt_path)))
    return None


def _rollback(created):
    for kind, repo_path, what in reversed(created):
        if kind == "worktree":
            run(["git", "-C", str(repo_path), "worktree", "remove", "--force", what])
        else:
            run(["git", "-C", str(repo_path), "branch", "-D", what])


def cmd_init_impl(args):
    r = Run(args.state)
    topic = r.need("TOPIC")
    created = []
    if not r.is_multi_repo:
        main = r.need("MAIN_CHECKOUT")
        start_sha = r.need("START_SHA")
        wt_path = worktrees_root(main) / f"{topic}-impl"
        fb = r.need("FEATURE_BRANCH")
        err = _make_impl(main, fb, start_sha, wt_path, impl_branch(topic), created,
                         ours=r.get("FEATURE_BRANCH_CREATED") == fb)
        if not err:
            if any(kind == "branch" and what == fb for kind, _, what in created):
                r.set(FEATURE_BRANCH_CREATED=fb)
            rc, out = tool("check-tree.sh", str(wt_path), start_sha)
            err = None if rc == 0 else f"check-tree: {out}"
        if err:
            _rollback(created)
            if not _branch_exists(main, fb):
                r.set(FEATURE_BRANCH_CREATED=None)
            print(f"ERROR {err}", file=sys.stderr)
            return 1
        # PR_TARGET is optional in the flight manifest and defaults to the base branch, as per repo below.
        pt = r.get("PR_TARGET") or r.get("BASE_BRANCH")
        r.set(IMPL_WORKTREE=wt_path, INITIAL_BASE_SHA=start_sha, LAST_MERGED=start_sha, PR_TARGET=pt)
        print(f"IMPL_WORKTREE {wt_path}")
        return 0

    ws = Path(r.need("WORKSPACE_DIR")).resolve()
    rv = read_meta(r.need("FLIGHT_MANIFEST"))
    impl_root = worktrees_root(ws) / f"{topic}-impl"
    impl_root.mkdir(parents=True, exist_ok=True)

    updates = {"IMPL_WORKTREE": str(impl_root)}
    for name in r.repos:
        k = repo_key(name)
        repo_path = r.need(f"REPO_PATH_{k}")
        start_sha = r.need(f"START_SHA_{k}")
        fb = rv.get(f"FEATURE_BRANCH_{k}") or rv.get("FEATURE_BRANCH")
        if not fb:
            _rollback(created)
            print(f"ERROR missing FEATURE_BRANCH (or FEATURE_BRANCH_{k}) in {r.need('FLIGHT_MANIFEST')}", file=sys.stderr)
            return 1
        pt = rv.get(f"PR_TARGET_{k}") or r.need(f"BASE_BRANCH_{k}")
        wt_path = impl_root / name
        err = _make_impl(repo_path, fb, start_sha, wt_path, impl_branch(topic), created,
                         ours=r.get(f"FEATURE_BRANCH_CREATED_{k}") == fb)
        if not err:
            if any(kind == "branch" and what == fb and rp == repo_path for kind, rp, what in created):
                r.set(**{f"FEATURE_BRANCH_CREATED_{k}": fb})
            rc, out = tool("check-tree.sh", str(wt_path), start_sha)
            err = None if rc == 0 else f"check-tree: {out}"
        if err:
            _rollback(created)
            for n in r.repos:
                kn = repo_key(n)
                made = r.get(f"FEATURE_BRANCH_CREATED_{kn}")
                if made and not _branch_exists(r.need(f"REPO_PATH_{kn}"), made):
                    r.set(**{f"FEATURE_BRANCH_CREATED_{kn}": None})
            print(f"ERROR {name}: {err}", file=sys.stderr)
            return 1

        updates[f"IMPL_WT_{k}"] = str(wt_path)
        updates[f"FEATURE_BRANCH_{k}"] = fb
        updates[f"PR_TARGET_{k}"] = pt
        updates[f"INITIAL_BASE_SHA_{k}"] = start_sha
        updates[f"LAST_MERGED_{k}"] = start_sha
        if f"CONVENTION_PATHS_{k}" in rv:
            updates[f"CONVENTION_PATHS_{k}"] = rv[f"CONVENTION_PATHS_{k}"]
        # Always set (empty when the manifest has none): step 3 passes it to gate.py --skip as it is.
        updates[f"BASELINE_SKIP_{k}"] = rv.get(f"BASELINE_SKIP_{k}", "")

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
    """Remove the impl worktree(s) and their impl branch, which must be merged into the feature branch."""
    r = Run(args.state)
    failed = False
    if not r.is_multi_repo:
        wt = r.get("IMPL_WORKTREE")
        if wt and Path(wt).exists():
            rc, out = tool("worktree.sh", "remove", r.need("MAIN_CHECKOUT"), wt,
                           "--delete-branch", "--merged-into", r.need("FEATURE_BRANCH"))
            print(out)
            if rc == 0:
                _remove_excludes(r.need("MAIN_CHECKOUT"))
            return 0 if rc == 0 else 1
        return 0
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
            _remove_excludes(r.need(f"REPO_PATH_{k}"))
    if not failed and impl_root.exists():
        try:
            impl_root.rmdir()
        except OSError:
            pass
    return 1 if failed else 0


def _delete_branch(repo_path, branch):
    sha = git(str(repo_path), "rev-parse", f"refs/heads/{branch}")
    if sha is None:
        return True
    r = run(["git", "-C", str(repo_path), "branch", "-D", branch])
    if r.returncode == 0:
        print(f"DELETED_BRANCH {branch} (was {sha}; `git -C {repo_path} branch {branch} {sha}` restores it)")
    return r.returncode == 0


def cmd_remove_docs(args):
    """Remove the docs worktree(s) and the docs branch: the run's documents, no product code."""
    r = Run(args.state)
    if not r.is_multi_repo:
        main, wt = r.need("MAIN_CHECKOUT"), Path(r.need("DOCS_WORKTREE"))
        name = docs_branch(r.need("TOPIC"))
        if wt.exists():
            current = git(str(wt), "branch", "--show-current")
            if current != name:
                print(f"ERROR the docs worktree has {current or 'a detached HEAD'} checked out, not {name}; "
                      "switch it back first", file=sys.stderr)
                return 1
            res = run(["git", "-C", main, "worktree", "remove", "--force", str(wt)])
            if res.returncode != 0:
                print(f"ERROR removing docs worktree: {(res.stdout + res.stderr).strip()}", file=sys.stderr)
                return 1
        ok = _delete_branch(main, name)
        _remove_excludes(main)
        return 0 if ok else 1
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
        else:
            _remove_excludes(r.need(f"REPO_PATH_{k}"))
    docs_root = Path(r.need("DOCS_WORKTREE"))
    if not failed and docs_root.exists():
        shutil.rmtree(docs_root)
    return 1 if failed else 0


def cmd_list_runs(args):
    """`<topic> <state.json>` for every run with a state file."""
    if bool(args.main_checkout) == bool(args.workspace):
        print("ERROR pass exactly one of --main-checkout, --workspace", file=sys.stderr)
        return 64
    anchor = Path(args.main_checkout or args.workspace).resolve()
    dirs = []
    if args.main_checkout:
        out = git(str(anchor), "worktree", "list", "--porcelain") or ""
        dirs = [Path(l[len("worktree "):]) for l in out.splitlines() if l.startswith("worktree ")]
    else:
        root = worktrees_root(anchor)
        if root.is_dir():
            dirs = sorted((d for d in root.iterdir() if d.is_dir() and d.name.endswith("-docs")), key=lambda p: p.name)
    for d in dirs:
        st = d / ".mission" / "state.json"
        if st.is_file():
            try:
                s = json.loads(st.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                s = None
            topic = s.get("TOPIC") if isinstance(s, dict) else None
            print(f"{topic if topic_ok(topic) else d.name[:-5]} {st}")
    return 0


def _runs_under(root):
    """(docs folder, state dict) for every run folder directly under root; the state is None when state.json is
    missing or unreadable (nothing then says whether the run is finished or its repo gone)."""
    found = []
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name.endswith("-docs")):
            st = d / ".mission" / "state.json"
            try:
                s = json.loads(st.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                s = None
            found.append((d, s if isinstance(s, dict) else None))
    return found


def cmd_prune(args):
    picked = [x for x in (args.main_checkout, args.workspace, args.all) if x]
    if len(picked) != 1:
        print("ERROR pass exactly one of --main-checkout, --workspace, --all", file=sys.stderr)
        return 64
    if args.all:
        base = worktrees_base()
        roots = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    else:
        roots = [worktrees_root(args.main_checkout or args.workspace)]
    failed = False
    for root in roots:
        for docs, s in _runs_under(root):
            if s is None:
                print(f"UNREADABLE {docs.name[:-5]} {docs} (state.json missing or unreadable; not removed)")
                continue
            anchor = s.get("WORKSPACE_DIR") if s.get("REPOS", "none") not in ("", "none") else s.get("MAIN_CHECKOUT")
            topic = s.get("TOPIC", docs.name[:-5])
            if not topic_ok(topic):
                print(f"UNREADABLE {docs.name[:-5]} {docs} (TOPIC in state.json is malformed; not removed)")
                continue
            if not anchor or not Path(anchor).exists():
                kind = "ORPHANED"
            elif s.get("PHASE") == "done":
                kind = "DONE"
            else:
                print(f"KEEP {topic} {docs} (phase {s.get('PHASE', '?')}, step {s.get('STEP', '?')})")
                continue
            print(f"{kind} {topic} {docs}")
            if not args.apply:
                continue
            if kind == "ORPHANED":
                for sib in (docs, docs.with_name(f"{topic}-impl")):
                    shutil.rmtree(sib, ignore_errors=True)
                print(f"REMOVED {docs}")
                continue
            ns = argparse.Namespace(state=str(docs / ".mission" / "state.json"))
            if cmd_remove_impl(ns) != 0 or cmd_remove_docs(ns) != 0:
                failed = True
            else:
                print(f"REMOVED {docs}")
        if args.apply and root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    if not args.apply:
        print("(dry run: pass --apply to remove the DONE and ORPHANED runs above)")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_det = sub.add_parser("detect")
    p_det.add_argument("--dir", required=True)
    p_det.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)

    p_root = sub.add_parser("worktrees-root")
    p_root.add_argument("--anchor", required=True)

    p_chk = sub.add_parser("check-topic")
    p_chk.add_argument("--workspace")
    p_chk.add_argument("--main-checkout")
    p_chk.add_argument("--topic", required=True)
    p_chk.add_argument("--repo", action="append", default=[])
    p_chk.add_argument("--force-new", action="store_true")

    p_id = sub.add_parser("init-docs")
    p_id.add_argument("--workspace")
    p_id.add_argument("--main-checkout")
    p_id.add_argument("--start-sha")
    p_id.add_argument("--topic", required=True)
    p_id.add_argument("--repo", action="append", default=[])

    for name in ("init-impl", "remove-impl", "remove-docs"):
        sub.add_parser(name).add_argument("--state", required=True)

    p_pr = sub.add_parser("prune")
    p_pr.add_argument("--main-checkout")
    p_pr.add_argument("--workspace")
    p_pr.add_argument("--all", action="store_true")
    p_pr.add_argument("--apply", action="store_true")

    p_lr = sub.add_parser("list-runs")
    p_lr.add_argument("--workspace")
    p_lr.add_argument("--main-checkout")

    try:
        a = ap.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0

    handlers = {
        "detect": cmd_detect,
        "worktrees-root": cmd_worktrees_root,
        "check-topic": cmd_check_topic,
        "init-docs": cmd_init_docs,
        "init-impl": cmd_init_impl,
        "remove-impl": cmd_remove_impl,
        "remove-docs": cmd_remove_docs,
        "list-runs": cmd_list_runs,
        "prune": cmd_prune,
    }
    return handlers[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
