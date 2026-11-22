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

        # init-docs creates hidden .mission-control/auth-docs with mounted repo worktrees
        r = py(WORKSPACE, "init-docs", "--workspace", str(ws), "--topic", "auth",
               "--repo", f"my-api={ws / 'my-api'}@{sha_api}",
               "--repo", f"web-app={ws / 'web-app'}@{sha_web}")
        self.assertEqual(r.returncode, 0, r.stderr)
        kv = dict(line.split(" ", 1) for line in r.stdout.strip().splitlines())
        docs_wt = Path(kv["DOCS_WORKTREE"])
        run_dir = Path(kv["RUN_DIR"])
        docs_dir = Path(kv["DOCS_DIR"])
        self.assertEqual(docs_wt, (ws / ".mission-control" / "auth-docs").resolve())
        self.assertTrue((docs_wt / "my-api" / "README.md").is_file())
        self.assertTrue((docs_wt / "web-app" / "README.md").is_file())

        # Write state & run-values.txt
        rv = docs_dir / "run-values.txt"
        rv.write_text(
            "FEATURE_BRANCH=feat/auth\n"
            "VERIFY_my_api_1=test -f README.md\n"
            "VERIFY_web_app_1=test -f README.md\n"
        )
        state = run_dir / "state.json"
        state.write_text(json.dumps({
            "TOPIC": "auth",
            "WORKSPACE_DIR": str(ws),
            "REPOS": "my-api web-app",
            "DOCS_WORKTREE": str(docs_wt),
            "DOCS_DIR": str(docs_dir),
            "RUN_DIR": str(run_dir),
            "RUN_VALUES": str(rv),
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
        self.assertTrue((Path(st["IMPL_WT_my_api"]) / "README.md").is_file())
        self.assertTrue((Path(st["IMPL_WT_web_app"]) / "README.md").is_file())

        # gate.py --repo runs per-repo checks
        r_gate = py(GATE, "--run-values", str(rv), "--worktree", st["IMPL_WT_my_api"],
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

        rv = docs_dir / "run-values.txt"
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
            "RUN_VALUES": str(rv),
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

        rv = Path(kv["DOCS_DIR"]) / "run-values.txt"
        rv.write_text("FEATURE_BRANCH=feat/nested\n")
        state = Path(kv["RUN_DIR"]) / "state.json"
        state.write_text(json.dumps({
            "TOPIC": "nested-run",
            "WORKSPACE_DIR": str(ws),
            "REPOS": "services/auth-api clients/web-app",
            "DOCS_WORKTREE": str(docs_wt),
            "DOCS_DIR": kv["DOCS_DIR"],
            "RUN_DIR": kv["RUN_DIR"],
            "RUN_VALUES": str(rv),
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
