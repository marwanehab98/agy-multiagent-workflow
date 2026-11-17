# ToS-Compliant Multi-Agent Workflow

*   **Author:** Marwan Elsheikh
*   **Status:** Proposed
*   **Date:** 2026-11-17

--------------------------------------------------------------------------------

## Overview

This design defines a ToS-compliant, multi-harness software delivery workflow derived from `/home/marwan/.claude/skills/orca-multiagent-workflow`. It takes a JIRA key or user-written requirements through design, Edge-and-Goldfish-Model (EGM) review, task implementation, automated check gates, two-stage review, final review, and atomic branch merge.

To comply with Google Antigravity Additional Terms §6 and consumer Google AI Pro policy (`#187241`) without requiring AI Studio or Vertex AI API keys, the workflow uses a **same-session coordinator funnel** and **three-case execution routing**:
1.  **Case 1 (Non-`agy` coordinator and workers):** Runs via Orca workers as today.
2.  **Case 2 (`agy` coordinator and `agy` workers):** Runs natively inside `agy` using `swarm` workers and deterministic Python/shell scripts.
3.  **Case 3 (`agy` coordinator with mixed `agy` and non-`agy` workers):** `agy` dispatches `agy` roles via `swarm` and non-`agy` roles (such as Claude Code) via Orca `worker-start`.

## The Problem

`/home/marwan/.claude/skills/orca-multiagent-workflow` currently launches all workers—including Antigravity (`agy`)—through Orca's `worker-start` command, which runs Orca's bundled `agy_acp_server.par` runtime, and allows Claude Code to act as coordinator/architect over Antigravity workers. Under Google Antigravity Additional Terms §6 and staff guidance (`#187241`), having third-party coding agents or unapproved ACP wrappers programmatically orchestrate `agy` on a consumer Google AI Pro login risks automated enforcement (`403 PERMISSION_DENIED`) and account suspension (with a one-time appeal limit).

At the same time, switching to pay-as-you-go Gemini API keys or Vertex AI is out of scope, and dropping role separation/model tiering increases quota burn as feature size grows. We need a workflow that preserves role separation, isolated worktrees, deterministic scripts, and sub-linear quota scaling while keeping Antigravity usage within first-party `agy` boundaries.

## Goals

*   **ToS-compliant orchestration on consumer subscriptions:** Prevent any external coding agent or unapproved ACP wrapper (`agy_acp_server.par`) from orchestrating `agy` on a Google AI Pro login.
*   **Same-session coordinator funnel:** Eliminate the setup-to-coordinator handoff launch by making the invoking session the Coordinator, and disable `agy` role options when invoked from a non-`agy` session (`"agy roles are only available from an agy coordinator."`).
*   **Explicit model pinning without silent downgrades:** Allow pinning Gemini 4 Hydrogen for high-leverage roles (Architect, EGM, Escalation, Final Reviewer) via `swarm` rather than coarse `invoke_subagent` tiers that can fall back to 3.8 Flash; pause on rate limits rather than silently downgrading.
*   **Sub-linear token/quota growth as work scales:** Reuse deterministic Python/shell scripts (`state.py`, `build_spec.py`, `gate.py`, `merge-task.sh`, `validate_meta.py`) at zero LLM token cost, `kill` single-use `swarm` workers immediately, and inject incremental diff/gate context (`git diff <BASE_SHA>..HEAD` + failing log) on fix rounds instead of re-reading full history.
*   **Preserve delivery invariants:** Keep isolated `docs` (`<TOPIC>-docs`) and `impl` git worktrees, gate-before-review enforcement, read-only reviewer tree checks, atomic `state.json` resume, and the escalation ladder.

## Non-Goals

*   Using Google AI Studio (`GEMINI_API_KEY`) or Vertex AI / ADC billing.
*   Supporting automated cross-vendor orchestration where a non-`agy` agent (Claude Code, Codex) spawns or prompts `agy`.
*   Using `invoke_subagent` for roles that require explicit Gemini 4 Hydrogen model pinning.
*   Using weak local open-weight models for primary multi-file implementation or architectural roles.
*   Replacing deterministic Python/shell scripts (`gate.py`, `state.py`, `merge-task.sh`) with LLM-driven checks or merges.

## Open Questions

None. All initial questions (`swarm` worktree path-scoping via `inherit` for `<DOCS_WORKTREE>` vs. `<IMPL_WORKTREE>`, exact `swarm models` effort slugs, and zero-token background `run_command` Orca status waiting) are resolved and staged for the Technical Plan.

## Critical User Journeys (CUJs)

### CUJ 1: Setup Funnel in a Non-`agy` Session (Case 1)

*   **Given**: The user invokes the skill from a Claude Code session.
*   **When**: Setup runs harness detection and presents role menus for Architect, EGM, and Base Implementer.
*   **Then**: `agy` is disabled in every role menu with the notice `"agy roles are only available from an agy coordinator."`, and the run executes via Orca workers in the current session as Coordinator.

### CUJ 2: All-`agy` Native Run on Google AI Pro (Case 2)

*   **Given**: The user invokes the skill from an `agy` session on their chosen coordinator model/effort and picks `agy` models for all roles.
*   **When**: The workflow progresses through design, EGM review, task implementation, `gate.py`, two-stage review, and final review.
*   **Then**: The `agy` Coordinator dispatches workers via `swarm` (explicitly pinned models, killing single-use workers after completion), runs deterministic Python/shell scripts via `run_command`, passes incremental diffs on fix rounds, and merges atomically without invoking Orca `worker-start`.

### CUJ 3: Hybrid `agy` + Non-`agy` Run (Case 3)

*   **Given**: The user invokes the skill from an `agy` session and selects `agy` for some roles (e.g., Architect, EGM) and `claude` for others (e.g., Base Implementer or Reviewer).
*   **When**: The `agy` Coordinator dispatches `agy` roles via `swarm` and `claude` roles via Orca `worker-start`.
*   **Then**: No external agent prompts `agy`; `agy` monitors Orca worker state (`succeeded`, `failed`, `lost`, `asking`) via `orca` CLI commands and relays any worker questions to the Architect/user before advancing.

## Technical Plan (Architecture)

### 1. Same-Session Coordinator & Role-Selection Funnel
Setup runs directly inside the invoking session, which becomes the Coordinator (`COORDINATOR_HARNESS` = current host: `agy` or `claude`/Orca):
*   **Harness Discovery (`harnesses.py`):**
    *   If invoked from `agy`, `agy` models are populated from `swarm{action:"models", all:true}` (giving exact slugs such as `gemini-4-hydrogen-low|medium|high|xhigh` and `quota_remaining`), while non-`agy` harnesses are detected via their CLIs (`claude`, etc.).
    *   If invoked from a non-`agy` session, `agy` is excluded from selectable worker harnesses and role menus display `"agy roles are only available from an agy coordinator."`
*   **Pick Storage:** Picks are saved in `<CFG>/roles.json` and `state.json` without a separate setup-to-coordinator handoff launch.

### 2. Three-Case Execution Routing
The Coordinator dispatches workers through a backend interface (`WorkerBackend`) selected by `COORDINATOR_HARNESS` and the role's chosen harness:

```text
Invoking Session = Coordinator
  │
  ├── Non-agy Coordinator (Case 1)
  │     └── All worker roles ──▶ Orca worker-start + run_design/run_task/run_finish
  │
  └── agy Coordinator
        ├── Role harness == agy (Case 2, or Case 3 agy roles)
        │     └── swarm{action:"add", model:<slug>, workspace:"inherit"}
        │           ├── Docs/Design/EGM roles ──▶ scoped to <DOCS_WORKTREE>
        │           └── Implementer/Reviewers ──▶ scoped to <IMPL_WORKTREE>
        │
        └── Role harness != agy (Case 3 non-agy roles)
              └── ORCA orchestration worker-start
                    └── Background run_command (orca_wait.py)
                          └── Wakes agy on succeeded | failed | lost | asking
```

### 3. `swarm` Board Hygiene & Worktree Scoping
*   **Explicit Path Scoping:** Every `swarm` worker runs with `workspace: "inherit"` and an explicit worktree path in its brief (`<DOCS_WORKTREE>` for Design Architect, EGM, and post-plan Architect jobs; `<IMPL_WORKTREE>` for Implementer, Spec Reviewer, Code-Quality Reviewer, and Final Reviewer). Read-only reviewers and `gate.py` continue to run `git status --porcelain` checks against the target worktree.
*   **Immediate `kill` for Single-Use Workers:** Because every `post_message` on a `swarm` board is read by all living workers, the Coordinator must `swarm{action:"kill"}` single-use workers (lens reviewers, EGM, spec/quality reviewers, completed implementers) as soon as their output/verdict is saved. Only the active Design Architect (until plan approval) or active task Implementer (across fix rounds) stays `waiting` on the board.

### 4. Incremental Fix Context & Rate-Limit Policy
*   **Artifact-Indexed Incremental Context:** When a worker starts fresh (new `swarm` worker or post-compaction retry), `build_spec.py` injects only the task spec, `git diff <BASE_SHA>..HEAD`, and the exact failing `gate.py` log or reviewer verdict file, avoiding full-history re-reads.
*   **No Silent Model Downgrade:** If a pinned model (such as `gemini-4-hydrogen-*`) hits a rolling quota/rate limit, the Coordinator pauses and prompts the user (`retry`, `stop`, or `manual-handoff`) instead of silently switching to `gemini-3.8-flash-*`.

### 5. Zero-Token Orca Waiter (Case 3)
When `agy` dispatches a non-`agy` worker via `ORCA orchestration worker-start`:
*   `agy` launches a lightweight Python waiter (`scripts/orca_wait.py --dispatch <id>`) via `run_command` in the background (`WaitMsBeforeAsync: 500`) and ends its turn.
*   `orca_wait.py` polls `ORCA` status locally at zero LLM token cost and exits with structured status (`succeeded`, `failed`, `lost`, or `asking` + message body) when the worker settles or asks a question, waking `agy` to process the result.

## Alternatives Considered

### Alternative A: Keep `/orca-multiagent-workflow` with `antigravity` as Coordinator via Orca `worker-start`
*   **Pros**: Zero changes to existing Orca orchestration scripts (`run_design.py`, `run_task.py`, `run_finish.py`).
*   **Cons**: While Orca's interactive Terminal/CLI mode (`agent launch` in CLI mode) runs the system `agy` binary in a PTY, the existing workflow's scripts (`common.py`, `probe.py`, `run_design.py`, `run_task.py`, `run_finish.py`) spawn every worker via `ORCA orchestration worker-start --agent antigravity`. Per `references/orca-facts.md`, `worker-start` always launches a structured worker through Orca's bundled `~/.config/orca/agent-runtimes/antigravity/1.4.1/agy_acp_server.par` runtime rather than the system `agy` CLI, leaving it in an unapproved third-party ACP wrapper gray area under Antigravity Additional Terms §6 and `#187241`.
*   **Reason for Rejection**: Rejected by author: avoiding Google AI Pro account suspension risk (and the one-time appeal limit) requires eliminating Orca `worker-start` (`agy_acp_server.par`) for `agy` roles.

### Alternative B: Use a Gemini API Key (`GEMINI_API_KEY` or Vertex AI) with Orca + Claude Code
*   **Pros**: Removes consumer-plan ToS restrictions on third-party orchestrators while keeping the existing Orca workflow unchanged.
*   **Cons**: Pay-per-token API billing for frontier models (such as Gemini 4 Hydrogen) in multi-turn coding and review loops grows quickly compared to a flat \$19.99/month Google AI Pro subscription.
*   **Reason for Rejection**: Rejected by author: staying on the flat Google AI Pro subscription without API keys is a hard constraint.

### Alternative C: Use `agy` `invoke_subagent` with Coarse Tiers (`flash_lite`, `flash`, `pro`, `inherit`)
*   **Pros**: Lighter-weight than `swarm` (no shared message board broadcast overhead).
*   **Cons**: Does not expose per-model or per-effort pinning; `flash` can route to `gemini-3.8-flash` instead of `gemini-4-hydrogen-low`, degrading implementation/review quality and increasing fix-round retries.
*   **Reason for Rejection**: Rejected by author: explicit model pinning (preferring Gemini 4 Hydrogen over 3.8 Flash) is required.

### Alternative D: Drive One Interactive `agy` Session via PTY `/clear` and `/model` Injection
*   **Pros**: Avoids spawning new `agy` processes per task.
*   **Cons**: External PTY automation from a non-`agy` tool still constitutes third-party orchestration on consumer OAuth, and TUI prompt injection is brittle.
*   **Reason for Rejection**: Rejected by author: does not satisfy ToS constraints when driven externally, and `swarm` natively provides fresh worker contexts with model pinning.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **ToS & Auth Safety**: No OAuth token extraction, proxying, or multi-account rotation. Non-`agy` coordinators are blocked at setup from selecting `agy` worker roles.
*   **Quota & Token Overhead**: Keep `swarm` board membership minimal by `kill`ing single-use workers immediately; offload git plumbing, spec building, test gates, and Case 3 Orca waiting to deterministic Python/shell scripts at zero LLM token cost.
*   **Resume & Rollback**: All step counters, worker IDs/names, and SHAs persist atomically in `<RUN_DIR>/state.json` via `state.py`. `gate.py` restores `<IMPL_WORKTREE>` after checks, and `merge-task.sh` uses atomic `git update-ref` fast-forwards.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

All new and updated files will be packaged in `~/.gemini/config/skills/multiagent-workflow/` (and optionally symlinked to `~/.claude/skills/multiagent-workflow/` so Case 1 works identically from Claude Code). Existing deterministic scripts from `/home/marwan/.claude/skills/orca-multiagent-workflow/scripts/` are copied into the skill's `scripts/` directory and updated as enumerated below.

### Target Files to Create/Modify

#### 1. `SKILL.md` (Create / Adapt from `orca-multiagent-workflow/SKILL.md`)
*   **Type / Scope**: Skill entrypoint & Coordinator setup protocol.
*   **Proposed Changes**: Replace the setup-to-coordinator handoff step with same-session coordinator initialization. Add the funnel rule: detect the invoking host harness (`COORDINATOR_HARNESS`), and when `COORDINATOR_HARNESS != agy`, disable `agy` in role menus with `"agy roles are only available from an agy coordinator."` Update Role and Rule tables to route `agy` roles through `swarm` (Case 2 / Case 3) and non-`agy` roles through Orca `worker-start` (Case 1 / Case 3), while enforcing immediate `swarm{action:"kill"}` for single-use workers and pause-on-rate-limit behavior instead of silent model fallback.

#### 2. `references/coordinator.md` (Modify)
*   **Type / Scope**: Coordinator execution guide.
*   **Proposed Changes**: Add `swarm` dispatch/board-hygiene instructions (`workspace: "inherit"`, explicit `<DOCS_WORKTREE>` / `<IMPL_WORKTREE>` path scoping, `swarm_wait`, immediate `kill` on single-use workers) and Case 3 non-`agy` dispatch instructions (`worker-start` + background `run_command` calling `scripts/orca_wait.py`). Preserve all verbatim relay, `state.py`, and `merge-task.sh` rules.

#### 3. `references/agy-facts.md` (Create, alongside `references/orca-facts.md`)
*   **Type / Scope**: Harness behavior reference.
*   **Proposed Changes**: Document verified `swarm{action:"models", all:true}` slug formats (`gemini-4-hydrogen-low|medium|high|xhigh`, `gemini-4-argon-*`, `gemini-3.8-flash-*`), quota fields, `swarm` board broadcast behavior, and `workspace: "inherit"` path-scoping checks.

#### 4. `scripts/harnesses.py` (Modify)
*   **Type / Scope**: Python harness & model discovery module.
*   **Proposed Changes**: Add `--coordinator-harness <agy|claude|...>` to `detect` and `roles.py menu` so `agy` is excluded and annotated with `"agy roles are only available from an agy coordinator."` when the coordinator is not `agy`. For `agy`, accept a JSON snapshot from `swarm{action:"models", all:true}` (or `agy models` fallback) to populate exact `swarm` model IDs and quotas without Orca `worker-start` launch styles.

#### 5. `scripts/roles.py` (Modify)
*   **Type / Scope**: Python role selection, validation, and state initialization.
*   **Proposed Changes**: Remove `ROLE_COORDINATOR` selection from interactive prompts (bound automatically to `COORDINATOR_HARNESS` and current model). Update `check` and `to-state` to validate Case 1 / Case 2 / Case 3 invariants (rejecting any `agy` worker pick if `COORDINATOR_HARNESS != agy`).

#### 6. `scripts/orca_wait.py` (Create)
*   **Type / Scope**: Python background waiter for Case 3 non-`agy` Orca workers.
*   **Proposed Changes**: Implement a CLI script (`python3 -I scripts/orca_wait.py --dispatch <id> [--poll-secs 5]`) that polls `ORCA` worker status locally at zero LLM token cost and exits 0 with structured JSON/key-value output when the worker reaches `succeeded`, `failed`, `lost`, or `asking` (including the question text/path).

#### 7. `scripts/build_spec.py` and `references/templates/{fix,implement,spec-review,quality-review}.txt` (Modify)
*   **Type / Scope**: Spec builder & prompt templates.
*   **Proposed Changes**: Add explicit `{{TARGET_WORKTREE}}` scoping instructions in templates so `swarm` workers with `workspace: "inherit"` never edit outside their assigned `<DOCS_WORKTREE>` or `<IMPL_WORKTREE>`. In `fix.txt` and `build_spec.py`, support injecting incremental fix context (`git diff <BASE_SHA>..HEAD` + failing `gate.py` log or reviewer verdict excerpt) so fresh `swarm` workers do not re-read full repo history.

#### 8. `scripts/probe.py`, `scripts/run_design.py`, `scripts/run_task.py`, `scripts/run_finish.py`, `scripts/common.py` (Modify)
*   **Type / Scope**: Python state machines & backend abstraction.
*   **Proposed Changes**: Introduce a backend mode per worker (`orca` vs. `swarm`) in `common.py` / `state.py`. In `probe.py`, skip Orca `worker-start` probes for `agy` roles when `COORDINATOR_HARNESS == agy` (validating against `swarm models` instead). In `run_design.py`, `run_task.py`, and `run_finish.py`, emit structured `DISPATCH_SWARM <role> <model> <worktree> <spec_file>` / `KILL_SWARM <name>` instructions when the next worker is `agy`, and `DISPATCH_ORCA ...` + `orca_wait.py` instructions when the next worker is non-`agy` (Case 1 or Case 3), keeping all gate, merge, and state transitions unchanged.

#### 9. `scripts/state.py`, `scripts/gate.py`, `scripts/merge-task.sh`, `scripts/merge-local.sh`, `scripts/validate_meta.py`, `scripts/save_message.py` (Copy Unchanged / Minimal Wiring)
*   **Type / Scope**: Deterministic plumbing & verification scripts.
*   **Proposed Changes**: Reuse unchanged for atomic `state.json` persistence, test/build gate execution + tree reset, `git update-ref` fast-forward merges, `.meta` validation, and verdict/message saving.

#### 10. `tests/test_funnel_and_routing.py` (Create) and `tests/test_harnesses.py`, `tests/test_roles.py`, `tests/test_run_task.py` (Modify)
*   **Type / Scope**: Python unit & integration tests (using `fake_orca.py` and `swarm models` JSON fixtures).
*   **Proposed Changes**: Test that non-`agy` coordinators block `agy` role selection; test Case 1, Case 2, and Case 3 dispatch outputs; test `orca_wait.py` handling of `succeeded`, `failed`, `lost`, and `asking`; and test incremental fix-round spec generation in `build_spec.py`.

### Verification Plan

*   **Unit Tests**: Run `python3 -m unittest discover -s tests` inside the skill directory to verify funnel enforcement, Case 1/2/3 routing, `orca_wait.py` state handling, and `build_spec.py` incremental context expansion.
*   **Static Analysis & Linting**: Run `python3 -m py_compile scripts/*.py` and `bash -n scripts/*.sh` to ensure zero syntax errors across all scripts.
*   **End-to-End Smoke Runs**:
    *   **Case 1 smoke test:** Invoke from a non-`agy` session and confirm `agy` is disabled in role menus with `"agy roles are only available from an agy coordinator."`
    *   **Case 2 smoke test:** Invoke from `agy`, select `agy` (`gemini-4-hydrogen-*`) for all roles, and verify `swarm` workers stay scoped to `<DOCS_WORKTREE>` / `<IMPL_WORKTREE>`, single-use workers are `kill`ed immediately, `gate.py` runs before reviews, and `merge-task.sh` merges cleanly.
    *   **Case 3 smoke test:** Invoke from `agy`, select `agy` for Architect/EGM and `claude` for Base Implementer, and verify `orca_wait.py` runs in background `run_command` and wakes the coordinator on `succeeded` and `asking`.

--------------------------------------------------------------------------------

*Drafted with EGM*
