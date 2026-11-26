# Capsule Communicator: Step 2: Design phase

Part of mission-control. Everything in `<SKILL_DIR>/references/capsule-communicator.md` ("How you work", "Messages", "State keys") and the Rules in `<SKILL_DIR>/skills/mission/SKILL.md` hold here.

Handle `run_director.py`'s `RESULT` file (`<saved>`) by branching on its first line:

- **`RESULT: NEED ...` or `RESULT: COMMITTED`:** run `DESIGN --result <saved>` and branch on `DESIGN`'s last line:

  | Last line (exit) | Do |
  | --- | --- |
  | `DIRECTOR_JOB <job> <input>` (20) | Send the Flight Director job `<job>` with `INPUT=<input>` (everything after the job name), unchanged: `DIRECTOR send --state <RUN_DIR>/state.json --job <job> --input '<input>' --pair <ROLE_DIRECTOR>`. Its next `RESULT` goes through the list above. |
  | `CONSULT <ask_file>` (22) | Send Flight Director job `consult` with `INPUT=<ask_file>` via `DIRECTOR send` (`--job consult --input '<ask_file>'`). On `RESULT: ANSWER <answer_file>`, run `DESIGN --answer <answer_file>` and branch again. |
  | `USAGE_LIMIT <pair> <detail>` (31) | The preflight-check pair's test launch hit its model's usage limit; nothing failed. Show the usage-limit prompt of Step 3 item 7 (in `<SKILL_DIR>/references/capcom/step-3-setup.md`) exactly, with this `<pair>` and `<detail>`. "retry" runs `DESIGN` (no arguments: it tests the launch again) and branches again; "switch", "pause" and "stop" are handled as there. |

- **`RESULT: PLAN_APPROVED <flight-manifest>`:** go to Step 3.
- **Any other `RESULT:` line** answers the job that asked for it ("Messages", "Flight Director lost").

`run_design.py` checks the docs worktree, test-launches the preflight-check pair, runs each worker synchronously, retries a failed one once, counts runs, and writes the `-done` job's input, including `failed: <file>` after a second failure. You never do any of that in this step, and never read a design worker's output.

