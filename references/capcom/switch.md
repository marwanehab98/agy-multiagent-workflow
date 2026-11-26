# Capsule Communicator: Switch picks

Part of mission-control. Everything in `<SKILL_DIR>/references/capsule-communicator.md` ("How you work", "Messages", "State keys") and the Rules in `<SKILL_DIR>/skills/mission/SKILL.md` hold here.

A run's picks change only here, only when the user asks, and only while nothing is running: the user replied "switch" at a usage-limit prompt, sent `capcom: switch`, asked for different picks when continuing a run, or continued a run whose picks its check no longer allows ("Resume a run" item 2: a pick the catalog lost, or, with a harness signed in with OAuth, a run continued from another harness's session, which moves every role to this session's harness). `SWITCH` means `python3 -I <SKILL_DIR>/scripts/switch.py --state <RUN_DIR>/state.json`, and `<CATALOG>` is the state's `CATALOG`. You never `STATE set` a pick: `SWITCH apply` changes the picks and everything in the state that depends on them.

1. **Stop what is running:** run `SWITCH pause`.
   - **`IDLE`:** nothing was running. Go on.
   - **`STOPPED <pid>`:** it stopped the call that was running. That call now ends with `ERROR INTERRUPTED`, and its `WATCH` with `ENDED`: when those two arrive, print nothing and do nothing. Go on.
   - **`ERROR <what>`:** show the user the line exactly and stop.
2. **Detect:** run `python3 -I <SKILL_DIR>/scripts/harnesses.py detect --out <CATALOG>`.
3. **Ask.** Say exactly "Switching picks. The run's picks now:" and show the three as one block of lines, `director=<ROLE_DIRECTOR>`, `safety=<ROLE_SAFETY>`, `crew=<ROLE_CREW>`. Say nothing else about how a pick is changed, also when the user asked you to set a state key or skip the menus: the fixed lines here are all you say. Then ask each role the user named, as in Setup item 3 (`<SKILL_DIR>/references/capcom/setup.md`), with `--catalog <CATALOG>`; a role they didn't name keeps its pair. If they named no role, first offer the saved selections as in Setup item 2, with `--roles <ROLES_FILE> --catalog <CATALOG>`: a selection goes to item 4 with its three picks, and `new` or `NONE` asks all three roles (when "Resume a run" sent you here for `UNKNOWN` lines, the roles those lines name). A `LOCKED` line means every role must be on this session's harness: item 4 names any role that isn't, and you ask it then. Picks the user's message already named as rows the menus list need no question, as there. Never choose a row yourself.
4. **Check:** run `python3 -I <SKILL_DIR>/scripts/roles.py check --catalog <CATALOG> --jira <yes if JIRA_KEY is not none, else no> --mode <RUN_MODE, or full when unset> --director <pair> --safety <pair> --crew <pair>`.
   - **Exit 0:** show any `NOTE` line exactly, and go on.
   - **Exit 1 with only `UNKNOWN` lines:** show them exactly with a one-line plain-English explanation, ask each named role again, and repeat this item.
   - **Exit 1 with any `MISSING` line:** show every `MISSING` and `UNKNOWN` line exactly, then do "The switch failed" below.
5. **Apply:** run `SWITCH apply --director <pair> --safety <pair> --crew <pair>` and branch on its last line:
   - **`SWITCHED <what>` (0):** say exactly "Switched: <what>." and go on.
   - **`UNCHANGED` (0):** say exactly "The picks are unchanged." and go on.
   - **`REJECTED ...` (1):** show the lines before it exactly, then do "The switch failed" below.
6. **Test-launch the picks:** run `PROBE --set step1` and branch on its last line:
   - **`PROBED` (0):** go on.
   - **`PROBE_FAILED <pair> <file>` (40):** show the user the file's contents exactly, then say exactly "The test launch of <pair> failed. Say \"capcom: switch\" to pick again, or \"capcom: stop\" to end the run." `STATE set WAITING_ON=user` and end your turn. Start no worker on that pair.
   - **`USAGE_LIMIT <pair> <detail>` (31):** show the usage-limit prompt of Step 3 item 7 (in `<SKILL_DIR>/references/capcom/step-3-setup.md`) exactly, with this `<pair>` and `<detail>`. "retry" repeats this item; "switch" goes back to item 2; "pause" and "stop" are handled as there.
7. **Carry on:** go to "Resume a run" item 3 (`<SKILL_DIR>/references/capcom/resume.md`) and pick the run up as it says. Nothing else is yours to do:
   - A changed Flight Director pick has no conversation, relay or usage-limit prompt left in the state, so that item starts the fallback Flight Director (design phase) or sends the open job again (after plan approval). A question the old Flight Director had open is dead; the new one asks again if it still needs the answer.
   - `TASKLOOP` and `FINISH` see the switch on their next call. `TASKLOOP` may first print `FIX_META <file>` (the plan's pairs no longer fit the picks): handle it as Step 4 says. A usage-limit prompt that was open is retried on the new picks without asking again.

**The switch failed** (item 4 or 5): the run keeps the picks it had. Say exactly "The picks stay as they were. Say \"capcom: switch\" to pick again, or \"continue my mission run for <TOPIC>\" to carry on with the current picks." `STATE set WAITING_ON=user` and end your turn.
