# Flight Director: JOB: final-review and JOB: verification-failed

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: final-review and JOB: verification-failed

The input is the final reviewer's verdict, or the failing verification output.

Either way, add any Minor issues to `DECISIONS_LOG` under "Open Minor findings".
- **Any Critical or Important issue, or a real failure:** write a final-fix task as `tasks/F<n>.*`, with `<n>` the next free number (`F1`, `F2`, …). An `AC-<n>: MISSING` line in the verdict counts as an Important issue.
  - Ownership is the files concerned plus their tests.
  - Acceptance is the full verification commands.
  - Tier is the highest tier of the plan tasks that own those files, or `standard` if no plan task owns them. `SPEC_TYPE` follows the plan's rule, and `ACS` lists the acceptance criteria it restores, or `none`. Choose its models per "Model choices".
  - Don't change `TASKS` in `flight-manifest.txt`.

  Commit, then settle `RESULT: FINAL_FIX F<n>`.
- **Otherwise:** commit, then settle `RESULT: FINAL_CLEAN`. After a verification failure, this makes the Capsule Communicator ask the user whether to open the PR with verification failing, so first tell the user in a `NOTIFY` why the failure needs no fix.

