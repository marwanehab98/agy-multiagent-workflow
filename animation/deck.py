"""The observation deck of one run: the web player on a port of its own, and the link to it.

The Capsule Communicator runs two commands per session (references/capsule-communicator.md, "Observation deck"):
  play.py --link <RUN_DIR>   picks the port, saves it in <RUN_DIR>/observation-deck.port and prints the one line to
                             show the user: a Markdown link named "Observation deck" to http://localhost:<port>.
                             With --pill (Antigravity), and while the side pane (pane.py) is up, the link opens
                             that pane instead: sidecar://<plugin>/observation_deck/
  play.py --deck <RUN_DIR>   serves the run's animation on that port until it is stopped (started in the background)
The port is picked before the player starts because nobody reads a background command's output until it ends.
A run resumed while its deck is still up keeps its link: --link prints it again and --deck ends at once.

The deck closes itself when its run is over (run_over): the run's folder is gone (workspace.py remove-docs or
prune), or the run is done and nobody has looked at the page for DONE_LINGER seconds. So a deck never outlives
its run for long, even where nothing stops a background command when the session that started it ends."""
import hashlib
import json
import socket
import time
import urllib.request
from pathlib import Path

NAME = "Observation deck"
PORT_FILE = "observation-deck.port"
HOST = "127.0.0.1"  # served to this machine only; the link says localhost
DONE_LINGER = 600   # seconds a finished run's deck stays up after the last look at its page


def url(port):
    return f"http://localhost:{port}"


def link_line(port):
    return f"[{NAME}]({url(port)})"


def ident(run_dir):
    """What a deck calls its run when asked (/deck.json): a fingerprint of the run folder, never its path."""
    return hashlib.sha256(str(Path(run_dir).resolve()).encode("utf-8")).hexdigest()[:16]


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def saved_port(run_dir):
    try:
        port = int((Path(run_dir) / PORT_FILE).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def save_port(run_dir, port):
    (Path(run_dir) / PORT_FILE).write_text(f"{port}\n", encoding="ascii")


def serves_this_run(port, run_dir):
    """True when this run's deck is already answering on the port (and not some other program)."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/deck.json", timeout=1.0) as r:
            return json.loads(r.read().decode("utf-8")).get("run") == ident(run_dir)
    except (OSError, ValueError):
        return False


def pane_link():
    """The link that opens the side pane, when Antigravity is running it; else None."""
    import pane
    mark = pane.read_mark()
    if not mark:
        return None
    try:
        with urllib.request.urlopen(f"http://{HOST}:{mark['port']}/deck.json", timeout=1.0) as r:
            up = json.loads(r.read().decode("utf-8")).get("pane") is True
    except (OSError, ValueError):
        up = False
    return f"[{NAME}](sidecar://{mark['sidecar']}/)" if up else None


def link(run_dir, pill=False):
    """Pick (or keep) the run's port and return the line to show the user."""
    port = saved_port(run_dir)
    if port is None or not serves_this_run(port, run_dir):
        port = free_port()
        save_port(run_dir, port)
    return (pane_link() if pill else None) or link_line(port)


def run_over(run_dir, hub):
    """True when the deck has nothing left to show anyone: see the module text."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return True
    try:
        done = json.loads((run_dir / "state.json").read_text(encoding="utf-8")).get("PHASE") == "done"
    except (OSError, ValueError, AttributeError):
        return False
    return done and hub.viewers <= 0 and time.monotonic() - hub.looked > DONE_LINGER


def serve(run_dir, driver, fps):
    """Serve the run's animation on its saved port. 0 when this run's deck is already up, when stopped, and when
    it closed itself because the run is over."""
    import web
    port = saved_port(run_dir)
    if port is not None and serves_this_run(port, run_dir):
        print(f"{NAME} already open at {url(port)}", flush=True)
        return 0
    if port is None:
        port = free_port()
        save_port(run_dir, port)
    rc = web.serve(driver, HOST, port, fps, name=NAME, shown=url(port), ident=ident(run_dir),
                   over=lambda hub: run_over(run_dir, hub))
    if rc != web.PORT_TAKEN:
        return 0 if rc == 130 else rc
    port = free_port()          # another program took the port since --link: move, and keep the file true
    save_port(run_dir, port)
    rc = web.serve(driver, HOST, port, fps, name=NAME, shown=url(port), ident=ident(run_dir),
                   over=lambda hub: run_over(run_dir, hub))
    return 0 if rc == 130 else rc
