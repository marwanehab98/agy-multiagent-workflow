# Live test of configurable roles: issues found

Test project: `/home/marwan/Projects/omw-add-test` (add-two-numbers CLI), run topic `add-cli-sum`, run id `run_e6ed59b6a572`.
Skill under test: master `13b4d58`. Picks: coordinator `claude:sonnet@low`, architect `claude:sonnet@medium`, EGM and base `antigravity:gemini-4-hydrogen@low`.

Status: the run finished. Every issue below, plus A–E found when the run was checked afterwards, is fixed on branch `live-fixes` (see "Fixes" at the end).

## Issues

### 1. probe.py discards the requirements file (severity: high, run-breaking)
- **Where:** `scripts/probe.py:157` (`finish()` calls `discard-changes.sh` on any dirty docs worktree); ordering in `references/coordinator.md:61-69` (Step 1 item 10 probes, item 11 saves requirements).
- **What happened:** the coordinator wrote `docs/plans/2026-11-15-add-cli-sum-requirements.md` (untracked) and set `REQUIREMENTS_FILE` before the probe workers had finished. The first probe's tree check then deleted it. `.run/run-log.md`:
  `probe claude:sonnet@medium: docs worktree changed (UNEXPECTED_CHANGE ?? docs/plans/2026-11-15-add-cli-sum-requirements.md); discarded: DISCARDING: ?? docs/plans/2026-11-15-add-cli-sum-requirements.md CLEAN head=1f6511e (exit 0)`
- **Effect:** the architect started with only the README. It asked the coordinator for the requirements (relay `001-to-user.md`), so the user had to paste them again.
- **Cause:** a coordinator order slip (the `WAIT` branch says to end the turn; it ran items 10 and 11 in one batch). The skill also leaves the file exposed: it is uncommitted, and any later tree check would discard it too.
- **Suggested fix:** in item 10's `WAIT` row, say "do not write the requirements file until `PROBED`". Better still, make the file survive probes: commit it, or write it only in the architect's `start` job, or have probes ignore `docs/plans/`.

### 2. `roles.py menu --harness <unknown>` prints a bare `ERROR 'agy'` (severity: low)
- **Command:** `python3 -I scripts/roles.py menu --catalog <CATALOG> --harness agy`
- **Output:** `ERROR 'agy'` (a raw `KeyError`). It doesn't say that the valid harness names are `claude` and `antigravity`.
- **Suggested fix:** catch the unknown harness and print `UNKNOWN harness agy (known: claude, antigravity)`.
- **File:line:** to be filled in when fixing (look in `scripts/roles.py`, the `menu` handler).

### 3. WITHDRAWN: handoff brief and back-references
Not a defect. The request "use your list as the requirements" was an artifact of this test, where the assistant wrote the requirements. Real users write the requirements or give a JIRA key, so the brief carries them verbatim. The setup session did paste the list into the brief by hand in this run, which is a test-only workaround.

### 4. The skill's prose doesn't name the `run-create` JSON field (severity: info)
- **Command:** `ORCA orchestration run-create --objective "orca-multiagent-workflow: add-cli-sum" --json`
- **Output:** `{"ok":true,"result":{"runId":"run_e6ed59b6a572","objective":"orca-multiagent-workflow: add-cli-sum"}}`
- **Answer to test check 2:** the id is in `result.runId`. `coordinator.md` Step 1 item 9 says "record its run id as `RUN_ID`" and doesn't name the field. The coordinator got it right.
- **Suggested fix:** name `result.runId` in item 9, and add it to `orca-facts.md`.

### 5. The user has to ask for EGM by name, and `EGM_REQUESTED` stays `no` (severity: low; revised after the run showed the path works)
- **Where:** `references/coordinator.md:43` (`EGM_REQUESTED` is set once at Step 1); `references/architect.md:140,170,182-184`.
- **What happened:** the user's first reply, "yes, but don't skip the extra review passes" (`002-from-user.md`), got the lens pass and the builder check, but the architect still said "I'll skip the extra whole-design review (EGM)" (`004-to-user.md`). The user had to say "yes but do not skip the extra whole-design review (EGM)" (`004-from-user.md`) before EGM ran. It then ran (`design egm dispatched ... settled succeeded`, `EGM_RUNS=1`).
- **Remaining problem:** the state key still reads `EGM_REQUESTED=no` after EGM ran. The user also could not have known to name EGM, because the architect called the review passes "extra review passes" and "the reviewer".
- **Suggested fix:** at the first sign-off, have the architect list the review options in plain words (lens pass, builder check, whole-design EGM review) and ask which to run. Update `EGM_REQUESTED` when the architect settles `NEED egm`, so the state matches what happened.

### 6. The architect uses internal terms with the user and re-asks for sign-off (severity: low)
- **Where:** `.run/relay/002-to-user.md` and `004-to-user.md`.
- **What happened:** the user was told the architect "skipped the extra review passes" and later that "the reviewer read the design", which are internal jobs (lens pass, builder check) the user had not heard of. After the user signed off, the architect asked for sign-off again on a design it said changed nothing the user had approved (`003-to-user.md`).
- **Suggested fix:** in `architect.md`, tell the architect to describe reviews in plain words, and not to ask a second time when the lens pass changed nothing that needs a decision.

### 7. The catalog offers efforts that Orca rejects for haiku (severity: high, run-stopping)
- **Where:** `scripts/harnesses.py:141-143` (`models()` reads the effort list once from `claude --help --effort (low, medium, high, xhigh, max)` and gives it to every model); `scripts/validate_meta.py` and `scripts/roles.py menu` trust that catalog.
- **What happened:** the architect picked `CLERICAL_PAIR=claude:haiku@medium`, which `validate_meta.py` accepted because the catalog lists all five efforts for haiku. At Step 3 item 8, `PROBE --set step3` failed. The coordinator showed:
  `ERROR ORCA_FAILED worker-start: {"ok": false, "error": {"code": "invalid_argument", "message": "Agent claude model haiku does not support effort medium. ..."}}` (command: `orca orchestration worker-start --task task_642fb6443242 --worktree path:<docs worktree> --agent claude --model haiku --effort medium --json`).
- **Root cause:** Orca keeps its own per-model effort options. In its bundle (`main/chunks/agent-choice-args-*.js`) haiku has `options: []`, meaning no effort at all. fable, opus and sonnet have effort options. The `claude` CLI itself accepts `--model haiku --effort low|medium` (I ran both: each answered `ok`), so only Orca rejects them.
- **Effects:** (a) the setup menu offers haiku with five efforts, so a user picking `claude:haiku@<effort>` for any role would fail later; (b) the failure comes after the whole design phase (lens, builder check, EGM, plan review, sign-off) and the user's go-ahead, so that work is wasted; (c) the run is stopped by Rule 21 with no way to continue.
- **Suggested fix:** get each model's efforts from Orca, or from a table with haiku as `["default"]` (the code already supports `default`, which launches with no `--effort`). Also have `validate_meta.py` or `roles.py check` test-launch or otherwise validate every pair before approval, so a bad pair is caught at the plan job and not at Step 3.

### 8. A launch rejected by Orca is reported as `ERROR`, not `PROBE_FAILED` (severity: low)
- **Where:** `scripts/probe.py` (the `worker-start` call in the probe), with `coordinator.md:61-67`.
- **What happened:** an Orca `invalid_argument` on a pair came back as `ERROR ORCA_FAILED worker-start ...` (exit 2). `PROBE_FAILED <pair> <file>` (exit 40) was not used, so there is no file for the user to read and no named pair. The coordinator handled it correctly (showed the line and stopped), but the message names no pair.
- **Suggested fix:** when `worker-start` fails with `invalid_argument` for a pair, print `PROBE_FAILED <pair> <file>` with the Orca message in the file.

### 9. The baseline check cannot pass on a repository with no tests (severity: medium)
- **Where:** the baseline run after plan approval (`.run/gates/baseline-1.log`) and the architect's `baseline-failed` job (`.run/results/architect-6.md`).
- **What happened:** the first baseline ran `python3 -m unittest -v` and `python3 -m py_compile add.py test_add.py` on the start commit, which holds only `README.md`. It failed (`Ran 0 tests`, exit 5; `No such file or directory: 'add.py'`, exit 1). That is expected for a new project. The coordinator started a `baseline-failed` architect job, which changed `VERIFY_1` and `VERIFY_2` to pass with a note when neither file exists (commit `4488a29`). The skill handled it, but the workaround weakens the gate: the checks now pass vacuously if the task creates neither file.
- **Suggested fix:** let the plan mark checks that only apply after the task (for example `AFTER_FIRST_TASK`), so a greenfield baseline skips them without making them vacuous later.

### 10. An untracked `__pycache__` makes the commit stage loop forever (severity: high, run-stopping)
- **Where:** `scripts/run_task.py:193-200` (`do_check`: `check-tree.sh` exit 2 starts a `commit-uncommitted` worker and re-enters the check), `scripts/check-tree.sh` (treats every untracked file as `UNEXPECTED_CHANGE`).
- **What happened:** the implementer ran `python3 -m unittest` and `py_compile` as the task asks, which created `__pycache__/add.cpython-313.pyc` and `__pycache__/test_add.cpython-313.pyc`. All 5 commits were made and all 10 tests passed, but every commit worker found nothing to commit, and the check saw the untracked files again. `.run/run-log.md`: `01 commit reused ...` / `01 commit settled succeeded`, repeated at 17:14:01 to 17:15:13 (five workers), until the coordinator stopped it by hand. There is no cap on this stage: the module docstring (`run_task.py:42-43`) caps only fix rounds, and the commit stage in `do_check` (lines 193-200) has no counter.
- **Cause:** the loop has no cap on commit rounds, and it can't tell "nothing left to commit" from "untracked junk". Any Python project, and any other stack with build output, hits this.
- **Suggested fix:** (a) cap the commit stage (for example two rounds, then escalate); (b) when a commit worker settles with nothing to commit, stop looping; (c) ignore build output in the check, for example by having Step 1 item 5.3 add `__pycache__/`, `node_modules/` and similar to `info/exclude` next to `.run/`, or by letting the task spec list ignorable paths.
- **Test-only workaround applied:** appended `__pycache__/` to `/home/marwan/Projects/omw-add-test/.git/info/exclude` (shared by all worktrees). `check-tree.sh <impl> f562f62` then printed `CLEAN` (exit 0).

### 11. The setup questions are plain-text number lists, not a multiple-choice UI (severity: low, user feedback)
- **Where:** `SKILL.md:36` (Setup item 3: "each a numbered list in plain text, not a multiple-choice tool").
- **What happened:** all twelve setup questions (harness, model and effort for four roles) were plain text, and the user had to type the number each time. A typed word such as "claude" is refused, so a wrong reply costs a round trip.
- **User's wish:** use the multiple-choice selection UI (`AskUserQuestion` in Claude Code).
- **Things to settle when fixing:** (a) `AskUserQuestion` takes 2 to 4 options per question, and the antigravity model menu has 7 rows, so those need splitting (for example the 3 most useful models plus "Other", or two questions); (b) the setup session may run on a harness without such a tool, so keep the plain-text list as the fallback; (c) the "only a number the user picks from the menu counts" rule (Setup item 2) needs rewording for a selected option.

### 12. The EGM decision was never offered as a choice (severity: medium, user feedback; adds to issue 5)
- **Where:** `references/architect.md:181-185` ("EGM decision"): with `EGM_REQUESTED=no` the architect asks only when the ticket is an Epic, covers several features, spans more than one service, or rests on unconfirmed assumptions; otherwise it sets `EGM_OUTCOME=skipped` itself. `references/coordinator.md:43` sets `EGM_REQUESTED` from the request alone.
- **What happened:** for this small change the architect decided on its own to skip EGM and told the user only afterwards, in passing ("I'll skip the extra whole-design review (EGM)"). The user had to override it twice (`002-from-user.md`, `004-from-user.md`) before EGM ran.
- **User's wish:** always ask, as a multiple-choice question, and state the architect's recommendation. Here that would be "Run EGM review / Skip it (recommended: skip, because this is a small single-feature change)".
- **Things to settle when fixing:** relays are character-for-character text (Rule 5), so a choice UI means a new relay type the coordinator renders as a multiple-choice question, with the architect supplying the options and the recommended one; the reply goes back unchanged. The same applies to the other fixed-choice relays (design sign-off, plan go-ahead, "keep the branch").

## Test-only hand edits (not skill behavior)

These were made by the test owner with the user's approval so the run could continue after issue 7. Everything after this point in the run is on a hand-edited run.

- **17:1x, after the Step 3 probe failed:** `CLERICAL_PAIR` changed from `claude:haiku@medium` to `claude:sonnet@low` in `docs/plans/run-values.txt` (docs branch commit `b4728a2`, "TEST WORKAROUND ...") and in `.run/state.json` (`state.py set CLERICAL_PAIR=claude:sonnet@low PROBE_HEAD=b4728a2...`). `PROBE_HEAD` was moved to the new commit so the probe's tree check would not discard it.
- **Why not `claude:haiku@default`:** that is the pair Orca would accept (haiku has no effort options), but `validate_meta.py harness-pair` rejects it (`PROBLEM pair 'claude:haiku@default': effort default not detected for haiku; use one of low medium high xhigh max`). So with the current catalog there is no valid way to use haiku at all. This belongs under issue 7.
- **During the task loop:** added `__pycache__/` to `.git/info/exclude` of the scratch repo (see issue 10).
- **Resume path:** the old coordinator stopped on `ERROR` (Rule 21). The run was then continued through SKILL.md "Continue a run" (a fresh coordinator on the state file's `ROLE_COORDINATOR`), which also tests that path.

## Observations that passed

- **Detection and menus (check 1):** menus look right; harness values are `claude` and `antigravity`. `roles.py check` exited 0 with four `OK` lines and no `NOTE`.
- **Handoff:** `orca agent launch --agent claude --model sonnet --effort low --prompt ... --json` worked. The result had `outcome.kind` `structured`, `prompt.outcome` `journaled`, and the worktree id `b5905f72-5381-4209-9154-d5719e0fd79f::/home/marwan/Projects/omw-add-test`.
- **`skill-dir` output:** `harnesses.py skill-dir` returns the path inside the Claude profile (`/home/marwan/.config/orca/claude-profiles/<id>/home/skills/orca-multiagent-workflow`), not `~/.claude/skills/...`. That is fine, but it explains why `SKILL_DIR` in `state.json` looks different from the skill's install path.
- **Test launches (check 3):**
  - `claude:sonnet@medium`: launch style `flag`.
  - `antigravity:gemini-4-hydrogen@low`: `slug`.
  - `antigravity:gemini-4-hydrogen@xhigh` (the `BASE_TOP` pair): `slug`.
  - All three probes settled.
  - Apart from issue 1, the docs worktree was clean after each probe.
- **Design-phase lens pass (check 4, partly):** `run_design.py` dispatched the lens pass to `antigravity:gemini-4-hydrogen@low` (`ctx_d090519c189b`) at 16:42:51, and it settled at 16:44:26 with 10 findings in `.run/lens-1.md`. The architect then committed a refined design. `DOCS_HEAD` follows the commits, and the docs worktree was clean afterwards.
- **Builder check:** the architect named `claude:sonnet@low`. `run_design.py` first test-launched that pair (`flag`, 16:45:14), then ran the check (`ctx_8d3fb447c4d2`), which settled 11 seconds later with "nothing important left".
- **Design-phase EGM review:** dispatched to `antigravity:gemini-4-hydrogen@low` (`ctx_2e90bff8bc12`) at 16:49:45 and settled at 16:54:14 (4.5 minutes). It found 12 issues (1 critical); the architect fixed 10 and dismissed 2. The architect's commits survived each script step (no discard).
- **Plan review:** `antigravity:gemini-4-hydrogen@low` (`ctx_89e7eada150e`), 16:57:50 to 17:01:23. It found no blockers and five minor points, which the architect fixed (commit `f30b36e`).
- **Not yet exercised:** the stress checks, the task loop and `run_finish.py`.

## Final result of the run

- Run `run_e6ed59b6a572` finished: one task, merged as `2ae409e` on branch `add-cli-sum`; the final review passed (`final-1.md`), verification exited 0, and the user chose "keep the branch" (no remote, no PR).
- The delivered tool was checked outside the run against the requirements: `2 3` prints `5`, `0.1 0.2` prints `0.3`, `1.5 1.5` prints `3`, `-3 1` prints `-2`, `abc 1` and wrong argument counts print a one-line usage error on stderr and exit 1, and `python3 -m unittest` runs 11 tests, all passing.
- The stage 1 reviewer caught a real defect (duplicated test classes that shadowed an edge-case test, so it never ran; `.run/verdicts/01-s1-1.md`), the fix round corrected it, and the second review passed. That is the review loop working as designed.
- The same facts, with the launch styles, are in `references/orca-facts.md` under "Launch styles".
- **Not run:** the optional stress checks (a message during an EGM review worker; a resume during a builder-check test launch).
- **Fix order suggested:** issues 7 and 10 first (each stops a run), then 1, then 9, then 12 and 5 (EGM choice), then the rest.

## Found when checking the run afterwards

- **A. Resuming overwrote an earlier architect result (medium).** `results/architect-1.md` (16:42, the first job's result) was replaced at 17:11:18 by the resumed coordinator's save of the `baseline-failed` result; `gates/baseline-2.log` was rewritten the same way. `coordinator.md` said `<n>` is the next free number, and the fresh coordinator used 1 anyway.
- **B. The architect's `RESULT:` line carried prose (medium, latent).** `RESULT: RUN_VALUES_FIXED. The baseline failed because ...`. The coordinator coped, but the scripts compared words exactly (`run_task.py` `len(words) == 5` for `ESCALATE`, `run_finish.py`, `run_design.py`), so the same habit on `ESCALATE`, `STOP`, `META_FIXED` or `NEED` would have stopped the run.
- **C. The final review skipped AC-7 (low).** `verdicts/final-1.md` lists AC-1 to AC-6 only, and the run report repeats it.
- **D. Resume re-ran a passed baseline (info).** Resuming at `3 setup` redid Step 3 item 5.
- **E. Issue 7's fix needs `@default` accepted everywhere (info).** Once haiku is listed as default-only, the menus and `validate_meta.py` must accept `claude:haiku@default`.
- **Corrections to the issues above:** issue 10's code is at `run_task.py:186-197`; its loop ended because of the `info/exclude` workaround, not by hand; issue 6's sign-off was asked three times (relays 002, 004, 005).

## Fixes (branch `live-fixes`)

| Issue | Fix | Checked by |
| --- | --- | --- |
| 1 | Written requirements go to `<RUN_DIR>/requirements.md` (git-excluded, never touched by tree checks); the architect copies and commits them | coordinator scenario 6 |
| 2 | `roles.py menu` prints `UNKNOWN harness <h> (known: ...)` / `UNKNOWN model ...`, exit 1 | `test_roles` |
| 4 | Step 1 item 9 names `result.runId` | coordinator scenario 7 |
| 5, 12 | One "Sign-off and EGM" relay: plain-words EGM description, a recommendation, and a choice block; `NEED egm` sets `EGM_REQUESTED=yes` | architect scenario 1, `test_run_design` |
| 6 | Reviews named by what they do; sign-off asked again only when a signed-off point changed, otherwise `NOTIFY` | architect scenarios 1–3 |
| 7, E | Claude models Orca gives no effort (haiku) are `default`-only in the catalog | `test_harnesses` |
| 8 | An Orca-refused `worker-start` counts as that launch style failing, so it ends in `PROBE_FAILED <pair> <file>` | `test_probe` |
| 7 (late failure) | A failed Step 3 probe of an architect-chosen pair returns `FIX_META <file>` with `PROBLEM` lines instead of stopping the run | `test_probe`, coordinator scenario 5 |
| 9 | `BASELINE_SKIP` in run-values; `gate.py --skip`; checks stay unconditional | `test_tools`, architect scenarios 4–5 |
| 10 | One commit round per implement/fix round, then leftovers are removed and logged; Step 1 excludes common cache folders | `test_run_task` |
| 11 | Setup menus use a multiple-choice tool, paged 3 + `More`; one-row menus aren't asked; a typed row value counts | setup scenarios 1–5 |
| 12 (other relays) | Architect `CHOICE:` blocks, rendered by the coordinator as options; the reply is passed back verbatim | coordinator scenarios 1–2 |
| A | `save_message.py` and `gate.py` take `{n}`; `save_message.py` never overwrites (`EXISTS`) | `test_tools`, coordinator scenario 3 |
| B | `common.result_words` cuts the line at the first sentence break and drops trailing punctuation; the architect's settle has a fixed two-part shape | `test_run_task`, `test_run_finish`, `test_run_design`, architect scenario 5 |
| C | `run_finish.py` passes the design's AC ids to the final reviewer, which lists exactly those | `test_run_finish` |
| D | `BASELINE_OK=yes` after a passing baseline; resume skips item 5 | coordinator scenario 4 |

## Second live run (run `run_cb947add77b4`, topic `tempconv-cli`, 2026-11-15 21:05–21:48)

Project `/home/marwan/Projects/omw-temp-test` (a new temperature-converter CLI, two tasks). Picks: coordinator `claude:sonnet@low`, architect `claude:sonnet@medium`, EGM and base `antigravity:gemini-4-hydrogen@low`. The user was played by the session that made the fixes, through `orca terminal send`; it chose "Sign off and run EGM", "Start the build" and "keep the branch".

**Fixes confirmed live:**
- Setup asked every question as a multiple-choice dialog, paged the 7-row Antigravity model menu as 3 + `More`, and handed off as a terminal.
- Requirements were saved to `.run/requirements.md` before the test launches; nothing was discarded, and the architect committed its own copy (issue 1).
- The design relay ended in a choice block, in plain words, with a recommendation (issues 5, 6, 12); choosing EGM set `EGM_REQUESTED=yes`. No second sign-off was asked.
- `run-values.txt` used `BASELINE_SKIP=VERIFY_1` from the start; the baseline logged it as skipped and set `BASELINE_OK=yes`, with no architect round (issue 9).
- `CLERICAL_PAIR=claude:haiku@default` passed its test launch (launch style `flag`, no `--effort`), and the `pr` and `report` jobs ran on it (issue 7).
- The `.git/info/exclude` cache patterns kept `__pycache__` out of every check: no commit worker was needed (issue 10).
- Every architect `RESULT:` line was bare; results were saved as `architect-1.md` to `-4.md` without overwrites (B, A).
- The final reviewer got `AC-1 AC-2 AC-3 AC-4 AC-5` and wrote one line for each (C).
- Task 01 went through two stage-2 fix rounds on the same implementer and merged; task 02 passed first time; final review PASS; verification exit 0. The delivered tool was checked by hand against all four requirements and passes, with 21 tests.

**New defects found and fixed:**
- **N1. Setup paged menus of exactly 4 rows** (3 + `More`, with the 4th row hidden in `More`'s description). Fixed in `0e8ad16`: 2–4 rows are always one question; only 5+ rows page. Checked with a fresh agent on 4-, 5- and 9-row menus.
- **N2. The coordinator showed a choice block's dialog without the relay text** (one `AskUserQuestion` call, so the user saw only the question, not the design). A first rewrite still failed in a live rehearsal: the reader stopped at step 4 and never reached the choice paragraph below. Fixed in `0b69ae1`: Rule 5 step 2 itself holds both forms, and the dialog's question carries `From the architect:` plus the whole relay text. A live rehearsal on Sonnet at low effort then showed the full relay inside the dialog. The run's coordinator had loaded the old text, so its go-ahead dialog had the same gap.

**Follow-up fixes (branch `minor-fixes`):**
- **N3. The coordinator narrated its turns** ("Your answer ... is back with the architect", a description of a review's findings), outside its fixed lines. Answering in description mode didn't reproduce it (3 of 3 said they'd print nothing), so it was tested in live Sonnet-low terminal rehearsals against a stubbed `run_task.py`: on the old text, 6 of 6 turns ended in narration paragraphs. Fix: "What you print" in `coordinator.md`, an exact end-of-turn block (`Merged task <id> at <head>.` lines, then `Waiting: <what>`). After two tightening rounds: the block was right in every turn, turn 2 was clean 3 of 3, and turn 1 still had a one-sentence preamble 2 of 3, never about a worker's output. Known residue.
- **N4. EGM's results reached the user only as a count.** The separate `NOTIFY` after `egm-done` was skipped live. Before the fix, 0 of 3 go-ahead relays said what changed. Fix: no `NOTIFY` after `egm-done` unless a signed-off point changed; the go-ahead relay has a required **Reviews** list (one line per review since the user last saw the design: findings by severity and what changed). After: 3 of 3 sent no separate notice and listed every review with its changes.
