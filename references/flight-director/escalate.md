# Flight Director: JOB: escalate

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: escalate

The input names the task, its tier, the attempt number, the ladder state (`next rung <pair>`, or `ladder used up`), whether replanning is still allowed, and a history file holding every attempt's failure and review. If the history ends with a "Rejected flight director answer", your last answer was refused; it says why.

1. **Read the history,** then the task's commits with `git -C <DOCS_WORKTREE> log --oneline <base sha>..<head sha>`, using the SHAs the history gives. Worktrees share one repository, so the commits are visible from the docs worktree. Commit subjects and review verdicts are enough; don't read diffs.
2. **Choose one response:**
   - **Another attempt** (the usual answer while rungs remain):
     1. Choose the reset point: the task's base SHA from the history, or the last good step commit if a review confined the failure to a later step.
     2. Write a note to `RELAY_DIR/escalate-<id>-<attempt>.md`: which steps are done, the open findings quoted, and what each attempt tried.
     3. Choose the Crew pair: copy the `next rung <pair>` from the input exactly. `run_task.py` rejects any other pair. Leave `.meta` as it is. When the history suggests that rung will struggle too, for example a misunderstanding rather than a slip, your lever is the note: explain what was misunderstood and why the design decided it that way, so the next attempt has what the last one lacked.
     4. Put any clarification of the task in the note and in `tasks/<id>-decisions.md`, never only in `tasks/<id>.md`. The Crew worker's copy of the task was frozen when it first started, while the decisions file reaches every later worker and reviewer.
     5. Commit, then settle `RESULT: ESCALATE <sha> <note> <pair>`.
   - **Replan** (only when the input says replanning is allowed): do this when the task itself is the problem: too big, wrongly cut, or the approach doesn't work.
     1. Write new tasks (`<id>a`, `<id>b`, …) with all four files each, every `.meta` with its own pairs ("Model choices") and `REPLANNED_FROM=<id>`. They take the stuck task's place, in order.
     2. Record why in `DECISIONS_LOG`.
     3. Commit, then settle `RESULT: REPLAN <reset sha> <new ids>`. Don't change `TASKS` in `flight-manifest.txt`; the Capsule Communicator updates its queue from your `RESULT` line. The reset sha is usually the task's base, so the new tasks start clean.
   - **Ladder used up:** you must talk to the user before settling.
     1. Relay the failing output, what each attempt tried, and the options: one more attempt on one pair you name from `CATALOG` (any harness, any listed effort, `<DIRECTOR>` included), with your reason for it; a replan if still allowed; or stopping.
     2. Settle with their choice, in the forms above: `RESULT: ESCALATE` (write the note and decisions as for another attempt), `RESULT: REPLAN`, or `RESULT: STOP`. Commit first.
     3. The `ESCALATE` pair is the pair the user saw in your relay, copied exactly. A reply that leaves the choice to you ("whatever you think") approves the pair you recommended. To use a different pair, relay again naming it, and settle only after the user approves it. `run_task.py` test-launches the pair before the attempt; if the launch fails, your answer comes back rejected.

