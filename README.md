# mission-control

A self-contained, multi-skill plugin for **Antigravity** (`agy`) and **Claude Code** (`claude`) for spec-driven development and multi-agent execution via headless CLI workers (`agy -p` / `claude -p`), including mixed cross-harness runs.

Every stage of the workflow is available both as an **independently invocable skill** (for ideation, spec drafting, planning, adversarial review, TDD, drift checking, verification, or branch landing) and as a **full end-to-end pipeline** (`mission` and `launch-plan`).

---

## Which Skill Should I Start With?

| Starting point | Use | What it does |
| --- | --- | --- |
| **Fuzzy / half-baked idea** ("should we even build this?") | `wind-tunnel` (standalone) | Runs all 4 steps: deep open-ended questioning, 7-lens stress testing, iterative refinement, and an RFC brief (`concepts_brief.md`) you can feed into `mission` or `mission-brief`. |
| **Concrete feature or JIRA story** | `mission` | Full end-to-end pipeline from requirements to a reviewed PR: spec drafting, automated stress-test & design review, task planning, per-task TDD + code review, drift check, and branch landing. |
| **Existing markdown plan** (or `tasks/` + `flight-manifest.txt`) | `launch-plan` | Skips design/planning and jumps straight into the per-task TDD execution, gate, review, and landing loop. |
| **Single focused task** (review a doc, fix a bug with TDD, land a branch) | Standalone skill | Call `mission-brief`, `flight-plan`, `flight-readiness`, `cross-check`, `red-green`, `gatecheck`, `spec-drift`, or `touchdown` directly in your session. |

---

## How It Works

When you run `mission` or `launch-plan`, your active chat session acts purely as the **Capsule Communicator**—it runs deterministic state-machine scripts, dispatches headless CLI workers, relays questions and approvals verbatim, and merges verified work atomically. It does no design, coding, or judgment work itself.

```
Requirements / JIRA Key
        │
        ▼
  1. Preflight & Systems Check   Detect installed CLIs/models, confirm 3 role picks, probe CLI flags
        │
        ▼
  2. Mission Brief & Safety Review  Flight Director clarifies & drafts spec (mission-brief)
        │                           ──► 7-lens stress test (wind-tunnel Step 2) + 5-stage audit (flight-readiness)
        │                           ──► You approve the spec
        ▼
  3. Flight Plan                 Flight Director breaks spec into ordered TDD tasks (flight-plan)
        │                        ──► Adversarial plan audit (cross-check) ──► You approve the plan
        ▼
  4. In-Flight Execution         Isolated git worktree ──► Crew implements (red-green TDD)
        │                        ──► Automated gate (tests/lint) ──► Spec & quality review
        │                        ──► Atomic fast-forward merge into feature branch
        ▼
  5. Final Check & Touchdown     Full-branch verification + spec-drift check ──► PR, local merge, or keep
```

### Key Mechanics

- **Two-phase Flight Director lifecycle (context vs. freshness):**
  - During **design and planning**, one Flight Director session stays open across turns via CLI session resumption (`--conversation` on `agy`, `--resume` on `claude`), so it remembers your Q&A and design rationale.
  - After **plan approval**, every Flight Director job (answering a Crew question, replanning, handling metadata fixes) starts a **fresh** headless session with no prior chat history—forcing it to answer strictly from the committed spec, plan, and files on disk.
- **Right-sized design review:** `mission` uses `mission-brief` to inspect your codebase first and draft the spec, then automatically runs `wind-tunnel` Step 2 (7-lens stress test) and `flight-readiness` (5-stage review) on non-trivial designs while skipping heavy passes on small changes.
- **Multi-harness & cross-harness role routing:** You assign a `<harness>:<model>@<effort>` pair to three roles during setup:
  - **Flight Director** (`director`) — designs the spec, writes the task plan, answers questions raised mid-run, and picks per-task reviewers.
  - **Range Safety** (`safety`) — runs fresh-context `wind-tunnel` Step 2, `flight-readiness`, `cross-check`, and `spec-drift` reviews.
  - **Crew** (`crew`) — writes code and tests for each task (must differ from the Flight Director pick so stuck tasks can escalate up the effort/model ladder).

  Roles can all run on Antigravity, all on Claude Code, or be **mixed in the same run** (for example, `claude:opus@high` for the Flight Director and `antigravity:<model>@high` for Crew). Only [`scripts/harnesses.py`](scripts/harnesses.py) knows harness-specific CLI flags, JSON envelopes, and model catalogs.

  > [!NOTE]
  > **Consumer subscription Terms of Service:** Having one vendor's CLI programmatically orchestrate another vendor's CLI may violate consumer subscription Terms of Service (e.g., Google AI Pro or Claude Pro/Max). If you are on consumer subscriptions rather than API-key / commercial billing, keep all roles on the same harness as your active session (`antigravity` in `agy`, or `claude` in `claude`).

- **Automatic repo detection & multi-repo workspaces:** [`scripts/workspace.py`](scripts/workspace.py) detects whether you're running inside a single git repository or a parent workspace directory containing multiple (including nested) repositories. In a multi-repo workspace, you choose which repos are in scope, tasks declare their target `REPO`, and each repo gets its own isolated implementation worktree, verification gate, and PR/merge step under `.mission-control/<topic>-impl/`.
- **Non-default base branches:** Runs never check out branches in your main checkout, so it doesn't need to be clean. You can name a base branch explicitly in your prompt (including local-only or pushed feature branches); if your currently checked-out branch differs from the repo default, setup asks which one to branch from.
- **Isolated git worktrees & deterministic state:** Run metadata, plans, and state live in a dedicated `<topic>-docs` worktree (or `.mission-control/<topic>-docs` in multi-repo workspaces) inside `.run/state.json`, while each task runs in its own isolated implementation worktree and only fast-forwards into the feature branch after passing automated gates and both spec and code-quality reviews.
- **Lean, behavior-focused TDD:** `red-green` enforces strict RED-GREEN-REFACTOR on public behaviors and realistic failure modes (preferring table-driven tests over per-helper boilerplate), and `quality-review` checks correctness and edge cases without demanding redundant tests.
- **Rate-limit & compaction safe:** Workers never silently downgrade models on quota/rate limits—instead, the run pauses cleanly with all state saved in `.run/state.json`, ready to resume in a new session.

---

## How to Use It

### Prerequisites

- `git` and `python3`
- At least one supported CLI installed and signed in: **Antigravity** (`agy`) and/or **Claude Code** (`claude`)
- *(Optional, for JIRA keys)* An Atlassian MCP server connected on your active harness—setup checks this automatically when your prompt includes a JIRA key.

### 1. Run a full end-to-end feature (`mission`)

Invoke `mission` from a single git repo or from a parent directory containing multiple repos, with either a JIRA key or written requirements (and an optional base branch or repo subset):

> "Use `mission` to implement PROJ-1234."
>
> "Use `mission` to add OAuth2 PKCE login with refresh token rotation and integration tests, starting from `release/2.4`."
>
> "Use `mission` across `api` and `web` to add cursor pagination for `/orders`."

**What to expect:**
1. **Role, repo & base-branch setup:** On first run, the Capsule Communicator detects installed CLIs/models and asks you to pick `<harness>:<model>@<effort>` for Flight Director, Range Safety, and Crew, then saves your choices to `~/.config/mission-control/roles.json` (on later runs it asks `same` / `new`). You can also name explicit model/effort picks in your opening prompt to confirm all three in one shot. It then detects your repo(s) and confirms the repo scope (in multi-repo workspaces) and base branch (if you're on a non-default branch).
2. **Design & plan sign-off:** The Flight Director asks clarifying questions one at a time, runs automated Range Safety reviews, and presents the spec and task plan for your approval before any production code is written.
3. **Hands-off execution:** Each task is implemented by Crew, tested, reviewed, and merged automatically. You are only interrupted if a genuine product decision is needed or a quota limit is hit.
4. **Landing:** Once all tasks pass final verification and `spec-drift`, `touchdown` asks whether to open a PR, fast-forward merge locally, keep the branch, or discard.

### 2. Execute an existing plan (`launch-plan`)

Already have a markdown plan (or a `tasks/` directory with `flight-manifest.txt`)? Skip design and jump straight into execution, review, and landing:

> "Use `launch-plan` on `docs/my-feature-plan.md`."

### 3. Resume an interrupted run

If your chat session compacts, disconnects, or pauses on a quota/rate limit, start a new session in the same repo or multi-repo workspace directory and say:

> "Continue my `mission` run for `<topic>`."

The Capsule Communicator locates `.run/state.json`—either in the `<topic>-docs` worktree (single-repo) or via `scripts/workspace.py list-runs` under `.mission-control/<topic>-docs/` (multi-repo)—and resumes from the exact step where it stopped without losing completed tasks. Role picks stay fixed for an in-progress run (to change picks, start a new run).

### 4. Use individual skills standalone

Every stage works on its own in your current chat session without starting the full `mission` orchestrator:

- **Early ideation (`wind-tunnel`):** *"Use `wind-tunnel` to stress-test my idea for multi-tenant rate limiting."* Runs all 4 steps (deep questioning, 7-lens parallel critique, refinement, `concepts_brief.md`).
- **Spec & planning (`mission-brief`, `flight-plan`):** *"Use `mission-brief` to design webhook retries"* or *"Use `flight-plan` to break `docs/plans/webhooks.md` into TDD tasks."*
- **Document review (`flight-readiness`, `cross-check`):** *"Run `flight-readiness` on `docs/design.md`"* for a 5-stage design review, or *"Run `cross-check` on `docs/plan.md`"* for an adversarial multi-reviewer audit loop.
- **Coding & verification (`red-green`, `gatecheck`, `spec-drift`):** *"Use `red-green` to fix the token expiry bug"*, *"Use `gatecheck` before claiming tests pass"*, or *"Run `spec-drift` against `docs/design.md`."*
- **Landing (`touchdown`):** *"Use `touchdown` to verify and open a PR for this branch."*

---

## Bundled Skills

| Skill | When to use | Execution style |
| --- | --- | --- |
| `mission` | End-to-end flow from JIRA key or written requirements through design, plan, execution, verification, and PR | Capsule Communicator + headless `agy -p` / `claude -p` workers |
| `launch-plan` | Execute an existing markdown plan (or `tasks/` + `flight-manifest.txt`) through execution, verification, and landing | Capsule Communicator + headless `agy -p` / `claude -p` workers |
| `mission-brief` | Clarify requirements, weigh approaches, and write a design doc | Interactive in-session |
| `flight-plan` | Break a spec or design doc into TDD tasks and verification steps | Interactive in-session |
| `wind-tunnel` | Full 4-step ideation + 7-lens stress-testing (`concepts_brief.md`); Step 2 is also used automatically inside `mission` | Fresh-context subagent lenses |
| `flight-readiness` | 5-stage design document review (zoom-out, comprehension, critique, readiness) | Fresh-context subagent reviewers |
| `cross-check` | Adversarial multi-reviewer audit loop on specs, plans, or docs | Fresh-context subagent reviewers |
| `spec-drift` | Compare finished source code against design docs | In-session / drift check |
| `red-green` | Strict RED-GREEN-REFACTOR test-first workflow (lean, behavior-focused tests) | In-session or worker |
| `gatecheck` | Require fresh command output before claiming success | In-session or worker |
| `touchdown` | Verify tests and complete via PR, fast-forward local merge, keep, or discard | In-session or Capsule Communicator final step |

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

`mission-control` includes `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` for Claude Code plugin discovery:

```sh
claude plugin marketplace add marwanehab98/mission-control
claude plugin install mission-control@mission-control
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
bash install.sh --ref <commit-or-tag>    # install an older version (rollback)
python3 scripts/harnesses.py install-dirs  # detected harnesses and their skills folders
```

Each install records a `VERSION` file naming its commit. Replaced versions are kept in `~/.local/share/mission-control/backups/<harness>` (newest 5 each).

---

## Tests & Validation

```sh
bash tests/run_all.sh
agy plugin validate --strict --json .
claude plugin validate .
```

---

## License & Attributions

Original code in this repository is licensed under the [MIT License](LICENSE). Vendored and adapted skills, agent personas, and document standards retain their respective upstream licenses (MIT and Apache-2.0) — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for full license texts and attributions.
