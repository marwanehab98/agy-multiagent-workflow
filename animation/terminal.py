"""Terminal display: each character cell is two pixels, a half block with the top pixel as the foreground colour
and the bottom pixel as the background colour (24-bit colour). Redraws in place; restores the terminal on exit.

A terminal too small for the full picture gets it shrunk to fit, in the same shape, with colours blended, down to
MIN_W columns (narrower than that, the scenes are no longer recognisable). Below that it shows the caption and how
wide to make the window, and the picture appears as soon as the window is wide enough. When stdout is not a terminal
(a pipe or a file), it prints only the captions, one line each time one changes."""
import shutil
import signal
import sys
import time

from art import H, RGB, W

HALF = "▀"  # upper half block
FG = [f"\x1b[38;2;{r};{g};{b}m" for r, g, b in RGB]
BG = [f"\x1b[48;2;{r};{g};{b}m" for r, g, b in RGB]
ROWS = H // 2 + 1  # picture rows plus the caption line
MIN_W = 48         # the narrowest picture still worth showing, in columns
MIN_ROWS = MIN_W * H // W // 2 + 1  # and the rows it needs, caption line included


def render_ansi(pixels, caption, prev=None, cols=0):
    """The frame as one string of escape codes: the picture rows that changed since prev (all when prev is None),
    then the caption line. cols is the terminal's width, when known: a row that fills it is not followed by
    "erase to end of line", which would wipe its last cell."""
    end = "\x1b[0m" if cols == W else "\x1b[0m\x1b[K"
    out = []
    for y in range(0, H, 2):
        top, bot = y * W, (y + 1) * W
        if prev is not None and pixels[top:bot + W] == prev[top:bot + W]:
            continue
        fg = bg = -1
        line = [f"\x1b[{y // 2 + 1};1H"]
        for x in range(W):
            t, b = pixels[top + x], pixels[bot + x]
            if t != fg:
                line.append(FG[t])
                fg = t
            if b != bg:
                line.append(BG[b])
                bg = b
            line.append(HALF)
        out.append("".join(line) + end)
    out.append(f"\x1b[{H // 2 + 1};1H\x1b[0m\x1b[1m " + caption[:W - 1] + "\x1b[0m\x1b[K")
    return "".join(out)


def fit(cols, rows):
    """(width, height) in pixels of the picture for a terminal of cols x rows: full size when it fits, else the
    largest smaller one of the same shape (one row is kept for the caption); None when that is under MIN_W."""
    scale = min(1.0, cols / W, (rows - 1) * 2 / H)
    tw, th = int(W * scale), int(H * scale) // 2 * 2
    return (tw, th) if tw >= MIN_W and th >= 2 else None


def shrink_plan(tw, th):
    """For each pixel of a tw x th picture, the four full-size pixels it blends: the centres of its quarters."""
    plan = []
    for y in range(th):
        y0, y1 = (min(H - 1, int((y + f) * H / th)) * W for f in (0.25, 0.75))
        for x in range(tw):
            x0, x1 = (min(W - 1, int((x + f) * W / tw)) for f in (0.25, 0.75))
            plan.append((y0 + x0, y0 + x1, y1 + x0, y1 + x1))
    return plan


def shrink(pixels, plan):
    """The picture as a list of (r, g, b), one per pixel of the plan's size."""
    out = []
    for a, b, c, d in plan:
        pa, pb, pc, pd = pixels[a], pixels[b], pixels[c], pixels[d]
        if pa == pb == pc == pd:
            out.append(RGB[pa])
        else:
            ca, cb, cc, cd = RGB[pa], RGB[pb], RGB[pc], RGB[pd]
            out.append(((ca[0] + cb[0] + cc[0] + cd[0]) >> 2, (ca[1] + cb[1] + cc[1] + cd[1]) >> 2,
                        (ca[2] + cb[2] + cc[2] + cd[2]) >> 2))
    return out


def render_rgb(colours, tw, caption, cols, prev=None):
    """A shrunk frame (shrink()) as escape codes, like render_ansi: the rows that changed since prev, then the
    caption, cut to the terminal's width."""
    th = len(colours) // tw
    end = "\x1b[0m" if cols == tw else "\x1b[0m\x1b[K"
    out = []
    for y in range(0, th, 2):
        top, bot = y * tw, (y + 1) * tw
        if prev is not None and colours[top:bot + tw] == prev[top:bot + tw]:
            continue
        fg = bg = None
        line = [f"\x1b[{y // 2 + 1};1H"]
        for x in range(tw):
            t, b = colours[top + x], colours[bot + x]
            if t != fg:
                line.append("\x1b[38;2;%d;%d;%dm" % t)
                fg = t
            if b != bg:
                line.append("\x1b[48;2;%d;%d;%dm" % b)
                bg = b
            line.append(HALF)
        out.append("".join(line) + end)
    out.append(f"\x1b[{th // 2 + 1};1H\x1b[0m\x1b[1m " + caption[:max(1, cols - 2)] + "\x1b[0m\x1b[K")
    return "".join(out)


def _sigterm(signum, frame):
    raise KeyboardInterrupt


def play(driver, fps, fast=False, seconds=0, out=None):
    out = out or sys.stdout
    if not out.isatty():
        return play_plain(driver, fps, fast, seconds, out)
    old = signal.signal(signal.SIGTERM, _sigterm)
    rc = 0
    last_row = ROWS
    out.write("\x1b[?25l\x1b[2J")
    try:
        tick = time.monotonic()
        size, prev, plan = None, None, None
        while not driver.finished() and not (seconds and driver.clock >= seconds):
            now_size = tuple(shutil.get_terminal_size((80, 24)))
            if now_size != size:      # the first frame, or the window changed: fit the picture again, redraw it all
                size, prev = now_size, None
                box = fit(*size)
                plan = shrink_plan(*box) if box and box != (W, H) else None
                last_row = box[1] // 2 + 1 if box else 2
                out.write("\x1b[2J")
            frame = driver.step()
            if box is None:           # too small for a picture: the caption and what to do, until it is wide enough
                hint = (f"Widen to {MIN_W}+ columns for picture" if size[0] < MIN_W
                        else f"Needs {MIN_ROWS}+ rows for picture")
                out.write("\x1b[1;1H\x1b[0m\x1b[1m " + frame.caption[:max(1, size[0] - 2)] + "\x1b[0m\x1b[K"
                          "\x1b[2;1H\x1b[2m " + hint[:max(1, size[0] - 2)] + "\x1b[0m\x1b[K")
            elif plan is None:
                out.write(render_ansi(frame.pixels, frame.caption, prev, size[0]))
                prev = frame.pixels
            else:
                colours = shrink(frame.pixels, plan)
                out.write(render_rgb(colours, box[0], frame.caption, size[0], prev))
                prev = colours
            out.flush()
            if not fast:
                tick += 1.0 / fps
                delay = tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    tick = time.monotonic()
    except KeyboardInterrupt:
        rc = 130
    finally:
        out.write(f"\x1b[0m\x1b[{last_row + 1};1H\x1b[?25h\n")
        out.flush()
        signal.signal(signal.SIGTERM, old)
    return rc


def play_plain(driver, fps, fast, seconds, out):
    """Captions only, with the animation time: for pipes, log files and small terminals."""
    last = None
    tick = time.monotonic()
    try:
        while not driver.finished() and not (seconds and driver.clock >= seconds):
            t = driver.clock
            frame = driver.step()
            if frame.caption != last:
                last = frame.caption
                out.write(f"[{int(t) // 60:02d}:{int(t) % 60:02d}] {frame.caption}\n")
                out.flush()
            if not fast:
                tick += 1.0 / fps
                delay = tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    return 0
