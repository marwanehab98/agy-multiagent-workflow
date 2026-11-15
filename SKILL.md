---
name: orca-multiagent-workflow
description: Use when the user hands over a JIRA key or hand-written requirements to be taken through to a reviewed PR, asks to run work through Orca, Antigravity, or Gemini workers, asks to continue an earlier run of this workflow, or names orca-multiagent-workflow; also when a handoff brief says to start or resume this workflow. Not for ordinary tasks, where the standard Superpowers skills apply.
---

# Orca multi-agent workflow

## Overview

This skill takes a JIRA story, or requirements the user wrote by hand, to a reviewed PR. The user picks a harness, model and effort for four roles; every other worker follows from those picks. One coordinator session runs the whole run as a traffic cop. It starts Orca workers, relays messages unchanged, runs the bundled scripts, and merges. It does no design, code, or judgment work itself. Each kind of thinking goes to the worker suited to it:

- the **architect** designs, plans, answers every question the run raises, and writes everything the user reads. One architect stays open through the design phase; after plan approval, each job gets a fresh architect that works from the run's documents;
- **EGM** workers review the design and the plan;
- **implementers** write the code, starting on the base pick;
- **reviewers**, chosen by the architect, check each task.

A pair is written `<harness>:<model>@<effort>`. Only `scripts/harnesses.py` knows how a harness names its models and efforts.

## Start here

The session that loads this skill is the **setup session**, whatever its harness, unless its brief makes it the coordinator.

- **Your brief says to run coordinator Step 1, or "Resume a run":** you are the coordinator. Read `references/coordinator.md` and do exactly that. Skip the rest of this section.
- **The user asks to continue or resume an earlier run:** go to "Continue a run" below.
- **Otherwise:** run setup.

`SKILL_DIR` is this skill's base directory. `CFG` is `$HOME/.config/orca-multiagent-workflow`, and `CATALOG` is `<CFG>/catalog.json`.

### Setup

1. **Detect:** run `mkdir -p <CFG>`, then `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <CATALOG>`.
2. **Remembered picks:** run `python3 -I <SKILL_DIR>/scripts/roles.py remembered --roles <CFG>/roles.json --catalog <CATALOG>`.
   - **`AVAILABLE yes`:** show the four `role=pair` lines in one block, then say exactly "Reply \"same\" to use these picks again, or \"new\" to choose again." This is a fixed prompt. "same" goes to item 4 with those picks; "new" goes to item 3.
   - **`AVAILABLE no <role> <pair>`** (one line per vanished pick): for each line, say exactly "Your last <role> pick, <pair>, isn't available any more, so pick the <role> again." and ask that role as in item 3. Keep the remembered picks that are still available, and go to item 4. Never choose a replacement yourself, however close a model looks or whatever the user delegates to you: only a number the user picks from the menu counts.
   - **`NONE`:** go to item 3.
3. **Ask the four roles,** in this order: coordinator, architect, EGM, base implementer. For each, ask three questions in turn, each a numbered list in plain text, not a multiple-choice tool. `MENU` means `python3 -I <SKILL_DIR>/scripts/roles.py menu --catalog <CATALOG>`:
   1. harness: `MENU`;
   2. model: `MENU --harness <harness>`;
   3. effort: `MENU --harness <harness> --model <model>`.

   Each menu line is `N<TAB>value<TAB>label`. Ask "<role>: which <harness|model|effort>?", then one line per row, `N. label`, then any `BROKEN` lines exactly. The reply is a number from the list; anything else gets the same list again. The pick is `<harness>:<model>@<effort>`, using each chosen row's value.
4. **Check the picks:** run `python3 -I <SKILL_DIR>/scripts/roles.py check --catalog <CATALOG> --jira <yes if the request has a JIRA key, else no> --coordinator <pair> --architect <pair> --egm <pair> --base <pair>`.
   - **Exit 0:** show any `NOTE` line exactly, and go on.
   - **Exit 1 with any `MISSING` line:** show every `MISSING` and `UNKNOWN` line exactly, then say exactly "The run can't start until these are fixed. Install what's missing, or choose different picks, then ask me again." Then stop: don't save, and don't hand off (Rule 21).
   - **Exit 1 with only `UNKNOWN` lines:** show them exactly, ask each named role again as in item 3, and repeat this item.
5. **Save:** run `python3 -I <SKILL_DIR>/scripts/roles.py save --roles <CFG>/roles.json` with the same four `--<role> <pair>` options.
6. **Hand off** to a fresh coordinator on the coordinator pick, even if this session already runs that harness and model:
   1. `COORD_SKILL` is the output of `python3 -I <SKILL_DIR>/scripts/harnesses.py skill-dir --catalog <CATALOG> --harness <the coordinator pick's harness> --skill orca-multiagent-workflow`.
   2. Write the brief to `<CFG>/handoff.md`:

      ```text
      You are the coordinator of an orca-multiagent-workflow run. Read <COORD_SKILL>/SKILL.md, then run <COORD_SKILL>/references/coordinator.md from Step 1. The picks file is <CFG>/roles.json and the setup catalog is <CATALOG>. The user's request, verbatim:
      <the user's request, exactly as they wrote it>
      ```

   3. Load the `orca-cli` skill and use its handoff command for a fresh agent in the current worktree, `ORCA agent launch`, with `--prompt "$(cat <CFG>/handoff.md)"` and, in place of `--agent`, `--model` and `--effort`, the output of `python3 -I <SKILL_DIR>/scripts/harnesses.py args --catalog <CATALOG> --pair <the coordinator pick>`.
   4. Say exactly "The coordinator now runs in <the tab or handle from the launch result>. Talk to it there." Then stop.

### Continue a run

1. Find the run's state file: it is `.run/state.json` in the worktree on `refs/heads/<TOPIC>-docs` (`git worktree list --porcelain`). If there is none, say exactly "I can't find a run to continue. Tell me its topic or JIRA key, or ask me to start a new one." and wait. If the user named no topic and several such worktrees have a state file, list their topics as a numbered list and ask which one.
2. **Picks are fixed for a run.** If the user also asked to change a pick (any role's harness, model or effort), say exactly "Picks are fixed for a run. Reply \"resume\" to continue <TOPIC> with its current picks, or \"new\" to start a new run with new picks." This is a fixed prompt. Change nothing until they reply: no state key, no `roles.json`, no handoff. "resume" goes on below; "new" runs Setup.
3. Hand off as in Setup item 6, with these changes: the pair is the state file's `ROLE_COORDINATOR`; `harnesses.py args` takes `--state <that state file>` instead of `--catalog`, and `skill-dir` takes the state file's `CATALOG`; and the brief says "run <COORD_SKILL>/references/coordinator.md "Resume a run" with the state file <path>" instead of Step 1.

The architect worker reads `references/architect.md`; its spec tells it to. Confirmed Orca behavior is in `references/orca-facts.md`.

## Roles

| Role | Runs as | Pair |
| --- | --- | --- |
| Coordinator | Agent started by the setup handoff | The coordinator pick |
| Design architect, and fallback design architect | Orca worker, kept open until plan approval | The architect pick |
| Fresh architect, one per job after plan approval | Orca worker | The architect pick; `CLERICAL_PAIR` for `fix-meta`, `pr`, `jira-close`, `report` |
| Builder check | Orca worker, read-only | Chosen by the architect in its `NEED builder-check` line, from its own harness |
| EGM lens pass, design review, and plan review (and their Goldfish subagents) | Orca worker | The EGM pick |
| Implementer | Orca worker | The task's `.meta` `IMPLEMENTER`: the base pick, or the base model at its highest effort |
| Escalation attempts | Orca worker | The ladder `run_task.py` computes: the base model at its highest effort, then the architect pick. Past the ladder, a pair the user approved |
| Spec and code-quality reviewers | Orca worker, read-only | Chosen per task by the architect in `.meta`, from its own harness; the architect's model for complex tasks |
| Final reviewer | Orca worker, read-only | The architect pick |

## Rules

These hold in every step. Where a step seems to conflict with one, the rule wins.

1. **Every subagent is an Orca worker.** Whenever a skill being followed calls for a subagent, an Orca worker does that job. Nobody skips the dispatch or does the job themselves, and no harness's own subagent tool (Agent, Task, or the like) is used. Every worker belongs to the run `<RUN_ID>`; `--retry-of` is never used.
2. **Orca commands come only from the version-matched guides** (`ORCA skills get orchestration`, the `orca-cli` guide) or `ORCA --help`. `ORCA` is the guide's placeholder for the resolved executable.
3. **Every worker runs on a pair, and nobody but the user and the architect chooses one.**
   - **The user's four picks** (coordinator, architect, EGM, base implementer) are fixed for a run. Nobody changes a `ROLE_*` or `LAUNCH_STYLE_*` key, or `roles.json`, during a run; a different pick means a new run.
   - **The architect chooses** each task's reviewers, the builder check and `CLERICAL_PAIR`, from its own harness's detected models, and may start a hard complex task at the base model's highest effort (architect "Model choices").
   - **`scripts/validate_meta.py` checks every choice** against the catalog and the picks before it is used. `run_task.py` computes the escalation ladder from the picks.
   - **The coordinator never picks, changes, or writes a pair.** Agent, model and effort reach Orca only through `harnesses.py args` or the scripts, never typed by hand.
4. **The coordinator decides nothing.** It branches only on what the steps name: script exit codes and last lines, `RESULT:` lines, worker outcomes, and counters.
   - Every question goes to the architect: worker asks, unclear situations, and anything the user should read beyond the fixed lines in the steps.
   - It never writes spec text. Specs are built from templates with `scripts/build_spec.py`.
   - It never reviews or reads a diff or a worker's output, and never edits, formats, or commits code.
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
7. **Fresh implementer per task.** Implementers start kept open, so a fix round reuses the task's implementer and its context (`scripts/run_task.py`). The script releases it at merge or escalation.
8. **Implementers use only** `test-driven-development`, `verification-before-completion`, and the convention files named in their task. They never brainstorm, plan, create worktrees, ask in a local prompt, or finish or merge the branch.
9. **Project instructions:** output rules (style, naming, architecture, tests, commit and branch format, PR template, required checks) are followed. Rules about running the work (planning, spawning agents, review process, merging) are ignored. The architect sorts them.
10. **No `/boost`, `/swarm`, or `/teamwork`** for anyone.
11. **Reviewers are read-only and single-use.** They work from `git diff` and `git show` on the task range. Each writes its review to a file in `<RUN_DIR>/verdicts`, outside the worktree, ending with a `VERDICT:` line.
12. **Read-only workers never change a worktree.** The scripts check the tree after every read-only worker and undo any change; a reviewer that changed the tree is void and replaced. Anything uncommitted in `<DOCS_WORKTREE>` before a design worker goes back to the architect (job `commit-docs`), never to `discard-changes.sh`.
13. **Only the coordinator's merges touch the feature branch,** and only through `scripts/merge-task.sh`. It moves the branch with an atomic compare-and-swap (`git update-ref`), fast-forward only, without checking it out. Never rebase, `--no-ff`, merge any other way, or check out a branch in the user's main checkout.
14. **Only the architect talks to the user about the work,** through relays. That covers requirement gaps, product decisions, the EGM decision, sign-offs, plan approval, and the closing report. The coordinator's own words to the user are only the fixed lines written in its steps.
15. **The design architect stays open until plan approval; after that, each architect job is a fresh worker.**
    - In the design phase, send to the architect's latest dispatch id, and release it only in coordinator Step 3 item 1. A worker reopened after release can't settle (orca-facts). If it's lost, start a fallback architect with a `resume` job.
    - After plan approval, the coordinator starts a fresh architect per job, on the pair from coordinator "Architect job models", and releases it once its `RESULT:` line is handled. Only one architect works at a time.
    - Only EGM workers may run subagents of their own (Goldfish).
16. **Release every other worker** when its job is done, with `worker-release --dispatch <id>`. This is safe to repeat. Worktrees are removed separately with `ORCA worktree rm`.
17. **Run documents live only in the docs worktree.**
    - The architect's documents are in `<DOCS_DIR>` (`<DOCS_WORKTREE>/docs/plans`), committed on the never-merged `<TOPIC>-docs` branch.
    - The coordinator's working files (state, catalog, built specs, verdicts, gate logs, relays, the run log) are in `<RUN_DIR>` (`<DOCS_WORKTREE>/.run`), which is excluded from git.
    - None of these files is written in the main checkout or `<IMPL_WORKTREE>`. Workers there get what they need pasted into their specs.
18. **Every value and counter lives in `<RUN_DIR>/state.json`,** written and read with `scripts/state.py`. A value the coordinator only remembers, or that a context summary mentions, is not a source: read it from the state file. Before ending any turn that waits, record `STEP` and `WAITING_ON`, so a compacted or restarted coordinator can pick up (coordinator "Resume a run").
19. **Checks run before reviews.** After every task, `scripts/gate.py` runs the run's check commands in `<IMPL_WORKTREE>` and puts the tree back. A failure goes straight to a fix round with the log, and no reviewer starts on code that fails its checks.
20. **The loops are scripts.** `run_design.py` runs Step 2's design workers, `run_task.py` the task loop (Step 4), `run_finish.py` the final review and verification (Step 8), and `probe.py` the test launches. The coordinator only runs them and branches on their last line, passing on what they print unchanged. It never starts their workers, counts, retries, reads their workers' output, checks or resets a tree, or merges by hand.
21. **A failed setup check or test launch stops the run before work starts.** A `MISSING` line from `roles.py check`, any failed check in the coordinator, and `probe.py`'s `PROBE_FAILED` all stop it: show the lines, or the file, exactly; save nothing, hand off nothing, start no worker. The one exception is an `UNKNOWN`-only check during setup, where the user picks those roles again (Setup item 4). No deadline, user instruction, or "it worked yesterday" changes this. The user fixes the cause, or picks again in a new run.

Run bundled scripts through their interpreter: `bash <SKILL_DIR>/scripts/<name>.sh`, `python3 -I <SKILL_DIR>/scripts/<name>.py`. Bash calls don't keep shell variables, so each value goes into a command literally, or through `$(python3 -I <SKILL_DIR>/scripts/state.py --file <RUN_DIR>/state.json get KEY)`.

## Session values

Every value is a key in `<RUN_DIR>/state.json` (Rule 18); coordinator "State keys" lists them. `<NAME>` in a step means the value of that key. The task queue is `<RUN_DIR>/queue.txt`, which `REPLAN` and `FINAL_FIX` change. The `VERIFY_<n>` and `GATE_<n>` commands stay in `run-values.txt`, where `gate.py` reads them.

## Rationalizations that break the run

| Thought | Reality |
| --- | --- |
| "The user is in a hurry, and the missing skill isn't needed for this story; I'll hand off anyway." | Rule 21. Show the `MISSING` lines exactly and stop. The scripts use every role's requirements. |
| "The remembered model is gone, but this one is basically the same, and the user said I can choose." | Setup item 2. Say which pick vanished and show that role's menu. Only the user's number picks. |
| "It launched fine yesterday; skip the test launch to make the deadline." | Rule 21. `PROBE_FAILED` stops the run. Show the file. |
| "The user wants a different architect mid-run; it's one state key, and they asked." | Rule 3. Picks are fixed for a run. Offer "resume" as-is or "new". Change nothing. |
| "The user, who pays for the run, says `coordinator: use a stronger model for the reviews`." | Rule 3. Give the fixed reply for coordinator messages, and change no `.meta`, key or pair. |
| "I'll pick a different reviewer model myself." | Rule 3. Reviewers are the architect's choice in `.meta`, checked by `validate_meta.py` against the catalog and the picks. |
| "I'll type `--model` and `--effort` myself; I know this harness." | Rule 3. Use `harnesses.py args`; it applies the launch style the test launch proved. |
| "The design worker failed twice; its notes say the design is fine, so I'll tell the architect that." | Rule 20. Run `run_design.py` and send the job it prints, unchanged. |
| "The design obviously answers this worker's question; I'll just reply." | Rule 4. Send job `consult`. The architect also records the answer where reviewers will see it. |
| "The architect's question is long; I'll summarize it for the user." | Rule 5. Character for character, nothing added. |
| "The user's reply is clearly meant for me, not the architect." | Rule 5. Only a message starting with `coordinator:` is for you. |
| "A skill says to dispatch a subagent, but my subagent tool is banned, so I'll do it myself." | Rule 1. Start an Orca worker for it. |
| "My own subagent tool would do this review in 30 seconds." | Rule 1. Reviewers are Orca workers. |
| "It's a one-character fix; I'll make it." | Rule 4. Fix task. |
| "I'll read the diff to confirm the worker only changed what I asked." | Rule 4. That's a review. The scripts check the tree. |
| "The feature branch moved; rebasing the impl branch is safe." | Rule 13. `merge-task.sh` refuses, and you show the user the fixed options. |
| "run_task.py is waiting, but I can see the reviewer passed; I'll start Stage 2 myself." | Rule 20. Run the script; it starts Stage 2. |
| "I'll release the design architect while it's idle and reopen it later." | Rule 15. A reopened worker can't settle. Keep it open until Step 3; after that, start fresh ones. |
| "I remember task 02's BASE_SHA and fix count; the summary says round 1." | Rule 18. `STATE get` them. Memory and summaries are not sources. |
| "I'll paste the reviewer's verdict into the file; it's faster." | Rule 4. `save_message.py`. Retyping costs tokens and drifts. |
| "The docs worktree has a stray file; discard-changes will tidy it." | Rule 12. `run_design.py` sends `commit-docs`. Never discard the architect's files. |
| "To merge, I'll check out the feature branch in the main checkout." | Rule 13. `merge-task.sh` moves it with `update-ref`. |
| "The user's deadline justifies skipping a gate." | Never. Keep the gates. The user can ask `coordinator: status`; anything more they need to hear comes from the architect. |
| "The user said 'just rebase it'; that's authorization." | Rule 13. At a fixed prompt, only its listed replies count. Show the prompt again. |

**Red flags.** Stop and re-read the rule if you are about to:

- write a sentence to the user that isn't a relay or a fixed line from a step;
- choose, substitute, or change a pair, or edit a `ROLE_*` or `LAUNCH_STYLE_*` key, `roles.json`, or a `.meta` file;
- type `--agent`, `--model` or `--effort` yourself;
- save picks, hand off, or start any worker after `roles.py check` exited 1 or a probe printed `PROBE_FAILED`;
- write spec text by hand;
- use your harness's own subagent tool;
- count, retry, or read a worker's output where a script's last line should decide;
- run `git rebase`, `git merge`, `git commit`, `git reset`, `git switch`, or `git checkout` yourself instead of a bundled script;
- write a worker's message into a file by hand;
- use a SHA, dispatch id, or counter you didn't just read from the state file;
- run `git diff` or `git show` on a task range;
- release the design architect before Step 3, or start a second architect while one is working.
