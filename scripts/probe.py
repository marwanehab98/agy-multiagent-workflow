"""Test-launch pairs (design 2.5): find which launch style each pair needs, as a resumable state machine.

A probe worker starts in DOCS_WORKTREE with a spec that only asks it to settle OK. Each pair
is tried with its harness's launch styles in order; the first that settles succeeded is
recorded as LAUNCH_STYLE_<key>. Failures are appended to <RUN_DIR>/results/probe-<key>.md.
After each probe worker the docs worktree is checked against PROBE_HEAD; any change is discarded,
except in the resume set (ruling R11): an architect may be working there, so a change is only logged
(run-log line and the pair's results file) and left as is, and nothing is ever reset.
It never polls: a call starts every pending pair at once, then exits at the first wait.

Usage:
  python3 -I probe.py --state <RUN_DIR>/state.json advance --set step1|step3|resume
  python3 -I probe.py --state <file> advance --set pair --pair <harness:model@effort>
  python3 -I probe.py --state <file> advance            # continue the stored PROBE_SET
  python3 -I probe.py --state <file> advance --docs-moved   # continue, after an architect job committed
                                                             # in DOCS_WORKTREE while a probe ran (R13)

Sets: step1 = ROLE_ARCHITECT, ROLE_EGM, ROLE_BASE and the base model at BASE_TOP;
step3 = every REVIEW_1/REVIEW_2 in the queue's .meta files plus CLERICAL_PAIR; pair = one pair;
resume = step1 and step3 together, re-probed even when they hold LAUNCH_STYLE_<key> (a resumed run
proves its pairs again; a success overwrites the style).
Pairs that already hold LAUNCH_STYLE_<key> are skipped, except those marked PROBE_RECHECK_<key>.
`--set resume` marks every pair in the set, unless marks from an unfinished resume remain. A call
without --set continues PROBE_SET; a different --set starts that set.

The last line of stdout is the status.
Exit 0   PROBED                            every pair in the set has a working launch style
Exit 10  WAIT <dispatch> probe             end your turn; call advance again when woken
Exit 40  PROBE_FAILED <pair> <file>        every style failed; <file> holds each style's failure text
Exit 21  FIX_META <file>                   step3 only: an architect-chosen pair failed; <file> holds
                                           validate_meta.py-style PROBLEM lines for the fix-meta job
A worker-start that Orca refuses outright (invalid_argument) counts as that style failing.
Exit 2   ERROR <what>                      bad state or Orca failure (OLD_FORMAT_RUN for an old state file)
Exit 64  bad usage

State keys: PROBE_SET, PROBE_PAIR, PROBE_HEAD, PROBE_STYLE_<key> (index of the style being tried),
PROBE_DISPATCH_<key>, PROBE_RECHECK_<key> (resume: probe again), LAUNCH_STYLE_<key> (written on success). Needs DOCS_WORKTREE and CATALOG.
--docs-moved (ruling R13) first moves PROBE_HEAD (and run_design's DOCS_HEAD) to the docs worktree's
current HEAD, so the check after each probe never resets past an architect's commits; uncommitted
changes are still discarded. The coordinator passes it on the first call after it handles the RESULT of
an architect job (consult, user-message) that ran while this script waited. run_design.py, run_task.py
and run_finish.py take the same flag (docs_moved()).
Embeddable: Prober(run, orca[, keep=True]).advance(pairs) returns on PROBED and raises Done otherwise;
keep=True logs tree changes instead of discarding them. review_pairs() and launch_problems() serve the
REPLAN and FINAL_FIX probes in run_task.py and run_finish.py.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path
from common import FIX_META, WAIT, STOP, ERROR, Done, Orca, Run, build, git, next_free, poll, save_body, tool  # noqa: E402
from harnesses import launch_args  # noqa: E402
from pairs import Pair, parse_pair, pair_key  # noqa: E402

PROBED = 0
NUDGE = ("You ended your turn without settling this task. If a command you started is still running, "
         "wait for it in the foreground until it finishes. Then finish the task and settle worker_done, "
         "with outcome succeeded or failed.")


class Prober:
    def __init__(self, run, orca, keep=False):
        self.r, self.o, self.keep = run, orca, keep
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
        in_flight = any(self.r.get(f"PROBE_DISPATCH_{pair_key(p)}") for p in pending)
        if pending and not in_flight:
            self.r.set(PROBE_HEAD=git(self.wt, "rev-parse", "HEAD"))
        waiting, failed = [], None
        for p in pending:
            key = pair_key(p)
            res = self.step(p, key)
            if res == "wait":
                waiting.append(self.r.get(f"PROBE_DISPATCH_{key}"))
            elif res == "failed" and failed is None:
                failed = (p, key)
        if failed:
            raise Done(STOP, f"PROBE_FAILED {failed[0]} {self.results_file(failed[1])}")
        if waiting:
            raise Done(WAIT, f"WAIT {waiting[0]} probe")

    def step(self, pair, key):
        """Move one pair forward. Returns 'wait', 'ok' or 'failed'."""
        styles = self.styles(pair)
        while True:
            dispatch = self.r.get(f"PROBE_DISPATCH_{key}")
            idx = int(self.r.get(f"PROBE_STYLE_{key}", "0"))
            if not dispatch:
                if idx >= len(styles):
                    self.r.set(**{f"PROBE_STYLE_{key}": None})
                    return "failed"
                try:
                    self.start(pair, key, idx, styles)
                except Done as d:
                    # Orca refused the launch itself (e.g. invalid_argument: an effort the model lacks).
                    # That is this style failing for this pair, not a broken run (live test 2026-11-15).
                    if not d.line.startswith("ERROR ORCA_FAILED worker-start"):
                        raise
                    with self.results_file(key).open("a", encoding="utf-8") as f:
                        f.write(f"## Launch style {styles[idx]} for {pair} failed (Orca refused the launch)\n"
                                f"{d.line}\n")
                    self.r.set(**{f"PROBE_STYLE_{key}": idx + 1, f"PROBE_DISPATCH_{key}": None})
                    self.r.log(f"probe {pair}: launch style {styles[idx]} refused by Orca")
                    continue
                return "wait"
            result = poll(self.o, self.r, dispatch)
            if result is None:
                return "wait"
            style = styles[idx]
            if result[0] == "idle":
                self.r.set(**{f"HANDLED_SEQ_{dispatch}": result[1]})
                if self.r.incr(f"NUDGES_{dispatch}") == 1:
                    res = self.o.send(dispatch, "Finish and settle this task", NUDGE)
                    new = res.get("dispatchId") if isinstance(res, dict) else None
                    if new and new != dispatch:
                        self.r.set(**{f"PROBE_DISPATCH_{key}": new})
                    self.r.log(f"probe {pair} style {style}: worker idle without settling; nudged")
                    return "wait"
                result = ("stopped", f"worker {dispatch} ended its turn twice without settling")
            if result[0] == "done" and result[1] == "succeeded":
                self.r.set(**{f"LAUNCH_STYLE_{key}": style, f"PROBE_STYLE_{key}": None, f"PROBE_DISPATCH_{key}": None,
                              f"PROBE_RECHECK_{key}": None})
                self.finish(dispatch, pair)
                self.r.log(f"probe {pair}: launch style {style} works")
                return "ok"
            header = f"## Launch style {style} for {pair} failed"
            if result[0] == "done":
                save_body(result[2], self.results_file(key), header=f"{header} (outcome {result[1]})", append=True)
            else:
                with self.results_file(key).open("a", encoding="utf-8") as f:
                    f.write(f"{header}\n{result[1]}\n")
            self.r.set(**{f"PROBE_STYLE_{key}": idx + 1, f"PROBE_DISPATCH_{key}": None})
            self.finish(dispatch, pair)
            self.r.log(f"probe {pair}: launch style {style} failed")

    def start(self, pair, key, idx, styles):
        out = self.results_file(key)
        if idx == 0 and out.exists():
            out.unlink()
        spec = build("probe.txt", self.r.run_dir / "specs" / f"probe-{key}-{idx}.txt")
        args = launch_args(self.catalog, pair, styles[idx])
        dispatch, _ = self.o.start_args(spec, f"Launch test: {pair}", args, self.wt)
        self.r.set(**{f"PROBE_DISPATCH_{key}": dispatch, f"PROBE_STYLE_{key}": idx})

    def finish(self, dispatch, pair):
        """Release the probe worker and put the docs worktree back if it changed anything (keep: log only)."""
        self.o.release(dispatch)
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
    """--docs-moved (ruling R13): an architect job committed in DOCS_WORKTREE while a worker or probe there
    was running. Move the baselines that tree checks reset to (PROBE_HEAD, DOCS_HEAD) to its current HEAD,
    so the architect's commits survive; uncommitted changes are still discarded against the new head."""
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
    o = Orca(r)
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
        Prober(r, o, keep=set_name == "resume").advance(set_pairs(r, set_name, one))
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
