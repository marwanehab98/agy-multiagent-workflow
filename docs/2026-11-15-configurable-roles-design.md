# orca-multiagent-workflow: review fixes and configurable roles

Date: 2026-11-15. Status: draft for approval.

## Intent

Two things, delivered together.

1. **Fix every issue from the 2026-11-15 review** of the skill (rated 8/10): a fix-round counter that can be reset forever, an unchecked escalation reset SHA, the design phase and Step 8 still run as prose state machines, an oversized coordinator procedure, a hard ERROR after three void reviewers, and a misleading test command.
2. **Stop hard-coding harnesses and models.** Today the coordinator is Claude Code on Sonnet, the architect Claude Code on Opus, EGM and the base implementers Antigravity on Gemini 4 Hydrogen, and the rules name Opus and Sonnet directly. Instead, the run starts by detecting what is installed and asking the user to pick harness + model + effort for four roles. Every other worker's model follows from those picks.

**Why:** the user wants to choose models per run, and the skill must not break when Orca's agent detection, a harness's model naming, or effort handling changes. Example: Orca's bundled Antigravity runtime is 1.4.1 while `agy` is 1.5.9, Orca's own model catalog is an undocumented internal SQLite cache, and `agy` gained `--effort` (low…max) after `orca-facts.md` said never to pass effort for Antigravity.

## Goals

- The user picks harness + model + effort for: coordinator, architect, EGM, base implementer.
- Harnesses, models and efforts are detected from each harness's own CLI, never from Orca's catalog.
- Each pick is checked for the role's requirements (skills, plugins, MCP servers); a missing requirement stops the run with a message naming what is missing and where.
- Each pick is proven launchable through Orca by a tiny test worker before real work starts.
- Picks are remembered; the next run offers "same as last time".
- All review issues fixed, with tests.

## Non-goals

- Bypassing Orca to launch workers. Workers still start with `orca orchestration worker-start --agent`, because supervision and messaging depend on it. The skill's Orca surface shrinks to: agent ids, `--model`, `--effort`, and the worker lifecycle verbs, all checked by the test launch.
- Supporting harnesses with no adapter. Orca agent ids that no adapter covers (codex, cursor, opencode, …) are not offered. Adding one is a new adapter class plus fixtures.
- Choosing reviewers, the builder check, or the final reviewer at setup. The architect chooses them (below).

## Anti-goals

- Any model or harness name hard-coded in the rules, templates or scripts outside the adapters and their fixtures.
- A run that fails mid-way because a model chosen at setup can't launch or lacks a skill: those must fail before the architect starts.
- A setup flow so long the user dreads it: four picks, each defaulting to last time.

## Approaches considered (for detection and launch)

- **A. Harness adapters + test launch** (chosen). One adapter per harness knows how to find its binary, list models and efforts, check requirements, map to an Orca agent id, and pass effort. Each pick then gets a test launch through Orca. A harness change means editing one adapter, and the test launch catches it before work starts.
- **B. Read Orca's catalog.** Least code, but it is the dependency being removed. Rejected.
- **C. Free-text picks, test launch only.** Simplest, but no menus, typos found late, and no detection. Rejected.

## Part 1: Review fixes

### 1.1 Fix-round cap can reset forever (`run_task.py`)

Today a gate pass after a gate failure resets `FIX_ROUNDS_<id>` to 0, so a task alternating between a review FIX and a failing gate never escalates. A gate failure also overwrites `FAILED_STAGE_<id>`, so a stage-2 fix whose gate fails restarts at stage 1.

Fix:
- Keep two separate values: `REVIEW_STAGE_<id>` (the review stage to resume at: 1 or 2) and `GATE_FAILED_<id>` (yes/no).
- Three counters per attempt (my call, from the builder check):
  - `STAGE_FIX_ROUNDS_<id>_S<n>`: review fix rounds for stage n. It counts only that stage's FIX verdicts and resets only when that stage passes. At 2, escalate. This keeps today's rule, and gate fixes no longer reset it.
  - `GATE_FIX_ROUNDS_<id>`: consecutive gate fix rounds. It resets when the gate passes. At 2, escalate.
  - `TOTAL_FIX_ROUNDS_<id>`: every fix round of either kind, never reset within an attempt. At 4, escalate. It is the backstop.
  - An escalation attempt resets all three.
- After a gate pass, resume at `REVIEW_STAGE_<id>`, not stage 1.
- Tests:
  - Stage-2 FIX, gate fail, gate pass, stage-2 FIX, then stage-2 FIX again: escalates on the third stage-2 FIX.
  - An alternating gate/review loop escalates at the total cap.
  - A stage-2 fix whose gate fails then passes returns to stage 2.

### 1.2 Unchecked escalation reset SHA (`run_task.py`)

Before `discard-changes.sh`, require `BASE_SHA_<id>` ⊑ `<reset sha>` ⊑ the implementation worktree's current HEAD (`git merge-base --is-ancestor`, both). Otherwise reject the architect's answer through the existing `reject()` path with the reason. Test: a SHA before the base is rejected.

### 1.3 Script the design-phase workers and Step 8

Move the remaining counting and branching out of prose:

- **`scripts/run_design.py advance`**, the same shape as `run_task.py` (state in `state.json`, never polls, last line is the status). It handles every `NEED` block of coordinator Step 2: the docs-tree check and `commit-docs` routing, building the spec, starting the worker, saving the settle, the tree check and discard, retry counters, run limits (`EGM_RUNS`, `LENS_RUNS`, `PLAN_REVIEW_RUNS`), and producing the `-done` job input. Its statuses: `WAIT`, `ARCHITECT_JOB <job> <input>`, `PROMPT`, `ERROR`. The coordinator still relays and sends architect jobs.
- **`scripts/run_finish.py advance`** for Step 8 items 1–3: the starting-state check, the final reviewer (start, save, tree check, void and replace, verdict), `FINAL_FIX_ROUNDS` and its fixed prompt, writing the final-fix id to the queue, and the verify gate. PR, JIRA, cleanup and report stay in prose (they are relays and one skill call).
- **`scripts/common.py`** holds what the three scripts share: `Run` (state), `Orca`, `verdict_line`, `save_body`, `next_free`, spec building, and the launch helper from Part 2. `run_task.py` imports it, and the duplicated verdict logic in Step 8 prose is deleted.
- Tests for both new scripts against `tests/fake_orca.py`, in the style of `test_run_task.py`.

### 1.4 Shrink the coordinator procedure

A consequence of 1.3: Step 2 and Step 8 items 1–3 become "run the script, branch on its last line" tables, as Step 4 already is. Target: `coordinator.md` under 200 lines. Rules that only the scripts enforce move to the scripts' docstrings.

### 1.5 Smaller items

- **Three void reviewers:** `run_task.py` prompts "retry or stop" instead of raising `ERROR` (same mechanism as the usage-limit prompt). `run_finish.py` does the same for the final reviewer.
- **Test command:** add `tests/run_all.sh` running `test_scripts.sh` and `python3 -I -m unittest discover -s tests`, and fix the docstring in `test_run_task.py`.
- **Environment-specific:** Part 2.

## Part 2: Configurable roles

### 2.1 Who picks what

The user picks four roles, each as harness + model + effort:

| Role | Requirements checked in its harness |
| --- | --- |
| Coordinator | this skill (`orca-multiagent-workflow`), Orca's `orchestration` and `orca-cli` skills, Superpowers (`verification-before-completion`, `finishing-a-development-branch`) |
| Architect | Superpowers (`brainstorming`, `writing-plans`); for a JIRA run, an Atlassian MCP server that is connected |
| EGM | the EGM plugin's `egm-brainstorm`, `egm-review`, `egm-review-loop` skills |
| Base implementer | Superpowers (`test-driven-development`, `verification-before-completion`) |

Everything else follows:

| Worker | Model |
| --- | --- |
| Lens pass, EGM design review, plan review | EGM pick |
| Builder check | Chosen by the architect from its own harness, scaled by the design's complexity: a small, contained design gets a cheaper model; a large or risky one (auth, concurrency, several services) a stronger one, up to the architect's own pick. Named in the `NEED builder-check` line. |
| Per-task reviewers (Stage 1, Stage 2) | Chosen by the architect per task from its own harness, scaled by complexity, in `.meta` as today |
| Final reviewer | The architect pick |
| Fresh architect, thinking jobs (`consult`, `escalate`, `final-review`, `verification-failed`, `baseline-failed`, `user-message`) | The architect pick |
| Fresh architect, clerical jobs (`fix-meta`, `pr`, `jira-close`, `report`) | `CLERICAL_PAIR` in `run-values.txt`, chosen by the architect from its own harness (my call: keeps today's Sonnet-for-clerical saving without a fifth setup question) |
| Implementer, first attempt | Base pick by default; the architect may start a hard `complex` task at the base model's highest effort (one line in "Model choices") |

**Hard limits** (enforced by `validate_meta.py`):
- Reviewer and builder-check pairs are in the architect's harness, from the detected catalog. The architect chooses their effort too.
- A `complex` task's `REVIEW_2` uses the architect's harness and model, at any effort the architect chooses; so does a `complex` `judgment` task's `REVIEW_1`. (My call: compare harness and model, not effort. Otherwise an architect picked at `max` would force every complex review to `max`.)
- A task's first implementer is either the base pick or the base model at its highest effort. On top of that, the exact architect pair (harness, model and effort) never implements first. It may only be the ladder's last rung, or an attempt the user approved after the ladder was used up. This check only matters when the architect and base share a model. Example: architect Hydrogen `xhigh`, base Hydrogen `high`. Every task then starts at `high`, because starting at `xhigh` would be the architect pair.

**When the architect and base picks share a harness and model** (for example, architect `antigravity:gemini-4-hydrogen@xhigh`, base `…@high`): my call is that setup allows it with a one-line note, and the ladder drops any rung equal to an earlier rung or to the architect pair, so rungs never repeat. With that example, the ladder is `…@high` then `…@xhigh` (the architect). The "start a hard task at the highest effort" option is then unavailable, because it would be the architect pair implementing first.

**Picks are fixed for a run** (my call): they are copied into `state.json` at Step 1. Resuming skips setup, reads `ROLE_*` from the state file, re-runs detection, and re-probes the four picks and any recorded reviewer pairs. A pick that no longer launches stops the resume with Orca's error; changing picks means starting a new run. Changing picks mid-run would mean re-validating every `.meta` and stale launch styles, which isn't worth it.

**Runs from before this change aren't resumed** (my call): a state file without `ROLE_*` keys makes every script exit with `ERROR OLD_FORMAT_RUN <how to remove it>`. The only such run is on the scratch repo `omw-smoke`.

### 2.2 The escalation ladder

Fixed, computed by the script from the picks, not written by the architect:

1. Base model at base effort (the task's first attempt).
2. Base model at its highest detected effort.
3. The architect pick.

If the base effort is already the highest, rung 2 is dropped. A rung equal to an earlier rung or to the architect pair is also dropped (2.1). If a task's first attempt already started at the highest effort, rung 2 is skipped for that task. "Highest" means the top effort the adapter detected for that model in that harness (`xhigh` for Hydrogen in `agy models` today, `max` for Claude Code models).

`LADDER` is removed from `.meta`. The architect no longer swaps rungs (my call: the user specified the ladder; this also removes a validation path). Past the ladder, with the user's approval, any detected pair that passes a test launch is allowed.

### 2.3 Pair format

A pair is written `<harness>:<model>@<effort>`, e.g. `antigravity:gemini-4-hydrogen@xhigh`, `claude:opus@high`. It is split at the first `:` and the last `@`. A model with no effort choice uses `@default`. Only the adapter turns a pair into Orca arguments. This replaces today's `agent:model` pairs everywhere (`.meta`, `RESULT: ESCALATE`, state keys, templates).

### 2.4 Detection: `scripts/harnesses.py`

`python3 -I harnesses.py detect --out <file>` writes a JSON catalog. Each adapter is a small class with five methods:

1. `find()`: the binary and its version, or absent.
2. `models()`: a list of `{model, label, efforts[]}`.
3. `requirements(role, jira)`: a list of `{name, ok, where}`.
4. `orca_agent()`: the Orca agent id.
5. `launch_args(model, effort, style)`: Orca `worker-start` arguments for a launch style.

Adapters at launch:
- **Claude Code** (`claude`, Orca id `claude`). Models: the aliases `claude --help` documents for `--model` (today `fable`, `opus`, `sonnet`), plus `haiku`, kept as a list in the adapter because Claude Code has no command that lists models; the test launch proves each one. Efforts: parsed from the `--effort` line of `claude --help`. Requirements: `claude plugin list`, skill folders under the Claude config home (`$CLAUDE_CONFIG_DIR` or `~/.claude`) and plugin skill folders, `claude mcp list` for a connected Atlassian server. Launch styles: `flag` (`--model <m> --effort <e>`).
- **Antigravity** (`agy`, Orca id `antigravity`). Models: `agy models`, each slug split into base model and effort suffix (`low|medium|high|xhigh|max`); a slug with no such suffix is one model with effort `default`. Requirements: `agy plugin list` (enabled), skill folders under `~/.gemini/config/skills` and `~/.gemini/config/plugins/*/skills`, `agy mcp list`. Launch styles, in preference order: `slug` (`--model <model>-<effort>`), then `flag` (`--model <model> --effort <effort>`).

Detection runs in the coordinator's environment, which is the environment Orca launches workers with. All parsing is covered by fixture tests (captured `agy models`, `claude --help`, `plugin list`, and `mcp list` output). When a parse finds nothing, the adapter reports the harness as broken with the raw output, rather than offering an empty menu.

### 2.5 Test launches: `scripts/probe.py`

For each distinct pair to check, it starts a worker in the docs worktree with a fixed spec ("Settle worker_done (succeeded) with the single word OK. Do nothing else."), trying the adapter's launch styles in order. A style passes when the worker settles `succeeded`. The first passing style is recorded per pair in the state file (`LAUNCH_STYLE_<pair>`), and every later launch of that pair uses it. Each probe worker is released and the docs tree checked afterwards. Probes run in parallel.

When probes run:
- **Coordinator Step 1, after the docs worktree exists:** the architect, EGM, base, and base-at-highest-effort pairs. The coordinator pick is proven by the handoff itself.
- **Coordinator Step 3, after `validate_meta.py` passes:** every distinct reviewer pair in `.meta`, plus `CLERICAL_PAIR`.
- **When the architect names a builder-check pair:** that pair, if not already probed.
- **Past the ladder:** the approved pair.

A pair whose every style fails stops the run with Orca's error text. The user re-runs setup to pick again.

### 2.6 Setup flow (new SKILL.md "Start here")

The session that loads the skill is the setup session, whatever its harness. A request to continue an earlier run skips setup and goes straight to the coordinator's "Resume a run" on the pick's harness, handing off if needed (2.1, "Picks are fixed for a run").

1. Run `harnesses.py detect`.
2. Load the remembered picks from `~/.config/orca-multiagent-workflow/roles.json`, which lives outside the skill folder so it survives skill updates. Offer "same as last time" only if every remembered pick is still in today's catalog. Show the four picks in one block.
3. Otherwise ask the four roles in order, each as numbered lists: harness, then model, then effort. Plain text, not a multiple-choice tool, because model lists exceed four options.
4. Run the requirement checks for each pick. On any failure, stop and name the missing item and the harness folder or command where it should be.
5. Save the picks to `roles.json`.
6. Hand off with `orca-cli` to a fresh coordinator on the chosen harness, model and effort, with a brief naming the picks file and the user's request verbatim. My call: always hand off. A session can't confirm its own effort, and the coordinator then starts with clean context. Tell the user which tab runs the work, then stop.

Coordinator Step 1 copies the picks into `state.json` (`ROLE_COORDINATOR`, `ROLE_ARCHITECT`, `ROLE_EGM`, `ROLE_BASE`, plus `CATALOG=<RUN_DIR>/catalog.json`) and runs the Step 1 probes. The `GEMINI_MODEL` / `GEMINI_EFFORT` values and the `agy` check in Step 1 item 2 are removed.

### 2.7 What changes in the existing files

- **`SKILL.md`:** the Overview and Roles table name roles, not models. "Start here" becomes 2.6. Rules 1, 3 and 15 are generalized ("the harness's own subagent tool", "the architect", "picks"). The rationalization table loses its model-specific rows and gains "I'll pick a different reviewer model myself" → validate_meta allowlist.
- **`coordinator.md`:** the Workers table, the Architect job models table, and the Step 1 intake change per 2.1 and 2.6. Steps 2 and 8 change per 1.3.
- **`architect.md`:** "Model choices" is rewritten with `<ARCH>`, `<BASE>`, `<BASE_TOP>` and "a model from your harness's catalog" (the spec gives the catalog path). `LADDER` is removed from `.meta`. `CLERICAL_PAIR` is added to `run-values.txt`. `NEED builder-check` takes a pair. The ESCALATE answer's pair must equal the next rung, or come after user approval past the ladder.
- **`validate_meta.py`:** the allowlist comes from the catalog and the picks (`--catalog`, `--roles`) instead of code, the hard limits follow 2.1, and the ladder is computed.
- **`run_task.py`:** the ladder comes from the picks, launches go through the adapter plus the recorded launch style, and the pair format follows 2.3.
- **Templates:** the EGM templates take the EGM skill paths from the adapter's requirement result, replacing the `find ~/.gemini` step.
- **`orca-facts.md`:** the Antigravity effort section is marked historical. Launch style is now decided by the probe and recorded per run.
- **Tests:** existing tests are updated for the pair format and the fixed ladder, with new tests for the adapters (fixtures), `probe.py`, `run_design.py`, `run_finish.py`, and the 1.1, 1.2 and 1.5 fixes. `fake_orca.py` learns to accept or reject launch styles per agent so the probe fallback is tested.
- **Live check before calling it done:** one setup-and-probe run against real Orca with both harnesses, plus one full run on the `omw-smoke` repo with non-default picks (for example, an `agy` architect).

## Left to the builder

- Internal structure of the adapters and the scripts, state key names beyond those named here, and status-line spellings, as long as each script documents its statuses in its docstring as `run_task.py` does.
- The exact numbered-list layout of the setup questions.
- How `run_design.py` splits work between itself and the coordinator's relay handling, as long as the coordinator never counts, retries, or checks trees by hand in Step 2.
