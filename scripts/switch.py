"""Change a run's picks while it is stopped, and stop the call running on a run.

Usage:
  python3 -I switch.py --state <RUN_DIR>/state.json apply --director P --safety P --crew P [--session-harness H]
  python3 -I switch.py --state <file> pause

`apply` replaces the run's ROLE_* picks with the three given pairs (a role that keeps its pick is given its
current pair). It checks them as `roles.py check` does, against the run's CATALOG (run `harnesses.py detect`
into it first), and refuses while another script runs on the run (ERROR RUN_BUSY). While a harness is signed in
with OAuth, every pick must be on the harness of the session that runs this command (detected from the environment,
or --session-harness), so a run moves to another harness by being continued from a session of that harness.
Earlier lines are events; the last line of stdout is the status:
Exit 0  SWITCHED <role> <old> -> <new>[; <role> ...]   the picks are changed
Exit 0  UNCHANGED                                      the three pairs are the run's picks already
Exit 1  REJECTED <n> problem(s); the run keeps its picks    earlier lines are the UNKNOWN / MISSING lines
Exit 2  ERROR <what>
Exit 64 bad usage

What `apply` changes besides ROLE_DIRECTOR, ROLE_SAFETY, ROLE_CREW and CREW_TOP:
- The picks file (ROLES_FILE): the new picks become its first selection (roles.py save_selection), so the next run
  offers them.
- A changed Flight Director pick drops its conversation and everything that needs it (DIRECTOR_CONVERSATION_ID,
  OPEN_RELAY, OPEN_USAGE_PROMPT, LAST_DIRECTOR_OP, LAST_DIRECTOR_ARG, a LAST_DIRECTOR_RESULT that is not a settled
  `RESULT`), resets JOB_RETRIES, and unsets STEP3_PROBED, so ground-crew jobs run on ROLE_DIRECTOR until
  GROUND_CREW_PAIR has been checked against the new pick. The capsule communicator then starts the fallback Flight
  Director (design phase) or sends the open job again (after plan approval), as when resuming a run.
- A design worker or finish worker waiting on a consult answer loses its conversation (DESIGN_ASK_CONV,
  FIN_ASK_CONV), so the answer goes to a fresh worker on the new pair; a preflight check not yet settled starts over
  (DESIGN_PHASE=start), so its pair is checked against the new Flight Director pick.
- A flag for each loop that has something to redo, seen on its next call, and only then, so none is left behind
  by a loop that does not run again: SWITCH_TASKS=yes when tasks are still queued (with nothing queued the task
  loop has nothing to fit to the new picks: a final-fix task queued later is checked against them when it is
  written), and SWITCH_FINISH=yes when run_finish.py waits at a usage-limit prompt or on a final-fix task's test
  launch (its reviewers read their pairs each time they start). run_task.py then checks the remaining tasks' .meta files and GROUND_CREW_PAIR against the new picks
  (flight director job fix-meta until they pass), test-launches their pairs, and puts the task in progress on its
  new pairs; both retry a usage-limit prompt that was open.

`pause` stops the call running on the run (run_design.py, run_task.py, run_finish.py, probe.py or
run_director.py) with SIGTERM: the call stops its worker and ends with `ERROR INTERRUPTED`, and the same command
run again resumes it. State is left as the call left it.
Exit 0  STOPPED <pid>     the call was stopped
Exit 0  IDLE              no call is running on this run
Exit 2  ERROR STILL_RUNNING <pid>   the call did not end within the wait
"""
import argparse
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import ERROR, Done, Run, _pid_command, drive  # noqa: E402
from harnesses import top_effort  # noqa: E402
from pairs import parse_pair  # noqa: E402
from roles import ROLES, check, save_selection  # noqa: E402

REJECTED = 1
SCRIPTS = ("run_design.py", "run_task.py", "run_finish.py", "run_director.py", "probe.py", "switch.py")
PAUSE_WAIT = 60  # seconds a stopped call gets to stop its worker and release the run


def apply(r, new, events, session_harness=None):
    r.require_new_format()
    catalog = json.loads(Path(r.need("CATALOG")).read_text(encoding="utf-8"))
    jira = r.get("JIRA_KEY", "none") not in ("", "none")
    lines, _ = check(catalog, jira, new, mode=r.get("RUN_MODE") or "full", session_harness=session_harness)
    # The session's own line is the capsule communicator's to act on (roles.py check); here only the roles count.
    bad = [l for l in lines if l.split()[0] in ("UNKNOWN", "MISSING") and l.split()[1] in ROLES]
    if bad:
        events.extend(bad)
        raise Done(REJECTED, f"REJECTED {len(bad)} problem(s); the run keeps its picks")
    old = {x: r.need(f"ROLE_{x.upper()}") for x in ROLES}
    changed = [x for x in ROLES if old[x] != new[x]]
    if not changed:
        raise Done(0, "UNCHANGED")
    crew = parse_pair(new["crew"])
    kv = {f"ROLE_{x.upper()}": new[x] for x in changed}
    kv["CREW_TOP"] = top_effort(catalog, crew.harness, crew.model)
    if "director" in changed:
        kv.update(DIRECTOR_CONVERSATION_ID=None, OPEN_RELAY=None, OPEN_USAGE_PROMPT=None, LAST_DIRECTOR_OP=None,
                  LAST_DIRECTOR_ARG=None, JOB_RETRIES=0, STEP3_PROBED=None)
        if not str(r.get("LAST_DIRECTOR_RESULT", "")).startswith("RESULT "):
            kv["LAST_DIRECTOR_RESULT"] = None
        preflight = str(r.get("DESIGN_NEED", "")).startswith("preflight-check")
        if preflight and r.get("DESIGN_PHASE") in ("probe", "dispatch", "dispatching"):
            kv["DESIGN_PHASE"] = "start"
    if "director" in changed or "safety" in changed:
        kv["DESIGN_ASK_CONV"] = None
    if r.get("FIN_ASK_KIND"):
        want = new["safety"] if r.get("FIN_ASK_KIND") == "drift" else new["director"]
        if want != r.get("FIN_ASK_PAIR"):
            kv.update(FIN_ASK_PAIR=want, FIN_ASK_CONV=None)
    fin_phase = str(r.get("FIN_PHASE", ""))
    if fin_phase.endswith("usage-limit") or fin_phase == "probe":
        kv["SWITCH_FINISH"] = "yes"
    queue = r.run_dir / "queue.txt"
    if r.get("PHASE") in ("execution", "finish") and queue.is_file() and queue.read_text(encoding="utf-8").split():
        kv["SWITCH_TASKS"] = "yes"
    r.set(**kv)
    what = "; ".join(f"{x} {old[x]} -> {new[x]}" for x in changed)
    r.log(f"picks switched: {what}")
    remember(r, new)
    raise Done(0, f"SWITCHED {what}")


def remember(r, new):
    """Write the new picks to the picks file the run was set up from; a failure there never fails the switch."""
    path = r.get("ROLES_FILE")
    if not path:
        return
    try:
        save_selection(path, new)
    except (OSError, ValueError) as e:
        r.log(f"picks file {path} not updated: {e}")


def lock_free(lock):
    """Whether no call holds the run's driver lock (common.drive)."""
    if not lock.exists():
        return True
    with lock.open("a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fh, fcntl.LOCK_UN)
    return True


def pause(state_path, wait=PAUSE_WAIT):
    run_dir = Path(state_path).resolve().parent
    lock = run_dir / ".driver.lock"
    if lock_free(lock):
        return 0, "IDLE"
    try:
        pid = int(json.loads((run_dir / ".call").read_text(encoding="utf-8")).get("pid"))
    except (OSError, ValueError, TypeError):
        return ERROR, "ERROR NO_CALL_RECORD a script holds the run, but .call does not name it"
    # Only a mission-control script is signalled, so a reused pid is never stopped.
    if not any(name in _pid_command(pid) for name in SCRIPTS):
        return ERROR, f"ERROR NOT_A_RUN_SCRIPT pid={pid}"
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as e:
        return ERROR, f"ERROR SIGNAL pid={pid} {e}"
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if lock_free(lock):
            if Path(state_path).is_file():
                Run(state_path).log(f"paused by the user: the running call (pid {pid}) was stopped")
            return 0, f"STOPPED {pid}"
        time.sleep(0.2)
    return ERROR, f"ERROR STILL_RUNNING {pid}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="switch.py")
    ap.add_argument("--state", required=True)
    sub = ap.add_subparsers(dest="op", required=True)
    a = sub.add_parser("apply")
    for role in ROLES:
        a.add_argument(f"--{role}", required=True)
    a.add_argument("--session-harness")
    sub.add_parser("pause")
    try:
        ns = ap.parse_args(argv)
    except SystemExit as e:
        return 64 if e.code else 0
    if ns.op == "pause":
        code, line = pause(ns.state)
        print(line)
        return code
    return drive(ns.state, lambda events: apply(Run(ns.state), {x: getattr(ns, x) for x in ROLES}, events,
                                                ns.session_harness))


if __name__ == "__main__":
    sys.exit(main())
