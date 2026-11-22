"""Fill a worker-spec template so the capsule communicator never writes spec text by hand.

Usage:
  python3 -I build_spec.py --template <file> --out <file> [--set NAME=value]... [--file NAME=path]...

In the template, {{NAME}} is replaced by a --set value and {{FILE:NAME}} by the full
contents of the --file path. The script fails, writing nothing, if any placeholder is
left unfilled, a --file path is missing, or a name is given but never used.
Exit 0: spec written (prints its path).  Exit 1: problem (prints what).
"""
import argparse
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{(FILE:)?([A-Z0-9_]+)\}\}")


def pair(text):
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"expected NAME=value, got {text!r}")
    name, value = text.split("=", 1)
    if not re.fullmatch(r"[A-Z0-9_]+", name):
        raise argparse.ArgumentTypeError(f"bad name {name!r}: use CAPITALS, digits, _")
    return name, value


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--template", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--set", type=pair, action="append", default=[])
    p.add_argument("--file", type=pair, action="append", default=[])
    a = p.parse_args()

    template = Path(a.template).read_text(encoding="utf-8")
    values = dict(a.set)
    files = {}
    for name, path in a.file:
        f = Path(path)
        if not f.is_file():
            print(f"MISSING_FILE {name}={path}")
            return 1
        files[name] = f.read_text(encoding="utf-8").rstrip("\n")

    used, problems = set(), []

    def fill(m):
        is_file, name = m.group(1), m.group(2)
        source = files if is_file else values
        if name not in source:
            problems.append(f"UNFILLED {m.group(0)}")
            return m.group(0)
        used.add(("f" if is_file else "s", name))
        return source[name]

    result = PLACEHOLDER.sub(fill, template)
    for name in values:
        if ("s", name) not in used:
            problems.append(f"UNUSED --set {name}")
    for name in files:
        if ("f", name) not in used:
            problems.append(f"UNUSED --file {name}")
    if problems:
        print("\n".join(problems))
        return 1
    Path(a.out).write_text(result + "\n", encoding="utf-8")
    print(f"WROTE {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
