# Flight Director: JOB: baseline-failed

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: baseline-failed

The input is a log of the `VERIFY_` (or `GATE_`) commands failing on the run's start commit, before any task ran. Every task's check would fail the same way, so this must be settled first.
1. Read the log. Decide whether each failure is a wrong command (a typo, the wrong folder, a missing project-local setup step the project declares, such as `npm ci`), a missing tool, or a failure that already exists on `BASE_BRANCH`.
2. Fix wrong commands in `flight-manifest.txt`. You may run a fixed command in `DOCS_WORKTREE` to check it; delete anything it creates there afterwards.
   - **A check that fails only because the start commit lacks files a task will create** (a new project, a test file no task has written yet): add its name to `BASELINE_SKIP` and leave the command as it is.
   - **A missing tool is not a wrong command.** Ask the user in a relay to install it, or to drop that check. Never add a step that installs anything outside the worktree.
3. For a failure that already exists, ask the user in a relay, with your recommendation: exclude that check or test from the commands (named precisely, never a whole suite), or stop the run so they can fix the base first.
4. Record every exclusion and every changed command, each with its reason, in `DECISIONS_LOG`, commit, and settle `RESULT: FLIGHT_MANIFEST_FIXED`, or `RESULT: STOP` if the user chose to stop.
