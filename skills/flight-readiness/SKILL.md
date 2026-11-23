---
name: flight-readiness
license: Apache-2.0
description: >-
 Use when the user explicitly asks for a flight-readiness review or a full
 multi-stage review of a design document, or when another bundled skill calls
 it. Runs five Range Safety Officer (RSO) stages: zoom out, comprehension,
 design critique, intermediate zoom out, implementation readiness.
---

# Flight Readiness Review

Reviews and validates a design document through specialized reviewer personas.

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

## Execution Constraints (CRITICAL)

1. **Artifact-First Delivery (MANDATORY)**: Never dump large proposals, drafts, summaries, or review reports directly in chat. Write them to disk (working directory or artifact directory) with your file-writing tool, then share the file path in chat and ask for review.
2. **Plain-English Standard**: Write briefs, summaries, and communications in direct, jargon-free plain English. Explain mechanisms concretely; avoid promotional adjectives (*seamless*, *robust*, *blazingly fast*) and decorative adverbs (*seamlessly*, *effortlessly*, *literally*, *drastically*, *obviously*).
3. **Stress-Test & Critique First**: Never agree by default or echo framing reflexively. Lead with weaknesses, edge cases, blind spots, and the strongest counter-arguments before noting merits; only agree after objective scrutiny.
4. **No Flattery or Apologies**: Never use validating filler ("brilliant," "great," "smart," "correct," "perfect," "excellent," "spot-on") or polite apologies ("I apologize," "I am sorry," "Forgive me"). State corrected technical logic and trade-offs directly.
5. **Interaction Invariants**: Every interactive gate (asked as in Step 2 item 2) MUST ask exactly one question, be the sole tool call in the turn, and offer clear action-oriented options without `(Recommended)` prefixes.

## Inputs

- **`TargetFile`**: Absolute or relative path to the design document.
- **`ReviewContext`** (Optional): Reading list to establish the system
  baseline. If omitted, it holds only the linked files that Step 1.3 adds.
- **`Step` (Optional)**: Specific pipeline preset or stage to run (e.g.
  `interactive`, `uninterrupted`, `feature_spec`, `implementation`, or an
  individual stage `5`, `6`, `7`, `8`, `9`). If omitted, the user is prompted.

## Workflow

### Step 1: Intake & Setup

1. Verify if `TargetFile` is provided. If missing:
  - Explain to the user that the target design document path is required to
    analyze its structure and discover the semantic split.
  - Prompt the user in chat for the path via a regular text message (do NOT
    use structured question tools such as `ask_question` / `AskUserQuestion`, as providing an
    arbitrary file path is an open-ended text input, not a choice between
    fixed options) and end your turn.
2. Once `TargetFile` is provided, verify if `ReviewContext` is provided (an
  empty list counts as provided, as does the user explicitly stating in their
  prompt to proceed with no additional context). If missing:
  - Explain to the user that review context (reading list of files) is
    optional, but can establish baseline knowledge for reviewers.
  - Clarify that linked files inside the working directory are added
    automatically, and that other links and URLs need approval.
  - Prompt the user in chat via regular text if they wish to provide
    additional context files. Do NOT use structured question tools for
    open-ended file paths.
3. **Linked Files (run later)**: Do not run this during intake. Run it once,
  after the Step 2 preset is chosen and right before the first stage that
  takes `ReviewContext` (any stage but Stage 6), in any flow including
  `uninterrupted`. Read `TargetFile`, skip anchor-only links (`#...`), drop
  the `#fragment` from other links, and resolve each relative link against
  the design document's folder. Add each link whose real path (after
  following symbolic links) stays inside the working directory to
  `ReviewContext`, skipping files that do not exist. If any link resolves
  outside the working directory or is a URL, list those links in chat as
  regular text, ask which to include, and end your turn; if no one can
  answer, as in a headless run, leave them all out and keep going. Add only
  approved ones and tell the user which were left out. Some reviewers cannot
  open URLs, so save the raw page of each URL in `ReviewContext` as a new
  file in the working directory (or your harness's artifact directory) and
  pass its absolute path instead. If a page cannot be saved, ask the user for
  a local copy and end your turn; if no one can answer, as in a headless run,
  leave it out, tell the user, and keep going. Pass
  `ReviewContext` to every reviewer in every stage except Stage 6, which
  reads the document alone.

### Step 2: Curated Pipeline Preset Selection

1. **Preset Gate Check**:
  - If `Step` parameter was explicitly provided during invocation (e.g.
    `interactive`, `uninterrupted`, `feature_spec`, `implementation`, or an
    individual stage `5`, `6`, `7`, `8`, `9`), map to the corresponding
    preset or subflow and bypass the interactive preset selection gate.
  - Otherwise (if `Step` was omitted), you **MUST** present the Curated
    Pipeline Presets single-select gate to the user.
2. **Asking**: Use your harness's structured question tool if it has one
  (e.g. `AskUserQuestion`: 2-4 options as `{label, description}`,
  `multiSelect: false`; Antigravity: `ask_question` with `options` and
  `is_multi_select: false`); otherwise ask in plain text with numbered
  options. Ask one question, as the sole tool call of the turn, then yield.
  Never multi-select: the stages depend on each other (5 → 6 → 7 → 8 → 9).
  Option 0 is the default action, without a `(Recommended)` prefix.

  - Question: `Select the review execution mode for this document:`
  - Option 0: `Interactive review with all review rubrics` (step-by-step,
    check in after each review)
  - Option 1: `Consolidated report with all review rubrics` (runs the full
    flow without stopping)
  - Option 2: `Feature spec only` (evaluate the design without considering
    implementation)
  - Option 3: `Implementation plan only` (evaluate the design for
    implementation readiness)

3. **Action Dispatch Based on Preset**:

  - **Preset 0: `Interactive review with all review rubrics`**: Proceed to
    Step 3 to discover the semantic split, then execute Stages 5, 6, 7, 8,
    and 9 sequentially with interactive checkpoint gates between stages.
  - **Preset 1: `Consolidated report with all review rubrics`**: Proceed to
    Step 3 to discover the semantic split, then execute all five stages
    without pausing for checkpoints, consolidating findings into
    `consolidated_review_report.md`.
  - **Preset 2: `Feature spec only`**: **Bypass Step 3 semantic split
    discovery entirely**. Set `FeatureRange = 1..End`, set `DetailedRange =
    N/A`, and execute Stages 5, 6, 7, and 8 with checkpoints. Cleanly halt
    review after Stage 8 before Stage 9.
  - **Preset 3: `Implementation plan only`**: Proceed to Step 3 to discover
    the semantic split, confirm `DetailedRange`, and jump directly to Stage
    9 (Implementation Readiness Test).

### Step 3: Semantic Split Discovery

1. **Split Bypass Check**: If Preset 2 (`Feature spec only`) was selected,
  **skip this step completely**. Set `FeatureRange` to cover the entire
  document (lines 1 through End) and proceed directly to Step 4.
2. **Inspect Document**: Read `TargetFile` to inspect its structure and line
  count.
3. **Discover Split Boundary**: Scan for the primary dividing header `##
  Detailed Implementation Plan` (or standard synonyms: `## Implementation
  Plan`, `## Work Breakdown`, `## Implementation Steps`).
  - **Standard Layout Detected**: If the boundary header is unambiguously
    identified at line $L$:
    - Automatically assign `FeatureRange = 1..(L-1)`.
    - Automatically assign `DetailedRange = L..End`.
    - Output a concise confirmation message to the user in chat:
      `"Discovered semantic boundary at line L. FeatureRange: 1..(L-1),
      DetailedRange: L..End."`
    - Proceed directly to Step 4 without presenting a confirmation modal.
  - **Ambiguous Boundary / Multiple Candidate Headers**: If multiple
    conflicting headers or non-standard structures exist:
    - Present a single-select gate (asked as in Step 2 item 2) proposing the most likely split line as Option 0, with Option 1
      allowing custom line ranges via write-in.
  - **No Implementation Plan Found**: If the document contains only a
    high-level feature specification:
    - Notify the user in chat that no implementation plan was detected.
    - Assign `FeatureRange = 1..End` and `DetailedRange = N/A`.
    - Offer to execute Stages 5-8 (Feature Spec gates) and proceed.

### Step 4: Staging Strategy

Once the semantic split and execution preset are confirmed:

1. **Confirm Review Target**: Verify that `FeatureRange` and `DetailedRange`
  are bound.
2. **Dispatch Execution Pipeline**:
  - **Interactive review with all review rubrics**: Execute Stage 5 → Stage
    6 → Stage 7 → Stage 8 → Stage 9 sequentially.
  - **Consolidated report with all review rubrics**: Run Subflow:
    Uninterrupted Full Flow.
  - **Feature spec only**: Execute Stage 5 → Stage 6 → Stage 7 → Stage 8,
    then cleanly halt.
  - **Implementation plan only**: Jump directly to Stage 9 (Implementation
    Readiness Test).

--------------------------------------------------------------------------------

## Subflows

### Subflow Checkpoint Gates & Pause/Resume Protocol

All interactive review stages conclude with an explicit single-select gate,
asked as in Step 2 item 2:

1. **Stage Checkpoint Patterns**:
  - **Stages 5, 6, 8 (Altitude & Comprehension Checkpoints)**:
    - Option 0: `Proceed to next stage: Stage [N+1] ([Next Stage Name])`
      (or `Conclude review and begin detailed implementation planning` for
      Stage 8 terminal).
    - Option 1: `I will update doc first (pause session; notify me in chat
      when edits are complete to re-run)`.
    - Option 2: `Pause Review (save progress and exit)`.
  - **Stages 7, 9 (Remediation & Readiness Checkpoints)**:
    - Option 0: `Proceed to next stage: Stage [N+1] ([Next Stage Name])`
      (or `Conclude review and finalize document` for Stage 9 terminal).
    - Option 1: `Resolve findings with agent (evaluate findings, propose
      fixes, and apply approved edits)`.
    - Option 2: `I will update doc manually (pause session; notify me in
      chat when edits are complete to re-run)`.
    - Option 3: `Pause Review (save progress and exit)`.
2. **Resolution Semantics (Option 1 for Stages 7 and 9)**:
  - **Inline Primary-Session Resolution**: In the primary session context window,
    triage the parsed findings from `critic_report.md` or
    `implementation_readiness_report.md` on the active range
    (`FeatureRange` or `DetailedRange`).
  - **Resolution Workflow**: Classify each finding as `PROPOSE FIX` (with
    exact proposed text edit) or `CONTEST` (with technical rationale),
    write `rso_review_report.md`, present proposed edits to the user
    for confirmation, and execute approved edits on `TargetFile`.
  - **Convergence**: After applying fixes, re-read `TargetFile` to
    recalculate line bounds, and either advance to the next stage or re-run
    the stage audit (enforcing max 1 re-audit limit).
3. **Pause & Resume Semantics (Manual Doc Updates)**:
  - **Turn Yield**: The agent confirms in chat that the review is paused for
    manual document edits, and yields execution immediately.
  - **Resume Notification**: When the author replies in chat indicating
    edits are complete:
    - Re-read `TargetFile` to recalculate line shifts.
    - If Step 1.3 has run, apply its rules to links added since then.
    - Re-run the current stage subflow.
    - **1-Audit Convergence Cap**: Each stage allows a maximum of 1
      re-audit. If manual doc updates are requested again after a
      re-audit, inform the author that the cap is reached, document
      residual findings as accepted trade-offs, auto-bind Option 0, and
      advance to the next stage.
4. **Graceful Halt Semantics (`Pause Review`)**:
  - Halt execution immediately with zero tools, preserving all report
    artifacts.

### Subflow: Uninterrupted Full Flow

1. **Uninterrupted Stage Execution**: Execute all five review stages in
  sequence without interactive checkpoint pauses between steps. For each
  stage, write the individual report file to the working directory (or your
  harness's artifact directory, if it has one). Pass `ReviewContext` to
  every step except Step 6.
  Dispatch each stage's reviewers yourself, in parallel (see Delegation);
  optionally pass their raw reports to `rso-synthesizer` to write the stage
  report.
  - **Step 5 (Initial Zoom Out)**: `rso-zoom-out` and
    `rso-inventive-catalyst` on `full file` → `zoom_out_report.md`.
  - **Step 6 (Comprehension Test)**: `rso-comprehension` alone on
    `FeatureRange` & `DetailedRange` → write its complete result, not a
    synthesis, to `comprehension_report.md`.
  - **Step 7 (Critic Review)**: `rso-design`, `rso-structural`, and
    `rso-style` on `FeatureRange` → `critic_report.md`.
  - **Step 8 (Intermediate Zoom Out)**: `rso-zoom-out` on `full file` →
    `intermediate_zoom_out_report.md`.
  - **Step 9 (Implementation Readiness Test)**:
    `rso-implementation-readiness`, `rso-codebase-details`, and `rso-style`
    on `DetailedRange` → `implementation_readiness_report.md`.
2. **Cross-Stage Synthesis & Deduplication**:
  - Collect all findings across the 5 stage reports.
  - Consolidate observations into actionable tasks by priority (🔴 Blocker, 🟡
    Major, 🟢 Nit).
  - Build a Traceability Matrix mapping raw findings to consolidated action
    items.
3. **Deliverable Delivery**: Write to `consolidated_review_report.md` using
  your file-writing tool.
4. **Notify User**: Output a concise chat notification pointing to
  `consolidated_review_report.md` (give its path).

### Subflow: Zoom Out Review

1. **Dispatch Reviewers**: Dispatch `rso-zoom-out` on `full file` applying `zoom-out-standard`, passing `ReviewContext`. For Stage 5, also dispatch `rso-inventive-catalyst` in parallel and write both into `zoom_out_report.md`. For Stage 8 (Intermediate Zoom Out), include `zoom_out_report.md` in context and focus on additions/mitigations from Stages 6–7.
2. **Delivery & Notify**: Write findings to `zoom_out_report.md` (Stage 5) or `intermediate_zoom_out_report.md` (Stage 8) and notify the user with the path (no chat dumps).
3. **Checkpoint Gate** (tri-state gate per Checkpoint Gate Protocol — Options 1 & 2 standard):
  - **Stage 5**: Question: `"Stage 5 (Initial Zoom Out Review) completed. Findings written to zoom_out_report.md. How would you like to proceed?"`, Option 0: `"Proceed to next stage: Stage 6 (Comprehension Test)"`.
  - **Stage 8 (Feature Spec Only)**: Question: `"Stage 8 (Intermediate Zoom Out) completed. High-level architecture verified. How would you like to proceed?"`, Option 0: `"Conclude review and begin detailed implementation planning"`.
  - **Stage 8 (Interactive Full Flow)**: Question: `"Stage 8 (Intermediate Zoom Out) completed. Findings written to intermediate_zoom_out_report.md. How would you like to proceed?"`, Option 0: `"Proceed to next stage: Stage 9 (Implementation Readiness Test)"`.

### Subflow: Comprehension Test

1. **Dispatch Reviewer Directly**: Dispatch `rso-comprehension` directly on `FeatureRange` & `DetailedRange` with no document standard (do not use the synthesizer, which drops the paraphrase; do not apply `re-implementation-standard.md`, which asks for code-level detail forbidden in `FeatureRange`).
2. **Delivery & Notify**: Write the complete reviewer result to `comprehension_report.md` (starting with the two paraphrase headings verbatim, then findings; keep the paraphrase even with zero findings) and notify the user with the path.
3. **Checkpoint Gate** (tri-state gate per Checkpoint Gate Protocol — Options 1 & 2 standard):
  - Question: `"Stage 6 (Comprehension Test) completed. Findings written to comprehension_report.md. How would you like to proceed?"`
  - Option 0: `"Proceed to next stage: Stage 7 (Critic Review)"`

### Subflow: Critic Review

1. **Invoke Adversarial Review Loop**: Invoke `cross-check` in-thread on `FeatureRange` with reviewers `[rso-design, rso-structural, rso-style]`, standards `{"rso-design": "feature-spec-standard", "rso-structural": "feature-spec-standard"}`, and `ReviewContext`. Write findings to `critic_report.md` and notify the user with the path.
2. **Checkpoint Gate** (4-option remediation gate per Checkpoint Gate Protocol — Options 1, 2 & 3 standard):
  - Question: `"Stage 7 (Critic Review) completed. Findings written to critic_report.md. How would you like to proceed?"`
  - Option 0: `"Proceed to next stage: Stage 8 (Intermediate Zoom Out Review)"`
3. **Option 1 Remediation**: Execute Resolution Semantics above on `FeatureRange` from `critic_report.md` (writing triage to `rso_review_report.md`), then advance to Stage 8 or re-run Stage 7 (max 1 re-audit).

### Subflow: Implementation Readiness Test

1. **Invoke Adversarial Review Loop**: Invoke `cross-check` in-thread on `DetailedRange` with reviewers `[rso-implementation-readiness, rso-codebase-details, rso-style]`, standards `{"rso-implementation-readiness": "implementation-readiness-standard", "rso-codebase-details": "implementation-readiness-standard"}`, and `ReviewContext`. Write findings to `implementation_readiness_report.md` and notify the user with the path.
2. **Terminal Checkpoint Gate** (4-option remediation gate per Checkpoint Gate Protocol — Options 1, 2 & 3 standard):
  - Question: `"Stage 9 (Implementation Readiness Test) completed. Report written to implementation_readiness_report.md. How would you like to proceed?"`
  - Option 0: `"Conclude review and finalize document"`
3. **Option 1 Remediation**: Execute Resolution Semantics above on `DetailedRange` from `implementation_readiness_report.md` (writing triage to `rso_review_report.md`), then conclude or re-run Stage 9 (max 1 re-audit).

<!-- Adapted from google/elephant-goldfish-model (Apache-2.0); modified for mission-control. See THIRD_PARTY_NOTICES.md. -->
