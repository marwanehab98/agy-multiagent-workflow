# Capsule Communicator: Step 5: Finish

Part of mission-control. Everything in `<SKILL_DIR>/references/capsule-communicator.md` ("How you work", "Messages", "State keys") and the Rules in `<SKILL_DIR>/skills/mission/SKILL.md` hold here.

**Items 1–3: final review and verification.** Run `FINISH` and branch on its last line. At each of items 4–7, first `STATE set STEP="5 <item number>"`.

| Last line (exit) | Do |
| --- | --- |
| `VERIFIED <log>` (0) | Go to item 4, with `<log>` as the passing verify log. |
| `DIRECTOR_JOB <job> <input>` (20) | Send job `<job>` with `INPUT=<input>`. When the Flight Director returns `RESULT`, run `FINISH --director-result <saved>` and branch again. |
| `RUN_QUEUE <F id>` (21) | `queue.txt` now holds that task: go to Step 4. Its `QUEUE_EMPTY` brings you back here. |
| `CONSULT <ask_file>` (22) | Send job `consult` with `INPUT=<ask_file>`. When the Flight Director returns `RESULT: ANSWER <answer_file>`, run `FINISH --answer <answer_file>` and branch again. |
| `PROMPT <file> <reply>...` (30) | A fixed prompt, handled as in Step 4, with `FINISH --reply <word>`. |
| `PAUSED <file>` (41) | Handled as in Step 4. |
| `STOP <reason>` (40) | Dispatch nothing more and go to item 7. |

4. **Open the PR (or finish locally):**
   - **First, check the target moved** (once per run; skip when `BASE_DRIFT_OK` is `yes`): run `bash <SKILL_DIR>/scripts/base-drift.sh <MAIN_CHECKOUT> <PR_TARGET> <FEATURE_BRANCH>` (multi-repo: for each touched repo, with `<REPO_PATH_<k>> <PR_TARGET_<k>> <FEATURE_BRANCH_<k>>`).
     - **Exit 0 (`BASE_CURRENT`) everywhere:** `STATE set BASE_DRIFT_OK=yes` and go on.
     - **Exit 1 (`BASE_AHEAD ...`) anywhere:** show its output exactly, then say exactly "`<PR_TARGET>` moved while this run worked. Reply \"continue\" to land the branch as it is (a PR will show any conflicts), or \"keep\" to keep the branch without landing it." This is a fixed prompt. "continue": `STATE set BASE_DRIFT_OK=yes` and go on. "keep": skip to item 6.
     - **Exit 64:** show its output and go on.
   - **Single-repo (`REPOS=none`):**
     1. **When `START_HOW` is `no-remote` and `REQUIREMENTS_SOURCE` is `written`:** skip `job=pr` (there is no remote to push to). Invoke `touchdown` for `<FEATURE_BRANCH>` with target `<PR_TARGET>`, telling it `START_HOW=no-remote` (omit the Push/PR option and offer only local merge or keep as-is), that the branch is not checked out anywhere, that it has already been verified (`<the passing verify log>`), and to skip its own worktree cleanup. Carry out the user's choice, using `bash <SKILL_DIR>/scripts/merge-local.sh <MAIN_CHECKOUT> <PR_TARGET> <FEATURE_BRANCH>` for a local merge (never through that skill or a hand-typed git command; on exit 2 or 3 show its output and offer the options again).
     2. **Otherwise:** send job `pr` with `INPUT=<the passing verify log>`. On `RESULT: PR_READY <title> <body>`, invoke `touchdown` for `<FEATURE_BRANCH>` with target `<PR_TARGET>`, title `$(cat <title>)`, and body file `<body>`. Tell it the branch is not checked out anywhere (it pushes with `git -C <MAIN_CHECKOUT> push -u origin <FEATURE_BRANCH>`), that it has already been verified (the log above), and to skip its own worktree cleanup. For `jira`, tell it to push and create the PR immediately, without presenting options; for `written`, let it offer its usual options and carry out the user's choice, with one change: a local merge never goes through that skill or a hand-typed git command. Run `bash <SKILL_DIR>/scripts/merge-local.sh <MAIN_CHECKOUT> <PR_TARGET> <FEATURE_BRANCH>` and show its output exactly. On exit 2 or 3 nothing changed: show its output, then offer the options again.
     3. Log the PR URL (if a PR was created).
   - **Multi-repo:** determine the touched repos (each `<r>` in `REPOS` where `LAST_MERGED_<k> != INITIAL_BASE_SHA_<k>`). Send job `pr` with `INPUT=<the passing verify log>` unless every touched repo has `START_HOW_<k>=no-remote` and `REQUIREMENTS_SOURCE=written` (for multi-repo, `job=pr` writes per-repo title/body files and lists `<repo> <title> <body>` in a manifest, or returns `PR_READY <manifest>`). Then for each touched repo `<r>` (key `<k>`), run the same `touchdown` flow against `<REPO_PATH_<k>>` for `<FEATURE_BRANCH_<k>>` targeting `<PR_TARGET_<k>>` (using `bash <SKILL_DIR>/scripts/merge-local.sh <REPO_PATH_<k>> <PR_TARGET_<k>> <FEATURE_BRANCH_<k>>` for local merges), and log each PR URL.
5. **Update JIRA** (`jira` with a PR only): send job `jira-close` with `INPUT=<PR URL(s)>`, and log the `RESULT: JIRA_DONE` line.
6. **Clean up:** run `python3 -I <SKILL_DIR>/scripts/workspace.py remove-impl --state <RUN_DIR>/state.json`, whatever the user chose: every task is merged into the feature branch, so it removes only the implementation worktree(s) and the `mission/<TOPIC>-impl` branch, never the feature branch. If it fails, show the user the output.
7. **Report:** run `python3 -I <SKILL_DIR>/scripts/costs.py --state <RUN_DIR>/state.json --out <RUN_DIR>/costs.md`, then send job `report` with `INPUT=<RUN_DIR>/mission-log.md <RUN_DIR>/costs.md`. On `RESULT: REPORT <path>`, relay `<path>` to the user as in Rule 5, then add exactly:
   "Run documents: `<DOCS_DIR>`. When you're done with them, remove them and their `mission/<TOPIC>-docs` branch with `python3 -I <SKILL_DIR>/scripts/workspace.py remove-docs --state <RUN_DIR>/state.json` (it prints the branch's last commit, so you can restore it), or every finished run at once with `python3 -I <SKILL_DIR>/scripts/workspace.py prune --all` (it lists them first; add `--apply` to remove them)."
   Then `STATE set PHASE=done WAITING_ON=`.

