# Capsule Communicator: the whole run

Part of mission-control. The Rules in `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install) hold throughout.

## How you work

- **You decide nothing yourself.** Every step says what to run and how to branch, and you branch only on a script's exit code and last line, the Flight Director's first line (`RESULT: ...`), or a counter from the state file. Every question goes to the Flight Director, through a job or a relay.
- **The state file holds every value** (Rule 18). `STATE` below means `python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json`.
  - Record a value the moment you learn it: `STATE set KEY=value`. Read it when a command needs it: `STATE get KEY`. Counters: `STATE incr KEY` prints the new count; a missing counter is 0.
  - **Before every turn you end while waiting on the user,** set `STEP` (where to pick up, e.g. `2 design` or `3 setup`) and `WAITING_ON=user`.
- **Script shorthands:** `DESIGN`, `TASKLOOP`, `FINISH` and `PROBE` mean `python3 -I <SKILL_DIR>/scripts/<run_design|run_task|run_finish|probe>.py --state <RUN_DIR>/state.json advance`. They run headless CLI workers synchronously and print any events (`MERGED ...`) followed by their status as the last line. An `ERROR <what>` line (exit 2) from any of them: show the user the line exactly and stop. `ERROR RUN_BUSY` means one of them is already running on this run: never start a second one or stop the first, whatever the user asks. Say exactly "A script is still running on this run; I'll continue when it finishes. Live progress: `<RUN_DIR>/progress.log`.", watch that call with `WATCH` as below until one prints `ENDED`, then run your command again.
- **Long calls run in the background, and `WATCH` brings you their news.** `DESIGN`, `TASKLOOP`, `FINISH`, `PROBE` and `DIRECTOR` can each run for far longer than a shell tool allows in the foreground (one worker may take hours; `TASKLOOP` runs several in a row). Always start them in the background: your shell tool's background option (for example `run_in_background`), or a command that turns asynchronous after a short wait. Right after, start `WATCH` (`python3 -I <SKILL_DIR>/scripts/watch.py --state <RUN_DIR>/state.json`) in the background the same way, and end your turn. Your harness wakes you when a background command finishes, and `WATCH` finishes when a task merges, when something fails, when ten minutes pass, or when the call ends. Each time `WATCH` finishes, read its output. Every line before its last is a note: print those lines exactly, as one block, with nothing added. The notes are Markdown (a bold header, then bullets): print them as plain message text, never inside a code fence. Its last line says what to do:
  - `RUNNING`: the call is still running. Start `WATCH` in the background again and end your turn.
  - `ENDED`: the call has finished. Read the call's own output and act on its status line.
  - `WATCHING`: another `WATCH` is already running on this run. Do nothing.

  More on this:
  - **A running call always has a `WATCH` running next to it.** Your harness may say it will tell you when the call finishes, so there is nothing to check. That is no reason to skip `WATCH`: `TASKLOOP` finishes only after every task in its queue, and without `WATCH` the user hears nothing until then. If you are not sure a `WATCH` is running (for example after you answered a user message), start one: a second one prints `WATCHING` and does no harm.
  - **Act on each call's status line once.** The call's own finish wakes you too, just before or after `WATCH` prints `ENDED`. Whichever comes first, act on it; when the other comes, print any notes it has and do nothing more.
  - Never run `WATCH` in the foreground, and never sleep or poll the call's output instead of it.
  - If a call ended without a status line, run the same command again: the scripts resume where they stopped and stop any worker the cut-off call left running.
- **Progress.** Every script appends what happens to `<RUN_DIR>/progress.log`: `[progress]` milestones (task started, gate result, reviewer verdict, merge, escalation) and one line per worker action (tool calls, what it says, failed tool calls, to-do updates, and at the end its cost and turns when the harness reports them). `WATCH` shows the milestones, and after ten quiet minutes the latest line of the worker that is running; everything else stays in the file. Full per-worker logs are in `<RUN_DIR>/logs/`. Before the first `TASKLOOP` of a run, say exactly once: "Live progress: `<RUN_DIR>/progress.log` (worker logs in `<RUN_DIR>/logs/`)."
- **Observation deck.** `DECK` means `python3 -I <SKILL_DIR>/animation/play.py`. It shows the run as a live animation in the user's browser. It only reads `<RUN_DIR>/progress.log`, and nothing in the run depends on it. As soon as `RUN_DIR` is recorded in Step 1, and again each time you resume a run, run `DECK --link <RUN_DIR>` (on Antigravity: `DECK --link <RUN_DIR> --pill`, which links to the plugin's side pane when Antigravity has it open) and print the one line it prints, exactly as it prints it (a Markdown link named "Observation deck"). Then start `DECK --deck <RUN_DIR>` in the background, the way a long call is started, and go straight on with your step.
  - The deck has no news and no status line. Never start `WATCH` for it, never wait for it, and never read its output.
  - It runs until the session ends, and closes itself once the run is finished and nobody is watching, or when the run's folder is removed. If your harness tells you it ended, do nothing and say nothing: it is not a call, and its end is not a reason to act.
  - If `DECK --link` exits other than 0, or prints anything but one line starting `[Observation deck](`, print nothing, skip `DECK --deck`, and go on. The run never waits on the deck.
- **You never write or build spec text.** The scripts build every worker spec from the templates in `<SKILL_DIR>/references/templates/`: `DIRECTOR start|send --job <JOB> --input <input>` builds the Flight Director's from the state, and `DESIGN`, `TASKLOOP`, `FINISH` and `PROBE` build their workers'. Never run `build_spec.py` or edit a template yourself.
- **You never retype a worker's message.** `run_director.py` saves every Flight Director response verbatim to `<RUN_DIR>/results/director-{n}.md`. The loop scripts save their own workers' outputs and asks. Only the user's own replies are written by hand.
- **File numbers:** `{n}` in a path you pass to `gate.py` is filled in by the script with the next free number; use the path its output names (`log=<path>`). Elsewhere, `<n>` or `<NNN>` in a file name is the next free number in that folder. The exception is `<NNN>-from-user.md`, which takes the number of the `<NNN>-to-user.md` it answers. All paths you pass to scripts are absolute.
- **Fixed prompts:** some steps stop with a fixed prompt and list the replies they accept. While one is open, a user message that isn't one of those replies and doesn't start with `capcom:` gets the same prompt again, followed by "Reply with exactly one of: <the accepted replies>." Don't send it to the Flight Director.
- **What you print** (Rule 14): relays (Rule 5; one ending in a choice block is a dialog, never printed text), the fixed lines the steps give, the note lines `WATCH` prints, exactly as it prints them, and these status lines: `Running <DESIGN|TASKLOOP|FINISH|PROBE>...` when a call starts, `Merged task <id> at <head>.` for each `MERGED` event, and the one line `DECK --link` prints. Nothing else; never summarize, reword, or comment on a relay or a progress line.
- **Tell the user when the run needs them.** Whenever you end a turn waiting on the user (a relay, a fixed prompt, a pause) and one of your tools sends the user a notification (a push or desktop notification), send one: "<TOPIC>: the run is waiting for you." Never otherwise.
- **Let the user open the run's documents.** The docs worktree is outside the user's checkout. Relays that ask for a review end with a `DOCS:` block of links into it. Before showing the first such relay of a run, if one of your tools gives the user's app access to a folder outside your working directory (a directory-access request), use it once for `<DOCS_WORKTREE>` and `STATE set DOCS_ACCESS=yes`; skip this when `DOCS_ACCESS` is `yes` or you have no such tool.
- **Keep a mission log:** append one line per event to `<RUN_DIR>/mission-log.md`, `<time> <event>` for what you do yourself: the Flight Directors you start, merges you see, the PR URL. The scripts log their own workers.
- **Running the Flight Director** (`run_director.py`, Rule 15). `DIRECTOR` below means `python3 -I <SKILL_DIR>/scripts/run_director.py`. "Send job `<JOB>` with `INPUT=<input>`" means the command below for the phase. Pass `<input>` as one argument, in single quotes (`--input '<input>'`): it may contain spaces. `run_director.py` builds the spec and sets `LAST_JOB=<JOB>`, `LAST_JOB_INPUT=<input>` and `WAITING_ON=director` itself. Record the event in `mission-log.md`.
  - **Design phase** (`PHASE=design`): `DIRECTOR send --state <RUN_DIR>/state.json --job <JOB> --input '<input>' --pair <ROLE_DIRECTOR>`. (The first job uses `DIRECTOR start ... --keep-open` instead of `send`.)
  - **After plan approval:** `DIRECTOR start --state <RUN_DIR>/state.json --job <JOB> --input '<input>' --pair <the job's pair>`.
  - **Handle `run_director.py` exit codes:**

    | Exit (last line) | Do |
    | --- | --- |
    | `0` (`RESULT <saved>`) | `STATE set JOB_RETRIES=0`, `STATE unset OPEN_RELAY OPEN_USAGE_PROMPT LAST_DIRECTOR_OP LAST_DIRECTOR_ARG LAST_DIRECTOR_RESULT`, and branch on `<saved>`'s first line as the calling step specifies. |
    | `11` (`RELAY <relay_path> <saved>`) | `STATE set OPEN_RELAY=<relay_path> WAITING_ON=user`, `STATE unset OPEN_USAGE_PROMPT LAST_DIRECTOR_OP LAST_DIRECTOR_ARG`, relay `<relay_path>` verbatim (Rule 5), and end your turn. When the user replies, write their answer verbatim to `<RELAY_DIR>/<NNN>-from-user.md`, `STATE unset OPEN_RELAY LAST_DIRECTOR_RESULT`, `STATE set WAITING_ON=director`, and run `DIRECTOR reply --state <RUN_DIR>/state.json --file <from-user> --pair <the job's pair>`, then handle its exit code using this table. |
    | `12` (`NOTIFY <notify_path> <saved>`) | `STATE unset LAST_DIRECTOR_RESULT`, show `<notify_path>` to the user verbatim (Rule 5), then immediately run `DIRECTOR notify-ack --state <RUN_DIR>/state.json --pair <the job's pair>` and handle its exit code using this table. |
    | `13` (`LOST <detail>`) | In `PHASE=design`, follow "Flight Director lost". After plan approval, follow "A fresh Flight Director fails". |
    | `31` (`USAGE_LIMIT <detail>`) | `STATE set OPEN_USAGE_PROMPT=yes WAITING_ON=user LAST_DIRECTOR_OP=<start|send|reply|notify-ack> LAST_DIRECTOR_ARG=<file, or empty>` (for `start` and `send`, the file is the spec the call built: `STATE get LAST_DIRECTOR_SPEC`), then say exactly "The Flight Director hit its model's usage limit: <detail>\nReply \"retry\" once the limit has reset, \"pause\" to leave the run exactly here and come back later, or \"stop\" to end the run now (tasks not merged yet are not built)." This is a fixed prompt. "retry" unsets `OPEN_USAGE_PROMPT` and retries the same `DIRECTOR` command; "pause" changes no state: say exactly "Paused. The run keeps its place: when the limit has reset, say \"continue my mission run for <TOPIC>\" and reply \"retry\"." and end your turn; "stop" goes to Step 5 item 7. |
    | `1` (`FAILED <saved>`) | Follow "Flight Director lost" (design phase) or "A fresh Flight Director fails" (after plan approval). |
    | `2` (`ERROR <what>`) | Show the user the line exactly and stop. |

- **Flight Director job models** (after plan approval):

  | Job | Pair |
  | --- | --- |
  | `consult`, `escalate`, `final-review`, `verification-failed`, `drift-done`, `baseline-failed`, `user-message` | `ROLE_DIRECTOR` |
  | `fix-meta`, `pr`, `jira-close`, `report` | `GROUND_CREW_PAIR` once Step 3 item 7 has printed `PROBED`; `ROLE_DIRECTOR` before that |

- **Workers by role.** You run only the Flight Director through `run_director.py`; the loop scripts run every other worker synchronously on its pair:

  | Worker | Pair | Started by |
  | --- | --- | --- |
  | Design Flight Director (and fallback); fresh Flight Directors after plan approval | `ROLE_DIRECTOR`; "Flight Director job models" | you (`run_director.py`) |
  | Test launch | each pair being tested | `probe.py` |
  | Preflight check; Range Safety lens pass, design review, plan review | the pair in the `NEED preflight-check` line; `ROLE_SAFETY` | `run_design.py` |
  | Crew, escalation attempt, Stage 1 and Stage 2 reviewers | the task's `.meta` and the computed ladder | `run_task.py` |
  | Final reviewer; drift check | `ROLE_DIRECTOR`; `ROLE_SAFETY` | `run_finish.py` |

## Steps

Each step is in its own file under `<SKILL_DIR>/references/capcom/`. Read a step's file in full when you enter that step, before acting on any line of it. After a context compaction, re-read this file and the current step's file (`STEP` in the state file says which). A step says "go to Step N" or "Step N item M" to move on: read that step's file first.

| Step | File | Entered |
| --- | --- | --- |
| 1 Intake | `capcom/step-1-intake.md` | a new run |
| 2 Design phase | `capcom/step-2-design.md` | the Flight Director's first `RESULT` of a new run |
| 3 Set up execution | `capcom/step-3-setup.md` | `RESULT: PLAN_APPROVED` (or `launch-plan` Step 4) |
| 4 Run the task loop | `capcom/step-4-tasks.md` | Step 3 item 8, and `RUN_QUEUE` from Step 5 |
| 5 Finish | `capcom/step-5-finish.md` | `QUEUE_EMPTY` from Step 4 |
| Resume a run | `capcom/resume.md` | the user asks to continue a run, or after a context compaction |

## Messages (any step)

- **A user message:**
  - **Starting with `capcom:`:** it is for you. "status" means show the last 10 mission-log lines. "use the fallback" means "Flight Director lost" below. "stop" goes to Step 5 item 7. Anything else, including a request to change a pick, a model or a `.meta`: say exactly "I only understand: status, use the fallback, stop." and change nothing.
  - **Otherwise, with a relay open (`OPEN_RELAY` is set):** it is the answer. Write it, exactly as typed, to `<RELAY_DIR>/<NNN>-from-user.md`, `STATE unset OPEN_RELAY LAST_DIRECTOR_RESULT`, `STATE set WAITING_ON=director`, and run `DIRECTOR reply --state <RUN_DIR>/state.json --file <from-user> --pair <the job's pair>` (using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`).
  - **Otherwise, with `OPEN_USAGE_PROMPT` set to `yes`:** it answers the Flight Director usage-limit prompt ("retry" unsets `OPEN_USAGE_PROMPT` and retries `DIRECTOR <LAST_DIRECTOR_OP>` with `LAST_DIRECTOR_ARG` (`--spec <LAST_DIRECTOR_ARG>` for `start` and `send`, `--file <LAST_DIRECTOR_ARG>` for `reply`); "pause" is handled as in the exit-`31` row; "stop" goes to Step 5 item 7).
  - **Otherwise, with a fixed prompt open:** follow "Fixed prompts" in "How you work".
  - **Otherwise:** write it to `<RELAY_DIR>/<NNN>-from-user.md` and send job `user-message` with `INPUT=<that path>`. When it finishes with `RESULT: NOTED`, pass `--docs-moved` on the next call to `DESIGN`, `TASKLOOP`, `FINISH`, or `PROBE`.
- **A fresh Flight Director fails** (after plan approval): `run_director.py` exits `1`, `13`, or returns an unexpected line. `STATE incr JOB_RETRIES`. At 1, start the same job again from `LAST_JOB` and `LAST_JOB_INPUT`. At 2, say exactly "The Flight Director failed twice on job <LAST_JOB>. Reply \"retry\" to try again, or \"stop\" to stop the run." This is a fixed prompt: "retry" resets `JOB_RETRIES` to 0 and starts the job again; "stop" goes to Step 5 item 7. Reset `JOB_RETRIES` to 0 after every job that settles with a `RESULT:` line.
- **Flight Director lost** (design phase only): `run_director.py` exits `1` or `13`, or the user says "use the fallback".
  Run `DIRECTOR start --state <RUN_DIR>/state.json --job resume --input none --pair <ROLE_DIRECTOR> --keep-open` (it leaves `LAST_JOB` and `LAST_JOB_INPUT` as they are). After `RESULT: RESUMED`, resend `LAST_JOB` with `LAST_JOB_INPUT`, if it had no `RESULT`. Any relay the lost Flight Director had open is dead: the new Flight Director asks again if it still needs the answer, and a user's answer to it anyway goes as a `user-message` job.

## State keys

| Keys | Meaning | Set in |
| --- | --- | --- |
| `SKILL_DIR`, `ROLES_FILE`, `SETUP_CATALOG`, `DATE`, `TOPIC`, `REQUIREMENTS_SOURCE`, `JIRA_KEY`, `REQUIREMENTS_FILE`, `MISSION_NOTES`, `SAFETY_REQUESTED`, `RUN_MODE`, plus optional harness binary overrides (e.g., `AGY`) | From Setup and the request; harness binary override keys are optional; `run_design.py` sets `SAFETY_REQUESTED=yes` when the user chooses Range Safety review later; `launch-plan` sets `RUN_MODE=launch-plan` (default `full`) | 1 |
| `REPOS`, `WORKSPACE_DIR` (multi-repo), `MAIN_CHECKOUT` (single-repo), `DEFAULT_BRANCH`, `BASE_BRANCH`, `START_SHA`, `START_HOW`, and per-repo `REPO_PATH_<k>`, `DEFAULT_BRANCH_<k>`, `BASE_BRANCH_<k>`, `START_SHA_<k>`, `START_HOW_<k>` | Repo(s) | 1 |
| `DOCS_WORKTREE`, `DOCS_DIR`, `RUN_DIR`, `RELAY_DIR`, and per-repo `DOCS_WT_<k>` | The docs worktree / workspace | 1 |
| `ROLE_DIRECTOR`, `ROLE_SAFETY`, `ROLE_CREW`, `CREW_TOP`, `CATALOG` | The picks, the Crew model's highest effort, and the catalog; fixed for the run | 1 (`roles.py to-state`) |
| `LAUNCH_STYLE_<pair key>`, `PROBE_HARNESS_VERSIONS` | The launch style a test launch proved for a pair and the harness versions probed. Never set them | `probe.py` |
| `PHASE` | `design`, `execution`, `finish`, or `done` | 1, 3, 5 |
| `STEP`, `WAITING_ON`, `OPEN_RELAY`, `OPEN_USAGE_PROMPT`, `LAST_DIRECTOR_OP`, `LAST_DIRECTOR_ARG` | Where to pick up after a compaction or restart | every wait |
| `DIRECTOR_CONVERSATION_ID`, `DIRECTOR_KEEP_OPEN`, `LAST_JOB`, `LAST_JOB_INPUT`, `LAST_DIRECTOR_SPEC`, `LAST_DIRECTOR_RESULT`, `JOB_RETRIES` | The current Flight Director conversation, its job, and its settled result | `run_director.py` |
| `FLIGHT_MANIFEST`, `FEATURE_BRANCH`, `CONVENTION_PATHS`, `PR_TARGET`, `DESIGN_FILE`, `DECISIONS_LOG`, `TASKS`, `GROUND_CREW_PAIR`, `BASELINE_SKIP`, `DRIFT_CHECK` | From `flight-manifest.txt` | 3 |
| `INITIAL_BASE_SHA`, `LAST_MERGED`, `IMPL_WORKTREE` | Execution | 3 (`workspace.py init-impl`); `run_task.py` updates `LAST_MERGED` |
| `BASE_DRIFT_OK` | The target-moved check before landing was passed or accepted | 5 |
| `DOCS_ACCESS` | The user's app was given access to the docs worktree | first relay with a `DOCS:` block |
| `WORKER_PID`, `WORKER_EXE` | The worker a script is running now; the next call stops it if its script was killed. Never set them | the scripts |
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
| "The script is taking long; I'll start it again to speed things up." | `ERROR RUN_BUSY`: only one script runs on a run. Keep a `WATCH` running until one prints `ENDED`. |
| "The call is in the background and my harness will tell me when it finishes; I'll skip `WATCH` and wait." | That wake-up comes only when the whole call is done, and `TASKLOOP` runs every task first. Start `WATCH` in the background after every long call and after every `RUNNING`. |
| "`WATCH` printed five lines; I'll tell the user what they mean in a sentence." | "What you print". Print the lines exactly, as one block, with nothing added. |
| "The target branch moved; I'll rebase the feature branch so the PR is clean." | Rule 13. Show `base-drift.sh`'s output and the fixed prompt. |
| "The user's deadline justifies skipping a gate." | Never. Keep the gates. The user can ask `capcom: status`; anything more they need to hear comes from the Flight Director. |
| "The user said 'just rebase it'; that's authorization." | Rule 13. At a fixed prompt, only its listed replies count. Show the prompt again. |
