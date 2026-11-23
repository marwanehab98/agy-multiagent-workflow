"""Run the flight director via headless CLI worker and manage design-phase session turns.

Usage:
  python3 -I run_director.py start --state <S> --spec <file> --pair <pair> [--keep-open]
  python3 -I run_director.py send --state <S> --spec <file> --pair <pair>
  python3 -I run_director.py reply --state <S> --file <from-user.md> --pair <pair>
  python3 -I run_director.py notify-ack --state <S> --pair <pair>

All commands run in DOCS_WORKTREE, save the verbatim response to
<RUN_DIR>/results/director-{n}.md (never overwriting an earlier file), and branch on line 1:
  Exit 0   RESULT <saved_file>                Response started with RESULT: (not RELAY/NOTIFY)
  Exit 11  RELAY <relay_path> <saved_file>    Response started with RESULT: RELAY <path>
  Exit 12  NOTIFY <notify_path> <saved_file>  Response started with RESULT: NOTIFY <path>
  Exit 13  LOST <detail>                      Conversation could not be resumed (fallback needed)
  Exit 31  USAGE_LIMIT <detail>               Model hit a usage/quota limit
  Exit 1   FAILED <saved_file>                Worker failed or returned no RESULT: line
  Exit 2   ERROR <what>                       Bad state or missing file
  Exit 64  bad usage
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import AgyRunner, Run, drive, first_words, next_free, rest_path, save_body  # noqa: E402

def quote_path(p):
    """A path for a status line, in double quotes when it has whitespace, so the line stays unambiguous."""
    return f'"{p}"' if any(c.isspace() for c in p) else p


RC_RESULT, RC_FAILED, RC_ERROR, RC_RELAY, RC_NOTIFY, RC_LOST, RC_USAGE = 0, 1, 2, 11, 12, 13, 31


def _handle_response(r, result, keep_open):
    if result is None:
        r.set(DIRECTOR_CONVERSATION_ID=None, LAST_DIRECTOR_RESULT=None)
        return RC_LOST, "LOST flight director conversation could not be resumed"
    kind, detail, body, conv_id = result
    if kind == "usage":
        r.set(LAST_DIRECTOR_RESULT=None)
        return RC_USAGE, f"USAGE_LIMIT {detail}"
    saved = next_free(r.run_dir / "results", "director-{n}.md")
    if kind == "stopped":
        save_body(detail, saved)
        r.set(LAST_DIRECTOR_RESULT=None)
        if not keep_open:
            r.set(DIRECTOR_CONVERSATION_ID=None)
        return RC_FAILED, f"FAILED {saved}"
    if kind == "done" and detail == "failed":
        save_body(body, saved)
        r.set(LAST_DIRECTOR_RESULT=None)
        if not keep_open:
            r.set(DIRECTOR_CONVERSATION_ID=None)
        return RC_FAILED, f"FAILED {saved}"

    save_body(body, saved)
    words = first_words(saved)
    if len(words) >= 3 and words[:2] in (["RESULT:", "RELAY"], ["RESULT:", "NOTIFY"]):
        words = words[:2] + [rest_path(words, 2)]  # a path with spaces, unquoted
    if len(words) >= 3 and words[:2] == ["RESULT:", "RELAY"]:
        if not Path(words[2]).is_file():
            save_body(f"\nERROR: RELAY target file does not exist: {words[2]}", saved, append=True)
            r.set(LAST_DIRECTOR_RESULT=None)
            if not keep_open:
                r.set(DIRECTOR_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        if not conv_id:
            save_body("\nERROR: RELAY requires a conversation_id to receive the user's reply", saved, append=True)
            r.set(LAST_DIRECTOR_RESULT=None)
            if not keep_open:
                r.set(DIRECTOR_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        line = f"RELAY {quote_path(words[2])} {saved}"
        r.set(DIRECTOR_CONVERSATION_ID=conv_id, OPEN_RELAY=words[2], LAST_DIRECTOR_RESULT=line)
        return RC_RELAY, line
    if len(words) >= 3 and words[:2] == ["RESULT:", "NOTIFY"]:
        if not Path(words[2]).is_file():
            save_body(f"\nERROR: NOTIFY target file does not exist: {words[2]}", saved, append=True)
            r.set(LAST_DIRECTOR_RESULT=None)
            if not keep_open:
                r.set(DIRECTOR_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        if not conv_id:
            save_body("\nERROR: NOTIFY requires a conversation_id to continue after SHOWN.", saved, append=True)
            r.set(LAST_DIRECTOR_RESULT=None)
            if not keep_open:
                r.set(DIRECTOR_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        line = f"NOTIFY {quote_path(words[2])} {saved}"
        r.set(DIRECTOR_CONVERSATION_ID=conv_id, LAST_DIRECTOR_RESULT=line)
        return RC_NOTIFY, line
    if len(words) >= 2 and words[0] == "RESULT:":
        line = f"RESULT {saved}"
        updates = {"LAST_DIRECTOR_RESULT": line}
        if keep_open and conv_id:
            updates["DIRECTOR_CONVERSATION_ID"] = conv_id
        elif not keep_open:
            updates["DIRECTOR_CONVERSATION_ID"] = None
        r.set(**updates)
        return RC_RESULT, line
    r.set(LAST_DIRECTOR_RESULT=None)
    if not keep_open:
        r.set(DIRECTOR_CONVERSATION_ID=None)
    return RC_FAILED, f"FAILED {saved}"


def run_cmd(ns):
    r = Run(ns.state)
    r.require_new_format()
    runner = AgyRunner(r)
    wt = r.need("DOCS_WORKTREE")
    if ns.op in ("start", "send") and not Path(ns.spec).is_file():
        return RC_ERROR, f"ERROR MISSING_FILE {ns.spec}"
    # A director- tag names the worker log and puts the turn under Flight Director in the cost report.
    stem = Path(ns.spec).stem if ns.op in ("start", "send") else ns.op
    label = stem if stem.startswith("director-") else f"director-{stem}"
    # Only the flight director keeps the user's MCP servers (JIRA jobs); every other worker runs without them.
    if ns.op == "start":
        keep = bool(ns.keep_open)
        r.set(DIRECTOR_KEEP_OPEN="yes" if keep else "no", LAST_DIRECTOR_RESULT=None)
        res = runner.run(ns.spec, pair=ns.pair, worktree=wt, label=label, mcp=True)
        return _handle_response(r, res, keep)

    conv_id = r.get("DIRECTOR_CONVERSATION_ID")
    keep = r.get("DIRECTOR_KEEP_OPEN") == "yes"
    if not conv_id:
        r.set(LAST_DIRECTOR_RESULT=None)
        return RC_LOST, "LOST no DIRECTOR_CONVERSATION_ID in state"

    if ns.op == "send":
        r.set(LAST_DIRECTOR_RESULT=None)
        res = runner.try_resume(ns.spec, ns.pair, wt, conv_id, label=label, mcp=True)
        return _handle_response(r, res, keep)
    if ns.op == "reply":
        p = Path(ns.file)
        if not p.is_file():
            return RC_ERROR, f"ERROR MISSING_FILE {p}"
        r.set(LAST_DIRECTOR_RESULT=None)
        prompt = "User reply (verbatim):\n\n" + p.read_text(encoding="utf-8")
        res = runner.try_resume(prompt, ns.pair, wt, conv_id, label=label, mcp=True)
        return _handle_response(r, res, keep)
    if ns.op == "notify-ack":
        r.set(LAST_DIRECTOR_RESULT=None)
        prompt = "SHOWN. Continue your current job and end with a RESULT: line."
        res = runner.try_resume(prompt, ns.pair, wt, conv_id, label=label, mcp=True)
        return _handle_response(r, res, keep)
    return 64, "bad usage"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="run_director.py")
    sub = ap.add_subparsers(dest="op", required=True)
    for name in ("start", "send", "reply", "notify-ack"):
        p = sub.add_parser(name)
        p.add_argument("--state", required=True)
        p.add_argument("--pair", required=True)
        if name in ("start", "send"):
            p.add_argument("--spec", required=True)
        if name == "start":
            p.add_argument("--keep-open", action="store_true")
        if name == "reply":
            p.add_argument("--file", required=True)
    try:
        ns = ap.parse_args(argv)
    except SystemExit as e:
        return 64 if e.code else 0
    return drive(ns.state, lambda events: run_cmd(ns))


if __name__ == "__main__":
    sys.exit(main())
