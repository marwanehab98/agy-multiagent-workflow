# Coordinator: the whole run

Part of agy-multiagent-workflow. The Rules in SKILL.md hold throughout.

## How you work

- **You decide nothing yourself.** Every step says what to run and how to branch, and you branch only on a script's exit code and last line, the architect's first line (`RESULT: ...`), or a counter from the state file. Every question goes to the architect, through a job or a relay.
- **The state file holds every value** (Rule 18). `STATE` below means `python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json`.
  - Record a value the moment you learn it: `STATE set KEY=value`. Read it when a command needs it: `STATE get KEY`. Counters: `STATE incr KEY` prints the new count; a missing counter is 0.
  - **Before every turn you end while waiting on the user,** set `STEP` (where to pick up, e.g. `2 design` or `3 setup`) and `WAITING_ON=user`.
- **Script shorthands:** `DESIGN`, `TASKLOOP`, `FINISH` and `PROBE` mean `python3 -I <SKILL_DIR>/scripts/<run_design|run_task|run_finish|probe>.py --state <RUN_DIR>/state.json advance`. They run headless `agy -p` workers synchronously and print any events (`MERGED ...`) followed by their status as the last line. An `ERROR <what>` line (exit 2) from any of them: show the user the line exactly and stop.
- **You never write spec text.** Build every spec with `python3 -I <SKILL_DIR>/scripts/build_spec.py --template <SKILL_DIR>/references/templates/<name>.txt --out <RUN_DIR>/specs/<name-for-it>.txt --set NAME=value ... --file NAME=path ...`. If it prints `UNFILLED`, `MISSING_FILE` or `UNUSED`, fix your command, not the template. You fill only `architect-start.txt` (`FIRST_JOB`, `INPUT`, and every run value it names) and `architect-job.txt` (`SKILL_DIR`, `JOB`, `INPUT`); the scripts fill every other template.
- **You never retype a worker's message.** `run_architect.py` saves every architect response verbatim to `<RUN_DIR>/results/architect-{n}.md`. The loop scripts save their own workers' outputs and asks. Only the user's own replies are written by hand.
- **File numbers:** `{n}` in a path you pass to `gate.py` is filled in by the script with the next free number; use the path its output names (`log=<path>`). Elsewhere, `<n>` or `<NNN>` in a file name is the next free number in that folder. The exception is `<NNN>-from-user.md`, which takes the number of the `<NNN>-to-user.md` it answers. All paths you pass to scripts are absolute.
- **Fixed prompts:** some steps stop with a fixed prompt and list the replies they accept. While one is open, a user message that isn't one of those replies and doesn't start with `coordinator:` gets the same prompt again, followed by "Reply with exactly one of: <the accepted replies>." Don't send it to the architect.
- **What you print.** Run your commands without commentary. Your text in a turn is a relay (Rule 5; one ending in a choice block is a dialog, never printed text), the output or fixed line a step tells you to show, or any `Merged task <id> at <head>.` lines (one per `MERGED <id> <head>` the script printed).
- **Keep a run log:** append one line per event to `<RUN_DIR>/run-log.md`, `<time> <event>` for what you do yourself: the architects you start, merges you see, the PR URL. The scripts log their own workers.
- **Running the architect** (`run_architect.py`, Rule 15). `ARCH` below means `python3 -I <SKILL_DIR>/scripts/run_architect.py`. Set `LAST_JOB=<JOB>` and `LAST_JOB_INPUT=<input>` before running it, and record the event in `run-log.md`.
  - **Design phase** (`PHASE=design`): build `architect-job.txt` and run `ARCH send --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_ARCHITECT>`. (The first job uses `ARCH start --keep-open`.)
  - **After plan approval:** build `architect-start.txt` with `FIRST_JOB=<JOB>` and `INPUT=<input>` and run `ARCH start --state <RUN_DIR>/state.json --spec <spec> --pair <the job's pair>`.
  - **Handle `run_architect.py` exit codes:**

    | Exit (last line) | Do |
    | --- | --- |
    | `0` (`RESULT <saved>`) | `STATE set JOB_RETRIES=0`, `STATE unset OPEN_RELAY OPEN_USAGE_PROMPT LAST_ARCH_OP LAST_ARCH_ARG`, and branch on `<saved>`'s first line as the calling step specifies. |
    | `11` (`RELAY <relay_path> <saved>`) | `STATE set OPEN_RELAY=<relay_path> WAITING_ON=user`, `STATE unset OPEN_USAGE_PROMPT LAST_ARCH_OP LAST_ARCH_ARG`, relay `<relay_path>` verbatim (Rule 5), and end your turn. When the user replies, write their answer verbatim to `<RELAY_DIR>/<NNN>-from-user.md`, `STATE unset OPEN_RELAY`, `STATE set WAITING_ON=architect`, and run `ARCH reply --state <RUN_DIR>/state.json --file <from-user> --pair <the job's pair>`, then handle its exit code using this table. |
    | `12` (`NOTIFY <notify_path> <saved>`) | Show `<notify_path>` to the user verbatim (Rule 5), then immediately run `ARCH notify-ack --state <RUN_DIR>/state.json --pair <the job's pair>` and handle its exit code using this table. |
    | `13` (`LOST <detail>`) | In `PHASE=design`, follow "Architect lost". After plan approval, follow "A fresh architect fails". |
    | `31` (`USAGE_LIMIT <detail>`) | `STATE set OPEN_USAGE_PROMPT=yes WAITING_ON=user LAST_ARCH_OP=<start|send|reply|notify-ack> LAST_ARCH_ARG=<spec, file, or empty>`, then say exactly "The architect hit its model's usage limit: <detail>\nReply \"retry\" once the limit has reset, or \"stop\" to stop the run." This is a fixed prompt. "retry" unsets `OPEN_USAGE_PROMPT` and retries the same `ARCH` command; "stop" goes to Step 8 item 7. |
    | `1` (`FAILED <saved>`) | Follow "Architect lost" (design phase) or "A fresh architect fails" (after plan approval). |
    | `2` (`ERROR <what>`) | Show the user the line exactly and stop. |

- **Architect job models** (after plan approval):

  | Job | Pair |
  | --- | --- |
  | `consult`, `escalate`, `final-review`, `verification-failed`, `drift-done`, `baseline-failed`, `user-message` | `ROLE_ARCHITECT` |
  | `fix-meta`, `pr`, `jira-close`, `report` | `CLERICAL_PAIR` once Step 3 item 8 has printed `PROBED`; `ROLE_ARCHITECT` before that |

- **Workers by role.** You run only the architect through `run_architect.py`; the loop scripts run every other worker synchronously on its pair:

  | Worker | Pair | Started by |
  | --- | --- | --- |
  | Design architect (and fallback); fresh architects after plan approval | `ROLE_ARCHITECT`; "Architect job models" | you (`run_architect.py`) |
  | Test launch | each pair being tested | `probe.py` |
  | Builder check; EGM lens pass, design review, plan review | the pair in the `NEED builder-check` line; `ROLE_EGM` | `run_design.py` |
  | Implementer, escalation attempt, Stage 1 and Stage 2 reviewers | the task's `.meta` and the computed ladder | `run_task.py` |
  | Final reviewer; drift check | `ROLE_ARCHITECT`; `ROLE_EGM` | `run_finish.py` |

## Step 1: Intake

1. **Set the values** from Setup and the user's request:
   - `REQUIREMENTS_SOURCE`: `jira` when there's a JIRA key; `written` when there's pasted text or a file path; `jira` plus a requirements file when there are both. When there's neither, say exactly "Give me a JIRA key or the requirements (pasted text or a file path)." and wait.
   - `JIRA_KEY`, or `none`. `EGM_REQUESTED`: `yes` only if the user asked for EGM or a spec review.
   - **Run notes:** the sentences of the request that say how to run the work rather than what to build (which reviews to run or skip, how to split it, what to ask about), copied exactly. Item 9 saves them.
   - `TOPIC`: `JIRA_KEY`, or a 2–4 word kebab-case slug of the requirements. `DATE`: today, `YYYY-MM-DD`.
   - `SKILL_DIR`: this skill's folder. `ROLES_FILE` and `SETUP_CATALOG`: the picks file and setup catalog from Setup.
   - `AGY`: `agy` (or `command -v agy`).

   Keep them for item 6, where the state file is created.
2. **Find the main checkout.** `MAIN_CHECKOUT` is `git rev-parse --show-toplevel`. `DEFAULT_BRANCH` is `git -C <MAIN_CHECKOUT> symbolic-ref --short refs/remotes/origin/HEAD` without its `origin/` prefix; if that fails, `git -C <MAIN_CHECKOUT> branch --show-current`. The run never checks out a branch in the main checkout, so it doesn't need to be clean.
3. **Choose the start commit:** run `bash <SKILL_DIR>/scripts/start-point.sh <MAIN_CHECKOUT> <DEFAULT_BRANCH>`. It prints `START <sha> <how>`, which gives `START_SHA` and `START_HOW`. On exit 3, say exactly "Local `<DEFAULT_BRANCH>` and `origin/<DEFAULT_BRANCH>` have diverged. Reconcile them, then tell me \"retry\"." This is a fixed prompt; "retry" repeats this item.
4. **Check for an earlier run of this topic.** Run `git -C <MAIN_CHECKOUT> worktree list --porcelain`, and look for a worktree on `refs/heads/<TOPIC>-docs`.
   - **If one exists and its `.run/state.json` exists,** say exactly "There's already a run for <TOPIC> at <its path>. Reply \"resume\" to continue it, or \"new\" to start a separate run." This is a fixed prompt. "new" goes to the next bullet. "resume" hands off to "Resume a run" with that state file. Create nothing first.
   - **If `<TOPIC>-docs` or `<TOPIC>-impl` exists as a branch** (`git -C <MAIN_CHECKOUT> show-ref --verify refs/heads/<name>`), or the user said "new," change `TOPIC` to `<TOPIC>-<k>`, the smallest `<k>` from 2 up for which neither branch exists.
5. **Create the docs worktree:**
   1. Run `bash <SKILL_DIR>/scripts/worktree.sh create <MAIN_CHECKOUT> <TOPIC>-docs <TOPIC>-docs <START_SHA>`. Record its printed path as `DOCS_WORKTREE`. Then `DOCS_DIR` is `<DOCS_WORKTREE>/docs/plans`, `RUN_DIR` is `<DOCS_WORKTREE>/.run`, and `RELAY_DIR` is `<RUN_DIR>/relay`.
   2. `bash <SKILL_DIR>/scripts/check-tree.sh <DOCS_WORKTREE> <START_SHA>` must exit 0. On exit 1, run `bash <SKILL_DIR>/scripts/discard-changes.sh <DOCS_WORKTREE> <START_SHA>`; the branch is new, so nothing is lost.
   3. Append `.run/` to `$(git -C <MAIN_CHECKOUT> rev-parse --path-format=absolute --git-common-dir)/info/exclude`, plus `.worktrees/`, and these build-output and cache patterns, each on its own line, so running tests never leaves files a worker could commit: `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `node_modules/`, `.DS_Store`. Skip any line the file already has. Run `mkdir -p <DOCS_DIR> <RELAY_DIR> <RUN_DIR>/specs <RUN_DIR>/verdicts <RUN_DIR>/asks <RUN_DIR>/gates <RUN_DIR>/results`.
6. **Create the state file:** `STATE set` every value from items 1–5, plus `PHASE=design`.
7. **Copy the picks:** run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <RUN_DIR>/catalog.json`; then `roles.py check --catalog <RUN_DIR>/catalog.json --jira <yes|no> --architect <pair> --egm <pair> --base <pair>`; then `python3 -I <SKILL_DIR>/scripts/roles.py to-state --roles <ROLES_FILE> --state <RUN_DIR>/state.json --catalog <RUN_DIR>/catalog.json`, which sets `ROLE_ARCHITECT`, `ROLE_EGM`, `ROLE_BASE`, `BASE_TOP` and `CATALOG`. If any of the three exits other than 0, show the user its output exactly and stop (Rule 21).
8. **Test-launch the picks:** run `PROBE --set step1` and branch on its last line:

   | Last line (exit) | Do |
   | --- | --- |
   | `PROBED` (0) | Go on to item 9. |
   | `PROBE_FAILED <pair> <file>` (40) | Show the user the file's contents exactly, then say exactly "The test launch of <pair> failed, so the run stops here." Start nothing (Rule 21). |

9. **Save written requirements** to `<RUN_DIR>/requirements.md` and record it as `REQUIREMENTS_FILE`: for a file path, `cp` it; for pasted text, write it exactly as the user typed it, with only the requirements, not instructions about the run. Otherwise `REQUIREMENTS_FILE` is `none`. Save the run notes from item 1 the same way to `<RUN_DIR>/run-notes.md` and record it as `RUN_NOTES`, or `RUN_NOTES=none` when there are none. Never write either in `<DOCS_DIR>`: a test launch's tree check deletes anything uncommitted there. The architect commits its own copy.
10. **Start the architect:** build `architect-start.txt` with `FIRST_JOB=start`, `INPUT=none`, and every run value it names. Set `STATE set LAST_JOB=start LAST_JOB_INPUT=none STEP="2 design" WAITING_ON=architect`, and run `ARCH start --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_ARCHITECT> --keep-open`. Handle its exit code per "Running the architect" (if it immediately returns `RELAY`, show the relay and wait; if `RESULT`, go to Step 2).

## Step 2: Design phase

Handle `run_architect.py`'s `RESULT` file (`<saved>`) by branching on its first line:

- **`RESULT: NEED ...` or `RESULT: COMMITTED`:** run `DESIGN --result <saved>` and branch on `DESIGN`'s last line:

  | Last line (exit) | Do |
  | --- | --- |
  | `ARCHITECT_JOB <job> <input>` (20) | Send the architect job `<job>` with `INPUT=<input>` (everything after the job name), unchanged, via `ARCH send`. Its next `RESULT` goes through the list above. |
  | `CONSULT <ask_file>` (22) | Send architect job `consult` with `INPUT=<ask_file>` via `ARCH send`. On `RESULT: ANSWER <answer_file>`, run `DESIGN --answer <answer_file>` and branch again. |

- **`RESULT: PLAN_APPROVED <run-values>`:** go to Step 3.
- **Any other `RESULT:` line** answers the job that asked for it ("Messages", "Architect lost").

`run_design.py` checks the docs worktree, test-launches the builder-check pair, runs each worker synchronously via `agy -p`, retries a failed one once, counts runs, and writes the `-done` job's input, including `failed: <file>` after a second failure. You never do any of that in this step, and never read a design worker's output.

## Step 3: Set up execution

1. **Close the design architect conversation:** `STATE unset ARCHITECT_CONVERSATION_ID ARCHITECT_KEEP_OPEN` and `STATE set PHASE=execution STEP="3 setup"`. From now on every architect job starts a fresh `agy -p` run.
2. **Read `<run-values>`** and `STATE set` each of `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS` and `CLERICAL_PAIR`, plus `RUN_VALUES=<run-values path>`, `BASELINE_SKIP` (empty when the file has none) and `DRIFT_CHECK` (`no` when the file has none). The `VERIFY_<n>` and `GATE_<n>` commands stay in that file; `gate.py` reads them from there.
3. **Create the feature branch** without checking it out: run `git -C <MAIN_CHECKOUT> branch <FEATURE_BRANCH> <START_SHA>`. If that fails, show the user its output and stop. `STATE set INITIAL_BASE_SHA=<START_SHA> LAST_MERGED=<START_SHA>`.
4. **Create the implementation worktree:** run `bash <SKILL_DIR>/scripts/worktree.sh create <MAIN_CHECKOUT> <TOPIC>-impl <TOPIC>-impl <FEATURE_BRANCH>` and record its path as `IMPL_WORKTREE`. `bash <SKILL_DIR>/scripts/check-tree.sh <IMPL_WORKTREE> <INITIAL_BASE_SHA>` must exit 0; otherwise show its output to the user and stop.
5. **Check the base passes the checks.** Run `python3 -I <SKILL_DIR>/scripts/gate.py --run-values <RUN_VALUES> --worktree <IMPL_WORKTREE> --head <INITIAL_BASE_SHA> --log "<RUN_DIR>/gates/baseline-{n}.log" --prefix VERIFY --skip "<BASELINE_SKIP>"`. If the file has `GATE_` lines (`grep -q '^GATE_' <RUN_VALUES>`), run it again without `--prefix`, keeping `--skip`.
   - **Both exit 0:** `STATE set BASELINE_OK=yes` and go on. **Exit 2 or 64:** show the user its output and stop.
   - **Exit 1:** `STATE incr BASELINE_FAILS`. At 3, show the user the last log's path and stop. Otherwise send job `baseline-failed` with `INPUT=<that log>`. `RESULT: RUN_VALUES_FIXED` repeats this item; `RESULT: STOP` goes to Step 8 item 7.
6. **Write the queue:** put the `TASKS` ids, space-separated, in `<RUN_DIR>/queue.txt`. It is the only list of tasks you follow; `REPLAN` and `FINAL_FIX` change it.
7. **Validate the plan:** run `python3 -I <SKILL_DIR>/scripts/validate_meta.py tasks --state <RUN_DIR>/state.json --tasks-dir <DOCS_DIR>/tasks --ids "<the queue>" --design <DESIGN_FILE> > <RUN_DIR>/validate-<n>.txt`, then `python3 -I <SKILL_DIR>/scripts/validate_meta.py harness-pair --state <RUN_DIR>/state.json --pair <CLERICAL_PAIR> >> <the same file>`.
   - **Both exit 0:** `STATE set META_FAILS=0` and go on.
   - **Otherwise:** `STATE incr META_FAILS`. At 3, show the user the last output and stop. Otherwise send job `fix-meta` with `INPUT=<that file>`. On `RESULT: META_FIXED`, read `CLERICAL_PAIR` from `<RUN_VALUES>` again, `STATE set` it, and repeat this item.
8. **Test-launch the plan's pairs:** run `PROBE --set step3`.
   - **`PROBED` (0):** `STATE set STEP3_PROBED=yes` and go on to item 9.
   - **`FIX_META <file>` (21):** a reviewer pair or `CLERICAL_PAIR` the architect chose failed its test launch. Handle it as a failed validation in item 7: `STATE set STEP="3 setup"`, `STATE incr META_FAILS` (at 3, show the user the file and stop), send job `fix-meta` with `INPUT=<file>`, and on `RESULT: META_FIXED` re-read `CLERICAL_PAIR`, then repeat item 7 and this item.
9. **Tell the user:** "Plan approved: <number of tasks> tasks. Implementation is starting." Then go to Step 4.

## Step 4: Run the task loop

`run_task.py` runs every task in `<RUN_DIR>/queue.txt` synchronously: it implements, checks the tree, runs the gate, runs both reviews and the fix rounds, merges, and prepares escalations. Its rules (fix-round caps, void reviewers, usage limits, ladder checks) are in its module docstring.

1. **Run `TASKLOOP`.** Print one `Merged task <id> at <head>.` line for each `MERGED <id> <head>` it printed (and log it in `run-log.md`), and branch on its **last line**:

   | Last line (exit) | Do |
   | --- | --- |
   | `QUEUE_EMPTY` (0) | If `PHASE` is `execution`, go to Step 8. If it is `finish`, run `FINISH` and branch as in Step 8 items 1–3. |
   | `ESCALATE <input>` (20) | Send job `escalate` with `INPUT=<input>` (everything after `ESCALATE `). When the architect returns `RESULT`, run `TASKLOOP --architect-result <saved>` and branch again. |
   | `FIX_META <file>` (21) | Send job `fix-meta` with `INPUT=<file>`. When the architect returns `RESULT`, run `TASKLOOP --architect-result <saved>` and branch again. |
   | `CONSULT <ask_file>` (22) | Send job `consult` with `INPUT=<ask_file>`. When the architect returns `RESULT: ANSWER <answer_file>`, run `TASKLOOP --answer <answer_file>` and branch again. |
   | `PROMPT <file> <reply>...` (30) | A fixed prompt. Show the user `<file>`'s contents exactly, and end your turn (`STATE set WAITING_ON=user`). A reply that is one of the listed words: run `TASKLOOP --reply <word>` and branch again. Any other reply that doesn't start with `coordinator:`: show the file again, followed by "Reply with exactly one of: <the words>." |
   | `STOP <reason>` (40) | Dispatch nothing more and go to Step 8 item 7. |

You don't read verdicts, count rounds, pick pairs, reset worktrees, or merge in this step; the script does all of it.

## Step 8: Finish

**Items 1–3: final review and verification.** Run `FINISH` and branch on its last line. At each of items 4–7, first `STATE set STEP="8 <item number>"`.

| Last line (exit) | Do |
| --- | --- |
| `VERIFIED <log>` (0) | Go to item 4, with `<log>` as the passing verify log. |
| `ARCHITECT_JOB <job> <input>` (20) | Send job `<job>` with `INPUT=<input>`. When the architect returns `RESULT`, run `FINISH --architect-result <saved>` and branch again. |
| `RUN_QUEUE <F id>` (21) | `queue.txt` now holds that task: go to Step 4. Its `QUEUE_EMPTY` brings you back here. |
| `CONSULT <ask_file>` (22) | Send job `consult` with `INPUT=<ask_file>`. When the architect returns `RESULT: ANSWER <answer_file>`, run `FINISH --answer <answer_file>` and branch again. |
| `PROMPT <file> <reply>...` (30) | A fixed prompt, handled as in Step 4, with `FINISH --reply <word>`. |
| `STOP <reason>` (40) | Dispatch nothing more and go to item 7. |

4. **Open the PR:**
   1. Send job `pr` with `INPUT=<the passing verify log>`. On `RESULT: PR_READY <title> <body>`, invoke `finishing-a-development-branch` for `<FEATURE_BRANCH>` with target `<PR_TARGET>`, title `$(cat <title>)`, and body file `<body>`. Tell it the branch is not checked out anywhere (it pushes with `git -C <MAIN_CHECKOUT> push -u origin <FEATURE_BRANCH>`), that it has already been verified (the log above), and to skip its own worktree cleanup. For `jira`, tell it to push and create the PR immediately, without presenting options; for `written`, let it offer its usual options and carry out the user's choice, with one change: a local merge never goes through that skill or a hand-typed git command. Run `bash <SKILL_DIR>/scripts/merge-local.sh <MAIN_CHECKOUT> <PR_TARGET> <FEATURE_BRANCH>` and show its output exactly. On exit 2 or 3 nothing changed: show its output, then offer the options again.
   2. Log the PR URL.
5. **Update JIRA** (`jira` with a PR only): send job `jira-close` with `INPUT=<PR URL>`, and log the `RESULT: JIRA_DONE` line.
6. **Clean up:** once a PR is open or the branch is merged, run `bash <SKILL_DIR>/scripts/worktree.sh remove <MAIN_CHECKOUT> <IMPL_WORKTREE> --delete-branch --merged-into <FEATURE_BRANCH>`, never with `--force`. If it fails, show the user the output. If the user chose keep or discard, leave the worktree in place.
7. **Report:** send job `report` with `INPUT=<RUN_DIR>/run-log.md`. On `RESULT: REPORT <path>`, relay `<path>` to the user as in Rule 5, then add exactly: "Run documents: `<DOCS_DIR>`. Remove the docs worktree with `bash <SKILL_DIR>/scripts/worktree.sh remove <MAIN_CHECKOUT> <DOCS_WORKTREE>` when you're done with them." Then `STATE set PHASE=done WAITING_ON=`.

## Messages (any step)

- **A user message:**
  - **Starting with `coordinator:`:** it is for you. "status" means show the last 10 run-log lines. "use the fallback" means "Architect lost" below. "stop" goes to Step 8 item 7. Anything else, including a request to change a pick, a model or a `.meta`: say exactly "I only understand: status, use the fallback, stop." and change nothing.
  - **Otherwise, with a relay open (`OPEN_RELAY` is set):** it is the answer. Write it, exactly as typed, to `<RELAY_DIR>/<NNN>-from-user.md`, `STATE unset OPEN_RELAY`, `STATE set WAITING_ON=architect`, and run `ARCH reply --state <RUN_DIR>/state.json --file <from-user> --pair <the job's pair>` (using `LAST_JOB` and "Architect job models" for `<the job's pair>`).
  - **Otherwise, with `OPEN_USAGE_PROMPT` set to `yes`:** it answers the architect usage-limit prompt ("retry" unsets `OPEN_USAGE_PROMPT` and retries `ARCH <LAST_ARCH_OP>` with `LAST_ARCH_ARG`; "stop" goes to Step 8 item 7).
  - **Otherwise, with a fixed prompt open:** follow "Fixed prompts" in "How you work".
  - **Otherwise:** write it to `<RELAY_DIR>/<NNN>-from-user.md` and send job `user-message` with `INPUT=<that path>`. When it finishes with `RESULT: NOTED`, pass `--docs-moved` on the next call to `DESIGN`, `TASKLOOP`, `FINISH`, or `PROBE`.
- **A fresh architect fails** (after plan approval): `run_architect.py` exits `1`, `13`, or returns an unexpected line. `STATE incr JOB_RETRIES`. At 1, start the same job again from `LAST_JOB` and `LAST_JOB_INPUT`. At 2, say exactly "The architect failed twice on job <LAST_JOB>. Reply \"retry\" to try again, or \"stop\" to stop the run." This is a fixed prompt: "retry" resets `JOB_RETRIES` to 0 and starts the job again; "stop" goes to Step 8 item 7. Reset `JOB_RETRIES` to 0 after every job that settles with a `RESULT:` line.
- **Architect lost** (design phase only): `run_architect.py` exits `1` or `13`, or the user says "use the fallback".
  Build `architect-start.txt` with `FIRST_JOB=resume` and `INPUT=none`, run `ARCH start --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_ARCHITECT> --keep-open` (leave `LAST_JOB` as it is). After `RESULT: RESUMED`, resend `LAST_JOB` with `LAST_JOB_INPUT`, if it had no `RESULT`. Any relay the lost architect had open is dead: the new architect asks again if it still needs the answer, and a user's answer to it anyway goes as a `user-message` job.

## Resume a run

Use this when the user asks to continue a run or after a context compaction in the same session (first re-read `<SKILL_DIR>/SKILL.md` and this file in full).

1. Find `<RUN_DIR>/state.json` (for a topic, in the worktree on `refs/heads/<TOPIC>-docs`) and run `STATE show`.
   - **No state file, or no `ROLE_ARCHITECT` in it:** say exactly "ERROR OLD_FORMAT_RUN remove <RUN_DIR> and start a new run" and stop.
   - **No `RUN_NOTES` in it:** `STATE set RUN_NOTES=none`.
2. **Check and re-probe the picks.** The run keeps its `ROLE_*` picks; never change one.
   1. Run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <CATALOG>`, then `roles.py check` as in Step 1 item 7, with `--catalog <CATALOG>` and the three `ROLE_*` values. Stop on exit 1.
   2. Run `PROBE --set resume`. On `PROBE_FAILED`, do as in Step 1 item 8.
3. Pick up at the first of these that holds:
   - **`OPEN_USAGE_PROMPT` is `yes`:** re-show the architect usage-limit prompt ("Reply \"retry\" once the limit has reset, or \"stop\" to stop the run.") and wait. On "retry", unset `OPEN_USAGE_PROMPT` and re-run `ARCH <LAST_ARCH_OP>` with `LAST_ARCH_ARG` (using `LAST_JOB` and "Architect job models" for `<the job's pair>`, and adding `--keep-open` if `PHASE=design` and `LAST_ARCH_OP=start`), then continue per `STEP`.
   - **`OPEN_RELAY` is set:** show `<OPEN_RELAY>` to the user again as in Rule 5, and wait. When the user replies, write their answer to `<RELAY_DIR>/<NNN>-from-user.md`, unset `OPEN_RELAY`, and run `ARCH reply` using `LAST_JOB` and "Architect job models" for `<the job's pair>`; once `ARCH reply` returns `0` (`RESULT <saved>`), if `LAST_JOB=user-message` (expecting `RESULT: NOTED`), continue at `STEP` passing `--docs-moved` on the next `DESIGN`, `TASKLOOP`, `FINISH`, or `PROBE` call; otherwise route `<saved>` according to `STEP`:
     - `2 design`: branch per Step 2;
     - `3 setup`: continue Step 3 from the failing item (`baseline-failed` -> item 5; `fix-meta` -> item 7);
     - `run_design ...`: pass `--result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `DESIGN` and branch per Step 2;
     - `run_task ...`: pass `--architect-result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `TASKLOOP` and branch per Step 4;
     - `run_finish ...`: pass `--architect-result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `FINISH` and branch per Step 8;
     - `8 <n>`: continue Step 8 at item `<n>` with `<saved>`.
   - **`STEP` is `2 design`:** run "Architect lost" to start a fallback design architect (`FIRST_JOB=resume`) and resend `LAST_JOB` with `LAST_JOB_INPUT`.
   - **`STEP` is `3 setup`:** redo Step 3 from item 2, skipping item 3 if `INITIAL_BASE_SHA` is set, item 4 if `IMPL_WORKTREE` is set, and item 5 if `BASELINE_OK` is `yes`.
   - **`STEP` is `8 <n>`:** continue Step 8 at item `<n>`.
   - **`STEP` starts with `run_design`, `run_task` or `run_finish`:** run `DESIGN`, `TASKLOOP` or `FINISH` and branch as its step says.
4. Say exactly "Resumed the <TOPIC> run at step <STEP>." and continue.

## State keys

| Keys | Meaning | Set in |
| --- | --- | --- |
| `SKILL_DIR`, `ROLES_FILE`, `SETUP_CATALOG`, `AGY`, `DATE`, `TOPIC`, `REQUIREMENTS_SOURCE`, `JIRA_KEY`, `REQUIREMENTS_FILE`, `RUN_NOTES`, `EGM_REQUESTED` | From Setup and the request; `run_design.py` sets `EGM_REQUESTED=yes` when the user chooses EGM later | 1 |
| `MAIN_CHECKOUT`, `DEFAULT_BRANCH`, `START_SHA`, `START_HOW` | Repo | 1 |
| `DOCS_WORKTREE`, `DOCS_DIR`, `RUN_DIR`, `RELAY_DIR` | The docs worktree | 1 |
| `ROLE_ARCHITECT`, `ROLE_EGM`, `ROLE_BASE`, `BASE_TOP`, `CATALOG` | The picks, the base model's highest effort, and the catalog; fixed for the run | 1 (`roles.py to-state`) |
| `LAUNCH_STYLE_<pair key>` | The launch style a test launch proved for a pair. Never set them | `probe.py` |
| `PHASE` | `design`, `execution`, `finish`, or `done` | 1, 3, 8 |
| `STEP`, `WAITING_ON`, `OPEN_RELAY`, `OPEN_USAGE_PROMPT`, `LAST_ARCH_OP`, `LAST_ARCH_ARG` | Where to pick up after a compaction or restart | every wait |
| `ARCHITECT_CONVERSATION_ID`, `ARCHITECT_KEEP_OPEN`, `LAST_JOB`, `LAST_JOB_INPUT`, `JOB_RETRIES` | The current architect conversation and its job | `run_architect.py` |
| `RUN_VALUES`, `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS`, `CLERICAL_PAIR`, `BASELINE_SKIP`, `DRIFT_CHECK` | From `run-values.txt` | 3 |
| `INITIAL_BASE_SHA`, `LAST_MERGED`, `IMPL_WORKTREE` | Execution | 3; `run_task.py` updates `LAST_MERGED` |
| `META_FAILS`, `BASELINE_FAILS`; `BASELINE_OK`, `STEP3_PROBED` | Run counters and flags | 3, and the scripts |
| `PROBE_*`; `DESIGN_*`, `DOCS_HEAD`, `EGM_SKILLS`, `EGM_RUNS`, `LENS_RUNS`, `PLAN_REVIEW_RUNS`, `RETRY_<kind>`; `CURRENT_TASK`, `LOOP_<id>`, `IMPL_CONVERSATION_ID_<id>`, `TASK_ASK_*`, and every key naming a task id; `FIN_*`, `FINAL_FIX_ROUNDS`, `VOIDS_FINAL` | The scripts' own state (`probe.py`, `run_design.py`, `run_task.py`, `run_finish.py`; their docstrings list them). Read them for status; never set them | 1–8 |
