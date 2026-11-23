# Flight Director: JOB: jira-close

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: jira-close

The input is the PR URL. Only for `jira` runs.
1. Comment on the issue with the PR link and a two-line summary.
2. List its transitions and apply the one meaning in review, such as "In Review" or "Code Review".
3. If none clearly fits, leave the status and say so.

Settle `RESULT: JIRA_DONE <what you did>`.

