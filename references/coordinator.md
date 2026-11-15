# Coordinator: the whole run

Part of orca-multiagent-workflow. The Rules in SKILL.md hold throughout.

## How you work

- **You decide nothing yourself.** Every step says what to run and how to branch, and you branch only on a script's exit code and last line, the architect's first line (`RESULT: ...`), a worker settling `succeeded` or `failed`, or a counter from the state file. Every question goes to the architect, through a job or a relay.
- **The state file holds every value** (Rule 18). `STATE` below means `python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json`.
  - Record a value the moment you learn it: `STATE set KEY=value`. Read it when a command needs it: `STATE get KEY`. Counters: `STATE incr KEY` prints the new count; a missing counter is 0.
  - **Before every turn you end while waiting,** set `STEP` (where to pick up, e.g. `2 design` or `3 setup`) and `WAITING_ON` (the dispatch id you wait on, or `user`). The scripts of Steps 2, 4 and 8 set both themselves while their own workers run.
- **Script shorthands:** `DESIGN`, `TASKLOOP`, `FINISH` and `PROBE` mean `python3 -I <SKILL_DIR>/scripts/<run_design|run_task|run_finish|probe>.py --state <RUN_DIR>/state.json advance`. Each prints its status as its last line. An `ERROR <what>` line (exit 2) from any of them: show the user the line exactly and stop.
- **You never write spec text.** Build every spec with `python3 -I <SKILL_DIR>/scripts/build_spec.py --template <SKILL_DIR>/references/templates/<name>.txt --out <RUN_DIR>/specs/<name-for-it>.txt --set NAME=value ... --file NAME=path ...`. If it prints `UNFILLED`, `MISSING_FILE` or `UNUSED`, fix your command, not the template. You fill only `architect-start.txt` (`FIRST_JOB`, `INPUT`, and every run value it names) and `architect-job.txt` (`SKILL_DIR`, `JOB`, `INPUT`); the scripts fill every other template.
- **You never retype a worker's message.** Save an architect's settle, or any worker's ask, with `python3 -I <SKILL_DIR>/scripts/save_message.py --orca <ORCA> --run <RUN_ID> --out <file>` plus `--dispatch <id> --type worker_done` (a settle), or `--id <message id>` (an ask). `--header <line>` puts a line first, and `--append` adds to a file. `NOT_FOUND` means the message isn't in the inbox yet: run `ORCA orchestration check --peek --wait --timeout-ms 60000`, then save again. The scripts save their own workers' settles; you never save or read those.
- **File numbers:** `<n>` or `<NNN>` in a file name is the next free number in that folder. The exception is `<NNN>-from-user.md`, which takes the number of the `<NNN>-to-user.md` it answers. All paths you pass to scripts are absolute.
- **Fixed prompts:** some steps stop with a fixed prompt and list the replies they accept. While one is open, a user message that isn't one of those replies and doesn't start with `coordinator:` gets the same prompt again, followed by "Reply with exactly one of: <the accepted replies>." Don't send it to the architect.
- **Record every dispatch id you start** in the state file, and **keep a run log:** append one line per event to `<RUN_DIR>/run-log.md`, `<time> <event>` for what you do yourself: the architects you start and their settles, merges you see, the PR URL. The scripts log their own workers.
- **Launch arguments:** `ARGS(<pair>)` means the one line `python3 -I <SKILL_DIR>/scripts/harnesses.py args --state <RUN_DIR>/state.json --pair <pair>` prints: the `--agent`, `--model` and `--effort` options for that pair, in the launch style its test launch proved. Run it on its own first. Exit 0: put its line where `ARGS(...)` stands. Any other exit: show the user its output exactly and stop; never put that output into a `worker-start`. Never type those options yourself (Rule 3).
- **An architect job in flight comes first** (from sending it until you've handled its `RESULT:` line). Never run `DESIGN`, `PROBE`, `TASKLOOP` or `FINISH` then, even when Orca wakes you about one of their workers: keep waiting. **`--docs-moved` is only for `consult` and `user-message`** (Messages): those jobs run while a script waits, and may commit in the docs worktree, which that script would reset away. Once you've handled the `RESULT:` of one of them, if `STEP` names a script, run it once with `--docs-moved` added, which keeps those commits, and branch as its step says: `DESIGN`, `TASKLOOP` or `FINISH` when `STEP` starts with `run_design`, `run_task` or `run_finish`; `PROBE` when it ends in `probe`. Every other job's `RESULT:` goes where its step says, without the flag. Never set the scripts' keys yourself.
- **Architect jobs** (Rule 15). Set `LAST_JOB=<JOB>`, `LAST_JOB_INPUT=<input>` and `WAITING_ON=<ARCHITECT_DISPATCH>` (the new id) before you end your turn. The architect settles with a `RESULT:` line: save the settle with `save_message.py --dispatch <ARCHITECT_DISPATCH> --type worker_done --out <RUN_DIR>/results/architect-<n>.md`, and branch on its first line.
  - **Design phase** (`PHASE=design`): build `architect-job.txt`, run `ORCA orchestration send --to dispatch:<ARCHITECT_DISPATCH> --subject "Architect job: <JOB>" --body "$(cat <spec file>)"`, and record the new dispatch id from the receipt as `ARCHITECT_DISPATCH`.
  - **After plan approval:** build `architect-start.txt` with `FIRST_JOB=<JOB>` and `INPUT=<input>`, run `ORCA orchestration worker-start --spec-file <spec> --task-title "Architect job: <JOB>" --worktree path:<DOCS_WORKTREE> ARGS(<the job's pair>) --json`, and record its dispatch id as `ARCHITECT_DISPATCH`. Once you've handled its `RESULT:` line, release it.
- **Architect job models** (after plan approval):

  | Job | Pair |
  | --- | --- |
  | `consult`, `escalate`, `final-review`, `verification-failed`, `baseline-failed`, `user-message` | `ROLE_ARCHITECT` |
  | `fix-meta`, `pr`, `jira-close`, `report` | `CLERICAL_PAIR` once Step 3 item 8 has printed `PROBED`; `ROLE_ARCHITECT` before that |

- **Workers by role.** You start only architects; the scripts start every other worker on its pair:

  | Worker | Pair | Started by |
  | --- | --- | --- |
  | Design architect (and fallback); fresh architects after plan approval | `ROLE_ARCHITECT`, kept open; "Architect job models" | you |
  | Test launch | each pair being tested | `probe.py` |
  | Builder check; EGM lens pass, design review, plan review | the pair in the `NEED builder-check` line; `ROLE_EGM` | `run_design.py` |
  | Implementer, escalation attempt, Stage 1 and Stage 2 reviewers | the task's `.meta` and the computed ladder | `run_task.py` |
  | Final reviewer | `ROLE_ARCHITECT` | `run_finish.py` |

## Step 1: Intake

1. **Set the values** from your brief and the user's request:
   - `REQUIREMENTS_SOURCE`: `jira` when there's a JIRA key; `written` when there's pasted text or a file path; `jira` plus a requirements file when there are both. When there's neither, say exactly "Give me a JIRA key or the requirements (pasted text or a file path)." and wait.
   - `JIRA_KEY`, or `none`. `EGM_REQUESTED`: `yes` only if the user asked for EGM or a spec review.
   - `TOPIC`: `JIRA_KEY`, or a 2–4 word kebab-case slug of the requirements. `DATE`: today, `YYYY-MM-DD`.
   - `SKILL_DIR`: the folder holding the `SKILL.md` your brief named. `ROLES_FILE` and `SETUP_CATALOG`: the picks file and the setup catalog your brief named.

   Keep them for item 7, where the state file is created. Then **check the picks** before anything exists: `python3 -I <SKILL_DIR>/scripts/roles.py remembered --roles <ROLES_FILE> --catalog <SETUP_CATALOG>` prints them as `role=pair` lines, and `python3 -I <SKILL_DIR>/scripts/roles.py check --catalog <SETUP_CATALOG> --jira <yes if JIRA_KEY isn't none, else no> --coordinator <pair> --architect <pair> --egm <pair> --base <pair>` checks them. On exit 1, show the user every `MISSING` and `UNKNOWN` line exactly and stop (Rule 21).
2. **Load Orca:** load the `orchestration` skill, resolve `ORCA` per its "Resolve the CLI for this session" section, run `ORCA skills get orchestration`, and load the `orca-cli` skill. If an Orca error isn't fixed by the guide's recovery steps, show it to the user verbatim and stop.
3. **Find the main checkout.** `MAIN_CHECKOUT` is `git rev-parse --show-toplevel`. `DEFAULT_BRANCH` is `git -C <MAIN_CHECKOUT> symbolic-ref --short refs/remotes/origin/HEAD` without its `origin/` prefix; if that fails, `git -C <MAIN_CHECKOUT> branch --show-current`. The run never checks out a branch in the main checkout, so it doesn't need to be clean.
4. **Choose the start commit:** run `bash <SKILL_DIR>/scripts/start-point.sh <MAIN_CHECKOUT> <DEFAULT_BRANCH>`. It prints `START <sha> <how>`, which gives `START_SHA` and `START_HOW`. On exit 3, say exactly "Local `<DEFAULT_BRANCH>` and `origin/<DEFAULT_BRANCH>` have diverged. Reconcile them, then tell me \"retry\"." This is a fixed prompt; "retry" repeats this item.
5. **Check for an earlier run of this topic.** Run `git -C <MAIN_CHECKOUT> worktree list --porcelain`, and look for a worktree on `refs/heads/<TOPIC>-docs`.
   - **If one exists and its `.run/state.json` exists,** say exactly "There's already a run for <TOPIC> at <its path>. Reply \"resume\" to continue it, or \"new\" to start a separate run." This is a fixed prompt. "new" goes to the next bullet. "resume" hands that run to its own coordinator, since picks are fixed for a run and you started on the new ones: do SKILL.md "Continue a run" item 3 with that state file, then stop this session. Create nothing first.
   - **If `<TOPIC>-docs` or `<TOPIC>-impl` exists as a branch** (`git -C <MAIN_CHECKOUT> show-ref --verify refs/heads/<name>`), or the user said "new," change `TOPIC` to `<TOPIC>-<k>`, the smallest `<k>` from 2 up for which neither branch exists.
6. **Create the docs worktree:**
   1. Run `ORCA worktree create --name <TOPIC>-docs --branch <TOPIC>-docs --base-branch <START_SHA>`. If Orca refuses a commit as the base, run it again with `--base-branch <DEFAULT_BRANCH>`. Record its path as `DOCS_WORKTREE`. Then `DOCS_DIR` is `<DOCS_WORKTREE>/docs/plans`, `RUN_DIR` is `<DOCS_WORKTREE>/.run`, and `RELAY_DIR` is `<RUN_DIR>/relay`.
   2. `bash <SKILL_DIR>/scripts/check-tree.sh <DOCS_WORKTREE> <START_SHA>` must exit 0. On exit 1, run `bash <SKILL_DIR>/scripts/discard-changes.sh <DOCS_WORKTREE> <START_SHA>`; the branch is new, so nothing is lost.
   3. Append `.run/` to `$(git -C <MAIN_CHECKOUT> rev-parse --git-common-dir)/info/exclude`, plus `<DOCS_WORKTREE>`'s parent folder (e.g. `.worktrees/`) if it is inside `<MAIN_CHECKOUT>`. Run `mkdir -p <DOCS_DIR> <RELAY_DIR> <RUN_DIR>/specs <RUN_DIR>/verdicts <RUN_DIR>/asks <RUN_DIR>/gates <RUN_DIR>/results`.
7. **Create the state file:** `STATE set` every value from items 1–6, plus `ORCA=<the resolved executable>` and `PHASE=design`.
8. **Copy the picks:** run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <RUN_DIR>/catalog.json`; then `roles.py check` as in item 1, with `--catalog <RUN_DIR>/catalog.json`; then `python3 -I <SKILL_DIR>/scripts/roles.py to-state --roles <ROLES_FILE> --state <RUN_DIR>/state.json --catalog <RUN_DIR>/catalog.json`, which sets `ROLE_COORDINATOR`, `ROLE_ARCHITECT`, `ROLE_EGM`, `ROLE_BASE`, `BASE_TOP` and `CATALOG`. If any of the three exits other than 0, show the user its output exactly and stop (Rule 21).
9. **Bind the Orca run:** run `ORCA orchestration run-create --objective "orca-multiagent-workflow: <TOPIC>" --json`, and record its run id as `RUN_ID`.
10. **Test-launch the picks:** run `PROBE --set step1` and branch on its last line:

    | Last line (exit) | Do |
    | --- | --- |
    | `PROBED` (0) | Go on to item 11. |
    | `WAIT <dispatch> probe` (10) | `STATE set STEP="1 probe" WAITING_ON=<dispatch>` and end your turn. When Orca wakes you about a test-launch worker, run `PROBE` (no `--set`) and branch again. |
    | `PROBE_FAILED <pair> <file>` (40) | Show the user the file's contents exactly, then say exactly "The test launch of <pair> failed, so the run stops here." Start nothing (Rule 21). |

11. **Save written requirements** to `<DOCS_DIR>/<DATE>-<TOPIC>-requirements.md` and record it as `REQUIREMENTS_FILE`: for a file path, `cp` it; for pasted text, write it exactly as the user typed it, with only the requirements, not instructions about the run. Otherwise `REQUIREMENTS_FILE` is `none`.
12. **Start the architect.** Build `architect-start.txt` with `FIRST_JOB=start`, `INPUT=none`, and every run value it names. Run `ORCA orchestration worker-start --spec-file <spec> --task-title "Architect: <TOPIC>" --worktree path:<DOCS_WORKTREE> ARGS(<ROLE_ARCHITECT>) --keep-open --json`, record `ARCHITECT_DISPATCH` (`dispatchId`), and `STATE set LAST_JOB=start LAST_JOB_INPUT=none STEP="2 design" WAITING_ON=<ARCHITECT_DISPATCH>`.
13. **Tell the user exactly:** "The architect is designing this with you. I'll pass its questions to you here unchanged; reply here and I'll pass your answers back unchanged. To talk to me instead, start your message with `coordinator:`." Then end your turn.

## Step 2: Design phase

Wait for the architect, and handle messages per "Messages". When it settles, save its message (How you work, "Architect jobs") and branch on its first line:

- **`RESULT: NEED ...` or `RESULT: COMMITTED`:** run `DESIGN --result <the saved file>`.
- **`RESULT: PLAN_APPROVED <run-values>`:** go to Step 3.
- **Any other `RESULT:` line** answers the job that asked for it ("Messages", "Architect lost").

Branch on `DESIGN`'s last line:

| Last line (exit) | Do |
| --- | --- |
| `WAIT <dispatch> <kind>` (10) | End your turn. When Orca wakes you with that worker's settle or an escalation for it, run `DESIGN` (no `--result`) and branch again, unless an architect job is in flight (How you work); the script saves and reads the settle. |
| `ARCHITECT_JOB <job> <input>` (20) | Send the architect job `<job>` with `INPUT=<input>` (everything after the job name), unchanged. Its next `RESULT:` line goes through the list above. |

`run_design.py` checks the docs worktree, test-launches the builder-check pair, starts each worker, retries a failed one once, counts runs, and writes the `-done` job's input, including `failed: <file>` after a second failure. You never do any of that in this step, and never read a design worker's output.

## Step 3: Set up execution

1. **Release the design architect** with `worker-release --dispatch <ARCHITECT_DISPATCH>`, and `STATE set PHASE=execution STEP="3 setup"`. From now on every architect job is a fresh worker.
2. **Read `<run-values>`** and `STATE set` each of `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS` and `CLERICAL_PAIR`, plus `RUN_VALUES=<run-values path>`. The `VERIFY_<n>` and `GATE_<n>` commands stay in that file; `gate.py` reads them from there.
3. **Create the feature branch** without checking it out: run `git -C <MAIN_CHECKOUT> branch <FEATURE_BRANCH> <START_SHA>`. If that fails, show the user its output and stop. `STATE set INITIAL_BASE_SHA=<START_SHA> LAST_MERGED=<START_SHA>`.
4. **Create the implementation worktree:** run `ORCA worktree create --name <TOPIC>-impl --branch <TOPIC>-impl --base-branch <FEATURE_BRANCH>` and record its path as `IMPL_WORKTREE`. `bash <SKILL_DIR>/scripts/check-tree.sh <IMPL_WORKTREE> <INITIAL_BASE_SHA>` must exit 0; otherwise show its output to the user and stop.
5. **Check the base passes the checks.** Run `python3 -I <SKILL_DIR>/scripts/gate.py --run-values <RUN_VALUES> --worktree <IMPL_WORKTREE> --head <INITIAL_BASE_SHA> --log <RUN_DIR>/gates/baseline-<n>.log --prefix VERIFY`. If the file has `GATE_` lines (`grep -q '^GATE_' <RUN_VALUES>`), run it again without `--prefix`.
   - **Both exit 0:** go on. **Exit 2:** show the user its output and stop.
   - **Exit 1:** `STATE incr BASELINE_FAILS`. At 3, show the user the last log's path and stop. Otherwise send job `baseline-failed` with `INPUT=<that log>`. `RESULT: RUN_VALUES_FIXED` repeats this item; `RESULT: STOP` goes to Step 8 item 7.
6. **Write the queue:** put the `TASKS` ids, space-separated, in `<RUN_DIR>/queue.txt`. It is the only list of tasks you follow; `REPLAN` and `FINAL_FIX` change it.
7. **Validate the plan:** run `python3 -I <SKILL_DIR>/scripts/validate_meta.py tasks --state <RUN_DIR>/state.json --tasks-dir <DOCS_DIR>/tasks --ids "<the queue>" --design <DESIGN_FILE> > <RUN_DIR>/validate-<n>.txt`, then `python3 -I <SKILL_DIR>/scripts/validate_meta.py harness-pair --state <RUN_DIR>/state.json --pair <CLERICAL_PAIR> >> <the same file>`.
   - **Both exit 0:** `STATE set META_FAILS=0` and go on.
   - **Otherwise:** `STATE incr META_FAILS`. At 3, show the user the last output and stop. Otherwise send job `fix-meta` with `INPUT=<that file>`. On `RESULT: META_FIXED`, read `CLERICAL_PAIR` from `<RUN_VALUES>` again, `STATE set` it, and repeat this item.
8. **Test-launch the plan's pairs:** run `PROBE --set step3` and branch as in Step 1 item 10, with `STEP="3 probe"`; `PROBED` goes on to item 9.
9. **Tell the user exactly:** "Plan approved: <number of tasks> tasks. Implementation is starting." Then go to Step 4.

## Step 4: Run the task loop

`run_task.py` runs every task in `<RUN_DIR>/queue.txt`: it implements, checks the tree, runs the gate, runs both reviews and the fix rounds, merges, and prepares escalations. Its rules (fix-round caps, void reviewers, usage limits, ladder checks) are in its module docstring.

1. **Run `TASKLOOP`.** Log any `MERGED <id> <head>` lines it prints, and branch on its **last line**:

   | Last line (exit) | Do |
   | --- | --- |
   | `WAIT <dispatch> <kind>` (10) | End your turn. |
   | `QUEUE_EMPTY` (0) | If `PHASE` is `execution`, go to Step 8. If it is `finish`, run `FINISH` and branch as in Step 8 items 1–3. |
   | `ESCALATE <input>` (20) | Send job `escalate` with `INPUT=<input>` (everything after `ESCALATE `). When the architect settles, save its message, release it, and run `TASKLOOP --architect-result <the saved file>`. |
   | `FIX_META <file>` (21) | Send job `fix-meta` with `INPUT=<file>`. Save and release as for `ESCALATE`, then run `TASKLOOP --architect-result <the saved file>`. |
   | `PROMPT <file> <reply>...` (30) | A fixed prompt. Show the user the file's contents exactly, and nothing else. A reply that is one of the listed words: run `TASKLOOP --reply <word>`. Any other reply that doesn't start with `coordinator:`: show the file again, followed by "Reply with exactly one of: <the words>." |
   | `STOP <reason>` (40) | Dispatch nothing more and go to Step 8 item 7. |

2. **Whenever Orca wakes you** with a `worker_done`, or an escalation with `stopReason` or `failureKind`, from a worker in this loop, run `TASKLOOP` again, unless an architect job is in flight (How you work). A worker's ask and an `approval` are handled by "Messages", and the loop keeps waiting.

You don't read verdicts, count rounds, pick pairs, reset worktrees, or merge in this step; the script does all of it.

## Step 8: Finish

**Items 1–3: final review and verification.** Run `FINISH` and branch on its last line. At each of items 4–7, first `STATE set STEP="8 <item number>"`.

| Last line (exit) | Do |
| --- | --- |
| `VERIFIED <log>` (0) | Go to item 4, with `<log>` as the passing verify log. |
| `WAIT <dispatch> <kind>` (10) | End your turn. When Orca wakes you about that worker (a final reviewer, or a `probe` of a final-fix task's reviewer pairs), run `FINISH` again. |
| `ARCHITECT_JOB <job> <input>` (20) | Send job `<job>` with `INPUT=<input>`. When the architect settles, save its message, release it, and run `FINISH --architect-result <the saved file>`. |
| `RUN_QUEUE <F id>` (21) | `queue.txt` now holds that task: go to Step 4. Its `QUEUE_EMPTY` brings you back here. |
| `PROMPT <file> <reply>...` (30) | A fixed prompt, handled as in Step 4, with `FINISH --reply <word>`. |
| `STOP <reason>` (40) | Dispatch nothing more and go to item 7. |

4. **Open the PR:**
   1. Send job `pr` with `INPUT=<the passing verify log>`. On `RESULT: PR_READY <title> <body>`, invoke `superpowers:finishing-a-development-branch` for `<FEATURE_BRANCH>` with target `<PR_TARGET>`, title `$(cat <title>)`, and body file `<body>`. Tell it the branch is not checked out anywhere (it pushes with `git -C <MAIN_CHECKOUT> push -u origin <FEATURE_BRANCH>`), that it has already been verified (the log above), and to skip its own worktree cleanup. For `jira`, tell it to push and create the PR immediately, without presenting options; for `written`, let it offer its usual options and carry out the user's choice.
   2. Log the PR URL.
5. **Update JIRA** (`jira` with a PR only): send job `jira-close` with `INPUT=<PR URL>`, and log the `RESULT: JIRA_DONE` line.
6. **Clean up:** once a PR is open or the branch is merged, run `ORCA worktree rm --worktree path:<IMPL_WORKTREE> --delete-branch`, never with `--force`. If it fails, show the user the output. If the user chose keep or discard, leave the worktree in place.
7. **Report:** send job `report` with `INPUT=<RUN_DIR>/run-log.md`. On `RESULT: REPORT <path>`, relay that file to the user as in Rule 5, then add exactly: "Run documents: `<DOCS_DIR>`. Remove the docs worktree with `orca worktree rm --worktree path:<DOCS_WORKTREE>` when you're done with them." Release the architect with `worker-release --dispatch <ARCHITECT_DISPATCH>`, and `STATE set PHASE=done`.

## Messages (any step)

- **An architect ask whose body is `RELAY <path>` or `NOTIFY <path>`:** relay it (Rule 5). For `RELAY`, `STATE set OPEN_RELAY=<message id> WAITING_ON=user` before you end your turn; once you've replied, `STATE unset OPEN_RELAY` and `STATE set WAITING_ON=<ARCHITECT_DISPATCH>`.
- **Any other worker's ask:** save it with `save_message.py --id <message id> --header "task=<id> <title>" --out <RUN_DIR>/asks/<n>.md` (in the design phase the header is `design phase`), `STATE set OPEN_ASK=<message id>`, and send job `consult` with `INPUT=<that file>`. On `RESULT: ANSWER <file>`, run `ORCA orchestration reply --id <OPEN_ASK> --body "$(cat <file>)"`, then `STATE unset OPEN_ASK WAITING_ON`, so a resume goes back to the step's own `STEP`, and go back to that step with `--docs-moved` (How you work).
- **A user message:**
  - **Starting with `coordinator:`:** it is for you. "status" means show the last 10 run-log lines. "use the fallback" means "Architect lost" below. "stop" means stop every worker with `worker-stop`, then go to Step 8 item 7. Anything else, including a request to change a pick, a model or a `.meta`: say exactly "I only understand: status, use the fallback, stop." and change nothing.
  - **Otherwise, with a relay open:** it is the answer. Write it, exactly as typed, to `<RELAY_DIR>/<NNN>-from-user.md`, then reply to the architect's ask with `--body "RELAY <that path>"`.
  - **Otherwise:** write it the same way and send job `user-message` with `INPUT=<that path>`. If an architect job is still running, send it only after that job's `RESULT:` line, so two architects never work at once. On `RESULT: NOTED`, `STATE unset WAITING_ON`, and go back to the step as for `consult`.
- **A fresh architect fails** (after plan approval): it settles `failed`, its first line isn't `RESULT:`, or Orca reports it `exited` or `lost`. Release or stop it, then `STATE incr JOB_RETRIES`. At 1, start the same job again from `LAST_JOB` and `LAST_JOB_INPUT`. At 2, say exactly "The architect failed twice on job <LAST_JOB>. Reply \"retry\" to try again, or \"stop\" to stop the run." This is a fixed prompt: "retry" resets `JOB_RETRIES` to 0 and starts the job again; "stop" goes to Step 8 item 7. Reset `JOB_RETRIES` to 0 after every job that settles with a `RESULT:` line.
- **Architect lost** (design phase only): an Orca escalation names `ARCHITECT_DISPATCH` as `exited` or `lost`, a send to it is refused, or the user says "use the fallback".
  Build `architect-start.txt` with `FIRST_JOB=resume` and `INPUT=none`, start it with Step 1 item 12's `worker-start` command, and `STATE set` the new `ARCHITECT_DISPATCH` and `WAITING_ON` to its dispatch id; leave `LAST_JOB` as it is. After `RESULT: RESUMED`, resend `LAST_JOB` with `LAST_JOB_INPUT`, if it had no `RESULT`. Any relay the lost architect had open is dead: the new architect asks again if it still needs the answer, and a user's answer to it anyway goes as a `user-message` job. Never `send` to the old architect again (orca-facts: reopened workers can't settle).
- **An escalation with `stopReason` or `failureKind` from any other worker:** run the script whose `WAIT` you are in (`PROBE`, `DESIGN`, `TASKLOOP` or `FINISH`); it decides.
- **An escalation with `approval`** (a worker's permission prompt): say exactly "A worker is waiting on a permission prompt in its tab: <worker title>. Allow or deny it there." Carry on with anything else.

## Resume a run

Use this when your brief or the user asks to continue a run. After a context compaction in the same session, you need only item 1 (`STATE show`) and item 5, or item 3 first when `STEP` is `resume probe`.

1. Find `<RUN_DIR>/state.json` (for a topic, in the worktree on `refs/heads/<TOPIC>-docs`) and run `STATE show`.
   - **No state file, or no `RUN_ID` in it:** the run stopped in Step 1, before any work. Say exactly "The <TOPIC> run stopped before its architect started, so I'm starting it again." Then run Step 1, whose item 5 gives the new run its own name.
   - **No `ROLE_ARCHITECT` in it:** the run is from before configurable roles. Say exactly "ERROR OLD_FORMAT_RUN remove <RUN_DIR> and start a new run" and stop.
2. Load Orca as in Step 1 item 2, then run `ORCA orchestration run-use --id <RUN_ID>`. If it fails, show the user its output and stop.
3. **Check and re-probe the picks.** The run keeps its `ROLE_*` picks; never change one.
   1. Run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <CATALOG>`, then `roles.py check` as in Step 1 item 1, with `--catalog <CATALOG>` and the four `ROLE_*` values. Stop on exit 1.
   2. Unless `STEP` is already `resume probe`, `STATE set RESUME_STEP=<STEP> RESUME_WAITING_ON=<WAITING_ON, or none> STEP="resume probe"`.
   3. Run `PROBE --set resume`. On `PROBED`, `STATE set STEP=<RESUME_STEP> WAITING_ON=<RESUME_WAITING_ON>` (unset `WAITING_ON` if that was `none`), `STATE unset RESUME_STEP RESUME_WAITING_ON`, and go on. On `WAIT <dispatch> probe`, `STATE set WAITING_ON=<dispatch>` and end your turn; when Orca wakes you about it, run `PROBE` and branch again. On `PROBE_FAILED`, do as in Step 1 item 10.
4. Run `ORCA orchestration worker-list --run <RUN_ID>`.
5. Pick up at the first of these that holds:
   - **`OPEN_RELAY` is set:** the relay file is the one its message names. Show it to the user again as in Rule 5, and wait.
   - **`WAITING_ON` is `ARCHITECT_DISPATCH`:** save its settle to `<RUN_DIR>/results/architect-<n>.md` (How you work, "Architect jobs").
     - `SAVED`, routed by `LAST_JOB`: `escalate` or `fix-meta` while `STEP` starts with `run_task` goes to `TASKLOOP --architect-result <that file>`; `final-review`, `verification-failed` or `fix-meta` while `STEP` starts with `run_finish` goes to `FINISH --architect-result <that file>`. Any other job's `RESULT:` line is handled in the step or message that sent it: Step 2's list for design-phase jobs, Step 3 for `baseline-failed` and `fix-meta` there, "Messages" for `consult` and `user-message`, Step 8 for `pr`, `jira-close` and `report`.
     - `NOT_FOUND`: if `worker-show` says it is still working, wait. If it is `exited` or `lost`, follow "Architect lost" (design phase) or "A fresh architect fails". Never run a step's script here: it would print the same job again and start a second architect.
   - **`OPEN_ASK` is set:** send the `consult` job again.
   - **`STEP` is `1 probe` (no `ARCHITECT_DISPATCH` yet) or `3 probe`:** run `PROBE --set step1` or `PROBE --set step3` and branch as in that step.
   - **`STEP` is `3 setup`:** redo Step 3 from item 2, skipping item 3 if `INITIAL_BASE_SHA` is set and item 4 if `IMPL_WORKTREE` is set.
   - **`STEP` is `8 <n>`:** continue Step 8 at item `<n>`.
   - **`STEP` starts with `run_design`, `run_task` or `run_finish`:** run `DESIGN`, `TASKLOOP` or `FINISH` and branch as its step says.
6. Say exactly "Resumed the <TOPIC> run at step <STEP>." and continue.

## State keys

| Keys | Meaning | Set in |
| --- | --- | --- |
| `SKILL_DIR`, `ROLES_FILE`, `SETUP_CATALOG`, `DATE`, `TOPIC`, `REQUIREMENTS_SOURCE`, `JIRA_KEY`, `REQUIREMENTS_FILE`, `EGM_REQUESTED` | From the brief and the request | 1 |
| `ORCA`, `MAIN_CHECKOUT`, `DEFAULT_BRANCH`, `START_SHA`, `START_HOW` | Tools and repo | 1 |
| `DOCS_WORKTREE`, `DOCS_DIR`, `RUN_DIR`, `RELAY_DIR`, `RUN_ID` | The docs worktree and the Orca run | 1 |
| `ROLE_COORDINATOR`, `ROLE_ARCHITECT`, `ROLE_EGM`, `ROLE_BASE`, `BASE_TOP`, `CATALOG` | The picks, the base model's highest effort, and the catalog; fixed for the run | 1 (`roles.py to-state`) |
| `LAUNCH_STYLE_<pair key>` | The launch style a test launch proved for a pair. Never set them | `probe.py` |
| `PHASE` | `design`, `execution`, `finish`, or `done` | 1, 3, 8 |
| `STEP`, `WAITING_ON`, `OPEN_RELAY`, `OPEN_ASK`; `RESUME_STEP`, `RESUME_WAITING_ON` | Where to pick up after a compaction or restart; the saved values during a resume's re-probe | every wait; Resume |
| `ARCHITECT_DISPATCH`, `LAST_JOB`, `LAST_JOB_INPUT`, `JOB_RETRIES` | The current architect and its job | every job |
| `RUN_VALUES`, `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS`, `CLERICAL_PAIR` | From `run-values.txt` | 3 |
| `INITIAL_BASE_SHA`, `LAST_MERGED`, `IMPL_WORKTREE` | Execution | 3; `run_task.py` updates `LAST_MERGED` |
| `META_FAILS`, `BASELINE_FAILS` | Run counters | 3, and the scripts |
| `PROBE_*`; `DESIGN_*`, `DOCS_HEAD`, `EGM_SKILLS`, `EGM_RUNS`, `LENS_RUNS`, `PLAN_REVIEW_RUNS`, `RETRY_<kind>`; `CURRENT_TASK`, `LOOP_<id>`, `LOOP_WAIT` and every key naming a task id (`BASE_SHA_<id>`, `WORKER_HEAD_<id>`, `PAIR_<id>`, `ATTEMPT_<id>`, `REVIEW_STAGE_<id>`, `GATE_FAILED_<id>`, `STAGE_FIX_ROUNDS_<id>_S<n>`, `GATE_FIX_ROUNDS_<id>`, `TOTAL_FIX_ROUNDS_<id>`, ...); `FIN_*`, `FINAL_FIX_ROUNDS`, `VOIDS_FINAL` | The scripts' own state (`probe.py`, `run_design.py`, `run_task.py`, `run_finish.py`; their docstrings list them). Read them for status; never set them | 1–8 |
