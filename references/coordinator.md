# Coordinator: the whole run

Part of orca-multiagent-workflow. The Rules in SKILL.md hold throughout.

## How you work

- **You decide nothing yourself.** Every step says what to run and how to branch, and you branch only on one of these:
  - a script's exit code;
  - a review's verdict (`VERDICT: PASS` or `VERDICT: FIX`), read from its review file as Step 8 item 2 describes (in Step 4, `run_task.py` reads it);
  - the architect's first line (`RESULT: ...`);
  - a worker settling `succeeded` or `failed`;
  - a counter from the state file.

  Every question goes to the architect, through a job or a relay.
- **The state file holds every value** (Rule 18). `STATE` below means `python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json`.
  - Record a value the moment you learn it: `STATE set KEY=value`.
  - Read it when a command needs it: `STATE get KEY`. A value you only remember, or that a context summary mentions, is not a source.
  - Counters: `STATE incr KEY` prints the new count; a missing counter is 0.
  - **Before every turn you end while waiting,** set `STEP` (where to pick up, e.g. `2 plan-review` or `8 final-review`) and `WAITING_ON` (the dispatch id you wait on, or `user`). In Step 4 the task loop sets both itself; you set only `WAITING_ON=<ARCHITECT_DISPATCH>` when you start an architect job for it.
  - The keys are listed in "State keys" at the end of this file.
- **You never write spec text.** Build every spec with `python3 -I <SKILL_DIR>/scripts/build_spec.py --template <SKILL_DIR>/references/templates/<name>.txt --out <RUN_DIR>/specs/<name-for-it>.txt --set NAME=value ... --file NAME=path ...`. Start the worker from the file it writes.
  - If the script prints `UNFILLED`, `MISSING_FILE` or `UNUSED`, fix your command, not the template.
  - Templates and their names are listed near the end of this file.
- **You never retype a worker's message.** Save it with `python3 -I <SKILL_DIR>/scripts/save_message.py --orca <ORCA> --run <RUN_ID> --out <file>` plus `--dispatch <id> --type worker_done` (a settle), or `--id <message id>` (an ask). `--header <line>` puts a line first, and `--append` adds to a file. It prints `outcome=` for a settle. `NOT_FOUND` means the message isn't in the inbox yet: run `ORCA orchestration check --peek --wait --timeout-ms 60000`, then save again.
- **File numbers:** `<n>` or `<NNN>` in a file name is the next free number in that folder. The exception is `<NNN>-from-user.md`, which takes the number of the `<NNN>-to-user.md` it answers. All paths you pass to scripts are absolute, and a task's files are `<DOCS_DIR>/tasks/<id>.md`, `<id>.meta`, `<id>-summary.md`, and `<id>-decisions.md`.
- **Fixed prompts:** some steps stop with a fixed prompt and list the replies they accept. While one is open, a user message that isn't one of those replies and doesn't start with `coordinator:` gets the same prompt again, followed by "Reply with exactly one of: <the accepted replies>." Don't send it to the architect.
- **Record every worker's dispatch id** from its `worker-start` receipt in the state file; you need it to save its messages and release it.
- **You keep a run log.** Append one line per event to `<RUN_DIR>/run-log.md`: `<time> <event>`. Events are dispatches, settles, verdict and gate file paths, merges, escalations, the PR URL, and verification log paths.
- **Architect jobs** (Rule 15). Build `architect-job.txt` with `SKILL_DIR`, `JOB` and `INPUT`, or `architect-start.txt` for a fresh architect.
  - **Design phase** (`PHASE=design`): run `ORCA orchestration send --to dispatch:<ARCHITECT_DISPATCH> --subject "Architect job: <JOB>" --body "$(cat <spec file>)"`. Record the new dispatch id from the receipt as `ARCHITECT_DISPATCH`.
  - **After plan approval:** start a fresh architect for the job. Build `architect-start.txt` with `FIRST_JOB=<JOB>`, `INPUT=<input>` and the run values. Run `ORCA orchestration worker-start --spec-file <spec> --task-title "Architect job: <JOB>" --worktree path:<DOCS_WORKTREE> --agent claude --model <model> --json`, with the model from "Architect job models", and record its dispatch id as `ARCHITECT_DISPATCH`. Once you've handled its `RESULT:` line, release it.
  - Either way, set `LAST_JOB=<JOB>` and `LAST_JOB_INPUT=<input>` before you end your turn. The architect settles with a `RESULT:` line; branch on it.
- **Workers by role**, with the model always set with `--model` (Rule 3):

  | Worker | `--agent` and `--model` |
  | --- | --- |
  | Design architect, and fallback design architect | `claude`, `opus`, with `--keep-open` |
  | Fresh architect (after plan approval) | `claude` and the job's model from "Architect job models" |
  | Builder check | `claude`, `sonnet` |
  | EGM lens pass, design review, plan review | `antigravity`, `<GEMINI_MODEL>-high` |
  | Implementer (started by `run_task.py`) | the task's `.meta` `IMPLEMENTER`, with `--keep-open` |
  | Escalation attempt (started by `run_task.py`) | the `<agent:model>` in the architect's `RESULT: ESCALATE`, with `--keep-open` |
  | Stage 1 / Stage 2 reviewer (started by `run_task.py`) | the task's `.meta` `REVIEW_1` / `REVIEW_2` |
  | Final reviewer | `claude`, `opus` |

  A pair written `<agent>:<model>` means `--agent <agent> --model <model>`: split it at the first `:`. You never choose a pair yourself, and you start a task's workers only after `validate_meta.py` has passed its `.meta` (Step 3).
- **Architect job models** (after plan approval):

  | Job | Model |
  | --- | --- |
  | `consult`, `escalate`, `final-review`, `verification-failed`, `baseline-failed`, `user-message` | `opus` |
  | `fix-meta`, `pr`, `jira-close`, `report` | `sonnet` |

## Step 1: Intake

1. **Set the values** from the user's request:
   - `REQUIREMENTS_SOURCE`:
     - `jira` when there's a JIRA key;
     - `written` when there's pasted text or a file path;
     - `jira` plus a requirements file when there are both;
     - when there's neither, say exactly "Give me a JIRA key or the requirements (pasted text or a file path)." and wait.
   - `JIRA_KEY`, or `none`.
   - `EGM_REQUESTED`: `yes` only if the user asked for EGM or a spec review.
   - `GEMINI_MODEL` (default `gemini-4-hydrogen`) and `GEMINI_EFFORT` (`high` or `xhigh`, default `high`).
   - `TOPIC`: `JIRA_KEY`, or a 2–4 word kebab-case slug of the requirements.
   - `DATE`: today, `YYYY-MM-DD`.
   - `SKILL_DIR`: the base directory shown when this skill loaded.

   Keep them for item 8, where the state file is created.
2. **Check `agy`:** run `agy --version` and `agy models`. If `<GEMINI_MODEL>` isn't listed, say "`<GEMINI_MODEL>` isn't available in agy. Which model should implementers use? (suggested: gemini-3.8)" and wait.
3. **Load Orca:** load the `orchestration` skill, resolve `ORCA` per its "Resolve the CLI for this session" section, run `ORCA skills get orchestration`, and load the `orca-cli` skill. If an Orca error isn't fixed by the guide's recovery steps, show it to the user verbatim and stop.
4. **Find the EGM skills:** run `find "$HOME/.gemini" -maxdepth 7 -path '*/egm/skills/egm-review/SKILL.md' -print -quit`. `EGM_SKILLS` is that path without `/egm-review/SKILL.md`. If it prints nothing, say "I can't find the EGM plugin's skills under ~/.gemini. Install EGM in agy, or tell me the path of its skills folder." and wait.
5. **Find the main checkout.**
   - `MAIN_CHECKOUT` is `git rev-parse --show-toplevel`.
   - `DEFAULT_BRANCH` is `git -C <MAIN_CHECKOUT> symbolic-ref --short refs/remotes/origin/HEAD` without its `origin/` prefix. If that fails, use `git -C <MAIN_CHECKOUT> branch --show-current`.
   - The run never checks out a branch in the main checkout, so it doesn't need to be clean.
6. **Choose the start commit:** run `bash <SKILL_DIR>/scripts/start-point.sh <MAIN_CHECKOUT> <DEFAULT_BRANCH>`. It prints `START <sha> <how>`, which gives `START_SHA` and `START_HOW`.
   - **Exit 3:** say exactly "Local `<DEFAULT_BRANCH>` and `origin/<DEFAULT_BRANCH>` have diverged. Reconcile them, then tell me \"retry\"." This is a fixed prompt; "retry" repeats this item.
7. **Check for an earlier run of this topic.** Run `git -C <MAIN_CHECKOUT> worktree list --porcelain`, and look for a worktree on `refs/heads/<TOPIC>-docs`.
   - **If one exists and its `.run/state.json` exists,** say exactly "There's already a run for <TOPIC> at <its path>. Reply \"resume\" to continue it, or \"new\" to start a separate run." This is a fixed prompt. "resume" goes to "Resume a run" with that state file, and "new" goes to the next bullet.
   - **If `<TOPIC>-docs` or `<TOPIC>-impl` exists as a branch** (`git -C <MAIN_CHECKOUT> show-ref --verify refs/heads/<name>`), or the user said "new," change `TOPIC` to `<TOPIC>-<k>`. `<k>` is the smallest number from 2 up for which neither branch exists.
8. **Create the docs worktree:**
   1. Run `ORCA worktree create --name <TOPIC>-docs --branch <TOPIC>-docs --base-branch <START_SHA>`. If Orca refuses a commit as the base, run it again with `--base-branch <DEFAULT_BRANCH>`. Record its path as `DOCS_WORKTREE`. Then set `DOCS_DIR` to `<DOCS_WORKTREE>/docs/plans`, `RUN_DIR` to `<DOCS_WORKTREE>/.run`, and `RELAY_DIR` to `<RUN_DIR>/relay`.
   2. `check-tree.sh <DOCS_WORKTREE> <START_SHA>` must exit 0. On exit 1, run `discard-changes.sh <DOCS_WORKTREE> <START_SHA>`; the branch is new, so nothing is lost.
   3. Append `.run/` to `$(git -C <MAIN_CHECKOUT> rev-parse --git-common-dir)/info/exclude`. If `<DOCS_WORKTREE>` is inside `<MAIN_CHECKOUT>`, append its parent folder too (e.g. `.worktrees/`).
   4. Run `mkdir -p <DOCS_DIR> <RELAY_DIR> <RUN_DIR>/specs <RUN_DIR>/verdicts <RUN_DIR>/asks <RUN_DIR>/gates <RUN_DIR>/results`.
   5. **Create the state file:** `STATE set` every value from items 1–8, plus `ORCA=<the resolved executable>` and `PHASE=design`.
9. **Save written requirements** to `<DOCS_DIR>/<DATE>-<TOPIC>-requirements.md` and record it as `REQUIREMENTS_FILE`:
   - for a file path, `cp` it;
   - for pasted text, write it exactly as the user typed it, with only the requirements, not instructions like "use xhigh".

   Otherwise `REQUIREMENTS_FILE` is `none`.
10. **Start the architect.**
    1. Build `architect-start.txt` with `FIRST_JOB=start`, `INPUT=none`, and every run value it names.
    2. Run `ORCA orchestration worker-start --spec-file <spec> --task-title "Architect: <TOPIC>" --worktree path:<DOCS_WORKTREE> --agent claude --model opus --keep-open --json`.
    3. Record `RUN_ID` (`runId`) and `ARCHITECT_DISPATCH` (`dispatchId`).
11. **Tell the user exactly:** "An Opus architect is designing this with you. I'll pass its questions to you here unchanged; reply here and I'll pass your answers back unchanged. To talk to me instead, start your message with `coordinator:`." Then end your turn.

## Step 2: Design phase

Wait for the architect. Handle messages per "Messages" below. On its `RESULT:` line, run the matching block.

**Every read-only design worker** (builder check, EGM, lens, plan review) is started the same way:
1. **Check the docs worktree first.** `STATE set DOCS_HEAD=$(git -C <DOCS_WORKTREE> rev-parse HEAD)`, then run `check-tree.sh <DOCS_WORKTREE> <DOCS_HEAD> > <RUN_DIR>/results/docs-check-<n>.txt`. A non-zero exit means the architect left changes it didn't commit. Send job `commit-docs` with `INPUT=<that file>`, and on `RESULT: COMMITTED`, repeat this item. Never discard the architect's files.
2. Build the spec, then start it with `ORCA orchestration worker-start --spec-file <spec> --task-title "<title>: <TOPIC>" --worktree path:<DOCS_WORKTREE> --agent <agent> --model <model> --json`.
3. **On settle:**
   1. Save the settle message with `save_message.py --dispatch <id> --type worker_done --out <RUN_DIR>/results/<kind>-<n>.md`, then release the worker.
   2. Run `check-tree.sh <DOCS_WORKTREE> <DOCS_HEAD>` (with the `--allow` named in the block). A non-zero exit means run `discard-changes.sh <DOCS_WORKTREE> <DOCS_HEAD>`. The worker's output is in `.run/`, so it survives.
   3. **If it settled `failed`, or its output file is missing or empty,** `STATE incr RETRY_<kind>`. At 1, start it again from item 1 of this list; the block's run counter is not counted again. At 2, send the block's `-done` job with `INPUT="failed: <the saved settle message file>"`. Once a worker of that kind succeeds, `STATE set RETRY_<kind>=0`.

The blocks:

- **`NEED builder-check <design>`:** `<kind>` is `builder-check`. Build `builder-check.txt` with `DESIGN_FILE=<design>` and `OUTPUT_FILE=<RUN_DIR>/builder-check-<n>.md`. The agent is `claude`, model `sonnet`, title "Builder check". Then send job `builder-check-done` with `INPUT=<RUN_DIR>/builder-check-<n>.md`.
- **`NEED egm <design> <findings>`:** `<kind>` is `egm`.
  1. `STATE incr EGM_RUNS`. At 3, send job `egm-limit` with `INPUT=none` and stop here.
  2. Build `egm.txt` with `EGM_SKILLS`, `DESIGN_FILE=<design>` and `OUTPUT_FILE=<findings>`. The agent is `antigravity`, model `<GEMINI_MODEL>-high`, title "EGM review".
  3. On settle, run `cp <DOCS_WORKTREE>/<findings> <RUN_DIR>/egm-<n>.md` before the tree check, and pass `--allow <findings>` to it.
  4. Send job `egm-done` with `INPUT=<RUN_DIR>/egm-<n>.md`.
- **`NEED lens <concepts file>`:** `<kind>` is `lens`.
  1. `STATE incr LENS_RUNS`. At 2, send job `lens-done` with `INPUT=<the first run's findings file>` and stop here.
  2. Build `lens.txt` with `EGM_SKILLS`, `CONCEPTS_FILE=<concepts file>` and `OUTPUT_FILE=<RUN_DIR>/lens-<n>.md`. The agent is `antigravity`, model `<GEMINI_MODEL>-high`, title "EGM lens pass".
  3. Send job `lens-done` with `INPUT=<RUN_DIR>/lens-<n>.md`.
- **`NEED plan-review <plan file> <context file>`:** `<kind>` is `plan-review`.
  1. `STATE incr PLAN_REVIEW_RUNS`. At 3, send job `plan-review-done` with `INPUT="Plan review limit reached; finish triage with <the latest findings file>"` and stop here.
  2. Build `plan-review.txt` with `EGM_SKILLS`, `PLAN_FILE=<plan file>`, `CONTEXT=<context file>` (a `--file`), and `OUTPUT_FILE=<RUN_DIR>/plan-review-<n>.md`. The agent is `antigravity`, model `<GEMINI_MODEL>-high`, title "EGM plan review".
  3. Send job `plan-review-done` with `INPUT=<RUN_DIR>/plan-review-<n>.md`.
- **`PLAN_APPROVED <run-values>`:** go to Step 3.

## Step 3: Set up execution

1. **Release the design architect** with `worker-release --dispatch <ARCHITECT_DISPATCH>`, and `STATE set PHASE=execution`. From now on every architect job is a fresh worker (How you work, "Architect jobs").
2. **Read `<run-values>`** and `STATE set` each of `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG` and `TASKS`, plus `RUN_VALUES=<run-values path>`. The `VERIFY_<n>` and `GATE_<n>` commands stay in that file; `gate.py` reads them from there.
3. **Create the feature branch** without checking it out: run `git -C <MAIN_CHECKOUT> branch <FEATURE_BRANCH> <START_SHA>`. If that fails, show the user its output and stop. `STATE set INITIAL_BASE_SHA=<START_SHA> LAST_MERGED=<START_SHA>`.
4. **Create the implementation worktree:**
   - Run `ORCA worktree create --name <TOPIC>-impl --branch <TOPIC>-impl --base-branch <FEATURE_BRANCH>` and record its path as `IMPL_WORKTREE`.
   - `check-tree.sh <IMPL_WORKTREE> <INITIAL_BASE_SHA>` must exit 0. Otherwise show its output to the user and stop.
5. **Check the base passes the checks.** Run `python3 -I <SKILL_DIR>/scripts/gate.py --run-values <RUN_VALUES> --worktree <IMPL_WORKTREE> --head <INITIAL_BASE_SHA> --log <RUN_DIR>/gates/baseline-<n>.log --prefix VERIFY`. If the file has `GATE_` lines (`grep -q '^GATE_' <RUN_VALUES>`), run it again without `--prefix`.
   - **Both exit 0:** go on.
   - **Exit 1:** `STATE incr BASELINE_FAILS`. At 3, show the user the last log's path and stop. Otherwise send job `baseline-failed` with `INPUT=<that log>`. `RESULT: RUN_VALUES_FIXED` repeats this item; `RESULT: STOP` goes to Step 8 item 7.
   - **Exit 2:** show the user its output and stop.
6. **Write the queue:** put the `TASKS` ids, space-separated, in `<RUN_DIR>/queue.txt`. It is the only list of tasks you follow; `REPLAN` and `FINAL_FIX` change it.
7. **Validate the plan:** run `python3 -I <SKILL_DIR>/scripts/validate_meta.py tasks --tasks-dir <DOCS_DIR>/tasks --gemini-model <GEMINI_MODEL> --ids "<the queue>" --design <DESIGN_FILE> > <RUN_DIR>/validate-<n>.txt`.
   - **Exit 0:** go on.
   - **Otherwise:** `STATE incr META_FAILS`. At 3, show the user the last output and stop. Otherwise send job `fix-meta` with `INPUT=<that file>`, and on `RESULT: META_FIXED`, validate again.
8. **Tell the user exactly:** "Plan approved: <number of tasks> tasks. Implementation is starting."

## Step 4: Run the task loop

`scripts/run_task.py` runs every task in `<RUN_DIR>/queue.txt`. It implements, checks the tree, runs the gate, runs both reviews, runs fix rounds, merges, and prepares escalations. It keeps its own state in the state file. `TASKLOOP` below means `python3 -I <SKILL_DIR>/scripts/run_task.py --state <RUN_DIR>/state.json advance`.

1. **Run `TASKLOOP`.** Log any `MERGED <id> <head>` lines it prints, and branch on its **last line**:

   | Last line (exit) | Do |
   | --- | --- |
   | `WAIT <dispatch> <kind>` (10) | End your turn. |
   | `QUEUE_EMPTY` (0) | If `PHASE` is `execution`, go to Step 8. If it is `finish`, return to Step 8 item 2. |
   | `ESCALATE <input>` (20) | Send job `escalate` with `INPUT=<input>` (everything after `ESCALATE `). When the architect settles, save its message with `save_message.py --dispatch <ARCHITECT_DISPATCH> --type worker_done --out <RUN_DIR>/results/architect-<n>.md`, release it, and run `TASKLOOP --architect-result <that file>`. |
   | `FIX_META <file>` (21) | Send job `fix-meta` with `INPUT=<file>`. Save and release as for `ESCALATE`, then run `TASKLOOP --architect-result <that file>`. |
   | `PROMPT <file> <reply>...` (30) | A fixed prompt. Show the user the file's contents exactly, and nothing else. A reply that is one of the listed words: run `TASKLOOP --reply <word>`. Any other reply that doesn't start with `coordinator:`: show the file again, followed by "Reply with exactly one of: <the words>." |
   | `STOP <reason>` (40) | Dispatch nothing more and go to Step 8 item 7. |
   | `ERROR <what>` (2) | Show the user the line exactly and stop. |

2. **Whenever Orca wakes you** with a `worker_done` or an `escalation` that has `stopReason` or `failureKind`, from a worker in this loop, run `TASKLOOP` again. A worker's ask and an `approval` are handled by "Messages", and the loop keeps waiting.

You don't read verdicts, count rounds, pick models, reset worktrees, or merge in this step; the script does all of it, from the task's `.meta` and the state file. Its rules are the ones Steps 4–7 used to spell out:
- fresh implementer per task, kept open, reused for its fix rounds;
- the gate before every review, with a failure going to a fix round;
- Stage 1 then Stage 2;
- at most two fix rounds in a row before escalating;
- a reviewer that changes the tree or stops is void and replaced;
- a missing `VERDICT` line twice counts as `FIX`;
- a usage limit pauses the run with a prompt instead of counting as a failure;
- the escalation pair is checked against the `LADDER` and `validate_meta.py`, and two rejections prompt the user;
- `REPLAN` is validated (with `fix-meta`) before the queue changes;
- merges go only through `merge-task.sh`.

## Step 8: Finish

1. **Check the starting state.** `STATE set PHASE=finish`.
   - `ORCA orchestration worker-list --run <RUN_ID>` shows no worker still open. Release any that is.
   - `check-tree.sh <IMPL_WORKTREE> <LAST_MERGED>` exits 0. Otherwise show its output to the user and stop.
2. **Final review:**
   1. Set `<FEATURE_HEAD>` = `LAST_MERGED`.
   2. Build `final-review.txt` (`BASE_SHA=<INITIAL_BASE_SHA>`, `WORKER_HEAD=<FEATURE_HEAD>`, `CONVENTION_PATHS`, `DESIGN=<DESIGN_FILE>`, `DECISIONS_LOG=<DECISIONS_LOG>`, `OUTPUT_FILE=<RUN_DIR>/verdicts/final-<n>.md`). Run `ORCA orchestration task-create --spec-file <spec> --task-title "Final review: <TOPIC>" --json`, then `ORCA orchestration worker-start --task <its taskId> --worktree path:<IMPL_WORKTREE> --agent claude --model opus --json`. `worker-start` takes `--task` or `--spec-file`, never both.
   3. On settle, save it with `save_message.py --dispatch <id> --type worker_done --out <RUN_DIR>/results/final-settle-<n>.md`, release it, and run `check-tree.sh <IMPL_WORKTREE> <FEATURE_HEAD>`. On a non-zero exit, run `discard-changes.sh <IMPL_WORKTREE> <FEATURE_HEAD>` and start a new final reviewer; the verdict is void. Otherwise take the verdict from the review file `<RUN_DIR>/verdicts/final-<n>.md`: its last line, if it is a `VERDICT` line. If it isn't (or the file is missing), use the last `VERDICT: PASS` or `VERDICT: FIX` anywhere in the saved settle message, even mid-sentence (`grep -o 'VERDICT: \(PASS\|FIX\)' <file> | tail -n 1`), and copy that message into the review file. No `VERDICT` line in either gets one new reviewer; a second such line counts as `VERDICT: FIX`.
   4. On `VERDICT: PASS`, go to item 3. On `VERDICT: FIX`, send job `final-review` with `INPUT=<verdict file>`.
   5. **On `RESULT: FINAL_FIX <F id>`:**
      - `STATE incr FINAL_FIX_ROUNDS`. At 3 (two final-fix tasks already merged and the final review still fails), tell the user exactly "The final review still finds issues after two final-fix rounds: <the latest final verdict file>. Reply \"continue\" for one more round, or \"stop\" to stop the run." This is a fixed prompt. "continue" sets `FINAL_FIX_ROUNDS` to 2 and goes on below; "stop" goes to item 7.
      - Validate it as in Step 3 item 7, with `--ids "<F id>"` and no `--design`.
      - Write `<F id>` as the only id in `<RUN_DIR>/queue.txt`, and go to Step 4. The task starts from `<FEATURE_HEAD>`, and its `QUEUE_EMPTY` brings you back here to repeat item 2.
   6. On `RESULT: FINAL_CLEAN`, go to item 3.
3. **Verify** per `superpowers:verification-before-completion`. Run `python3 -I <SKILL_DIR>/scripts/gate.py --run-values <RUN_VALUES> --worktree <IMPL_WORKTREE> --head <LAST_MERGED> --log <RUN_DIR>/verify-<k>.log --prefix VERIFY`.
   - **Exit 0:** go to item 4.
   - **Exit 1:** send job `verification-failed` with `INPUT=<that log>`. `RESULT: FINAL_FIX` goes through item 2's path, including a new final review, and then you verify again.
   - **Exit 2:** show the user its output and stop.
4. **Open the PR:**
   1. Send job `pr` with `INPUT=<the passing verify log>`. On `RESULT: PR_READY <title> <body>`, invoke `superpowers:finishing-a-development-branch` for `<FEATURE_BRANCH>` with target `<PR_TARGET>`, title `$(cat <title>)`, and body file `<body>`.
      - Tell it the branch is not checked out anywhere: it pushes with `git -C <MAIN_CHECKOUT> push -u origin <FEATURE_BRANCH>`, it has already been verified (the log above), and it should skip its own worktree cleanup.
      - **`jira`:** tell it to push and create the PR immediately, without presenting options.
      - **`written`:** let it offer its usual options and carry out the user's choice.
   2. Log the PR URL.
5. **Update JIRA** (`jira` with a PR only): send job `jira-close` with `INPUT=<PR URL>`, and log the `RESULT: JIRA_DONE` line.
6. **Clean up:**
   - Once a PR is open or the branch is merged, run `ORCA worktree rm --worktree path:<IMPL_WORKTREE> --delete-branch`, never with `--force`. If it fails, show the user the output.
   - If the user chose keep or discard, leave the worktree in place.
7. **Report:**
   1. Send job `report` with `INPUT=<RUN_DIR>/run-log.md`. On `RESULT: REPORT <path>`, relay that file to the user as in Rule 5.
   2. Add exactly: "Run documents: `<DOCS_DIR>`. Remove the docs worktree with `orca worktree rm --worktree path:<DOCS_WORKTREE>` when you're done with them."
   3. Release the architect with `worker-release --dispatch <ARCHITECT_DISPATCH>`, and `STATE set PHASE=done`.

## Messages (any step)

- **An architect ask whose body is `RELAY <path>` or `NOTIFY <path>`:** relay it (Rule 5). For `RELAY`, `STATE set OPEN_RELAY=<message id> WAITING_ON=user` before you end your turn, and `STATE unset OPEN_RELAY` once you've replied.
- **Any other worker's ask** (implementer, reviewer, builder check, EGM):
  1. Save it with `save_message.py --id <message id> --header "task=<id> <title>" --out <RUN_DIR>/asks/<n>.md`. In the design phase the header is `design phase`.
  2. `STATE set OPEN_ASK=<message id>`, then send job `consult` with `INPUT=<that file>`.
  3. On `RESULT: ANSWER <file>`, run `ORCA orchestration reply --id <OPEN_ASK> --body "$(cat <file>)"`, then `STATE unset OPEN_ASK`.
- **A user message:**
  - **Starting with `coordinator:`:** it is for you.
    - "status" means show the last 10 run-log lines.
    - "use the fallback" means "Architect lost" below.
    - "stop" means stop every worker with `worker-stop`, then go to Step 8 item 7.
    - Anything else: say "I only understand: status, use the fallback, stop."
  - **Otherwise, with a relay open:** it is the answer. Write it, exactly as typed, to `<RELAY_DIR>/<NNN>-from-user.md`, then reply to the architect's ask with `--body "RELAY <that path>"`.
  - **Otherwise, with no relay open:** write it the same way and send job `user-message` with `INPUT=<that path>`. If an architect job is still running, send it only after that job's `RESULT:` line, so two architects never work at once.
- **A fresh architect fails** (after plan approval): it settles `failed`, its first line isn't `RESULT:`, or Orca reports it `exited` or `lost`.
  1. Release or stop it, then `STATE incr JOB_RETRIES`.
  2. At 1, start the same job again from `LAST_JOB` and `LAST_JOB_INPUT`.
  3. At 2, tell the user exactly "The architect failed twice on job <LAST_JOB>. Reply \"retry\" to try again, or \"stop\" to stop the run." This is a fixed prompt. "retry" resets `JOB_RETRIES` to 0 and starts the job again; "stop" goes to Step 8 item 7.

  Reset `JOB_RETRIES` to 0 after every job that settles with a `RESULT:` line.
- **Architect lost** (design phase only): an Orca escalation names `ARCHITECT_DISPATCH` as `exited` or `lost`, a send to it is refused, or the user says "use the fallback".
  1. Build `architect-start.txt` with `FIRST_JOB=resume` and `INPUT=none`, and start it as in Step 1 item 10.
  2. Record the new `ARCHITECT_DISPATCH`.
  3. After `RESULT: RESUMED`, resend `LAST_JOB` with `LAST_JOB_INPUT`, if it had no `RESULT`. Any relay the lost architect had open is dead: the new architect asks again if it still needs the answer. If the user answers that dead relay anyway, send their answer as a `user-message` job.

  Never `send` to the old architect again (orca-facts: reopened workers can't settle).
- **An escalation with `stopReason` or `failureKind` from a non-architect worker:** in Step 4, run `TASKLOOP`; it decides. A design worker counts as `failed` (Step 2). The final reviewer is void, so start a new one.
- **An escalation with `approval`** (a worker's permission prompt): tell the user exactly "A worker is waiting on a permission prompt in its tab: <worker title>. Allow or deny it there." Carry on with anything else.

## Resume a run

Use this when the user asks to continue a run, or when Step 1 item 7 gets "resume". After a context compaction in the same session, you need only item 1 (`STATE show`) and item 4.

1. Find `<RUN_DIR>/state.json`. For a topic, it is in the worktree on `refs/heads/<TOPIC>-docs` (`git worktree list --porcelain`). Run `STATE show`.
   - **No state file, or no `RUN_ID` in it:** the run stopped in Step 1, before any worker started. Tell the user exactly "The <TOPIC> run stopped before its architect started, so I'm starting it again." Then run Step 1, whose item 7 gives the new run its own name.
2. Load Orca as in Step 1 item 3, then run `ORCA orchestration run-use --id <RUN_ID>` to coordinate the run from this session. If it fails, show the user its output and stop.
3. Run `ORCA orchestration worker-list --run <RUN_ID>`.
4. Pick up, checking these in order:
   - **`WAITING_ON` is `ARCHITECT_DISPATCH`:** run `save_message.py --dispatch <it> --type worker_done --out <RUN_DIR>/results/architect-<n>.md`.
     - `SAVED`: if `LAST_JOB` is `escalate` or `fix-meta`, run `TASKLOOP --architect-result <that file>` and continue at Step 4 item 1. Otherwise handle its `RESULT:` line in the step that sent `LAST_JOB`.
     - `NOT_FOUND`: if `worker-show` says it is still working, wait. If it is `exited` or `lost`, follow "Architect lost" (design phase) or "A fresh architect fails". Never run `TASKLOOP` here: it would print the same `ESCALATE` again and start a second architect.
   - **`STEP` starts with `run_task`:** run `TASKLOOP` and continue at Step 4 item 1.
   - **`WAITING_ON` is another dispatch id:** run `save_message.py --dispatch <it> --type worker_done --out <RUN_DIR>/results/resume-<n>.md`.
     - `SAVED`: handle that settle as the step says.
     - `NOT_FOUND`: check `worker-show --dispatch <it>`. If it is still running, wait for it. If it is `exited` or `lost`, handle it as that worker's loss: "Architect lost" or "A fresh architect fails" for an architect, and the `stopReason` rule above for any other worker.
   - **`OPEN_RELAY` is set:** the relay file is the one its message names. Show it to the user again as in Rule 5, and wait.
   - **`OPEN_ASK` is set and no `consult` result came back:** send the `consult` job again.
5. Tell the user exactly "Resumed the <TOPIC> run at step <STEP>." and continue.

## Templates

| Template | Fills | Used in |
| --- | --- | --- |
| `architect-start.txt` | `FIRST_JOB`, `INPUT`, and the run values | Step 1, Architect lost, every job after plan approval |
| `architect-job.txt` | `SKILL_DIR`, `JOB`, `INPUT` | design-phase jobs |
| `builder-check.txt` | `DESIGN_FILE`, `OUTPUT_FILE` | Step 2 |
| `egm.txt` | `EGM_SKILLS`, `DESIGN_FILE`, `OUTPUT_FILE` | Step 2 |
| `lens.txt` | `EGM_SKILLS`, `CONCEPTS_FILE`, `OUTPUT_FILE` | Step 2 |
| `plan-review.txt` | `EGM_SKILLS`, `PLAN_FILE`, file `CONTEXT`, `OUTPUT_FILE` | Step 2 |
| `implement.txt` | `TASK_TITLE`, file `TASK`, file `DECISIONS` | Step 4, filled by `run_task.py` |
| `commit-uncommitted.txt` | `TASK_TITLE`, file `TASK` | Step 4, filled by `run_task.py` |
| `spec-review.txt` | file `TASK`, file `DECISIONS`, `BASE_SHA`, `WORKER_HEAD`, `OUTPUT_FILE` | Step 4, filled by `run_task.py` |
| `quality-review.txt` | file `SUMMARY`, file `DECISIONS`, `CONVENTION_PATHS`, `BASE_SHA`, `WORKER_HEAD`, `OUTPUT_FILE` | Step 4, filled by `run_task.py` |
| `fix.txt` | `TASK_TITLE`, `BASE_SHA`, `WORKER_HEAD`, file `FINDINGS`, file `DECISIONS`, file `IMPLEMENT_SPEC` | Step 4, filled by `run_task.py` |
| `escalate.txt` | `TASK_TITLE`, `RESET_SHA`, file `NOTE`, file `DECISIONS`, file `IMPLEMENT_SPEC` | Step 4, filled by `run_task.py` |
| `final-review.txt` | `BASE_SHA`, `WORKER_HEAD`, `CONVENTION_PATHS`, file `DESIGN`, file `DECISIONS_LOG`, `OUTPUT_FILE` | Step 8 |

## State keys

| Keys | Meaning | Set in |
| --- | --- | --- |
| `SKILL_DIR`, `DATE`, `TOPIC`, `REQUIREMENTS_SOURCE`, `JIRA_KEY`, `REQUIREMENTS_FILE`, `EGM_REQUESTED`, `GEMINI_MODEL`, `GEMINI_EFFORT` | From the request | 1 |
| `ORCA`, `EGM_SKILLS`, `MAIN_CHECKOUT`, `DEFAULT_BRANCH`, `START_SHA`, `START_HOW` | Tools and repo | 1 |
| `DOCS_WORKTREE`, `DOCS_DIR`, `RUN_DIR`, `RELAY_DIR`, `RUN_ID` | The docs worktree and the Orca run | 1 |
| `DOCS_HEAD` | The docs worktree's commit before a read-only design worker | 2 |
| `PHASE` | `design`, `execution`, `finish`, or `done` | 1, 3, 8 |
| `STEP`, `WAITING_ON`, `OPEN_RELAY`, `OPEN_ASK` | Where to pick up after a compaction or restart | every wait |
| `ARCHITECT_DISPATCH`, `LAST_JOB`, `LAST_JOB_INPUT`, `JOB_RETRIES` | The current architect and its job | every job |
| `RUN_VALUES`, `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS` | From `run-values.txt` | 3 |
| `INITIAL_BASE_SHA`, `LAST_MERGED`, `IMPL_WORKTREE` | Execution | 3; `run_task.py` updates `LAST_MERGED` |
| `CURRENT_TASK`, `LOOP_<id>`, `LOOP_WAIT`, and every key ending in `_<id>` (`BASE_SHA_`, `WORKER_HEAD_`, `PAIR_`, `FIX_ROUNDS_`, `ATTEMPT_`, ...) | The task loop's own state. Read them for status; never set them | 4 (`run_task.py`) |
| `EGM_RUNS`, `LENS_RUNS`, `PLAN_REVIEW_RUNS`, `RETRY_<kind>`, `META_FAILS`, `BASELINE_FAILS`, `FINAL_FIX_ROUNDS` | Run counters | 2, 3, 8 |
