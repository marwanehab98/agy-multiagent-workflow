"""The scenes. Each scene draws itself onto a Canvas for a point in time; the engine decides which one plays.

Every scene keeps animating when nothing happens (its idle loop), and takes its timing from `now`, the engine clock
in seconds, so a scene never freezes.
"""
import math
import random

from art import (DIRECTOR, K, W, H, Sprite, bracket_box, clouds, enemy, fields,
                 fireball, jet, plate, ridge, sky_day, sky_dusk, sparks, stars, text_width)

# Kill marks: a pending bandit is a dim outline; a downed one is a solid bright badge with the plane cut out.
MINI_DIM = Sprite([".x...", "x.xxx", ".x.x."], {"x": "hud0"})
MINI_DOWN = Sprite(["ooooo", "o.ooo", ".....", "o.o.o", "ooooo"], {"o": "hud"})


def ease(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def hud_frame(c, col=K.hud0):
    """Cockpit-display corner marks."""
    for (x, y, dx, dy) in ((1, 1, 1, 1), (W - 2, 1, -1, 1), (1, H - 2, 1, -1), (W - 2, H - 2, -1, -1)):
        c.hline(x, x + dx * 4, y, col)
        c.vline(x, y, y + dy * 3, col)


def tally(c, merged, total, flash=False):
    """The kill tally, top right: one mark per task, filled when merged, and merged/total in digits."""
    if not total:
        return
    label = f"{merged}/{total}"
    icons = total <= 8
    width = text_width(label) + (total * 6 + 2 if icons else 0) + 5
    plate(c, W - 2 - width, 1, width, 8)
    if icons:
        x = W - 4 - total * 6
        for i in range(total):
            if i < merged:
                s = MINI_DOWN.recolored({K.hud: K.flash}) if flash and i == merged - 1 else MINI_DOWN
                c.blit(s, x + i * 6, 2)
            else:
                c.blit(MINI_DIM, x + i * 6, 3)
        c.text(x - text_width(label) - 2, 2, label, K.flash if flash else K.hud)
    else:
        c.text(W - 4 - text_width(label), 2, label, K.flash if flash else K.hud)


def comm_icon(c, age, label):
    """A radio call coming in while airborne: a tower icon with pulsing waves and who is calling, bottom left."""
    if age is None or age > 2.6:
        return
    if age > 2.0 and int(age * 8) % 2:
        return
    x, y = 2, H - 12
    w = 15 + text_width(label)
    plate(c, x, y, w, 10)
    c.vline(x + 3, y + 3, y + 8, K.hud)
    c.hline(x + 2, x + 4, y + 3, K.hud)
    c.set(x + 3, y + 2, K.red1 if int(age * 8) % 2 else K.flash)
    for k in range(3):
        if (age * 5 - k) % 3 < 2:
            c.ring(x + 3, y + 3, 2 + 2 * k, K.hud, -1.0, 1.0)
    c.text(x + 12, y + 3, label, K.hud)


# ---- ground: tower -------------------------------------------------------------------------------------------------

def draw_tower(c, x, base, t, lit=False):
    """The control tower: concrete shaft, glass cab, rotating radar, blinking beacon. x is its left edge."""
    top = base - 34
    c.rect(x + 4, top + 9, 8, base - top - 9, K.concrete1)
    c.rect(x + 9, top + 9, 3, base - top - 9, K.concrete0)
    for yy in range(top + 13, base - 3, 5):
        c.rect(x + 6, yy, 3, 2, K.glass0 if not lit else K.yellow)
    c.rect(x + 1, top + 7, 14, 2, K.concrete0)
    c.rect(x, top + 2, 16, 5, K.metal1)
    c.rect(x + 1, top + 3, 14, 3, K.glass1 if not lit else K.amber)
    for i in range(1, 15, 4):
        c.vline(x + i, top + 3, top + 5, K.metal0)
    c.set(x + 3 + int(t * 2) % 10, top + 4, K.glass2 if not lit else K.yellow)
    c.rect(x, top + 1, 16, 1, K.metal0)
    c.vline(x + 8, top - 5, top, K.metal0)
    beacon = (t * 1.25) % 1.0 < 0.35
    c.set(x + 8, top - 6, K.red1 if beacon else K.red0)
    if beacon:
        c.set(x + 7, top - 6, K.red0)
        c.set(x + 9, top - 6, K.red0)
        c.set(x + 8, top - 7, K.red0)
    a = t * 3.0
    w = round(3 * math.cos(a))
    c.vline(x + 3, top - 2, top, K.metal0)
    c.hline(x + 3 - abs(w), x + 3 + abs(w), top - 3, K.metal3 if w > 0 else K.metal2)
    c.set(x + 3, top - 4, K.metal2)
    return x + 8, top - 6


def ground_day(c, horizon, t=0.0, scroll=0.0):
    ridge(c, scroll * 0.1, 0, horizon, 7, K.mtn2, 1.0)
    ridge(c, scroll * 0.2, 0, horizon, 4, K.mtn1, 4.0)
    c.rect(0, horizon, W, H - horizon, K.grass1)
    c.dither(0, horizon, W, 2, K.grass2, 8)
    for x in range(W):
        u = int(x + scroll * 0.6)
        if (u // 3) % 7 == 0:
            c.set(x, horizon + 1, K.grass0)


class TowerScene:
    """A flight tower beaming a signal to whoever receives the job: the Flight Director's ops room or the hangar."""

    def __init__(self, now, target="FD", label=None):
        self.start = now
        self.target = target
        self.label = label or target

    def render(self, c, now, world=None):
        t = now - self.start
        horizon = 40
        sky_day(c, horizon)
        clouds(c, now, 0, 2, 6, 48)
        ground_day(c, horizon, now, now * 2)
        c.rect(0, 46, W, 5, K.tarmac1)
        c.hline(0, W - 1, 46, K.tarmac0)
        for x in range(-(int(now * 3) % 10), W, 10):
            c.hline(x, x + 4, 48, K.concrete1)
        bx, by = draw_tower(c, 8, 46, now)
        # the receiver is drawn first so the beam lands on it; it lights up as each pulse arrives
        period = 0.9
        us = [((t + k * period / 2) % period) / period for k in range(2)]
        lit = any(u > 0.82 or u < 0.06 for u in us) and t > 0.8
        rx, ry = self._receiver(c, now, lit, bx, by)
        # the signal: radio rings at the tower, pulses running down a beam
        dx, dy = rx - bx, ry - by
        dist = math.hypot(dx, dy)
        ang = math.atan2(dy, dx)
        ux, uy = dx / dist, dy / dist
        for d in range(5, int(dist) - 3, 3):
            c.set(bx + ux * d, by + uy * d, K.haze)
        phase = int(t * 8) % 4
        for k in range(3):
            if k < phase:
                c.ring(bx, by, 3 + 3 * k, K.flash if k == phase - 1 else K.yellow, ang - 0.7, ang + 0.7)
        for u in us:
            d = 4 + u * (dist - 6)
            px, py = bx + ux * d, by + uy * d
            for tail in range(1, 6):
                c.set(px - ux * tail * 1.5, py - uy * tail * 1.5, K.yellow if tail < 3 else K.orange)
            c.rect(px - 1, py - 1, 3, 3, K.yellow)
            c.set(px, py, K.flash)
        if lit:
            for k in range(2):
                c.ring(rx, ry, 3 + 3 * k, K.green_lt, ang + math.pi - 1.0, ang + math.pi + 1.0)
        hud_frame(c)

    def _dish(self, c, cx, cy, r, tx, ty, lit):
        """A parabolic dish centred at (cx, cy) aimed at (tx, ty); returns its feed point."""
        dx, dy = tx - cx, ty - cy
        n = math.hypot(dx, dy) or 1.0
        ax, ay = dx / n, dy / n
        for y in range(int(cy - r - 2), int(cy + r + 3)):
            for x in range(int(cx - r - 2), int(cx + r + 3)):
                px, py = x - cx, y - cy
                a = px * ax + py * ay          # along the aim
                b = -px * ay + py * ax         # across it
                if abs(b) > r:
                    continue
                k = a - b * b / (1.6 * r)
                if -3.2 <= k <= 0.5:
                    c.set(x, y, K.white if k > -0.4 else (K.steel3 if k > -1.8 else K.steel1))
                    if abs(abs(b) - r) < 0.7 or k < -2.6:
                        c.set(x, y, K.steel0)
        fx, fy = cx + ax * r * 0.9, cy + ay * r * 0.9
        for side in (-1, 1):
            c.line(cx - ay * side * r * 0.7, cy + ax * side * r * 0.7, fx, fy, K.steel0)
        c.rect(fx - 1, fy - 1, 2, 2, K.green_lt if lit else K.red0)
        return int(round(fx)), int(round(fy))

    def _receiver(self, c, now, lit, tx, ty):
        """Draw the receiving building with its dish and name sign; return the dish's feed point."""
        base = 46
        if self.target == "HANGAR":
            x = 52
            for i in range(34):
                hgt = int(11 * math.sqrt(max(0.0, 1 - ((i - 16.5) / 17.0) ** 2)))
                c.vline(x + i, base - 7 - hgt, base - 1, K.metal1 if i % 3 else K.metal0)
            c.rect(x + 8, base - 10, 18, 10, K.wall0)
            for i in range(9, 25, 3):
                c.vline(x + i, base - 9, base - 1, K.wall1)
            c.rect(x + 14, base - 5, 6, 5, K.amber if int(now * 2) % 2 else K.orange)
            mast_x, top = x + 33, base - 26
        else:
            x = 56
            c.rect(x, base - 15, 28, 15, K.concrete0)
            c.rect(x, base - 16, 28, 1, K.metal0)
            c.rect(x + 1, base - 15, 26, 1, K.concrete1)
            for i in range(4):
                on = (int(now * 0.7) + i) % 5 != 0
                c.rect(x + 2 + i * 6, base - 8, 4, 3, K.yellow if on else K.glass0)
            c.rect(x + 12, base - 4, 4, 4, K.wood0)
            mast_x, top = x + 31, base - 26
        # lattice mast with the dish on top
        c.vline(mast_x - 2, top + 4, base - 1, K.steel1)
        c.vline(mast_x + 2, top + 4, base - 1, K.steel1)
        for y in range(top + 4, base - 1, 4):
            c.line(mast_x - 2, y, mast_x + 2, y + 4, K.steel0)
            c.line(mast_x + 2, y, mast_x - 2, y + 4, K.steel0)
        fx, fy = self._dish(c, mast_x, top, 7, tx, ty, lit)
        # the name sign: dim until a pulse lands, then lit
        w = text_width(self.label) + 6
        sx, sy = (x + 2, base - 24) if self.target != "HANGAR" else (x + 17 - w // 2, base - 30)
        c.vline(sx + 2, sy + 9, sy + 11 if self.target != "HANGAR" else base - 17, K.metal0)
        c.vline(sx + w - 3, sy + 9, sy + 11 if self.target != "HANGAR" else base - 17, K.metal0)
        if lit:
            c.rect(sx, sy, w, 9, K.green_lt)
            c.text(sx + 3, sy + 2, self.label, K.ink)
        else:
            plate(c, sx, sy, w, 9)
            c.text(sx + 3, sy + 2, self.label, K.hud0)
        return fx, fy


# ---- ground: briefing room ---------------------------------------------------------------------------------------

ROUTE = [(36, 30), (44, 24), (52, 26), (60, 16), (70, 18), (78, 11), (84, 12)]


def _route_point(p):
    """The point at fraction p (0..1) along ROUTE, and the index of the segment it is on."""
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(ROUTE, ROUTE[1:])]
    d = p * sum(seg)
    for i, s in enumerate(seg):
        if d <= s or i == len(seg) - 1:
            u = d / s if s else 0
            a, b = ROUTE[i], ROUTE[i + 1]
            return a[0] + (b[0] - a[0]) * min(u, 1), a[1] + (b[1] - a[1]) * min(u, 1), i
        d -= s
    return ROUTE[-1][0], ROUTE[-1][1], len(seg) - 1


class BriefingScene:
    """The Flight Director at the planning board, drawing the flight path; when the job is done, it is approved."""

    def __init__(self, now, job="start"):
        self.start = now
        self.job = job
        self.done_at = None

    def restart(self, now, job):
        if self.done_at is not None:
            self.start = now
        self.job = job
        self.done_at = None

    def finish(self, now):
        self.done_at = now

    def render(self, c, now, world=None):
        t = now - self.start
        # wall with panels, floor
        c.rect(0, 0, W, 42, K.wall1)
        for x in range(0, W, 12):
            c.vline(x, 0, 41, K.wall0)
        c.rect(0, 0, W, 3, K.wall0)
        c.rect(0, 42, W, H - 42, K.wood0)
        for y in range(44, H, 3):
            c.hline(0, W - 1, y, K.wood1 if y % 2 else K.wood0)
        c.hline(0, W - 1, 42, K.ink)
        # ceiling lamp light pool
        c.dither(26, 3, 64, 38, K.wall2, 5)
        # clock
        c.disc(9, 9, 4, K.concrete1)
        c.ring(9, 9, 4, K.metal0)
        a = now * 0.8
        c.line(9, 9, 9 + 3 * math.cos(a), 9 + 3 * math.sin(a), K.ink)
        c.line(9, 9, 9 + 2 * math.cos(a / 12 - 1.5), 9 + 2 * math.sin(a / 12 - 1.5), K.red0)
        # the board
        bx0, by0, bx1, by1 = 28, 5, 91, 37
        c.rect(bx0 - 1, by0 - 1, bx1 - bx0 + 3, by1 - by0 + 3, K.metal1)
        c.rect(bx0, by0, bx1 - bx0 + 1, by1 - by0 + 1, K.board0)
        for x in range(bx0 + 3, bx1, 6):
            for y in range(by0, by1 + 1, 2):
                c.set(x, y, K.board1)
        for y in range(by0 + 3, by1, 6):
            for x in range(bx0, bx1 + 1, 2):
                c.set(x, y, K.board1)
        for (cx, cy, r) in ((40, 30, 6), (46, 33, 5), (34, 27, 3), (64, 18, 5), (70, 15, 4), (58, 21, 3),
                            (85, 10, 4)):
            c.disc(cx, cy, r, K.land0)
        for (cx, cy, r) in ((40, 29, 3), (65, 17, 2)):
            c.disc(cx, cy, r, K.land1)
        c.rect(bx0 - 2, by1 + 2, bx1 - bx0 + 5, 2, K.metal0)
        # route, drawn progressively; once drawn, a plane marker flies it on a loop
        done = self.done_at is not None
        if done:
            p = 1.0
        else:
            p = min(1.0, t / 5.0)
        px, py, seg = _route_point(p)
        col = K.green1 if done else K.yellow
        for i in range(seg + 1):
            a = ROUTE[i]
            b = ROUTE[i + 1] if i < seg else (px, py)
            c.line(a[0], a[1] + 1, b[0], b[1] + 1, K.ink)
            c.line(a[0], a[1], b[0], b[1], col)
        for i, (wx, wy) in enumerate(ROUTE[:seg + 2]):
            if i <= seg or p >= 1.0:
                wc = K.green1 if done else K.flash
                c.rect(wx - 1, wy - 1, 3, 3, K.board0)
                c.set(wx, wy, wc)
                c.set(wx - 1, wy, wc)
                c.set(wx + 1, wy, wc)
                c.set(wx, wy - 1, wc)
                c.set(wx, wy + 1, wc)
        # target ring at the destination
        tx, ty = ROUTE[-1]
        c.ring(tx, ty, 3 + (int(now * 3) % 2), K.red1)
        if p < 1.0:
            # the pen
            c.rect(px - 1, py - 1, 3, 3, K.flash if int(now * 8) % 2 else K.yellow)
        else:
            q = (now * 0.12) % 1.0
            mx, my, _ = _route_point(q)
            c.rect(mx - 1, my, 3, 1, K.white)
            c.set(mx, my - 1, K.white)
        # the Flight Director, pointing at the board
        fx, fy = 15, 32
        c.blit(DIRECTOR, fx, fy)
        hx, hy = fx + 4, fy + 4
        if done:
            c.line(hx, hy, hx + 3, hy - 4, K.skin)
            c.set(hx + 3, hy - 5, K.skin)
        else:
            tip_x, tip_y = 30 + 2 * math.sin(now * 1.3), 26 + 2 * math.cos(now * 1.1)
            c.line(hx, hy, tip_x, tip_y, K.wood1)
            c.set(hx, hy, K.skin)
        # desk in the foreground, with a lamp and a coffee mug
        c.rect(0, 46, 26, 3, K.wood1)
        c.rect(0, 49, 26, 5, K.wood0)
        c.rect(3, 45, 4, 1, K.metal0)
        c.vline(4, 40, 44, K.metal0)
        c.line(4, 40, 7, 37, K.metal0)
        c.rect(6, 35, 5, 2, K.green0)
        c.hline(7, 9, 37, K.yellow)
        c.rect(17, 43, 3, 3, K.shirt)
        c.set(20, 44, K.shirt)
        for k in range(3):
            s = (now * 3 + k) % 3
            c.set(18 + round(math.sin(now * 2 + k)), 42 - int(s * 2), K.metal3 if s < 2 else K.wall2)
        # paper on the desk
        c.rect(9, 45, 6, 1, K.shirt)
        if done:
            if int((now - self.done_at) * 3) % 2 == 0 or now - self.done_at > 1.5:
                c.rect(bx1 - 17, by1 - 9, 15, 7, K.green1)
                c.rect(bx1 - 16, by1 - 8, 13, 5, K.board0)
                c.text(bx1 - 13, by1 - 7, "GO", K.green1)
        hud_frame(c)


# ---- ground: hangar ------------------------------------------------------------------------------------------------

REVIEW_CODES = {"safety": "RSO", "plan-review": "PLAN", "preflight-check": "PFC", "lens": "LENS"}

FLOOR = 46


def big_jet(c, ox=0, oy=0):
    """The jet up close in the hangar (about 90 px long), facing right, with its gear down."""
    def P(pts):
        return [(x + ox, y + oy) for x, y in pts]
    body = P([(10, 24), (54, 22), (60, 18), (66, 16), (72, 17), (77, 21), (84, 26), (93, 30), (84, 32), (70, 33),
              (66, 36), (48, 36), (44, 33), (10, 33)])
    # fin and stabiliser behind the body
    c.poly(P([(12, 24), (18, 5), (25, 5), (33, 23)]), K.steel1)
    c.line(13 + ox, 23 + oy, 18 + ox, 5 + oy, K.steel2)
    c.poly(P([(16, 10), (24, 10), (25, 14), (16, 14)]), K.red1)
    c.hline(18 + ox, 24 + ox, 5 + oy, K.steel3)
    c.rect(4 + ox, 25 + oy, 7, 8, K.metal0)
    c.rect(4 + ox, 26 + oy, 1, 6, K.ink)
    for yy in range(26, 32, 2):
        c.hline(5 + ox, 9 + ox, yy + oy, K.metal1)
    c.poly(body, K.steel2)
    # shade by column: lit top edge, dark belly
    for x in range(8 + ox, 95 + ox):
        col = [y for y in range(H) if c.get(x, y) == K.steel2]
        if not col:
            continue
        top, bot = col[0], col[-1]
        c.set(x, top, K.steel3)
        if bot - top > 4:
            c.set(x, top + 1, K.steel3)
            c.set(x, bot - 1, K.steel1)
        c.set(x, bot, K.steel0)
    c.poly(P([(6, 29), (26, 28), (21, 32), (6, 33)]), K.steel1)
    c.hline(6 + ox, 25 + ox, 28 + oy, K.steel2)
    c.poly(P([(30, 28), (53, 28), (45, 31), (27, 31)]), K.steel1)
    c.hline(30 + ox, 52 + ox, 28 + oy, K.steel3)
    c.poly(P([(48, 33), (61, 33), (61, 36), (48, 36)]), K.metal0)
    c.hline(49 + ox, 60 + ox, 34 + oy, K.ink)
    for x in (22, 56, 82):
        c.vline(x + ox, 25 + oy, 31 + oy, K.steel1)
    c.poly(P([(58, 21), (62, 18), (67, 16), (72, 17), (77, 21)]), K.glass0)
    c.poly(P([(62, 19), (66, 17), (69, 17), (66, 19)]), K.glass1)
    c.set(64 + ox, 18 + oy, K.glass2)
    c.text(36 + ox, 24 + oy, "07", K.steel0)
    c.rect(86 + ox, 28 + oy, 3, 1, K.steel0)
    # gear: main and nose struts, wheels on the floor
    for gx, top in ((42, 33), (80, 32)):
        c.vline(gx + ox, top + oy, FLOOR - 4, K.steel1)
        c.vline(gx + 1 + ox, top + oy, FLOOR - 4, K.steel0)
        c.disc(gx + ox + 0.5, FLOOR - 3, 2.6, K.ink)
        c.set(gx + ox, FLOOR - 3, K.steel1)
        c.set(gx + 1 + ox, FLOOR - 3, K.steel1)


def access_panel(c, now, ox, oy, fault=False):
    """The open avionics bay: a dark hole with wires, its door hinged down."""
    x, y = 34 + ox, 26 + oy
    c.rect(x, y, 9, 5, K.ink)
    for i, col in enumerate((K.yellow, K.red1, K.green1, K.blue_lt)):
        c.line(x + 1 + i * 2, y + 1, x + 2 + i * 2, y + 4, col)
    if fault and int(now * 9) % 3 == 0:
        c.set(x + 4, y + 2, K.flash)
    c.poly([(x, y + 5), (x + 9, y + 5), (x + 10, y + 8), (x + 1, y + 8)], K.steel3)
    c.hline(x + 1, x + 9, y + 8, K.steel1)


def person(c, x, feet, now, pose="stand", face=1, hand=None, phase=0.0):
    """A ground crew member about 12 px tall: hard hat, orange overalls with a hi-vis stripe. face: 1 = right."""
    if pose == "kneel":
        top = feet - 9
        c.rect(x, feet - 2, 4, 2, K.suit)
        c.rect(x + (3 if face > 0 else -2), feet - 1, 3, 1, K.suit)
        c.set(x + (0 if face > 0 else 3), feet - 1, K.ink)
    else:
        top = feet - 12
        c.rect(x, feet - 4, 2, 3, K.suit)
        c.rect(x + 2, feet - 4, 2, 3, K.suit)
        c.set(x + 1, feet - 4, K.wood0)
        c.hline(x, x + 1, feet - 1, K.ink)
        c.hline(x + 2, x + 3, feet - 1, K.ink)
    c.rect(x, top + 4, 4, 4, K.suit)
    c.hline(x, x + 3, top + 6, K.yellow)
    hx = x + 1 if face > 0 else x
    c.rect(hx, top + 2, 3, 2, K.skin)
    c.set(hx + (2 if face > 0 else 0), top + 2, K.ink)
    c.rect(x, top + 1, 4, 1, K.yellow)
    c.rect(x + 1, top, 3 if face > 0 else 2, 1, K.yellow)
    c.set(x + (4 if face > 0 else -1), top + 1, K.amber)
    sx, sy = (x + 3 if face > 0 else x), top + 4
    if hand is not None:
        c.line(sx, sy, hand[0], hand[1], K.suit)
        c.set(hand[0], hand[1], K.skin)
    return top


class HangarScene:
    """Close-up in the hangar: the ground crew inspecting the jet (a review before implementation). When the review
    worker finishes, the crew files its report and sends it to the Flight Director; if the worker itself breaks,
    a fault shows and the review is rerun. The log has no pass/fail verdict for these reviews, so none is shown."""

    def __init__(self, now, kind="safety"):
        self.start = now
        self.kind = kind
        self.result = None       # None: working; True: report sent; False: the review worker failed
        self.result_at = None

    def restart(self, now, kind):
        self.kind = kind
        self.start = now
        self.result = None
        self.result_at = None

    def finish(self, now, ok):
        self.result = ok
        self.result_at = now

    def render(self, c, now, world=None):
        rng = random.Random(int(now * 12))
        fault = self.result is False
        sent = self.result is True
        age = now - self.result_at if self.result_at is not None else 0.0
        lights_on = not (fault and rng.random() < 0.18)
        # back wall, trusses, floor
        c.rect(0, 0, W, FLOOR, K.wall1 if lights_on else K.wall0)
        for x in range(1, W, 4):
            c.vline(x, 7, FLOOR - 1, K.wall0)
        c.rect(0, 0, W, 6, K.wall0)
        for x in range(-8, W, 16):
            c.line(x, 6, x + 8, 1, K.metal1)
            c.line(x + 8, 1, x + 16, 6, K.metal1)
        c.hline(0, W - 1, 6, K.metal1)
        c.hline(0, W - 1, 1, K.metal1)
        for lx in (8, 84):
            c.vline(lx, 7, 8, K.metal0)
            c.rect(lx - 3, 9, 7, 1, K.metal1)
            c.hline(lx - 2, lx + 2, 10, K.flash if lights_on else K.metal0)
        c.rect(0, FLOOR, W, H - FLOOR, K.concrete0)
        c.hline(0, W - 1, FLOOR, K.metal0)
        c.rect(6, FLOOR + 1, 84, 2, K.tarmac1)
        for x in range(0, W, 10):
            c.hline(x, x + 5, H - 3, K.yellow)
        ox, oy = 0, 4
        big_jet(c, ox, oy)
        access_panel(c, now, ox, oy, fault)
        # ceiling sign: the review's code and what the crew is doing
        code = REVIEW_CODES.get(self.kind, self.kind[:4].upper())
        sx, sy = 34, 6
        c.vline(sx + 4, 5, sy, K.metal0)
        c.vline(sx + 21, 5, sy, K.metal0)
        plate(c, sx, sy, 26, 14)
        c.text(sx + 3, sy + 2, code, K.hud)
        if self.result is None:
            word, col = "WORK", (K.amber if int(now * 2) % 2 else K.orange)
        elif sent:
            word, col = "SENT", K.white
        else:
            word, col = "FAULT", (K.red1 if int(now * 4) % 2 else K.red0)
        c.text(sx + 3, sy + 8, word, col)
        if sent:
            # a filed report: a small sheet with lines
            c.rect(sx + 19, sy + 2, 5, 6, K.white)
            for yy in (sy + 3, sy + 5):
                c.hline(sx + 20, sx + 22, yy, K.metal1)
        elif self.result is None:
            c.disc(sx + 21, sy + 4, 1.5, col)
        # 1. welder kneeling under the tail, torch on the engine
        wx = 13
        if self.result is None:
            person(c, wx, FLOOR, now, "kneel", 1, hand=(wx + 6, 37 + oy - 1))
            c.rect(wx + 1, FLOOR - 7, 3, 2, K.metal0)          # welding mask down
            if rng.random() < 0.8:
                fx, fy = wx + 6, 36 + oy
                c.disc(fx, fy, 1.6, K.weld)
                c.set(fx, fy, K.white)
                for _ in range(6):
                    a = rng.uniform(0.3, 2.8)
                    d = rng.uniform(1, 6)
                    c.set(fx + d * math.cos(a), fy + d * math.sin(a), rng.choice((K.flash, K.yellow, K.orange)))
        else:
            person(c, wx, FLOOR, now, "stand", 1, hand=(wx + 5, FLOOR - 9))
            if fault:
                for k in range(3):
                    s = (now * 1.3 + k * 0.5) % 1.5
                    c.disc(wx + 7 - s * 2, 37 + oy - s * 6, 0.8 + s, (K.smoke0, K.smoke1, K.smoke2)[k])
        # 2. avionics tech on a step, reaching into the open bay
        tx = 27
        c.rect(tx - 1, FLOOR - 3, 7, 1, K.red0)
        c.vline(tx - 1, FLOOR - 2, FLOOR - 1, K.red0)
        c.vline(tx + 5, FLOOR - 2, FLOOR - 1, K.red0)
        if self.result is None:
            reach = (36 + ox + int(2 * math.sin(now * 5)), 29 + oy)
            person(c, tx, FLOOR - 3, now, "stand", 1, hand=reach)
            c.set(reach[0] + 1, reach[1] - 1, K.flash if int(now * 6) % 2 else K.yellow)   # torch light
        elif fault:
            person(c, tx - 1, FLOOR - 3, now, "stand", -1, hand=(tx - 3, FLOOR - 16))
            if int(now * 5) % 2:
                sparks(c, 38 + ox, 28 + oy, (now * 2) % 0.6, seed=int(now * 2), n=10, speed=18)
        else:
            person(c, tx, FLOOR - 3, now, "stand", 1, hand=(tx + 6, FLOOR - 11))
        # 3. mechanic on a creeper under the fuselage, wrench working on the belly
        mx, my = 49, FLOOR - 2
        c.rect(mx - 1, my + 1, 14, 1, K.metal0)
        c.set(mx, my + 2, K.ink)
        c.set(mx + 11, my + 2, K.ink)
        c.rect(mx, my - 2, 2, 2, K.yellow)                       # helmet
        c.rect(mx + 2, my - 2, 2, 2, K.skin)
        c.rect(mx + 4, my - 2, 5, 3, K.suit)
        c.vline(mx + 6, my - 2, my, K.yellow)
        c.rect(mx + 9, my - 1, 4, 2, K.suit)
        c.rect(mx + 13, my - 1, 1, 2, K.ink)
        if self.result is None:
            wy = 41 + int(round(math.sin(now * 7)))
            c.line(mx + 5, my - 2, mx + 5, wy, K.suit)
            c.rect(mx + 4, wy - 2, 3, 1, K.steel3)
            c.set(mx + 5, wy - 1, K.steel2)
        # 4. inspector: up the ladder at the cockpit while working, then down with the report
        lx = 84
        c.line(lx, FLOOR - 1, lx - 3, 22 + oy, K.yellow)
        c.line(lx + 4, FLOOR - 1, lx + 1, 22 + oy, K.yellow)
        for k in range(5):
            yy = FLOOR - 4 - k * 4
            u = (FLOOR - 1 - yy) / (FLOOR - 1 - 22 - oy)
            c.hline(lx - int(3 * u), lx + 4 - int(3 * u), yy, K.amber)
        if self.result is None:
            top = person(c, lx - 3, 33 + oy, now, "stand", -1, hand=(lx - 6, 25 + oy + int(math.sin(now * 3))))
            c.rect(lx - 9, 24 + oy, 3, 4, K.white)                # clipboard at the canopy
            c.set(lx - 8, 25 + oy, K.metal1)
            del top
        else:
            px = 69
            person(c, px, FLOOR, now, "stand", 1 if sent else -1,
                   hand=(px + 5, FLOOR - 8) if sent else (px - 1, FLOOR - 13))
            if sent:
                c.rect(px + 5, FLOOR - 10, 3, 4, K.white)
                # copies of the report flying off to the Flight Director, top right
                for k in range(2):
                    u = ((age * 0.8 + k * 0.5) % 1.0)
                    fx = px + 6 + u * 24
                    fy = FLOOR - 10 - u * 8 - 4 * math.sin(u * 3.0)
                    if fx < W - 2:
                        c.rect(fx, fy, 2, 3, K.white)
                        c.set(fx, fy, K.metal2)
        if fault:
            # rotating warning beacon on the wall
            on = int(now * 6) % 2
            c.rect(3, 12, 3, 3, K.red1 if on else K.red0)
            c.vline(4, 15, 17, K.metal0)
            if on:
                c.dither(0, 9, 10, 10, K.red0, 4)
                c.rect(3, 12, 3, 3, K.red1)
        hud_frame(c)


# ---- takeoff -------------------------------------------------------------------------------------------------------

TAKEOFF_TIME = 5.0


def afterburner(c, x, y, slope, length, now):
    """A long afterburner plume from the nozzle of a jet drawn at (x, y), with shock diamonds."""
    flick = (int(now * 30) % 3) - 1
    n = max(2, int(length) + flick)
    for i in range(n):
        yy = y + 6 + round((i + 13) * slope)
        u = i / n
        core = K.flash if u < 0.25 else (K.yellow if u < 0.6 else K.orange)
        c.set(x - 1 - i, yy, core)
        if u < 0.75:
            c.set(x - 1 - i, yy + 1, K.orange if u > 0.3 else K.yellow)
        if u < 0.5:
            c.set(x - 1 - i, yy - 1, K.orange)
        if i % 3 == 1 and u < 0.7:
            c.set(x - 1 - i, yy, K.white)
    c.set(x - 1 - n, y + 6 + round((n + 13) * slope), K.red1)


class TakeoffScene:
    """Spool-up, brakes off, a hard roll with the ground rushing past, rotation, gear up, and a steep climb."""

    def __init__(self, now):
        self.start = now

    @staticmethod
    def _dist(t):
        if t < 0.6:
            return 0.0
        u = t - 0.6
        return 6 * u + 13 * u * u

    def render(self, c, now, world=None):
        t = min(now - self.start, TAKEOFF_TIME + 3)
        release, roll_end, rot_end = 0.6, 2.7, 3.3
        dist = self._dist(t)
        speed = (self._dist(t + 0.05) - dist) / 0.05
        lift = 0.0
        if t > rot_end:
            u = t - rot_end
            lift = 9 * u * u + 5 * u
        shake = (1 if int(now * 24) % 2 else 0) if release < t < roll_end else 0
        horizon = 40 + int(lift) + shake
        sky_day(c, min(horizon, H))
        clouds(c, now + dist * 0.02, 0, 4, 6 + int(lift * 0.4), 48)
        if horizon < H:
            ground_day(c, horizon, now, dist)
            ry = horizon + 5
            c.rect(0, ry, W, 6, K.tarmac1)
            c.hline(0, W - 1, ry, K.tarmac0)
            c.hline(0, W - 1, ry + 5, K.tarmac0)
            # centre line dashes and edge lights stretch into streaks as the speed builds
            streak = min(14, int(speed * 0.12))
            for x in range(-int(dist) % 12 - 24, W, 12):
                c.hline(x, x + 5 + streak, ry + 3, K.white)
            for x in range(-int(dist) % 20 - 40, W, 20):
                c.hline(x, x + max(0, streak - 2), ry - 1, K.runway_lt)
                c.set(x, ry - 1, K.flash)
            # tufts of grass and runway markings rushing by in the foreground
            for x in range(-int(dist * 1.6) % 9 - 18, W, 9):
                c.hline(x, x + 1 + streak // 2, ry + 7, K.grass0)
            if dist * 0.5 < 96:
                draw_tower(c, 70 - int(dist * 0.5), horizon + 3, now)
        # speed streaks in the air
        if speed > 25:
            for k in range(5):
                sx = W - ((now * 140 + k * 41) % (W + 30))
                sy = 8 + (k * 7) % 26
                c.hline(sx, sx + 4 + int(speed * 0.05), sy, K.haze)
        # the jet: the camera holds it near x 14, letting it creep forward as it accelerates
        jx = 12 + min(dist, 30) * 0.4
        if t < rot_end - 0.7:
            jy, slope = 33 + shake, 0.0
        elif t < rot_end:
            u = (t - (rot_end - 0.7)) / 0.7
            jy, slope = 33 - 2 * u * u, 0.32 * ease(u)
        else:
            u = t - rot_end
            jy, slope = 31 - 5 * u - 2 * u * u + lift * 0.0, 0.32
            jx += u * 4
        jy = max(jy, 10)
        gear = 1.0
        if t > rot_end + 0.2:
            gear = max(0.0, 1.0 - (t - rot_end - 0.2) / 0.6)
        flame = 3 if t < 0.3 else (6 if t < release else 9)
        afterburner(c, jx, jy, slope, flame, now)
        if release - 0.4 < t < rot_end and horizon < H:
            # the blast lights up the runway behind the jet
            for i in range(4, 14, 2):
                c.set(jx - i, horizon + 5, K.orange if (i + int(now * 20)) % 4 else K.yellow)
        jet(c, jx, jy, gear=gear, flame=0, slope=slope, t=now)
        if t < 0.6:
            # heat shimmer behind the nozzle while it spools up
            for k in range(4):
                c.set(jx - 6 - k * 2, jy + 4 + (int(now * 20 + k) % 3), K.haze)
        if world is not None:
            tally(c, world.merged, world.total)
        hud_frame(c)


# ---- in the air ----------------------------------------------------------------------------------------------------

def flight_backdrop(c, now, speed=1.0):
    horizon = 40
    sky_day(c, horizon)
    clouds(c, now * speed, 1, 6, 5, 44)
    ridge(c, now * speed, 3, horizon, 9, K.mtn2, 2.0)
    ridge(c, now * speed, 7, horizon + 2, 6, K.mtn1, 5.0)
    base = horizon + 2
    fields(c, base, now, speed, (K.grass1, K.grass2, K.grass1, K.land1, K.dirt, K.grass1), K.grass0)
    c.dither(0, base, W, 1, K.mtn1, 8)
    clouds(c, now * speed, 2, 16, 14, 56, small=True)


class FlightScene:
    """The dogfight: chase the bandit (a task being implemented), acquire a lock (gate and reviews), fire (merge).
    A miss (failed gate, non-PASS verdict) and a hit (escalation) play over the chase; the jet keeps flying."""

    def __init__(self, now):
        self.start = now
        self.enemy_since = None      # when the current bandit flew in; None: no bandit
        self.lock = 0                # 0..3: gate passed, stage 1 passed, stage 2 passed
        self.tracking = False        # a check is running on the bandit
        self.action = None           # "kill", "miss", "hit"
        self.action_at = 0.0
        self.smoke_since = None
        self.comm_at = None
        self.comm_label = ""
        self.kill_pos = (0, 0)

    # events
    def new_bandit(self, now):
        if self.enemy_since is None or self.action == "kill":
            self.enemy_since = now
            self.action = None
        self.lock, self.tracking = 0, False

    def set_lock(self, level):
        self.lock = max(self.lock, level)
        self.tracking = False

    def track(self):
        self.tracking = True

    def do(self, action, now):
        self.action, self.action_at = action, now
        if action == "miss":
            self.lock, self.tracking = 0, False
        if action == "hit" and self.smoke_since is None:
            self.smoke_since = now + 0.5
        if action == "kill":
            self.kill_pos = self._enemy_xy(now)
            self.smoke_since = None

    def comm(self, now, label):
        self.comm_at, self.comm_label = now, label

    # geometry
    def _jet_xy(self, now):
        return 14 + 3 * math.sin(now * 0.37), 22 + 2.5 * math.sin(now * 1.3)

    def _enemy_xy(self, now):
        x = 62 + 5 * math.sin(now * 0.5) - 2 * self.lock
        y = 15 + 5 * math.sin(now * 0.9 + 1.0)
        if self.enemy_since is not None:
            u = ease((now - self.enemy_since) / 1.6)
            x += (1 - u) * 50
            y -= (1 - u) * 8
        if self.action == "miss":
            a = now - self.action_at
            if 0.3 < a < 2.2:
                y -= 7 * math.sin(math.pi * min(1.0, (a - 0.3) / 1.9))
        return x, y

    def busy(self, now):
        return self.action is not None and now - self.action_at < 3.0

    def render(self, c, now, world=None):
        flight_backdrop(c, now)
        jx, jy = self._jet_xy(now)
        a = now - self.action_at
        # speed streaks
        for k in range(6):
            sx = W - ((now * 90 + k * 37) % (W + 20))
            sy = 6 + (k * 13) % 30
            c.hline(sx, sx + 3, sy, K.haze)
        # smoke trail after a hit
        if self.smoke_since is not None and now >= self.smoke_since:
            for k in range(12):
                d = k * 3.5 + (now * 28) % 3.5
                px = jx + 4 - d
                py = jy + 4 - math.sin(now * 1.3 - d * 0.05) * 1.5 - d * 0.1
                r = 0.8 + d * 0.06 + 0.5 * math.sin(k * 1.9 + now * 3)
                col = (K.smoke0, K.smoke1)[k % 2] if k < 6 else (K.smoke1, K.smoke2)[k % 2]
                c.disc(px, py - (k % 2) * 0.6, max(0.6, r), col)
        # the bandit
        ex, ey = self._enemy_xy(now)
        has_enemy = self.enemy_since is not None and not (self.action == "kill" and a >= 0.8)
        if has_enemy:
            enemy(c, ex, ey, t=now)
        # the player's jet
        jitter = 0
        if self.action == "hit" and 0.5 < a < 1.4:
            jitter = 1 if int(now * 20) % 2 else -1
        jet(c, jx, jy + jitter, flame=1, t=now)
        if self.action == "hit" and 0.45 < a < 0.62:
            c.disc(jx + 6, jy + 5, 4, K.flash)
        if self.action == "hit" and 0.5 < a < 1.8:
            sparks(c, jx + 6, jy + 5, a - 0.5, seed=5, n=14, speed=20)
            if a < 1.1:
                c.disc(jx + 5, jy + 5, 2.5 - (a - 0.5) * 2, K.orange)
                c.disc(jx + 5, jy + 5, 1.2, K.yellow)
        if self.action == "hit" and 0.6 < a < 2.6 and (a * 3) % 1 < 0.75:
            c.text(jx + 6, jy - 8, "HIT", K.red1, shadow=K.ink)
        # enemy fire on a hit
        if self.action == "hit" and a < 0.6 and has_enemy:
            for k in range(3):
                u = min(1.0, a / 0.5 + k * 0.15)
                tx = ex + (jx + 8 - ex) * u
                ty = ey + 7 + (jy + 5 - ey - 7) * u
                c.hline(tx, tx + 2, ty, K.red1)
        # missile (kill and miss)
        if self.action in ("kill", "miss") and a < 1.3:
            self._missile(c, now, a, jx, jy, ex, ey)
        if self.action == "miss":
            # flares from the bandit
            if 0.3 < a < 2.0:
                for k in range(4):
                    fa = a - 0.3 - k * 0.12
                    if fa > 0:
                        fx, fy = ex + 18 + fa * 8 + k, ey + 9 + fa * fa * 14 + k * 2
                        c.set(fx, fy, K.flash)
                        c.set(fx + 1, fy, K.yellow if int(now * 10 + k) % 2 else K.orange)
                        c.set(fx, fy - 1, K.smoke2)
            if 0.8 < a < 1.6:
                sparks(c, ex + 30, ey + 16, a - 0.8, seed=9, n=12, speed=18)
                if a < 0.95:
                    c.disc(ex + 30, ey + 16, 2, K.flash)
            if 0.9 < a < 2.6 and (a * 3) % 1 < 0.75:
                c.text(40, 46, "MISS", K.amber, shadow=K.ink)
        # explosion
        if self.action == "kill" and a >= 0.8:
            kx, ky = self.kill_pos
            fireball(c, kx + 11, ky + 6, a - 0.8, seed=int(self.action_at * 10))
        # target lock reticle
        if has_enemy and self.action != "kill" and (self.lock or self.tracking) and now - self.enemy_since > 1.2:
            self._reticle(c, now, ex, ey)
        if self.action == "kill" and 0.6 < a < 2.8 and int(a * 4) % 2 == 0:
            c.text(34, 46, "SPLASH", K.flash, shadow=K.ink)
        if world is not None:
            pending = self.action == "kill" and a < 0.8
            m = world.merged - (1 if pending else 0)
            tally(c, m, world.total, flash=(self.action == "kill" and 0.8 <= a < 2.2 and int(a * 8) % 2 == 0))
        comm_icon(c, None if self.comm_at is None else now - self.comm_at, self.comm_label)
        hud_frame(c)
        # gun cross in front of the nose
        c.set(jx + 32, jy + 6, K.hud)
        c.set(jx + 30, jy + 6, K.hud0)
        c.set(jx + 34, jy + 6, K.hud0)

    def _missile(self, c, now, a, jx, jy, ex, ey):
        u = min(1.0, a / 0.8)
        sx, sy = jx + 12, jy + 10
        if self.action == "miss":
            tx, ty = ex + 26 + (a > 0.4) * 4, ey + 16
        else:
            tx, ty = self.kill_pos[0] + 8, self.kill_pos[1] + 6
        # a drop, then a curved run at the target
        mx = sx + (tx - sx) * (u * u)
        my = sy + 2 * math.sin(math.pi * u) + (ty - sy) * u
        if a < 0.9:
            for k in range(1, 9):
                uu = max(0.0, u - k * 0.04)
                px = sx + (tx - sx) * (uu * uu)
                py = sy + 2 * math.sin(math.pi * uu) + (ty - sy) * uu
                c.set(px, py, K.smoke2 if k < 4 else K.smoke1)
            c.hline(mx - 2, mx + 1, my, K.white)
            c.set(mx + 2, my, K.red1)
            c.set(mx - 3, my, K.yellow if int(now * 20) % 2 else K.orange)

    def _reticle(self, c, now, ex, ey):
        cx, cy = ex + 11, ey + 6
        levels = ((34, K.hud0), (28, K.amber), (24, K.orange), (20, K.red1))
        size, col = levels[self.lock]
        if self.tracking:
            size += 3 * math.sin(now * 8)
            if self.lock == 0:
                col = K.hud
        bracket_box(c, cx - size / 2, cy - size * 0.35, size, size * 0.7, 3, col)
        if self.lock >= 3:
            d = 3
            c.line(cx - d, cy, cx, cy - d, col)
            c.line(cx, cy - d, cx + d, cy, col)
            c.line(cx + d, cy, cx, cy + d, col)
            c.line(cx, cy + d, cx - d, cy, col)
            if int(now * 4) % 2 == 0:
                c.text(cx - 7, cy + size * 0.35 + 2, "LOCK", K.red1, shadow=K.ink)
        else:
            # lock progress pips under the box
            for i in range(3):
                c.rect(cx - 5 + i * 4, cy + size * 0.35 + 2, 3, 1, col if i < self.lock else K.metal0)


# ---- approach and landing -------------------------------------------------------------------------------------------

CHECKS = ("REV", "VFY", "DFT")


def dusk_backdrop(c, now, speed, horizon=34, dark=0):
    """Sunset (dark 0) to night (dark 2): smooth sky, the sun on the horizon, a glowing horizon line, a distant tree
    line, and farmland in distinct muted tones, lighter toward the horizon."""
    sky_dusk(c, horizon, dark)
    if dark < 2:
        sun_y = horizon - 3 + dark * 4
        c.disc(76, sun_y, 6, K.dusk3 if dark else K.sun)
        c.disc(76, sun_y, 4, K.sun if dark else K.flash)
    if dark:
        stars(c, now, horizon - 12, density=14 * dark)
    tint = ({K.cloud: K.dusk3, K.cloud1: K.dusk2, K.cloud2: K.dusk1} if not dark else
            {K.cloud: K.dusk1, K.cloud1: K.dusk0, K.cloud2: K.night})
    clouds(c, now * speed, 4, 3, 8, 50, small=True, tint=tint)
    ridge(c, now * speed, 1, horizon, 4, K.treeline if dark < 2 else K.ink, 3.0)
    if dark:
        cols, trees = (K.nfield0, K.nfield1, K.nfield2, K.nfield3), K.ink
    else:
        cols, trees = (K.dfield0, K.dfield1, K.dfield2, K.dfield3), K.dtree
    fields(c, horizon, now, speed, cols, trees)
    c.hline(0, W - 1, horizon, K.dglow if dark == 0 else (K.dusk1 if dark == 1 else K.night))


def approach_lights(c, now, speed, y):
    """Approach light bars on posts, scrolling toward the runway, with the sequenced strobe running along them."""
    spacing = 16
    off = (now * speed) % spacing
    n = W // spacing + 2
    strobe = int(now * 12) % n
    for k in range(n):
        x = int(k * spacing - off)
        c.vline(x + 2, y + 1, y + 4, K.dtree)
        c.hline(x, x + 4, y, K.runway_lt)
        if k == strobe:
            c.rect(x, y - 1, 5, 3, K.white)
            c.set(x + 2, y - 2, K.flash)


class ApproachScene:
    """Final approach: gear down at dusk, approach lights below, and three landing checks
    (final review, verification, drift check) lighting up one by one."""

    def __init__(self, now):
        self.start = now
        self.checks = [None, None, None]   # None: not started, "busy", True: pass, False: fail
        self.comm_at = None
        self.comm_label = ""

    def set_check(self, i, value):
        self.checks[i] = value

    def comm(self, now, label):
        self.comm_at, self.comm_label = now, label

    def render(self, c, now, world=None):
        horizon = 34
        dusk_backdrop(c, now, 0.8, horizon)
        approach_lights(c, now, 26, horizon + 12)
        # the jet, gear down, slightly nose-up, descending gently
        t = now - self.start
        jx = 26 + 2 * math.sin(now * 0.4)
        jy = 16 + 1.5 * math.sin(now * 1.1) + min(4.0, t * 0.3)
        jet(c, jx, jy, gear=True, flame=1, slope=-0.06, t=now)
        # glideslope scale (right) with the diamond near centre
        gx = W - 6
        for y in range(14, 32, 3):
            c.set(gx, y, K.hud0)
        dy = 23 + 2 * math.sin(now * 0.9)
        c.set(gx - 1, dy, K.hud)
        c.set(gx + 1, dy, K.hud)
        c.set(gx, dy - 1, K.hud)
        c.set(gx, dy + 1, K.hud)
        # landing checks, top left
        plate(c, 1, 1, 54, 9)
        for i, (name, state) in enumerate(zip(CHECKS, self.checks)):
            x, y = 3 + i * 17, 3
            if state is None:
                col = K.metal1
            elif state == "busy":
                col = K.amber if int(now * 3) % 2 else K.wall2
            elif state:
                col = K.green1
            else:
                col = K.red1 if int(now * 4) % 2 else K.red0
            c.rect(x, y, 3, 5, col)
            c.text(x + 4, y, name, col if state is not None else K.hud0)
        if world is not None:
            tally(c, world.merged, world.total)
        comm_icon(c, None if self.comm_at is None else now - self.comm_at, self.comm_label)
        hud_frame(c)


TOUCHDOWN, STOPPED = 3.0, 7.5


class LandingScene:
    """Touchdown: the runway arrives, the wheels smoke, the jet rolls to a stop by the tower; then night falls."""

    def __init__(self, now):
        self.start = now

    def _dist(self, t):
        v0 = 46.0
        if t <= TOUCHDOWN:
            return v0 * t
        T = STOPPED - TOUCHDOWN
        u = min(1.0, (t - TOUCHDOWN) / T)
        return v0 * TOUCHDOWN + v0 * T * (u - u * u / 2)

    def render(self, c, now, world=None):
        t = now - self.start
        horizon = 34
        dist = self._dist(t)
        moving = t < STOPPED
        night = min(1.0, max(0.0, (t - STOPPED - 1.0) / 4.0))
        dark = 0 if night <= 0 else (1 if night < 1.0 else 2)
        dusk_backdrop(c, dist / 40.0, 1.0, horizon, dark)
        # runway: starts off to the right, slides under the jet
        rw_start = 120 - dist
        ry = 44
        x0 = max(0, int(rw_start))
        if rw_start > 4:
            approach_lights(c, dist / 40.0, 40, ry + 1)
            c.rect(int(rw_start), ry - 4, W, 12, K.dfield3 if not dark else K.nfield3)
        if x0 < W:
            c.rect(x0, ry, W - x0, 6, K.tarmac1)
            c.hline(x0, W - 1, ry, K.concrete0)
            c.hline(x0, W - 1, ry + 5, K.concrete0)
            for x in range(int(rw_start) + 6, W, 12):
                if x >= x0:
                    c.hline(x, x + 6, ry + 3, K.white)
            if rw_start >= 0:
                for k in range(0, 6, 2):
                    c.vline(int(rw_start) + 1 + k, ry + 1, ry + 4, K.white)
            for x in range(int(rw_start), W, 12):
                if x >= 0:
                    c.set(x, ry - 1, K.runway_lt)
                    c.set(x + 6, ry + 6, K.runway_lt)
            if rw_start >= 0:
                c.vline(int(rw_start) - 1, ry - 1, ry + 6, K.green_lt)
        # tower comes into view as the jet slows
        tower_x = 64 + self._dist(STOPPED) - dist
        if tower_x < W:
            draw_tower(c, int(tower_x), ry, now, lit=night > 0.3)
        # the jet
        if t < TOUCHDOWN:
            u = t / TOUCHDOWN
            jy = 18 + (ry - 13 - 18) * ease(u)
            slope = -0.06 * (1 - u) - 0.1 * u
            jx = 22
        else:
            jy = ry - 13
            slope = -0.1 * max(0.0, 1 - (t - TOUCHDOWN) / 0.5)
            jx = 22 + min(6.0, (t - TOUCHDOWN) * 1.5)
        jet(c, jx, jy, gear=True, flame=1 if t < TOUCHDOWN else 0, slope=slope, t=now)
        if TOUCHDOWN <= t < TOUCHDOWN + 2.0:
            age = t - TOUCHDOWN
            for k in range(4):
                s = age * 2 + k * 0.3
                r = (1 + s) * max(0.0, 1 - age / 2.0)
                if r >= 0.8:
                    for wx in (jx + 7, jx + 19):
                        c.disc(wx - s * 5 - k * 2, ry - 2 - s * 1.5, r, K.smoke2 if k % 2 else K.concrete1)
        if not moving and night > 0:
            # canopy and nav lights after shutdown
            c.set(jx + 22, jy + 6, K.green1 if int(now * 2) % 2 else K.green0)
            c.set(jx + 3, jy + 1, K.red1 if int(now * 2 + 1) % 2 else K.red0)
        if world is not None:
            tally(c, world.merged, world.total)
        hud_frame(c)
