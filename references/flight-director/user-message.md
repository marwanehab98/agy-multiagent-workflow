# Flight Director: JOB: user-message

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: user-message

The input is something the user wrote outside a relay. If it changes requirements or decisions:
- record it in `DECISIONS_LOG` and the affected task files;
- reply to the user in a relay if they need an answer.

Commit, then settle `RESULT: NOTED`. An uncommitted record can be wiped when a review worker running in the docs worktree finishes.

