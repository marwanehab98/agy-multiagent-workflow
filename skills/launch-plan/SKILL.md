---
name: launch-plan
description: Use when you already have an implementation plan (a markdown plan file, or tasks/*.meta and run-values.txt) and want to execute it via Antigravity / Gemini headless agy -p workers, gates, two-stage reviews, final verification, and PR or local merge without running draft-spec or design steps.
---

# Execute Plan (Multi-Agent Execution Pipeline)

**After a context compaction** (your earlier conversation is replaced by a summary): before anything else, re-read this file in full, `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install, for all Coordinator Rules 1–21), and `<SKILL_DIR>/references/coordinator.md`, then pick up from `<RUN_DIR>/state.json`. A summary of the rules is not the rules.

## Overview

This skill runs the **execution, review, verification, and finishing stages** (Coordinator Steps 3–8) starting from an existing plan, without running draft-spec or design reviews.

The invoking Antigravity session acts as the **coordinator** and delegates every step to the bundled deterministic scripts (`run_architect.py`, `probe.py`, `run_task.py`, `run_finish.py`), which run synchronous headless `agy -p` workers. All Coordinator Rules (Rules 1–21 in `<SKILL_DIR>/skills/mission/SKILL.md`, or `<SKILL_DIR>/SKILL.md` in a flat install) apply throughout.

`SKILL_DIR` is the plugin/bundle root containing `scripts/` and `references/`:
- In a plugin or repo checkout (`.../skills/launch-plan/SKILL.md`): two directories above this file (the parent of `skills/`).
- In a flat `install.sh --harness` install (`<skills_dir>/launch-plan/SKILL.md`): the sibling `<skills_dir>/mission` folder.

`CFG` is `$HOME/.config/mission-control`, and `CATALOG` is `<CFG>/catalog.json`.

## Start here

- **The user asks to continue or resume an earlier run:** follow "Continue a run" in `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install) -> `<SKILL_DIR>/references/coordinator.md` "Resume a run".
- **Otherwise:** follow Steps 1–4 below in this same session.

### Step 1: Identify the Plan

1. **Locate the plan input** from the user's message:
   - Either a ready task bundle containing `run-values.txt` and `tasks/<id>.{md,meta,-summary.md,-decisions.md}`, OR
   - A markdown implementation plan (e.g., `docs/plans/YYYY-MM-DD-<topic>-plan.md`), optionally with an associated design/spec file.
   - If neither was provided, say exactly `"Give me the path to the plan file (or tasks directory) you want to execute."` and wait.
2. **Set `TOPIC` and `DATE`:** `TOPIC` is a 2–4 word kebab-case slug derived from the plan filename or title; `DATE` is today (`YYYY-MM-DD`).

### Step 2: Setup (Role Picks)

Run the same Setup flow as Setup items 1–5 in `<SKILL_DIR>/skills/mission/SKILL.md` (or `<SKILL_DIR>/SKILL.md` in a flat install), with one change:
- In Setup item 4 (`roles.py check`), pass `--mode launch-plan`:
  `python3 -I <SKILL_DIR>/scripts/roles.py check --mode launch-plan --catalog <CATALOG> --jira no --architect <pair> --egm <pair> --base <pair>`

### Step 3: Initialize Docs Worktree & State

Read `<SKILL_DIR>/references/coordinator.md` in full, then run Step 1 items 2–8 with these initial values:
- `REQUIREMENTS_SOURCE=written`
- `JIRA_KEY=none`
- `REQUIREMENTS_FILE=none`
- `RUN_NOTES=none`
- `EGM_REQUESTED=no`
- `ROLES_FILE=<CFG>/roles.json`, `SETUP_CATALOG=<CATALOG>`, `AGY=agy` (or `command -v agy`)
- In item 6, set `PHASE=design` and `RUN_MODE=launch-plan` (Step 4 updates `PHASE` to `execution` once plan artifacts are ready).
- In item 7, pass `--mode launch-plan` to `roles.py check`, then run `roles.py to-state` as normal.
- In item 8, run `PROBE --set step1` as normal.

### Step 4: Prepare Plan Artifacts and Enter Execution

Branch based on what the user provided:

- **Case A — Ready plan bundle** (`run-values.txt` + `tasks/<id>.{md,meta,-summary.md,-decisions.md}` + design file already exist):
  1. Copy the design file, plan file, `DECISIONS_LOG` (create `<DOCS_DIR>/<DATE>-<TOPIC>-decisions.md` with `"none"` if none exists), `run-values.txt`, and `tasks/` into `<DOCS_DIR>`.
  2. Ensure `DESIGN_FILE` and `DECISIONS_LOG` inside `<DOCS_DIR>/run-values.txt` point to their absolute paths inside `<DOCS_DIR>`, and ensure `DESIGN_FILE` lists acceptance criteria (`AC-1`, `AC-2`, ...) matching task `ACS` (or if `run-values.txt` lacks `DRIFT_CHECK`, ask the user once whether to run the end-of-run drift check and set `DRIFT_CHECK=yes|no`).
  3. Commit those files in `<DOCS_WORKTREE>` (`git -C <DOCS_WORKTREE> add ... && git -C <DOCS_WORKTREE> commit ...`).
  4. Set `STATE set PHASE=execution STEP="3 setup" WAITING_ON=` and jump straight to `<SKILL_DIR>/references/coordinator.md` **Step 3** (starting at item 2).

- **Case B — Standalone markdown plan only** (no `tasks/*.meta` or `run-values.txt` yet):
  1. Copy the plan file to `<RUN_DIR>/plan-input.md` (never into `<DOCS_DIR>` before starting, so tree checks stay clean). If a linked or separate design/spec doc was named, copy it to `<RUN_DIR>/design-input.md` and pass `INPUT="<RUN_DIR>/plan-input.md <RUN_DIR>/design-input.md"`; otherwise `INPUT="<RUN_DIR>/plan-input.md"`.
  2. Build `architect-start.txt` with `FIRST_JOB=prepare-plan`, `INPUT=<that input>`, and all run values it names.
  3. `STATE set PHASE=design LAST_JOB=prepare-plan LAST_JOB_INPUT=<input> STEP="2 design" WAITING_ON=architect`, and run `ARCH start --state <RUN_DIR>/state.json --spec <spec> --pair <ROLE_ARCHITECT> --keep-open`.
  4. Handle `run_architect.py` exits using `<SKILL_DIR>/references/coordinator.md` ("Running the architect": `11` RELAY, `12` NOTIFY, `31` USAGE_LIMIT, `1`/`13` Architect lost -> `FIRST_JOB=resume`).
  5. When `ARCH` returns `0` (`RESULT <saved>`) with `RESULT: PLAN_APPROVED <run-values>`, continue straight to `<SKILL_DIR>/references/coordinator.md` **Step 3** (item 1 unsets `ARCHITECT_CONVERSATION_ID` and `ARCHITECT_KEEP_OPEN` and proceeds through Steps 3, 4, and 8).
