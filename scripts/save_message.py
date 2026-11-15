"""Save an Orca message's body to a file exactly as sent, so the coordinator never
retypes a worker's settle message, question, or failure output.

Usage:
  python3 -I save_message.py --orca <ORCA> --run <RUN_ID> --out <file>
      (--dispatch <dispatch_id> --type <worker_done|question|escalation> | --id <message_id>)
      [--header <first line>] [--append]

--out may contain {n}: it becomes the first number with no existing file. Without --append, an
existing --out is never overwritten: it prints EXISTS <path> and exits 3, so an earlier settle survives.
--dispatch picks the newest message of --type sent by that dispatch (matched on the
payload's dispatchId). --header writes one line before the body. --append adds to the
file instead of replacing it.
Exit 0: saved; prints `SAVED <out> id=<message id> outcome=<outcome or ->`.
Exit 1: no such message (prints NOT_FOUND).  Exit 2: the orca command failed.  Exit 3: EXISTS.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def inbox(orca, run):
    cmd = [orca, "orchestration", "inbox", "--terminal", f"run:{run}",
           "--limit", "500", "--full", "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ORCA_FAILED exit={r.returncode}\n{r.stderr.strip()}")
        sys.exit(2)
    data = json.loads(r.stdout)
    if not data.get("ok"):
        print(f"ORCA_FAILED {r.stdout.strip()[:500]}")
        sys.exit(2)
    return data["result"]["messages"]


def payload(m):
    """`inbox --full` gives the payload as a JSON string; the concise view gives an object."""
    p = m.get("payload") or {}
    if isinstance(p, dict):
        return p
    try:
        return json.loads(p)
    except (json.JSONDecodeError, TypeError):
        return {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--orca", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--dispatch")
    p.add_argument("--type", choices=["worker_done", "question", "escalation"])
    p.add_argument("--id")
    p.add_argument("--header")
    p.add_argument("--append", action="store_true")
    a = p.parse_args()
    if bool(a.id) == bool(a.dispatch) or (a.dispatch and not a.type):
        print("give --id, or --dispatch with --type", file=sys.stderr)
        return 64

    out = Path(a.out)
    if "{n}" in out.name:
        n = 1
        while (out.parent / out.name.format(n=n)).exists():
            n += 1
        out = out.parent / out.name.format(n=n)
    if out.exists() and not a.append:
        print(f"EXISTS {out}")
        return 3

    messages = sorted(inbox(a.orca, a.run), key=lambda m: m.get("sequence", 0), reverse=True)
    if a.id:
        found = next((m for m in messages if m["id"] == a.id), None)
    else:
        found = next((m for m in messages
                      if m["type"] == a.type and payload(m).get("dispatchId") == a.dispatch), None)
    if found is None:
        print("NOT_FOUND")
        return 1

    text = (a.header + "\n" if a.header else "") + found["body"].rstrip("\n") + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a" if a.append else "w", encoding="utf-8") as f:
        f.write(text)
    print(f"SAVED {out} id={found['id']} outcome={payload(found).get('outcome', '-')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
