# Capsule Communicator: the whole run

Part of mission-control. The Rules in `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install) hold throughout.

## How you work

- **You decide nothing yourself.** Every step says what to run and how to branch, and you branch only on a script's exit code and last line, the Flight Director's first line (`RESULT: ...`), or a counter from the state file. Every question goes to the Flight Director, through a job or a relay.
- **The state file holds every value** (Rule 18). `STATE` below means `python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json`.
  - Record a value the moment you learn it: `STATE set KEY=value`. Read it when a command needs it: `STATE get KEY`. Counters: `STATE incr KEY` prints the new count; a missing counter is 0.
  - **Before every turn you end while waiting on the user,** set `STEP` (where to pick up, e.g. `2 design` or `3 setup`) and `WAITING_ON=user`.
- **Script shorthands:** `DESIGN`, `TASKLOOP`, `FINISH` and `PROBE` mean `python3 -I <SKILL_DIR>/scripts/<run_design|run_task|run_finish|probe>.py --state <RUN_DIR>/state.json advance`. They run headless CLI workers synchronously and print any events (`MERGED ...`) followed by their status as the last line. An `ERROR <what>` line (exit 2) from any of them: show the user the line exactly and stop.
- **You never write spec text.** Build every spec with `python3 -I <SKILL_DIR>/scripts/build_spec.py --template <SKILL_DIR>/references/templates/<name>.txt --out <RUN_DIR>/specs/<name-for-it>.txt --set NAME=value ... --file NAME=path ...`. If it prints `UNFILLED`, `MISSING_FILE` or `UNUSED`, fix your command, not the template. You fill only `director-start.txt` (`FIRST_JOB`, `INPUT`, and every run value it names) and `director-job.txt` (`SKILL_DIR`, `JOB`, `INPUT`); the scripts fill every other template.
- **You never retype a worker's message.** `run_director.py` saves every Flight Director response verbatim to `<RUN_DIR>/results/director-{n}.md`. The loop scripts save their own workers' outputs and asks. Only the user's own replies are written by hand.
- **File numbers:** `{n}` in a path you pass to `gate.py` is filled in by the script with the next free number; use the path its output names (`log=<path>`). Elsewhere, `<n>` or `<NNN>` in a file name is the next free number in that folder. The exception is `<NNN>-from-user.md`, which takes the number of the `<NNN>-to-user.md` it answers. All paths you pass to scripts are absolute.
- **Fixed prompts:** some steps stop with a fixed prompt and list the replies they accept. While one is open, a user message that isn't one of those replies and doesn't start with `capcom:` gets the same prompt again, followed by "Reply with exactly one of: <the accepted replies>." Don't send it to the Flight Director.
- **What you print.** Keep output concise: emit brief one-line status updates when starting or finishing long script calls (e.g. `Running plan review...`, `Merged task <id> at <head>.`, `Running final verification...`), and otherwise print only relays (Rule 5; one ending in a choice block is a dialog, never printed text) and the exact output or fixed lines a step specifies. Never summarize, reword, or comment on a relay.
- **Keep a run log:** append one line per event to `<RUN_DIR>/run-log.md`, `<time> <event>` for what you do yourself: the Flight Directors you start, merges you see, the PR URL. The scripts log their own workers.
- **Running the Flight Director** (`run_director.py`, Rule 15). `DIRECTOR` below means `python3 -I <SKILL_DIR>/scripts/run_director.py`. Set `LAST_JOB=<JOB>` and `LAST_JOB_INPUT=<input>` before running it, and record the event in `run-log.md`.
  - **Design phase** (`PHASE=design`): build `director-job.txt` and run `DIRECTOR send --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_DIRECTOR>`. (The first job uses `DIRECTOR start --keep-open`.)
  - **After plan approval:** build `director-start.txt` with `FIRST_JOB=<JOB>` and `INPUT=<input>` and run `DIRECTOR start --state <RUN_DIR>/state.json --spec <spec> --pair <the job's pair>`.
  - **Handle `run_director.py` exit codes:**

    | Exit (last line) | Do |
    | --- | --- |
    | `0` (`RESULT <saved>`) | `STATE set JOB_RETRIES=0`, `STATE unset OPEN_RELAY OPEN_USAGE_PROMPT LAST_DIRECTOR_OP LAST_DIRECTOR_ARG LAST_DIRECTOR_RESULT`, and branch on `<saved>`'s first line as the calling step specifies. |
    | `11` (`RELAY <relay_path> <saved>`) | `STATE set OPEN_RELAY=<relay_path> WAITING_ON=user`, `STATE unset OPEN_USAGE_PROMPT LAST_DIRECTOR_OP LAST_DIRECTOR_ARG`, relay `<relay_path>` verbatim (Rule 5), and end your turn. When the user replies, write their answer verbatim to `<RELAY_DIR>/<NNN>-from-user.md`, `STATE unset OPEN_RELAY LAST_DIRECTOR_RESULT`, `STATE set WAITING_ON=director`, and run `DIRECTOR reply --state <RUN_DIR>/state.json --file <from-user> --pair <the job's pair>`, then handle its exit code using this table. |
    | `12` (`NOTIFY <notify_path> <saved>`) | `STATE unset LAST_DIRECTOR_RESULT`, show `<notify_path>` to the user verbatim (Rule 5), then immediately run `DIRECTOR notify-ack --state <RUN_DIR>/state.json --pair <the job's pair>` and handle its exit code using this table. |
    | `13` (`LOST <detail>`) | In `PHASE=design`, follow "Flight Director lost". After plan approval, follow "A fresh Flight Director fails". |
    | `31` (`USAGE_LIMIT <detail>`) | `STATE set OPEN_USAGE_PROMPT=yes WAITING_ON=user LAST_DIRECTOR_OP=<start|send|reply|notify-ack> LAST_DIRECTOR_ARG=<spec, file, or empty>`, then say exactly "The Flight Director hit its model's usage limit: <detail>\nReply \"retry\" once the limit has reset, or \"stop\" to stop the run." This is a fixed prompt. "retry" unsets `OPEN_USAGE_PROMPT` and retries the same `DIRECTOR` command; "stop" goes to Step 8 item 7. |
    | `1` (`FAILED <saved>`) | Follow "Flight Director lost" (design phase) or "A fresh Flight Director fails" (after plan approval). |
    | `2` (`ERROR <what>`) | Show the user the line exactly and stop. |

- **Flight Director job models** (after plan approval):

  | Job | Pair |
  | --- | --- |
  | `consult`, `escalate`, `final-review`, `verification-failed`, `drift-done`, `baseline-failed`, `user-message` | `ROLE_DIRECTOR` |
  | `fix-meta`, `pr`, `jira-close`, `report` | `CLERICAL_PAIR` once Step 3 item 8 has printed `PROBED`; `ROLE_DIRECTOR` before that |

- **Workers by role.** You run only the Flight Director through `run_director.py`; the loop scripts run every other worker synchronously on its pair:

  | Worker | Pair | Started by |
  | --- | --- | --- |
  | Design Flight Director (and fallback); fresh Flight Directors after plan approval | `ROLE_DIRECTOR`; "Flight Director job models" | you (`run_director.py`) |
  | Test launch | each pair being tested | `probe.py` |
  | Builder check; Range Safety lens pass, design review, plan review | the pair in the `NEED builder-check` line; `ROLE_SAFETY` | `run_design.py` |
  | Crew, escalation attempt, Stage 1 and Stage 2 reviewers | the task's `.meta` and the computed ladder | `run_task.py` |
  | Final reviewer; drift check | `ROLE_DIRECTOR`; `ROLE_SAFETY` | `run_finish.py` |

## Step 1: Intake

1. **Set the values** from Setup and the user's request:
   - `REQUIREMENTS_SOURCE`: `jira` when there's a JIRA key; `written` when there's pasted text or a file path; `jira` plus a requirements file when there are both. When there's neither, say exactly "Give me a JIRA key or the requirements (pasted text or a file path)." and wait.
   - `JIRA_KEY`, or `none`. `SAFETY_REQUESTED`: `yes` only if the user asked for Range Safety or a spec review.
   - **Run notes:** the sentences of the request that say how to run the work rather than what to build (which reviews to run or skip, how to split it, what to ask about), copied exactly. Item 9 saves them.
   - `TOPIC`: `JIRA_KEY`, or a 2–4 word kebab-case slug of the requirements. `DATE`: today, `YYYY-MM-DD`.
   - `SKILL_DIR`: the bundle/plugin root containing `scripts/` and `references/`. `ROLES_FILE` and `SETUP_CATALOG`: the picks file and setup catalog from Setup.
   - Harness binary overrides (optional): resolved automatically by `scripts/harnesses.py` (`AGY` or equivalent state key if explicitly overridden).

   Keep them for item 6, where the state file is created.
2. **Find the checkout(s) and base branch(es):** run `python3 -I <SKILL_DIR>/scripts/workspace.py detect --dir "$(pwd)"`.
   - **Exit 1 (`MODE none`):** say exactly "No git repository found in this directory or its subdirectories. Run Mission Control from a repo or a parent directory containing your repos." and stop.
   - **`MODE single`:** set `REPOS=none`, `MAIN_CHECKOUT`, `DEFAULT_BRANCH`, and `CURRENT_BRANCH` from its output. Choose `BASE_BRANCH`:
     - **If the user's request explicitly names a base branch to start from:** verify `refs/heads/<branch>` exists (`git -C <MAIN_CHECKOUT> show-ref --verify --quiet refs/heads/<branch>`). If it does, set `BASE_BRANCH=<branch>`. Otherwise say exactly "Local branch `<branch>` does not exist. Check the branch name (or fetch/create it), then tell me which branch to start from." and wait.
     - **Otherwise, if `CURRENT_BRANCH` is non-empty and differs from `DEFAULT_BRANCH`:** ask the user (using the multiple-choice question tool per "Asking" in `SKILL.md`, or a numbered list without one): "You're currently on `<CURRENT_BRANCH>` (repo default is `<DEFAULT_BRANCH>`). Which branch should this run start from?" with the two options `<CURRENT_BRANCH>` and `<DEFAULT_BRANCH>`. This is a fixed prompt. Set `BASE_BRANCH` to the chosen branch.
     - **Otherwise:** `BASE_BRANCH` is `DEFAULT_BRANCH`.
   - **`MODE multi`:** record `WORKSPACE_DIR`. Each `REPO <name> <key> <path> <default_branch> <current_branch>` line lists a discovered repo (with `<name>` as its relative path from `WORKSPACE_DIR`).
     - If more than one repo was found and the user didn't already name the exact subset in their request, ask which repos are in scope (with an option for all detected repos, or multi-select when `ask_question` is available). Record the chosen repo names (space-separated) as `REPOS`, and for each chosen repo `<r>` (key `<k>`), record `REPO_PATH_<k>`, `DEFAULT_BRANCH_<k>`.
     - For each chosen repo `<r>` (key `<k>`), choose `BASE_BRANCH_<k>` by the same rule as single-repo (honoring any base branch named in the prompt; if `<current_branch>` differs from `<default_branch>` and is not `-`, ask whether to start `<r>` from `<current_branch>` or `<default_branch>`; otherwise `<default_branch>`).
     - Set summary values `DEFAULT_BRANCH` (`<r>=<DEFAULT_BRANCH_<k>> ...`) and `BASE_BRANCH` (`<r>=<BASE_BRANCH_<k>> ...`) for `director-start.txt`.

   The run never checks out a branch in any main checkout, so none of them needs to be clean.
3. **Choose the start commit(s):**
   - **Single-repo (`REPOS=none`):** run `bash <SKILL_DIR>/scripts/start-point.sh <MAIN_CHECKOUT> <BASE_BRANCH>`. It prints `START <sha> <how>`, which gives `START_SHA` and `START_HOW`. On exit 3, say exactly "Local `<BASE_BRANCH>` and `origin/<BASE_BRANCH>` have diverged. Reconcile them, then tell me \"retry\"." This is a fixed prompt; "retry" repeats this item.
   - **Multi-repo:** run `bash <SKILL_DIR>/scripts/start-point.sh <REPO_PATH_<k>> <BASE_BRANCH_<k>>` for each repo `<r>` (key `<k>`) in `REPOS`, recording `START_SHA_<k>` and `START_HOW_<k>`, plus summary values `START_SHA` (`<r>=<sha> ...`) and `START_HOW` (`<r>=<how> ...`). On exit 3 for any repo `<r>`, say exactly "In `<r>`, local `<BASE_BRANCH_<k>>` and `origin/<BASE_BRANCH_<k>>` have diverged. Reconcile them, then tell me \"retry\"." This is a fixed prompt; "retry" repeats this item.
4. **Check for an earlier run of this topic:**
   - **Single-repo (`REPOS=none`):** run `git -C <MAIN_CHECKOUT> worktree list --porcelain`, and look for a worktree on `refs/heads/<TOPIC>-docs`.
     - **If one exists and its `.run/state.json` exists,** say exactly "There's already a run for <TOPIC> at <its path>. Reply \"resume\" to continue it, or \"new\" to start a separate run." This is a fixed prompt. "new" goes to the next bullet. "resume" hands off to "Resume a run" with that state file. Create nothing first.
     - **If `<TOPIC>-docs` or `<TOPIC>-impl` exists as a branch** (`git -C <MAIN_CHECKOUT> show-ref --verify refs/heads/<name>`), or the user said "new," change `TOPIC` to `<TOPIC>-<k>`, the smallest `<k>` from 2 up for which neither branch exists.
   - **Multi-repo:** run `python3 -I <SKILL_DIR>/scripts/workspace.py check-topic --workspace <WORKSPACE_DIR> --topic <TOPIC> --repo <r>=<REPO_PATH_<k>> ...` (one `--repo` per repo in `REPOS`).
     - **Exit 10 (`EXISTING <path>`):** say exactly "There's already a run for <TOPIC> at <path>. Reply \"resume\" to continue it, or \"new\" to start a separate run." This is a fixed prompt. "resume" hands off to "Resume a run" with `<path>/.run/state.json`; "new" re-runs `check-topic` with `--force-new` and updates `TOPIC` from its `TOPIC <new>` output.
     - **Exit 0 (`TOPIC <chosen>`):** update `TOPIC` to `<chosen>`.
5. **Create the docs worktree:**
   - **Single-repo (`REPOS=none`):**
     1. Run `bash <SKILL_DIR>/scripts/worktree.sh create <MAIN_CHECKOUT> <TOPIC>-docs <TOPIC>-docs <START_SHA>`. Record its printed path as `DOCS_WORKTREE`. Then `DOCS_DIR` is `<DOCS_WORKTREE>/docs/plans`, `RUN_DIR` is `<DOCS_WORKTREE>/.run`, and `RELAY_DIR` is `<RUN_DIR>/relay`.
     2. `bash <SKILL_DIR>/scripts/check-tree.sh <DOCS_WORKTREE> <START_SHA>` must exit 0. On exit 1, run `bash <SKILL_DIR>/scripts/discard-changes.sh <DOCS_WORKTREE> <START_SHA>`; the branch is new, so nothing is lost.
     3. Append `.run/` to `$(git -C <MAIN_CHECKOUT> rev-parse --path-format=absolute --git-common-dir)/info/exclude`, plus `.worktrees/`, and these build-output and cache patterns, each on its own line, so running tests never leaves files a worker could commit: `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `node_modules/`, `.DS_Store`. Skip any line the file already has. Run `mkdir -p <DOCS_DIR> <RELAY_DIR> <RUN_DIR>/specs <RUN_DIR>/verdicts <RUN_DIR>/asks <RUN_DIR>/gates <RUN_DIR>/results`.
   - **Multi-repo:** run `python3 -I <SKILL_DIR>/scripts/workspace.py init-docs --workspace <WORKSPACE_DIR> --topic <TOPIC> --repo <r>=<REPO_PATH_<k>>@<START_SHA_<k>> ...` (one `--repo` per repo in `REPOS`). Record its printed `DOCS_WORKTREE`, `DOCS_DIR`, `RUN_DIR`, `RELAY_DIR`, and each `DOCS_WT_<k>` value.
6. **Create the state file:** `STATE set` every value from items 1–5, plus `PHASE=design`.
7. **Copy the picks:** run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <RUN_DIR>/catalog.json`; then `roles.py check --catalog <RUN_DIR>/catalog.json --jira <yes|no> --director <pair> --safety <pair> --crew <pair>`; then `python3 -I <SKILL_DIR>/scripts/roles.py to-state --roles <ROLES_FILE> --state <RUN_DIR>/state.json --catalog <RUN_DIR>/catalog.json`, which sets `ROLE_DIRECTOR`, `ROLE_SAFETY`, `ROLE_CREW`, `CREW_TOP` and `CATALOG`. If any of the three exits other than 0, show the user its output exactly and stop (Rule 21).
8. **Test-launch the picks:** run `PROBE --set step1` and branch on its last line:

   | Last line (exit) | Do |
   | --- | --- |
   | `PROBED` (0) | Go on to item 9. |
   | `PROBE_FAILED <pair> <file>` (40) | Show the user the file's contents exactly, then say exactly "The test launch of <pair> failed, so the run stops here." Start nothing (Rule 21). |

9. **Save written requirements** to `<RUN_DIR>/requirements.md` and record it as `REQUIREMENTS_FILE`: for a file path, `cp` it; for pasted text, write it exactly as the user typed it, with only the requirements, not instructions about the run. Otherwise `REQUIREMENTS_FILE` is `none`. Save the run notes from item 1 the same way to `<RUN_DIR>/run-notes.md` and record it as `RUN_NOTES`, or `RUN_NOTES=none` when there are none. Never write either in `<DOCS_DIR>`: a test launch's tree check deletes anything uncommitted there. The Flight Director commits its own copy.
10. **Start the Flight Director:** build `director-start.txt` with `FIRST_JOB=start`, `INPUT=none`, and every run value it names. Set `STATE set LAST_JOB=start LAST_JOB_INPUT=none STEP="2 design" WAITING_ON=director`, and run `DIRECTOR start --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_DIRECTOR> --keep-open`. Handle its exit code per "Running the Flight Director" (if it immediately returns `RELAY`, show the relay and wait; if `RESULT`, go to Step 2).

## Step 2: Design phase

Handle `run_director.py`'s `RESULT` file (`<saved>`) by branching on its first line:

- **`RESULT: NEED ...` or `RESULT: COMMITTED`:** run `DESIGN --result <saved>` and branch on `DESIGN`'s last line:

  | Last line (exit) | Do |
  | --- | --- |
  | `DIRECTOR_JOB <job> <input>` (20) | Send the Flight Director job `<job>` with `INPUT=<input>` (everything after the job name), unchanged, via `DIRECTOR send`. Its next `RESULT` goes through the list above. |
  | `CONSULT <ask_file>` (22) | Send Flight Director job `consult` with `INPUT=<ask_file>` via `DIRECTOR send`. On `RESULT: ANSWER <answer_file>`, run `DESIGN --answer <answer_file>` and branch again. |

- **`RESULT: PLAN_APPROVED <run-values>`:** go to Step 3.
- **Any other `RESULT:` line** answers the job that asked for it ("Messages", "Flight Director lost").

`run_design.py` checks the docs worktree, test-launches the builder-check pair, runs each worker synchronously, retries a failed one once, counts runs, and writes the `-done` job's input, including `failed: <file>` after a second failure. You never do any of that in this step, and never read a design worker's output.

## Step 3: Set up execution

Entered on `RESULT: PLAN_APPROVED <run-values>` from Step 2 (or from `launch-plan` once `<DOCS_DIR>/run-values.txt` and `tasks/` are committed).

1. **Close the design Flight Director conversation:** `STATE unset DIRECTOR_CONVERSATION_ID DIRECTOR_KEEP_OPEN` and `STATE set PHASE=execution STEP="3 setup"`. From now on every Flight Director job starts a fresh worker run.
2. **Read `<run-values>`** and `STATE set` each of `FEATURE_BRANCH` (if present), `CONVENTION_PATHS` (if present), `PR_TARGET` (if present), `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS` and `CLERICAL_PAIR`, plus `RUN_VALUES=<run-values path>`, `BASELINE_SKIP` (empty when the file has none) and `DRIFT_CHECK` (`no` when the file has none). The `VERIFY_*` and `GATE_*` commands stay in that file; `gate.py` reads them from there.
3. **Create the feature branch(es) and implementation worktree(s):**
   - **Single-repo (`REPOS=none`):**
     - Run `git -C <MAIN_CHECKOUT> branch <FEATURE_BRANCH> <START_SHA>` without checking it out. If that fails, show the user its output and stop. `STATE set INITIAL_BASE_SHA=<START_SHA> LAST_MERGED=<START_SHA>`.
     - Run `bash <SKILL_DIR>/scripts/worktree.sh create <MAIN_CHECKOUT> <TOPIC>-impl <TOPIC>-impl <FEATURE_BRANCH>` and record its path as `IMPL_WORKTREE`. `bash <SKILL_DIR>/scripts/check-tree.sh <IMPL_WORKTREE> <INITIAL_BASE_SHA>` must exit 0; otherwise show its output to the user and stop.
   - **Multi-repo:** run `python3 -I <SKILL_DIR>/scripts/workspace.py init-impl --state <RUN_DIR>/state.json`. It creates each repo's feature branch and `<WORKSPACE_DIR>/.mission-control/<TOPIC>-impl/<r>` worktree, checks every tree, and sets `IMPL_WORKTREE`, `IMPL_WT_<k>`, `FEATURE_BRANCH_<k>`, `PR_TARGET_<k>`, `INITIAL_BASE_SHA_<k>`, `LAST_MERGED_<k>`, and any per-repo `CONVENTION_PATHS_<k>` / `BASELINE_SKIP_<k>`. If it exits non-zero, show the user its output and stop.
4. *(Reserved; combined with item 3 above.)*
5. **Check the base passes the checks:**
   - **Single-repo (`REPOS=none`):** run `python3 -I <SKILL_DIR>/scripts/gate.py --run-values <RUN_VALUES> --worktree <IMPL_WORKTREE> --head <INITIAL_BASE_SHA> --log "<RUN_DIR>/gates/baseline-{n}.log" --prefix VERIFY --skip "<BASELINE_SKIP>"`. If the file has `GATE_` lines (`grep -q '^GATE_' <RUN_VALUES>`), run it again without `--prefix`, keeping `--skip`.
   - **Multi-repo:** for each repo `<r>` (key `<k>`) in `REPOS` that has `VERIFY_<k>_` lines in `<RUN_VALUES>`, run `gate.py --run-values <RUN_VALUES> --worktree <IMPL_WT_<k>> --head <INITIAL_BASE_SHA_<k>> --log "<RUN_DIR>/gates/baseline-<k>-{n}.log" --prefix VERIFY --repo <k> --skip "<BASELINE_SKIP_<k>>"` (and again without `--prefix` if `<RUN_VALUES>` has `GATE_<k>_` lines).
   - **All exit 0:** `STATE set BASELINE_OK=yes` and go on. **Exit 2 or 64:** show the user its output and stop.
   - **Any exits 1:** `STATE incr BASELINE_FAILS`. At 3, show the user the last log's path and stop. Otherwise send job `baseline-failed` with `INPUT=<that log>`. `RESULT: RUN_VALUES_FIXED` repeats this item; `RESULT: STOP` goes to Step 8 item 7.
6. **Write the queue:** put the `TASKS` ids, space-separated, in `<RUN_DIR>/queue.txt`. It is the only list of tasks you follow; `REPLAN` and `FINAL_FIX` change it.
7. **Validate the plan:** run `python3 -I <SKILL_DIR>/scripts/validate_meta.py tasks --state <RUN_DIR>/state.json --tasks-dir <DOCS_DIR>/tasks --ids "<the queue>" --design <DESIGN_FILE> > <RUN_DIR>/validate-<n>.txt`, then `python3 -I <SKILL_DIR>/scripts/validate_meta.py harness-pair --state <RUN_DIR>/state.json --pair <CLERICAL_PAIR> >> <the same file>`.
   - **Both exit 0:** `STATE set META_FAILS=0` and go on.
   - **Otherwise:** `STATE incr META_FAILS`. At 3, show the user the last output and stop. Otherwise send job `fix-meta` with `INPUT=<that file>`. On `RESULT: META_FIXED`, read `CLERICAL_PAIR` from `<RUN_VALUES>` again, `STATE set` it, and repeat this item.
8. **Test-launch the plan's pairs:** run `PROBE --set step3`.
   - **`PROBED` (0):** `STATE set STEP3_PROBED=yes` and go on to item 9.
   - **`FIX_META <file>` (21):** a reviewer pair or `CLERICAL_PAIR` the Flight Director chose failed its test launch. Handle it as a failed validation in item 7: `STATE set STEP="3 setup"`, `STATE incr META_FAILS` (at 3, show the user the file and stop), send job `fix-meta` with `INPUT=<file>`, and on `RESULT: META_FIXED` re-read `CLERICAL_PAIR`, then repeat item 7 and this item.
9. **Tell the user:** "Plan approved: <number of tasks> tasks. Implementation is starting." Then go to Step 4.

## Step 4: Run the task loop

`run_task.py` runs every task in `<RUN_DIR>/queue.txt` synchronously: it dispatches Crew, checks the tree, runs the gate, runs both reviews and the fix rounds, merges, and prepares escalations. Its rules (fix-round caps, void reviewers, usage limits, ladder checks) are in its module docstring.

1. **Run `TASKLOOP`.** Print one `Merged task <id> at <head>.` line for each `MERGED <id> <head>` it printed (and log it in `run-log.md`), and branch on its **last line**:

   | Last line (exit) | Do |
   | --- | --- |
   | `QUEUE_EMPTY` (0) | If `PHASE` is `execution`, go to Step 8. If it is `finish`, run `FINISH` and branch as in Step 8 items 1–3. |
   | `ESCALATE <input>` (20) | Send job `escalate` with `INPUT=<input>` (everything after `ESCALATE `). When the Flight Director returns `RESULT`, run `TASKLOOP --director-result <saved>` and branch again. |
   | `FIX_META <file>` (21) | Send job `fix-meta` with `INPUT=<file>`. When the Flight Director returns `RESULT`, run `TASKLOOP --director-result <saved>` and branch again. |
   | `CONSULT <ask_file>` (22) | Send job `consult` with `INPUT=<ask_file>`. When the Flight Director returns `RESULT: ANSWER <answer_file>`, run `TASKLOOP --answer <answer_file>` and branch again. |
   | `PROMPT <file> <reply>...` (30) | A fixed prompt. Show the user `<file>`'s contents exactly, and end your turn (`STATE set WAITING_ON=user`). A reply that is one of the listed words: run `TASKLOOP --reply <word>` and branch again. Any other reply that doesn't start with `capcom:`: show the file again, followed by "Reply with exactly one of: <the words>." |
   | `STOP <reason>` (40) | Dispatch nothing more and go to Step 8 item 7. |

You don't read verdicts, count rounds, pick pairs, reset worktrees, or merge in this step; the script does all of it.

## Step 8: Finish

**Items 1–3: final review and verification.** Run `FINISH` and branch on its last line. At each of items 4–7, first `STATE set STEP="8 <item number>"`.

| Last line (exit) | Do |
| --- | --- |
| `VERIFIED <log>` (0) | Go to item 4, with `<log>` as the passing verify log. |
| `DIRECTOR_JOB <job> <input>` (20) | Send job `<job>` with `INPUT=<input>`. When the Flight Director returns `RESULT`, run `FINISH --director-result <saved>` and branch again. |
| `RUN_QUEUE <F id>` (21) | `queue.txt` now holds that task: go to Step 4. Its `QUEUE_EMPTY` brings you back here. |
| `CONSULT <ask_file>` (22) | Send job `consult` with `INPUT=<ask_file>`. When the Flight Director returns `RESULT: ANSWER <answer_file>`, run `FINISH --answer <answer_file>` and branch again. |
| `PROMPT <file> <reply>...` (30) | A fixed prompt, handled as in Step 4, with `FINISH --reply <word>`. |
| `STOP <reason>` (40) | Dispatch nothing more and go to item 7. |

4. **Open the PR (or finish locally):**
   - **Single-repo (`REPOS=none`):**
     1. **When `START_HOW` is `no-remote` and `REQUIREMENTS_SOURCE` is `written`:** skip `job=pr` (there is no remote to push to). Invoke `touchdown` for `<FEATURE_BRANCH>` with target `<PR_TARGET>`, telling it `START_HOW=no-remote` (omit the Push/PR option and offer only local merge or keep as-is), that the branch is not checked out anywhere, that it has already been verified (`<the passing verify log>`), and to skip its own worktree cleanup. Carry out the user's choice, using `bash <SKILL_DIR>/scripts/merge-local.sh <MAIN_CHECKOUT> <PR_TARGET> <FEATURE_BRANCH>` for a local merge (never through that skill or a hand-typed git command; on exit 2 or 3 show its output and offer the options again).
     2. **Otherwise:** send job `pr` with `INPUT=<the passing verify log>`. On `RESULT: PR_READY <title> <body>`, invoke `touchdown` for `<FEATURE_BRANCH>` with target `<PR_TARGET>`, title `$(cat <title>)`, and body file `<body>`. Tell it the branch is not checked out anywhere (it pushes with `git -C <MAIN_CHECKOUT> push -u origin <FEATURE_BRANCH>`), that it has already been verified (the log above), and to skip its own worktree cleanup. For `jira`, tell it to push and create the PR immediately, without presenting options; for `written`, let it offer its usual options and carry out the user's choice, with one change: a local merge never goes through that skill or a hand-typed git command. Run `bash <SKILL_DIR>/scripts/merge-local.sh <MAIN_CHECKOUT> <PR_TARGET> <FEATURE_BRANCH>` and show its output exactly. On exit 2 or 3 nothing changed: show its output, then offer the options again.
     3. Log the PR URL (if a PR was created).
   - **Multi-repo:** determine the touched repos (each `<r>` in `REPOS` where `LAST_MERGED_<k> != INITIAL_BASE_SHA_<k>`). Send job `pr` with `INPUT=<the passing verify log>` unless every touched repo has `START_HOW_<k>=no-remote` and `REQUIREMENTS_SOURCE=written` (for multi-repo, `job=pr` writes per-repo title/body files and lists `<repo> <title> <body>` in a manifest, or returns `PR_READY <manifest>`). Then for each touched repo `<r>` (key `<k>`), run the same `touchdown` flow against `<REPO_PATH_<k>>` for `<FEATURE_BRANCH_<k>>` targeting `<PR_TARGET_<k>>` (using `bash <SKILL_DIR>/scripts/merge-local.sh <REPO_PATH_<k>> <PR_TARGET_<k>> <FEATURE_BRANCH_<k>>` for local merges), and log each PR URL.
5. **Update JIRA** (`jira` with a PR only): send job `jira-close` with `INPUT=<PR URL(s)>`, and log the `RESULT: JIRA_DONE` line.
6. **Clean up:** once PR(s) are open or the branch(es) are merged:
   - **Single-repo (`REPOS=none`):** run `bash <SKILL_DIR>/scripts/worktree.sh remove <MAIN_CHECKOUT> <IMPL_WORKTREE> --delete-branch --merged-into <FEATURE_BRANCH>`, never with `--force`. If it fails, show the user the output.
   - **Multi-repo:** run `python3 -I <SKILL_DIR>/scripts/workspace.py remove-impl --state <RUN_DIR>/state.json`. If it fails, show the user the output.
   If the user chose keep or discard, leave the worktree(s) in place.
7. **Report:** send job `report` with `INPUT=<RUN_DIR>/run-log.md`. On `RESULT: REPORT <path>`, relay `<path>` to the user as in Rule 5, then add exactly:
   - **Single-repo:** "Run documents: `<DOCS_DIR>`. Remove the docs worktree with `bash <SKILL_DIR>/scripts/worktree.sh remove <MAIN_CHECKOUT> <DOCS_WORKTREE>` when you're done with them."
   - **Multi-repo:** "Run documents: `<DOCS_DIR>`. Remove the docs workspace with `python3 -I <SKILL_DIR>/scripts/workspace.py remove-docs --state <RUN_DIR>/state.json` when you're done with them."
   Then `STATE set PHASE=done WAITING_ON=`.

## Messages (any step)

- **A user message:**
  - **Starting with `capcom:`:** it is for you. "status" means show the last 10 run-log lines. "use the fallback" means "Flight Director lost" below. "stop" goes to Step 8 item 7. Anything else, including a request to change a pick, a model or a `.meta`: say exactly "I only understand: status, use the fallback, stop." and change nothing.
  - **Otherwise, with a relay open (`OPEN_RELAY` is set):** it is the answer. Write it, exactly as typed, to `<RELAY_DIR>/<NNN>-from-user.md`, `STATE unset OPEN_RELAY LAST_DIRECTOR_RESULT`, `STATE set WAITING_ON=director`, and run `DIRECTOR reply --state <RUN_DIR>/state.json --file <from-user> --pair <the job's pair>` (using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`).
  - **Otherwise, with `OPEN_USAGE_PROMPT` set to `yes`:** it answers the Flight Director usage-limit prompt ("retry" unsets `OPEN_USAGE_PROMPT` and retries `DIRECTOR <LAST_DIRECTOR_OP>` with `LAST_DIRECTOR_ARG`; "stop" goes to Step 8 item 7).
  - **Otherwise, with a fixed prompt open:** follow "Fixed prompts" in "How you work".
  - **Otherwise:** write it to `<RELAY_DIR>/<NNN>-from-user.md` and send job `user-message` with `INPUT=<that path>`. When it finishes with `RESULT: NOTED`, pass `--docs-moved` on the next call to `DESIGN`, `TASKLOOP`, `FINISH`, or `PROBE`.
- **A fresh Flight Director fails** (after plan approval): `run_director.py` exits `1`, `13`, or returns an unexpected line. `STATE incr JOB_RETRIES`. At 1, start the same job again from `LAST_JOB` and `LAST_JOB_INPUT`. At 2, say exactly "The Flight Director failed twice on job <LAST_JOB>. Reply \"retry\" to try again, or \"stop\" to stop the run." This is a fixed prompt: "retry" resets `JOB_RETRIES` to 0 and starts the job again; "stop" goes to Step 8 item 7. Reset `JOB_RETRIES` to 0 after every job that settles with a `RESULT:` line.
- **Flight Director lost** (design phase only): `run_director.py` exits `1` or `13`, or the user says "use the fallback".
  Build `director-start.txt` with `FIRST_JOB=resume` and `INPUT=none`, run `DIRECTOR start --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_DIRECTOR> --keep-open` (leave `LAST_JOB` as it is). After `RESULT: RESUMED`, resend `LAST_JOB` with `LAST_JOB_INPUT`, if it had no `RESULT`. Any relay the lost Flight Director had open is dead: the new Flight Director asks again if it still needs the answer, and a user's answer to it anyway goes as a `user-message` job.

## Resume a run

Use this when the user asks to continue a run or after a context compaction in the same session (first re-read `<SKILL_DIR>/skills/mission/SKILL.md` — or `<SKILL_DIR>/SKILL.md` in a flat install — and this file in full).

1. Find `<RUN_DIR>/state.json` (in single-repo mode, in the worktree on `refs/heads/<TOPIC>-docs`; in multi-repo mode, via `python3 -I <SKILL_DIR>/scripts/workspace.py list-runs --workspace <WORKSPACE_DIR>`) and run `STATE show`.
   - **No state file, or no `ROLE_DIRECTOR` in it:** say exactly "ERROR OLD_FORMAT_RUN remove <RUN_DIR> and start a new run" and stop.
   - **No `RUN_NOTES` in it:** `STATE set RUN_NOTES=none`.
   - **No `BASE_BRANCH` in it:** `STATE set BASE_BRANCH=<DEFAULT_BRANCH>`.
   - **No `REPOS` in it:** `STATE set REPOS=none`.
2. **Check and re-probe the picks.** The run keeps its `ROLE_*` picks; never change one.
   1. Run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <CATALOG>`, then `roles.py check` as in Step 1 item 7, with `--catalog <CATALOG>`, `--mode <RUN_MODE>` (default `full` when `RUN_MODE` is unset), and the three `ROLE_*` values. Stop on exit 1.
   2. Run `PROBE --set resume --if-changed`. (Skips re-probing when harness versions in `<CATALOG>` are unchanged and every pair in the resume set already has a recorded `LAUNCH_STYLE_*`; re-probes automatically if harness versions changed or any pair is new.) On `PROBE_FAILED`, do as in Step 1 item 8.
3. Pick up at the first of these that holds:
   - **`OPEN_USAGE_PROMPT` is `yes`:** re-show the Flight Director usage-limit prompt ("Reply \"retry\" once the limit has reset, or \"stop\" to stop the run.") and wait. On "retry", unset `OPEN_USAGE_PROMPT` and re-run `DIRECTOR <LAST_DIRECTOR_OP>` with `LAST_DIRECTOR_ARG` (using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`, and adding `--keep-open` if `PHASE=design` and `LAST_DIRECTOR_OP=start`), then handle its exit code and route any returned `RESULT <saved>` via the `LAST_JOB` / `STEP` routing rules below.
   - **`OPEN_RELAY` is set:** show `<OPEN_RELAY>` to the user again as in Rule 5, and wait. When the user replies, write their answer to `<RELAY_DIR>/<NNN>-from-user.md`, unset `OPEN_RELAY LAST_DIRECTOR_RESULT`, and run `DIRECTOR reply` using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`; once `DIRECTOR reply` returns `0` (`RESULT <saved>`), route it by `LAST_JOB` and `STEP`:
     - `LAST_JOB=user-message` (expecting `RESULT: NOTED`): continue at `STEP` passing `--docs-moved` on the next `DESIGN`, `TASKLOOP`, `FINISH`, or `PROBE` call;
     - `2 design`: branch per Step 2;
     - `3 setup`: continue Step 3 from the failing item (`baseline-failed` -> item 5; `fix-meta` -> item 7);
     - `run_design ...`: pass `--result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `DESIGN` and branch per Step 2;
     - `run_task ...`: pass `--director-result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `TASKLOOP` and branch per Step 4;
     - `run_finish ...`: pass `--director-result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `FINISH` and branch per Step 8;
     - `8 <n>`: continue Step 8 at item `<n>` with `<saved>`.
   - **`LAST_DIRECTOR_RESULT` is set** (an interrupted `run_director.py` call already finished and saved its response):
     - **`RESULT <saved>`** (and `<saved>` exists): `STATE set JOB_RETRIES=0`, `STATE unset LAST_DIRECTOR_RESULT`, and route `<saved>` directly via the `LAST_JOB` / `STEP` rules above without spawning a fallback Flight Director.
     - **`NOTIFY <notify_path> <saved>`** (and `<notify_path>` exists): `STATE unset LAST_DIRECTOR_RESULT`, show `<notify_path>` verbatim, and run `DIRECTOR notify-ack` using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`.
   - **`STEP` is `2 design`:** run "Flight Director lost" to start a fallback design Flight Director (`FIRST_JOB=resume`) and resend `LAST_JOB` with `LAST_JOB_INPUT`.
   - **`STEP` is `3 setup`:** redo Step 3 from item 2, skipping item 3 if `IMPL_WORKTREE` is set (or `INITIAL_BASE_SHA` is set in single-repo mode), and item 5 if `BASELINE_OK` is `yes`.
   - **`STEP` is `8 <n>`:** continue Step 8 at item `<n>`.
   - **`STEP` starts with `run_design`, `run_task` or `run_finish`:** run `DESIGN`, `TASKLOOP` or `FINISH` and branch as its step says.
4. Say exactly "Resumed the <TOPIC> run at step <STEP>." and continue.

## State keys

| Keys | Meaning | Set in |
| --- | --- | --- |
| `SKILL_DIR`, `ROLES_FILE`, `SETUP_CATALOG`, `DATE`, `TOPIC`, `REQUIREMENTS_SOURCE`, `JIRA_KEY`, `REQUIREMENTS_FILE`, `RUN_NOTES`, `SAFETY_REQUESTED`, `RUN_MODE`, plus optional harness binary overrides (e.g., `AGY`) | From Setup and the request; harness binary override keys are optional; `run_design.py` sets `SAFETY_REQUESTED=yes` when the user chooses Range Safety review later; `launch-plan` sets `RUN_MODE=launch-plan` (default `full`) | 1 |
| `REPOS`, `WORKSPACE_DIR` (multi-repo), `MAIN_CHECKOUT` (single-repo), `DEFAULT_BRANCH`, `BASE_BRANCH`, `START_SHA`, `START_HOW`, and per-repo `REPO_PATH_<k>`, `DEFAULT_BRANCH_<k>`, `BASE_BRANCH_<k>`, `START_SHA_<k>`, `START_HOW_<k>` | Repo(s) | 1 |
| `DOCS_WORKTREE`, `DOCS_DIR`, `RUN_DIR`, `RELAY_DIR`, and per-repo `DOCS_WT_<k>` | The docs worktree / workspace | 1 |
| `ROLE_DIRECTOR`, `ROLE_SAFETY`, `ROLE_CREW`, `CREW_TOP`, `CATALOG` | The picks, the Crew model's highest effort, and the catalog; fixed for the run | 1 (`roles.py to-state`) |
| `LAUNCH_STYLE_<pair key>`, `PROBE_HARNESS_VERSIONS` | The launch style a test launch proved for a pair and the harness versions probed. Never set them | `probe.py` |
| `PHASE` | `design`, `execution`, `finish`, or `done` | 1, 3, 8 |
| `STEP`, `WAITING_ON`, `OPEN_RELAY`, `OPEN_USAGE_PROMPT`, `LAST_DIRECTOR_OP`, `LAST_DIRECTOR_ARG` | Where to pick up after a compaction or restart | every wait |
| `DIRECTOR_CONVERSATION_ID`, `DIRECTOR_KEEP_OPEN`, `LAST_JOB`, `LAST_JOB_INPUT`, `LAST_DIRECTOR_RESULT`, `JOB_RETRIES` | The current Flight Director conversation, its job, and its settled result | `run_director.py` |
| `RUN_VALUES`, `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS`, `CLERICAL_PAIR`, `BASELINE_SKIP`, `DRIFT_CHECK` | From `run-values.txt` | 3 |
| `INITIAL_BASE_SHA`, `LAST_MERGED`, `IMPL_WORKTREE` | Execution | 3; `run_task.py` updates `LAST_MERGED` |
| `META_FAILS`, `BASELINE_FAILS`; `BASELINE_OK`, `STEP3_PROBED` | Run counters and flags | 3, and the scripts |
| `PROBE_*`; `DESIGN_*`, `DOCS_HEAD`, `SAFETY_SKILLS`, `SAFETY_RUNS`, `LENS_RUNS`, `PLAN_REVIEW_RUNS`, `RETRY_<kind>`; `CURRENT_TASK`, `LOOP_<id>`, `IMPL_CONVERSATION_ID_<id>`, `TASK_ASK_*`, and every key naming a task id; `FIN_*`, `FINAL_FIX_ROUNDS`, `VOIDS_FINAL` | The scripts' own state (`probe.py`, `run_design.py`, `run_task.py`, `run_finish.py`; their docstrings list them). Read them for status; never set them | 1–8 |

## Rationalizations that break the run

| Thought | Reality |
| --- | --- |
| "The user is in a hurry, and the missing skill isn't needed for this story; I'll start anyway." | Rule 21. Show the `MISSING` lines exactly and stop. The scripts use every role's requirements. |
| "The remembered model is gone, but this one is basically the same, and the user said I can choose." | Setup item 2. Say which pick vanished and show that role's menu. Only the user's number picks. |
| "It launched fine yesterday; skip the test launch to make the deadline." | Rule 21. `PROBE_FAILED` stops the run. Show the file. |
| "The user wants a different Flight Director mid-run; it's one state key, and they asked." | Rule 3. Picks are fixed for a run. Offer "resume" as-is or "new". Change nothing. |
| "The user, who pays for the run, says `capcom: use a stronger model for the reviews`." | Rule 3. Give the fixed reply for Capsule Communicator messages, and change no `.meta`, key or pair. |
| "I'll pick a different reviewer model myself." | Rule 3. Reviewers are the Flight Director's choice in `.meta`, checked by `validate_meta.py` against the catalog and the picks. |
| "I'll type `--model` and `--effort` or call the worker CLI myself." | Rule 2 & 3. Use `run_director.py` and the loop scripts; they apply the launch style the test launch proved. |
| "`roles.py to-state` failed, but the picks passed at setup; I'll carry on." | Rule 21. Any failed check in the Capsule Communicator stops the run. Show its output exactly. |
| "The user said \"resume\" at Step 1; I'll ignore the old picks and keep going." | Picks are fixed for a run. Run "Continue a run" item 3 with the old run's state file. |
| "The design worker failed twice; its notes say the design is fine, so I'll tell the Flight Director that." | Rule 20. Run `run_design.py` and send the job it prints, unchanged. |
| "The design obviously answers this worker's question (`CONSULT`); I'll just write the answer." | Rule 4. Send job `consult`. The Flight Director also records the answer where reviewers will see it. |
| "The Flight Director's question is long; I'll summarize it for the user." | Rule 5. Character for character, nothing added. |
| "The user's reply is clearly meant for me, not the Flight Director." | Rule 5. Only a message starting with `capcom:` is for you. |
| "A skill says to dispatch a subagent; I'll use `invoke_subagent` or `Agent`." | Rule 1. Use the bundled scripts (headless CLI workers). |
| "My own subagent tool would do this review in 30 seconds." | Rule 1. Reviewers are headless CLI workers run by `run_task.py`. |
| "It's a one-character fix; I'll make it." | Rule 4. Fix task. |
| "I'll read the diff to confirm the worker only changed what I asked." | Rule 4. That's a review. The scripts check the tree. |
| "The feature branch moved; rebasing the impl branch is safe." | Rule 13. `merge-task.sh` refuses, and you show the user the fixed options. |
| "I remember task 02's BASE_SHA and fix count; the summary says round 1." | Rule 18. `STATE get` them. Memory and summaries are not sources. |
| "The docs worktree has a stray file; discard-changes will tidy it." | Rule 12. `run_design.py` sends `commit-docs`. Never discard the Flight Director's files. |
| "To merge, I'll check out the feature branch in the main checkout." | Rule 13. `merge-task.sh` moves it with `update-ref`. |
| "The user's deadline justifies skipping a gate." | Never. Keep the gates. The user can ask `capcom: status`; anything more they need to hear comes from the Flight Director. |
| "The user said 'just rebase it'; that's authorization." | Rule 13. At a fixed prompt, only its listed replies count. Show the prompt again. |
