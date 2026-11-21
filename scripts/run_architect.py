"""Run the architect via headless `agy -p` and manage design-phase `--conversation` turns.

Usage:
  python3 -I run_architect.py start --state <S> --spec <file> --pair <pair> [--keep-open]
  python3 -I run_architect.py send --state <S> --spec <file> --pair <pair>
  python3 -I run_architect.py reply --state <S> --file <from-user.md> --pair <pair>
  python3 -I run_architect.py notify-ack --state <S> --pair <pair>

All commands run `agy -p` in DOCS_WORKTREE, save the verbatim response to
<RUN_DIR>/results/architect-{n}.md (never overwriting an earlier file), and branch on line 1:
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
from common import ERROR, AgyRunner, Done, Run, first_words, next_free, save_body  # noqa: E402

RC_RESULT, RC_FAILED, RC_ERROR, RC_RELAY, RC_NOTIFY, RC_LOST, RC_USAGE = 0, 1, 2, 11, 12, 13, 31


def _handle_response(r, result, keep_open):
    if result is None:
        r.set(ARCHITECT_CONVERSATION_ID=None)
        return RC_LOST, "LOST architect conversation could not be resumed"
    kind, detail, body, conv_id = result
    if kind == "usage":
        return RC_USAGE, f"USAGE_LIMIT {detail}"
    saved = next_free(r.run_dir / "results", "architect-{n}.md")
    if kind == "stopped":
        save_body(detail, saved)
        if not keep_open:
            r.set(ARCHITECT_CONVERSATION_ID=None)
        return RC_FAILED, f"FAILED {saved}"
    if kind == "done" and detail == "failed":
        save_body(body, saved)
        if not keep_open:
            r.set(ARCHITECT_CONVERSATION_ID=None)
        return RC_FAILED, f"FAILED {saved}"

    save_body(body, saved)
    words = first_words(saved)
    if len(words) >= 3 and words[:2] == ["RESULT:", "RELAY"]:
        if not Path(words[2]).is_file():
            save_body(f"\nERROR: RELAY target file does not exist: {words[2]}", saved, append=True)
            if not keep_open:
                r.set(ARCHITECT_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        if not conv_id:
            save_body("\nERROR: RELAY requires a conversation_id to receive the user's reply", saved, append=True)
            if not keep_open:
                r.set(ARCHITECT_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        r.set(ARCHITECT_CONVERSATION_ID=conv_id)
        return RC_RELAY, f"RELAY {words[2]} {saved}"
    if len(words) >= 3 and words[:2] == ["RESULT:", "NOTIFY"]:
        if not Path(words[2]).is_file():
            save_body(f"\nERROR: NOTIFY target file does not exist: {words[2]}", saved, append=True)
            if not keep_open:
                r.set(ARCHITECT_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        if not conv_id:
            save_body("\nERROR: NOTIFY requires a conversation_id to continue after SHOWN.", saved, append=True)
            if not keep_open:
                r.set(ARCHITECT_CONVERSATION_ID=None)
            return RC_FAILED, f"FAILED {saved}"
        r.set(ARCHITECT_CONVERSATION_ID=conv_id)
        return RC_NOTIFY, f"NOTIFY {words[2]} {saved}"
    if len(words) >= 2 and words[0] == "RESULT:":
        if keep_open and conv_id:
            r.set(ARCHITECT_CONVERSATION_ID=conv_id)
        elif not keep_open:
            r.set(ARCHITECT_CONVERSATION_ID=None)
        return RC_RESULT, f"RESULT {saved}"
    if not keep_open:
        r.set(ARCHITECT_CONVERSATION_ID=None)
    return RC_FAILED, f"FAILED {saved}"


def run_cmd(ns):
    r = Run(ns.state)
    r.require_new_format()
    runner = AgyRunner(r)
    wt = r.need("DOCS_WORKTREE")
    if ns.op == "start":
        keep = bool(ns.keep_open)
        r.set(ARCHITECT_KEEP_OPEN="yes" if keep else "no")
        res = runner.run(ns.spec, pair=ns.pair, worktree=wt)
        return _handle_response(r, res, keep)

    conv_id = r.get("ARCHITECT_CONVERSATION_ID")
    keep = r.get("ARCHITECT_KEEP_OPEN") == "yes"
    if not conv_id:
        return RC_LOST, "LOST no ARCHITECT_CONVERSATION_ID in state"

    if ns.op == "send":
        res = runner.try_resume(ns.spec, ns.pair, wt, conv_id)
        return _handle_response(r, res, keep)
    if ns.op == "reply":
        p = Path(ns.file)
        if not p.is_file():
            return RC_ERROR, f"ERROR MISSING_FILE {p}"
        prompt = "User reply (verbatim):\n\n" + p.read_text(encoding="utf-8")
        res = runner.try_resume(prompt, ns.pair, wt, conv_id)
        return _handle_response(r, res, keep)
    if ns.op == "notify-ack":
        prompt = "SHOWN. Continue your current job and end with a RESULT: line."
        res = runner.try_resume(prompt, ns.pair, wt, conv_id)
        return _handle_response(r, res, keep)
    return 64, "bad usage"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="run_architect.py")
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
    try:
        code, line = run_cmd(ns)
    except Done as d:
        code, line = d.code, d.line
    except (OSError, ValueError) as e:
        code, line = RC_ERROR, f"ERROR {e}"
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
