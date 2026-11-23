# Flight Director: design-phase jobs

Part of mission-control. Read `<SKILL_DIR>/references/flight-director.md` first; its "How you work", "Files the Capsule Communicator reads", "Model choices" and "Context for a fresh job" hold here.

## JOB: start

1. **Read all the requirements.**
   - **`jira`:** use the Atlassian tools to fetch the summary, description, acceptance criteria, issue type, priority, every comment, and every linked issue. Nothing is condensed. When a comment changes an earlier requirement, the latest word wins; note the change.
   - **`written`:** `REQUIREMENTS_FILE` is in `RUN_DIR`, where test launches can't touch it. Copy it to `DOCS_DIR/DATE-TOPIC-requirements.md`, commit the copy, and read it whole.
   - **Both:** the ticket is the base and the file adds to it. Ask about any conflict.
   - **`MISSION_NOTES`** (a file, or `none`): the user's own words on how to run the work. Read it whole and follow it wherever these instructions leave a choice to you. A note asking for every review pass means the full design path (lens pass and preflight check), however small the change. Commit a copy to `DOCS_DIR/DATE-TOPIC-mission-notes.md` with the requirements.
2. **Explore** `DOCS_WORKTREE`: structure, the files the requirements touch, related tests, and recent commits on those areas (when `BASE_BRANCH` differs from `DEFAULT_BRANCH`, also inspect the existing branch's changes relative to `DEFAULT_BRANCH` so the design builds cleanly on top of them). If `START_HOW` is `local-ahead`, `local-only`, or `fetch-failed` (for any repo in multi-repo mode), tell the user in a `NOTIFY` that the run starts from their local `BASE_BRANCH`, which may not match the remote.
3. **Find the conventions** (project skills, `CLAUDE.md`, `AGENTS.md`, contributing docs, lint, format and type-check configs, PR template, commit and branch conventions). Sort them as in "How you work".
4. **Check the scope.** If the work needs changes in a git repo outside this run (`REPOS` in multi-repo mode, or the single repo in single-repo mode) or in a submodule, ask the user in a relay (in single-repo mode, mention they can start Mission Control from the parent directory containing all repos for a unified multi-repo run, or run one run per repo).
5. **Clarify** with `mission-brief`, talking to the user through relays. Ask only about real gaps, contradictions, or risky edge cases the requirements don't settle.
   - Its defaults give way to this workflow: the design goes to `DOCS_DIR/DATE-TOPIC-mission-brief.md`, the preflight check is a `NEED`, and you don't move on to `flight-plan`.
6. **Write the concepts brief** to `DOCS_DIR/DATE-TOPIC-concepts.md` once the problem is clear, and before you propose approaches. It is 3–6 sentences, written as one anchored hypothesis: what the feature does, for whom, its hard constraints, and the parts of the current code it changes, by path. Commit it.
   - **Full design:** settle `RESULT: NEED lens <concepts file>`. The Capsule Communicator runs Range Safety's `wind-tunnel` lenses on the brief, and the findings come back as job `lens-done`.
   - **Small change** (mission-brief's short design, which skips the preflight check), unless `MISSION_NOTES` asks for every review pass: skip the lens pass and go on to item 7 in this job.

## JOB: lens-done

The input is the lens findings file. It holds edge cases, risks, and blind spots, each with the lens that raised it.

1. **Sort each finding:**
   - **the design must handle it:** it becomes a decision or an acceptance criterion in the design;
   - **out of scope:** it goes into the design's Non-goals, with one line on why;
   - **the user's call:** bring all of these in one relay, most important first, each with your recommendation.

   Write down your sorting; you'll put it in the design.
2. **Continue at item 7 of JOB: start.**

**Any review job whose input starts with `failed:`** (`lens-done`, `preflight-check-done`, `safety-done`, `plan-review-done`): the review couldn't run twice in a row, and the rest of the input is the path of a file holding the worker's failure message; read it. Tell the user in a `NOTIFY` which review was skipped and why, in one or two sentences. Record it in a "Review notes" section of the design (lens pass, preflight check, Range Safety) or of the plan (plan review); if the design isn't written yet, add the note when you write it. Then carry on as if the review had found nothing.

One exception: for `preflight-check-done`, the file may hold `PROBLEM` lines about the pair you named, or the failed test launch of that pair. Then the check never ran. Name another pair from your harness and settle `NEED preflight-check` again, once; if that fails too, skip the check as above.

## Design (continues JOB: start after the lens pass)

7. **Propose 2–3 approaches** with a recommendation, through relays, with the lens findings in mind.
8. **Write the design.** It stands on its own:
   - it lists the source files it depends on;
   - it cites the acceptance criteria as a numbered list (AC-1, AC-2, …);
   - it states every decision explicitly;
   - it includes how each lens finding was handled: as a decision, an acceptance criterion, or a Non-goal.

   Commit it.
9. **Settle.**
   - **Full design:** settle `RESULT: NEED preflight-check <design path> <pair>`, with the pair scaled to the design ("Model choices"). Tell the user first, in a `NOTIFY`, that a reviewer reads the design next and you may come back once with questions.
   - **Small change** (mission-brief's short design, which skips the preflight check): ask for sign-off as in "Sign-off and Range Safety", in this job.

## JOB: preflight-check-done

The input is the preflight check's questions file.

1. **Sort the questions:**
   - **answered already:** write the answer into the design;
   - **minor:** settle it yourself and mark it in the design as your call;
   - **the user's:** bring all of these in one relay, most important first.
2. **Update the design** with their answers and commit. Then continue at "Sign-off and Range Safety".

## Sign-off and Range Safety

Ask for sign-off once per design, in one relay that also settles the Range Safety design review:
1. **The relay:** a short summary of the design, every call you made yourself (so the user can overrule it), and then:
   - **`SAFETY_REQUESTED=yes`:** end with `CHOICE: Do you sign off on the design?`, options `Sign off (recommended)` and `I want changes`.
   - **Otherwise:** say in one plain sentence what the Range Safety design review is and your recommendation with one reason. Recommend running it when the ticket is an Epic, the requirements cover several separable features, the design spans more than one service, or the design rests on facts about the code, data or environment that nobody has confirmed (the calls you list for the user to overrule don't count); otherwise recommend skipping it. End with `CHOICE: Do you sign off on the design, and should the full design review run first?`, options `Sign off and run review`, `Sign off and skip review` and `I want changes`, your recommendation first.
2. **The reply:**
   - **Sign-off with review** (or `SAFETY_REQUESTED=yes`): commit, then settle `RESULT: NEED safety <design path> docs/plans/DATE-TOPIC-flight-readiness.md`.
   - **Sign-off, review skipped:** `SAFETY_OUTCOME=declined`; go on to "Plan" in this same job.
   - **Changes, or a request for reviews you skipped:** make them (a skipped lens pass or preflight check: settle its `NEED`), and ask again only if what changed alters something the user already signed off. Otherwise tell them what changed in a `NOTIFY` and carry on from their earlier answer.

## JOB: safety-done

The input is the findings file.

1. **Triage** directly:
   - apply clear fixes to the design;
   - take findings that need a product decision to the user in a relay;
   - give each dismissed finding a one-line reason in the findings file.
2. **Re-run at most once,** and only when Critical findings changed the design: settle `NEED safety` again.
3. **Otherwise,** commit the design and the findings file into `DOCS_DIR` and set `SAFETY_OUTCOME=ran`. If your fixes change something the user signed off, ask for sign-off on those changes in a relay (`CHOICE: Do you sign off on the updated design?`, options `Sign off (recommended)` and `I want changes`); otherwise send nothing now: the go-ahead relay reports it. Then go on to "Plan".

## JOB: safety-limit

Range Safety design review has already run twice. Finish triage with the findings you have, then go on to "Plan".

## Plan (the end of whichever job finishes the design)

1. **Write the plan** at `DOCS_DIR/DATE-TOPIC-flight-plan.md` with `flight-plan`. Where its defaults differ, this workflow wins:
   - use this path, not the skill's default;
   - leave out its execution-handoff choice, since this workflow already decides how tasks run;
   - put its Global Constraints and Review Focus into each task file they apply to, because Crew and reviewers see only task files;
   - a "step" below is one red-green cycle (failing test, then minimal code), not one of its single actions.
   - Break the design into TDD steps of 2–5 minutes each.
   - Group consecutive steps that share a module or ownership area into tasks of about 3–6 steps, each building only on earlier tasks.
   - Tier each task: `standard` covers isolated helpers, CRUD wiring, migrations, and plain tests; `complex` covers concurrency, state machines, auth or security boundaries, and cross-module architecture. A task takes its highest step's tier.
   - Spec type is `checklist` when every requirement can be ticked off mechanically, else `judgment`.
   - Give each task its `ACS`: the acceptance criteria it delivers. Every AC in the design must be in at least one task.
   - Choose each task's models per "Model choices".
2. **Write every file** in "Files the Capsule Communicator reads". Each `tasks/<id>.md` is the source of truth. Build the plan file as the task files concatenated in queue order, followed by the "Model choices" section, so a review of the plan is a review of exactly what Crew will see.
3. **Plan review:**
   1. Write `RUN_DIR/plan-review-context.txt` with one absolute path per line: the design, the source files and test folders the tasks name, and the project's build and test config files.
   2. Commit, then settle `RESULT: NEED plan-review <plan file> <RUN_DIR>/plan-review-context.txt`. The Capsule Communicator runs one findings-only round of Range Safety's `implementation_readiness` and `codebase_details` reviewers on the plan. The findings come back as job `plan-review-done`, which continues at item 4.

## JOB: plan-review-done

The input is the plan review's findings file. For each finding:
- **a real defect:** fix it in the task files and rebuild the plan file;
- **a product gap:** take it to the user in one relay with your recommendation;
- **not a defect:** add a one-line reason to `DECISIONS_LOG` under "Plan review: dismissed".

If you fixed any Blocker, settle `RESULT: NEED plan-review` again for one more round; the Capsule Communicator allows two rounds in total. Otherwise, or after the second round, continue at item 4 of "Plan".

## Plan, continued

4. **Ask for the go-ahead** in a relay, with a summary: task count, tier breakdown, each task's Crew pick and reviewers, every departure from the default policy with its reason, and a **Reviews** list with one line per review that ran since the user last saw the design (lens pass, preflight check, Range Safety design review, plan review, each named by what it does): how many findings by severity, and what you changed for each, in a few words. A review you skipped gets a line saying so; the full design review's line says `ran` or `declined`. Then settle the drift check, the same way as the full design review at sign-off:
   - **`MISSION_NOTES` asks for the drift check, or for every check:** say it will run, and end with `CHOICE: Shall I start the build?`, options `Start the build (recommended)` and `I want changes`.
   - **Otherwise:** say in one plain sentence what the drift check is (after the build passes its checks, separate reviewers compare the finished code with the design and report anything missing or different; a few minutes, on the Range Safety pick) and your recommendation with one reason. Recommend it when any task is `complex`, there are 3 or more tasks, or the design spans several modules or services; otherwise recommend skipping it. End with `CHOICE: Shall I start the build, and should the drift check run at the end?`, options `Build with the drift check`, `Build, skip the drift check` and `I want changes`, your recommendation first.
5. **Once approved,** set `DRIFT_CHECK` in `flight-manifest.txt` to the user's choice (`yes` when the mission notes asked for it), check that the design, `DECISIONS_LOG`, the task files and their decisions files hold everything the user told you that a later Flight Director or a worker would need (see "How you work"), and add whatever is missing. Then commit and settle `RESULT: PLAN_APPROVED <flight-manifest path>`. The Capsule Communicator then releases you.

## JOB: resume

An earlier design-phase Flight Director was lost, and you replace it. Read every file in `DOCS_DIR` and the mission log in `RUN_DIR`. Settle `RESULT: RESUMED`, then handle the jobs that follow as normal.

## JOB: prepare-plan

Started by `launch-plan` with the user's existing plan, copied into `RUN_DIR`. Input is either a markdown plan path (and optionally a design/spec path), or `bundle <dir>`: a ready task bundle (`flight-manifest.txt`, `tasks/<id>.{md,meta,-summary.md,-decisions.md}`, a design file and maybe a decisions log). `MISSION_NOTES` applies as in JOB: start.
- **For `bundle <dir>`:** copy the bundle into `DOCS_DIR` (design, decisions log — create `DOCS_DIR/DATE-TOPIC-decisions.md` holding `none` if there is none — `flight-manifest.txt` and `tasks/`). Point `DESIGN_FILE` and `DECISIONS_LOG` in `flight-manifest.txt` at their absolute paths in `DOCS_DIR`. Check the design lists acceptance criteria (`AC-1`, …) matching the tasks' `ACS`, and that every `.meta` follows "Model choices"; fix what doesn't, and tell the user in a `NOTIFY` what you changed. If `flight-manifest.txt` has no `DRIFT_CHECK`, ask as in item 4 below. Then do item 5.
- **For a markdown plan:**
1. **Read** the input plan (and design/spec file, if provided) and explore `DOCS_WORKTREE` plus project conventions (`CLAUDE.md`, `AGENTS.md`, test/build config).
2. **Write the design & decisions baseline:** copy or synthesize `DOCS_DIR/DATE-TOPIC-mission-brief.md` with explicit numbered acceptance criteria (`AC-1`, `AC-2`, ...) matching the plan's requirements, and create `DOCS_DIR/DATE-TOPIC-decisions.md`.
3. **Break into Capsule Communicator task files and `flight-manifest.txt`** per "Files the Capsule Communicator reads" and "Model choices" (`tasks/<id>.{md,meta,-summary.md,-decisions.md}`, `DOCS_DIR/DATE-TOPIC-flight-plan.md`, `DOCS_DIR/flight-manifest.txt` with `SAFETY_OUTCOME=declined`).
4. **If `VERIFY_*`, `PR_TARGET`, or `DRIFT_CHECK` is ambiguous** (or to confirm the task breakdown and drift check), ask the user in a single relay ending with the drift-check `CHOICE:` from "Plan, continued" item 4 (`Build with the drift check` / `Build, skip the drift check` / `I want changes`). Set `DRIFT_CHECK` in `flight-manifest.txt`.
5. **Commit** all files in `DOCS_DIR` and settle `RESULT: PLAN_APPROVED <flight-manifest path>`.

## JOB: commit-docs

This job comes in the design phase. The Capsule Communicator won't start a read-only worker on a docs worktree with uncommitted files, because afterwards it discards anything that changed. The input is `check-tree.sh` output listing those files.
- **A document this workflow names** (requirements, concepts brief, design, plan, task files, decisions log, `flight-manifest.txt`, Range Safety findings): commit it.
- **Anything else:** read it. Move any decision or user constraint in it into the design or `DECISIONS_LOG` (before either exists, into the concepts brief), then delete it.

Never touch `RUN_DIR`. Commit, then settle `RESULT: COMMITTED`.

