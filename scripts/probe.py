"""Test-launch pairs synchronously with `agy -p`: find which launch style each pair needs.

Each pending pair is tried in DOCS_WORKTREE with its harness's launch styles in order (`slug`,
then `flag`); the first that succeeds is recorded as LAUNCH_STYLE_<key>. Failures are appended to
<RUN_DIR>/results/probe-<key>.md. After each attempt the docs worktree is checked against PROBE_HEAD;
any change is discarded, except in the resume set (keep=True): an architect may be working there,
so a change is only logged and left as is.

Usage:
  python3 -I probe.py --state <RUN_DIR>/state.json advance --set step1|step3|resume
  python3 -I probe.py --state <file> advance --set pair --pair <harness:model@effort>
  python3 -I probe.py --state <file> advance            # continue the stored PROBE_SET
  python3 -I probe.py --state <file> advance --docs-moved

Sets: step1 = ROLE_ARCHITECT, ROLE_EGM, ROLE_BASE and the base model at BASE_TOP;
step3 = every REVIEW_1/REVIEW_2 in the queue's .meta files plus CLERICAL_PAIR; pair = one pair;
resume = step1 and step3 together, re-probed even when they hold LAUNCH_STYLE_<key>.

The last line of stdout is the status.
Exit 0   PROBED                            every pair in the set has a working launch style
Exit 40  PROBE_FAILED <pair> <file>        every style failed; <file> holds each style's failure text
Exit 21  FIX_META <file>                   step3 only: an architect-chosen pair failed; <file> holds
                                           validate_meta.py-style PROBLEM lines for the fix-meta job
Exit 2   ERROR <what>                      bad state or missing catalog (OLD_FORMAT_RUN for an old state file)
Exit 64  bad usage
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import FIX_META, STOP, ERROR, AgyRunner, Done, Run, build, git, next_free, save_body, tool  # noqa: E402
from harnesses import launch_args  # noqa: E402
from pairs import Pair, parse_pair, pair_key  # noqa: E402

PROBED = 0


class Prober:
    def __init__(self, run, runner, keep=False):
        self.r, self.runner, self.keep = run, runner, keep
        self.catalog = json.loads(Path(run.need("CATALOG")).read_text(encoding="utf-8"))
        self.wt = run.need("DOCS_WORKTREE")

    def styles(self, pair):
        h = self.catalog["harnesses"].get(pair.harness)
        if h is None:
            raise Done(ERROR, f"ERROR UNKNOWN_HARNESS {pair.harness}")
        return h["styles"]

    def results_file(self, key):
        return self.r.run_dir / "results" / f"probe-{key}.md"

    def advance(self, pairs):
        pairs = list(dict.fromkeys(p if isinstance(p, Pair) else parse_pair(p) for p in pairs))
        pending = [p for p in pairs if not self.r.get(f"LAUNCH_STYLE_{pair_key(p)}")
                   or self.r.get(f"PROBE_RECHECK_{pair_key(p)}")]
        if not pending:
            return
        self.r.set(PROBE_HEAD=git(self.wt, "rev-parse", "HEAD"))
        for p in pending:
            key = pair_key(p)
            if not self.probe_one(p, key):
                raise Done(STOP, f"PROBE_FAILED {p} {self.results_file(key)}")

    def probe_one(self, pair, key):
        styles = self.styles(pair)
        out = self.results_file(key)
        if out.exists():
            out.unlink()
        for idx, style in enumerate(styles):
            spec = build("probe.txt", self.r.run_dir / "specs" / f"probe-{key}-{idx}.txt")
            args = launch_args(self.catalog, pair, style)
            result = self.runner.run(spec, args=args, worktree=self.wt)
            self.finish_attempt(pair)
            if result[0] == "done" and result[1] == "succeeded":
                self.r.set(**{f"LAUNCH_STYLE_{key}": style, f"PROBE_RECHECK_{key}": None})
                self.r.log(f"probe {pair}: launch style {style} works")
                return True
            header = f"## Launch style {style} for {pair} failed"
            body = result[2] if result[0] == "done" else result[1]
            if result[0] == "done":
                save_body(body, out, header=f"{header} (outcome {result[1]})", append=True)
            else:
                save_body(body or result[0], out, header=header, append=True)
            self.r.log(f"probe {pair}: launch style {style} failed")
        return False

    def finish_attempt(self, pair):
        """Check the docs worktree after a probe attempt and restore if changed (keep: log only)."""
        head = self.r.need("PROBE_HEAD")
        rc, out = tool("check-tree.sh", self.wt, head)
        if rc != 0 and self.keep:
            note = f"probe {pair}: docs worktree differs from {head} ({out}); left as is"
            self.r.log(note)
            with self.results_file(pair_key(pair)).open("a", encoding="utf-8") as f:
                f.write(f"## {note}\n")
        elif rc != 0:
            rc2, out2 = tool("discard-changes.sh", self.wt, head)
            self.r.log(f"probe {pair}: docs worktree changed ({out}); discarded: {out2} (exit {rc2})")


def read_meta(path):
    meta = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            meta[k.strip()] = v.strip()
    return meta


def review_pairs(r, ids):
    """The REVIEW_1/REVIEW_2 pairs of the given tasks' .meta files."""
    found = []
    for t in ids:
        meta = read_meta(r.task_file(t, ".meta"))
        found += [meta[k] for k in ("REVIEW_1", "REVIEW_2") if meta.get(k)]
    return found


def launch_problems(r, ids, failed_line):
    """validate_meta.py-style PROBLEM lines for the tasks whose reviewer pair failed its test launch."""
    _, pair, results = failed_line.split()
    lines = [f"PROBLEM {t}: {k} {pair} failed its test launch; see {results}"
             for t in ids for k, v in read_meta(r.task_file(t, ".meta")).items()
             if k in ("REVIEW_1", "REVIEW_2") and v == pair]
    return "\n".join(lines or [f"PROBLEM {pair} failed its test launch; see {results}"]) + "\n"


def docs_moved(r):
    """--docs-moved: an architect job committed in DOCS_WORKTREE. Move PROBE_HEAD/DOCS_HEAD to current HEAD."""
    head = git(r.need("DOCS_WORKTREE"), "rev-parse", "HEAD")
    moved = [k for k in ("PROBE_HEAD", "DOCS_HEAD") if r.get(k) and r.get(k) != head]
    if moved:
        r.set(**{k: head for k in moved})
        r.log(f"docs worktree moved by an architect job: {' '.join(moved)} now {head}")


def set_pairs(r, name, one=None):
    if name == "pair":
        if not one:
            raise Done(ERROR, "ERROR MISSING_ARG --pair")
        return [parse_pair(one)]
    if name == "step1":
        base = parse_pair(r.need("ROLE_BASE"))
        return [parse_pair(r.need("ROLE_ARCHITECT")), parse_pair(r.need("ROLE_EGM")), base,
                Pair(base.harness, base.model, r.need("BASE_TOP"))]
    if name == "resume":
        return list(dict.fromkeys(set_pairs(r, "step1") + set_pairs(r, "step3")))
    if name == "step3":
        queue = r.run_dir / "queue.txt"
        found = review_pairs(r, queue.read_text(encoding="utf-8").split() if queue.exists() else [])
        if r.get("CLERICAL_PAIR"):
            found.append(r.get("CLERICAL_PAIR"))
        return [parse_pair(x) for x in found]
    raise Done(ERROR, f"ERROR UNKNOWN_SET {name}")


def run_advance(r, set_name, one, moved=False):
    r.require_new_format()
    runner = AgyRunner(r)
    if moved:
        docs_moved(r)
    if set_name and (set_name != r.get("PROBE_SET") or (set_name == "pair" and one != r.get("PROBE_PAIR"))):
        r.set(PROBE_SET=set_name, PROBE_PAIR=one)
    if set_name == "resume" and not any(k.startswith("PROBE_RECHECK_") for k in r.s):
        r.set(**{f"PROBE_RECHECK_{pair_key(p)}": "yes" for p in set_pairs(r, "resume")})
    set_name, one = r.get("PROBE_SET"), r.get("PROBE_PAIR")
    if not set_name:
        raise Done(ERROR, "ERROR NO_PROBE_SET pass --set step1|step3|resume|pair")
    try:
        Prober(r, runner, keep=set_name == "resume").advance(set_pairs(r, set_name, one))
    except Done as d:
        if set_name == "step3" and d.code == STOP and d.line.startswith("PROBE_FAILED"):
            raise Done(FIX_META, f"FIX_META {step3_problems(r, d.line)}")
        raise
    raise Done(PROBED, "PROBED")


def step3_problems(r, failed_line):
    """Step 3's pairs are the architect's choices (reviewers, CLERICAL_PAIR), so a failed launch goes back
    to it as fix-meta input, in validate_meta.py's PROBLEM form, instead of stopping the run."""
    _, pair, results = failed_line.split()
    queue = r.run_dir / "queue.txt"
    ids = queue.read_text(encoding="utf-8").split() if queue.exists() else []
    lines = [l for l in launch_problems(r, ids, failed_line).splitlines() if l.startswith("PROBLEM ") and ":" in l.split()[1]]
    if r.get("CLERICAL_PAIR") == pair:
        lines.append(f"PROBLEM pair '{pair}': CLERICAL_PAIR failed its test launch; see {results}")
    out = next_free(r.run_dir, "validate-{n}.txt")
    out.write_text("\n".join(lines or [f"PROBLEM pair '{pair}': failed its test launch; see {results}"]) + "\n",
                   encoding="utf-8")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    sub = p.add_subparsers(dest="op", required=True)
    adv = sub.add_parser("advance")
    adv.add_argument("--set", choices=["step1", "step3", "resume", "pair"])
    adv.add_argument("--pair")
    adv.add_argument("--docs-moved", action="store_true")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    try:
        run_advance(Run(a.state), a.set, a.pair, a.docs_moved)
        code, line = ERROR, "ERROR NO_STATUS"
    except Done as d:
        code, line = d.code, d.line
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
