"""The animation engine: takes cues (from cues.py), decides which scene plays, and renders frames.

Each frame is a W x H grid of palette indices (art.PALETTE) plus a short caption. The displays (terminal, web)
only show frames; every scene and every decision lives here.
"""
from collections import deque, namedtuple

from art import BAYER, H, K, W, Canvas, plate, text_width
from cues import Cue
from scenes import (REVIEW_CODES, STOPPED, TAKEOFF_TIME, ApproachScene, BriefingScene, FlightScene, HangarScene,
                    LandingScene, TakeoffScene, TowerScene)

WAITING_SIGN = "WAITING FOR YOU"
WAITING_CAPTION = "Waiting for your answer."
FPS = 12
DT = 1.0 / FPS
TRANSITION = 0.6

Frame = namedtuple("Frame", "pixels caption")

REVIEW_NAMES = {"safety": "Range Safety design review", "plan-review": "Plan review",
                "preflight-check": "Preflight check", "lens": "Wind-tunnel lens review"}
JOB_NAMES = {"start": "drafting the design", "consult": "answering a worker's question",
             "fix-meta": "fixing the task metadata", "escalate": "escalation", "final-review": "final review verdict",
             "drift-done": "drift findings", "verification-failed": "verification failure",
             "pr": "writing the PR", "report": "writing the mission report", "jira-close": "closing the JIRA issue",
             "resume": "resuming", "user-message": "a message from you", "baseline-failed": "a failing baseline"}


def job_name(job):
    if job in JOB_NAMES:
        return JOB_NAMES[job]
    if job.endswith("-done"):
        kind = job[:-len("-done")]
        return f"reading the {REVIEW_NAMES.get(kind, kind).lower()}"
    return job


class World:
    """What every scene may show: the kill tally."""

    def __init__(self, engine):
        self.e = engine

    @property
    def merged(self):
        return len(self.e.merged_ids)

    @property
    def total(self):
        return self.e.total


class Engine:
    def __init__(self, total=None, task_ids=None):
        self.now = 0.0
        self.queue = deque()
        self.ready_at = 0.0
        self.phase = "ground"            # ground -> air -> approach -> landed
        self.ids = list(task_ids or [])
        self.dropped = set()
        self.merged_ids = set()
        self.fixed_total = total
        self.world = World(self)
        self.waiting = False             # the run is waiting for the user (set by whoever follows a live run)
        self.tower = TowerScene(0.0, "FD")
        self.home = self.tower           # the scene a ground beam returns to
        self.scene = self.tower
        self.old = None
        self.old_at = 0.0
        self.flight = None
        self.approach = None
        self.landing = None
        self.briefing = None
        self.hangar = None
        self.task = None
        self.review_fails = {}
        self.caption = "Mission control: standing by"

    # ---- bookkeeping ---------------------------------------------------------------------------------------------

    @property
    def total(self):
        known = len([t for t in self.ids if t not in self.dropped])
        return max(self.fixed_total or 0, known, len(self.merged_ids))

    def add_task_ids(self, ids):
        for t in ids:
            if t not in self.ids:
                self.ids.append(t)

    def label(self, t):
        live = [x for x in self.ids if x not in self.dropped]
        if t in live and self.total:
            return f"Task {live.index(t) + 1}/{self.total} ({t})"
        return f"Task {t}"

    def feed(self, cue):
        self.queue.append(cue)

    def fast_forward(self, cues):
        """Apply cues at once, without holds or transitions (a live log that already has lines in it)."""
        for cue in cues:
            self._apply_all(cue, instant=True)
        if isinstance(self.scene, TakeoffScene):
            self.scene = self._flight()
        if self.flight is not None:
            self.flight.action = None
            self.flight.comm_at = None
        self.old = None
        self.ready_at = self.now

    def _apply_all(self, cue, instant):
        follow = [cue]
        while follow:
            c = follow.pop(0)
            res = self._apply(c)
            if isinstance(res, tuple):
                follow = list(res[1]) + follow
        if instant:
            self.old = None

    # ---- the clock -----------------------------------------------------------------------------------------------

    def step(self):
        """Advance one frame and return it."""
        while self.queue and self.now >= self.ready_at:
            cue = self.queue.popleft()
            res = self._apply(cue)
            hold, more = (res if isinstance(res, tuple) else (res, ()))
            for extra in reversed(more):
                self.queue.appendleft(extra)
            backlog = len(self.queue)
            scale = 1.0 if backlog <= 4 else (0.5 if backlog <= 12 else 0.2)
            self.ready_at = self.now + (hold or 0.0) * scale
        caption = f"{WAITING_CAPTION} {self.caption}" if self.waiting else self.caption
        frame = Frame(bytes(self.render().px), caption)
        self.now += DT
        return frame

    def idle(self):
        """True when no cue is waiting and the current one has had its time."""
        return not self.queue and self.now >= self.ready_at

    def render(self):
        c = Canvas()
        self.scene.render(c, self.now, self.world)
        if self.old is not None:
            p = (self.now - self.old_at) / TRANSITION
            if p >= 1.0:
                self.old = None
            else:
                o = Canvas()
                self.old.render(o, self.now, self.world)
                level = int(p * 17)
                for y in range(H):
                    brow = BAYER[y & 3]
                    base = y * W
                    for x in range(W):
                        if brow[x & 3] >= level:
                            c.px[base + x] = o.px[base + x]
        if self.waiting:
            self._waiting_sign(c)
        return c

    def _waiting_sign(self, c):
        """A sign across the bottom of any scene while the run waits for the user: lit amber, blinking."""
        w = text_width(WAITING_SIGN) + 6
        x, y = (W - w) // 2, H - 11
        if self.now % 1.2 < 0.8:
            c.rect(x, y, w, 9, K.amber)
            c.text(x + 3, y + 2, WAITING_SIGN, K.ink)
        else:
            plate(c, x, y, w, 9)
            c.text(x + 3, y + 2, WAITING_SIGN, K.amber)

    def switch(self, scene):
        if scene is self.scene:
            return
        self.old, self.old_at = self.scene, self.now
        self.scene = scene

    # ---- scenes on demand ----------------------------------------------------------------------------------------

    def _flight(self):
        if self.flight is None:
            self.flight = FlightScene(self.now)
        return self.flight

    def _approach(self):
        if self.approach is None:
            self.approach = ApproachScene(self.now)
        return self.approach

    def _comm(self, label):
        """A call while airborne shows as an icon in the HUD instead of a cut to the tower."""
        if self.scene is self.flight:
            self.flight.comm(self.now, label)
        elif self.scene is self.approach:
            self.approach.comm(self.now, label)

    def _beam(self, target, caption, then=(), label=None):
        """On the ground: cut to the tower beaming the job to its receiver, then go on with `then`."""
        at_tower = self.home is self.tower      # nothing else to go back to yet: stay on this receiver, so the
        self.tower = TowerScene(self.now, target, label)    # picture keeps matching the caption after the hold
        if at_tower:
            self.home = self.tower
        self.switch(self.tower)
        self.caption = caption
        return 1.5, tuple(then) or (Cue("_home"),)

    # ---- cue -> scene --------------------------------------------------------------------------------------------

    def _apply(self, cue):
        """Act on one cue; return how long it holds the screen (seconds), or (seconds, follow-up cues)."""
        k, t = cue.kind, cue.task
        if t and t not in self.ids:
            self.ids.append(t)
        air = self.phase in ("air", "approach")

        if k == "_home":
            self.switch(self.home)
            return 0.0
        if k == "_plan":
            if self.briefing is None:
                self.briefing = BriefingScene(self.now, cue.arg)
            else:
                self.briefing.restart(self.now, cue.arg)
            self.home = self.briefing
            self.switch(self.briefing)
            self.caption = f"Flight Director: {job_name(cue.arg)}"
            return 1.8
        if k == "_hangar":
            if self.hangar is None:
                self.hangar = HangarScene(self.now, cue.arg)
            else:
                self.hangar.restart(self.now, cue.arg)
            self.home = self.hangar
            self.switch(self.hangar)
            self.caption = f"{REVIEW_NAMES.get(cue.arg, cue.arg)}: the crew is inspecting the jet"
            return 2.6
        if k == "_chase":
            return self._chase(cue.task, cue.arg)

        if k == "director":
            job = cue.arg
            if job == "pr":
                return self._land("Touchdown: the Flight Director writes the PR")
            if job == "report":
                if self.phase != "landed":
                    self._land(None)
                self.caption = f"Mission complete: {self.world.merged}/{self.total} tasks merged; writing the report"
                return 2.0
            if air:
                self._comm("FD")
                self.caption = f"Flight Director job: {job_name(job)}"
                return 1.2
            if self.phase == "landed":
                self.caption = f"Flight Director job: {job_name(job)}"
                return 1.0
            return self._beam("FD", f"Tower to Flight Director: job {job}", (Cue("_plan", None, job),))
        if k == "director_done":
            if cue.arg == "report" and self.phase == "landed":     # the closing report is written: the run is over
                self.caption = f"Mission complete: {self.world.merged}/{self.total} tasks merged"
                return 0.0
            if self.phase == "ground" and self.briefing is not None and self.scene is self.briefing:
                self.briefing.finish(self.now)
                self.caption = f"Flight Director: {job_name(cue.arg)}: done"
                return 1.2
            return 0.0
        if k == "probe":
            if air:
                self._comm("CREW")
                self.caption = f"Test-launching the crew pair {cue.arg}"
                return 0.8
            if self.scene is self.tower and self.tower.target == "CREW":
                self.caption = f"Tower to crew: test launch of {cue.arg}"
                return 1.0
            return self._beam("CREW", f"Tower to crew: test launch of {cue.arg}")
        if k == "design_review":
            name = REVIEW_NAMES.get(cue.arg, cue.arg)
            if air:
                self._comm("RSO")
                self.caption = f"{name} dispatched"
                return 1.0
            return self._beam("HANGAR", f"Tower to hangar: {name} dispatched", (Cue("_hangar", None, cue.arg),),
                              REVIEW_CODES.get(cue.arg, "CREW"))
        if k == "design_result":
            name = REVIEW_NAMES.get(cue.arg, cue.arg)
            fails = self.review_fails.get(cue.arg, 0) + (0 if cue.ok else 1)
            self.review_fails[cue.arg] = 0 if cue.ok or fails >= 2 else fails
            if cue.ok:
                what = "inspection complete, report sent to the Flight Director"
            elif fails < 2:
                what = "the review worker failed; rerunning it"
            else:
                what = "the review worker failed again; the Flight Director decides what next"
            if self.phase != "ground":
                self.caption = f"{name}: {what}"
                return 0.5
            if self.hangar is None:
                self.hangar = HangarScene(self.now, cue.arg)
            self.home = self.hangar
            self.switch(self.hangar)
            self.hangar.kind = cue.arg
            self.hangar.finish(self.now, cue.ok)
            self.caption = f"{name}: {what}"
            return 2.0
        if k == "impl":
            if self.phase in ("ground", "landed"):
                self.phase = "air"
                self.switch(TakeoffScene(self.now))
                self.caption = f"{self.label(t)}: implementation starts. Cleared for takeoff!"
                return TAKEOFF_TIME, (Cue("_chase", t, cue.arg),)
            if self.phase == "approach":
                self.phase = "air"
                self.caption = f"Go-around: final fix {self.label(t)}"
                return self._chase(t, cue.arg, go_around=True)
            return self._chase(t, cue.arg)

        f = self.flight
        if k in ("settled", "gate", "review", "verdict", "void", "escalation", "escalation_start", "replanned",
                 "merged") and (f is None or self.scene is not f):
            # a task event while the dogfight is not on screen: get back in the air first
            if k == "merged":
                self.merged_ids.add(t)
            if self.phase == "ground":
                return 0.0
            self.switch(self._flight())
            f = self.flight
            if k == "merged":
                self.caption = f"{self.label(t)}: merged ({self.world.merged}/{self.total} down)"
                return 1.0
        if k == "settled":
            if cue.ok:
                what = {"impl": "implementation", "fix": "fix", "commit": "commit round"}.get(cue.arg, cue.arg)
                self.caption = f"{self.label(t)}: {what} done, closing in on the target"
                return 0.8
            f.do("miss", self.now)
            self.caption = f"{self.label(t)}: the worker failed. Missed!"
            return 2.2
        if k == "gate":
            if cue.ok:
                f.set_lock(1)
                self.caption = f"{self.label(t)}: gate passed. Lock 1/3"
                return 1.0
            f.do("miss", self.now)
            self.caption = f"{self.label(t)}: gate FAILED. Missed, re-engaging"
            return 2.2
        if k == "review":
            f.track()
            self.caption = f"{self.label(t)}: stage {cue.arg} review. Acquiring lock"
            return 1.0
        if k == "verdict":
            if cue.ok:
                f.set_lock(cue.arg + 1)
                done = cue.arg + 1 >= 3
                self.caption = (f"{self.label(t)}: stage {cue.arg} review PASS. "
                                + ("Target locked" if done else f"Lock {cue.arg + 1}/3"))
                return 1.2
            f.do("miss", self.now)
            self.caption = f"{self.label(t)}: stage {cue.arg} review asks for fixes. Missed, re-engaging"
            return 2.2
        if k == "void":
            f.tracking = False
            self.caption = f"{self.label(t)}: stage {cue.arg} reviewer void. Re-acquiring"
            return 0.6
        if k in ("escalation", "replanned"):
            if k == "replanned":
                self.dropped.add(t)
                self.add_task_ids(cue.arg)
                self.caption = f"{self.label(t)}: replanned into {' '.join(cue.arg)}. Hit! Still flying"
            else:
                self.caption = f"{self.label(t)}: escalation attempt {cue.arg}. Hit! Still flying"
            f.do("hit", self.now)
            return 2.4
        if k == "escalation_start":
            f.comm(self.now, "FD")
            self.caption = f"{self.label(t)}: escalation attempt {cue.arg}, back on the target"
            return 1.0
        if k == "merged":
            self.merged_ids.add(t)
            f.do("kill", self.now)
            self.caption = f"{self.label(t)}: merged. Splash one! ({self.world.merged}/{self.total} down)"
            return 3.2

        if k in ("final", "final_verdict", "final_void", "verify", "drift", "drift_result"):
            if self.phase in ("ground", "air"):
                self.phase = "approach"
                self.switch(self._approach())
            a = self._approach()
            if k == "final":
                a.set_check(0, "busy")
                self.caption = f"Final approach: final review {cue.arg}"
                return 1.6
            if k == "final_verdict":
                a.set_check(0, bool(cue.ok))
                self.caption = f"Final review {cue.arg}: {'PASS' if cue.ok else 'fixes needed'}"
                return 1.6
            if k == "final_void":
                a.set_check(0, "busy")
                self.caption = "Final reviewer void; another one is on the way"
                return 0.8
            if k == "verify":
                a.set_check(1, bool(cue.ok))
                self.caption = f"Verification {cue.arg}: {'passed' if cue.ok else 'FAILED'}"
                return 1.6
            if k == "drift":
                a.set_check(2, "busy")
                self.caption = "Drift check: comparing the spec with what was built"
                return 1.2
            a.set_check(2, bool(cue.ok))
            self.caption = f"Drift check {'done' if cue.ok else 'FAILED'}"
            return 1.6
        if k == "end":
            if self.phase != "landed":
                return self._land(f"Mission complete: {self.world.merged}/{self.total} tasks merged")
            self.caption = f"Mission complete: {self.world.merged}/{self.total} tasks merged"
            return 0.0
        return 0.0

    def _chase(self, t, kind, go_around=False):
        f = self._flight()
        self.switch(f)
        new = t != self.task or f.enemy_since is None or f.action == "kill"
        self.task = t
        if new:
            f.new_bandit(self.now)
        what = {"impl": "implementing", "fix": "fix round, re-engaging", "commit": "commit round"}.get(kind, kind)
        if not go_around:
            self.caption = f"{self.label(t)}: {what}" + (". Bandit ahead" if new and kind == "impl" else "")
        return 1.8 if new else 0.8

    def _land(self, caption):
        if self.landing is None:
            self.landing = LandingScene(self.now)
        self.phase = "landed"
        self.home = self.landing
        self.switch(self.landing)
        if caption:
            self.caption = caption
        return STOPPED + 1.0
