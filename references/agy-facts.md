# Confirmed headless CLI behavior (`agy` & `claude`)

Part of mission-control. Checked against the official Antigravity CLI headless mode documentation (`https://antigravity.google/docs/cli/headless/`) and Claude Code CLI `--help`. Re-check after any CLI update, since flags and defaults change.

## Headless invocation (`agy -p`)

- **Command shape:**
  ```sh
  agy -p "<prompt>" \
    --output-format stream-json \
    --dangerously-skip-permissions \
    --model <slug_or_base> [--effort <effort>] \
    [--conversation <conversation_id>]
  ```
- **Streaming NDJSON & result envelope:**
  - `agy -p --output-format stream-json` emits NDJSON events (`init`, `step_update` with `step_type` / `tool_name` / `tool_info` / `state`, and a terminal `result` event whose payload matches `--output-format json`).
  - `scripts/common.py` logs concise tool/step progress and periodic heartbeats (`WORKER_HEARTBEAT`, default `30`s) to `<RUN_DIR>/logs/<worker>.log` and `stderr`, and falls back to `--output-format json` automatically if a CLI rejects `stream-json` (or when `WORKER_STREAM=0`).
  - Terminal result fields:
    - `conversation_id`: string ID used to resume the session in a subsequent `agy -p ... --conversation <id>` call.
    - `status`: terminal status (`SUCCESS`, `ERROR`, `CANCELED`, `INTERRUPTED`, `INVALID`, `WAITING`, `RUNNING`).
    - `response`: the model's response text for that turn.
    - `error`: error message when `status != "SUCCESS"`.
- **Working directory:** `agy -p` operates in its process `cwd` (`DOCS_WORKTREE` for design/architect workers, `IMPL_WORKTREE` for implementation/review/final-review/drift workers).
- **Permissions:** `--dangerously-skip-permissions` approves tool calls (edits and commands) inside the isolated git worktree so unattended runs never stall on interactive permission prompts. Read-only constraints are enforced deterministically after each worker by `scripts/check-tree.sh` and `scripts/discard-changes.sh`.

## Headless invocation (`claude -p`)

- **Command shape:**
  ```sh
  claude -p "<prompt>" \
    --output-format stream-json \
    --verbose \
    --dangerously-skip-permissions \
    --model <alias> [--effort <effort>] \
    [--resume <session_id>]
  ```
- **Streaming NDJSON & result envelope:**
  - `claude -p --output-format stream-json --verbose` emits NDJSON events (`system` init, `assistant` tool calls, `user` tool results) and ends with the terminal `type: "result"` envelope:
    - `type`: `"result"`
    - `subtype`: `"success"` or an error subtype (e.g. `"error_during_execution"`).
    - `is_error`: boolean indicating whether the turn ended in an error.
    - `result`: the model's response text for that turn.
    - `session_id`: string ID used to resume the session in a subsequent `claude -p ... --resume <session_id>` call.
    - `errors` (optional): list of error strings when `is_error` is true.
- **Model aliases and efforts:** `opus`, `sonnet` (effort levels parsed from `claude --help`, e.g. `low..max`), and `haiku` (`@default`), launched with `flag` style (`--model` + optional `--effort`).
- **Working directory & permissions:** same as `agy -p` (`cwd` is the target worktree; `--dangerously-skip-permissions` avoids interactive prompts; `check-tree.sh` and `discard-changes.sh` enforce read-only constraints).

## Model pinning and launch styles

- `agy models` lists available model slugs (`<model>-<effort>` for effort variants, or bare slugs with `@default`). `claude --help` advertises supported `--effort` levels; `scripts/harnesses.py` exposes `opus`, `sonnet`, and `haiku`.
- `scripts/harnesses.py` tries `slug` (`--model <model>-<effort>`) first, then `flag` (`--model <model> --effort <effort>`) for Antigravity, and `flag` for Claude Code.
- `scripts/probe.py` verifies each pair synchronously at the start of the run and records the winning style in `LAUNCH_STYLE_<pair key>`.
- Unlike interactive TUI mode, headless workers fail loudly if `--model` or `--effort` is invalid rather than silently falling back to another model.

## Turn-based relays and consults

- Because headless invocations run one turn to completion per invocation, workers do not pause mid-turn on an external mailbox:
  - **Architect -> Coordinator:** ends the turn with `RESULT: RELAY <path>` or `RESULT: NOTIFY <path>` as line 1 of its response; `scripts/run_architect.py` returns the relay/notify path to the coordinator, and resumes `ARCHITECT_CONVERSATION_ID` with the user's answer via `--conversation` (`agy`) or `--resume` (`claude`).
  - **Other workers -> Coordinator:** if blocked on an ambiguity, respond with `ASK: <question>` on line 1; the loop script exits `22` (`CONSULT <ask_file>`) and resumes the worker (or falls back to a fresh worker run with the answer appended) once the architect answers.
