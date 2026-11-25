# Flight Director: JOB: report

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: report

The input is the Capsule Communicator's mission log, then the run's cost summary (`costs.md`). Write `RELAY_DIR/report.md` for the user. It covers:
- what was built;
- the verification evidence;
- the PR link, or, when there is none, how the branch landed (the mission log says): merged locally into the target branch, or kept as the feature branch. After a local merge the feature branch no longer exists (the Capsule Communicator deletes it), so never suggest pushing it or opening a PR from it;
- the JIRA update, or "n/a";
- which tasks escalated, and to which rung;
- the drift check: ran (what it found and what was fixed), declined, or couldn't run;
- every open Minor finding from the task reviews and the final review;
- what the run cost: copy the cost summary's total and both tables as they are;
- anything left undone.

Commit `DECISIONS_LOG`, then settle `RESULT: REPORT <path>`.

