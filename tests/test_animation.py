"""Unit tests for the animation prototype in animation/: the sample log's cues, the engine's scenes and frames,
the PNG dump, and both displays."""
import io
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zlib
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "animation"))
sys.path.insert(0, str(ROOT / "scripts"))

import art  # noqa: E402
import cues  # noqa: E402
import engine  # noqa: E402
import pane  # noqa: E402
import play  # noqa: E402
import pngdump  # noqa: E402
import scenes  # noqa: E402
import sources  # noqa: E402
import terminal  # noqa: E402
import watch  # noqa: E402
import deck  # noqa: E402
import web  # noqa: E402

SAMPLE = ROOT / "animation" / "sample-progress.log"

EXPECTED = [
    "director start", "director_done start +", "design_review preflight-check", "design_result preflight-check +",
    "director preflight-check-done", "director_done preflight-check-done +", "design_review safety",
    "design_result safety -", "design_review safety", "design_result safety -", "director safety-done",
    "director_done safety-done +",
    "design_review safety", "design_result safety +", "director safety-done", "director_done safety-done +",
    "design_review plan-review", "design_result plan-review +", "director plan-review-done",
    "director_done plan-review-done +", "probe claude:opus@high", "probe antigravity:gemini-3-pro@high",
    "probe claude:sonnet@high",
    "impl 01 impl", "settled 01 impl +", "gate 01 +", "review 01 1", "verdict 01 1 +", "review 01 2",
    "verdict 01 2 +", "merged 01",
    "impl 02 impl", "settled 02 impl +", "gate 02 -", "impl 02 fix", "settled 02 fix +", "gate 02 +",
    "review 02 1", "verdict 02 1 +", "review 02 2", "verdict 02 2 +", "merged 02",
    "impl 03 impl", "settled 03 impl +", "gate 03 +", "review 03 1", "verdict 03 1 +", "review 03 2",
    "verdict 03 2 -", "impl 03 fix", "settled 03 fix +", "gate 03 +", "review 03 2", "verdict 03 2 +", "merged 03",
    "impl 04 impl", "settled 04 impl -", "escalation 04 1", "director escalate", "director_done escalate +",
    "probe claude:opus@max", "escalation_start 04 1", "impl 04 impl", "settled 04 impl +", "gate 04 +",
    "review 04 1", "verdict 04 1 +", "review 04 2", "verdict 04 2 +", "merged 04",
    "impl 05 impl", "settled 05 impl +", "gate 05 +", "review 05 1", "verdict 05 1 +", "review 05 2",
    "verdict 05 2 +", "merged 05",
    "final 1", "final_verdict 1 +", "director final-review", "director_done final-review +", "verify 1 +", "drift",
    "drift_result +", "director drift-done", "director_done drift-done +", "director pr", "director_done pr +",
    "director report", "director_done report +",
]


def short(c):
    parts = [c.kind]
    if c.task:
        parts.append(c.task)
    if c.arg is not None:
        parts.append(str(c.arg))
    if c.ok is not None:
        parts.append("+" if c.ok else "-")
    return " ".join(parts)


def sample_lines():
    return SAMPLE.read_text(encoding="utf-8").splitlines()


class Cues(unittest.TestCase):
    def test_sample_log_maps_to_the_expected_cues(self):
        self.assertEqual([short(c) for c in cues.parse_lines(sample_lines())], EXPECTED)

    def test_every_sample_milestone_matches_a_watch_pattern(self):
        patterns = [v for k, v in vars(watch).items()
                    if k.startswith("_") and k != "_PROGRESS_LINE" and hasattr(v, "match")]
        for line in sample_lines():
            m = watch._PROGRESS_LINE.match(line)
            self.assertIsNotNone(m, line)
            if m.group(2).startswith("[progress] "):
                ev = m.group(2)[len("[progress] "):]
                self.assertTrue(any(p.match(ev) for p in patterns) or watch.ROUTINE.search(line), line)

    def test_unrecognised_lines_are_ignored(self):
        for line in ("", "garbage", "12:00:00 [progress] something new", "99:99:99 [progress] 01 merged abc",
                     "12:00:00 [worker +5s] 01-implement: tool: Bash", "12:00:00 [worker] start 01-implement (x)",
                     "12:00:00 [progress] 01 gate exit=", "\x00\xff"):
            self.assertIsNone(cues.parse_line(line), line)

    def test_task_ids_follow_replans(self):
        cs = cues.parse_lines(["10:00:00 [progress] 01 impl dispatched a:b@c",
                               "10:00:01 [progress] 01 replanned into 01a 01b from " + "a" * 40])
        self.assertEqual(cues.task_ids(cs), ["01a", "01b"])

    def test_replay_compresses_the_run(self):
        sched = sources.replay_schedule(sample_lines())
        gaps = [b[0] - a[0] for a, b in zip(sched, sched[1:])]
        self.assertLessEqual(max(gaps), sources.GAP_CAP + 1.0)
        self.assertEqual(sched[-1][1].kind, "end")
        fast = sources.replay_schedule(sample_lines(), speed=4)
        self.assertLess(fast[-1][0], sched[-1][0])


class Engine(unittest.TestCase):
    def run_driver(self, args, limit=400.0):
        d = play.Driver(args)
        seen, captions, frames = set(), set(), 0
        while not d.finished(linger=1.0) and d.clock < limit:
            f = d.step()
            frames += 1
            self.assertEqual(len(f.pixels), art.W * art.H)
            self.assertLess(max(f.pixels), len(art.PALETTE))
            self.assertTrue(f.caption)
            seen.add(type(d.engine.scene).__name__)
            captions.add(f.caption)
        return d, seen, captions, frames

    def ns(self, **kw):
        base = dict(replay=None, follow=None, demo=False, speed=1.0, tasks=0)
        base.update(kw)
        return play.argparse.Namespace(**base)

    def test_demo_renders_every_scene(self):
        d, seen, captions, _ = self.run_driver(self.ns(demo=True))
        self.assertEqual(seen, {"TowerScene", "BriefingScene", "HangarScene", "TakeoffScene", "FlightScene",
                                "ApproachScene", "LandingScene"})
        self.assertTrue(any("Missed" in c for c in captions))
        self.assertTrue(any("Hit!" in c for c in captions))
        self.assertEqual(d.engine.phase, "landed")

    def test_sample_replay_lands_with_every_task_merged(self):
        d, seen, captions, frames = self.run_driver(self.ns(replay=str(SAMPLE)))
        self.assertEqual((d.engine.world.merged, d.engine.total), (5, 5))
        self.assertEqual(d.engine.phase, "landed")
        self.assertIn("FlightScene", seen)
        self.assertTrue(any("Task 3/5 (03): stage 2 review" in c for c in captions))
        self.assertTrue(60 <= frames / engine.FPS <= 180, frames / engine.FPS)

    def test_design_reviews_show_no_verdict_and_takeoff_clears_for_flight(self):
        d, _, captions, _ = self.run_driver(self.ns(replay=str(SAMPLE)))
        hangar = [c for c in captions if c.startswith(tuple(engine.REVIEW_NAMES.values()))]
        self.assertTrue(hangar)
        for c in hangar:
            self.assertNotIn("cleared", c.lower())
            self.assertNotIn("pass", c.lower())
        self.assertIn("Range Safety design review: the review worker failed; rerunning it", captions)
        self.assertIn("Range Safety design review: the review worker failed again; the Flight Director decides "
                      "what next", captions)
        self.assertIn("Plan review: inspection complete, report sent to the Flight Director", captions)
        self.assertTrue(any(c.endswith("Cleared for takeoff!") for c in captions))

    def test_downed_and_pending_tally_marks_differ(self):
        c = art.Canvas(art.K.sky2)
        scenes.tally(c, 1, 2)
        x = art.W - 4 - 2 * 6
        downed = {c.get(x + i, y) for i in range(5) for y in range(2, 7)}
        pending = {c.get(x + 6 + i, y) for i in range(5) for y in range(2, 7)}
        self.assertIn(art.K.hud, downed)
        self.assertNotIn(art.K.hud, pending)
        self.assertGreater(sum(c.get(x + i, y) == art.K.hud for i in range(5) for y in range(2, 7)), 12)

    def test_every_flight_action_and_check_state_renders(self):
        f = scenes.FlightScene(0.0)
        f.new_bandit(0.0)
        for lock in range(4):
            f.lock = lock
            f.tracking = bool(lock % 2)
            f.render(art.Canvas(), 2.0)
        for action in ("miss", "hit", "kill"):
            f.do(action, 3.0)
            for t in range(0, 40):
                f.render(art.Canvas(), 3.0 + t * 0.1)
        a = scenes.ApproachScene(0.0)
        for i, v in enumerate((None, "busy", True)):
            a.set_check(i, v)
        a.set_check(2, False)
        a.comm(0.0, "FD")
        h = scenes.HangarScene(0.0, "lens")
        for ok in (None, True, False):
            if ok is not None:
                h.finish(1.0, ok)
            for t in range(30):
                h.render(art.Canvas(), t * 0.1)
                a.render(art.Canvas(), t * 0.1)
        for t in range(0, 140):
            scenes.LandingScene(0.0).render(art.Canvas(), t * 0.1)
            scenes.TakeoffScene(0.0).render(art.Canvas(), t * 0.1)

    def test_follow_starts_from_what_the_log_already_has(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "progress.log"
            lines = sample_lines()
            cut = next(i for i, ln in enumerate(lines) if "02 merged" in ln) + 1
            log.write_text("\n".join(lines[:cut]) + "\n", encoding="utf-8")
            (Path(tmp) / "queue.txt").write_text("03 04 05\n", encoding="utf-8")
            d = play.Driver(self.ns(follow=str(log)))
            self.assertEqual(d.engine.phase, "air")
            self.assertEqual((d.engine.world.merged, d.engine.total), (2, 5))
            with log.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines[cut:cut + 3]) + "\npartial line without newline")
            for _ in range(30):
                d.step()
            self.assertIn("(03)", d.engine.caption)


class Displays(unittest.TestCase):
    def test_png_dump_writes_an_upscaled_png(self):
        c = art.Canvas(art.K.sky2)
        data = pngdump.png_bytes(bytes(c.px), scale=3)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (art.W * 3, art.H * 3))
        idat = data.index(b"IDAT")
        size = struct.unpack(">I", data[idat - 4:idat])[0]
        raw = zlib.decompress(data[idat + 4:idat + 4 + size])
        self.assertEqual(len(raw), (art.W * 3 + 1) * art.H * 3)

    def test_terminal_shows_captions_only_when_not_a_tty(self):
        out = io.StringIO()
        d = play.Driver(play.argparse.Namespace(replay=None, follow=None, demo=True, speed=1.0, tasks=0))
        rc = terminal.play(d, engine.FPS, fast=True, seconds=12, out=out)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertIn("Tower to Flight Director", text)

    def test_terminal_frame_is_half_blocks_with_a_caption(self):
        c = art.Canvas(art.K.sky2)
        s = terminal.render_ansi(bytes(c.px), "Task 1/2: gate")
        self.assertEqual(s.count(terminal.HALF), art.W * art.H // 2)
        self.assertTrue(s.endswith("Task 1/2: gate\x1b[0m\x1b[K"))
        self.assertNotIn(terminal.HALF, terminal.render_ansi(bytes(c.px), "x", prev=bytes(c.px)))

    def test_a_small_terminal_gets_the_picture_shrunk_to_fit(self):
        self.assertEqual(terminal.fit(100, 30), (art.W, art.H))
        self.assertIsNone(terminal.fit(40, 20))
        tw, th = terminal.fit(73, 63)
        self.assertLessEqual(tw, 73)
        self.assertEqual(th % 2, 0)
        self.assertAlmostEqual(tw / th, art.W / art.H, delta=0.1)
        c = art.Canvas(art.K.sky2)
        colours = terminal.shrink(bytes(c.px), terminal.shrink_plan(tw, th))
        self.assertEqual(set(colours), {tuple(art.RGB[art.K.sky2])})
        s = terminal.render_rgb(colours, tw, "Task 1/2: gate", 73)
        self.assertEqual(s.count(terminal.HALF), tw * th // 2)
        self.assertNotIn("\x1b[K" + "\x1b[2;1H", s)      # a row that fills the terminal is not erased to its end
        self.assertNotIn(terminal.HALF, terminal.render_rgb(colours, tw, "x", 73, prev=colours))

    def test_web_serves_the_page_and_frames(self):
        hub = web.Hub()
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(hub))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            hub.publish(engine.Frame(bytes(art.W * art.H), "hello"))
            base = f"http://127.0.0.1:{server.server_address[1]}"
            page = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
            self.assertIn("<canvas", page)
            self.assertIn("image-rendering: pixelated", page)
            frame = json.loads(urllib.request.urlopen(base + "/frame.json", timeout=5).read())
            self.assertEqual(frame["c"], "hello")
            self.assertEqual(frame["v"], web.version())
            self.assertIn(f'VER = "{web.version()}"', page)
            with urllib.request.urlopen(base + "/frames", timeout=5) as stream:
                self.assertEqual(stream.headers["Content-Type"], "text/event-stream")
                self.assertTrue(stream.readline().startswith(b"data: {"))
        finally:
            with hub.cond:
                hub.stop = True
                hub.cond.notify_all()
            server.shutdown()
            server.server_close()

    def test_a_replay_at_normal_speed_gives_every_scene_its_time(self):
        """At --speed 1 the next cue waits for the scene on screen, so cues never pile up and get cut short."""
        ns = play.argparse.Namespace(replay=str(SAMPLE), follow=None, demo=False, speed=1.0, tasks=0)
        d = play.Driver(ns)
        cues, deepest = len(d.schedule), 0
        for _ in range(60 * engine.FPS):
            d.step()
            deepest = max(deepest, len(d.engine.queue))
        self.assertLessEqual(deepest, 1)
        self.assertGreater(d.lag, 0)
        self.assertLess(len(d.schedule), cues)

    def test_a_page_from_before_an_art_change_is_told_to_reload(self):
        """A page left open across a restart must not draw new frames with its old palette: the version differs."""
        before = web.version()
        with mock.patch.object(web, "RGB", list(web.RGB) + [(1, 2, 3)]):
            self.assertNotEqual(web.version(), before)
        self.assertIn("f.v !== VER", web.PAGE)
        self.assertIn("location.reload()", web.PAGE)

    def test_the_deck_link_is_one_named_markdown_link_to_localhost(self):
        with tempfile.TemporaryDirectory() as d:
            line = deck.link(d)
            port = deck.saved_port(d)
            self.assertEqual(line, f"[Observation deck](http://localhost:{port})")
            self.assertNotIn("\n", line)
            # Nothing serves the run yet, so the next call may pick again; the file always holds the printed port.
            self.assertEqual(deck.link(d), f"[Observation deck](http://localhost:{deck.saved_port(d)})")
        self.assertIsNone(deck.saved_port("/nonexistent/run"))

    def test_a_running_deck_keeps_its_link_and_a_second_one_ends_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            hub = web.Hub()
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(hub, deck.ident(d)))
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                port = server.server_address[1]
                deck.save_port(d, port)
                self.assertTrue(deck.serves_this_run(port, d))
                self.assertEqual(deck.link(d), deck.link_line(port))
                self.assertEqual(deck.serve(d, driver=None, fps=engine.FPS), 0)   # returns without serving again
                with tempfile.TemporaryDirectory() as other:                      # the port belongs to another run
                    self.assertFalse(deck.serves_this_run(port, other))
                    deck.save_port(other, port)
                    self.assertNotEqual(deck.link(other), deck.link_line(port))
            finally:
                server.shutdown()
                server.server_close()

    def _fake_runs(self, base, names):
        for i, name in enumerate(names):
            d = Path(base) / "repo-1234" / f"{name}-docs" / ".mission"
            d.mkdir(parents=True)
            (d / "progress.log").write_text("\n".join(sample_lines()[:20]) + "\n", encoding="utf-8")
            os.utime(d / "progress.log", (time.time() - 100 + i, time.time() - 100 + i))
        return Path(base) / "repo-1234"

    def test_the_pane_finds_recent_runs_the_one_active_last_first(self):
        with tempfile.TemporaryDirectory() as base, mock.patch.dict("os.environ", {"MISSION_CONTROL_WORKTREES": base}):
            self.assertEqual(pane.find_runs(), ([], None))
            repo = self._fake_runs(base, ["alpha", "bravo"])
            (repo / "alpha-docs" / ".mission" / "state.json").write_text('{"TOPIC": "PAY-1"}', encoding="utf-8")
            (repo / "old-docs" / ".mission").mkdir(parents=True)
            stale = repo / "old-docs" / ".mission" / "progress.log"
            stale.write_text("x\n", encoding="utf-8")
            os.utime(stale, (time.time() - 2 * pane.RECENT,) * 2)
            self.assertEqual([r["name"] for r in pane.find_runs()[0]], ["bravo", "PAY-1"])

    def test_a_pane_shows_the_run_its_conversation_named_last(self):
        conv = "0a1b2c3d-0000-4000-8000-123456789abc"
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as home, \
                mock.patch.dict("os.environ", {"MISSION_CONTROL_WORKTREES": base,
                                               "ANTIGRAVITY_EXECUTABLE_DATA_DIR": f"{home}/app/sidecar_data/p/s/data"}):
            repo = self._fake_runs(base, ["alpha", "alpha-fixes", "bravo"])
            old = repo / "alpha-docs" / ".mission" / "progress.log"
            os.utime(old, (time.time() - 3 * pane.RECENT,) * 2)                 # too old to be offered by itself
            logs = Path(home) / "app" / "brain" / conv / ".system_generated" / "logs"
            logs.mkdir(parents=True)
            alpha, fixes = (str(repo / f"{n}-docs" / ".mission") for n in ("alpha", "alpha-fixes"))
            (logs / "transcript.jsonl").write_text(
                json.dumps({"cmd": f"state.py --file {fixes}/state.json show"}) + "\n"
                + json.dumps({"cmd": f"run_task.py --state {alpha}/state.json advance"}) + "\n", encoding="utf-8")
            runs, own = pane.find_runs(conv)
            self.assertEqual(own, pane.run_id(Path(alpha)))
            self.assertEqual([r["name"] for r in runs], ["alpha", "bravo", "alpha-fixes"])   # own first, then recent
            self.assertEqual(pane.find_runs("0000aaaa-no-such-conversation")[1], None)
            self.assertEqual(pane.find_runs("../../etc")[1], None)                           # not an id: never a path
            self.assertEqual([r["name"] for r in pane.find_runs()[0]], ["bravo", "alpha-fixes"])
            shows = pane.Shows(lambda log: play.Standby(), engine.FPS)
            try:
                self.assertIs(shows.pick(None, conv), shows.pick(own))
                self.assertIsNot(shows.pick(None, conv), shows.pick(None))
                self.assertEqual(shows.runs(conv)["own"], own)
            finally:
                shows.close()

    def test_the_pane_serves_each_run_and_falls_back_to_the_newest(self):
        with tempfile.TemporaryDirectory() as base, mock.patch.dict("os.environ", {"MISSION_CONTROL_WORKTREES": base}):
            made = []

            def driver(log):
                made.append(log)
                return play.Standby()
            shows = pane.Shows(driver, engine.FPS)
            try:
                standby = shows.pick(None)
                self.assertEqual(made, [None])
                self._fake_runs(base, ["alpha", "bravo"])
                ids = [r["id"] for r in shows.runs()["runs"]]
                newest = shows.pick(None)
                self.assertIsNot(newest, standby)
                self.assertIs(shows.pick("no-such-run"), newest)
                self.assertIs(shows.pick(ids[0]), newest)
                self.assertIsNot(shows.pick(ids[1]), newest)
                self.assertEqual(len(made), 3)                      # one player per run, made once
                self.assertTrue(made[1].endswith("bravo-docs/.mission/progress.log"))
            finally:
                shows.close()

    def test_the_pill_links_to_the_pane_only_while_antigravity_runs_it(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as run, \
                mock.patch.dict("os.environ", {"MISSION_CONTROL_WORKTREES": base}):
            self.assertTrue(deck.link(run, pill=True).startswith("[Observation deck](http://localhost:"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(None, pick=lambda run, conv: web.Hub()))
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                pane.mark_path().write_text(json.dumps({"port": server.server_address[1],
                                                        "sidecar": "mission-control/observation_deck"}))
                self.assertEqual(deck.link(run, pill=True),
                                 "[Observation deck](sidecar://mission-control/observation_deck/)")
                self.assertTrue(deck.link(run).startswith("[Observation deck](http://localhost:"))   # no --pill
            finally:
                server.shutdown()
                server.server_close()
            self.assertTrue(deck.link(run, pill=True).startswith("[Observation deck](http://localhost:"))  # pane gone

    def test_the_sidecar_manifest_starts_the_pane(self):
        folder = ROOT / "sidecars" / pane.SIDECAR
        manifest = json.loads((folder / "sidecar.json").read_text(encoding="utf-8"))
        script = [a for a in manifest["args"] if a.endswith(".py")]
        self.assertEqual([(folder / a).resolve() for a in script], [ROOT / "animation" / "play.py"])
        self.assertIn("--pane", manifest["args"])
        self.assertTrue(manifest["has_web_ui"])
        view = manifest["ui_config"]["views"][0]
        self.assertEqual((view["title"], view["path"], view["entrypoint"]),
                         ("Observation deck", "/", "SIDECAR_UI_ENTRYPOINT_AUX_PANE"))
        self.assertEqual(pane.sidecar_id(), f"{ROOT.name}/{pane.SIDECAR}")

    def test_a_live_run_ends_on_mission_complete_when_the_report_is_written(self):
        e = engine.Engine(total=1)
        for cue in (cues.Cue("impl", "01", "impl"), cues.Cue("merged", "01"), cues.Cue("director", None, "report")):
            e.feed(cue)
        for _ in range(30 * engine.FPS):
            e.step()
        self.assertTrue(e.caption.endswith("writing the report"))
        e.feed(cues.Cue("director_done", None, "report", True))
        for _ in range(engine.FPS):
            e.step()
        self.assertEqual(e.caption, "Mission complete: 1/1 tasks merged")

    def test_the_tower_keeps_the_receiver_its_caption_names(self):
        """After a test launch's beam, the picture goes back to the tower: it must still show the crew's sign."""
        e = engine.Engine(total=None)
        e.feed(cues.Cue("probe", None, "claude:haiku@default"))
        for _ in range(4 * engine.FPS):
            e.step()
        self.assertIn("Tower to crew", e.caption)
        self.assertIs(e.scene, e.tower)
        self.assertEqual(e.tower.label, "CREW")

    def test_one_show_answers_the_runs_question_with_none(self):
        hub = web.Hub()
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(hub))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/runs.json"
            self.assertEqual(json.loads(urllib.request.urlopen(url, timeout=5).read()), {"runs": [], "own": None})
        finally:
            server.shutdown()
            server.server_close()

    def test_the_deck_says_when_the_run_waits_for_the_user(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "progress.log"
            log.write_text("\n".join(sample_lines()[:6]) + "\n", encoding="utf-8")
            state = Path(d) / "state.json"
            drv = play.Driver(play.argparse.Namespace(replay=None, follow=str(log), demo=False, speed=1.0, tasks=0))
            plain = drv.step()
            self.assertFalse(drv.engine.waiting)                 # no state file: not waiting
            self.assertNotIn(engine.WAITING_CAPTION, plain.caption)
            state.write_text('{"WAITING_ON": "user"}', encoding="utf-8")
            for _ in range(engine.FPS):
                frame = drv.step()
            self.assertTrue(frame.caption.startswith(engine.WAITING_CAPTION))
            seen = {drv.step().pixels[(art.H - 7) * art.W + art.W // 2] for _ in range(2 * engine.FPS)}
            self.assertIn(art.K.amber, seen)                     # the sign is on the picture
            state.write_text('{"WAITING_ON": "director"}', encoding="utf-8")
            for _ in range(engine.FPS):
                frame = drv.step()
            self.assertNotIn(engine.WAITING_CAPTION, frame.caption)
            self.assertTrue(all(ch == " " or ch in art.FONT for ch in engine.WAITING_SIGN))

    def _serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, url, method="GET"):
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_a_player_answers_only_its_short_list_of_requests(self):
        with tempfile.TemporaryDirectory() as run:
            base = self._serve(web.make_handler(web.Hub(), deck.ident(run)))
            for path in ("/", "/index.html", "/frame.json", "/deck.json", "/runs.json"):
                self.assertEqual(self._get(base + path)[0], 200, path)
            for path in ("/state.json", "/progress.log", "/../etc/passwd", "/frames/x", "/deck"):
                self.assertEqual(self._get(base + path)[0], 404, path)
            for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                self.assertEqual(self._get(base + "/frame.json", method)[0], 404, method)
            status, headers, body = self._get(base + "/deck.json")
            self.assertNotIn("Python", headers.get("Server", ""))
            self.assertNotIn(run, body.decode("utf-8"))                   # a fingerprint, never the folder's path
            self.assertNotIn("/", json.loads(body)["run"])
            self.assertTrue(deck.serves_this_run(int(base.rsplit(":", 1)[1]), run))

    def test_a_pane_with_a_token_tells_run_names_only_to_requests_that_carry_it(self):
        with tempfile.TemporaryDirectory() as base, mock.patch.dict("os.environ", {"MISSION_CONTROL_WORKTREES": base}):
            self._fake_runs(base, ["alpha", "bravo"])
            shows = pane.Shows(lambda log: play.Standby(), engine.FPS)
            self.addCleanup(shows.close)
            url = self._serve(web.make_handler(None, pick=shows.pick, runs=shows.runs, token="s3cret",
                                               standby=shows.standby))
            for q in ("", "?token=wrong", "?token=", "?conversationId=0a1b2c3d-0000-4000-8000-123456789abc"):
                self.assertEqual(json.loads(self._get(url + "/runs.json" + q)[2]), {"runs": [], "own": None}, q)
            self.assertEqual(self._get(url + "/frame.json?run=" + pane.run_id(Path(base)))[0], 200)
            self.assertEqual(set(shows.hubs), {""})                        # only the standby picture was started
            told = json.loads(self._get(url + "/runs.json?token=s3cret")[2])
            self.assertEqual([r["name"] for r in told["runs"]], ["bravo", "alpha"])
            self._get(url + "/frame.json?token=s3cret&run=" + told["runs"][1]["id"])
            self.assertEqual(set(shows.hubs), {"", told["runs"][1]["id"]})
            self.assertIn('q.set("token", token)', web.PAGE)               # the page passes on the token it was opened with

    def test_badly_shaped_run_and_conversation_values_are_dropped(self):
        seen = []

        def pick(run, conv):
            seen.append((run, conv))
            return web.Hub()
        url = self._serve(web.make_handler(None, pick=pick, runs=lambda conv: {"runs": [], "own": conv}))
        self._get(url + "/frame.json?run=../../x&conversationId=../../etc")
        self._get(url + "/frame.json?run=0123456789ab&conversationId=0a1b2c3d-0000-4000-8000-123456789abc")
        self.assertEqual(seen, [(None, None), ("0123456789ab", "0a1b2c3d-0000-4000-8000-123456789abc")])
        self.assertEqual(json.loads(self._get(url + "/runs.json?conversationId=%2e%2e%2fx")[2])["own"], None)

    def test_a_show_nobody_looks_at_stops_drawing(self):
        hub = web.Hub()
        self.assertFalse(hub.idle())
        hub.looked -= web.IDLE_AFTER + 1
        self.assertTrue(hub.idle())
        hub.look(+1)
        hub.looked -= web.IDLE_AFTER + 1
        self.assertFalse(hub.idle())            # a stream is open
        hub.look(-1)
        self.assertFalse(hub.idle())            # just closed: stays awake for IDLE_AFTER

    def test_a_deck_is_over_when_its_run_is_gone_or_done_and_unwatched(self):
        hub = web.Hub()
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            self.assertTrue(deck.run_over(run, hub))                      # the run's folder is gone
            run.mkdir()
            self.assertFalse(deck.run_over(run, hub))                     # no state file yet: a run starting
            (run / "state.json").write_text('{"PHASE": "execution"}', encoding="utf-8")
            hub.looked -= deck.DONE_LINGER + 1
            self.assertFalse(deck.run_over(run, hub))                     # still running, however long unwatched
            (run / "state.json").write_text('{"PHASE": "done"}', encoding="utf-8")
            self.assertTrue(deck.run_over(run, hub))                      # done and nobody has looked for a while
            hub.look(+1)
            hub.looked -= deck.DONE_LINGER + 1
            self.assertFalse(deck.run_over(run, hub))                     # done, but a page is open
            hub.look(-1)
            self.assertFalse(deck.run_over(run, hub))                     # just closed: lingers

    def test_the_server_closes_when_the_show_is_over(self):
        hub = web.Hub()
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(hub))
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        serving.start()
        answers = iter([False, False, True])
        watcher, closed = web.close_when(server, hub, lambda h: next(answers), every=0.05)
        serving.join(timeout=5)
        watcher.join(timeout=5)
        server.server_close()
        self.assertFalse(serving.is_alive())
        self.assertTrue(closed.is_set())

    def test_the_instructions_and_the_script_agree_on_the_deck(self):
        """The Capsule Communicator is told what `DECK --link` prints; a renamed link must change both."""
        doc = (ROOT / "references" / "capsule-communicator.md").read_text(encoding="utf-8")
        self.assertIn("`DECK` means `python3 -I <SKILL_DIR>/animation/play.py`", doc)
        self.assertIn("starting `[Observation deck](`", doc)
        self.assertTrue(deck.link_line(1).startswith("[Observation deck]("))
        for flag in ("--link", "--deck"):
            self.assertIn(f"DECK {flag} <RUN_DIR>", doc)
            self.assertEqual(play.main([flag, "/nonexistent/run"]), 64)
        resume = (ROOT / "references" / "capcom" / "resume.md").read_text(encoding="utf-8")
        self.assertIn("DECK --link <RUN_DIR>", resume)

    def test_web_port_comes_from_the_sidecar_variable(self):
        ns = play.argparse.Namespace(port=0)
        with mock.patch.dict("os.environ", {"ANTIGRAVITY_SIDECAR_WEB_PORT": "9123"}):
            self.assertEqual(play.web_port(ns), 9123)
            ns.port = 8001
            self.assertEqual(play.web_port(ns), 8001)


if __name__ == "__main__":
    unittest.main()
