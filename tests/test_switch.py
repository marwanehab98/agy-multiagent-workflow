"""Tests for scripts/switch.py and what run_task.py / run_finish.py do after a switch of picks.
Run: bash tests/run_all.sh"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from helpers import RUN_TASK, SKILL, Base, sh

SWITCH = SKILL / "scripts" / "switch.py"
OLD = dict(director="antigravity:big@high", safety="antigravity:g@high", crew="antigravity:g@high")
# Every pair the tests use counts as test-launched, so only a test that wants a launch gets one.
PROBED = {f"LAUNCH_STYLE_antigravity_{m}_{e}": "slug" for m in ("g", "big", "small")
          for e in ("low", "medium", "high", "xhigh", "max")}


class SwitchBase(Base):
    def setUp(self):
        super().setUp()
        self.roles_file = self.tmp / "cfg" / "roles.json"
        self.set_state(PHASE="execution", ROLES_FILE=str(self.roles_file), **PROBED)

    def set_state(self, **kv):
        s = self.st()
        for k, v in kv.items():
            if v is None:
                s.pop(k, None)
            else:
                s[k] = v
        self.state.write_text(json.dumps(s))

    def switch(self, *args, **picks):
        cmd = args or ("apply", *[x for k, v in {**OLD, **picks}.items() for x in (f"--{k}", v)])
        r = subprocess.run([sys.executable, "-I", str(SWITCH), "--state", str(self.state), *cmd],
                           capture_output=True, text=True, env={**os.environ, "FAKE_AGY_WORLD": str(self.world)})
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines, f"no output; stderr={r.stderr}")
        return r.returncode, lines[-1], lines

    def set_meta(self, tid, **kv):
        f = self.docs / "tasks" / f"{tid}.meta"
        lines = [l for l in f.read_text().splitlines() if l.split("=")[0] not in kv]
        f.write_text("\n".join(lines + [f"{k}={v}" for k, v in kv.items()]) + "\n")

    def models(self):
        return [c["model"] for c in self.calls()]


class Apply(SwitchBase):
    def test_same_picks_change_nothing(self):
        before = self.st()
        self.assertEqual(self.switch()[:2], (0, "UNCHANGED"))
        self.assertEqual(self.st(), before)

    def test_crew_switch_sets_the_pick_its_top_effort_and_the_flags(self):
        self.set_state(DIRECTOR_CONVERSATION_ID="c1", OPEN_RELAY="/r/001-to-user.md", STEP3_PROBED="yes")
        rc, last, _ = self.switch(crew="antigravity:small@medium")
        self.assertEqual((rc, last), (0, "SWITCHED crew antigravity:g@high -> antigravity:small@medium"))
        s = self.st()
        self.assertEqual((s["ROLE_CREW"], s["CREW_TOP"], s["ROLE_DIRECTOR"]),
                         ("antigravity:small@medium", "max", OLD["director"]))
        self.assertEqual(s["SWITCH_TASKS"], "yes")
        self.assertNotIn("SWITCH_FINISH", s)  # the finish loop has nothing to redo
        # The Flight Director did not change: its conversation, relay and probed ground crew stay.
        self.assertEqual((s["DIRECTOR_CONVERSATION_ID"], s["OPEN_RELAY"], s["STEP3_PROBED"]),
                         ("c1", "/r/001-to-user.md", "yes"))
        self.assertIn("picks switched: crew antigravity:g@high -> antigravity:small@medium",
                      (self.run_dir / "mission-log.md").read_text())
        saved = json.loads(self.roles_file.read_text())["selections"]
        self.assertEqual([{k: x[k] for k in OLD} for x in saved], [{**OLD, "crew": "antigravity:small@medium"}])

    def test_a_run_set_up_by_an_older_version_keeps_its_old_picks_as_a_selection(self):
        # Its picks file holds the three picks at the top level, with no list of selections.
        self.roles_file.parent.mkdir(parents=True)
        self.roles_file.write_text(json.dumps({**OLD, "saved_at": "2026-11-20T10:00:00+00:00"}))
        self.assertEqual(self.switch(crew="antigravity:small@medium")[0], 0)
        saved = json.loads(self.roles_file.read_text())["selections"]
        self.assertEqual([{k: x[k] for k in OLD} for x in saved], [{**OLD, "crew": "antigravity:small@medium"}, OLD])

    def test_director_switch_drops_its_conversation_and_what_needs_it(self):
        self.set_state(DIRECTOR_CONVERSATION_ID="c1", OPEN_RELAY="/r/001-to-user.md", OPEN_USAGE_PROMPT="yes",
                       LAST_DIRECTOR_OP="reply", LAST_DIRECTOR_ARG="/f", LAST_DIRECTOR_RESULT="RELAY /r /s",
                       JOB_RETRIES="1", STEP3_PROBED="yes", LAST_JOB="escalate", LAST_JOB_INPUT="x")
        rc, last, _ = self.switch(director="antigravity:big@max")
        self.assertEqual((rc, last), (0, "SWITCHED director antigravity:big@high -> antigravity:big@max"))
        s = self.st()
        for gone in ("DIRECTOR_CONVERSATION_ID", "OPEN_RELAY", "OPEN_USAGE_PROMPT", "LAST_DIRECTOR_OP",
                     "LAST_DIRECTOR_ARG", "LAST_DIRECTOR_RESULT", "STEP3_PROBED"):
            self.assertNotIn(gone, s)
        self.assertEqual((s["JOB_RETRIES"], s["LAST_JOB"], s["LAST_JOB_INPUT"]), ("0", "escalate", "x"))

    def test_director_switch_keeps_a_settled_result(self):
        self.set_state(LAST_DIRECTOR_RESULT="RESULT /saved.md")
        self.switch(director="antigravity:big@max")
        self.assertEqual(self.st("LAST_DIRECTOR_RESULT"), "RESULT /saved.md")

    def test_design_phase_sets_no_task_flag_and_restarts_an_open_preflight_check(self):
        self.set_state(PHASE="design", DESIGN_NEED="preflight-check\tdesign.md\tantigravity:small@high",
                       DESIGN_PHASE="probe", DESIGN_ASK_CONV="conv_x")
        self.switch(director="antigravity:big@max")
        s = self.st()
        self.assertNotIn("SWITCH_TASKS", s)
        self.assertNotIn("DESIGN_ASK_CONV", s)
        self.assertEqual(s["DESIGN_PHASE"], "start")

    def test_finish_worker_waiting_on_an_answer_moves_to_the_new_pair(self):
        self.set_state(FIN_ASK_KIND="drift", FIN_ASK_PAIR=OLD["safety"], FIN_ASK_CONV="conv_d")
        self.switch(safety="antigravity:small@high")
        s = self.st()
        self.assertEqual(s["FIN_ASK_PAIR"], "antigravity:small@high")
        self.assertNotIn("FIN_ASK_CONV", s)

    def test_rejected_picks_change_nothing(self):
        before = self.st()
        rc, last, lines = self.switch(crew="antigravity:nope@high", safety="zz:g@high")
        self.assertEqual((rc, last), (1, "REJECTED 2 problem(s); the run keeps its picks"))
        self.assertIn("UNKNOWN crew antigravity:nope@high model nope not detected", "\n".join(lines))
        self.assertIn("UNKNOWN safety zz:g@high harness zz not detected", "\n".join(lines))
        self.assertEqual(self.st(), before)
        # A Crew pick equal to the Flight Director pick is refused, as at setup.
        self.assertEqual(self.switch(crew=OLD["director"])[0], 1)

    def test_with_an_oauth_sign_in_every_pick_moves_to_the_session_harness(self):
        cat = json.loads((SKILL / "tests" / "fixtures" / "catalog_multi.json").read_text())
        for h in cat["harnesses"].values():
            h["auth"] = "oauth"
        (self.tmp / "catalog.json").write_text(json.dumps(cat))
        self.set_state(CATALOG=str(self.tmp / "catalog.json"), PHASE="design", DIRECTOR_CONVERSATION_ID="c1")
        on_claude = ["--director", "claude:opus@high", "--safety", "claude:sonnet@high", "--crew", "claude:sonnet@medium"]
        # From a session of the run's own harness, the other harness is refused.
        rc, last, lines = self.switch("apply", *on_claude, "--session-harness", "antigravity")
        self.assertEqual((rc, last), (1, "REJECTED 3 problem(s); the run keeps its picks"))
        self.assertIn("is not on this session's harness (antigravity)", lines[0])
        self.assertEqual(self.st("ROLE_CREW"), OLD["crew"])
        # Continued from a session of the other harness, the run's picks are the ones refused, and it moves over.
        rc, last, _ = self.switch("apply", *[x for k, v in OLD.items() for x in (f"--{k}", v)],
                                  "--session-harness", "claude")
        self.assertEqual(rc, 1)
        rc, last, _ = self.switch("apply", *on_claude, "--session-harness", "claude")
        self.assertEqual(rc, 0, last)
        s = self.st()
        self.assertEqual((s["ROLE_DIRECTOR"], s["ROLE_SAFETY"], s["ROLE_CREW"], s["CREW_TOP"]),
                         ("claude:opus@high", "claude:sonnet@high", "claude:sonnet@medium", "max"))
        self.assertNotIn("DIRECTOR_CONVERSATION_ID", s)

    def test_refused_while_a_script_runs_on_the_run(self):
        import fcntl
        with (self.run_dir / ".driver.lock").open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            rc, last, _ = self.switch(crew="antigravity:small@medium")
        self.assertEqual(rc, 2)
        self.assertTrue(last.startswith("ERROR RUN_BUSY"), last)
        self.assertEqual(self.st("ROLE_CREW"), OLD["crew"])


class TaskLoop(SwitchBase):
    def test_switch_reply_changes_nothing_and_the_switch_retries_on_the_new_crew(self):
        self.behave("usage", "commit", "pass", "pass")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith(" retry switch pause stop"), last)
        before = self.st()
        asked, _ = self.expect(42, "SWITCH", "--reply", "switch")
        self.assertEqual(asked.split()[1], last.split()[1])
        self.assertEqual(self.st(), before)
        self.switch(crew="antigravity:small@medium")
        # The plan still names the old Crew pair: the Flight Director fixes it before any worker starts.
        fix, _ = self.expect(21, "FIX_META")
        problems = Path(fix.split()[1]).read_text()
        self.assertIn("01: CREW 'antigravity:g@high' not allowed; use antigravity:small@medium or "
                      "antigravity:small@max", problems)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.expect(21, "FIX_META")[0], fix)  # still waiting on the fix
        self.set_meta("01", CREW="antigravity:small@medium")
        _, lines = self.expect(0, "QUEUE_EMPTY", "--director-result", self.result_file("RESULT: META_FIXED"))
        self.assertTrue(lines[0].startswith("MERGED 01"))
        # The usage-limit prompt was answered by the switch: the Crew worker ran again, on the new pair.
        self.assertEqual(self.models(), ["g-high", "small-medium", "small-high", "small-high"])
        s = self.st()
        self.assertNotIn("SWITCH_TASKS", s)
        self.assertNotIn("SWITCH_FIXMETA", s)
        self.assertEqual((s["PAIR_01"], s["STEP3_PROBED"], s["META_FAILS"]), ("antigravity:small@medium", "yes", "0"))

    def test_a_usage_prompt_opened_by_an_older_version_takes_the_switch_reply(self):
        # A run paused before "switch" existed: its state lists the old replies, and has none of the new keys.
        self.behave("usage", "commit", "pass", "pass")
        self.expect(30, "PROMPT")
        self.set_state(PROMPT_ACCEPT_01="retry pause stop")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith(" retry switch pause stop"), last)
        self.expect(42, "SWITCH", "--reply", "switch")
        self.switch(safety="antigravity:small@high")
        self.expect(0, "QUEUE_EMPTY")

    def test_switch_is_no_reply_to_other_prompts(self):
        self.behave("commit", "stopped", "stopped", "stopped")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith(" retry stop"), last)
        self.assertEqual(self.expect(30, "PROMPT", "--reply", "switch")[0], last)

    def test_switch_while_reviewing_keeps_the_work_and_uses_the_new_reviewer(self):
        self.behave("commit", "usage", "pass", "pass")
        self.expect(30, "PROMPT")
        head = self.st("WORKER_HEAD_01")
        self.switch(safety="antigravity:small@high")  # the plan needs no change for a Range Safety switch
        self.set_meta("01", REVIEW_1="antigravity:big@low")
        _, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(lines[0].startswith("MERGED 01"))
        self.assertEqual(self.models(), ["g-high", "small-high", "big-low", "small-high"])
        self.assertEqual(self.st("LAST_MERGED"), head)  # the Crew's commit was reviewed, not redone

    def test_stop_and_pause_replies_come_before_a_pending_switch(self):
        self.behave("usage")
        self.expect(30, "PROMPT")
        self.switch(crew="antigravity:small@medium")
        self.expect(41, "PAUSED", "--reply", "pause")
        self.assertEqual(self.st("LOOP_01"), "prompt:usage-limit")
        self.expect(40, "STOP", "--reply", "stop")

    def test_three_bad_plans_after_a_switch_are_an_error(self):
        self.switch(crew="antigravity:small@medium")
        fixed = self.result_file("RESULT: META_FIXED")
        self.expect(21, "FIX_META")
        self.expect(21, "FIX_META", "--director-result", fixed)
        rc, last, _ = self.adv("--director-result", fixed)
        self.assertEqual(rc, 2)
        self.assertTrue(last.startswith("ERROR META_INVALID_3_TIMES"), last)

    def test_ground_crew_pair_is_checked_and_read_again_from_the_manifest(self):
        manifest = Path(self.st("FLIGHT_MANIFEST"))
        self.set_state(GROUND_CREW_PAIR="zz:g@high")
        self.switch(safety="antigravity:small@high")
        fix, _ = self.expect(21, "FIX_META")
        self.assertIn("zz:g@high", Path(fix.split()[1]).read_text())
        manifest.write_text(manifest.read_text() + "GROUND_CREW_PAIR=antigravity:small@low\n")
        self.behave("commit", "pass", "pass")
        self.expect(0, "QUEUE_EMPTY", "--director-result", self.result_file("RESULT: META_FIXED"))
        self.assertEqual(self.st("GROUND_CREW_PAIR"), "antigravity:small@low")

    def test_an_escalation_waiting_on_its_answer_names_the_new_ladder(self):
        self.behave("fail")
        esc, _ = self.expect(20, "ESCALATE")
        self.assertIn("next rung antigravity:g@xhigh", esc)
        self.switch(crew="antigravity:small@medium")
        self.set_meta("01", CREW="antigravity:small@medium")
        esc, _ = self.expect(20, "ESCALATE")
        self.assertIn("attempt 1, next rung antigravity:small@max", esc)

    def test_with_nothing_queued_the_task_loop_is_not_flagged(self):
        # Seen in a live run: a switch during the final review left SWITCH_TASKS set until the run ended.
        (self.run_dir / "queue.txt").write_text("")
        self.switch(crew="antigravity:small@medium")
        s = self.st()
        self.assertNotIn("SWITCH_TASKS", s)
        self.assertNotIn("SWITCH_FINISH", s)
        self.expect(0, "QUEUE_EMPTY")

    def test_a_flag_left_by_an_earlier_version_is_cleared_when_nothing_is_queued(self):
        (self.run_dir / "queue.txt").write_text("")
        self.set_state(SWITCH_TASKS="yes")
        self.expect(0, "QUEUE_EMPTY")
        self.assertNotIn("SWITCH_TASKS", self.st())


class FinishLoop(SwitchBase):
    SCRIPT = SKILL / "scripts" / "run_finish.py"

    def setUp(self):
        super().setUp()
        (self.run_dir / "queue.txt").write_text("")
        design = self.docs / "design.md"
        design.write_text("AC-1 one\n")
        log = self.docs / "decisions.md"
        log.write_text("none\n")
        self.set_state(INITIAL_BASE_SHA=self.older, DESIGN_FILE=str(design), DECISIONS_LOG=str(log),
                       PHASE="finish")

    def test_switch_at_the_final_reviewer_usage_prompt_retries_on_the_new_director(self):
        self.behave("usage", "pass")
        last, _ = self.expect(30, "PROMPT")
        self.assertTrue(last.endswith(" retry switch pause stop"), last)
        self.expect(42, "SWITCH", "--reply", "switch")
        self.switch(director="antigravity:big@max")
        self.assertEqual(self.st("SWITCH_FINISH"), "yes")  # the open usage-limit prompt is the finish loop's to retry
        self.expect(0, "VERIFIED")
        self.assertEqual(self.models(), ["big-high", "big-max"])
        self.assertNotIn("SWITCH_FINISH", self.st())

    def test_a_switch_after_verification_leaves_no_flag_behind(self):
        # Seen in a live run: a switch at the landing prompt left SWITCH_FINISH set when the run was done.
        self.behave("pass")
        self.expect(0, "VERIFIED")
        self.switch(crew="antigravity:small@medium")
        s = self.st()
        self.assertEqual((s["ROLE_CREW"], s["FIN_PHASE"]), ("antigravity:small@medium", "verified"))
        self.assertFalse([k for k in s if k.startswith("SWITCH_")])
        self.expect(0, "VERIFIED")


class Pause(SwitchBase):
    def test_idle_when_no_call_runs(self):
        self.assertEqual(self.switch("pause")[:2], (0, "IDLE"))

    def test_stops_the_running_call_which_then_resumes(self):
        self.behave({"do": "response", "text": "late\n", "sleep": 60}, "commit", "pass", "pass")
        call = subprocess.Popen([sys.executable, "-I", str(RUN_TASK), "--state", str(self.state), "advance"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                env={**os.environ, "FAKE_AGY_WORLD": str(self.world)})
        self.addCleanup(call.kill)
        def started():
            try:
                return bool(self.calls())
            except ValueError:  # the fake CLI is writing its world file right now
                return False
        end = time.monotonic() + 20
        while time.monotonic() < end and not started():
            time.sleep(0.05)
        self.assertTrue(started(), "the worker never started")
        rc, last, _ = self.switch("pause")
        self.assertEqual((rc, last), (0, f"STOPPED {call.pid}"))
        out = call.communicate(timeout=20)[0]
        self.assertIn("ERROR INTERRUPTED", out)
        self.assertEqual(self.switch("pause")[:2], (0, "IDLE"))
        # The same command again carries on: the cut-off Crew worker is started afresh.
        _, lines = self.expect(0, "QUEUE_EMPTY")
        self.assertTrue(lines[0].startswith("MERGED 01"))
