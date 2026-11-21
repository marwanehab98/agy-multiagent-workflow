# mission-control

A self-contained, multi-skill plugin for Antigravity and Claude Code supporting spec-driven development and multi-agent execution via headless `agy -p` and `claude -p` workers (including mixed cross-harness runs). Zero external Superpowers or EGM plugin dependencies are required.

Each workflow stage is exposed as its own independently invocable skill, alongside the full end-to-end `mission` orchestrator and `launch-plan`.

## Bundled Skills

| Skill | When to use | Execution style |
| --- | --- | --- |
| `mission` | End-to-end flow from JIRA key or written requirements through design, plan, execution, verification, and PR | Coordinator + headless `agy -p` / `claude -p` workers |
| `launch-plan` | Execute an existing markdown plan (or `tasks/` + `run-values.txt`) through Steps 3–8 | Coordinator + headless `agy -p` / `claude -p` workers |
| `draft-spec` | Clarify requirements, weigh approaches, and write a design doc | Interactive in-session |
| `flight-plan` | Break a spec or design doc into TDD tasks and verification steps | Interactive in-session |
| `crucible` | Ideation + 7-lens Goldfish stress-testing (`concepts_brief.md`) | Fresh-context subagent lenses |
| `gauntlet` | 5-stage design document review (zoom-out, comprehension, critique, readiness) | Fresh-context subagent reviewers |
| `crossfire` | Antagonistic multi-reviewer audit loop on specs, plans, or docs | Fresh-context subagent reviewers |
| `spec-drift` | Compare finished source code against design docs | In-session / drift check |
| `red-green` | Strict RED-GREEN-REFACTOR test-first workflow | In-session or worker |
| `gatecheck` | Require fresh command output before claiming success | In-session or worker |
| `touchdown` | Verify tests and complete via PR, fast-forward local merge, keep, or discard | In-session or coordinator Step 8 |

## Installation

### Option 1: Antigravity Plugin (recommended)

```sh
agy plugin install https://github.com/marwanehab98/mission-control
```

Validate locally before installing:

```sh
agy plugin validate --strict .
```

### Option 2: Claude Code Plugin

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

## Tests

```sh
bash tests/run_all.sh
```
