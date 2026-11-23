"""Tests for multi-repo workspace.py, gate.py --repo, validate_meta.py REPO, run_task.py, and run_finish.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import CATALOG, FAKE, META, SKILL, sh  # noqa: E402

WORKSPACE = SKILL / "scripts" / "workspace.py"
GATE = SKILL / "scripts" / "gate.py"
VALIDATE = SKILL / "scripts" / "validate_meta.py"
RUN_TASK = SKILL / "scripts" / "run_task.py"
RUN_FINISH = SKILL / "scripts" / "run_finish.py"


def py(script, *args, env=None):
    return subprocess.run(
        [sys.executable, "-I", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def make_repo(path, branch="main", extra_branch=None):
    path.mkdir(parents=True, exist_ok=True)
    g = lambda *a: sh("git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", *a)
    g("init", "-q", "-b", branch)
    (path / "README.md").write_text(f"{path.name}\n")
    g("add", ".")
    g("commit", "-qm", "init")
    if extra_branch:
        g("checkout", "-q", "-b", extra_branch)
        (path / "feature.txt").write_text("wip\n")
        g("add", ".")
        g("commit", "-qm", "wip")
    return g("rev-parse", "HEAD")


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mc-ws-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        old = os.environ.get("MISSION_CONTROL_WORKTREES")
        os.environ["MISSION_CONTROL_WORKTREES"] = str(self.tmp / "wt-root")
        self.addCleanup(lambda: os.environ.pop("MISSION_CONTROL_WORKTREES") if old is None
                        else os.environ.__setitem__("MISSION_CONTROL_WORKTREES", old))

    def root(self, anchor):
        return Path(py(WORKSPACE, "worktrees-root", "--anchor", str(anchor)).stdout.strip())

    def test_detect_single_multi_and_none(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        r = py(WORKSPACE, "detect", "--dir", str(empty))
        self.assertEqual(r.returncode, 1)
        self.assertIn("MODE none", r.stdout)

        ws = self.tmp / "parent"
        ws.mkdir()
        (ws / "notes").mkdir()  # non-git dir ignored
        make_repo(ws / "my-api", branch="main", extra_branch="feat/existing")
        make_repo(ws / "web-app", branch="main")

        # Inside a single repo -> MODE single
        r_single = py(WORKSPACE, "detect", "--dir", str(ws / "my-api"))
        self.assertEqual(r_single.returncode, 0, r_single.stderr)
        self.assertIn("MODE single", r_single.stdout)
        self.assertIn("CURRENT_BRANCH feat/existing", r_single.stdout)

        # In parent dir -> MODE multi with both repos
        r_multi = py(WORKSPACE, "detect", "--dir", str(ws))
        self.assertEqual(r_multi.returncode, 0, r_multi.stderr)
        self.assertIn("MODE multi", r_multi.stdout)
        self.assertIn("REPO my-api my_api", r_multi.stdout)
        self.assertIn("REPO web-app web_app", r_multi.stdout)

    def test_lifecycle_docs_impl_list_and_remove(self):
        ws = self.tmp / "parent"
        ws.mkdir()
        sha_api = make_repo(ws / "my-api")
        sha_web = make_repo(ws / "web-app")

        # check-topic when clean
        r = py(WORKSPACE, "check-topic", "--workspace", str(ws), "--topic", "auth",
               "--repo", f"my-api={ws / 'my-api'}", "--repo", f"web-app={ws / 'web-app'}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "TOPIC auth")

        # init-docs creates <worktrees root>/auth-docs, outside the workspace, with mounted repo worktrees
        r = py(WORKSPACE, "init-docs", "--workspace", str(ws), "--topic", "auth",
               "--repo", f"my-api={ws / 'my-api'}@{sha_api}",
               "--repo", f"web-app={ws / 'web-app'}@{sha_web}")
        self.assertEqual(r.returncode, 0, r.stderr)
        kv = dict(line.split(" ", 1) for line in r.stdout.strip().splitlines())
        docs_wt = Path(kv["DOCS_WORKTREE"])
        run_dir = Path(kv["RUN_DIR"])
        docs_dir = Path(kv["DOCS_DIR"])
        self.assertEqual(docs_wt, self.root(ws) / "auth-docs")
        self.assertFalse(docs_wt.is_relative_to(ws))
        self.assertTrue(str(self.root(ws)).startswith(str(self.tmp / "wt-root")))
        self.assertTrue((docs_wt / "my-api" / "README.md").is_file())
        self.assertTrue((docs_wt / "web-app" / "README.md").is_file())

        # Write state & flight-manifest.txt
        rv = docs_dir / "flight-manifest.txt"
        rv.write_text(
            "FEATURE_BRANCH=feat/auth\n"
            "VERIFY_my_api_1=test -f README.md\n"
            "VERIFY_web_app_1=test -f README.md\n"
            "BASELINE_SKIP_my_api=VERIFY_my_api_1\n"
        )
        state = run_dir / "state.json"
        state.write_text(json.dumps({
            "TOPIC": "auth",
            "WORKSPACE_DIR": str(ws),
            "REPOS": "my-api web-app",
            "DOCS_WORKTREE": str(docs_wt),
            "DOCS_DIR": str(docs_dir),
            "RUN_DIR": str(run_dir),
            "FLIGHT_MANIFEST": str(rv),
            "REPO_PATH_my_api": str(ws / "my-api"),
            "REPO_PATH_web_app": str(ws / "web-app"),
            "BASE_BRANCH_my_api": "main",
            "BASE_BRANCH_web_app": "main",
            "START_SHA_my_api": sha_api,
            "START_SHA_web_app": sha_web,
            "DOCS_WT_my_api": kv["DOCS_WT_my_api"],
            "DOCS_WT_web_app": kv["DOCS_WT_web_app"],
        }))

        # check-topic detects existing run, --force-new bumps to auth-2
        r_ex = py(WORKSPACE, "check-topic", "--workspace", str(ws), "--topic", "auth",
                  "--repo", f"my-api={ws / 'my-api'}", "--repo", f"web-app={ws / 'web-app'}")
        self.assertEqual(r_ex.returncode, 10)
        self.assertIn("EXISTING", r_ex.stdout)
        r_new = py(WORKSPACE, "check-topic", "--workspace", str(ws), "--topic", "auth",
                   "--repo", f"my-api={ws / 'my-api'}", "--repo", f"web-app={ws / 'web-app'}", "--force-new")
        self.assertEqual(r_new.returncode, 0)
        self.assertEqual(r_new.stdout.strip(), "TOPIC auth-2")

        # list-runs finds auth
        r_list = py(WORKSPACE, "list-runs", "--workspace", str(ws))
        self.assertEqual(r_list.returncode, 0)
        self.assertIn(f"auth {state}", r_list.stdout)

        # init-impl creates per-repo feature branches and side-by-side impl worktrees
        r_impl = py(WORKSPACE, "init-impl", "--state", str(state))
        self.assertEqual(r_impl.returncode, 0, r_impl.stderr)
        st = json.loads(state.read_text())
        self.assertEqual(st["FEATURE_BRANCH_my_api"], "feat/auth")
        self.assertEqual(st["PR_TARGET_web_app"], "main")
        # Every repo gets BASELINE_SKIP_<k>, empty when the manifest names none, for gate.py --skip as it is.
        self.assertEqual((st["BASELINE_SKIP_my_api"], st["BASELINE_SKIP_web_app"]), ("VERIFY_my_api_1", ""))
        out = subprocess.run([sys.executable, "-I", str(SKILL / "scripts" / "state.py"), "--file", str(state), "get",
                              "BASELINE_SKIP_web_app"], capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout), (0, "\n"))
        self.assertTrue((Path(st["IMPL_WT_my_api"]) / "README.md").is_file())
        self.assertTrue((Path(st["IMPL_WT_web_app"]) / "README.md").is_file())

        # gate.py --repo runs per-repo checks
        r_gate = py(GATE, "--flight-manifest", str(rv), "--worktree", st["IMPL_WT_my_api"],
                    "--head", sha_api, "--log", str(run_dir / "gates" / "api.log"),
                    "--prefix", "VERIFY", "--repo", "my_api")
        self.assertEqual(r_gate.returncode, 0, r_gate.stdout + r_gate.stderr)

        # remove-impl and remove-docs clean up worktrees and branches cleanly
        r_ri = py(WORKSPACE, "remove-impl", "--state", str(state))
        self.assertEqual(r_ri.returncode, 0, r_ri.stderr)
        self.assertFalse(Path(st["IMPL_WORKTREE"]).exists())

        r_rd = py(WORKSPACE, "remove-docs", "--state", str(state))
        self.assertEqual(r_rd.returncode, 0, r_rd.stderr)
        self.assertFalse(docs_wt.exists())

    def test_single_repo_lifecycle(self):
        repo = self.tmp / "app"
        sha = make_repo(repo)
        g = lambda *a: sh("git", "-C", str(repo), *a)
        r = py(WORKSPACE, "check-topic", "--main-checkout", str(repo), "--topic", "auth")
        self.assertEqual(r.stdout.strip(), "TOPIC auth", r.stderr)
        r = py(WORKSPACE, "init-docs", "--main-checkout", str(repo), "--topic", "auth", "--start-sha", sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        kv = dict(line.split(" ", 1) for line in r.stdout.strip().splitlines())
        docs_wt, run_dir = Path(kv["DOCS_WORKTREE"]), Path(kv["RUN_DIR"])
        self.assertEqual(docs_wt, self.root(repo) / "auth-docs")
        self.assertFalse(docs_wt.is_relative_to(repo))
        self.assertEqual(sh("git", "-C", str(docs_wt), "branch", "--show-current"), "mission/auth-docs")
        self.assertIn(".mission/", (repo / ".git" / "info" / "exclude").read_text())
        state = run_dir / "state.json"
        state.write_text(json.dumps({"TOPIC": "auth", "REPOS": "none", "MAIN_CHECKOUT": str(repo),
                                     "START_SHA": sha, "FEATURE_BRANCH": "feat/auth", "RUN_DIR": str(run_dir),
                                     "DOCS_WORKTREE": str(docs_wt), "BASE_BRANCH": "main"}))
        # An existing run is found; a new one gets the next free topic.
        r = py(WORKSPACE, "check-topic", "--main-checkout", str(repo), "--topic", "auth")
        self.assertEqual((r.returncode, r.stdout.strip()), (10, f"EXISTING {docs_wt}"))
        r = py(WORKSPACE, "list-runs", "--main-checkout", str(repo))
        self.assertIn(f"auth {state}", r.stdout)
        r = py(WORKSPACE, "check-topic", "--main-checkout", str(repo), "--topic", "auth", "--force-new")
        self.assertEqual(r.stdout.strip(), "TOPIC auth-2")

        # init-impl twice: the second call reuses what the first created.
        for _ in range(2):
            r = py(WORKSPACE, "init-impl", "--state", str(state))
            self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(state.read_text())
        impl = Path(st["IMPL_WORKTREE"])
        self.assertEqual(impl, self.root(repo) / "auth-impl")
        self.assertEqual((st["INITIAL_BASE_SHA"], st["LAST_MERGED"]), (sha, sha))
        self.assertEqual(st["PR_TARGET"], "main")  # no PR_TARGET in the manifest: the base branch
        self.assertEqual(g("rev-parse", "feat/auth"), sha)
        self.assertEqual(g("branch", "--show-current"), "main")  # the main checkout is never switched

        r = py(WORKSPACE, "remove-impl", "--state", str(state))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(impl.exists())
        self.assertNotEqual(subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "-q",
                                            "refs/heads/mission/auth-impl"]).returncode, 0)

        r = py(WORKSPACE, "remove-docs", "--state", str(state))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DELETED_BRANCH mission/auth-docs", r.stdout)
        self.assertFalse(docs_wt.exists())

    def test_init_impl_recreates_a_worktree_deleted_by_hand(self):
        repo = self.tmp / "app"
        sha = make_repo(repo)
        r = py(WORKSPACE, "init-docs", "--main-checkout", str(repo), "--topic", "t", "--start-sha", sha)
        run_dir = Path(dict(l.split(" ", 1) for l in r.stdout.strip().splitlines())["RUN_DIR"])
        state = run_dir / "state.json"
        state.write_text(json.dumps({"TOPIC": "t", "REPOS": "none", "MAIN_CHECKOUT": str(repo), "START_SHA": sha,
                                     "FEATURE_BRANCH": "feat/t", "RUN_DIR": str(run_dir)}))
        self.assertEqual(py(WORKSPACE, "init-impl", "--state", str(state)).returncode, 0)
        impl = Path(json.loads(state.read_text())["IMPL_WORKTREE"])
        shutil.rmtree(impl)  # git still lists it until pruned
        r = py(WORKSPACE, "init-impl", "--state", str(state))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((impl / "README.md").is_file())

    def test_init_impl_refuses_an_existing_branch_and_remove_docs_checks_its_branch(self):
        repo = self.tmp / "app"
        sha = make_repo(repo)
        r = py(WORKSPACE, "init-docs", "--main-checkout", str(repo), "--topic", "t", "--start-sha", sha)
        kv = dict(l.split(" ", 1) for l in r.stdout.strip().splitlines())
        state = Path(kv["RUN_DIR"]) / "state.json"
        base = {"TOPIC": "t", "REPOS": "none", "MAIN_CHECKOUT": str(repo), "START_SHA": sha,
                "RUN_DIR": kv["RUN_DIR"], "DOCS_WORKTREE": kv["DOCS_WORKTREE"]}
        # FEATURE_BRANCH naming the user's own branch (here the base branch, at the start commit) is refused.
        state.write_text(json.dumps({**base, "FEATURE_BRANCH": "main"}))
        r = py(WORKSPACE, "init-impl", "--state", str(state))
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)
        self.assertEqual(sh("git", "-C", str(repo), "rev-parse", "main"), sha)
        # remove-docs deletes only the run's docs branch, never one the docs worktree was switched to.
        sh("git", "-C", kv["DOCS_WORKTREE"], "switch", "-q", "-c", "other")
        r = py(WORKSPACE, "remove-docs", "--state", str(state))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not mission/t-docs", r.stderr)
        self.assertTrue(Path(kv["DOCS_WORKTREE"]).exists())

    def test_init_impl_rolls_back_a_partial_failure(self):
        ws = self.tmp / "parent"
        sha_a = make_repo(ws / "a")
        sha_b = make_repo(ws / "b")
        sh("git", "-C", str(ws / "b"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
           "--allow-empty", "-m", "moved")
        sh("git", "-C", str(ws / "b"), "branch", "feat/x", "HEAD")  # exists, but not at the start commit
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        fm = run_dir / "flight-manifest.txt"
        fm.write_text("FEATURE_BRANCH=feat/x\n")
        state = run_dir / "state.json"
        state.write_text(json.dumps({
            "TOPIC": "x", "WORKSPACE_DIR": str(ws), "REPOS": "a b", "RUN_DIR": str(run_dir),
            "FLIGHT_MANIFEST": str(fm), "REPO_PATH_a": str(ws / "a"), "REPO_PATH_b": str(ws / "b"),
            "BASE_BRANCH_a": "main", "BASE_BRANCH_b": "main", "START_SHA_a": sha_a, "START_SHA_b": sha_b}))
        r = py(WORKSPACE, "init-impl", "--state", str(state))
        self.assertEqual(r.returncode, 1)
        self.assertIn("feat/x already exists", r.stderr)
        for b in ("feat/x", "mission/x-impl"):  # repo a is back as it was
            self.assertNotEqual(subprocess.run(["git", "-C", str(ws / "a"), "show-ref", "--verify", "-q",
                                                f"refs/heads/{b}"]).returncode, 0, b)

    def test_prune_removes_finished_and_orphaned_runs_only(self):
        repos = {name: self.tmp / name for name in ("done", "live", "gone")}
        states = {}
        for name, repo in repos.items():
            sha = make_repo(repo)
            r = py(WORKSPACE, "init-docs", "--main-checkout", str(repo), "--topic", name, "--start-sha", sha)
            kv = dict(l.split(" ", 1) for l in r.stdout.strip().splitlines())
            st = Path(kv["RUN_DIR"]) / "state.json"
            st.write_text(json.dumps({"TOPIC": name, "REPOS": "none", "MAIN_CHECKOUT": str(repo), "START_SHA": sha,
                                      "RUN_DIR": kv["RUN_DIR"], "DOCS_WORKTREE": kv["DOCS_WORKTREE"],
                                      "PHASE": "done" if name == "done" else "execution"}))
            states[name] = Path(kv["DOCS_WORKTREE"])
        shutil.rmtree(repos["gone"])
        dry = py(WORKSPACE, "prune", "--all")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("DONE done", dry.stdout)
        self.assertIn("ORPHANED gone", dry.stdout)
        self.assertIn("KEEP live", dry.stdout)
        self.assertTrue(all(d.exists() for d in states.values()))  # dry run removes nothing
        r = py(WORKSPACE, "prune", "--all", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(states["done"].exists())
        self.assertFalse(states["gone"].exists())
        self.assertTrue(states["live"].exists())
        self.assertNotEqual(subprocess.run(["git", "-C", str(repos["done"]), "show-ref", "--verify", "-q",
                                            "refs/heads/mission/done-docs"]).returncode, 0)

    def test_init_impl_keeps_a_pr_target_from_the_manifest(self):
        repo = self.tmp / "app"
        sha = make_repo(repo)
        r = py(WORKSPACE, "init-docs", "--main-checkout", str(repo), "--topic", "t", "--start-sha", sha)
        run_dir = Path(dict(l.split(" ", 1) for l in r.stdout.strip().splitlines())["RUN_DIR"])
        state = run_dir / "state.json"
        state.write_text(json.dumps({"TOPIC": "t", "REPOS": "none", "MAIN_CHECKOUT": str(repo), "START_SHA": sha,
                                     "FEATURE_BRANCH": "feat/t", "RUN_DIR": str(run_dir), "BASE_BRANCH": "main",
                                     "PR_TARGET": "release"}))
        self.assertEqual(py(WORKSPACE, "init-impl", "--state", str(state)).returncode, 0)
        self.assertEqual(json.loads(state.read_text())["PR_TARGET"], "release")

    def test_excludes_are_one_marked_block_removed_with_the_last_run(self):
        repo = self.tmp / "app"
        sha = make_repo(repo)
        exclude = repo / ".git" / "info" / "exclude"
        # The user's own lines, and one line an older version wrote unmarked.
        exclude.write_text("# mine\n*.log\nnode_modules/\n")
        runs = {}
        for topic in ("a", "b"):
            r = py(WORKSPACE, "init-docs", "--main-checkout", str(repo), "--topic", topic, "--start-sha", sha)
            self.assertEqual(r.returncode, 0, r.stderr)
            kv = dict(l.split(" ", 1) for l in r.stdout.strip().splitlines())
            st = Path(kv["RUN_DIR"]) / "state.json"
            st.write_text(json.dumps({"TOPIC": topic, "REPOS": "none", "MAIN_CHECKOUT": str(repo), "START_SHA": sha,
                                      "RUN_DIR": kv["RUN_DIR"], "DOCS_WORKTREE": kv["DOCS_WORKTREE"]}))
            runs[topic] = st
        lines = exclude.read_text().splitlines()
        self.assertEqual(lines[:3], ["# mine", "*.log", "node_modules/"])
        self.assertEqual(lines.count("node_modules/"), 1)  # not added again inside the block
        self.assertEqual(lines.count(".mission/"), 1)  # the second run added nothing
        self.assertEqual(lines[3], "# >>> mission-control: run worktree ignores (removed with the repo's last "
                                   "mission-control worktree)")
        self.assertEqual(lines[-1], "# <<< mission-control")
        # The block stays while another run's worktree is left, and goes with the last one.
        self.assertEqual(py(WORKSPACE, "remove-docs", "--state", str(runs["a"])).returncode, 0)
        self.assertIn(".mission/", exclude.read_text())
        self.assertEqual(py(WORKSPACE, "remove-docs", "--state", str(runs["b"])).returncode, 0)
        self.assertEqual(exclude.read_text(), "# mine\n*.log\nnode_modules/\n")

    def test_prune_never_removes_a_run_whose_state_is_unreadable(self):
        repo = self.tmp / "app"
        sha = make_repo(repo)
        docs = {}
        for topic in ("broken", "missing"):
            r = py(WORKSPACE, "init-docs", "--main-checkout", str(repo), "--topic", topic, "--start-sha", sha)
            docs[topic] = Path(dict(l.split(" ", 1) for l in r.stdout.strip().splitlines())["DOCS_WORKTREE"])
        (docs["broken"] / ".mission" / "state.json").write_text("{not json")
        for args in ((), ("--apply",)):
            r = py(WORKSPACE, "prune", "--main-checkout", str(repo), *args)
            self.assertEqual(r.returncode, 0, r.stderr)
            for topic, d in docs.items():
                self.assertIn(f"UNREADABLE {topic} {d} (state.json missing or unreadable; not removed)", r.stdout)
                self.assertTrue(d.exists())
            self.assertNotIn("REMOVED", r.stdout)

    def test_validate_meta_requires_repo_in_multi_repo(self):
        ws = self.tmp / "val"
        tasks = ws / "tasks"
        tasks.mkdir(parents=True)
        for tid, extra in (("01", "REPO=my-api\n"), ("02", ""), ("03", "REPO=unknown\n")):
            (tasks / f"{tid}.md").write_text("task\n")
            (tasks / f"{tid}.meta").write_text(META.format(title=f"T{tid}", acs=f"AC-{int(tid)}", extra=extra))
            (tasks / f"{tid}-summary.md").write_text("sum\n")
            (tasks / f"{tid}-decisions.md").write_text("none\n")
        state = ws / "state.json"
        state.write_text(json.dumps({
            "CATALOG": str(CATALOG),
            "ROLE_DIRECTOR": "antigravity:big@high",
            "ROLE_CREW": "antigravity:g@high",
            "CREW_TOP": "xhigh",
            "REPOS": "my-api web-app",
        }))
        r = py(VALIDATE, "tasks", "--state", str(state), "--tasks-dir", str(tasks), "--ids", "01 02 03")
        self.assertEqual(r.returncode, 1)
        self.assertIn("OK 01", r.stdout)
        self.assertIn("PROBLEM 02: missing REPO", r.stdout)
        self.assertIn("PROBLEM 03: REPO 'unknown' not in run REPOS", r.stdout)

    def test_multi_repo_run_task_and_run_finish(self):
        ws = self.tmp / "e2e"
        ws.mkdir()
        sha_api = make_repo(ws / "my-api")
        sha_web = make_repo(ws / "web-app")

        r = py(WORKSPACE, "init-docs", "--workspace", str(ws), "--topic", "multi",
               "--repo", f"my-api={ws / 'my-api'}@{sha_api}",
               "--repo", f"web-app={ws / 'web-app'}@{sha_web}")
        self.assertEqual(r.returncode, 0, r.stderr)
        kv = dict(line.split(" ", 1) for line in r.stdout.strip().splitlines())
        docs_dir = Path(kv["DOCS_DIR"])
        run_dir = Path(kv["RUN_DIR"])
        (docs_dir / "tasks").mkdir(parents=True, exist_ok=True)

        design = docs_dir / "design.md"
        design.write_text("Acceptance criteria: AC-1, AC-2\n")
        decisions = docs_dir / "decisions.md"
        decisions.write_text("none\n")

        for tid, repo, ac in (("01", "my-api", "AC-1"), ("02", "web-app", "AC-2")):
            (docs_dir / "tasks" / f"{tid}.md").write_text(f"Task {tid} in {repo}\nDelivers {ac}\n")
            (docs_dir / "tasks" / f"{tid}.meta").write_text(
                META.format(title=f"Task {tid}", acs=ac, extra=f"REPO={repo}\n")
            )
            (docs_dir / "tasks" / f"{tid}-summary.md").write_text(f"Summary {tid}\n")
            (docs_dir / "tasks" / f"{tid}-decisions.md").write_text("none\n")

        rv = docs_dir / "flight-manifest.txt"
        rv.write_text(
            "FEATURE_BRANCH=feat/multi\n"
            "VERIFY_my_api_1=test -f README.md\n"
            "VERIFY_web_app_1=test -f README.md\n"
        )
        agy = self.tmp / "fake-agy"
        agy.write_text(f'#!/bin/sh\nexec {sys.executable} -I {FAKE} "$@"\n')
        agy.chmod(0o755)
        world = self.tmp / "world.json"
        world.write_text(json.dumps({
            "seq": 0,
            "behaviors": [
                {"do": "commit"}, {"do": "pass"}, {"do": "pass"},  # task 01 in my-api
                {"do": "commit"}, {"do": "pass"}, {"do": "pass"},  # task 02 in web-app
                {"do": "pass"},  # final review
            ],
            "calls": [],
        }))

        state = run_dir / "state.json"
        state.write_text(json.dumps({
            "AGY": str(agy),
            "TOPIC": "multi",
            "WORKSPACE_DIR": str(ws),
            "REPOS": "my-api web-app",
            "DOCS_WORKTREE": kv["DOCS_WORKTREE"],
            "DOCS_DIR": str(docs_dir),
            "RUN_DIR": str(run_dir),
            "FLIGHT_MANIFEST": str(rv),
            "DESIGN_FILE": str(design),
            "DECISIONS_LOG": str(decisions),
            "CONVENTION_PATHS": "README.md",
            "ROLE_DIRECTOR": "antigravity:big@high",
            "ROLE_SAFETY": "antigravity:g@high",
            "ROLE_CREW": "antigravity:g@high",
            "CREW_TOP": "xhigh",
            "CATALOG": str(CATALOG),
            "REPO_PATH_my_api": str(ws / "my-api"),
            "REPO_PATH_web_app": str(ws / "web-app"),
            "BASE_BRANCH_my_api": "main",
            "BASE_BRANCH_web_app": "main",
            "START_SHA_my_api": sha_api,
            "START_SHA_web_app": sha_web,
            "DOCS_WT_my_api": kv["DOCS_WT_my_api"],
            "DOCS_WT_web_app": kv["DOCS_WT_web_app"],
        }))
        (run_dir / "queue.txt").write_text("01 02\n")

        r_ii = py(WORKSPACE, "init-impl", "--state", str(state))
        self.assertEqual(r_ii.returncode, 0, r_ii.stderr)

        env = {**os.environ, "FAKE_AGY_WORLD": str(world)}
        r_task = py(RUN_TASK, "--state", str(state), "advance", env=env)
        self.assertEqual(r_task.returncode, 0, r_task.stdout + r_task.stderr)
        lines = r_task.stdout.strip().splitlines()
        self.assertEqual(lines[-1], "QUEUE_EMPTY")
        self.assertEqual(sum(1 for l in lines if l.startswith("MERGED ")), 2)

        st = json.loads(state.read_text())
        self.assertNotEqual(st["LAST_MERGED_my_api"], sha_api)
        self.assertNotEqual(st["LAST_MERGED_web_app"], sha_web)
        self.assertEqual(sh("git", "-C", str(ws / "my-api"), "rev-parse", "refs/heads/feat/multi"),
                         st["LAST_MERGED_my_api"])
        self.assertEqual(sh("git", "-C", str(ws / "web-app"), "rev-parse", "refs/heads/feat/multi"),
                         st["LAST_MERGED_web_app"])

        r_fin = py(RUN_FINISH, "--state", str(state), "advance", env=env)
        self.assertEqual(r_fin.returncode, 0, r_fin.stdout + r_fin.stderr)
        fin_last = r_fin.stdout.strip().splitlines()[-1]
        self.assertTrue(fin_last.startswith("VERIFIED "), fin_last)
        verify_log = Path(fin_last.split(" ", 1)[1]).read_text()
        self.assertIn("=== repo: my-api (exit=0) ===", verify_log)
        self.assertIn("=== repo: web-app (exit=0) ===", verify_log)

        # Final review spec included both repos' ranges
        fin_spec = (run_dir / "specs" / "final-review-1.txt").read_text()
        self.assertIn(f"my-api: {sha_api}..{st['LAST_MERGED_my_api']}", fin_spec)
        self.assertIn(f"web-app: {sha_web}..{st['LAST_MERGED_web_app']}", fin_spec)

    def test_nested_repos_detect_mount_and_cleanup(self):
        ws = self.tmp / "nested"
        ws.mkdir()
        sha_api = make_repo(ws / "services" / "auth-api")
        sha_web = make_repo(ws / "clients" / "web-app")
        # A nested git repo inside auth-api should be pruned (not reported separately)
        make_repo(ws / "services" / "auth-api" / "embedded-fixture")

        r = py(WORKSPACE, "detect", "--dir", str(ws))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MODE multi", r.stdout)
        self.assertIn("REPO services/auth-api services_auth_api", r.stdout)
        self.assertIn("REPO clients/web-app clients_web_app", r.stdout)
        self.assertNotIn("embedded-fixture", r.stdout)

        r_docs = py(WORKSPACE, "init-docs", "--workspace", str(ws), "--topic", "nested-run",
                    "--repo", f"services/auth-api={ws / 'services' / 'auth-api'}@{sha_api}",
                    "--repo", f"clients/web-app={ws / 'clients' / 'web-app'}@{sha_web}")
        self.assertEqual(r_docs.returncode, 0, r_docs.stderr)
        kv = dict(line.split(" ", 1) for line in r_docs.stdout.strip().splitlines())
        docs_wt = Path(kv["DOCS_WORKTREE"])
        self.assertTrue((docs_wt / "services" / "auth-api" / "README.md").is_file())
        self.assertTrue((docs_wt / "clients" / "web-app" / "README.md").is_file())

        rv = Path(kv["DOCS_DIR"]) / "flight-manifest.txt"
        rv.write_text("FEATURE_BRANCH=feat/nested\n")
        state = Path(kv["RUN_DIR"]) / "state.json"
        state.write_text(json.dumps({
            "TOPIC": "nested-run",
            "WORKSPACE_DIR": str(ws),
            "REPOS": "services/auth-api clients/web-app",
            "DOCS_WORKTREE": str(docs_wt),
            "DOCS_DIR": kv["DOCS_DIR"],
            "RUN_DIR": kv["RUN_DIR"],
            "FLIGHT_MANIFEST": str(rv),
            "REPO_PATH_services_auth_api": str(ws / "services" / "auth-api"),
            "REPO_PATH_clients_web_app": str(ws / "clients" / "web-app"),
            "BASE_BRANCH_services_auth_api": "main",
            "BASE_BRANCH_clients_web_app": "main",
            "START_SHA_services_auth_api": sha_api,
            "START_SHA_clients_web_app": sha_web,
            "DOCS_WT_services_auth_api": kv["DOCS_WT_services_auth_api"],
            "DOCS_WT_clients_web_app": kv["DOCS_WT_clients_web_app"],
        }))

        r_impl = py(WORKSPACE, "init-impl", "--state", str(state))
        self.assertEqual(r_impl.returncode, 0, r_impl.stderr)
        st = json.loads(state.read_text())
        self.assertTrue((Path(st["IMPL_WORKTREE"]) / "services" / "auth-api" / "README.md").is_file())
        self.assertTrue((Path(st["IMPL_WORKTREE"]) / "clients" / "web-app" / "README.md").is_file())

        # remove-impl prunes nested empty parent directories as well as IMPL_WORKTREE itself
        r_ri = py(WORKSPACE, "remove-impl", "--state", str(state))
        self.assertEqual(r_ri.returncode, 0, r_ri.stderr)
        self.assertFalse(Path(st["IMPL_WORKTREE"]).exists())

        r_rd = py(WORKSPACE, "remove-docs", "--state", str(state))
        self.assertEqual(r_rd.returncode, 0, r_rd.stderr)
        self.assertFalse(docs_wt.exists())


if __name__ == "__main__":
    unittest.main()
