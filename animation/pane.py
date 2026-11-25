"""The observation deck as a side pane: one player for every run on this machine.

Antigravity starts it as the plugin's sidecar (sidecars/observation_deck/sidecar.json) and shows its page in the
side pane. It finds the runs itself, where scripts/workspace.py puts them:
  ${MISSION_CONTROL_WORKTREES:-${XDG_STATE_HOME:-~/.local/state}/mission-control/worktrees}/<checkout>/<topic>-docs/.mission/
and follows the progress.log of the one the page asks for. With no run, it shows the tower standing by.

Which run a pane shows: Antigravity opens the page with ?conversationId=<the conversation the pane is in>. That
conversation's transcript (<app data>/brain/<id>/.system_generated/logs/transcript.jsonl) holds every command its
Capsule Communicator ran, and those name the run folder, so the pane shows the run the transcript named last. A
conversation that has named none (or a page opened without one) gets the run that was active last. A picker
offers the other recent runs.

While Antigravity runs it (it is given ANTIGRAVITY_SIDECAR_WEB_PORT), <worktrees folder>/.observation-deck-pane
holds its port and its sidecar id, so `play.py --link <RUN_DIR> --pill` can print the link that opens the pane
instead of a localhost link. A pane started by hand leaves no such file: no pill would open it.

Antigravity also gives its sidecar a token (ANTIGRAVITY_SIDECAR_UI_TOKEN) and opens the page with ?token=<it>. With
one, run names, a run's picture and the conversation lookup go only to requests that carry it (web.make_handler);
any other request gets the standby picture and an empty list. A pane started by hand has no token and answers all."""
import hashlib
import json
import os
import re
import signal
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import web

MARK = ".observation-deck-pane"
RECENT = 24 * 3600       # a run whose log last changed longer ago than this is not offered
ID_RE = re.compile(r"^[0-9a-f-]{8,}$", re.I)     # a conversation id, before it is joined into a path
TAIL = 8 * 1024 * 1024   # how much of the end of a transcript is searched
SIDECAR = "observation_deck"


def worktrees_base():
    """The same folder as worktrees_base() in scripts/workspace.py."""
    base = os.environ.get("MISSION_CONTROL_WORKTREES")
    if not base:
        state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        base = str(Path(state_home) / "mission-control" / "worktrees")
    return Path(base)


def run_id(run_dir):
    return hashlib.sha256(str(run_dir).encode("utf-8")).hexdigest()[:12]


def all_runs():
    """Every run on this machine, the one active last first: [{"id", "name", "dir", "log", "active"}]."""
    out = []
    base = worktrees_base()
    for run_dir in base.glob("*/*-docs/.mission") if base.is_dir() else ():
        log = run_dir / "progress.log"
        try:
            active = log.stat().st_mtime
        except OSError:
            continue
        name = run_dir.parent.name[:-len("-docs")]
        try:
            topic = json.loads((run_dir / "state.json").read_text(encoding="utf-8")).get("TOPIC")
            name = topic if isinstance(topic, str) and topic else name
        except (OSError, ValueError, AttributeError):
            pass
        out.append({"id": run_id(run_dir), "name": name, "dir": str(run_dir), "log": str(log), "active": active})
    return sorted(out, key=lambda r: -r["active"])


def app_data_dirs():
    """Where Antigravity keeps conversations: the app data folder this sidecar runs under, found from
    ANTIGRAVITY_EXECUTABLE_DATA_DIR (<app data>/sidecar_data/...); started by hand, every ~/.gemini/<product>
    that has a brain/."""
    data = os.environ.get("ANTIGRAVITY_EXECUTABLE_DATA_DIR")
    if data:
        d = Path(data)
        for p in (d, *d.parents):
            if p.name == "sidecar_data":
                return [p.parent] if (p.parent / "brain").is_dir() else []
        return []
    try:
        return sorted(c for c in (Path.home() / ".gemini").iterdir() if (c / "brain").is_dir())
    except OSError:
        return []


def run_of_conversation(conversation, runs):
    """The id of the run whose folder the conversation's transcript named last, or None."""
    if not conversation or not ID_RE.match(conversation):
        return None
    for app in app_data_dirs():
        transcript = app / "brain" / conversation / ".system_generated" / "logs" / "transcript.jsonl"
        try:
            with transcript.open("rb") as f:
                f.seek(max(0, transcript.stat().st_size - TAIL))
                text = f.read()
        except OSError:
            continue
        last = max(((text.rfind(r["dir"].encode("utf-8")), r["id"]) for r in runs), default=(-1, None))
        if last[0] >= 0:
            return last[1]
    return None


def find_runs(conversation=None, now=None):
    """(the runs to offer, the id of the conversation's own run or None). Offered: the recent runs, the one
    active last first, and the conversation's own run in front of them whatever its age."""
    now = time.time() if now is None else now
    every = all_runs()
    own = run_of_conversation(conversation, every)
    offered = [r for r in every if r["id"] == own] + [r for r in every if r["id"] != own and now - r["active"] <= RECENT]
    return offered, own


def mark_path():
    return worktrees_base() / MARK


def read_mark():
    try:
        mark = json.loads(mark_path().read_text(encoding="utf-8"))
        return mark if isinstance(mark.get("port"), int) and isinstance(mark.get("sidecar"), str) else None
    except (OSError, ValueError, AttributeError):
        return None


def sidecar_id():
    """<plugin folder>/<sidecar folder>, the id Antigravity gives a plugin's sidecar."""
    return f"{Path(__file__).resolve().parent.parent.name}/{SIDECAR}"


class Shows:
    """One hub and one drawing thread per run that has been asked for, plus the standby picture."""

    def __init__(self, make_driver, fps):
        self.make_driver = make_driver
        self.fps = fps
        self.lock = threading.Lock()
        self.hubs = {}
        self.stop = False

    def _start(self, key, log):
        hub = web.Hub()
        self.hubs[key] = hub
        threading.Thread(target=web.produce, args=(self.make_driver(log), hub, self.fps), daemon=True).start()
        return hub

    def standby(self):
        """The hub of the picture with no run: what a request without the pane's token is shown."""
        with self.lock:
            return self.hubs.get("") or self._start("", None)

    def pick(self, wanted, conversation=None):
        """The hub of the run with that id. When the id is unknown or None: the conversation's own run, else the
        run active last, else standby."""
        run = next((r for r in all_runs() if r["id"] == wanted), None) if wanted else None
        if run is None:
            runs, _ = find_runs(conversation)
            run = runs[0] if runs else None
        key, log = (run["id"], run["log"]) if run else ("", None)
        with self.lock:
            return self.hubs.get(key) or self._start(key, log)

    def runs(self, conversation=None):
        runs, own = find_runs(conversation)
        return {"runs": [{"id": r["id"], "name": r["name"]} for r in runs], "own": own}

    def close(self):
        with self.lock:
            for hub in self.hubs.values():
                with hub.cond:
                    hub.stop = True
                    hub.cond.notify_all()


def serve(make_driver, fps, port, host="127.0.0.1"):
    """Serve the pane until stopped. make_driver(log or None) -> a driver following that log (None: standby)."""
    shows = Shows(make_driver, fps)
    token = os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN", "").strip() or None      # Antigravity opens the page with it
    try:
        server = ThreadingHTTPServer((host, port), web.make_handler(None, pick=shows.pick, runs=shows.runs,
                                                                    token=token, standby=shows.standby))
    except OSError as e:
        print(f"ERROR cannot listen on {host}:{port}: {e}", flush=True)
        return web.PORT_TAKEN
    server.daemon_threads = True
    mark = mark_path() if os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", "").strip() else None
    try:
        if mark is not None:
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text(json.dumps({"port": server.server_address[1], "sidecar": sidecar_id()}), encoding="utf-8")
    except OSError:
        mark = None
    print(f"Observation deck pane at http://localhost:{server.server_address[1]} (Ctrl-C stops it)", flush=True)

    def on_term(signum, frame):
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGTERM, on_term)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, old)
        shows.close()
        server.server_close()
        if mark is not None:
            try:
                mark.unlink()
            except OSError:
                pass
    return 0
