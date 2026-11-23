---
name: cross-check
license: Apache-2.0
description: >-
  Use when the user explicitly asks to cross-check, adversarially review, or
  audit a document (design doc, spec, implementation plan, README) with
  several fresh-context reviewers, or when another bundled skill calls it.
  Runs a Range Safety Officer (RSO) review loop and applies approved fixes.
---

# Cross-Check: Adversarial Review Loop

## Overview

This skill acts as the central orchestration engine driving the multi-agent
review loop. It coordinates two distinct agent memory models:

*   **RSO Reviewers (Adversarial Auditors)**: Subagents spawned in isolated,
    fresh context windows to catch unstated assumptions without historical bias.
*   **Primary Session (Persistent Resolver)**: The primary agent context holding
    institutional knowledge to evaluate findings and apply approved repairs.

The driver manages argument intake, resolves paths for reviewers and standards,
unconditionally re-parses target line ranges before every iteration, spawns
concurrent specialized RSO auditors based on configuration, and coordinates
with the primary session context to resolve findings.

## Delegation

An RSO reviewer runs in a fresh context with no conversation history. You, the primary session, dispatch every reviewer yourself, in parallel where the step allows, and wait for each report: a subagent cannot spawn subagents, so never ask one to launch reviewers. Where the plugin's agents are registered as subagent types, use `mission-control:<name>` (e.g. `mission-control:rso-design`) when it is available. Otherwise (Antigravity's `invoke_subagent` / `define_subagent`, or a general subagent), tell the subagent to read its persona file first. Either way, give it the absolute path of any assigned standard in `resources/document_standards/`, and pass the prompt verbatim.

| Name (either form) | Persona file |
| --- | --- |
| `rso-zoom-out` / `zoom_out` | `agents/zoom_out.md` |
| `rso-inventive-catalyst` / `inventive_catalyst` | `agents/inventive_catalyst.md` |
| `rso-comprehension` / `comprehension` | `agents/comprehension.md` |
| `rso-design` / `design_critic` | `agents/design_critic.md` |
| `rso-structural` / `structural` | `agents/structural.md` |
| `rso-style` / `style` | `agents/style.md` |
| `rso-implementation-readiness` / `implementation_readiness` | `agents/implementation_readiness.md` |
| `rso-codebase-details` / `codebase_details` | `agents/codebase_details.md` |
| `rso-synthesizer` / `synthesizer` | `agents/synthesizer.md` |

`rso-synthesizer` is not a reviewer. To keep raw reports out of your context, you may pass the reports you collected to it; it deduplicates them and writes the consolidated report, and never dispatches reviewers.

If running inside a headless CLI worker started by `mission` (`lens.txt`, `range-safety.txt`, `plan-review.txt`), you are already executing in a fresh isolated RSO context; if nested subagent tools are unavailable in that headless process, execute the requested reviewer personas directly in sequence and write the report requested by your prompt. Otherwise, in an interactive session without a subagent tool, stop and tell the user: a review in the same context is not an isolated RSO review.

## Asset Resolution

Resolve `agents/<file>.md` and `resources/document_standards/<name>.md` from the plugin/skill bundle root (never from the target repository's working tree):
- In a plugin or repo layout (`.../skills/<this-skill>/SKILL.md`): `../../agents/` and `../../resources/document_standards/`.
- In a flat skills install (`<skills_dir>/<this-skill>/SKILL.md`): `../mission/agents/` and `../mission/resources/document_standards/`.

## Inputs

The caller or user provides exactly these parameters:

*   **`TargetFile`**: Absolute or relative path to target document.
*   **`SemanticTarget`**: Descriptive targeting string (e.g., *"Target the H4
    block with SS-ID 'S1.12'"*), or `full file`.
*   **`Reviewers`**: List of reviewer names (shortnames or paths).
*   **`Standards`**: Mapping of reviewer names to standard names (shortnames or
    paths).
*   **`ReviewContext`**: A reading list (source files, directories, or
    documentation URLs) fed to reviewers to establish their baseline. An empty
    list from the caller counts as provided; do not ask for it again. Some
    reviewers cannot open URLs, so before the first round save the raw page of
    each URL as a new file in the working directory (or your harness's artifact
    directory) and pass its absolute path instead. If a page cannot be saved,
    ask the user for a local copy and end your turn; if no one can answer,
    as in a headless run, leave it out, tell the user, and keep going.

## Path & Standard Resolution Logic

When resolving names in `Reviewers` and `Standards`:

*   **Reviewer Names**: Map each reviewer name, in either form (`rso-design`
    or `design_critic`), to its persona file with the table in Delegation.
    The persona file defines the reviewer's core mandates, audit vectors, and
    output format. Map `Standards` keys the same way.
*   **Standard Shortnames**: Map standard shortname (e.g., `feature-spec-standard`,
    `implementation-readiness-standard`, `zoom-out-standard`, `heilmeier-standard`,
    `re-implementation-standard`) to `resources/document_standards/{name}.md`.
*   **Custom Paths**: If the name contains a slash (`/`), treat it as an
    absolute or workspace-relative path and read it directly.

## Execution Workflow

### Step 1: Intake & Configuration UX

Evaluate the provided inputs (`TargetFile`, `SemanticTarget`, `Reviewers`,
`Standards`, `ReviewContext`).

1.  **Identify Missing Inputs**: Determine which of the required parameters are
    missing.
2.  **Interactive Onboarding**:
    *   If **all** inputs are present, proceed to Step 2.
    *   If **some or all** inputs are missing, interactively prompt the user
        **only for the missing inputs**, one at a time via chat messages.
3.  **Reviewer Recommendations**:
    *   *Feature Specs*: Recommend `rso-design` and `rso-structural`, both
        mapped to `feature-spec-standard`.
    *   *Implementation Plans*: Recommend `rso-implementation-readiness`
        (mapped to `implementation-readiness-standard`) and
        `rso-codebase-details`.
4.  **Confirm Configuration**: Display the consolidated plan (Target, Reviewers,
    and mapped Standards) and ask the user to confirm before proceeding.

### Step 2: The Sharpening Loop (Iterative Cycle)

Execute the following sequence in a continuous loop:

#### 2.1 Unconditional Target Resolution

Before every iteration, unconditionally re-scan `TargetFile` by reading it
and using the stable `SemanticTarget` string (e.g., locating the target header) to
dynamically determine the updated absolute start and end line numbers. This
ensures absolute accuracy even if the file was edited in prior rounds. (If `full
file` mode is specified, confirm full-file line bounds).

#### 2.2 Review Swarm Execution

1.  **Launch the Review Panel**: Dispatch every reviewer in `Reviewers`
    yourself, in parallel (see Delegation). Each audits `TargetFile` at the
    resolved line range against its designated standard, with
    `ReviewContext`, and reports findings with severity (🔴 Blocker, 🟡
    Major, 🟢 Nit), line numbers, discrepancy descriptions, and rationales.
    Optionally pass the raw reports to `rso-synthesizer` to deduplicate them
    and write the report; its summary must give a status, `completed` or
    `not run`, for each requested reviewer.
2.  **Consolidate Findings**: Collect all findings across reviewers into a single
    flat `FindingsList`. Give each finding a stable id
    (`<reviewer>-<round>-<n>`) and record its reviewer, severity, and affected
    scope.
3.  **Failed Reviewers**: A reviewer that times out, fails, or returns no report
    did not review. Retry it once. If it fails again, record it as **not run**
    in the report. Never count it as a reviewer with zero findings. If the
    synthesizer summary has no status for a requested reviewer, record that
    reviewer as not run.

#### 2.3 Aggregated Resolution

1.  Execute finding resolution directly in the primary session context using
    `TargetFile`, the active line range, and the aggregated `FindingsList`:
    *   Categorize each finding as `CONTEST` (with technical rationale) vs.
        `PROPOSE FIX` (with exact text repair).
    *   Save `rso_review_report.md` with your file-writing tool.
    *   Present proposed repairs to the user for confirmation (Auto-accept all
        proposed fixes, Walk through individually, or Contest/skip).
    *   Apply approved text repairs to `TargetFile` using your file-editing tool.

#### 2.4 Iteration Check & Turn Boundary

Fresh reviewers sample differently each round, so a round with zero findings
is rare even when the document improves. Use these rules instead:

*   **Settled findings**: A finding is settled when it is contested or
    recorded without a fix (accepted risk). Keep the settled list across
    rounds. If a new reviewer raises a settled finding again (same affected
    scope, same issue), do not re-open it.
*   **Fixed findings**: An applied fix does not settle a finding. If a later
    reviewer raises the same issue at the same scope, the fix failed or a
    later edit reintroduced the issue. Re-open the finding.
*   **Clean round**: A round is clean when no reviewer raises a Blocker or
    Major finding that is not settled. Nits do not block. A reviewer recorded
    as not run makes the round not clean. Decide this from the review results,
    before you apply the fixes of that round.
*   **Converged**: Two consecutive clean rounds. One clean round is not enough,
    because the next fresh reviewer can find a different gap in the same text.
*   **Cap**: 5 rounds.
*   **Churn**: A new finding is at the scope of an applied fix, and reverting
    that fix would resolve it.

On converged, go to Step 3. On cap or churn, write a short list of the open
findings by severity, then ask the user one question: accept and proceed,
continue past the cap, or return the document to drafting.

**Crucial Turn-Taking Requirement**: After applying approved fixes for a
given round, **remain idle across human turns**,
only looping back to Step 2.1 when the user explicitly confirms they wish to run
another round of review.

### Step 3: Output Boundary & Final Checkpoint

Upon completion (converged, cap or churn accepted by the user, or manual
halt):

*   **Programmatic Callers**: If running as a subflow of `flight-readiness`, exit
    cleanly to hand execution back to the parent workflow.
*   **Manual Invocations**: Output a summary of completion in chat: *"The review
    loop has completed after [N] rounds ([converged | accepted at cap | halted]).
Open findings are in the report. Would you like to proceed to
    the next stage, or do you have any final questions?"*

<!-- Adapted from google/elephant-goldfish-model (Apache-2.0); modified for mission-control. See THIRD_PARTY_NOTICES.md. -->
