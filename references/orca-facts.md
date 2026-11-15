# Confirmed Orca and agy behavior

Part of orca-multiagent-workflow. These facts were confirmed on 2026-11-13 against Orca's orchestration guide, its recovery reference, and `--help` for `worker-release`, `worker-start`, and `worktree rm`. Re-check them after any Orca or `agy` update, since flags and defaults change. Commands still come from the guide and `--help` first (Rule 2).

## EGM and models

- The EGM command is `/egm-review`, and it accepts a design doc in `docs/plans/`. EGM's Goldfish run on Hydrogen.
- `gemini-4-hydrogen` is available on this Google AI Pro account.
- **Historical (2026-11-14), superseded by "Launch styles" below. Effort goes in the model slug, passed straight to `--model`** (tested live 2026-11-14). `worker-start --agent antigravity --model gemini-4-hydrogen-high` (and `-xhigh`, `-low`) launches a structured Antigravity worker, and Orca records `options.model` as the slug. No custom agent command is needed.
  - The runtime validates model ids: a made-up id fails at once with "Model '<id>' is not available for the current authentication method", reported as a `lost` escalation. So an accepted slug really is that model.
  - `--effort` is accepted only with the bare id (`--model gemini-4-hydrogen --effort high|low`). Orca refuses `xhigh` that way, and refuses any `--effort` on a slug ("does not support effort xhigh"). Don't use `--effort` for Antigravity.
  - Orca runs its own bundled Antigravity runtime (`~/.config/orca/agent-runtimes/antigravity/1.4.1`), not the `agy` CLI (1.5.8 here). Its catalog (`agent-model-catalog.db`) lists `gemini-4-hydrogen` with no known efforts. `agy models` is still the list the slugs come from.
  - A worker's prompt says "EFFORT LEVEL: 0.50" whatever the model or effort, so a worker can't tell you its effort.
- The EGM skills (`egm-brainstorm`, `egm-review`, `egm-review-loop`) set `disable-model-invocation: true`, so the templates give workers each `SKILL.md` path to read instead of naming the skill. `egm-review` takes `Step: uninterrupted` to skip its preset gate. Its severities are Blocker, Major and Nit.
- The `orca-cli` handoff can target Claude Code on `sonnet`.
- **Setup handoff** (checked 2026-11-15 against the `orca-cli` guide and `orca agent launch --help`): the guide's handoff for a fresh agent in an existing worktree is `ORCA agent launch --agent <id> [--worktree <selector>] --prompt <text> --model <id> --effort <level>`, so SKILL.md "Start here" uses it with `harnesses.py args`, not the `worker-start --spec-file` fallback. `--effort` requires `--model`; an Orca host too old for them refuses `--model`/`--effort`.
- **`run-create` binds a run before any worker:** `ORCA orchestration run-create --objective <text>` creates and binds a Run (from `--help`). Coordinator Step 1 runs it before the test launches, because `probe.py` creates tasks with `task-create`, which fails with `run_required` until a Run is bound.

## Launch styles (2026-11-15)

- **Launch style is decided per run, not by these notes.** Each harness adapter in `scripts/harnesses.py` lists its launch styles in preference order: Antigravity tries `slug` (`--model <model>-<effort>`), then `flag` (`--model <model> --effort <effort>`); Claude Code has only `flag`. `scripts/probe.py` test-launches every pair the run uses through Orca and records the first style whose worker settles `succeeded` as `LAUNCH_STYLE_<pair key>` in the run's state file. Every later launch of that pair uses it, through `harnesses.py args`.
- So when Orca or a harness changes how it takes effort, the probe finds out at the start of the run, and the run stops with Orca's error text if no style works. Nothing in the rules needs editing.
- Models and efforts come from each harness's own CLI (`agy models`, `claude --help`), never from Orca's model catalog.
- **Live check (2026-11-15, run `run_e6ed59b6a572`, topic `add-cli-sum`, Orca with `claude` 2.1.323 and `antigravity` 1.5.9; full list of defects in `docs/2026-11-15-live-test-issues.md`):**
  - **Launch styles that passed:** `claude:sonnet@low` and `claude:sonnet@medium` use `flag`. `antigravity:gemini-4-hydrogen@low` and `@xhigh` (the `BASE_TOP` pair) use `slug`. Antigravity never fell back to `flag`. All probes settled `succeeded`, and `.run/run-log.md` records `launch style <style> works` for each.
  - **Claude haiku takes no effort in Orca.** Orca's own agent catalog gives `haiku` an empty options list, and `worker-start --agent claude --model haiku --effort medium` fails with `invalid_argument` ("Agent claude model haiku does not support effort medium"). The `claude` CLI itself accepts `--model haiku --effort low|medium`. The setup catalog still lists all five efforts for haiku, because `harnesses.py` reads one effort list from `claude --help` for every model, so the "never from Orca's model catalog" rule above misses this case. And `claude:haiku@default`, the pair Orca does accept, is rejected by `validate_meta.py harness-pair`. A haiku pair therefore can't be used at all until this is fixed.
  - **Probes and the docs worktree:** each probe leaves the docs worktree clean, but `probe.py:157` discards any untracked file in it, including a requirements file written too early (issue 1).
  - **`run-create` JSON:** `{"ok":true,"result":{"runId":"run_...","objective":"..."}}`. The run id is `result.runId`.
  - **`agent launch` for the handoff:** `--agent claude --model sonnet --effort low --prompt ... --json` works. The first launch was `outcome.kind: structured` with `prompt.outcome: journaled`. The resume launch, later in the same session, was `terminal` with `handed-to-terminal`. Which surface you get follows the user's Chat | CLI setting.
  - **`harnesses.py skill-dir`** returns the skill path inside the Claude profile (`~/.config/orca/claude-profiles/<id>/home/skills/...`), not `~/.claude/skills/...`.
  - **Design phase ran end to end:** lens pass, builder check (with its own probe of `claude:sonnet@low`), EGM review and plan review all dispatched and settled on the chosen pairs. The architect's commits survived each script step.
  - **Task loop and finish ran end to end** on `antigravity:gemini-4-hydrogen@low` (implementer) and `claude:sonnet@medium` (both reviewers and the final reviewer): stage 1 returned FIX once, the fix round and a second stage 1 passed, stage 2 passed, `merge-task.sh` merged `2ae409e` into the `add-cli-sum` branch, the final review passed and verification exited 0. The finished tool passes all its requirements. The user chose "keep the branch".
  - **Not run:** the optional stress checks (a message while an EGM review worker runs, `--docs-moved`; a resume during a builder-check test launch).
  - **Second run (2026-11-15, `run_cb947add77b4`):** `claude:haiku@default` launches with `--agent claude --model haiku` and no `--effort` (style `flag`), and settles. No hand edits were needed.
  - **Hand edits needed to finish (first run):** `CLERICAL_PAIR` changed from `claude:haiku@medium` to `claude:sonnet@low` (issue 7), and `__pycache__/` added to `.git/info/exclude` (issue 10).

## Session mailboxes (tested 2026-11-13 from an Orca chat session)

- Every agent session has the Orca mailbox `orca_session_id:<ORCA_AGENT_SESSION_ID>`. That env var is set in Orca sessions.
- `ORCA orchestration send --to orca_session_id:<id> --type question ...` returns `{"ok":true}` with a message id. Orca delivers it into the target chat session, even mid-turn, together with the exact `orchestration reply --id <msg> --from <mailbox>` command.
- `orchestration reply` delivers the answer back to the sender the same way.
- Untested: delivery into a terminal-surface (non-chat) session, and behavior when the target session is closed.

## Kept-open workers (tested 2026-11-14 with a Claude worker on haiku)

- `worker-start ... --agent claude --model haiku --keep-open` started a `mode: "structured"` worker, which is a chat. With this user's settings, Claude workers open as chats. `--model` set the model; no custom agent command was needed.
- After `worker_done`, a kept-open worker stays `terminalState: retained`. `send --to dispatch:<id>` then delivers as `follow_up` into the same conversation. The worker remembered an earlier instruction and settled the follow-up normally. Each follow-up gets a new dispatch id, so always send to the latest one.
- After `worker-release`, a `send` reports `delivery: reopened`. The reopened worker kept its conversation history, but Orca rejected its `worker_done` with `worker_identity_changed`, so the task could not settle and needed `worker-stop`. Never release a worker you still need to talk to.
- `worker-start --help` lists `--model` for Claude, Codex, Cursor, Antigravity, Grok and Muse agents. `agy` slugs such as `gemini-4-hydrogen-high` work with it (tested 2026-11-14; see "EGM and models" above).
- `--worktree current` fails with `selector_not_found` when the session is not inside an Orca workspace.

## Mail, waiting, and resuming (from `--help`, 2026-11-14)

- `ORCA orchestration inbox --terminal run:<run_id> --limit <n> --full --json` returns the run's mail newest first. Each message has `id`, `type` (`worker_done`, `question`, `escalation`, ...), the full `body`, and a JSON `payload` string with `dispatchId` and, for a settle, `outcome`. `scripts/save_message.py` reads this; tested against real run mail.
- `ORCA orchestration check --peek --wait --timeout-ms <n>` blocks until mail arrives without marking it read. A consuming `check` belongs to the agent itself; a script must only peek.
- **`ORCA orchestration run-use --id <run_id>` takes over a run** (tested live 2026-11-14). A fresh session that isn't a worker of the run binds it, and the old coordinator becomes unbound (`run-current` gives `runId: null`). A worker started before the takeover sends its `worker_done` to the new coordinator only. The new coordinator can `task-create` and `worker-start` at once.
- **`stopReason: "finished"` doesn't mean the worker is gone.** A Claude worker that runs a long command in the background ends its turn, and Orca reports "ended its turn without sending worker_done". The worker resumes when the command finishes and settles normally. `run_task.py` nudges such a worker with `send` (twice at most) instead of failing it. `stopReason: "lost"` (or `exited`) means it is gone.
## Tested live 2026-11-14 (Haiku workers, scratch repo `omw-smoke`)

- `task-create` fails with `run_required` until a Run is bound to the coordinator. In this workflow the architect's first `worker-start --spec-file` binds it.
- The `worker-start --json` receipt has `result.dispatchId`, `taskId`, `runId` and `terminal` (the handle), plus `keptOpen` when `--keep-open` is given.
- `worker-show --json` has `result.state` (`ready`, then `succeeded` or `failed`), `stage` (`input_accepted`, then `settled`), `liveness` (`live`/`exited`), and `terminalState`.
- **Reuse works:** a kept-open worker is `retained` after `worker_done`. `worker-start --task <new task> --worktree path:<wt> --terminal <handle>` sends the new task to the same chat, with no new agent launched, and gives a new dispatch id. The worker settles it normally.
  - After a reuse, the new dispatch owns the terminal. The old dispatch shows `terminalState: null`, and releasing it returns `ownership_transferred`, which does nothing. Always track the latest dispatch.
- Escalation payloads carry `failureKind`, e.g. `usageLimitReached` with a `detail` such as "You've hit your 5-hour limit". `run_task.py` pauses on it rather than escalating the task.
- `ORCA worktree create --base-branch <commit sha>` works; the worktree's head is that commit.
- `scripts/run_task.py` ran one full task end to end against real Orca: implement, gate, both reviews, and merge by `update-ref`, with the main checkout untouched.
- Side effect: a worker may install missing tools itself. A Haiku worker ran `pip install --user --break-system-packages pytest`.

## Worktrees

- `ORCA worktree create --name <name> --base-branch <base>` names the branch `<name>` (profile `branchPrefix: "none"`) and adds a numeric suffix on collision. `--branch <name>` sets the name exactly. `autoRenameBranchFromWork: true` may rename branches later.
- `ORCA worktree rm --worktree path:<path>` removes a worktree.
  - `current` and `active` worktrees are refused.
  - `--delete-branch` deletes only provably merged branches.
  - `--force` allows a dirty worktree.
  - `orca.yaml` archive hooks run only with `--run-hooks`.

## Workers

- `worker-start` takes exactly one of:
  - `--task <task_id>`: binds to an existing task;
  - `--spec <text>`;
  - `--spec-file <path|->`.

  The last two create a new task, and a run bound to the coordinator if none is bound; the receipt names the run. `--task-title`, `--deps`, and `--parent` apply to a task created this way.
- `worker-start --worktree path:<path>` runs a new agent in an existing worktree without rerunning setup.
- `--terminal <handle>` reuses an unreleased agent and cancels its pending release.
- `--retry-of <dispatch_id>` requires `--task` naming the failed task. A retry doesn't inherit placement, so `--on`, `--worktree`, and `--agent`/`--terminal` must be repeated. This workflow doesn't use it.
- `worker-release` closes only the agent terminal; the worktree, files, and branch stay. It is safe to repeat.
- Orca auto-releases workers about 2 minutes after `worker_done`. Workers started with `--worktree new-child` or `new-top-level` are the exception.
