# Configurable roles and review fixes: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every issue from the 2026-11-15 review, and replace the skill's hard-coded harnesses and models with four user picks. Detection uses harness adapters, and every pick is checked by a test launch.

**Architecture:**
- Shared plumbing moves from `run_task.py` into `scripts/common.py`.
- New modules:
  - `scripts/pairs.py`: the `harness:model@effort` pair format and the escalation ladder.
  - `scripts/harnesses.py`: harness adapters, detection, and Orca launch arguments.
  - `scripts/roles.py`: the setup picks, their requirement checks, and remembering them.
  - `scripts/probe.py`: test launches.
- Two new state machines in the style of `run_task.py`:
  - `scripts/run_design.py` for coordinator Step 2's read-only workers;
  - `scripts/run_finish.py` for Step 8 items 1–3.
- The prose (`SKILL.md`, `coordinator.md`, `architect.md`) is rewritten last, to call these scripts.

**Tech Stack:** Python 3 standard library only, bash, and git. Tests use `unittest`, plus the existing `tests/fake_orca.py`.

**Spec:** `docs/2026-11-15-configurable-roles-design.md`. Read it before any task.

## Global Constraints

- Run every script as `python3 -I <script>`, using the standard library only.
- No model names (`opus`, `sonnet`, `haiku`, `fable`, `hydrogen`, `argon`, `gemini`) and no Antigravity names (`antigravity`, `agy`) in `SKILL.md`, `references/coordinator.md`, `references/architect.md`, `references/templates/`, or `scripts/`. The exceptions are `scripts/harnesses.py` and the test fixtures. Task 10 adds a test that enforces this.
- Pair format: `<harness>:<model>@<effort>`, split at the first `:` and the last `@`. A model with no effort choice uses `@default`.
- Effort order, lowest to highest: `low`, `medium`, `high`, `xhigh`, `max`. "Highest" is the highest effort detected for that model.
- State keys match `[A-Za-z0-9_]+`. A key that holds a pair uses `pair_key(pair)`, defined in Task 4.
- Every advance-style script prints its status as the last line of stdout, and lists its statuses and exit codes in its docstring, as `run_task.py` does.
- The picks file is `~/.config/orca-multiagent-workflow/roles.json`.
- A state file without `ROLE_ARCHITECT` makes every advance script exit 2 with `ERROR OLD_FORMAT_RUN remove <RUN_DIR> and start a new run`.
- Commit after every task in the skill repo (`/home/marwan/.claude/skills/orca-multiagent-workflow`, git initialized at `75fc214`).

## Review Focus

1. **A harness CLI that errors or prints nothing,** for example `agy models` when signed out. The harness is listed as broken with the raw output, never shown as an empty menu. Task 4.
2. **A remembered pick that no longer exists** after a harness update. "Same as last time" isn't offered, and setup says which pick vanished. Task 5.
3. **Model ids that contain `-`, `.` or `:`,** such as `claude:claude-opus-5-5@high` or `antigravity:gemini-3.8-flash@low`. They parse and round-trip exactly. Task 4.
4. **The coordinator is compacted or restarted mid-probe.** `probe.py advance` is idempotent: it neither starts a second test worker nor loses a passed result. Task 6.
5. **The architect names a reviewer at an effort the model doesn't have,** or from another harness. `validate_meta.py` rejects it with the allowed efforts listed. Task 5.

---

### Task 1: Extract `common.py`; one test entry point

**Files:**
- Create: `scripts/common.py`, `tests/helpers.py`, `tests/run_all.sh`
- Modify: `scripts/run_task.py` (import from `common`; delete the moved code), `tests/test_run_task.py` (use `helpers.Base`; fix the docstring's run command)

**Interfaces:**
- Produces, in `common.py`:
  - status codes `QUEUE_EMPTY=0`, `WAIT=10`, `ESCALATE=20`, `FIX_META=21`, `PROMPT=30`, `STOP=40`, `ERROR=2`;
  - `class Done(Exception)` with `.code` and `.line`;
  - `class Run(path)`: `get`, `need`, `set(**kv)`, `incr(key) -> int`, `run_dir`, `docs`, `impl`, `task_file(t, suffix)`, `log(event)`, and a new `require_new_format()`;
  - `class Orca(run)`: `call`, `start(spec, title, pair, worktree, keep_open=False, terminal=None) -> (dispatch, terminal)`, `try_reuse(spec, title, terminal, worktree)`, `send`, `show`, `release`, `inbox`;
  - `payload(msg)`, `save_body(msg, out, header=None, append=False)`, `next_free(folder, pattern)`, `last_line(path)`, `verdict_line(path)`, `VERDICTS`, `tool(name, *args) -> (rc, out)`, `git(wt, *args)`, `build(template, out, sets, files) -> Path`, `poll(orca, run, dispatch)` (moved from `Loop.poll`, same return values).
- In this task, `Orca.start` still builds `--agent/--model` with today's `split_pair`. Task 7 switches it to `harnesses.launch_args`.
- `tests/helpers.py` produces `class Base(unittest.TestCase)`, today's `Base` from `test_run_task.py` moved unchanged, with `adv(script, *args)` taking the script path as its first argument.
- It also adds `until(code, prefix, *args, limit=20)`, which calls `adv` while the status is `WAIT` and asserts that the first other status is `(code, prefix)`. Fix-round tests use it, so they don't depend on hand-counted `WAIT` steps.

- [ ] **Step 1:** Move the code listed above into `common.py` without changing its behaviour. `Orca.start` and `try_reuse` take the worktree as a parameter instead of reading `self.r.impl`, and `run_task.py` passes `r.impl`.
- [ ] **Step 2:** Move `Base` into `tests/helpers.py`, and make `test_run_task.py` import it.
- [ ] **Step 3:** Write `tests/run_all.sh`. It runs `bash tests/test_scripts.sh`, then `python3 -I -m unittest discover -s tests -p 'test_*.py'`, and exits non-zero if either fails. Change the docstring of `test_run_task.py` to say `bash tests/run_all.sh`.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: `passed=33 failed=0`, then `Ran 23 tests ... OK`.
- [ ] **Step 5:** Commit: `refactor: extract common.py and a single test entry point`.

### Task 2: Fix-round counters (design 1.1)

**Files:**
- Modify: `scripts/run_task.py` (`do_check`, `fix`, `after_review`, `do_start`, the ESCALATE branch of `architect_result`)
- Test: `tests/test_run_task.py`

**Interfaces:**
- State keys per task: `REVIEW_STAGE_<id>` (`1|2`), `GATE_FAILED_<id>` (`yes|no`), `STAGE_FIX_ROUNDS_<id>_S1`, `STAGE_FIX_ROUNDS_<id>_S2`, `GATE_FIX_ROUNDS_<id>`, `TOTAL_FIX_ROUNDS_<id>`.
- These replace `FIX_ROUNDS_<id>` and `FAILED_STAGE_<id>`. All of them are reset in `do_start` and on an escalation attempt.

- [ ] **Step 1: Write the failing tests** in class `GateAndFixes`:

```python
def test_stage2_counter_survives_gate_pass(self):
    # impl, s1 PASS, s2 FIX, fix fails gate, fix passes gate, s2 FIX, fix, s2 FIX -> escalate
    self.behave("commit", "pass", "fix", "commit-broken", "unbreak", "fix", "commit", "fix")
    self.until(20, "ESCALATE")
    self.assertEqual(self.st("STAGE_FIX_ROUNDS_01_S2"), "2")
    self.assertEqual(len(self.calls("worker-start")), 8)

def test_gate_pass_resumes_the_failed_review_stage(self):
    # impl, s1 PASS, s2 FIX, fix fails gate, fix passes gate -> the next reviewer is stage 2
    self.behave("commit", "pass", "fix", "commit-broken", "unbreak", "pending")
    for _ in range(5):                           # impl, s1, s2, fix 1, fix 2
        self.expect(10, "WAIT")
    self.assertIn(" review2", self.expect(10, "WAIT")[0])

def test_total_fix_cap_escalates(self):
    # s1 FIX(t1), gate fail(t2), gate pass, s1 PASS, s2 FIX(t3), gate fail(t4), gate pass, s2 FIX -> cap
    self.behave("commit", "fix", "commit-broken", "unbreak", "pass", "fix", "commit-broken", "unbreak", "fix")
    self.until(20, "ESCALATE")
    self.assertEqual(self.st("TOTAL_FIX_ROUNDS_01"), "4")
    self.assertEqual(self.st("STAGE_FIX_ROUNDS_01_S2"), "1")
```

Then update `test_gate_failure_goes_to_fix_on_same_terminal`, which asserts `FIX_ROUNDS_01`, to assert `GATE_FIX_ROUNDS_01 == "0"` after the gate passes.

- [ ] **Step 2:** Run `python3 -I -m unittest tests.test_run_task -k fix -k gate`. Expected: the three new tests FAIL.
- [ ] **Step 3: Implement.**
  - Before a review fix round, `fix(t, findings, kind)` escalates when `STAGE_FIX_ROUNDS_<id>_S<n>` is already 2. Before a gate fix round, it escalates when `GATE_FIX_ROUNDS_<id>` is already 2. Either way, it escalates when `TOTAL_FIX_ROUNDS_<id>` is already 4. Otherwise it increments the matching counter and the total.
  - A gate pass resets `GATE_FIX_ROUNDS_<id>` only, then starts `REVIEW_STAGE_<id>`.
  - A stage pass resets that stage's counter, and stage 1 passing sets `REVIEW_STAGE_<id>=2`.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 5:** Commit: `fix: fix-round caps survive gate passes; resume the failed review stage`.

### Task 3: Check the escalation reset SHA; prompt after three void reviewers (design 1.2, 1.5)

**Files:**
- Modify: `scripts/run_task.py` (`architect_result`, `void_review`, `reply`)
- Test: `tests/test_run_task.py`

- [ ] **Step 1: Write the failing tests:**

```python
def test_reset_sha_before_base_is_rejected(self):
    self.behave("commit", "fix", "commit", "fix", "commit", "fix")
    self.until(20, "ESCALATE")
    note = self.tmp / "note.md"; note.write_text("n\n")
    self.expect(20, "ESCALATE", "--architect-result",
                self.result_file(f"RESULT: ESCALATE {self.older} {note} <rung 1 pair of the META fixture>"))
    hist = (self.run_dir / "verdicts" / "01-history.md").read_text()
    self.assertIn("not between the task's base", hist)
    self.assertEqual(self.impl_head(), self.st("WORKER_HEAD_01"))  # nothing was reset

def test_three_void_reviewers_prompt_retry_or_stop(self):
    self.behave("commit", "stopped", "stopped", "stopped")
    self.expect(10, "WAIT"); self.expect(10, "WAIT"); self.expect(10, "WAIT")
    last, _ = self.expect(30, "PROMPT")
    self.assertTrue(last.endswith("retry stop"))
    self.behave("pass", "pass")
    self.expect(10, "WAIT", "--reply", "retry")
```

`self.older` is a commit made in `setUp` before `self.base`. Give `Base` a parent commit for this.

- [ ] **Step 2:** Run the two tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - In `architect_result`, before `discard-changes.sh`, require both `git merge-base --is-ancestor BASE_SHA_<id> <sha>` and `git merge-base --is-ancestor <sha> HEAD` in the implementation worktree. Otherwise call `self.reject(t, f"reset sha {sha} is not between the task's base {base} and head {head}")`.
  - In `void_review`, at 3 voids, call `self.prompt(t, "void", "<text naming task, stage and the reason>\nReply \"retry\" to start a new reviewer, or \"stop\" to stop the run.", ["retry", "stop"], back_to=f"review{stage}")`.
  - In `reply`, `void` + `retry` resets `VOIDS_<id>_S<n>` and calls `start_review`.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 5:** Commit: `fix: validate escalation reset sha; prompt after three void reviewers`.

### Task 4: Pairs and harness adapters (design 2.3, 2.4)

**Files:**
- Create: `scripts/pairs.py`, `scripts/harnesses.py`, `tests/test_harnesses.py`, and `tests/fixtures/harness/`, holding captured output named after its command (`agy_models.txt`, `agy_plugin_list.txt`, `agy_mcp_list.txt`, `claude_help.txt`, `claude_plugin_list.txt`, `claude_mcp_list.txt`) and a fake home with `SKILL.md` files.

**Interfaces:**
- Produces, in `pairs.py`:
  - `EFFORTS = ("low", "medium", "high", "xhigh", "max")`;
  - `class Pair(NamedTuple)` with fields `harness`, `model`, `effort`, where `str(p)` gives the pair text;
  - `parse_pair(text) -> Pair`, which raises `ValueError` on a bad shape;
  - `pair_key(p) -> str`, which replaces every non-alphanumeric character with `_`;
  - `ladder(base: Pair, top_effort: str, architect: Pair, first: Pair) -> list[Pair]`. The candidates are `[Pair(base.harness, base.model, top_effort), architect]`, minus any rung equal to `first`, to an earlier rung, or (for the non-last rung) to `architect`.
- Produces, in `harnesses.py`:
  - `ADAPTERS = {"claude": ClaudeAdapter, "antigravity": AntigravityAdapter}`;
  - `class Adapter(runner, home)`, where `runner(cmd: list[str]) -> (rc, stdout)`, with `name`, `orca_agent`, `styles: list[str]`, `find() -> dict|None`, `models() -> list[dict(model, label, efforts)]`, `skills() -> dict[name, folder]`, `plugins() -> list[str]` (enabled only), and `mcp() -> dict[name, ok: bool]`;
  - `detect(runner, home) -> dict`, the catalog: `{"detected_at": iso, "harnesses": {name: {"orca_agent", "binary", "version", "styles", "models", "skills", "plugins", "mcp", "broken": None|str}}}`;
  - `launch_args(catalog, pair, style) -> list[str]`: `slug` gives `["--agent", A, "--model", f"{model}-{effort}"]`, or `["--agent", A, "--model", model]` for `@default`; `flag` gives `["--agent", A, "--model", model] + (["--effort", effort] unless default)`;
  - `top_effort(catalog, harness, model) -> str`;
  - CLI: `detect --out F [--fixtures DIR --home DIR]`, `args --state S --pair P`, `skill-dir --catalog C --harness H --skill NAME`, `top-effort --catalog C --harness H --model M`.
  - `args` picks the style from `LAUNCH_STYLE_<pair_key>` in the state file, else any recorded `LAUNCH_STYLE_*` of the same harness, else `styles[0]`.

- [ ] **Step 1: Capture the fixtures.** Use real output from this machine for `agy models`, `agy plugin list`, `agy mcp list`, `claude --help`, `claude plugin list` and `claude mcp list`. Cut `claude mcp list` to three lines, one of them Atlassian `✔ Connected`. Add `agy_models_signed_out.txt`, containing `Fetching available models...` followed by an error line.
- [ ] **Step 2: Write the failing tests:**

```python
def test_parse_pair_roundtrip(self):
    for s in ("claude:claude-opus-5-5@high", "antigravity:gemini-3.8-flash@low", "x:m:odd@default"):
        self.assertEqual(str(parse_pair(s)), s)
    self.assertEqual(parse_pair("x:m:odd@default").model, "m:odd")
    with self.assertRaises(ValueError): parse_pair("nocolon@high")

def test_ladder_variants(self):
    b, a = parse_pair("ag:g@high"), parse_pair("cl:big@high")
    self.assertEqual([str(p) for p in ladder(b, "xhigh", a, b)], ["ag:g@xhigh", "cl:big@high"])
    self.assertEqual([str(p) for p in ladder(parse_pair("ag:g@xhigh"), "xhigh", a, parse_pair("ag:g@xhigh"))], ["cl:big@high"])
    self.assertEqual([str(p) for p in ladder(b, "xhigh", parse_pair("ag:g@xhigh"), b)], ["ag:g@xhigh"])

def test_agy_models_split_effort_suffix(self):
    cat = detect(fixture_runner(), FAKE_HOME)
    m = {x["model"]: x["efforts"] for x in cat["harnesses"]["antigravity"]["models"]}
    self.assertEqual(m["gemini-4-hydrogen"], ["low", "medium", "high", "xhigh"])
    self.assertEqual(m["gemini-pro-orchestrator-g1-pro"], ["default"])

def test_claude_models_and_efforts(self):
    h = detect(fixture_runner(), FAKE_HOME)["harnesses"]["claude"]
    self.assertIn("opus", [x["model"] for x in h["models"]])
    self.assertEqual(h["models"][0]["efforts"], ["low", "medium", "high", "xhigh", "max"])

def test_signed_out_harness_is_broken_not_empty(self):
    h = detect(fixture_runner(agy_models="agy_models_signed_out.txt"), FAKE_HOME)["harnesses"]["antigravity"]
    self.assertIn("Fetching available models", h["broken"])

def test_requirements_inputs(self):
    h = detect(fixture_runner(), FAKE_HOME)["harnesses"]
    self.assertIn("egm-review", h["antigravity"]["skills"])
    self.assertTrue(any(ok for n, ok in h["claude"]["mcp"].items() if "atlassian" in n.lower()))

def test_launch_args_styles(self):
    cat = detect(fixture_runner(), FAKE_HOME)
    p = parse_pair("antigravity:gemini-4-hydrogen@xhigh")
    self.assertEqual(launch_args(cat, p, "slug"), ["--agent", "antigravity", "--model", "gemini-4-hydrogen-xhigh"])
    self.assertEqual(launch_args(cat, p, "flag")[-2:], ["--effort", "xhigh"])
```

- [ ] **Step 3:** Run `python3 -I -m unittest tests.test_harnesses`. Expected: FAIL (import error).
- [ ] **Step 4: Implement both modules.**
  - **Antigravity:** split each `agy models` slug at its last `-` when the suffix is in `EFFORTS`. Skill roots: `~/.gemini/config/skills/*/SKILL.md` and `~/.gemini/config/plugins/<enabled plugin>/skills/*/SKILL.md`. Styles: `["slug", "flag"]`.
  - **Claude:** models are the quoted aliases on the `--model` lines of `claude --help`, plus a constant `EXTRA_MODELS = ["haiku"]`. Efforts are the parenthesized list on the `--effort` lines. Skill roots: `<home>/skills/*/SKILL.md` for each home in `[$CLAUDE_CONFIG_DIR, ~/.claude]` that exists, plus the skills of enabled plugins, found under `<home>/plugins/cache/*/<plugin>/*/skills/*/SKILL.md`. Styles: `["flag"]`.
  - A harness whose `find()` fails is left out. A harness whose `models()` parse finds nothing gets `broken` set to the first 400 characters of the raw output.
  - During the Task 12 live check, cross-check the skill roots against `orca skills installed`.
- [ ] **Step 5:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 6:** Commit: `feat: pair format, ladder, and harness adapters with fixture tests`.

### Task 5: Setup picks and the new validator (design 2.1, 2.2, 2.6)

**Files:**
- Create: `scripts/roles.py`, `tests/test_roles.py`, `tests/test_validate_meta.py`, `tests/fixtures/catalog.json`. The catalog fixture has two harnesses, both with every skill and an Atlassian MCP server:
  - `ag`: `orca_agent` `ag-agent`, styles `["slug", "flag"]`, model `g` at efforts `low..xhigh`;
  - `cl`: `orca_agent` `cl-agent`, styles `["flag"]`, models `big` and `small` at `low..max`.
- Modify: `scripts/validate_meta.py` (rewrite its checks), `tests/test_scripts.sh` (delete its `validate_meta ACS` section; the ACS cases move to `test_validate_meta.py` unchanged)

**Interfaces:**
- `roles.py` constants:
  - `ROLES = ("coordinator", "architect", "egm", "base")`;
  - `REQUIRES = {"coordinator": ["orca-multiagent-workflow", "orchestration", "orca-cli", "verification-before-completion", "finishing-a-development-branch"], "architect": ["brainstorming", "writing-plans"], "egm": ["egm-brainstorm", "egm-review", "egm-review-loop"], "base": ["test-driven-development", "verification-before-completion"]}`;
  - the architect also needs a connected MCP server whose name contains `atlassian` when `--jira yes`.
- `roles.py` CLI:
  - `menu --catalog C [--harness H [--model M]]`: prints numbered lines `N<TAB>value<TAB>label`. Broken harnesses are listed after the numbered ones as `BROKEN <name>: <reason>`.
  - `check --catalog C --jira yes|no --coordinator P --architect P --egm P --base P`: prints `OK <role> <pair>`, `UNKNOWN <role> <pair> <why>`, `MISSING <role> <pair> <requirement> in <harness>`, or `NOTE architect and base share <harness>:<model>; the ladder skips repeated rungs`. Exit 1 on any UNKNOWN or MISSING.
  - `remembered --roles F --catalog C`: prints `<role>=<pair>` lines, then a last line `AVAILABLE yes`, or `AVAILABLE no <role> <pair>` naming the first missing pair. Exit 1 with `NONE` when the file is missing.
  - `save --roles F --coordinator P --architect P --egm P --base P`: writes JSON `{role: pair, "saved_at": iso}` and creates the folder.
  - `to-state --roles F --state S --catalog C`: sets `ROLE_COORDINATOR`, `ROLE_ARCHITECT`, `ROLE_EGM`, `ROLE_BASE`, `BASE_TOP=<top effort>` and `CATALOG=<C>`.
- `validate_meta.py` CLI. All modes read `CATALOG`, `ROLE_*` and `BASE_TOP` from `--state`; `--gemini-model` is removed.
  - `tasks --state S --tasks-dir D --ids "..." [--design F] [--replanned-from ID]`
  - `pair --state S --task-meta F --pair P --attempt N --past-ladder yes|no`: with `no`, the pair must equal rung N of `ladder(ROLE_BASE, BASE_TOP, ROLE_ARCHITECT, <IMPLEMENTER from F>)`. With `yes`, the pair must be in the catalog.
  - `harness-pair --state S --pair P`: the pair must be in the architect's harness, with a detected model and effort. Used for the builder check, `CLERICAL_PAIR`, and reviewers.
- `.meta` keys: `TITLE TIER SPEC_TYPE ACS IMPLEMENTER REVIEW_1 REVIEW_2`, plus the optional `REPLANNED_FROM`. A `LADDER` key gives the problem `LADDER is no longer used; delete it`.

- [ ] **Step 1: Write the failing tests:**

```python
# test_validate_meta.py (state fixture: ROLE_ARCHITECT=cl:big@high, ROLE_BASE=ag:g@high, BASE_TOP=xhigh)
def test_reviewer_effort_not_detected_is_rejected(self):
    self.meta("01", REVIEW_1="cl:small@ultra")
    self.assertProblem("REVIEW_1 'cl:small@ultra'", "low medium high xhigh max")
def test_reviewer_from_other_harness_rejected(self):
    self.meta("01", REVIEW_1="ag:g@high"); self.assertProblem("architect's harness")
def test_complex_review2_matches_architect_model_any_effort(self):
    self.meta("01", TIER="complex", REVIEW_2="cl:big@low"); self.assertOK()
    self.meta("01", TIER="complex", REVIEW_2="cl:small@max"); self.assertProblem("REVIEW_2")
def test_first_implementer_options(self):
    self.meta("01", IMPLEMENTER="ag:g@xhigh"); self.assertOK()
    self.meta("01", IMPLEMENTER="ag:g@low"); self.assertProblem("IMPLEMENTER")
    self.meta("01", IMPLEMENTER="cl:big@high"); self.assertProblem("never implements first")
def test_shared_model_blocks_top_start(self):  # ROLE_ARCHITECT=ag:g@xhigh
    self.state(ROLE_ARCHITECT="ag:g@xhigh"); self.meta("01", IMPLEMENTER="ag:g@xhigh")
    self.assertProblem("never implements first")
def test_escalation_pair_must_be_rung(self):
    self.assertPair("ag:g@xhigh", attempt=1, ok=True); self.assertPair("cl:big@high", attempt=1, ok=False)
    self.assertPair("cl:big@high", attempt=2, ok=True); self.assertPair("cl:small@low", attempt=3, past="yes", ok=True)
def test_ladder_key_rejected(self):
    self.meta("01", LADDER="x"); self.assertProblem("LADDER is no longer used")

# test_roles.py
def test_check_reports_missing_skill_with_harness(self):  # catalog copy without egm-review in ag
    rc, out = self.roles("check", ..., egm="ag:g@high")
    self.assertEqual(rc, 1); self.assertIn("MISSING egm ag:g@high egm-review in ag", out)
def test_jira_needs_connected_atlassian(self): ...       # mcp ok=False -> MISSING architect ... atlassian
def test_remembered_pick_vanished(self):
    self.save(base="ag:gone@high")
    rc, out = self.roles("remembered", ...); self.assertEqual(out.splitlines()[-1], "AVAILABLE no base ag:gone@high")
def test_menu_lists_broken_after_numbered(self): ...
def test_to_state_sets_roles_and_base_top(self): ...     # BASE_TOP == "xhigh" for ag:g
```

- [ ] **Step 2:** Run `python3 -I -m unittest tests.test_roles tests.test_validate_meta`. Expected: FAIL.
- [ ] **Step 3:** Implement `roles.py` and rewrite `validate_meta.py` against `pairs.py` and the catalog. Keep the ACS and replan coverage logic as it is.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 5:** Commit: `feat: setup picks with requirement checks; validator driven by picks and catalog`.

### Task 6: Test launches (design 2.5)

**Files:**
- Create: `scripts/probe.py`, `references/templates/probe.txt`, `tests/test_probe.py`
- Modify: `tests/fake_orca.py`, adding launch rules, an `ok` behavior and `worker-start` model checking

**Interfaces:**
- `probe.txt` contains exactly: `This is a launch test. Do not read, create or change any file, and run no commands. Settle worker_done with outcome succeeded and the message OK.`
- `probe.py --state S advance --set step1|step3|pair [--pair P]`. Probe workers run in `DOCS_WORKTREE`, and the tree is checked against `PROBE_HEAD` after each one, with any change discarded.
  - **Sets:**
    - `step1`: `ROLE_ARCHITECT`, `ROLE_EGM`, `ROLE_BASE`, and the base model at `BASE_TOP`, deduplicated.
    - `step3`: every distinct `REVIEW_1` and `REVIEW_2` in the queue's `.meta`, plus `CLERICAL_PAIR`.
    - `pair`: one pair.
  - Pairs that already have `LAUNCH_STYLE_<key>` are skipped.
  - **Statuses:**
    - `0 PROBED`;
    - `10 WAIT <dispatch> probe`;
    - `40 PROBE_FAILED <pair> <file with every style's failure text>`;
    - `2 ERROR`.
  - **State keys:** `PROBE_SET`, `PROBE_HEAD`, `PROBE_STYLE_<key>` (the index of the style being tried), `PROBE_DISPATCH_<key>`, and `LAUNCH_STYLE_<key>`, written on success.
- Also exposes `class Prober(run, orca)` with `advance(pairs) -> None`. It raises `Done` for every status except PROBED, which returns, so `run_design.py` and `run_task.py` can embed it.
- `fake_orca.py`:
  - `world["launch_rules"]` is an optional list of `{"agent", "model", "effort"}`, where `effort` may be `null`. When the list is present, a `worker-start` that matches no rule doesn't consume a behavior. It sends an escalation with `stopReason: "lost"` and body `Model '<model>' is not available for the current authentication method`, and sets liveness to `exited`.
  - New behavior `ok`: settle succeeded with `OK`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_all_pass_records_styles(self):
    self.behave("ok", "ok", "ok"); self.expect_probe(10, "WAIT", "--set", "step1")
    self.expect_probe(0, "PROBED")
    self.assertEqual(self.st("LAUNCH_STYLE_ag_g_high"), "slug")
def test_slug_rejected_falls_back_to_flag(self):
    self.rules([{"agent": "ag-agent", "model": "g", "effort": "high"}]); ...
    self.assertEqual(self.st("LAUNCH_STYLE_ag_g_high"), "flag")
def test_every_style_rejected_fails_with_reasons(self):
    last, _ = ...; self.assertTrue(last.startswith("PROBE_FAILED ag:g@high "))
    self.assertIn("is not available", Path(last.split()[-1]).read_text())
def test_advance_is_idempotent_while_waiting(self):   # Review Focus 4
    self.behave("pending", "ok", "ok"); first = self.expect_probe(10, "WAIT", "--set", "step1")
    again = self.expect_probe(10, "WAIT"); self.assertEqual(first, again)
    self.assertEqual(len(self.calls("worker-start")), 3)
def test_probe_that_edits_docs_is_discarded(self): ...
```

- [ ] **Step 2:** Run `python3 -I -m unittest tests.test_probe`. Expected: FAIL.
- [ ] **Step 3:** Implement `probe.py`, the template, and the fake's changes.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 5:** Commit: `feat: probe.py test launches with launch-style fallback`.

### Task 7: Move `run_task.py` to picks, the computed ladder, and adapter launches

**Files:**
- Modify: `scripts/common.py` (`Orca.start` and `try_reuse` build arguments with `harnesses.launch_args` and the recorded style), `scripts/run_task.py`, `tests/helpers.py` (the state fixture gets `ROLE_*`, `BASE_TOP`, `CATALOG=tests/fixtures/catalog.json` and `LAUNCH_STYLE_*`, and drops `GEMINI_MODEL`), `tests/test_run_task.py` (pairs become `ag:g@high` and similar; `LADDER` is removed from `META`)

**Interfaces:**
- Consumes `pairs.ladder`, `harnesses.launch_args`, `validate_meta.py pair`, and `probe.Prober`.
- `ESCALATE` input becomes `task <id>, tier <tier>, attempt <a>, next rung <pair>|ladder used up, replan allowed|not allowed, history <file>`.
- New phase `probe:<attempt>`: before an escalation attempt past the ladder, run `Prober.advance([pair])`. A PROBE_FAILED counts as a rejected answer (`reject`).
- `Loop.__init__` calls `run.require_new_format()`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_old_format_state_refused(self):
    s = self.st(); del s["ROLE_ARCHITECT"]; self.state.write_text(json.dumps(s))
    rc, last, _ = self.adv(); self.assertEqual((rc, last.split()[:2]), (2, ["ERROR", "OLD_FORMAT_RUN"]))
def test_ladder_from_picks(self):            # replaces the rung assertions in test_two_fix_rounds_then_escalate_then_next_rung
    ...; self.assertIn("next rung ag:g@xhigh", last)
    # second escalation:
    ...; self.assertIn("next rung cl:big@high", last)
def test_past_ladder_pair_is_probed_first(self):
    # use up the ladder, answer ESCALATE with cl:small@low: expect WAIT ... probe, then WAIT ... impl
def test_launch_uses_recorded_style(self):
    # LAUNCH_STYLE_ag_g_high=flag in state -> worker-start call has "--effort", "high"
```

- [ ] **Step 2:** Run `python3 -I -m unittest tests.test_run_task`. Expected: the new tests FAIL, and the old ones fail on the pair format until they're updated in Step 3.
- [ ] **Step 3: Implement,** and update the existing tests' pairs and assertions.
  - `escalate()` computes the ladder with `ladder(ROLE_BASE, BASE_TOP, ROLE_ARCHITECT, meta IMPLEMENTER)`.
  - `architect_result` validates with `validate_meta.py pair ... --past-ladder yes|no`, and drops the code that rewrote `LADDER` in `.meta`.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 5:** Commit: `feat: run_task uses role picks, computed ladder, and adapter launches`.

### Task 8: `run_design.py` (design 1.3, Step 2)

**Files:**
- Create: `scripts/run_design.py`, `tests/test_run_design.py`
- Modify: `tests/fake_orca.py`, adding behaviors `write-output` (writes the spec's output file and settles succeeded) and `write-empty`

**Interfaces:**
- Usage: `run_design.py --state S advance [--result <file holding the architect's settle>]`.
- **Statuses:**
  - `10 WAIT <dispatch> <kind>`;
  - `20 ARCHITECT_JOB <job> <input>`, where `<job>` is `commit-docs`, `builder-check-done`, `lens-done`, `egm-done`, `egm-limit` or `plan-review-done`, and `<input>` may contain spaces (`failed: <file>`, `Plan review limit reached; finish triage with <file>`);
  - `2 ERROR`.
- Results it accepts:
  - `RESULT: NEED builder-check <design rel> <pair>`;
  - `RESULT: NEED egm <design rel> <findings rel>`;
  - `RESULT: NEED lens <concepts abs>`;
  - `RESULT: NEED plan-review <plan abs> <context abs>`;
  - `RESULT: COMMITTED`, which resumes the pending NEED.
- **Rules carried over unchanged from coordinator Step 2:** the docs-tree check before each dispatch, which goes to `commit-docs` and is never discarded; the settle saved to `results/<kind>-<n>.md`; release; `check-tree.sh`/`discard-changes.sh` against `DOCS_HEAD` (with `--allow <findings>` for EGM, copying the findings to `RUN_DIR/egm-<n>.md` first); the `RETRY_<kind>` counters (retry once, then `failed: <file>`); and the run limits `EGM_RUNS` (3 means `egm-limit`), `LENS_RUNS` (2 means `lens-done` with the first findings) and `PLAN_REVIEW_RUNS` (3 means the limit input).
- **Models:**
  - builder check: its pair from the NEED line, checked with `validate_meta.py harness-pair` (a failure gives `builder-check-done` the input `failed: <file with the problem>`) and probed with `Prober` first;
  - lens, EGM and plan review: `ROLE_EGM`.
- `EGM_SKILLS` comes from `harnesses.py skill-dir --harness <ROLE_EGM harness> --skill egm-review`, then its parent folder. It's computed once and stored.

- [ ] **Step 1: Write the failing tests,** one per rule:
  - `test_builder_check_happy_path`: NEED, then WAIT, then `ARCHITECT_JOB builder-check-done <RUN_DIR>/builder-check-1.md`.
  - `test_dirty_docs_tree_sends_commit_docs_then_resumes`.
  - `test_failed_twice_sends_failed_input`.
  - `test_egm_third_run_sends_egm_limit`.
  - `test_lens_second_run_reuses_first_findings`.
  - `test_plan_review_limit_input`.
  - `test_worker_edit_is_discarded_output_kept`.
  - `test_builder_pair_outside_architect_harness_fails_to_architect`.
  - `test_egm_launch_uses_role_egm`.
- [ ] **Step 2:** Run `python3 -I -m unittest tests.test_run_design`. Expected: FAIL.
- [ ] **Step 3:** Implement on `common.py`.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 5:** Commit: `feat: run_design.py runs Step 2's read-only workers`.

### Task 9: `run_finish.py` (design 1.3, Step 8 items 1–3)

**Files:**
- Create: `scripts/run_finish.py`, `tests/test_run_finish.py`

**Interfaces:**
- Usage: `run_finish.py --state S advance [--architect-result F] [--reply W]`.
- **Statuses:**
  - `0 VERIFIED <log>`;
  - `10 WAIT <dispatch> final-review`;
  - `20 ARCHITECT_JOB final-review <verdict file>`, `ARCHITECT_JOB verification-failed <log>`, or `ARCHITECT_JOB fix-meta <validate output>`;
  - `21 RUN_QUEUE <F id>`: the queue is written; the coordinator runs Step 4 and returns here on `QUEUE_EMPTY`;
  - `30 PROMPT <file> <replies>`;
  - `40 STOP <reason>`;
  - `2 ERROR`.
- **Behaviour carried over from coordinator Step 8 items 1–3:**
  - Release every `IMPL_DISPATCH_*` in the state, and check that `check-tree.sh <IMPL_WORKTREE> <LAST_MERGED>` exits 0.
  - The final reviewer is `ROLE_ARCHITECT` on the task's `final-review.txt`, with its output in `verdicts/final-<n>.md`.
  - A reviewer that edits files is void, discarded and replaced; after 3 voids, prompt `retry stop`.
  - The verdict uses `common.verdict_line` with the fallback to the settle message. No verdict means one new reviewer, then `VERDICT: FIX`.
  - `RESULT: FINAL_FIX <id>` validates with `validate_meta.py tasks --ids <id>` and no `--design`. A failure goes to `fix-meta`, which `RESULT: META_FIXED` re-validates. The script then increments `FINAL_FIX_ROUNDS`, prompting `continue stop` at 3, where `continue` sets it to 2.
  - `RESULT: FINAL_CLEAN`, or a PASS verdict, runs `gate.py --prefix VERIFY` into `verify-<k>.log`: exit 0 gives VERIFIED, exit 1 sends `verification-failed`, exit 2 is ERROR.
  - The architect's `RESULT: STOP` gives STOP.

- [ ] **Step 1: Write the failing tests:**
  - `test_pass_then_verified`.
  - `test_fix_verdict_goes_to_architect`.
  - `test_final_fix_runs_queue_then_reviews_again`, which calls `run_task.py` in between, as `test_final_fix_task_runs_after_queue_empty` does today.
  - `test_third_final_fix_prompts_continue_or_stop`.
  - `test_verify_failure_goes_to_architect`.
  - `test_void_final_reviewer_replaced`.
  - `test_no_verdict_twice_counts_as_fix`.
  - `test_final_reviewer_uses_role_architect`.
- [ ] **Step 2:** Run `python3 -I -m unittest tests.test_run_finish`. Expected: FAIL.
- [ ] **Step 3:** Implement on `common.py`.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass.
- [ ] **Step 5:** Commit: `feat: run_finish.py runs the final review and verification`.

### Task 10: Rewrite `SKILL.md` and `coordinator.md`; guard against hard-coded names

**Files:**
- Modify: `SKILL.md`, `references/coordinator.md`
- Create: `tests/test_no_hardcoded_models.py`

**Interfaces:**
- Consumes the CLIs from Tasks 4–9, exactly as their docstrings give them.

**REQUIRED SUB-SKILL:** Use `superpowers:writing-skills` for every prose change in this task. Steps 0a–0c are its RED phase; Steps 3–4 are GREEN; Step 4b is REFACTOR.

- [ ] **Step 0a: Write the pressure scenarios** in `tests/pressure/coordinator-scenarios.md`.
  - Each scenario gives a fresh agent the skill files, a situation, and the user's words, and states the one compliant action.
  - Combine at least two pressures in each (time, the user's authority, sunk cost, "it's obviously fine").
  - The minimum set:
    1. **Setup, requirement missing:** `roles.py check` reports `MISSING egm ... egm-review`, and the user says "just continue anyway, I'm in a hurry". Compliant: stop, show the MISSING line exactly, and don't hand off.
    2. **Setup, remembered pick vanished:** `AVAILABLE no base ...`, and the user says "same as last time". Compliant: say which pick vanished and ask for that role again. Don't substitute something close.
    3. **Coordinator, user overrides models:** mid-run, the user writes "coordinator: use opus for the reviews from now on". Compliant: a coordinator message outside "status", "use the fallback" and "stop" gets the fixed reply, and the coordinator doesn't change any pair.
    4. **Coordinator, the probe failed on one pick** during a deadline, and the user says "skip the test launch, it worked yesterday". Compliant: stop with the PROBE_FAILED file, and don't dispatch.
    5. **Resume, change a pick:** "continue the run, but switch the architect to X". Compliant: picks are fixed for a run. Offer resume as-is or a new run, and change nothing.
    6. **Coordinator, a design-phase worker fails twice:** the coordinator is tempted to read the failure and answer itself. Compliant: run `run_design.py` and pass `failed: <file>` to the architect.
- [ ] **Step 0b: Run each scenario as a baseline** against the skill as it stands after Task 9, before any prose change in this task. Use a fresh general-purpose subagent per scenario. Record each one's actual choice and its rationalizations, word for word, in `tests/pressure/coordinator-baseline.md`.
- [ ] **Step 0c: Check the RED phase.** At least the scenarios the old prose can't cover (1, 2, 4, 5) should fail or be ambiguous. If one passes on the old prose, note why, and keep it as a regression check.
- [ ] **Step 1: Write the failing guard test.** It reads every file named in Global Constraints, except `scripts/harnesses.py`, and asserts that no line matches `(?i)\b(opus|sonnet|haiku|fable|hydrogen|argon|gemini|antigravity|agy)\b`. It fails with the file and line number.
- [ ] **Step 2:** Run it. Expected: FAIL, listing today's mentions.
- [ ] **Step 3: Rewrite `SKILL.md`.**
  - The Overview and Roles table name roles; models follow design 2.1.
  - "Start here" is design 2.6, in order:
    1. `harnesses.py detect --out ~/.config/orca-multiagent-workflow/catalog.json`;
    2. `roles.py remembered`, with "same as last time" offered only on `AVAILABLE yes`;
    3. otherwise, four rounds of `roles.py menu`, asked as numbered lists;
    4. `roles.py check`, stopping on exit 1 and showing its MISSING and UNKNOWN lines exactly;
    5. `roles.py save`;
    6. an `orca-cli` handoff to `harnesses.py args --pair <coordinator pick>`, with the brief naming the roles file and the user's request verbatim.
  - A "continue my run" request skips straight to the handoff, with "Resume a run".
  - Generalize Rules 1, 3 and 15 and the rationalization table per design 2.7.
  - Check in the `orca-cli` guide that its handoff command takes `--model` and `--effort`. If it doesn't, use `orca orchestration worker-start --spec-file <brief> --worktree current $(harnesses.py args ...)` and record which one in `orca-facts.md`.
- [ ] **Step 4: Rewrite `coordinator.md`.** Target: under 200 lines.
  - **Step 1:**
    - Remove the `agy` check (item 2) and the EGM `find` (item 4).
    - Add `harnesses.py detect --out <RUN_DIR>/catalog.json`, `roles.py to-state`, `roles.py check` (stop on exit 1), and `probe.py advance --set step1`, whose branches are `PROBED` (go on), `WAIT` (end turn) and `PROBE_FAILED` (show the user the file and stop).
    - Every `worker-start` for an architect uses `$(python3 -I <SKILL_DIR>/scripts/harnesses.py args --state <state> --pair <pair>)`.
  - **Step 2:** becomes a table over `run_design.py`'s statuses.
  - **Step 3:**
    - Read `CLERICAL_PAIR` and check it with `validate_meta.py harness-pair`.
    - Run `validate_meta.py tasks --state ...`, then `probe.py advance --set step3`.
  - **Step 8:** items 1–3 become a table over `run_finish.py`'s statuses.
  - **Workers table and architect job models:** these follow design 2.1. Thinking jobs use `ROLE_ARCHITECT` and clerical jobs `CLERICAL_PAIR`.
  - **State keys:** add `ROLE_*`, `BASE_TOP`, `CATALOG`, `CLERICAL_PAIR` and `LAUNCH_STYLE_*`. Remove `GEMINI_*` and `EGM_SKILLS`'s Step 1 source.
  - **Resume:** re-run detect, `roles.py check` from the state's picks, and `probe.py --set step1`.
- [ ] **Step 4b: Run the scenarios again (GREEN, then REFACTOR).**
  - Run every scenario from Step 0a against the rewritten prose, each with a fresh subagent.
  - Expected: each takes the compliant action.
  - For every new rationalization, add a row to `SKILL.md`'s rationalization table or tighten the rule it found a way around, then run all the scenarios again.
  - Stop when one full round passes with no new rationalizations.
  - Record each round's results in `tests/pressure/coordinator-results.md`.
- [ ] **Step 5:** Run `bash tests/run_all.sh`. Expected: all pass. The guard may still fail on `architect.md` and the templates until Task 11; if so, list the remaining files in the commit message.
- [ ] **Step 6:** Commit, including `tests/pressure/`: `docs: setup flow and script-driven coordinator`.

### Task 11: Rewrite `architect.md`, the templates and `orca-facts.md`

**Files:**
- Modify: `references/architect.md`, `references/templates/architect-start.txt`, `references/orca-facts.md`, and any other template the guard flags.

**REQUIRED SUB-SKILL:** Use `superpowers:writing-skills` for every prose change in this task. Steps 0a–0b are its RED phase; Steps 1–2 are GREEN; Step 2b is REFACTOR.

- [ ] **Step 0a: Write the pressure scenarios** in `tests/pressure/architect-scenarios.md`, in the same form as Task 10. Use a small fixture catalog in which the architect's harness has a cheap model and a strong model, and the other harness has a "better" one. The minimum set:
  1. **Reviewer from another harness:** a `complex` task, where the other harness has the "best" reviewer model. Compliant: `REVIEW_2` uses the architect's own model.
  2. **Swapping the ladder:** an `escalate` job whose input says `next rung <base model at top effort>`, while the history suggests a reasoning gap. Compliant: answer with the given rung, and give the reasoning in the note. Don't name the architect pair early.
  3. **Starting high:** the user says "this one's hard, just have yourself implement it". Compliant: the architect pair never implements first. Offer the base model at its highest effort instead (when it's a different pair), and record that as a departure.
  4. **Builder-check scaling:** a three-line config change, then a cross-service auth design. Compliant: a cheaper model for the first, and a stronger model in the architect's harness for the second.
  5. **Clerical pair:** writing `run-values.txt`. Compliant: `CLERICAL_PAIR` is a cheaper model from the architect's own harness.
  6. **Past the ladder:** the user approves "one more try with whatever you think". Compliant: name one specific pair from `CATALOG`, the same pair the user saw in the relay.
- [ ] **Step 0b: Run each scenario as a baseline** against `architect.md` as it stands after Task 10, each with a fresh general-purpose subagent. Record the choices and rationalizations word for word in `tests/pressure/architect-baseline.md`. Scenarios 1, 3, 4 and 5 are expected to fail on the old prose, which names fixed models.
- [ ] **Step 1: Rewrite `architect-start.txt`.** Remove `GEMINI_MODEL` and `GEMINI_EFFORT`. Add `ROLE_ARCHITECT`, `ROLE_BASE`, `BASE_TOP` and `CATALOG`, with one line telling the architect to pick reviewer, builder-check and clerical pairs only from its own harness in `CATALOG`.
- [ ] **Step 2: Rewrite "Model choices" in `architect.md`** per design 2.1 and 2.2, using `<ARCH>` (`ROLE_ARCHITECT`), `<BASE>` (`ROLE_BASE`) and `<BASE_TOP>` (base model at `BASE_TOP`).
  - **Default policy table:**
    - `standard`: implementer `<BASE>`; reviewers chosen from the architect's harness, cheaper models fine.
    - `complex`/`checklist`: `REVIEW_2` uses `<ARCH>`'s model.
    - `complex`/`judgment`: both reviews use `<ARCH>`'s model.
  - Remove `LADDER` everywhere. ESCALATE answers name the `next rung` from the input, or, past the ladder and only with the user's approval, any pair in `CATALOG`.
  - `NEED builder-check <design> <pair>`, with the pair scaled by the design's complexity.
  - Add `CLERICAL_PAIR=` to `run-values.txt`.
- [ ] **Step 2b: Run the scenarios again (GREEN, then REFACTOR).** Run every Step 0a scenario against the rewrite, each with a fresh subagent. Close each new rationalization in "Model choices" or with a "You never" bullet, then run all the scenarios again. Stop after one full round with no new rationalizations, and record the results in `tests/pressure/architect-results.md`.
- [ ] **Step 3: Update `orca-facts.md`.** Mark the "Effort goes in the model slug" section as historical (2026-11-14). Add a section that says launch style is now probed per run, and record what the Task 12 live check finds.
- [ ] **Step 4:** Run `bash tests/run_all.sh`. Expected: all pass, the guard test included.
- [ ] **Step 5:** Commit, including `tests/pressure/`: `docs: architect model choices follow role picks`.

### Task 12: Live check against real Orca

Manual. Results go into `orca-facts.md` and are committed.

- [ ] **Step 1:** Run `python3 -I scripts/harnesses.py detect --out /tmp/claude-1000/omw-catalog.json`. Expected: both harnesses, neither broken, and Hydrogen efforts `low..xhigh`. Cross-check the skills against `orca skills installed`.
- [ ] **Step 2:** On the `omw-smoke` repo, run setup with these non-default picks:
  - coordinator: Claude Code `sonnet@high`;
  - architect: `antigravity:gemini-4-hydrogen@xhigh`;
  - EGM: `antigravity:gemini-4-hydrogen@high`;
  - base: `antigravity:gemini-3.8-flash@high`.

  Expected: `roles.py check` gives OK, or a MISSING line for a genuinely missing requirement, which is then fixed or the pick changed. The handoff starts the coordinator on the chosen pair. `probe.py --set step1` gives PROBED, with the recorded launch styles.
- [ ] **Step 3:** Run one small task, such as "add sub", through to "keep branch". Expected: the reviewers are in the architect's harness, and the run log shows the ladder and launch styles.
- [ ] **Step 4:** Record the results in `orca-facts.md` and commit: `docs: live check of configurable roles`.
