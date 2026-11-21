# mission-control

A self-contained, multi-skill plugin for **Antigravity** (`agy`) and **Claude Code** (`claude`) for spec-driven development and multi-agent execution via headless CLI workers (`agy -p` / `claude -p`), including mixed cross-harness runs.

Every stage of the workflow is available both as an **independently invocable skill** (for brainstorming, planning, adversarial design review, TDD, drift checking, verification, or branch landing) and as a **full end-to-end pipeline** (`mission` and `launch-plan`).

---

## How It Works

When you run the full pipeline (`mission` or `launch-plan`), your active chat session acts purely as the **coordinator**—it runs deterministic state-machine scripts, dispatches headless worker sessions, relays outputs verbatim, and merges verified work atomically. It does no design, coding, or judgment work itself.

```
Requirements / JIRA Key
        │
        ▼
  1. Setup & Probe        Detect installed harnesses/models, confirm role picks, probe CLI flags
        │
        ▼
  2. Design & Review      Architect drafts spec ──► Independent reviewers stress-test & audit
        │
        ▼
  3. Task Plan            Architect breaks spec into ordered TDD tasks + verification commands
        │
        ▼
  4. Per-Task Loop        Isolated git worktree ──► Implement (TDD) ──► Gate (tests/lint)
        │                 ──► Spec & quality review ──► Atomic fast-forward merge
        ▼
  5. Finalize & Land      Full-branch verification + spec-drift check ──► PR, local merge, or keep
```

### Key Mechanics

- **Multi-harness & cross-harness role routing:** You assign a `<harness>:<model>@<effort>` pair to three roles during setup:
  - **Architect** — designs the spec, writes the task plan, answers questions raised mid-run, and chooses per-task reviewers.
  - **Design / Plan Reviewer** — runs structured, fresh-context design and plan reviews before any code is written.
  - **Base Implementer** — writes code and tests for each task, with automatic escalation to stronger picks if a task gets stuck.
  
  Roles can all run on Antigravity, all on Claude Code, or be **mixed in the same run** (for example, an architect on `claude:opus@high` with implementers on `antigravity:<model>@high`). Only [`scripts/harnesses.py`](scripts/harnesses.py) knows harness-specific CLI flags, JSON envelopes, and model catalogs.
- **Isolated git worktrees & deterministic state:** Run metadata, plans, and state live on a dedicated `<topic>-docs` worktree (`.run/state.json`), while each implementation task executes in its own task worktree and only fast-forwards into the feature branch after passing verification gates and reviews.
- **Session resumption with safe fallback:** Multi-turn workers resume their CLI session (`--conversation` on `agy`, `--resume` on `claude`) across fix rounds, automatically detecting expired or silently restarted sessions and falling back to a clean retry.
- **Compaction-safe & resumable:** Because all progress is persisted in `.run/state.json`, you can stop at any point and resume later from the exact step where you left off.

---

## How to Use It

### 1. Run a full end-to-end feature (`mission`)

Invoke `mission` with either a JIRA key or written requirements:

> "Use `mission` to implement PROJ-1234."
>
> "Use `mission` to add OAuth2 PKCE login with refresh token rotation and integration tests."

On first run, the coordinator detects your installed harnesses (`agy`, `claude`), walks you through selecting `<harness>:<model>@<effort>` for each role, checks required skills/MCP integrations, and saves your picks to `~/.config/mission-control/roles.json` for reuse on future runs.

### 2. Execute an existing plan (`launch-plan`)

Already have a markdown plan (or a `tasks/` directory with `run-values.txt`)? Skip design and jump straight into the task execution, review, and landing loop:

> "Use `launch-plan` on `docs/my-feature-plan.md`."

### 3. Resume an interrupted run

If a session ends or hits a quota/rate limit, simply ask in a new session:

> "Continue my `mission` run for `<topic>`."

The coordinator locates `.run/state.json` in the `<topic>-docs` worktree and picks up cleanly without losing completed tasks.

### 4. Use individual skills standalone

You don't need the full orchestrator to use any stage—invoke any bundled skill directly in your session:

- **Drafting & planning:** `draft-spec` to refine requirements into a design doc; `flight-plan` to turn a spec into ordered TDD tasks.
- **Stress-testing & review:** `crucible` for 7-lens ideation stress-testing; `gauntlet` for 5-stage design doc review; `crossfire` for adversarial multi-reviewer audits.
- **Implementation & shipping:** `red-green` for strict TDD; `gatecheck` for evidence-based verification; `spec-drift` to audit finished code against the spec; `touchdown` to verify tests and land a branch via PR or fast-forward merge.

---

## Bundled Skills

| Skill | When to use | Execution style |
| --- | --- | --- |
| `mission` | End-to-end flow from JIRA key or written requirements through design, plan, execution, verification, and PR | Coordinator + headless `agy -p` / `claude -p` workers |
| `launch-plan` | Execute an existing markdown plan (or `tasks/` + `run-values.txt`) through execution, verification, and landing | Coordinator + headless `agy -p` / `claude -p` workers |
| `draft-spec` | Clarify requirements, weigh approaches, and write a design doc | Interactive in-session |
| `flight-plan` | Break a spec or design doc into TDD tasks and verification steps | Interactive in-session |
| `crucible` | Ideation + 7-lens stress-testing (`concepts_brief.md`) | Fresh-context subagent lenses |
| `gauntlet` | 5-stage design document review (zoom-out, comprehension, critique, readiness) | Fresh-context subagent reviewers |
| `crossfire` | Adversarial multi-reviewer audit loop on specs, plans, or docs | Fresh-context subagent reviewers |
| `spec-drift` | Compare finished source code against design docs | In-session / drift check |
| `red-green` | Strict RED-GREEN-REFACTOR test-first workflow | In-session or worker |
| `gatecheck` | Require fresh command output before claiming success | In-session or worker |
| `touchdown` | Verify tests and complete via PR, fast-forward local merge, keep, or discard | In-session or coordinator final step |

---

## Installation

### Option 1: Antigravity Plugin (recommended for `agy`)

```sh
agy plugin install https://github.com/marwanehab98/mission-control
```

Validate locally before installing:

```sh
agy plugin validate --strict .
```

### Option 2: Claude Code Plugin (recommended for `claude`)

`mission-control` includes `.claude-plugin/plugin.json` for Claude Code plugin discovery:

```sh
claude plugin install https://github.com/marwanehab98/mission-control
```

Or load directly from a local checkout:

```sh
claude --plugin-dir /path/to/mission-control
```

### Option 3: Direct Skills-Folder Release (`install.sh`)

`install.sh` tests the target git ref in a clean copy first and never installs uncommitted work.

```sh
bash install.sh --harness antigravity    # install all 11 skills into ~/.gemini/config/skills
bash install.sh --harness claude         # install all 11 skills into ~/.claude/skills
bash install.sh                          # update every harness that already has mission-control
bash install.sh --target <dir>           # install the full plugin bundle into a custom directory
bash install.sh --ref <commit>           # install an older version (rollback)
python3 scripts/harnesses.py install-dirs  # detected harnesses and their skills folders
```

Each install records a `VERSION` file naming its commit. Replaced versions are kept in `~/.local/share/mission-control/backups/<harness>` (newest 5 each).

---

## Tests & Validation

```sh
bash tests/run_all.sh
agy plugin validate --strict --json .
```
