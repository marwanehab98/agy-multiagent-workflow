"""Play the mission-control flight animation: in the terminal, in a browser, or as PNG key frames.

Usage:
  python3 -I animation/play.py --replay <progress.log> [--speed N]   replay a recorded run (the sample: about 3 minutes;
                                                                     above --speed 1, scenes are cut short to keep up)
  python3 -I animation/play.py --follow <progress.log>               follow a live run
  python3 -I animation/play.py --demo                                every scene in order, without a log
  python3 -I animation/play.py --link <RUN_DIR> [--pill]             print a run's "Observation deck" link (deck.py)
  python3 -I animation/play.py --deck <RUN_DIR>                      serve that run's animation at the link
  python3 -I animation/play.py --pane [--port P]                     serve every recent run, for a side pane (pane.py)
Displays (default: the terminal):
  --web [--port P] [--host H]   serve the animation at http://localhost:P (port: --port, else
                                ANTIGRAVITY_SIDECAR_WEB_PORT, else 8765); Ctrl-C stops it
  --png-dir <dir>               write key frames as PNG files (no display, no waiting) and a captions.txt index
Other options:
  --tasks N     the number of tasks for the kill tally (default: counted from the log)
  --fast        do not wait between frames (terminal; for tests)
  --seconds S   stop after S seconds of animation
Exit 0, 64 on bad usage, 130 on Ctrl-C.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir from sys.path

from engine import DT, FPS, Engine  # noqa: E402
from sources import Follower, demo_schedule, known_ids, replay_schedule  # noqa: E402

LINGER = 4.0  # seconds a finished replay stays on screen in the terminal


class Driver:
    """Feeds cues from the source into the engine at the right moments; one step() per frame."""

    def __init__(self, args):
        self.follower = None
        self.schedule = []
        self.clock = 0.0
        self.done_at = None
        # A replay at normal speed gives every scene its full time: the next cue waits for the one on screen, and the
        # rest of the schedule moves back by that wait (self.lag). Faster replays let the engine shorten scenes.
        self.sequential = bool(args.replay) and args.speed <= 1.0
        self.lag = 0.0
        total = args.tasks
        if args.replay:
            lines = Path(args.replay).read_text(encoding="utf-8", errors="replace").splitlines()
            ids = known_ids(lines)
            self.engine = Engine(total=total or None, task_ids=ids)
            self.schedule = replay_schedule(lines, speed=args.speed)
        elif args.follow:
            self.follower = Follower(args.follow)
            self.engine = Engine(total=total or None)
            self.engine.add_task_ids(self.follower.queued_ids())
            self.engine.fast_forward(self.follower.poll())
            self.next_poll = 0.0
        else:
            self.engine = Engine(total=total or 3)
            self.schedule = demo_schedule()
        self.finite = self.follower is None

    def step(self):
        if self.follower is not None:
            if self.clock >= self.next_poll:
                self.engine.add_task_ids(self.follower.queued_ids())
                for cue in self.follower.poll():
                    self.engine.feed(cue)
                self.engine.waiting = self.follower.waiting_on_user()
                self.next_poll = self.clock + 0.5
        else:
            if self.sequential:
                if self.schedule and self.schedule[0][0] <= self.clock - self.lag:
                    if self.engine.idle():
                        self.engine.feed(self.schedule.pop(0)[1])
                    else:
                        self.lag += DT
            else:
                while self.schedule and self.schedule[0][0] <= self.clock:
                    self.engine.feed(self.schedule.pop(0)[1])
            if not self.schedule and self.done_at is None and self.engine.idle():
                self.done_at = self.clock
        frame = self.engine.step()
        self.clock += DT
        return frame

    def catch_up(self):
        """Apply everything a live log gained while nobody was looking, without animating it."""
        if self.follower is not None:
            self.engine.add_task_ids(self.follower.queued_ids())
            self.engine.fast_forward(self.follower.poll())
            self.engine.waiting = self.follower.waiting_on_user()
            self.next_poll = self.clock + 0.5

    def finished(self, linger=LINGER):
        return self.finite and self.done_at is not None and self.clock - self.done_at >= linger


class Standby:
    """The picture with no run to show: the engine with nothing fed to it."""

    def __init__(self):
        self.engine = Engine(total=None)

    def step(self):
        return self.engine.step()


def dump_pngs(driver, out_dir, seconds=None):
    """Write a frame at each caption change, then 0.4 s, 1.2 s and 2.4 s after it, and every 3 s in between."""
    from pngdump import write_png
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    last_caption, changed_at, last_shot, n = None, 0.0, -10.0, 0
    limit = seconds if seconds else 3600.0
    while not driver.finished(linger=3.0) and driver.clock < limit:
        t = driver.clock
        frame = driver.step()
        if frame.caption != last_caption:
            last_caption, changed_at = frame.caption, t
            shoot = True
        else:
            since = t - changed_at
            shoot = any(abs(since - s) < DT / 2 for s in (0.4, 1.2, 2.4)) or t - last_shot >= 3.0
        if shoot:
            n += 1
            name = f"{n:04d}_t{t:06.1f}.png"
            write_png(out / name, frame.pixels)
            index.append(f"{name}\t{frame.caption}")
            last_shot = t
    (out / "captions.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    return n


def web_port(args):
    if args.port:
        return args.port
    env = os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return 8765


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--replay", metavar="LOG")
    src.add_argument("--follow", metavar="LOG")
    src.add_argument("--demo", action="store_true")
    src.add_argument("--link", metavar="RUN_DIR")
    src.add_argument("--deck", metavar="RUN_DIR")
    src.add_argument("--pane", action="store_true")
    ap.add_argument("--pill", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--tasks", type=int, default=0)
    ap.add_argument("--web", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--png-dir")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--seconds", type=float, default=0)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 64
    if args.speed <= 0:
        print("ERROR --speed must be above 0", file=sys.stderr)
        return 64
    for run_dir in (args.link, args.deck):
        if run_dir and not Path(run_dir).is_dir():
            print(f"ERROR MISSING_RUN_DIR {run_dir}", file=sys.stderr)
            return 64
    if args.link:
        import deck
        print(deck.link(args.link, pill=args.pill))
        return 0
    if args.pane:
        import pane

        def follow(log):
            ns = argparse.Namespace(replay=None, follow=log, demo=False, speed=1.0, tasks=0)
            return Driver(ns) if log else Standby()
        return pane.serve(follow, FPS, web_port(args))
    if args.deck:
        args.follow = str(Path(args.deck) / "progress.log")
    for path in (args.replay, args.follow):
        if path and args.replay and not Path(path).is_file():
            print(f"ERROR MISSING_FILE {path}", file=sys.stderr)
            return 64
    driver = Driver(args)
    if args.png_dir:
        n = dump_pngs(driver, args.png_dir, args.seconds)
        print(f"wrote {n} frames to {args.png_dir}")
        return 0
    if args.deck:
        import deck
        return deck.serve(args.deck, driver, FPS)
    if args.web:
        import web
        return web.serve(driver, args.host, web_port(args), FPS)
    import terminal
    return terminal.play(driver, FPS, fast=args.fast, seconds=args.seconds)


if __name__ == "__main__":
    sys.exit(main())
