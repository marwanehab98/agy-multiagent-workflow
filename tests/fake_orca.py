"""A fake `orca` CLI for testing run_task.py without real workers.

The world lives in the JSON file named by $FAKE_ORCA_WORLD. Each `worker-start` takes the
next entry of world["behaviors"] and acts it out at once in the worker's worktree, then
queues the message a real worker would send. Receipts and message shapes copy what real
Orca 2026-11 printed (see references/orca-facts.md).

Behaviors ({"do": ...}):
  commit [file content]  commit a file, settle succeeded     commit-broken  commit BROKEN, settle succeeded
  unbreak                remove BROKEN, settle succeeded     dirty          leave an uncommitted file, settle succeeded
  commit-all             commit everything, settle succeeded nocommit       settle succeeded, commit nothing
  fail                   settle failed                       pass / fix     review file with a verdict, prose settle
  pass-body-only         no review file; VERDICT in the settle message     noverdict      neither
  edit-and-pass          edit a tracked file, verdict PASS   usage          usage-limit escalation, no settle
  stopped                lost escalation, no settle          pending        still working, nothing sent
  idle-then-commit       ends its turn unsettled; a `send` makes it commit and settle
  ok                     settle succeeded with OK           ok-edit        edit a tracked file, then settle OK
  idle-forever           ends its turn unsettled every time, even after a `send`
  write-output           write the output file named in the spec ("Write ... to <path>"; a relative path is
                         resolved in the worktree), settle succeeded. {"edit": true} also edits a tracked file
  write-empty            same, but the file is empty

World key "launch_rules" (optional): a list of {"agent", "model", "effort"} (effort may be null = no --effort).
When present, a worker-start matching no rule consumes no behavior and fails like a model the account lacks.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

WORLD = Path(os.environ["FAKE_ORCA_WORLD"])


def load():
    return json.loads(WORLD.read_text())


def save(w):
    WORLD.write_text(json.dumps(w, indent=1))


def out(result):
    print(json.dumps({"ok": True, "result": result}))
    return 0


def fail(code, msg):
    print(json.dumps({"ok": False, "error": {"code": code, "message": msg}}))
    return 1


def opt(args, name):
    return args[args.index(name) + 1] if name in args else None


def git(wt, *a):
    subprocess.run(["git", "-C", wt, "-c", "user.email=w@w", "-c", "user.name=w", *a],
                   check=True, capture_output=True)


def send(w, dispatch, task, typ, body, extra=None):
    w["seq"] += 1
    p = {"taskId": task, "dispatchId": dispatch, **(extra or {})}
    w["messages"].append({"id": f"msg_{w['seq']}", "type": typ, "subject": body.splitlines()[0][:60],
                          "body": body, "payload": json.dumps(p), "sequence": w["seq"]})


def review_file(w, d):
    m = re.search(r"Write your review to (\S+?)[,:](?:\s|$)", w["tasks"][d["taskId"]]["spec"])
    if not m:
        raise SystemExit("reviewer spec has no review file")
    return m.group(1)


def output_file(w, d, wt):
    m = re.search(r"Write\b[^\n]*? to (\S+?)[,.:](?:\s|$)", w["tasks"][d["taskId"]]["spec"])
    if not m:
        raise SystemExit("spec has no output file")
    path = Path(wt, m.group(1))  # an absolute path wins
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def act(w, d, wt, b):
    task, do = d["taskId"], b["do"]
    done = lambda outcome, body: (send(w, d["id"], task, "worker_done", body, {"outcome": outcome}),
                                  d.update(state=outcome, stage="settled", liveness="live"))
    if do == "commit":
        f = Path(wt, b.get("file", f"f{w['seq']}.txt"))
        f.write_text(b.get("content", "x\n"))
        git(wt, "add", f.name)
        git(wt, "commit", "-qm", f"add {f.name}")
        done("succeeded", f"committed {f.name}")
    elif do == "commit-broken":
        Path(wt, "BROKEN").write_text("x\n")
        git(wt, "add", "BROKEN")
        git(wt, "commit", "-qm", "broken")
        done("succeeded", "committed BROKEN")
    elif do == "unbreak":
        git(wt, "rm", "-q", "BROKEN")
        git(wt, "commit", "-qm", "unbreak")
        done("succeeded", "removed BROKEN")
    elif do == "dirty":
        Path(wt, "uncommitted.txt").write_text("x\n")
        done("succeeded", "left a file uncommitted")
    elif do == "commit-all":
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", "commit leftovers")
        done("succeeded", "committed leftovers")
    elif do == "nocommit":
        done("succeeded", "did nothing")
    elif do == "fail":
        done("failed", "could not make the test pass\nAssertionError: 1 != 2")
    elif do in ("pass", "fix"):
        # Like real reviewers: the review file ends in VERDICT, the settle message is prose without it.
        verdict = "VERDICT: PASS" if do == "pass" else "VERDICT: FIX"
        Path(review_file(w, d)).write_text(f"Critical: none.\nImportant: {'x.py:1 wrong' if do == 'fix' else 'none'}.\n{verdict}\n")
        done("succeeded", "Reviewed the range and wrote the review file. Nothing is left.")
    elif do == "pass-body-only":
        done("succeeded", "Read the diff; nothing is missing. VERDICT: PASS")
    elif do == "noverdict":
        done("succeeded", "looks fine to me")
    elif do == "edit-and-pass":
        tracked = subprocess.run(["git", "-C", wt, "ls-files"], capture_output=True, text=True).stdout.split()[0]
        Path(wt, tracked).write_text("reformatted\n")
        Path(review_file(w, d)).write_text("Critical: none.\nVERDICT: PASS\n")
        done("succeeded", "Reviewed and reformatted a file.")
    elif do in ("write-output", "write-empty"):
        path = output_file(w, d, wt)
        path.write_text("" if do == "write-empty" else "Critical: none.\nImportant: x.md:1 unclear.\n")
        if b.get("edit"):
            tracked = subprocess.run(["git", "-C", wt, "ls-files"], capture_output=True, text=True).stdout.split()[0]
            Path(wt, tracked).write_text("edited by a design worker\n")
        done("succeeded", f"written {path}")
    elif do == "ok":
        done("succeeded", "OK")
    elif do == "ok-edit":
        tracked = subprocess.run(["git", "-C", wt, "ls-files"], capture_output=True, text=True).stdout.split()[0]
        Path(wt, tracked).write_text("changed by a probe\n")
        Path(wt, "probe-stray.txt").write_text("x\n")
        done("succeeded", "OK")
    elif do == "usage":
        send(w, d["id"], task, "escalation", "Agent hit an error: Claude hit its session limit.",
             {"turnOutcome": "failure", "failureKind": "usageLimitReached",
              "detail": "You've hit your 5-hour limit"})
    elif do == "stopped":
        send(w, d["id"], task, "escalation", "Worker lost without reporting", {"stopReason": "lost"})
        d.update(liveness="exited")
    elif do in ("idle-then-commit", "idle-forever"):
        send(w, d["id"], task, "escalation", "Worker stopped without reporting", {"stopReason": "finished"})
        d["onSend"] = "commit" if do == "idle-then-commit" else "idle-forever"
    elif do == "pending":
        pass
    else:
        raise SystemExit(f"unknown behavior {do}")


def main(argv):
    if argv[:1] != ["orchestration"]:
        return fail("bad", "fake handles only orchestration")
    cmd, args = argv[1], argv[2:]
    w = load()
    w["calls"].append([cmd] + [a for a in args if a != "--json"])
    try:
        if cmd == "task-create":
            tid = f"task_{len(w['tasks']) + 1}"
            w["tasks"][tid] = {"spec": Path(opt(args, "--spec-file")).read_text(), "title": opt(args, "--task-title")}
            return out({"taskId": tid, "status": "ready"})
        if cmd == "worker-start":
            wt = opt(args, "--worktree").split(":", 1)[1]
            term = opt(args, "--terminal")
            if term:
                owner = next((d for d in reversed(list(w["dispatches"].values())) if d["terminal"] == term), None)
                if not owner or owner["terminalState"] != "retained":
                    return fail("terminal_not_reusable", "terminal is released")
                owner["terminalState"] = None
                agent, model, keep = owner["agent"], owner["model"], True
            else:
                agent, model, keep = opt(args, "--agent"), opt(args, "--model"), "--keep-open" in args
                term = f"structworker_{len(w['dispatches']) + 1}"
            did = f"ctx_{len(w['dispatches']) + 1}"
            d = {"id": did, "taskId": opt(args, "--task"), "agent": agent, "model": model, "terminal": term,
                 "keepOpen": keep, "reused": bool(opt(args, "--terminal")), "state": "ready",
                 "stage": "input_accepted", "liveness": "live",
                 "terminalState": "retained" if keep else "reclaimable"}
            d["worktree"] = wt
            w["dispatches"][did] = d
            d["effort"] = opt(args, "--effort")
            rules = w.get("launch_rules")
            if rules is not None and not any((r["agent"], r["model"], r.get("effort")) == (agent, model, d["effort"])
                                            for r in rules):
                send(w, did, d["taskId"], "escalation",
                     f"Model '{model}' is not available for the current authentication method", {"stopReason": "lost"})
                d.update(liveness="exited")
                return out({"dispatchId": did, "taskId": d["taskId"], "runId": "run_fake", "terminal": term,
                            "keptOpen": keep})
            if not w["behaviors"]:
                raise SystemExit(f"no behavior left for worker-start {args}")
            act(w, d, wt, w["behaviors"].pop(0))
            return out({"dispatchId": did, "taskId": d["taskId"], "runId": "run_fake", "terminal": term,
                        "keptOpen": keep})
        if cmd == "send":
            d = w["dispatches"].get(opt(args, "--to").split(":", 1)[1])
            if not d:
                return fail("not_found", "no such dispatch")
            wt = d.get("worktree")
            then = d.pop("onSend", None)
            if then == "commit":
                act(w, d, wt, {"do": "commit"})
            elif then == "idle-forever":
                act(w, d, wt, {"do": "idle-forever"})
            return out({"delivery": "queued", "messageId": f"msg_s{w['seq']}"})
        if cmd == "worker-show":
            d = w["dispatches"].get(opt(args, "--dispatch"))
            if not d:
                return fail("not_found", "no such dispatch")
            return out({"dispatchId": d["id"], "state": d["state"], "stage": d["stage"],
                        "liveness": d["liveness"], "terminalState": d["terminalState"]})
        if cmd == "worker-release":
            d = w["dispatches"].get(opt(args, "--dispatch"))
            if d:
                d["terminalState"] = "released"
            return out({"dispatchId": opt(args, "--dispatch"), "state": "released"})
        if cmd == "inbox":
            return out({"messages": sorted(w["messages"], key=lambda m: -m["sequence"]),
                        "count": len(w["messages"])})
        return fail("unknown_command", cmd)
    finally:
        save(w)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
