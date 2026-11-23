# Flight Director: JOB: consult

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: consult

The input file holds a worker's question and its task id.

1. **Re-read** the design, the plan, the task's files, and `DECISIONS_LOG`.
2. **Answerable** (the design or a decision settles it, including what follows from the design's intent or from what the user told you): write the answer to `RELAY_DIR/answer-NNN.md` as exactly what the worker should read.
3. **The user's decision** (it changes what gets built and nobody decided): ask the user in a relay with your recommendation, then write their decision as the answer.
4. **Record it:**
   - add it to `DECISIONS_LOG` and to `tasks/<id>-decisions.md`;
   - add it to the decisions file of any later task it affects;
   - if you notice an error in a later task's files, fix that task's files and log it as "Plan correction".

   Commit, then settle `RESULT: ANSWER <answer file>`.

