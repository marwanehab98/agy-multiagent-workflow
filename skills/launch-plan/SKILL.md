---
name: launch-plan
description: Use when the user has an implementation plan (a markdown plan, or tasks/*.meta plus flight-manifest.txt) to execute with headless Antigravity / Gemini agy -p or Claude Code claude -p workers — gates, two-stage reviews, verification, PR or local merge — skipping the design steps.
---

# Launch Plan

**After a context compaction** (your earlier conversation is replaced by a summary): before anything else, re-read this file in full, `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install, for all Capsule Communicator Rules 1–21), `<SKILL_DIR>/references/capsule-communicator.md`, and the file of the step in `<RUN_DIR>/state.json` (`STEP`), then pick up from there. A summary of the rules is not the rules.

## Overview

This skill runs the **execution, review, verification, and finishing stages** (Capsule Communicator Steps 3–5) starting from an existing plan, without running mission-brief or design reviews.

The invoking session acts as the **Capsule Communicator** and delegates every step to the bundled deterministic scripts (`run_director.py`, `probe.py`, `run_task.py`, `run_finish.py`), which run synchronous headless CLI workers. All Capsule Communicator Rules (Rules 1–21 in `<SKILL_DIR>/skills/mission/SKILL.md`, or `<SKILL_DIR>/SKILL.md` in a flat install) apply throughout.

`SKILL_DIR` is the plugin/bundle root containing `scripts/` and `references/`:
- In a plugin or repo checkout (`.../skills/launch-plan/SKILL.md`): two directories above this file (the parent of `skills/`).
- In a flat `install.sh --harness` install (`<skills_dir>/launch-plan/SKILL.md`): the sibling `<skills_dir>/mission` folder.

`CFG` is `$HOME/.config/mission-control`, and `CATALOG` is `<CFG>/catalog.json`.

## Start here

- **The user asks to continue or resume an earlier run:** follow "Continue a run" in `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install) -> "Resume a run" (`<SKILL_DIR>/references/capcom/resume.md`).
- **Otherwise:** follow Steps 1–4 below in this same session.

### Step 1: Identify the Plan

1. **Locate the plan input** from the user's message:
   - Either a ready task bundle containing `flight-manifest.txt` and `tasks/<id>.{md,meta,-summary.md,-decisions.md}`, OR
   - A markdown implementation plan (e.g., `docs/plans/YYYY-MM-DD-<topic>-flight-plan.md`), optionally with an associated design/spec file.
   - If neither was provided, say exactly `"Give me the path to the plan file (or tasks directory) you want to execute."` and wait.
2. **Set `TOPIC` and `DATE`:** `TOPIC` is a 2–4 word kebab-case slug derived from the plan filename or title; `DATE` is today (`YYYY-MM-DD`).

### Step 2: Setup (Role Picks)

Run the same Setup flow as Setup items 1–5 in `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install), with one change:
- In Setup item 4 (`roles.py check`), pass `--mode launch-plan`:
  `python3 -I <SKILL_DIR>/scripts/roles.py check --mode launch-plan --catalog <CATALOG> --jira no --director <pair> --safety <pair> --crew <pair>`

### Step 3: Initialize Docs Worktree & State

Read `<SKILL_DIR>/references/capsule-communicator.md` and `<SKILL_DIR>/references/capcom/step-1-intake.md` in full, then run Step 1 items 2–9 with these initial values:
- `REQUIREMENTS_SOURCE=written`
- `JIRA_KEY=none`
- `REQUIREMENTS_FILE=none`
- `SAFETY_REQUESTED=no`
- Mission notes as in Step 1 item 1 (the sentences of the request about how to run the work, copied exactly); item 9 saves them to `MISSION_NOTES`, or sets `MISSION_NOTES=none` when there are none.
- `ROLES_FILE=<CFG>/roles.json`, `SETUP_CATALOG=<CATALOG>` (harness binaries such as `AGY` are resolved automatically by `scripts/harnesses.py` unless explicitly overridden)
- In item 6, set `PHASE=design` and `RUN_MODE=launch-plan` (Step 4 updates `PHASE` to `execution` once plan artifacts are ready).
- In item 7, pass `--mode launch-plan` to `roles.py check`, then run `roles.py to-state` as normal.
- In item 8, run `PROBE --set step1` as normal.

### Step 4: Prepare Plan Artifacts and Enter Execution

You never copy, edit or commit plan files yourself (Rule 4): the Flight Director prepares them in `<DOCS_DIR>`.

1. **Copy the user's plan into `<RUN_DIR>`** (never into `<DOCS_DIR>`, so tree checks stay clean), and set `INPUT`:
   - **A ready bundle** (`flight-manifest.txt` and `tasks/<id>.{md,meta,-summary.md,-decisions.md}`, with its design file): `cp -R` the bundle folder to `<RUN_DIR>/plan-input/`, plus the design and any decisions log into that folder if they live elsewhere. `INPUT="bundle <RUN_DIR>/plan-input"`.
   - **A markdown plan:** copy it to `<RUN_DIR>/plan-input.md`, and any design/spec doc the user named to `<RUN_DIR>/design-input.md`. `INPUT="<RUN_DIR>/plan-input.md"`, plus `" <RUN_DIR>/design-input.md"` when there is one.
2. Build `director-start.txt` with `FIRST_JOB=prepare-plan`, `INPUT=<that input>`, and all run values it names.
3. `STATE set PHASE=design LAST_JOB=prepare-plan LAST_JOB_INPUT=<input> STEP="2 design" WAITING_ON=director`, and run `DIRECTOR start --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_DIRECTOR> --keep-open`.
4. Handle `run_director.py` exits using `<SKILL_DIR>/references/capsule-communicator.md` ("Running the Flight Director": `11` RELAY, `12` NOTIFY, `31` USAGE_LIMIT, `1`/`13` Flight Director lost -> `FIRST_JOB=resume`).
5. When `DIRECTOR` returns `0` (`RESULT <saved>`) with `RESULT: PLAN_APPROVED <flight-manifest>`, continue straight to **Step 3** (`<SKILL_DIR>/references/capcom/step-3-setup.md`; item 1 unsets `DIRECTOR_CONVERSATION_ID` and `DIRECTOR_KEEP_OPEN` and proceeds through Steps 3, 4, and 5).
