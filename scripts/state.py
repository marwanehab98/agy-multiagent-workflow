"""The capsule communicator's run state, kept in one JSON file so it survives context compaction
and a capsule communicator restart. Every recorded value and every counter lives here, never
only in the capsule communicator's context.

Usage:
  python3 -I state.py --file <RUN_DIR>/state.json set KEY=value [KEY=value]...
  python3 -I state.py --file <file> get KEY            # prints the value
  python3 -I state.py --file <file> incr KEY           # adds 1 (missing counts as 0), prints the new value
  python3 -I state.py --file <file> unset KEY [KEY]...
  python3 -I state.py --file <file> show               # prints every KEY=value, sorted

Exit 0: done.  Exit 1: `get` of a missing key (prints MISSING KEY).  Exit 64: bad usage.
Writes are atomic (temp file + rename), so a crash never leaves a half-written file.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

KEY = re.compile(r"[A-Za-z0-9_]+")


def load(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def key(text):
    if not KEY.fullmatch(text):
        raise SystemExit(f"bad key {text!r}: use letters, digits, _")
    return text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("op", choices=["set", "get", "incr", "unset", "show"])
    p.add_argument("args", nargs="*")
    a = p.parse_args()
    path = Path(a.file)
    data = load(path)

    if a.op == "set":
        if not a.args:
            print("set needs KEY=value", file=sys.stderr)
            return 64
        for item in a.args:
            if "=" not in item:
                print(f"expected KEY=value, got {item!r}", file=sys.stderr)
                return 64
            k, v = item.split("=", 1)
            data[key(k)] = v
        save(path, data)
        for item in a.args:
            print(f"SET {item.split('=', 1)[0]}")
    elif a.op == "get":
        if len(a.args) != 1:
            return 64
        k = key(a.args[0])
        if k not in data:
            print(f"MISSING {k}")
            return 1
        print(data[k])
    elif a.op == "incr":
        if len(a.args) != 1:
            return 64
        k = key(a.args[0])
        data[k] = str(int(data.get(k, "0")) + 1)
        save(path, data)
        print(data[k])
    elif a.op == "unset":
        for k in a.args:
            data.pop(key(k), None)
        save(path, data)
    else:
        for k in sorted(data):
            print(f"{k}={data[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
