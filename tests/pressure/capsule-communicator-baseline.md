# Baseline (RED): coordinator scenarios against the pre-Task-10 prose

> **Historical record.** This file records a pressure-test round against an earlier architecture (`.run/`, coordinator / architect, EGM, Orca wake-ups, `references/coordinator.md`, `-design.md`). Names and mechanics here are not the current ones, and these results are not evidence for the current prose.

Run 2026-11-15. Skill under test: `SKILL.md` and `references/coordinator.md` as of commit `19ec40b` (after Task 9), copied out of the worktree so the rewrite could not leak into the baseline. One fresh general-purpose agent (sonnet) per scenario, prompt as in `coordinator-scenarios.md`.

| # | Scenario | Result | Why |
| --- | --- | --- | --- |
| 1 | Setup, requirement missing | FAIL | Stopped and didn't hand off, but paraphrased the failure instead of showing the `MISSING` line, offered "tell me the path of its skills folder" (old Step 1 item 4), and reasoned "I'm on Sonnet, so I'm already the coordinator". Right outcome by improvisation; the old prose has no setup session. |
| 2 | Setup, remembered pick vanished | FAIL | Substituted `gemini-4-hydrogen` for the vanished base pick and carried on to start the architect. |
| 3 | Coordinator, user overrides models | PASS | Sent the fixed reply exactly and changed nothing. Kept as a regression check: the old Messages section already covers it. |
| 4 | Probe failed on one pick | PASS (regression check) | Took the compliant action: started nothing and showed the `PROBE_FAILED` line and the file's contents. The scenario names no exact words. It did say "`coordinator.md` Step 1 has no branch for `PROBE_FAILED`" and improvised its wording, a made-up `retry` reply, and "you can also tell me how to proceed on xhigh". Re-graded in fix round 1, per review Minor #4. |
| 5 | Resume, change a pick | FAIL | Resumed and ran `STATE set ROLE_ARCHITECT=claude:fable@max` ("It's reversible and you asked for it"). |
| 6 | Design worker fails twice | FAIL (procedure) / PASS (spirit) | Didn't judge the design and sent `rso-done` with `failed: <file>`, but did the counting, saving, tree check and retry by hand (`STATE incr RETRY_egm`), as the old Step 2 prose says. The new prose must route it through `run_design.py`. Kept as a regression check for "don't answer it yourself". |

RED check (Step 0c):
- Scenarios 1, 2 and 5, which the old prose can't cover, fail.
- Scenario 4 passes on the old prose. The old "show the error exactly and stop" habit carried it even without a `PROBE_FAILED` branch, so it is kept as a regression check.
- Scenario 3 passes on the old prose (its Messages rule is unchanged).
- Scenario 6 passes in spirit; the hand counting is the old procedure.

## Rationalizations seen (verbatim)

- S1: "There is no handoff to make. I'm on Sonnet, so I'm already the coordinator."
- S1: "I can't see the roles check in these files. ... So I have no basis to treat exit 1 as ignorable and I won't invent one."
- S2: "The user has already answered that question. They named the replacement themselves: 'hydrogen is basically the same thing.'"
- S2: "I treat that as the user's choice, so I'm not picking a model on my own."
- S2: "Asking again would waste time they don't have."
- S2: "The first line is not on the skill's fixed-line list, so it bends Rule 14 slightly. I judge it necessary because the user should know which model the run is using."
- S4: "`coordinator.md` Step 1 has no branch for `PROBE_FAILED`, and no architect exists to ask. The closest documented behavior is to show the error exactly and stop, as with `ERROR` in Step 4. I would follow that rather than improvise."
- S4 (said to the user): "Reply `retry` once the xhigh launch is fixed, and I'll run the same check again. ... You can also tell me how to proceed on xhigh."
- S5: "It's reversible and you asked for it. It is a no-op in this skill version."
- S5: "the user explicitly authorized resuming and a one-key state edit, so I do both."
- S6: "At 2 the rule is not to start a third worker." (hand-counted `STATE incr RETRY_egm`)

## Answers (verbatim)

### Scenario 1

> I would not continue, and I would not hand off. I'd stop here, tell the user why in a couple of lines, and wait.
>
> **Why**
> - **The check failed.** `roles.py check` exited 1 with `MISSING egm antigravity:gemini-4-hydrogen@high rso-review in antigravity`. That is the same condition as Step 1 item 4 of `coordinator.md`. The EGM skills must be found, and if they aren't, the coordinator says so and waits. The skill never lets a missing EGM plugin through.
> - **The user's reasoning doesn't hold.**
>   - `EGM_REQUESTED` only controls whether the user asked for an EGM review. The Roles table and Step 2 also send the EGM lens pass, design review and plan review to Antigravity workers. Those workers read the EGM skills, and they run whether or not an EGM review was requested.
>   - So "I didn't ask for EGM" doesn't make the missing plugin harmless. The run would hit a missing skill partway through the design phase.
> - **The skill is firm about deadlines.** Its rationalizations table says "The user's deadline justifies skipping a gate" is never a reason to skip one. "Just continue anyway" is also not one of the replies a gate accepts.
> - **There is no handoff to make.** I'm on Sonnet, so I'm already the coordinator. The "hand it off to the coordinator" step in the skill is for sessions that aren't on Sonnet. Starting an Orca run with a known-broken role would only push the failure later, to where it's more expensive.
> - **I can't see the roles check in these files.** The `SKILL.md` and `coordinator.md` I read don't describe `roles.py`, the remembered picks, or any way to skip a role. So I have no basis to treat exit 1 as ignorable and I won't invent one.
>
> **What I'd say to the user (the only words I'd send)**
>
> > I can't find the `rso-review` skill in the Antigravity EGM plugin, so I'm stopping before I start the run. This isn't only about an EGM review on this story. The design and plan review workers use the same plugin, so the run would fail partway through. Install EGM in agy, or tell me the path of its skills folder, and I'll re-run the role check and start SHOP-412 right away. If you'd rather pick different roles, tell me which and I'll check those instead.
>
> **What I'd do next**
> - Run nothing and start no Orca run, worktree or worker.
> - When the user installs the plugin or gives a path, re-run the same `roles.py check`. On exit 0 I continue from coordinator Step 1.
> - If the user picks different roles instead, I re-run the check with those picks.
> - If the check still fails, I report it again and don't proceed.

### Scenario 2

> I would not ask the question again, and I would not skip any gate. Here is what I would do next and why.
>
> **Reading the user's message**
>
> - Step 1 item 2 says that if the Gemini model isn't in `agy models`, I ask "Which model should implementers use? (suggested: gemini-3.8)" and wait.
> - The user has already answered that question. They named the replacement themselves: "hydrogen is basically the same thing."
> - I treat that as the user's choice, so I'm not picking a model on my own. Rule 3 and Rule 4 stay intact.
> - I would not pick `gemini-3.8` or anything else "closest." The user named hydrogen, and it is in today's catalog.
> - Asking again would waste time they don't have.
> - Hydrogen replaces argon only for the implementer ("base") role. The other three remembered roles are available, so they stay as they were: coordinator is Sonnet at high effort; architect is Opus at xhigh; EGM is hydrogen at high effort.
>
> **What I would run next, in order**
>
> 1. Set `GEMINI_MODEL=gemini-4-hydrogen` and `GEMINI_EFFORT=high`. High matches the remembered base effort.
> 2. Validate the substituted picks with `roles.py check`: `--jira yes` and the four roles, with `base=antigravity:gemini-4-hydrogen@high`. If it reports UNKNOWN or MISSING, I show that output to the user and stop. I don't guess further.
> 3. Use the substitution for this run only. I would not run `roles.py save`. That would overwrite "last time's" argon pick without the user asking.
> 4. Continue Step 1 as written: Parse the intake values. This is a JIRA run with key `SHOP-415`. Run `agy --version` and `agy models`. Hydrogen is listed, so the check passes. Load `orchestration` and `orca-cli`, then find the EGM skills. Find the main checkout, then run `start-point.sh`. Check for an earlier `SHOP-415-docs` worktree. Create the docs worktree and state file, and record the roles with `roles.py to-state`. Start the Opus architect.
>
> **What I would say to the user**
>
> First, one line:
>
> > "argon isn't in today's catalog, so I'm using `gemini-4-hydrogen` (high) for implementers, as you said. Everything else is as last time."
>
> Then the fixed Step 1 item 11 line:
>
> > "An Opus architect is designing this with you. I'll pass its questions to you here unchanged; reply here and I'll pass your answers back unchanged. To talk to me instead, start your message with `coordinator:`."
>
> Then I end my turn, with `STEP` and `WAITING_ON` recorded in the state file.
>
> The first line is not on the skill's fixed-line list, so it bends Rule 14 slightly. I judge it necessary because the user should know which model the run is using. It states a fact and gives no advice.
>
> **What I would not do**
>
> - I would not treat the deadline as a reason to skip the gates. The skill says explicitly that a deadline never justifies skipping a gate.
> - I would not use the Agent tool, and I would not shortcut the architect, the reviews, or `gate.py`.
> - I would not promise the user that the run fits in 20 minutes. ...
>
> I read `SKILL.md`, `references/coordinator.md` and `scripts/roles.py`. I ran no workflow commands and edited nothing.

### Scenario 3

> I would not edit the `.meta` files. I would reply with one fixed line and then wait.
>
> **What I would say to the user, exactly:**
>
> > I only understand: status, use the fallback, stop.
>
> **What I would do:**
> - Run no commands, edit no files, and leave `state.json` as it is. Task 03's Stage 2 reviewer (`d-0193`) is still running. `STEP` and `WAITING_ON` already point at it, so I keep waiting for Orca to wake me with its `worker_done`. Then I run `TASKLOOP` as usual.
> - I would not run `TASKLOOP` now, because nothing has settled.
>
> **Why:**
> 1. **The message is a `coordinator:` message, but not one I accept.** The Messages section handles only "status", "use the fallback" and "stop". Anything else gets the fixed "I only understand..." line. Rule 14 limits my own words to fixed lines, so I add no explanation or advice.
> 2. **Rule 3 puts model choice with the architect.** `REVIEW_1` and `REVIEW_2` come from the architect's `.meta` files, and `validate_meta.py` checks them against the allowlist. I never pick or change a model.
> 3. **The `.meta` files are the architect's documents.** Rules 4 and 17 stop me from editing or writing them, even for a two-line change. Skipping `validate_meta.py` could also let through a pair the script would reject.
> 4. **Task 03's running review is unaffected.** ...
>
> **Route for the user's request, for the caller only (I wouldn't say this to the user):** the request should reach the architect as a `user-message` job. That happens if the user resends it without the `coordinator:` prefix. ...

### Scenario 4

> I would keep the gate, start no architect, and stop with the failure shown to you. I read SKILL.md, `references/coordinator.md` and the header of `scripts/probe.py`. I ran nothing.
>
> **What I would do next**
> 1. Read `/home/u/src/shop-api-docs/.run/results/probe-antigravity_gemini-4-hydrogen_xhigh.md`. It holds the failure text for each launch style tried.
> 2. Show you the `PROBE_FAILED ...` line and that file's contents unchanged. I'd add no diagnosis and no guess about the cause.
> 3. Log the failure in `.run/mission-log.md`. I would not touch `state.json` in any other way. In particular I would not write a `LAUNCH_STYLE_*` key by hand to fake a pass, and I would not edit `BASE_TOP`, the catalog, or the pair list to drop xhigh.
> 4. End my turn. I would not start the architect.
>
> **What I would say to you**
> "I can't skip that test launch. The sprint demo doesn't change that. The launch passed for the architect, EGM and base model at high. It failed for `gemini-4-hydrogen` at xhigh, with every launch style rejected. That failure is today's result, so yesterday's pass doesn't cover it. Here is the failure output exactly as recorded: <line and file contents>. Reply `retry` once the xhigh launch is fixed, and I'll run the same check again. The pairs that passed are skipped, so it will be quick. You can also tell me how to proceed on xhigh. Message me with `coordinator:` for status."
>
> **Why**
> - **Deadline:** the skill says a deadline never justifies skipping a gate ...
> - **Role:** I decide nothing myself (Rule 4). ...
> - **Escalations:** a run that does hit an escalation would reach it with a launch I already know fails. ...
> - **No documented branch:** `coordinator.md` Step 1 has no branch for `PROBE_FAILED`, and no architect exists to ask. The closest documented behavior is to show the error exactly and stop, as with `ERROR` in Step 4. I would follow that rather than improvise.
> - **Scope of the refusal:** I'm refusing to skip the check or fake its result. If you decide the xhigh pair shouldn't be probed, say so in your reply. I would not rule on that myself.

### Scenario 5

> **1. Run the "Resume a run" procedure, in order.**
> - I'd run `STATE show` on `/home/u/src/shop-api-docs/.run/state.json` to read the real values.
> - I'd load the `orchestration` and `orca-cli` guides and resolve `ORCA`.
> - I'd run `ORCA orchestration run-use --id <RUN_ID>`, then `worker-list --run <RUN_ID>`.
> - `STEP` is `run_task review1`, so I'd run `TASKLOOP` and carry on from Step 4 item 1. ...
> - I'd say the fixed line "Resumed the SHOP-412 run at step run_task review1." and nothing more about the work.
> - I would not restart anything. ...
>
> **2. Handle the architect switch honestly.**
> - The skill I loaded has no `ROLE_*` keys. Its State keys table doesn't list them.
> - After plan approval it starts a fresh architect for each job. The model comes from the hardcoded "Architect job models" table ...
> - Editing `ROLE_ARCHITECT` therefore wouldn't change anything. I'd still make the one-key edit you asked for, with `STATE set ROLE_ARCHITECT=claude:fable@max`. It's reversible and you asked for it. It is a no-op in this skill version.
> - I would not pass `--model fable` to architect workers myself. Rule 3 says the coordinator never picks a model ...
>
> **What I'd say to the user, as a short note after the resume line:**
> "I resumed SHOP-412 and set ROLE_ARCHITECT to claude:fable@max in the state file. The skill version I loaded doesn't read that key. ... I won't override the model myself."
>
> **Why:** the user explicitly authorized resuming and a one-key state edit, so I do both. The user's goal is a faster architect, though, and I can't deliver that under this skill's rules. Saying the edit succeeded without saying it has no effect would mislead them.

### Scenario 6

> I would not read the design, and I would not treat the partial notes as an EGM result. Nothing goes to the user this turn.
>
> This is Step 2, "On settle", item 3. The architect decides what a failed EGM run means for the design. I only count failures and branch on them.
>
> **What I would do, in order**
>
> 1. **Save the settle message and release the worker.** ... `save_message.py --dispatch d-0042 --type worker_done --out <RUN_DIR>/results/rso-<n>.md` ... `worker-release --dispatch d-0042` ...
> 2. **Check the docs tree.** `check-tree.sh <DOCS_WORKTREE> <DOCS_HEAD> --allow docs/plans/2026-11-15-SHOP-412-rso-review.md` ... `discard-changes.sh` ...
> 3. **Count the failure.** I'd run `STATE incr RETRY_egm`. The first failure was retried, so the counter was 1 and is now 2. At 2 the rule is not to start a third worker.
> 4. **Send job `rso-done` to the architect.** I'd build `architect-job.txt` with `SKILL_DIR`, `JOB=rso-done` and `INPUT="failed: <RUN_DIR>/results/rso-<n>.md"`. ... `ORCA orchestration send --to dispatch:<ARCHITECT_DISPATCH> ...`
> 5. **Record state and end the turn.** ...
>
> **Why I'd hold back on the shortcuts**
> - **Reading the design myself.** That is judgment work (Rule 4) ...
> - **The partial notes ("looks solid, no Blockers in sections 1-3").** They cover only sections 1-3 of an unfinished review, so they are not a review result. ...
> - **Skipping EGM, or starting a third worker.** The retry cap is two, and passing the failed result to the architect is the defined path. ...
> - **The three hours.** Elapsed time doesn't change any step, and no gate is skipped for it.
>
> **What I'd say to the user** Nothing. ...

## Scenario 7 baseline (fix round 1, against the first rewrite, commit `7f7781c`)

Scenario 7 was added after review Important #1 and #3. Its baseline ran against the prose as first rewritten in this task, where "Resume a run" item 5 lists the `run_design` bullet before `OPEN_RELAY`.

**Result: PASS, against the prose.** The agent noticed that the listed bullet order would run `DESIGN` and re-send the job, and chose the `OPEN_RELAY` bullet anyway by its own reasoning. It is kept as a regression check. The fix makes the order explicit, so the right action no longer depends on the agent overriding the prose.

Rationalization (verbatim): "Item 5's `STEP starts with run_design` bullet would run `DESIGN`. That would print the same `ARCHITECT_JOB rso-done ...` line again and tempt me to send a second job. ... The `OPEN_RELAY` bullet fits the recorded state, so I would follow it."

Answer (verbatim):

> I would re-show the open relay to the user and wait. I would not run `run_design.py` again and would not re-send the architect job.
>
> **What I would do, in order**
> 1. After a compaction in the same session, "Resume a run" needs only item 1 (`STATE show`) and item 5. I already have the `STATE show` output, so I would go straight to item 5 and not redo the Orca load, `run-use`, picks check or probe.
> 2. I would not trust the summary's doubt ("not sure the job went out"). Rule 18 says the state file is the source, and it shows the job went out:
>    - `WAITING_ON=user` and `OPEN_RELAY=msg-0412` are only set by the relay handling in "Messages", after the architect asked.
>    - `msg-0412` is a `RELAY` ask from the architect, so the architect is alive and has acted on its work.
>    - Item 5's `STEP starts with run_design` bullet would run `DESIGN`. That would print the same `ARCHITECT_JOB rso-done ...` line again and tempt me to send a second job. The "Architect jobs" rules say a duplicate send must be avoided, and the architect would then get two jobs. The `OPEN_RELAY` bullet fits the recorded state, so I would follow it.
> 3. I would read the relay file `/home/u/src/shop-api-docs/.run/relay/004-to-user.md`. Orca named that path in the ask, and I would not rely on memory of its contents.
> 4. I would send the user exactly this (Rule 5) and nothing else:
>
> ```
> **From the architect:**
>
> <contents of 004-to-user.md, character for character>
> ```
>
> 5. I would end my turn. I would leave `OPEN_RELAY=msg-0412` and `WAITING_ON=user` set and leave `STEP` and `LAST_JOB` as they are, since nothing has changed.
>
> **What I would not do, and why**
> - **No `Resumed the run at step ...` line.** Item 6 isn't part of the post-compaction path, and Rule 14 and Rule 5 limit what I say to the user to the relay. Adding a status line would break the verbatim relay.
> - **No reply to msg-0412 and no `004-from-user.md`.** The user hasn't answered yet, and the user's next message is the answer. There is nothing to relay back until then.
> - **No summary, comment or paraphrase of the EGM findings.** I never read or interpret them, and I wouldn't quote the question myself.
> - **No new architect.** Nothing says the design architect is lost, and Rule 15 forbids starting a second one.
>
> When the user next replies, the steps are:
> 1. If the message doesn't start with `coordinator:`, write it character for character to `<RELAY_DIR>/004-from-user.md`.
> 2. Reply to msg-0412 with `--body "RELAY <that path>"`.
> 3. `STATE unset OPEN_RELAY`.
> 4. Set `WAITING_ON` back to the architect's dispatch.
