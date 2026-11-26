# Capsule Communicator: Resume a run

Part of mission-control. Everything in `<SKILL_DIR>/references/capsule-communicator.md` ("How you work", "Messages", "State keys") and the Rules in `<SKILL_DIR>/skills/mission/SKILL.md` hold here.

Use this when the user asks to continue a run or after a context compaction in the same session (first re-read `<SKILL_DIR>/skills/mission/SKILL.md` — or `<SKILL_DIR>/SKILL.md` in a flat install — `<SKILL_DIR>/references/capsule-communicator.md` and this file in full, then the file of the step you resume at).

1. Find `<RUN_DIR>/state.json` with `python3 -I <SKILL_DIR>/scripts/workspace.py list-runs --main-checkout <the repo>` (single repo) or `--workspace <WORKSPACE_DIR>` (multi-repo); each line is `<topic> <state file>`. Then run `STATE show`.
   - **No state file, or no `ROLE_DIRECTOR` in it:** say exactly "ERROR OLD_FORMAT_RUN remove <RUN_DIR> and start a new run" and stop.
   - **No `MISSION_NOTES` in it:** `STATE set MISSION_NOTES=none`.
   - **No `BASE_BRANCH` in it:** `STATE set BASE_BRANCH=<DEFAULT_BRANCH>`.
   - **No `REPOS` in it:** `STATE set REPOS=none`.
   - **`SKILL_DIR` in it is not the folder you are running from** (the plugin root whose `references/` you are reading; a plugin update moves it): `STATE set SKILL_DIR=<that folder>`, and use that folder as `<SKILL_DIR>` from now on.
   - **Open the observation deck again** ("Observation deck" in `<SKILL_DIR>/references/capsule-communicator.md`): `DECK --link <RUN_DIR>` (with `--pill` on Antigravity), print its line, then `DECK --deck <RUN_DIR>` in the background.
2. **Check and re-probe the picks.** The run keeps its `ROLE_*` picks. If the user asked for other picks, run "Switch picks" (`<SKILL_DIR>/references/capcom/switch.md`) from its item 1 in place of this item; its last item brings you to item 3 here.
   1. Run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <CATALOG>`, where `<CATALOG>` is the state's `CATALOG` (`<RUN_DIR>/catalog.json`, the file the run's workers read), then `roles.py check` as in Step 1 item 7, with `--catalog <CATALOG>`, `--mode <RUN_MODE>` (default `full` when `RUN_MODE` is unset), and the three `ROLE_*` values. On exit 1 with any `MISSING` line, show every `MISSING` and `UNKNOWN` line exactly and stop. On exit 1 with only `UNKNOWN` lines (a pick the catalog no longer has, or, with a harness signed in with OAuth, a run continued from another harness's session), show them exactly, then run "Switch picks" (`<SKILL_DIR>/references/capcom/switch.md`) from its item 1 in place of the rest of this item, asking the roles those lines name.
   2. Run `PROBE --set resume --if-changed`. (Skips re-probing when harness versions in `<CATALOG>` are unchanged and every pair in the resume set already has a recorded `LAUNCH_STYLE_*`; re-probes automatically if harness versions changed or any pair is new.) On `PROBE_FAILED`, do as in Step 1 item 8. On `USAGE_LIMIT <pair> <detail>` (31), show the usage-limit prompt of Step 3 item 7; "retry" runs this `PROBE` again; "switch", "pause" and "stop" are handled as there.
3. Pick up at the first of these that holds:
   - **`OPEN_USAGE_PROMPT` is `yes`:** re-show the Flight Director usage-limit prompt (as in the exit-`31` row) and wait; "switch", "pause" and "stop" are handled as in that row. On "retry", unset `OPEN_USAGE_PROMPT` and re-run `DIRECTOR <LAST_DIRECTOR_OP>` with `LAST_DIRECTOR_ARG` (`--spec <LAST_DIRECTOR_ARG>` for `start` and `send`, `--file <LAST_DIRECTOR_ARG>` for `reply`; using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`, and adding `--keep-open` if `PHASE=design` and `LAST_DIRECTOR_OP=start`), then handle its exit code and route any returned `RESULT <saved>` via the `LAST_JOB` / `STEP` routing rules below.
   - **`OPEN_RELAY` is set:** show `<OPEN_RELAY>` to the user again as in Rule 5, and wait. When the user replies, write their answer to `<RELAY_DIR>/<NNN>-from-user.md`, unset `OPEN_RELAY LAST_DIRECTOR_RESULT`, and run `DIRECTOR reply` using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`; once `DIRECTOR reply` returns `0` (`RESULT <saved>`), route it by `LAST_JOB` and `STEP`:
     - `LAST_JOB=user-message` (expecting `RESULT: NOTED`): continue at `STEP` passing `--docs-moved` on the next `DESIGN`, `TASKLOOP`, `FINISH`, or `PROBE` call;
     - `2 design`: branch per Step 2;
     - `3 setup`: continue Step 3 from the failing item (`baseline-failed` -> item 4; `fix-meta` -> item 6);
     - `run_design ...`: branch on `<saved>` as Step 2 does (only `RESULT: NEED ...` and `RESULT: COMMITTED` go to `DESIGN --result <saved>`; `RESULT: PLAN_APPROVED` goes to Step 3), or pass `--answer <answer_file>` to `DESIGN` when `LAST_JOB=consult`;
     - `run_task ...`: pass `--director-result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `TASKLOOP` and branch per Step 4;
     - `run_finish ...`: pass `--director-result <saved>` (or `--answer <answer_file>` when `LAST_JOB=consult`) to `FINISH` and branch per Step 5;
     - `5 <n>`: continue Step 5 at item `<n>` with `<saved>`.
   - **`LAST_DIRECTOR_RESULT` is set** (an interrupted `run_director.py` call already finished and saved its response):
     - **`RESULT <saved>`** (and `<saved>` exists): `STATE set JOB_RETRIES=0`, `STATE unset LAST_DIRECTOR_RESULT`, and route `<saved>` directly via the `LAST_JOB` / `STEP` rules above without spawning a fallback Flight Director.
     - **`NOTIFY <notify_path> <saved>`** (and `<notify_path>` exists): `STATE unset LAST_DIRECTOR_RESULT`, show `<notify_path>` verbatim, and run `DIRECTOR notify-ack` using `LAST_JOB` and "Flight Director job models" for `<the job's pair>`.
   - **`STEP` is `2 design`:** run "Flight Director lost" to start a fallback design Flight Director (`--job resume`) and resend `LAST_JOB` with `LAST_JOB_INPUT`.
   - **`STEP` is `3 setup`:** redo Step 3 from item 2 (item 3 is safe to repeat), skipping item 4 if `BASELINE_OK` is `yes`.
   - **`STEP` is `5 <n>`:** continue Step 5 at item `<n>`.
   - **`STEP` starts with `run_design`, `run_task` or `run_finish`:** run `DESIGN`, `TASKLOOP` or `FINISH` and branch as its step says.
4. Say exactly "Resumed the <TOPIC> run at step <STEP>." and continue.

