---
name: orca-multiagent-workflow
description: Use when the user hands over a JIRA key or hand-written requirements to be taken through to a reviewed PR, asks to run work through Orca, Antigravity, or Gemini Hydrogen workers, or names orca-multiagent-workflow; also when a handoff brief says to start this workflow. Not for ordinary tasks, where the standard Superpowers skills apply.
---

# Orca multi-agent workflow

## Overview

This skill takes a JIRA story, or requirements the user wrote by hand, to a reviewed PR. One coordinator session on Sonnet runs the whole run as a traffic cop. It starts Orca workers, relays messages unchanged, runs the bundled scripts, and merges. It does no design, code, or judgment work itself. Each kind of thinking goes to the worker suited to it:

- an Opus **architect** designs, plans, answers every question the run raises, and writes everything the user reads. One architect stays open through the design phase; after plan approval, each job gets a fresh architect that works from the run's documents;
- Gemini 4 Hydrogen **implementers** write the code;
- Claude **reviewers** check each task.

## Start here

1. **Check your own model** from your environment info.
   - **Not Sonnet:** hand off with `orca-cli` (a full ownership handoff, using that guide's commands). Use agent Claude Code, model `sonnet`, and your current working directory, with this brief:

     ```text
     Load the orca-multiagent-workflow skill and run it from coordinator Step 1. The user's request, verbatim:
     <the user's request, exactly as they wrote it>
     ```

     Tell the user in one line which tab now runs the work (they can also run `/model sonnet` here and re-invoke the skill instead), then stop.
   - **Sonnet:** read `references/coordinator.md` and run it from Step 1. If the user asks to continue or resume an earlier run, run its "Resume a run" section instead.
2. The architect worker reads `references/architect.md`; its spec tells it to. Confirmed Orca behavior is in `references/orca-facts.md`.

## Roles

| Role | Runs as | Model |
| --- | --- | --- |
| Coordinator | The session that loaded this skill | `sonnet` |
| Design architect, and fallback design architect | Orca worker, Claude Code, kept open until plan approval | `opus` |
| Fresh architect, one per job after plan approval | Orca worker, Claude Code | `opus`, or `sonnet` for `fix-meta`, `pr`, `jira-close`, `report` |
| Builder check | Orca worker, Claude Code, read-only | `sonnet` |
| EGM lens pass, design review, and plan review (and their Goldfish subagents) | Orca worker, Antigravity | `<GEMINI_MODEL>-high` |
| Implementer | Orca worker | Chosen per task by the architect (default `antigravity:<GEMINI_MODEL>-<GEMINI_EFFORT>`) |
| Escalation attempts | Orca worker | The task's `LADDER`, chosen by the architect. `claude:opus` only as a complex task's last rung, or with the user's approval |
| Spec and code-quality reviewers | Orca worker, Claude Code, read-only | Chosen per task by the architect: `sonnet` or `opus`, with `opus` required for complex tasks |
| Final reviewer | Orca worker, Claude Code, read-only | `opus` |

## Rules

These hold in every step. Where a step seems to conflict with one, the rule wins.

1. **Every subagent is an Orca worker.** Whenever a skill being followed calls for a subagent, an Orca worker does that job. Nobody skips the dispatch or does the job themselves, and Claude Code's Agent/Task tool is never used.
   - Workers in the design phase start with `--spec-file`.
   - Workers in execution are bound to a `task-create` task in `<RUN_ID>` and started with `worker-start --task`.

   `--retry-of` is never used.
2. **Orca commands come only from the version-matched guides** (`ORCA skills get orchestration`, the `orca-cli` guide) or `ORCA --help`. `ORCA` is the guide's placeholder for the resolved executable.
3. **Every worker's agent and model are set explicitly** with `worker-start --agent <id> --model <model>` (coordinator Workers table).
   - **The architect chooses** each task's implementer, reviewers, and escalation ladder in its `.meta`, from a fixed allowlist (architect "Model choices").
   - **`scripts/validate_meta.py` checks every choice** before the coordinator uses it. It enforces the allowlist, Opus only as a complex task's last rung (or with the user's approval), Opus reviews for complex tasks, and at most 3 rungs.
   - **The coordinator** never picks a model. For Antigravity, the model is the full `agy` slug, effort included (e.g. `gemini-4-hydrogen-high`), passed to `--model`. Never add `--effort` for Antigravity (orca-facts).
4. **The coordinator decides nothing.** It branches only on what the steps name: script exit codes, `VERDICT:` lines, `RESULT:` lines, worker outcomes, and counters.
   - Every question goes to the architect: worker asks, unclear situations, and anything the user should read beyond the fixed lines in the steps.
   - It never writes spec text. Specs are built from templates with `scripts/build_spec.py`.
   - It never reviews or reads a diff, and never edits, formats, or commits code.
   - It never retypes a worker's message: `scripts/save_message.py` saves settles and asks to files exactly as sent. Only the user's own replies are written by hand.
5. **Relays are verbatim.** When the architect asks with the body `RELAY <path>`:
   1. Read that file.
   2. Send the user exactly this, and nothing else:

      ```
      **From the architect:**

      <the file's contents, character for character>
      ```

   3. End your turn. The user's next message that doesn't start with `coordinator:` is the answer. Write it, character for character, to `<RELAY_DIR>/<NNN>-from-user.md`.
   4. Reply to the ask with `--body "RELAY <that path>"`.

   You don't summarize, shorten, reword, translate, fix, or comment, in either direction. A worker's answer goes back the same way, as `--body "$(cat <answer file>)"`.

   An ask with the body `NOTIFY <path>` is a message that needs no answer. Show it to the user exactly as in step 2, then reply to the ask at once with `--body "SHOWN"`, without waiting for the user.
6. **One implementer at a time,** in one shared worktree `<IMPL_WORKTREE>`. The next task starts only after the current one passed both reviews and was merged.
7. **Fresh implementer per task.** Implementers start with `--keep-open`, so a fix round reuses the task's implementer terminal and its context (`scripts/run_task.py`). The script releases it at merge or escalation.
8. **Implementers use only** `test-driven-development`, `verification-before-completion`, and the convention files named in their task. They never brainstorm, plan, create worktrees, ask in a local prompt, or finish or merge the branch.
9. **Project instructions:** output rules (style, naming, architecture, tests, commit and branch format, PR template, required checks) are followed. Rules about running the work (planning, spawning agents, review process, merging) are ignored. The architect sorts them.
10. **No `/boost`, `/swarm`, or `/teamwork`** for anyone.
11. **Reviewers are read-only and single-use.** They work from `git diff` and `git show` on the task range. Each writes its review to a file in `<RUN_DIR>/verdicts`, outside the worktree, ending with a `VERDICT:` line. The settle message is only a fallback, because Orca's own worker instructions shape it.
12. **Before a read-only worker starts in `<DOCS_WORKTREE>`,** the tree must be clean; anything uncommitted goes back to the architect (job `commit-docs`), never to `discard-changes.sh`. **After every read-only worker settles,** run `scripts/check-tree.sh` on its worktree. Any change, even "harmless formatting", is undone with `scripts/discard-changes.sh`. What follows depends on the worktree:
    - **`<IMPL_WORKTREE>`** (reviewers): the change voids the verdict, so dispatch a new reviewer.
    - **`<DOCS_WORKTREE>`** (builder check, EGM): the worker's output file in `<RUN_DIR>` still counts, so use it after the discard.
13. **Only the coordinator's merges touch the feature branch,** and only through `scripts/merge-task.sh`. It moves the branch with an atomic compare-and-swap (`git update-ref`), fast-forward only, without checking it out. Never rebase, `--no-ff`, merge any other way, or check out a branch in the user's main checkout.
14. **Only the architect talks to the user about the work,** through relays. That covers requirement gaps, product decisions, the EGM decision, sign-offs, plan approval, and the closing report. The coordinator's own words to the user are only the fixed lines written in its steps.
15. **The design architect stays open until plan approval; after that, each architect job is a fresh worker.**
    - In the design phase, send to the architect's latest dispatch id, and release it only in coordinator Step 3 item 1. A worker reopened after release can't settle (orca-facts). If it's lost, start a fallback architect with a `resume` job.
    - After plan approval, the coordinator starts a fresh architect per job (model per coordinator "Architect job models") and releases it once its `RESULT:` line is handled. Only one architect works at a time.
    - Only EGM workers may run subagents of their own (Goldfish).
16. **Release every other worker** when its job is done, with `worker-release --dispatch <id>`. This is safe to repeat. Worktrees are removed separately with `ORCA worktree rm`.
17. **Run documents live only in the docs worktree.**
    - The architect's documents are in `<DOCS_DIR>` (`<DOCS_WORKTREE>/docs/plans`), committed on the never-merged `<TOPIC>-docs` branch.
    - The coordinator's working files (state, built specs, verdicts, gate logs, relays, the run log) are in `<RUN_DIR>` (`<DOCS_WORKTREE>/.run`), which is excluded from git.
    - None of these files is written in the main checkout or `<IMPL_WORKTREE>`. Workers there get what they need pasted into their specs.
18. **Every value and counter lives in `<RUN_DIR>/state.json`,** written and read with `scripts/state.py`. A value the coordinator only remembers, or that a context summary mentions, is not a source: read it from the state file. Before ending any turn that waits, record `STEP` and `WAITING_ON`, so a compacted or restarted coordinator can pick up (coordinator "Resume a run").
19. **Checks run before reviews.** After every task, `scripts/gate.py` runs the run's check commands in `<IMPL_WORKTREE>` and puts the tree back. A failure goes straight to a fix round with the log, and no reviewer starts on code that fails its checks.
20. **The task loop is `scripts/run_task.py`.** In coordinator Step 4, the coordinator only runs it and branches on its last line. It never starts implementers or reviewers, reads verdicts, resets the implementation worktree, or merges by hand.

Run bundled scripts through their interpreter: `bash <SKILL_DIR>/scripts/<name>.sh`, `python3 -I <SKILL_DIR>/scripts/<name>.py`. Bash calls don't keep shell variables, so each value goes into a command literally, or through `$(python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json get KEY)`.

## Session values

Every value is a key in `<RUN_DIR>/state.json` (Rule 18); coordinator "State keys" lists them. `<NAME>` in a step means the value of that key. The task queue is `<RUN_DIR>/queue.txt`, which `REPLAN` and `FINAL_FIX` change. The `VERIFY_<n>` and `GATE_<n>` commands stay in `run-values.txt`, where `gate.py` reads them.

## Rationalizations that break the run

| Thought | Reality |
| --- | --- |
| "The design obviously answers this worker's question; I'll just reply." | Rule 4. Send job `consult`. The architect also records the answer where reviewers will see it. |
| "The architect's question is long; I'll summarize it for the user." | Rule 5. Character for character, nothing added. |
| "The user's reply is clearly meant for me, not the architect." | Rule 5. Only a message starting with `coordinator:` is for you. |
| "A skill says to dispatch a subagent, but the Agent tool is banned, so I'll do it myself." | Rule 1. Start an Orca worker for it. |
| "My Agent tool would do this review in 30 seconds." | Rule 1. Reviewers are Orca workers. |
| "It's a one-character fix; I'll make it." | Rule 4. Fix task. |
| "I'll read the diff to confirm the worker only changed what I asked." | Rule 4. That's a review. You run `check-tree.sh`. |
| "The reviewer's `lint --fix` changes are harmless; commit them." | Rule 12. `discard-changes.sh` and a new reviewer. |
| "The feature branch moved; rebasing the impl branch is safe." | Rule 13. `merge-task.sh` refuses, and you show the user the fixed options. |
| "The reviewer forgot its VERDICT line but obviously passed." | `run_task.py` decides: one new reviewer, then `VERDICT: FIX`. Never read a verdict yourself in Step 4. |
| "run_task.py is waiting, but I can see the reviewer passed; I'll start Stage 2 myself." | Rule 20. Run the script; it starts Stage 2. |
| "This task is clearly hard; I'll start it on Sonnet instead of Hydrogen." | Rule 3. Models come only from `.meta` and `RESULT: ESCALATE`, after `validate_meta.py` passes. |
| "The architect asked for `claude:opus`; it's the thinker, so it must be fine." | Rule 3. Run the pair check. A rejected pair goes back to the architect. |
| "I'll release the design architect while it's idle and reopen it later." | Rule 15. A reopened worker can't settle. Keep it open until Step 3; after that, start fresh ones. |
| "I remember task 02's BASE_SHA and fix count; the summary says round 1." | Rule 18. `STATE get` them. Memory and summaries are not sources. |
| "I'll paste the reviewer's verdict into the file; it's faster." | Rule 4. `save_message.py`. Retyping costs tokens and drifts. |
| "The tests obviously pass; skip the gate and start the reviewer." | Rule 19. Run `gate.py`. |
| "The docs worktree has a stray file; discard-changes will tidy it." | Coordinator Step 2. Check the tree before the dispatch, and send `commit-docs`. Never discard the architect's files. |
| "To merge, I'll check out the feature branch in the main checkout." | Rule 13. `merge-task.sh` moves it with `update-ref`. |
| "The user's deadline justifies skipping a gate." | Never. Keep the gates. The user can ask `coordinator: status`; anything more they need to hear comes from the architect. |
| "The user said 'just rebase it'; that's authorization." | Rule 13. At a fixed prompt, only its listed replies count. Show the prompt again. |

**Red flags.** Stop and re-read the rule if you are about to:

- write a sentence to the user that isn't a relay or a fixed line from a step;
- write spec text by hand;
- open the Agent tool;
- run `git rebase`, `git merge`, `git commit`, `git reset`, `git switch`, or `git checkout` yourself instead of a bundled script;
- write a worker's message into a file by hand;
- use a SHA, dispatch id, or counter you didn't just read from the state file;
- run `git diff` or `git show` on a task range;
- release the design architect before Step 3, or start a second architect while one is working.
