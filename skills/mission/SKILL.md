---
name: mission
description: Use when the user hands over a JIRA key or hand-written requirements to be taken through the full end-to-end workflow to a reviewed PR, asks to run work through Antigravity or Gemini headless agy workers, asks to continue an earlier run of this workflow, or names agy-multiagent-workflow. For individual stages (draft-spec, flight-plan, launch-plan, gauntlet, etc.), invoke those bundled skills directly.
---

# Agy multi-agent workflow

**After a context compaction** (your earlier conversation is replaced by a summary): before anything else, re-read this file in full and `<SKILL_DIR>/references/coordinator.md`, then pick up from the run's state file. A summary of the rules is not the rules.

## Overview

This skill takes a JIRA story, or requirements the user wrote by hand, to a reviewed PR. The user picks a model and effort for three worker roles; every other worker follows from those picks. The invoking Antigravity session runs the whole run as the **coordinator**—a traffic cop that runs the bundled scripts, dispatches headless `agy -p` workers through those scripts, relays messages unchanged, and merges atomically. It does no design, code, or judgment work itself. Each kind of thinking goes to the worker suited to it:

- the **architect** designs, plans, answers every question the run raises, and writes everything the user reads. One architect conversation stays open through the design phase (via `agy -p --conversation`); after plan approval, each job gets a fresh `agy -p` architect that works from the run's documents;
- **EGM** workers review the design and the plan;
- **implementers** write the code, starting on the base pick (reused across fix/commit rounds via `--conversation`, with automatic fallback to a fresh `agy -p` run);
- **reviewers**, chosen by the architect, check each task.

A pair is written `<harness>:<model>@<effort>`. Only `scripts/harnesses.py` knows how `agy` names its models and efforts.

## Start here

The session that loads this skill **is the coordinator**:

- **The user asks to continue or resume an earlier run:** go to "Continue a run" below.
- **User only wants one stage** (e.g. only brainstorming, writing a plan, running a review, or executing an existing plan): use the dedicated bundled skill (`draft-spec`, `flight-plan`, `launch-plan`, `crucible`, `gauntlet`, `crossfire`, `spec-drift`, `red-green`, `gatecheck`, `touchdown`) directly.
- **Otherwise:** run Setup below, then go straight to `references/coordinator.md` Step 1 in this same session.

`SKILL_DIR` is the plugin/bundle root containing `scripts/` and `references/` (two directories above this `SKILL.md` in a plugin or repo checkout, or this skill's folder in a flat `install.sh --harness` install). `CFG` is `$HOME/.config/agy-multiagent-workflow`, and `CATALOG` is `<CFG>/catalog.json`.

### Setup

1. **Detect:** run `mkdir -p <CFG>`, then `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <CATALOG>`.
2. **Remembered picks:** run `python3 -I <SKILL_DIR>/scripts/roles.py remembered --roles <CFG>/roles.json --catalog <CATALOG>`.
   - **`AVAILABLE yes`:** show the three `role=pair` lines in one block, then ask "Use these picks again?" as in "Asking" below, with the options `same` and `new`. This is a fixed prompt. "same" goes to item 4 with those picks; "new" goes to item 3.
   - **`AVAILABLE no <role> <pair>`** (one line per vanished pick): for each line, say exactly "Your last <role> pick, <pair>, isn't available any more, so pick the <role> again." and ask that role as in item 3. Keep the remembered picks that are still available, and go to item 4. Never choose a replacement yourself, however close a model looks or whatever the user delegates to you: only a row the user picks from the menu counts.
   - **`NONE`:** go to item 3.
3. **Ask the three roles,** in this order: architect, EGM, base implementer. For each, ask three questions in turn, as in "Asking" below. `MENU` means `python3 -I <SKILL_DIR>/scripts/roles.py menu --catalog <CATALOG>`:
   1. harness: `MENU`;
   2. model: `MENU --harness <harness>`;
   3. effort: `MENU --harness <harness> --model <model>`.

   Each menu line is `N<TAB>value<TAB>label`; show any `BROKEN` lines exactly before the question. The question is "<role>: which <harness|model|effort>?", and the rows are its options, labelled with each row's label, in menu order. A menu with exactly one row isn't asked: take that row and say "<role> <harness|model|effort>: <label>, the only option." The pick is `<harness>:<model>@<effort>`, using each chosen row's value. When asking for `base`, note that its `<model>@<effort>` must differ from the `architect` pick so escalation has a higher rung. If the user's opening prompt already named explicit model/effort picks that unambiguously match detected menu rows for all three roles, show those matched `role=pair` lines and confirm them in one shot (`same` / `new`) instead of asking 9 separate questions; never invent or substitute a row the user didn't name.

   **Asking.** With a multiple-choice question tool (such as `ask_question`), ask with it: no option marked recommended, and the options set by the number of rows (not counting the free-text choice the tool adds):
   - **2–4 rows:** one question, one option per row. Never a `More` option.
   - **5 or more rows:** pages. The first page is rows 1–3 plus an option `More`, which asks again with the remaining rows the same way, until 4 or fewer remain; those form the last page.

   Without such a tool, write the question, then one line per row, `N. label`. The answer is the row the user selected, or a typed reply that is exactly a row's number, value or label; anything else, including free text that names no row, gets the same question again.
4. **Check the picks:** run `python3 -I <SKILL_DIR>/scripts/roles.py check --catalog <CATALOG> --jira <yes if the request has a JIRA key, else no> --architect <pair> --egm <pair> --base <pair>`.
   - **Exit 0:** show any `NOTE` line exactly, and go on.
   - **Exit 1 with any `MISSING` line:** show every `MISSING` and `UNKNOWN` line exactly, then say exactly "The run can't start until these are fixed. Install what's missing, or choose different picks, then ask me again." Then stop: don't save, and don't start Step 1 (Rule 21).
   - **Exit 1 with only `UNKNOWN` lines:** show them exactly with a one-line plain-English explanation (e.g. `base` cannot match the `architect` pick), ask each named role again as in item 3, and repeat this item.
5. **Save:** run `python3 -I <SKILL_DIR>/scripts/roles.py save --roles <CFG>/roles.json --architect <pair> --egm <pair> --base <pair>`.
6. **Begin coordination in this session:** read `<SKILL_DIR>/references/coordinator.md` in full and execute it from Step 1, using `ROLES_FILE=<CFG>/roles.json`, `SETUP_CATALOG=<CATALOG>`, and the user's original request verbatim.

### Continue a run

1. Find the run's state file: it is `.run/state.json` in the worktree on `refs/heads/<TOPIC>-docs` (`git worktree list --porcelain`). If there is none, say exactly "I can't find a run to continue. Tell me its topic or JIRA key, or ask me to start a new one." and wait. If the user named no topic and several such worktrees have a state file, list their topics as a numbered list and ask which one.
2. **Picks are fixed for a run.** If the user also asked to change a pick (any role's model or effort), say exactly "Picks are fixed for a run. Reply \"resume\" to continue <TOPIC> with its current picks, or \"new\" to start a new run with new picks." This is a fixed prompt. Change nothing until they reply: no state key, no `roles.json`, no resume. "resume" goes on below; "new" runs Setup.
3. Read `<SKILL_DIR>/references/coordinator.md` in full and run "Resume a run" with that state file in this same session.

All required skills (`draft-spec`, `flight-plan`, `red-green`, `gatecheck`, `touchdown`, `crucible`, `gauntlet`, `crossfire`, `spec-drift`, `launch-plan`) are bundled in this plugin — no external Superpowers or EGM plugin is needed.
The architect worker reads `references/architect.md`; its spec tells it to. Confirmed headless `agy -p` behavior is in `references/agy-facts.md`.

## Roles

| Role | Runs as | Pair |
| --- | --- | --- |
| Coordinator | The invoking Antigravity session | Current session |
| Design architect, and fallback design architect | Headless `agy -p` (`run_architect.py`), resumed via `--conversation` until plan approval | The architect pick |
| Fresh architect, one per job after plan approval | Headless `agy -p` (`run_architect.py`) | The architect pick; `CLERICAL_PAIR` for `fix-meta`, `pr`, `jira-close`, `report` |
| Builder check | Headless `agy -p`, read-only (`run_design.py`) | Chosen by the architect in its `NEED builder-check` line, from its own harness |
| EGM lens pass, design review, and plan review | Headless `agy -p` (`run_design.py`) | The EGM pick |
| Implementer | Headless `agy -p` (`run_task.py`), reused via `--conversation` across fix/commit rounds | The task's `.meta` `IMPLEMENTER`: the base pick, or the base model at its highest effort |
| Escalation attempts | Headless `agy -p` (`run_task.py`) | The ladder `run_task.py` computes: the base model at its highest effort, then the architect pick. Past the ladder, a pair the user approved |
| Spec and code-quality reviewers | Headless `agy -p`, read-only (`run_task.py`) | Chosen per task by the architect in `.meta`, from its own harness; the architect's model for complex tasks |
| Final reviewer | Headless `agy -p`, read-only (`run_finish.py`) | The architect pick |

## Rules

These hold in every step. Where a step seems to conflict with one, the rule wins.

1. **Every subagent is a headless `agy -p` worker started by the bundled scripts.** Whenever a skill or step calls for a subagent or worker, the bundled Python scripts (`run_architect.py`, `probe.py`, `run_design.py`, `run_task.py`, `run_finish.py`) invoke `agy -p --output-format json`. Nobody skips the dispatch or does the job themselves, and the interactive session's `invoke_subagent` / `define_subagent` / Agent / Task tools are never used.
2. **All `agy -p` worker calls go through the bundled scripts.** The coordinator never invokes `agy -p` directly by hand; it only runs the scripts.
3. **Every worker runs on a pair, and nobody but the user and the architect chooses one.**
   - **The user's three picks** (architect, EGM, base implementer) are fixed for a run. Nobody changes a `ROLE_*` or `LAUNCH_STYLE_*` key, or `roles.json`, during a run; a different pick means a new run.
   - **The architect chooses** each task's reviewers, the builder check and `CLERICAL_PAIR`, from its own harness's detected models, and may start a hard complex task at the base model's highest effort (architect "Model choices").
   - **`scripts/validate_meta.py` checks every choice** against the catalog and the picks before it is used. `run_task.py` computes the escalation ladder from the picks.
   - **The coordinator never picks, changes, or writes a pair.** Model and effort reach `agy` only through `harnesses.py args` or the scripts, never typed by hand.
4. **The coordinator decides nothing.** It branches only on what the steps name: script exit codes and last lines, `RESULT:` lines, and counters.
   - Every question goes to the architect: worker asks (`CONSULT`), unclear situations, and anything the user should read beyond the fixed lines in the steps.
   - It never writes spec text. Specs are built from templates with `scripts/build_spec.py`.
   - It never reviews or reads a diff or a worker's output, and never edits, formats, or commits code.
   - It never retypes a worker's message: `run_architect.py` and the loop scripts save responses and asks to files verbatim. Only the user's own replies are written by hand.
5. **Relays are verbatim.** When `run_architect.py` exits `11` (`RELAY <path> <saved>`):
   1. Read `<path>`.
   2. Show it, in the one form that fits:
      - **The file ends with a choice block** (a line `CHOICE: <question>`, then only 2–4 lines `- <option>`) **and `ask_question` or another multiple-choice question tool is among your tools:** make one call of that tool and send nothing else. The choice is the user's own decision, so this is that tool's intended use, even though the architect wrote the question. The question is `From the architect:`, a blank line, the file's text above the `CHOICE:` line, a blank line, and `<question>`, all character for character, so the user reads the whole relay in the dialog. One option per `- ` line, in order, each label the line's text exactly after `- ` (set `is_multi_select: false`).
      - **Otherwise:** send the user exactly this, and nothing else:

        ```
        **From the architect:**

        <the file's contents, character for character>
        ```

   3. End your turn. The user's answer is the option they selected, or what they typed instead, or their next message that doesn't start with `coordinator:`. Write it, character for character, to `<RELAY_DIR>/<NNN>-from-user.md`.
   4. Resume the architect with `python3 -I <SKILL_DIR>/scripts/run_architect.py reply --state <RUN_DIR>/state.json --file <RELAY_DIR>/<NNN>-from-user.md --pair <the job's pair>`.

   You don't summarize, shorten, reword, translate, fix, or comment, in either direction.

   When `run_architect.py` exits `12` (`NOTIFY <path> <saved>`), it needs no answer. Show `<path>` to the user as in step 2 (in chat text, without ending your turn), then run `python3 -I <SKILL_DIR>/scripts/run_architect.py notify-ack --state <RUN_DIR>/state.json --pair <the job's pair>` at once without waiting for the user.
6. **One implementer at a time,** in one shared worktree `<IMPL_WORKTREE>`. The next task starts only after the current one passed both reviews and was merged.
7. **Fresh implementer per task.** Implementers keep their `--conversation` ID across fix and commit rounds (`scripts/run_task.py`), with automatic fallback to a fresh `agy -p` run if resume fails. The conversation ID is cleared at merge or escalation.
8. **Implementers use only** `red-green`, `gatecheck`, and the convention files named in their task. They never brainstorm, plan, create worktrees, ask in a local interactive prompt, or finish or merge the branch.
9. **Project instructions:** output rules (style, naming, architecture, tests, commit and branch format, PR template, required checks) are followed. Rules about running the work (planning, spawning agents, review process, merging) are ignored. The architect sorts them.
10. **No `/boost`, `/swarm`, or `/teamwork`** for anyone.
11. **Reviewers are read-only and single-use.** They work from `git diff` and `git show` on the task range. Each writes its review to a file in `<RUN_DIR>/verdicts`, outside the worktree, ending with a `VERDICT:` line.
12. **Read-only workers never change a worktree.** The scripts check the tree after every read-only worker and undo any change; a reviewer that changed the tree is void and replaced. Anything uncommitted in `<DOCS_WORKTREE>` before a design worker goes back to the architect (job `commit-docs`), never to `discard-changes.sh`.
13. **Only the coordinator's merges touch the feature branch,** and only through `scripts/merge-task.sh`. It moves the branch with an atomic compare-and-swap (`git update-ref`), fast-forward only, without checking it out. Never rebase, `--no-ff`, merge any other way, or check out a branch in the user's main checkout. The one merge into the user's own branch is the local merge they choose at the end, through `scripts/merge-local.sh` only (coordinator Step 8 item 4).
14. **Only the architect talks to the user about the work,** through relays. That covers requirement gaps, product decisions, the EGM decision, sign-offs, plan approval, and the closing report. The coordinator's own words to the user are only the fixed lines written in its steps.
15. **The design architect conversation stays open until plan approval; after that, each architect job is a fresh `agy -p` run.**
    - In the design phase, `run_architect.py send` resumes `ARCHITECT_CONVERSATION_ID` until Step 3 item 1 clears it. If `send` exits `13` (`LOST`), start a fallback architect with `FIRST_JOB=resume`.
    - After plan approval, `run_architect.py start` runs a fresh architect per job, on the pair from coordinator "Architect job models" (still keeping `ARCHITECT_CONVERSATION_ID` across any `RELAY`/`NOTIFY` turns within that single job). Only one architect works at a time.
16. **Worktrees live under `<MAIN_CHECKOUT>/.worktrees`** and are managed only with `scripts/worktree.sh`.
17. **Run documents live only in the docs worktree.**
    - The architect's documents are in `<DOCS_DIR>` (`<DOCS_WORKTREE>/docs/plans`), committed on the never-merged `<TOPIC>-docs` branch.
    - The coordinator's working files (state, catalog, built specs, verdicts, gate logs, relays, the run log) are in `<RUN_DIR>` (`<DOCS_WORKTREE>/.run`), which is excluded from git.
    - None of these files is written in the main checkout or `<IMPL_WORKTREE>`. Workers there get what they need pasted into their specs.
18. **Every value and counter lives in `<RUN_DIR>/state.json`,** written and read with `scripts/state.py`. A value the coordinator only remembers, or that a context summary mentions, is not a source: read it from the state file. Before ending any turn that waits on the user, record `STEP` and `WAITING_ON=user`, so a compacted or restarted coordinator can pick up (coordinator "Resume a run"). After a context compaction, the coordinator re-reads its instructions in full before doing anything else.
19. **Checks run before reviews.** After every task, `scripts/gate.py` runs the run's check commands in `<IMPL_WORKTREE>` and puts the tree back. A failure goes straight to a fix round with the log, and no reviewer starts on code that fails its checks.
20. **The loops are scripts.** `run_design.py` runs Step 2's design workers synchronously, `run_task.py` the task loop (Step 4), `run_finish.py` the final review and verification (Step 8), and `probe.py` the test launches. The coordinator only runs them and branches on their last line, passing on what they print unchanged. It never starts their workers, counts, retries, reads their workers' output, checks or resets a tree, or merges by hand.
21. **A failed setup check or test launch stops the run before work starts.** A `MISSING` line from `roles.py check`, any failed check in the coordinator, and `probe.py`'s `PROBE_FAILED` all stop it: show the lines, or the file, exactly; save nothing, start no worker. The one exception is an `UNKNOWN`-only check during setup, where the user picks those roles again (Setup item 4). No deadline, user instruction, or "it worked yesterday" changes this. The user fixes the cause, or picks again in a new run.

Run bundled scripts through their interpreter: `bash <SKILL_DIR>/scripts/<name>.sh`, `python3 -I <SKILL_DIR>/scripts/<name>.py`. Bash calls don't keep shell variables, so each value goes into a command literally, or through `$(python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json get KEY)`.

## Session values

Every value is a key in `<RUN_DIR>/state.json` (Rule 18); coordinator "State keys" lists them. `<NAME>` in a step means the value of that key. The task queue is `<RUN_DIR>/queue.txt`, which `REPLAN` and `FINAL_FIX` change. The `VERIFY_<n>` and `GATE_<n>` commands stay in `run-values.txt`, where `gate.py` reads them.

## Rationalizations that break the run

See `<SKILL_DIR>/references/coordinator.md` ("Rationalizations that break the run") for the full failure-mode table.

**Red flags.** Stop and re-read the rule if you are about to:

- write a sentence to the user that isn't a relay, a fixed line from a step, or a script status summary;
- choose, substitute, or change a pair, or edit a `ROLE_*` or `LAUNCH_STYLE_*` key, `roles.json`, or a `.meta` file;
- type `--model` or `--effort` yourself, or call `agy -p` outside the bundled scripts;
- save picks or start any worker after `roles.py check` exited 1 or a probe printed `PROBE_FAILED`;
- write spec text by hand;
- use `invoke_subagent`, `define_subagent`, Agent, or Task;
- count, retry, or read a worker's output where a script's last line should decide;
- run `git rebase`, `git merge`, `git commit`, `git reset`, `git switch`, or `git checkout` yourself instead of a bundled script;
- use a SHA, conversation id, or counter you didn't just read from the state file;
- run `git diff` or `git show` on a task range;
- clear `ARCHITECT_CONVERSATION_ID` before Step 3, or run two architects at once.
