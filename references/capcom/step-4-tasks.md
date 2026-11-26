# Capsule Communicator: Step 4: Run the task loop

Part of mission-control. Everything in `<SKILL_DIR>/references/capsule-communicator.md` ("How you work", "Messages", "State keys") and the Rules in `<SKILL_DIR>/skills/mission/SKILL.md` hold here.

`run_task.py` runs every task in `<RUN_DIR>/queue.txt` synchronously: it dispatches Crew, checks the tree, runs the gate, runs both reviews and the fix rounds, merges, and prepares escalations. Its rules (fix-round caps, void reviewers, usage limits, ladder checks) are in its module docstring.

1. **Run `TASKLOOP`.** Print one `Merged task <id> at <head>.` line for each `MERGED <id> <head>` it printed (and log it in `mission-log.md`), and branch on its **last line**:

   | Last line (exit) | Do |
   | --- | --- |
   | `QUEUE_EMPTY` (0) | If `PHASE` is `execution`, go to Step 5. If it is `finish`, run `FINISH` and branch as in Step 5 items 1–3. |
   | `ESCALATE <input>` (20) | Send job `escalate` with `INPUT=<input>` (everything after `ESCALATE `). When the Flight Director returns `RESULT`, run `TASKLOOP --director-result <saved>` and branch again. |
   | `FIX_META <file>` (21) | Send job `fix-meta` with `INPUT=<file>`. When the Flight Director returns `RESULT`, run `TASKLOOP --director-result <saved>` and branch again. |
   | `CONSULT <ask_file>` (22) | Send job `consult` with `INPUT=<ask_file>`. When the Flight Director returns `RESULT: ANSWER <answer_file>`, run `TASKLOOP --answer <answer_file>` and branch again. |
   | `PROMPT <file> <reply>...` (30) | A fixed prompt. Show the user `<file>`'s contents exactly, and end your turn (`STATE set WAITING_ON=user`). A reply that is one of the listed words: run `TASKLOOP --reply <word>` and branch again. Any other reply that doesn't start with `capcom:`: show the file again, followed by "Reply with exactly one of: <the words>." |
   | `PAUSED <file>` (41) | The user paused at a usage-limit prompt; the script changed nothing. `STATE set WAITING_ON=user`, say exactly "Paused. The run keeps its place: when the limit has reset, say \"continue my mission run for <TOPIC>\" and reply \"retry\".", and end your turn. |
   | `SWITCH <file>` (42) | The user replied "switch" at a usage-limit prompt; the script changed nothing. Run "Switch picks" (`<SKILL_DIR>/references/capcom/switch.md`). |
   | `STOP <reason>` (40) | Dispatch nothing more and go to Step 5 item 7. |

You don't read verdicts, count rounds, pick pairs, reset worktrees, or merge in this step; the script does all of it.

