# Flight Director: JOB: drift-done

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: drift-done

The input is the drift check's findings file, or `failed: <file>` when the check couldn't run (read the file). Every Minor finding goes into `DECISIONS_LOG` under "Open Minor findings".
- **`failed:`:** tell the user in a `NOTIFY`, in one or two sentences, that the drift check couldn't run and why, note it in `DECISIONS_LOG`, commit, and settle `RESULT: FINAL_CLEAN`.
- **A Critical or Important finding that is real drift** (the design or a decision says one thing and the code does another, or something it promises is missing): write one final-fix task for all of them, as in "JOB: final-review", commit, and settle `RESULT: FINAL_FIX F<n>`. The drift check doesn't run again; the final review after the fix does.
- **Otherwise** (no drift, only Minor findings, or findings that aren't drift, each dismissed with a one-line reason in `DECISIONS_LOG` under "Drift check: dismissed"): commit, and settle `RESULT: FINAL_CLEAN`.

