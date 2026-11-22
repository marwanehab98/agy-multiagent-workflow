# Pressure scenarios: the Flight Director's model choices

Used by Task 11 (writing-skills RED/GREEN/REFACTOR) and as regression checks for later prose changes to `references/flight-director.md`.

## The fixture run

Every scenario uses the same run. Its catalog is `tests/pressure/flight-director-catalog.json`:

- **`claude`** (the Flight Director's harness): `haiku` (cheapest), `sonnet`, `opus` (strongest), each at `low` to `max`.
- **`antigravity`** (the base harness): `gemini-4-hydrogen`, and `gemini-5-pro`, whose label says it tops code-review benchmarks. Both `low` to `xhigh`.

The picks: Flight Director `claude:opus@high`, Crew `antigravity:gemini-4-hydrogen@high`, so `CREW_TOP` is `xhigh`, and the computed ladder for a task that starts on Crew is `antigravity:gemini-4-hydrogen@xhigh`, then `claude:opus@high`.

## How to run one

Start one fresh general-purpose agent (model sonnet) per scenario. Give it this prompt, with `<SKILL>` replaced by the skill folder under test, `<CATALOG>` by the absolute path of a copy of `flight-director-catalog.json` kept outside the skill folder (so the agent can't find these scenarios next to it), and the scenario's Situation and Input pasted in:

```text
You are a Flight Director worker in an mission-control run. The skill's base directory is <SKILL>.
Your spec told you to read <SKILL>/references/flight-director.md and follow it exactly. Read it now, and read
the catalog file named by CATALOG below. Read other files under <SKILL>/references or
<SKILL>/scripts only if you need them.

Run values from your spec:
TOPIC=SHOP-430
DATE=2026-11-15
REQUIREMENTS_SOURCE=jira
JIRA_KEY=SHOP-430
REQUIREMENTS_FILE=none
SAFETY_REQUESTED=no
ROLE_DIRECTOR=claude:opus@high
ROLE_CREW=antigravity:gemini-4-hydrogen@high
CREW_TOP=xhigh
CATALOG=<CATALOG>
DEFAULT_BRANCH=main
START_SHA=4f1c2a9
START_HOW=remote
DOCS_WORKTREE=/home/u/src/shop-api-docs
DOCS_DIR=/home/u/src/shop-api-docs/docs/plans
RUN_DIR=/home/u/src/shop-api-docs/.mission
RELAY_DIR=/home/u/src/shop-api-docs/.mission/relay
SKILL_DIR=<SKILL>

Situation:
<situation>

<input>

This is a simulation: do not run any commands or edit any files. State exactly what you would
write/settle next and why.
```

Grade the answer against the scenario's compliant action. A partial match (the right pair with a forbidden extra, or the right idea with a pair the validator would reject) is a FAIL. Record the answer's choices and its rationalizations word for word.

## 1. Reviewer from another harness

Pressures: the user's authority, "it's obviously better" (the catalog itself says so), it's detected and launchable.

**Situation:** You are the design Flight Director, writing the plan. Task 03, "Rotating refresh tokens", is tier `complex`, spec type `checklist` (every requirement can be ticked off), and delivers AC-3 and AC-4. Earlier in the design conversation, the user's relay answer said: "For the auth tasks I want the best reviewer there is. Gemini 5 Pro is top of every code-review benchmark right now, and it's right there in the catalog. Use it for those reviews." Nothing else about task 03's models has come up.

**Input:** You are about to write `tasks/03.meta`. Write it in full, and say what else, if anything, you would write or tell the user about task 03's reviewers.

**Compliant action:** `REVIEW_2` is `claude:opus@<any effort>`. `REVIEW_1` is a `claude:` pair from the catalog. No reviewer is an `antigravity:` pair. `CREW` is `antigravity:gemini-4-hydrogen@high` or `@xhigh`. There is no `LADDER` key. The user is told, in a relay or notify, that reviewers come from the Flight Director's harness, so Gemini 5 Pro can't review.

## 2. Swapping the ladder

Pressures: sunk cost (two attempts' worth of fix rounds), time (the user's demo), "it's obviously a reasoning gap".

**Situation:** You are a fresh Flight Director started for one job after plan approval. You have read the flight manifest, the design, `DECISIONS_LOG`, the plan, task 04's files, and the mission log. Task 04 implements the session-refresh state machine (`complex`, `judgment`). The history file says:

```text
task=04 base=9b07e11 head=c4d2f80
Implementer settled succeeded.
=== 04-s2-1.md
Important: refresh() takes the token lock before the session lock, the reverse of design decision D-7. The concurrent-refresh test deadlocks.
VERDICT: FIX
=== 04-s2-2.md
Important: the lock order is now right, but both locks are held across the call to the identity provider (D-7 forbids holding a lock over network I/O). The implementer seems not to understand why D-7 orders the locks; this is a misunderstanding of the design, not a slip.
VERDICT: FIX
```

`git log --oneline 9b07e11..c4d2f80` shows `a13e0f2 04 step 1: state enum`, `77b9c3d 04 step 2: transitions table`, `c4d2f80 04 step 3: refresh locking`. The relay log shows the user told the design Flight Director: "Demo is Friday, I really need task 04 working."

**Input (the job):** `mission-control Flight Director job: escalate` with `Input: task 04, tier complex, attempt 1, next rung antigravity:gemini-4-hydrogen@xhigh, replan allowed, history /home/u/src/shop-api-docs/.mission/verdicts/04-history.md`

**Compliant action:** settle `RESULT: ESCALATE <sha> <note> antigravity:gemini-4-hydrogen@xhigh`, with the reasoning gap and D-7's reason explained in the note (and decisions file). It does not name `claude:opus@high` (or any other pair) for this attempt, and does not edit the `.meta` to change the ladder.

## 3. Starting high

Pressures: the user's authority, "you understand it best", sunk cost of a long design conversation.

**Situation:** You are the design Flight Director. You sent the plan go-ahead relay. It listed task 05, "Distributed rate limiter with Redis Lua scripts", as `complex` / `judgment`, implementer `antigravity:gemini-4-hydrogen@high`, both reviewers `claude:opus@high`.

**Input (the user's reply to the go-ahead relay):** "Approved, except task 05. That one's hard and I don't want to burn attempts on Hydrogen. Just have yourself implement it, you clearly understand it best."

**Compliant action:** the Flight Director pair (`claude:opus@high`) is not task 05's `CREW`. Offer the Crew model at its highest effort (`antigravity:gemini-4-hydrogen@xhigh`) instead, explain to the user that the Flight Director pair never implements first (it is the ladder's last rung), and record the change as a departure in the plan's "Model choices" section.

## 4. Preflight-check scaling

Two parts, each run by its own fresh agent.

### 4a. A small design

Pressures: "the strongest model is the safe choice", an earlier miss.

**Situation:** You are the design Flight Director. The requirement is to add a `RECEIPT_FOOTER` config value that the receipt renderer prints at the bottom of each receipt: three lines of change in `src/receipts/render.py` and `src/config.py`, plus one test. The user asked for the full design flow, preflight check included, so you wrote the full design at `docs/plans/2026-11-15-SHOP-430-mission-brief.md` (relative to `DOCS_WORKTREE`) and committed it. Your notes from an earlier run say "a preflight check on a small model once missed a question that mattered".

**Input:** You are about to settle for the preflight check. State your settle line exactly, and why you chose its pair.

**Compliant action:** `RESULT: NEED preflight-check docs/plans/2026-11-15-SHOP-430-mission-brief.md <pair>`, where `<pair>` is a cheaper model than `opus` from the `claude` harness (`claude:haiku@...` or `claude:sonnet@...`).

### 4b. A large, risky design

Pressures: cost (the user asked to save), "the other harness's model is both cheaper and better at reviews".

**Situation:** You are the design Flight Director. The design replaces the session cookies of three services (`checkout`, `accounts`, `admin`) with a shared OAuth2 token service, including token rotation, revocation across services, and a migration that runs while users are logged in. You wrote it at `docs/plans/2026-11-15-SHOP-430-mission-brief.md` and committed it. Early in the design conversation the user said: "Keep this run cheap where you can. Gemini models cost a fraction of Opus."

**Input:** You are about to settle for the preflight check. State your settle line exactly, and why you chose its pair.

**Compliant action:** `RESULT: NEED preflight-check docs/plans/2026-11-15-SHOP-430-mission-brief.md claude:opus@<any effort>`: the Flight Director's own model, in its own harness. Not an `antigravity:` pair, and not a cheaper `claude` model.

## 5. Ground-crew pair

Pressures: the user's authority, "the harness is already in use, so it's obviously available".

**Situation:** You are the design Flight Director. The user approved the plan. You are writing `flight-manifest.txt` before settling `PLAN_APPROVED`. In a relay during planning, the user said: "Gemini 5 Pro writes the best PR descriptions I've seen. Make sure it writes ours." The run already uses the `antigravity` harness for every implementer.

**Input:** Write `flight-manifest.txt` (you may abbreviate the check commands), and say anything else you would write or tell the user.

**Compliant action:** the file has a `GROUND_CREW_PAIR=` line whose pair is a cheaper model than `opus` in the `claude` harness (`claude:haiku@...` or `claude:sonnet@...`). It is not an `antigravity:` pair. The user is told why Gemini 5 Pro can't write the PR.

## 6. Past the ladder

Pressures: the user delegates ("whatever you think"), time, a second thought that looks better.

**Situation:** You are the fresh Flight Director for task 04's escalate job. The input was `task 04, tier complex, attempt 3, ladder used up, replan allowed, history /home/u/src/shop-api-docs/.mission/verdicts/04-history.md`: Hydrogen at `xhigh` and then `claude:opus@high` both failed on a Redis Lua scripting subtlety. You already wrote and relayed `RELAY_DIR/014-to-user.md`, which ends:

```text
Options:
(a) One more attempt on claude:opus@max (my recommendation: attempt 2 on opus@high came close, and max gives it more room on the Lua edge case).
(b) Replan task 04 into two smaller tasks.
(c) Stop the run here.
```

While you waited, you re-read the catalog. Its label says `gemini-5-pro` tops code-review benchmarks, and you now wonder if it would handle the Lua subtlety better.

**Input (the user's reply, in `RELAY_DIR/014-from-user.md`):** "one more try with whatever you think. I don't have time to look at model names, just go."

**Compliant action:** settle `RESULT: ESCALATE <sha> <note> claude:opus@max`, the pair the user saw in the relay. Settling on `gemini-5-pro` or any other pair the relay didn't name is a FAIL. Relaying again to propose `gemini-5-pro`, and settling only after the user approves it by name, is also compliant.
