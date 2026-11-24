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
  - `scripts/common.py` logs concise progress for both harnesses: tool calls with their main argument, short snippets of what the worker says, and failed tool calls (plus to-do updates for `claude`). An `agy` worker's message streams as `step_update` events with `text_delta` pieces until its `DONE` event, and a failed tool step has `state: ERROR` with `tool_info.error.message` (captured 2026-11-23; fixture `tests/fixtures/harness/agy_stream.ndjson`). `agy` reports tokens and duration but no cost and periodic heartbeats (`WORKER_HEARTBEAT`, default `30`s) to `<RUN_DIR>/logs/<worker>.log` and `<RUN_DIR>/progress.log`. Only milestones (run events, worker start and finish with cost and turns) also go to `stderr`, unless `PROGRESS_STDERR=all`. It falls back to `--output-format json` automatically if a CLI rejects `stream-json` (or when `WORKER_STREAM=0`).
  - Terminal result fields:
    - `conversation_id`: string ID used to resume the session in a subsequent `agy -p ... --conversation <id>` call.
    - `status`: terminal status (`SUCCESS`, `ERROR`, `CANCELED`, `INTERRUPTED`, `INVALID`, `WAITING`, `RUNNING`).
    - `response`: the model's response text for that turn. It joins every non-empty agent message of the turn with `\n`, including narration written before a tool call and a message written before a background command ended (a worker that ends its turn while a background shell command it started still runs gets no result yet: `agy` prints `root agent idle; waiting for 1 background task(s) to finish` on `stderr`, a `system_message` step wakes the agent when the command ends, and the one `result` carries both messages, e.g. `"WAITING\nFAILED: test marker\n"`; a `system_message` step also appears in ordinary turns). Live runs 2026-11-24, `agy` 1.5.15. So a marker line can sit behind earlier text: `AntigravityAdapter.parse_stream_event` in `scripts/harnesses.py` keeps the text of the turn's last non-empty `agent_response` step, and when the stripped `response` ends with it and has something in front of it, `scripts/common.py` uses that final message as the worker's response (for the `ASK:` / `FAILED:` check and the saved body, whose first line `RESULT:` and other readers use) and logs `dropped earlier messages before the final one`. Otherwise the response is used as is (also with `WORKER_STREAM=0`, which has no step events).
    - `error`: error message when `status != "SUCCESS"`.
- **Working directory:** `agy -p` operates in its process `cwd` (`DOCS_WORKTREE` for design/Flight Director workers, `IMPL_WORKTREE` for Crew/review/final-review/drift workers).
- **Permissions** (`Adapter.permission_args` in `scripts/harnesses.py`): workers that edit and commit (Crew, the Flight Director) run with `--dangerously-skip-permissions` in their worktree, so unattended runs never stall on prompts. Only the Flight Director keeps the user's MCP servers (JIRA jobs). `agy` has no flag that stops servers from starting, so Crew also get `--settings '{"permissions":{"deny":["mcp(*)"]}}'`: the servers start, but every MCP tool call is denied (the changelog says explicit deny rules hold under `--dangerously-skip-permissions`; verified live 2026-11-24 with this Crew profile: the call was refused with `Permission denied for mcp(atlassian-mcp-server/atlassianUserInfo)`). Read-only workers (reviewers, design workers, probes) on `agy` run with `--sandbox --settings '{"toolPermission":"proceed-in-sandbox","enableTerminalSandbox":true,"allowNonWorkspaceAccess":false,"permissions":{"allow":["read_file(*)","write_file(<RUN_DIR>)"],"deny":["read_url(*)","execute_url(*)","mcp(*)"]}}'` instead (the Range Safety design review also gets a `write_file` rule for the docs-worktree folder that holds its findings file; no other design worker writes in the docs worktree). There is no `--add-dir`: when the added folder lies in a worktree of the same repository (`RUN_DIR` in the docs worktree), the worker's shell starts there instead of in its own worktree. Without it the shell starts in the worker's own folder, and both the file tool and the shell can still write the output folder named by the `write_file` rule (verified live 2026-11-24). Verified live (2026-11-23, outside targets under `$HOME`): normal review commands, `git diff` and writing the output file work; network, file-tool writes and shell writes outside the workspace are denied; reading outside the workspace works. Facts learned on the way: `/tmp` stays writable inside the sandbox; a strict `request-review` allow list ends the run with no output at the first command not on the list; headless `agy` soft-denies anything that would prompt, and that also ends the run; `--dangerously-skip-permissions` would approve sandbox bypasses (antigravity-cli#36), so it is never combined with `--sandbox`. There is no fallback: `harnesses.py detect` records `read_sandbox` (whether `agy --help` lists `--sandbox` and `--settings`; for `claude`, `--permission-mode`, `--allowedTools` and `--strict-mcp-config`), and `roles.py` / `validate_meta.py` refuse a harness without them. Read-only is also enforced after each worker by `scripts/check-tree.sh` and `scripts/discard-changes.sh`, and read-only workers get an environment without credential-like variables (`WORKER_ENV_KEEP` keeps named ones). A run whose `RUN_DIR` has `( ) * ? [ ] \` or a control character is refused with an error on `agy` too, as on `claude` (unit-tested, not run live).

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
- **Saved memory:** every `claude` worker (read-only, Crew, Flight Director) runs with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, so it neither reads nor writes the user's saved memory under `~/.claude/projects/<project>/memory`, and a note from another ticket can no longer override the run's own decisions. Verified live 2026-11-24 with Claude Code 2.1.331: a made-up word in a scratch project's memory reached the worker without the variable and not with it, and nothing was written to the memory folder. The repo's `CLAUDE.md` still loads with memory off (verified live the same day: a read-only worker replied with a made-up word that existed only in its project's `CLAUDE.md`).
- **Cost of a resumed conversation:** the result envelope of a resumed `claude` conversation reports the conversation's running total, so `scripts/common.py` logs each worker line with only that turn's share, and the total in `costs.md` is the real total. A conversation started by an older plugin version logs the running total on its first resumed turn.
- **Working directory & permissions:** `cwd` is the target worktree. The Flight Director uses `--dangerously-skip-permissions`; Crew use `--dangerously-skip-permissions --strict-mcp-config` (no MCP servers start). Read-only workers use `--permission-mode dontAsk --allowedTools <reading, Write/Edit under RUN_DIR (and, for the Range Safety design review, the docs-worktree folder that holds its findings file), subagents, skills, read-only git, with `git -C` only on the run's repos> --disallowedTools <git options that write files or run programs> --strict-mcp-config`: no general shell, no network, no MCP servers, and anything else is denied without a prompt (verified live: `git diff` and writing the output file work; `curl`, `touch` and a write elsewhere are denied). Rules match a command as typed: `git -C` takes a repo path bare (when it needs no quoting) or quoted (`"..."` or `'...'`; rules are written for each), a path with a space only quoted, and the options that write files or run programs (`--output`, `-c`, `--ext-diff`) stay denied with a quoted path too (verified live); a path with `( ) * ? [ ] \` or a control character cannot be named in a rule (`git -C` on it stays denied), and a run whose `RUN_DIR` has such a character is refused with an error. `check-tree.sh` and `discard-changes.sh` still enforce read-only afterwards.

## Model pinning and launch styles

- `agy models` lists available model slugs (`<model>-<effort>` for effort variants, or bare slugs with `@default`). `claude --help` advertises supported `--effort` levels; `scripts/harnesses.py` exposes `opus`, `sonnet`, and `haiku`.
- `scripts/harnesses.py` tries `slug` (`--model <model>-<effort>`) first, then `flag` (`--model <model> --effort <effort>`) for Antigravity, and `flag` for Claude Code.
- `scripts/probe.py` verifies each pair synchronously at the start of the run and records the winning style in `LAUNCH_STYLE_<pair key>`.
- Unlike interactive TUI mode, headless workers fail loudly if `--model` or `--effort` is invalid rather than silently falling back to another model.

## Turn-based relays and consults

- Because headless invocations run one turn to completion per invocation, workers do not pause mid-turn on an external mailbox:
  - **Flight Director -> Capsule Communicator:** ends the turn with `RESULT: RELAY <path>` or `RESULT: NOTIFY <path>` as line 1 of its response; `scripts/run_director.py` returns the relay/notify path to the Capsule Communicator, and resumes `DIRECTOR_CONVERSATION_ID` with the user's answer via `--conversation` (`agy`) or `--resume` (`claude`).
  - **Other workers -> Capsule Communicator:** if blocked on an ambiguity, respond with `ASK: <question>` on line 1; the loop script exits `22` (`CONSULT <ask_file>`) and resumes the worker (or falls back to a fresh worker run with the answer appended) once the Flight Director answers.

## Interactive session background commands (Antigravity Capsule Communicator)

- Confirmed live in an interactive Antigravity session (2026-11-24) for `run_command` behavior when running as the Capsule Communicator:
  - **`WaitMsBeforeAsync` cap:** `run_command` limits synchronous foreground waiting to `500`–`10000` ms (10 seconds maximum); any command still running past that window is moved to a background task. Commands intended to run in the background (long loop scripts, `DIRECTOR`, and `WATCH`) use `WaitMsBeforeAsync=500`.
  - **Reactive wakeup on completion:** When the agent ends its turn after launching background commands, Antigravity automatically resumes it and delivers each task's exit code and output via a system message when the task finishes—the same completion-driven model as Claude Code's `run_in_background`, so the background `WATCH` protocol in `references/capsule-communicator.md` works on both harnesses without a foreground polling mode.
  - **`NotificationTimeoutSeconds`:** Leave unset on `WATCH` and long script calls; setting a short value triggers "still running" wake-ups before `WATCH` has news or reaches its own `--wait` interval.
