"""Web display: a small stdlib HTTP server. GET / is the page (a canvas scaled to fit, pixelated), GET /frames is a
server-sent event stream of frames, GET /frame.json the latest frame. One engine runs on the server per show; every
page open on a show gets the same frames, and a show nobody has looked at for IDLE_AFTER seconds stops drawing until
someone does. The side pane (pane.py) serves several shows: the page asks /runs.json which there are and names one
with ?run=<id>. It passes on the ?conversationId= Antigravity opened it with, which decides the show it starts on.

What a player answers is kept short on purpose, because any program on this machine can ask it. It answers GET on
the six paths in make_handler and nothing else (every other path or method: a bare 404). No answer holds a folder
path: a deck names its run by a fingerprint. A pane that Antigravity runs is given a token and opens its page
with ?token=<it>; only requests that carry it are told run names, shown a run, or matched to a conversation.
Everyone else gets the standby picture. Every frame carries the version of the page it was made for (palette and size), so a
page left open across a restart with different art reloads itself instead of drawing with the old palette."""
import base64
import hashlib
import hmac
import json
import re
import signal
import threading
import time
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from art import H, RGB, W

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Observation deck</title>
<style>
  html, body { margin: 0; height: 100%; background: #06080e; color: #7cff9c; overflow: hidden; }
  body { display: flex; flex-direction: column; align-items: center; justify-content: center; }
  canvas { image-rendering: pixelated; image-rendering: crisp-edges; display: block; background: #000; }
  #cap { font: 14px/1.35 ui-monospace, Menlo, Consolas, monospace; padding: 6px 8px 0; max-width: 100%;
         box-sizing: border-box; text-align: center; overflow-wrap: anywhere; }
  #cap.off { color: #4a6a55; }
  #runs { display: none; margin: 0 0 6px; max-width: 96%; background: #0d1420; color: #7cff9c; border: 1px solid #2a4a38;
          border-radius: 4px; padding: 3px 6px; font: 13px/1.3 ui-monospace, Menlo, Consolas, monospace; }
</style></head>
<body>
<select id="runs" aria-label="Run"></select>
<canvas id="screen" width="__W__" height="__H__"></canvas>
<div id="cap" class="off">connecting...</div>
<script>
const W = __W__, H = __H__, PAL = __PAL__, VER = "__VER__";
const cv = document.getElementById("screen"), cap = document.getElementById("cap"), sel = document.getElementById("runs");
const ctx = cv.getContext("2d"), img = ctx.createImageData(W, H);
function fit() {
  const aw = window.innerWidth - 8, ah = window.innerHeight - cap.offsetHeight - sel.offsetHeight - 18;
  let s = Math.max(0.25, Math.min(aw / W, ah / H));
  if (s >= 2) s = Math.floor(s);
  cv.style.width = Math.round(W * s) + "px";
  cv.style.height = Math.round(H * s) + "px";
}
window.addEventListener("resize", fit);
fit();
function draw(f) {
  if (f.v !== VER) { es.close(); location.reload(); return; }
  const bin = atob(f.p), d = img.data;
  for (let i = 0; i < bin.length; i++) {
    const c = PAL[bin.charCodeAt(i)];
    d[i * 4] = c[0]; d[i * 4 + 1] = c[1]; d[i * 4 + 2] = c[2]; d[i * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  if (cap.textContent !== f.c) { cap.textContent = f.c; cap.title = f.c; }
  cap.className = "";
}
const opened = new URLSearchParams(location.search);
const conv = opened.get("conversationId") || "", token = opened.get("token") || "";
function ask(path, id) {
  const q = new URLSearchParams();
  if (id) q.set("run", id);
  if (conv) q.set("conversationId", conv);
  if (token) q.set("token", token);
  return q.toString() ? path + "?" + q : path;
}
let es = null, shown = null, picked = false;
function show(id) {
  if (es) es.close();
  shown = id;
  es = new EventSource(ask("frames", id));
  es.onmessage = (e) => draw(JSON.parse(e.data));
  es.onerror = () => { cap.className = "off"; };
}
sel.onchange = () => { picked = true; show(sel.value); };
async function runs() {
  let list, own;
  try {
    const r = await fetch(ask("runs.json", null), { cache: "no-store" });
    if (!r.ok) return;
    ({ runs: list, own } = await r.json());
  } catch (e) { return; }
  const ids = list.map((r) => r.id);
  if (picked && !ids.includes(shown)) picked = false;
  const want = picked ? shown : (own || ids[0] || null);
  sel.replaceChildren(...list.map((r) => new Option(r.name, r.id, false, r.id === want)));
  sel.style.display = list.length > 1 ? "block" : "none";
  fit();
  if (want !== shown) show(want);
}
show(null);
runs();
setInterval(runs, 5000);
</script></body></html>
"""


def version():
    """What a page must agree on with the server to draw its frames: the canvas size and the palette."""
    return hashlib.sha256(json.dumps([W, H, [list(c) for c in RGB]]).encode("ascii")).hexdigest()[:12]


IDLE_AFTER = 5.0


class Hub:
    """The latest frame, and a condition the stream handlers wait on."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.data = None
        self.stop = False
        self.version = version()
        self.viewers = 0
        self.looked = time.monotonic()

    def publish(self, frame):
        payload = json.dumps({"v": self.version, "c": frame.caption, "p": base64.b64encode(frame.pixels).decode("ascii")})
        with self.cond:
            self.seq += 1
            self.data = payload
            self.cond.notify_all()

    def look(self, change=0):
        """Someone is looking: a stream opened (+1) or closed (-1), or the latest frame was asked for."""
        with self.cond:
            self.viewers += change
            self.looked = time.monotonic()

    def idle(self):
        return self.viewers <= 0 and time.monotonic() - self.looked > IDLE_AFTER

    def wait(self, seen, timeout=15.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != seen or self.stop, timeout)
            return self.seq, self.data


def page_html():
    return (PAGE.replace("__W__", str(W)).replace("__H__", str(H))
            .replace("__PAL__", json.dumps([list(c) for c in RGB])).replace("__VER__", version()))


RUN_RE = re.compile(r"^[0-9a-f]{8,64}$")
CONV_RE = re.compile(r"^[0-9a-f-]{8,64}$", re.I)
NO_RUNS = {"runs": [], "own": None}


def make_handler(hub, ident=None, pick=None, runs=None, token=None, standby=None):
    """The request handler. One show: `hub`. Several: pick(run id or None, conversation id or None) -> its hub,
    runs(conversation id or None) -> {"runs": [{"id", "name"}], "own": id or None}. With a token, a request without
    it is answered as if there were no runs: standby() is the hub it is shown."""
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "observation-deck"     # not the Python version
        sys_version = ""

        def refuse(self):
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

        do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = refuse

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            asked = parse_qs(query)

            def one(key, shape):
                value = (asked.get(key) or [""])[0]
                return value if shape.match(value) else None
            run, conv = one("run", RUN_RE), one("conversationId", CONV_RE)
            trusted = token is None or hmac.compare_digest((asked.get("token") or [""])[0].encode("utf-8"),
                                                           token.encode("utf-8"))

            def show():
                if not pick:
                    return hub
                return pick(run, conv) if trusted else standby()
            if path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", page_html().encode("utf-8"))
            if path == "/frame.json":
                chosen = show()
                chosen.look()
                _, data = chosen.wait(-1, 0)
                return self._send(200, "application/json", (data or "{}").encode("utf-8"))
            if path == "/frames":
                return self._stream(show())
            if path == "/deck.json":      # what this player is, for deck.py: one run's deck, or the side pane
                return self._send(200, "application/json", json.dumps({"run": ident, "pane": pick is not None}).encode("utf-8"))
            if path == "/runs.json":      # one show has no others to offer; answered all the same, so the page's
                found = runs(conv) if runs and trusted else NO_RUNS         # regular question is not an error
                return self._send(200, "application/json", json.dumps(found).encode("utf-8"))
            return self.refuse()

        def _stream(self, hub):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            seen = -1
            hub.look(+1)
            try:
                while not hub.stop:
                    seq, data = hub.wait(seen)
                    if hub.stop:
                        break
                    if seq == seen or data is None:
                        self.wfile.write(b": keep-alive\n\n")
                    else:
                        seen = seq
                        self.wfile.write(b"data: " + data.encode("ascii") + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                hub.look(-1)

    return Handler


def produce(driver, hub, fps):
    """Draw frames while someone is looking. After a pause, a live run is brought up to date at once (catch_up),
    so the viewer sees where the run is now and not a rush through what was missed."""
    tick = time.monotonic()
    slept = False
    while not hub.stop:
        if hub.idle():
            slept = True
            time.sleep(0.25)
            continue
        if slept:
            slept = False
            tick = time.monotonic()
            if hasattr(driver, "catch_up"):
                driver.catch_up()
        hub.publish(driver.step())
        tick += 1.0 / fps
        delay = tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            tick = time.monotonic()


PORT_TAKEN = 2


def close_when(server, hub, over, every=2.0):
    """Shut the server down once over(hub) says the show is over; checked every `every` seconds, off the serving
    thread. Returns the thread and a flag that is set when this is what closed the server."""
    closed = threading.Event()

    def watch():
        while not hub.stop:
            if over(hub):
                closed.set()
                server.shutdown()
                return
            time.sleep(every)
    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread, closed


def serve(driver, host, port, fps, name="Mission control animation", shown=None, ident=None, over=None):
    """Serve until stopped: 130 on Ctrl-C or SIGTERM, 0 when over(hub) says the show is over (close_when),
    PORT_TAKEN when the port cannot be listened on."""
    hub = Hub()
    try:
        server = ThreadingHTTPServer((host, port), make_handler(hub, ident))
    except OSError as e:
        print(f"ERROR cannot listen on {host}:{port}: {e}", flush=True)
        return PORT_TAKEN
    server.daemon_threads = True
    worker = threading.Thread(target=produce, args=(driver, hub, fps), daemon=True)
    worker.start()
    where = shown or f"http://{'localhost' if host == '127.0.0.1' else host}:{server.server_address[1]}"
    print(f"{name} at {where} (Ctrl-C stops it)", flush=True)
    rc = 0

    def on_term(signum, frame):
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGTERM, on_term)
    closed = close_when(server, hub, over)[1] if over else None
    try:
        server.serve_forever(poll_interval=0.25)
        if closed is not None and closed.is_set():
            print(f"{name} closed: its run is over", flush=True)
    except KeyboardInterrupt:
        rc = 130
    finally:
        signal.signal(signal.SIGTERM, old)
        with hub.cond:
            hub.stop = True
            hub.cond.notify_all()
        server.server_close()
    return rc
