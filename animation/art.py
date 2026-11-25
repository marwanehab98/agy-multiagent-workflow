"""Palette, pixel canvas, tiny font and sprites for the mission-control animation. Everything is drawn in code."""
import math
import random

W, H = 96, 54  # the logical resolution: 96 x 54 pixels (96 x 27 half-block cells in a terminal)

PALETTE = [
    ("void", (6, 8, 14)), ("ink", (14, 18, 30)), ("night", (22, 30, 58)),
    ("sky0", (30, 50, 102)), ("sky1", (44, 78, 140)), ("sky2", (66, 112, 176)), ("sky3", (102, 152, 206)),
    ("sky4", (150, 192, 228)), ("haze", (196, 220, 238)),
    ("cloud", (242, 246, 252)), ("cloud1", (204, 216, 236)), ("cloud2", (150, 170, 206)),
    ("mtn0", (52, 74, 120)), ("mtn1", (74, 100, 148)), ("mtn2", (104, 132, 176)),
    ("grass0", (30, 66, 48)), ("grass1", (48, 96, 58)), ("grass2", (84, 136, 70)), ("dirt", (110, 90, 64)),
    ("tarmac0", (40, 42, 52)), ("tarmac1", (62, 64, 76)), ("concrete0", (104, 106, 116)),
    ("concrete1", (150, 152, 160)), ("white", (250, 250, 250)),
    ("metal0", (40, 46, 60)), ("metal1", (86, 96, 116)), ("metal2", (138, 148, 168)), ("metal3", (192, 200, 216)),
    ("glass0", (30, 96, 150)), ("glass1", (92, 190, 236)), ("glass2", (190, 240, 255)),
    ("red0", (116, 18, 30)), ("red1", (210, 44, 44)), ("orange", (246, 132, 34)), ("yellow", (252, 212, 60)),
    ("flash", (255, 248, 206)),
    ("green0", (18, 84, 48)), ("green1", (56, 200, 96)), ("hud", (124, 255, 156)), ("hud0", (34, 120, 70)),
    ("amber", (246, 176, 40)),
    ("skin", (232, 178, 138)), ("hair", (66, 42, 30)), ("wood0", (74, 48, 34)), ("wood1", (120, 82, 50)),
    ("wall0", (28, 32, 44)), ("wall1", (42, 48, 64)), ("wall2", (58, 66, 86)),
    ("board0", (14, 38, 52)), ("board1", (30, 68, 86)), ("land0", (46, 96, 74)), ("land1", (70, 130, 92)),
    ("smoke0", (62, 62, 70)), ("smoke1", (110, 110, 120)), ("smoke2", (164, 164, 174)),
    ("dusk0", (52, 36, 86)), ("dusk1", (118, 58, 108)), ("dusk2", (206, 92, 92)), ("dusk3", (246, 150, 90)),
    ("sun", (255, 224, 140)),
    ("enemy0", (54, 40, 44)), ("enemy1", (98, 76, 76)), ("enemy2", (148, 122, 114)),
    ("suit", (214, 104, 36)), ("shirt", (222, 228, 238)), ("navy", (30, 44, 84)),
]


def _ramp(stops, n):
    """n colours evenly spaced along the RGB stops."""
    out = []
    for i in range(n):
        u = i / (n - 1) * (len(stops) - 1)
        k = min(int(u), len(stops) - 2)
        f = u - k
        a, b = stops[k], stops[k + 1]
        out.append(tuple(int(round(a[j] + (b[j] - a[j]) * f)) for j in range(3)))
    return out


def _mix(a, b, f):
    return tuple(int(round(a[j] + (b[j] - a[j]) * f)) for j in range(3))


# Smooth sky gradients, drawn as clean bands one palette step apart (no dithering).
_SKY = _ramp([(22, 40, 92), (48, 90, 160), (96, 150, 212), (164, 204, 236), (214, 234, 246)], 14)
_DUSK = _ramp([(16, 18, 46), (40, 32, 80), (86, 46, 108), (150, 62, 112), (212, 92, 98), (244, 140, 90),
               (255, 192, 112), (255, 226, 160)], 16)
_NIGHT = _ramp([(4, 6, 16), (10, 14, 34), (22, 26, 58), (40, 38, 76), (66, 50, 92), (92, 62, 104)], 16)
for _i, _c in enumerate(_SKY):
    PALETTE.append((f"sky_{_i}", _c))
for _d, _f in enumerate((0.0, 0.55, 1.0)):
    for _i, (_a, _b) in enumerate(zip(_DUSK, _NIGHT)):
        PALETTE.append((f"dusk_{_d}_{_i}", _mix(_a, _b, _f)))
# Dusk ground tones (index 0 near the horizon reads lighter), and the same at night.
PALETTE += [
    ("dfield0", (96, 100, 88)), ("dfield1", (118, 112, 84)), ("dfield2", (124, 96, 88)), ("dfield3", (78, 92, 80)),
    ("dtree", (68, 74, 70)), ("dglow", (255, 196, 128)), ("treeline", (58, 44, 74)),
    ("nfield0", (34, 40, 52)), ("nfield1", (42, 46, 52)), ("nfield2", (38, 36, 50)), ("nfield3", (28, 36, 46)),
    ("runway_lt", (255, 236, 170)), ("green_lt", (120, 255, 140)), ("blue_lt", (110, 160, 255)),
    ("steel0", (70, 78, 96)), ("steel1", (112, 122, 142)), ("steel2", (160, 170, 188)), ("steel3", (210, 216, 228)),
    ("weld", (200, 230, 255)),
]
RGB = [rgb for _, rgb in PALETTE]


class _Names:
    pass


K = _Names()  # K.sky2 -> palette index
for _i, (_n, _) in enumerate(PALETTE):
    setattr(K, _n, _i)

# 4x4 ordered-dither thresholds (0..15), for gradients, fades and transitions.
BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


class Sprite:
    def __init__(self, rows, legend):
        self.h = len(rows)
        self.w = max(len(r) for r in rows)
        self.data = [[(getattr(K, legend[ch]) if ch in legend else None) for ch in r.ljust(self.w, ".")]
                     for r in rows]

    def flipped(self):
        s = Sprite.__new__(Sprite)
        s.w, s.h, s.data = self.w, self.h, [list(reversed(r)) for r in self.data]
        return s

    def recolored(self, mapping):
        s = Sprite.__new__(Sprite)
        s.w, s.h = self.w, self.h
        s.data = [[mapping.get(c, c) if c is not None else None for c in r] for r in self.data]
        return s


class Canvas:
    def __init__(self, fill=0):
        self.w, self.h = W, H
        self.px = bytearray([fill]) * (W * H)

    def copy(self):
        c = Canvas.__new__(Canvas)
        c.w, c.h, c.px = self.w, self.h, bytearray(self.px)
        return c

    def set(self, x, y, c):
        x, y = int(x), int(y)
        if 0 <= x < W and 0 <= y < H:
            self.px[y * W + x] = c

    def get(self, x, y):
        x, y = int(x), int(y)
        if 0 <= x < W and 0 <= y < H:
            return self.px[y * W + x]
        return None

    def rect(self, x, y, w, h, c):
        x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(W, int(x) + int(w)), min(H, int(y) + int(h))
        if x1 <= x0:
            return
        row = bytes([c]) * (x1 - x0)
        for yy in range(y0, y1):
            self.px[yy * W + x0:yy * W + x1] = row

    def fill(self, c):
        self.rect(0, 0, W, H, c)

    def hline(self, x0, x1, y, c):
        if x1 < x0:
            x0, x1 = x1, x0
        self.rect(x0, y, x1 - x0 + 1, 1, c)

    def vline(self, x, y0, y1, c):
        if y1 < y0:
            y0, y1 = y1, y0
        self.rect(x, y0, 1, y1 - y0 + 1, c)

    def line(self, x0, y0, x1, y1, c, dash=0, phase=0):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err, i = dx + dy, phase
        while True:
            if not dash or (i // dash) % 2 == 0:
                self.set(x0, y0, c)
            i += 1
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def poly(self, pts, c):
        """Fill a polygon (scanline, even-odd)."""
        ys = [p[1] for p in pts]
        for y in range(max(0, int(min(ys))), min(H, int(max(ys)) + 1)):
            yc = y + 0.5
            xs = []
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for a, b in zip(xs[::2], xs[1::2]):
                self.rect(int(round(a)), y, int(round(b)) - int(round(a)), 1, c)

    def disc(self, cx, cy, r, c):
        r2 = r * r + r * 0.8
        for yy in range(int(cy - r - 1), int(cy + r + 2)):
            for xx in range(int(cx - r - 1), int(cx + r + 2)):
                if (xx - cx) ** 2 + (yy - cy) ** 2 <= r2:
                    self.set(xx, yy, c)

    def ring(self, cx, cy, r, c, a0=-math.pi, a1=math.pi, step=None):
        """An arc of radius r between angles a0..a1 (radians, 0 = right, positive = down)."""
        n = max(8, int(r * 7))
        for i in range(n + 1):
            a = a0 + (a1 - a0) * i / n
            self.set(round(cx + r * math.cos(a)), round(cy + r * math.sin(a)), c)

    def dither(self, x, y, w, h, c, level):
        """Cover the rect with c where the Bayer threshold is below level (0..16)."""
        for yy in range(max(0, int(y)), min(H, int(y + h))):
            brow = BAYER[yy & 3]
            for xx in range(max(0, int(x)), min(W, int(x + w))):
                if brow[xx & 3] < level:
                    self.px[yy * W + xx] = c

    def bands(self, y0, colors, heights, soft=True):
        """Horizontal colour bands from y0 down, dithered into each other where they meet."""
        y = y0
        for i, (c, hgt) in enumerate(zip(colors, heights)):
            self.rect(0, y, W, hgt, c)
            if soft and i > 0:
                self.dither(0, y - 1, W, 1, c, 8)
                self.dither(0, y, W, 1, colors[i - 1], 4)
            y += hgt

    def blit(self, s, x, y, flip=False):
        x, y = int(round(x)), int(round(y))
        for j, row in enumerate(s.data):
            yy = y + j
            if yy < 0 or yy >= H:
                continue
            base = yy * W
            for i, c in enumerate(reversed(row) if flip else row):
                if c is not None:
                    xx = x + i
                    if 0 <= xx < W:
                        self.px[base + xx] = c

    def blit_shear(self, s, x, y, slope, pivot=None):
        """Blit s with each column shifted up by slope * (column - pivot): a cheap pitch rotation."""
        pivot = s.w / 2 if pivot is None else pivot
        x, y = int(round(x)), int(round(y))
        for j, row in enumerate(s.data):
            for i, c in enumerate(row):
                if c is not None:
                    self.set(x + i, y + j - round((i - pivot) * slope), c)

    def text(self, x, y, s, c, shadow=None):
        cx = int(x)
        for ch in s.upper():
            glyph = FONT.get(ch)
            if glyph is None:
                cx += 4
                continue
            for j, bits in enumerate(glyph):
                for i in range(3):
                    if bits & (4 >> i):
                        if shadow is not None:
                            self.set(cx + i + 1, y + j + 1, shadow)
                        self.set(cx + i, y + j, c)
            cx += 4
        return cx


def text_width(s):
    return 4 * len(s) - 1


# 3x5 font: each glyph is 5 rows of 3 bits (4 = left column).
FONT = {
    "A": [2, 5, 7, 5, 5], "B": [6, 5, 6, 5, 6], "C": [3, 4, 4, 4, 3], "D": [6, 5, 5, 5, 6], "E": [7, 4, 6, 4, 7],
    "F": [7, 4, 6, 4, 4], "G": [3, 4, 5, 5, 3], "H": [5, 5, 7, 5, 5], "I": [7, 2, 2, 2, 7], "J": [1, 1, 1, 5, 2],
    "K": [5, 5, 6, 5, 5], "L": [4, 4, 4, 4, 7], "M": [5, 7, 7, 5, 5], "N": [6, 5, 5, 5, 5], "O": [2, 5, 5, 5, 2],
    "P": [6, 5, 6, 4, 4], "Q": [2, 5, 5, 6, 3], "R": [6, 5, 6, 5, 5], "S": [3, 4, 2, 1, 6], "T": [7, 2, 2, 2, 2],
    "U": [5, 5, 5, 5, 7], "V": [5, 5, 5, 5, 2], "W": [5, 5, 7, 7, 5], "X": [5, 5, 2, 5, 5], "Y": [5, 5, 2, 2, 2],
    "Z": [7, 1, 2, 4, 7], "0": [7, 5, 5, 5, 7], "1": [2, 6, 2, 2, 7], "2": [6, 1, 2, 4, 7], "3": [6, 1, 2, 1, 6],
    "4": [5, 5, 7, 1, 1], "5": [7, 4, 6, 1, 6], "6": [3, 4, 7, 5, 7], "7": [7, 1, 2, 2, 2], "8": [7, 5, 7, 5, 7],
    "9": [7, 5, 7, 1, 6], "/": [1, 1, 2, 4, 4], "-": [0, 0, 7, 0, 0], ":": [0, 2, 0, 2, 0], ".": [0, 0, 0, 0, 2],
    "!": [2, 2, 2, 0, 2], "?": [6, 1, 2, 0, 2], "+": [0, 2, 7, 2, 0],
}

# ---- sprites -------------------------------------------------------------------------------------------------------

_JET_LEGEND = {"a": "metal3", "b": "metal2", "c": "metal1", "d": "metal0", "g": "glass1", "h": "glass0",
               "w": "glass2", "r": "red1", "n": "ink", "y": "yellow"}
# The player's fighter, side view, facing right.
JET = Sprite([
    "..dd....................",
    "..dad...................",
    "..dbrd..................",
    "..dbrbd..........ddd....",
    "..dbbbbd........dhwgd...",
    "nddbbbbbaaaaaaaahhggga..",
    "ncbbbbbbbbbbbbbbbbbbbbaad",
    "ndcccbbbbbbbbbbbbbbccccd.",
    "..dddddcccccccccccdd.....",
    "....ddd..ddddddd.........",
], _JET_LEGEND)

# Wheels for the landing gear (drawn under JET at x+5 and x+17 when the gear is down).
GEAR = Sprite([
    ".c...........c",
    ".c...........c",
    "nnn.........nn",
    ".n...........n",
], _JET_LEGEND)

_ENEMY_LEGEND = {"a": "enemy2", "b": "enemy1", "c": "enemy0", "d": "ink", "g": "glass0", "w": "glass1",
                 "r": "red1", "y": "yellow"}
# The bandit, side view, facing left (a delta-wing interceptor with a red tail band).
ENEMY = Sprite([
    "...................dd.",
    "..................dbd.",
    ".................drbd.",
    ".................drbd.",
    ".......dd.......dbbbd.",
    ".....dgwgd.....dbbbbd.",
    "...aaagggaaaaaaabbbbbdd",
    "daabbbbbbbbbbbbbbbbbbbn",
    ".dcbbbbbbbbbbbbbbbcccdn",
    "...dccccccbbbbbbccccd..",
    "......dddcccccccddd....",
], _ENEMY_LEGEND)

_PERSON_LEGEND = {"h": "yellow", "s": "skin", "o": "suit", "d": "navy", "k": "ink", "w": "shirt", "a": "hair",
                  "g": "metal2", "e": "glass1"}
ENGINEER = [Sprite(rows, _PERSON_LEGEND) for rows in (
    [".hh.", "hhhh", ".ss.", "oooo", "oooo", ".oo.", ".o.o", ".k.k"],
    [".hh.", "hhhh", ".ss.", "ooooo", "oooo", ".oo.", ".o.o", ".k.k"],
)]
ENGINEER_CHEER = Sprite(["s..s", "ohho", "ohhh", ".ss.", "oooo", ".oo.", ".o.o", ".k.k"], _PERSON_LEGEND)
DIRECTOR = Sprite([
    ".aa.",
    "assa",
    ".ss.",
    "wwww",
    "wdww",
    "wwww",
    ".dd.",
    ".d.d",
    ".d.d",
    ".k.k",
], _PERSON_LEGEND)


def jet(c, x, y, gear=False, flame=0, slope=0.0, t=0.0, jitter=0):
    """The player's jet at (x, y) top-left, with afterburner flame strength 0..3, pitch slope, gear."""
    x, y = int(round(x)), int(round(y))
    if flame:
        fl = flame + (1 if int(t * 24) % 2 else 0)
        mid = y + 6
        for i in range(fl + 2):
            col = K.flash if i < 1 else (K.yellow if i < fl else K.orange)
            yy = mid + round(-(-i) * slope)
            c.set(x - 1 - i, yy, col)
            if i < fl:
                c.set(x - 1 - i, yy + 1, K.orange if i else K.yellow)
    g = GEAR
    if gear and gear is not True and gear < 1:
        rows = max(0, int(round(GEAR.h * gear)))
        g = Sprite.__new__(Sprite)
        g.w, g.h, g.data = GEAR.w, rows, GEAR.data[GEAR.h - rows:]
    if slope:
        if gear:
            c.blit_shear(g, x + 5, y + 10, slope, pivot=7)
        c.blit_shear(JET, x, y, slope, pivot=12)
    else:
        if gear:
            c.blit(g, x + 6, y + 10)
        c.blit(JET, x, y)


def enemy(c, x, y, t=0.0, flame=True):
    x, y = int(round(x)), int(round(y))
    if flame:
        fl = 2 + (int(t * 20) % 2)
        for i in range(fl):
            c.set(x + ENEMY.w + i, y + 7, K.orange if i else K.yellow)
            c.set(x + ENEMY.w + i, y + 8, K.red1 if i else K.orange)
    c.blit(ENEMY, x, y)


# ---- procedural scenery -------------------------------------------------------------------------------------------

def _make_cloud(rng, w, h):
    """A cloud sprite: overlapping discs, lit from the top, shaded underneath."""
    blobs = []
    n = max(3, w // 5)
    for i in range(n):
        cx = 3 + (w - 6) * i / max(1, n - 1) + rng.uniform(-1, 1)
        r = rng.uniform(h * 0.35, h * 0.6) * (1.0 - 0.5 * abs(i / max(1, n - 1) - 0.5))
        blobs.append((cx, h - r - 0.5, r))
    rows = []
    for yy in range(h):
        row = ""
        for xx in range(w):
            inside = [(xx - cx) ** 2 + (yy - cy) ** 2 <= r * r for cx, cy, r in blobs]
            if not any(inside):
                row += "."
            else:
                depth = min(((xx - cx) ** 2 + (yy - cy - 1.6) ** 2) / (r * r) for cx, cy, r in blobs)
                if yy >= h - 2:
                    row += "s" if yy == h - 1 else "m"
                elif depth > 0.9 and yy > h / 2:
                    row += "m"
                else:
                    row += "c"
        rows.append(row)
    return Sprite(rows, {"c": "cloud", "m": "cloud1", "s": "cloud2"})


_rng = random.Random(7)
CLOUDS_BIG = [_make_cloud(_rng, w, h) for w, h in ((26, 9), (20, 8), (30, 10), (18, 7))]
CLOUDS_SMALL = [_make_cloud(_rng, w, h) for w, h in ((12, 4), (9, 4), (14, 5), (8, 3))]


def clouds(c, t, layer, speed, y0, spacing, small=False, tint=None):
    """A row of clouds scrolling left at speed px/s, repeating every spacing px."""
    lib = CLOUDS_SMALL if small else CLOUDS_BIG
    off = (t * speed) % spacing
    n = int(W / spacing) + 3
    for i in range(-1, n):
        idx = i + int(t * speed // spacing)
        s = lib[(idx * 7 + layer) % len(lib)]
        if tint:
            s = s.recolored(tint)
        x = i * spacing - off + ((idx * 37 + layer * 13) % 17) - 8
        y = y0 + ((idx * 53 + layer * 7) % 7) - 3
        c.blit(s, x, y)


def ridge(c, t, speed, base_y, amp, col, seed, top_col=None):
    """A mountain ridge silhouette scrolling left; base_y is the baseline, amp the peak height."""
    off = t * speed
    for x in range(W):
        u = (x + off) * 0.06
        hgt = amp * (0.55 + 0.25 * math.sin(u + seed) + 0.15 * math.sin(u * 2.3 + seed * 3) +
                     0.08 * math.sin(u * 5.1 + seed * 7))
        top = int(base_y - hgt)
        c.vline(x, top, base_y, col)
        if top_col is not None:
            c.set(x, top, top_col)


def gradient(c, y0, y1, ramp, curve=1.0):
    """Fill rows y0..y1-1 from ramp[0] at the top to ramp[-1] at the bottom: one clean band per palette step."""
    n = len(ramp)
    span = max(1, y1 - y0 - 1)
    for y in range(max(0, y0), min(H, y1)):
        u = ((y - y0) / span) ** curve
        c.rect(0, y, W, 1, ramp[min(n - 1, int(u * n))])


SKY_RAMP = [getattr(K, f"sky_{i}") for i in range(len(_SKY))]
DUSK_RAMPS = [[getattr(K, f"dusk_{d}_{i}") for i in range(len(_DUSK))] for d in range(3)]


def sky_day(c, horizon):
    gradient(c, 0, horizon, SKY_RAMP, 0.9)


def sky_dusk(c, horizon, dark=0):
    """The sunset sky; dark 1..2 shifts it toward night."""
    gradient(c, 0, horizon, DUSK_RAMPS[max(0, min(2, dark))], 1.4)


def stars(c, t, max_y, density=40, seed=3):
    rng = random.Random(seed)
    for _ in range(density):
        x, y = rng.randrange(W), rng.randrange(max(1, max_y))
        tw = math.sin(t * rng.uniform(1.5, 4) + rng.uniform(0, 6))
        c.set(x, y, K.white if tw > 0.6 else (K.sky3 if tw > -0.2 else K.sky1))


def bracket_box(c, x, y, w, h, arm, col):
    """Four corner brackets around a box (HUD target reticle)."""
    x0, y0, x1, y1 = int(x), int(y), int(x + w - 1), int(y + h - 1)
    for (cx, cy, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        c.hline(cx, cx + dx * arm, cy, col)
        c.vline(cx, cy, cy + dy * arm, col)


def fireball(c, cx, cy, age, seed=1, size=9.0):
    """An explosion age seconds old: flash, fireball, then smoke and falling debris."""
    rng = random.Random(seed)
    if age < 0.12:
        c.disc(cx, cy, 6, K.flash)
        return
    r = min(size, 3 + age * 14)
    if age < 1.1:
        fade = age / 1.1
        c.disc(cx, cy, r, K.red1 if fade > 0.5 else K.orange)
        c.disc(cx - 1, cy - 1, r * (0.75 - 0.3 * fade), K.orange if fade > 0.5 else K.yellow)
        if fade < 0.6:
            c.disc(cx - 1, cy - 1, r * 0.4, K.flash)
        for k in range(7):
            a = rng.uniform(0, 6.28)
            d = r * rng.uniform(0.6, 1.1)
            c.disc(cx + d * math.cos(a), cy + d * math.sin(a), rng.uniform(1, 2.5), K.orange if k % 2 else K.red1)
    sm = max(0.0, age - 0.5)
    if sm > 0:
        for k in range(6):
            a = rng.uniform(0, 6.28)
            d = r * rng.uniform(0.3, 0.9) + sm * 4
            rr = (2 + sm * 2.5) * max(0.0, 1.0 - sm / 2.2)
            if rr >= 0.8:
                col = (K.smoke0, K.smoke1, K.smoke2)[min(2, int(sm * 1.6 + (k % 2)))]
                c.disc(cx + d * math.cos(a), cy + d * math.sin(a) - sm * 6, rr, col)
    for k in range(9):
        a = rng.uniform(-3.0, -0.1)
        sp = rng.uniform(12, 26)
        dx = math.cos(a) * sp * age - age * 10
        dy = math.sin(a) * sp * age + 22 * age * age
        col = (K.metal1, K.enemy1, K.orange, K.ink)[k % 4]
        c.set(cx + dx, cy + dy, col)
        if k % 3 == 0 and age < 1.5:
            c.set(cx + dx + 1, cy + dy - 1, K.yellow)


def sparks(c, cx, cy, age, seed=1, n=8, speed=14.0, cols=None):
    """A small burst of sparks flying outward and falling."""
    rng = random.Random(seed)
    cols = cols or (K.flash, K.yellow, K.orange)
    for k in range(n):
        a = rng.uniform(0, 6.28)
        sp = rng.uniform(0.5, 1.0) * speed
        c.set(cx + math.cos(a) * sp * age, cy + math.sin(a) * sp * age + 10 * age * age, cols[k % len(cols)])


def plate(c, x, y, w, h):
    """A dark cockpit-display plate with a dim green border, for HUD read-outs."""
    c.rect(x, y, w, h, K.ink)
    c.hline(x, x + w - 1, y, K.hud0)
    c.hline(x, x + w - 1, y + h - 1, K.hud0)
    c.vline(x, y, y + h - 1, K.hud0)
    c.vline(x + w - 1, y, y + h - 1, K.hud0)


def fields(c, y0, now, speed, cols, trees):
    """Farmland seen from the air below the horizon at y0: rows of patches, thin and slow far away, tall and fast
    near the bottom (parallax). cols: the patch colours; trees: the colour of tree clumps."""
    y, row = y0, 0
    while y < H:
        hgt = 1 + row
        sp = (5 + row * 8) * speed
        width = 7 + row * 6
        off = now * sp
        first = int(off // width)
        for i in range(first - 1, first + W // width + 2):
            x = i * width - off
            h = ((i * 2654435761) ^ (row * 40503)) & 0xFFFF
            c.rect(x, y, width, hgt, cols[h % len(cols)])
            if h % 3 == 0 and row >= 1:
                tx = x + (h >> 5) % max(1, width - 3)
                tw = 1 + row // 2
                c.rect(tx, y - 1, tw + 1, max(1, hgt), trees)
                c.rect(tx + 1, y - 1 - (row > 2), tw, 1, trees)
        y += hgt
        row += 1
