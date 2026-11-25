# Observation deck: the flight animation

A pixel-art view of a mission-control run, driven by `<RUN_DIR>/progress.log`. Python standard library only.
One engine (`engine.py`, `scenes.py`, `art.py`) renders 96x54-pixel frames at 12 fps with a caption; the terminal
and the web page only display them. `cues.py` turns progress.log lines into cues using the patterns in
`scripts/watch.py`.

During a run the Capsule Communicator opens it for you ("Observation deck" in
`references/capsule-communicator.md`): it prints a link named "Observation deck" once, and the page shows that run
live. Each run has its own port, saved in `<RUN_DIR>/observation-deck.port`. The two commands behind that:

```sh
python3 -I animation/play.py --link <RUN_DIR>   # prints [Observation deck](http://localhost:<port>)
python3 -I animation/play.py --deck <RUN_DIR>   # serves the run's animation there until stopped
```

In the Antigravity app the plugin also ships a side pane (`sidecars/observation_deck/`): one player for every
run on the machine, and the Capsule Communicator's link opens that pane. A pane shows the run of the conversation
it is open in (the run folder that conversation's transcript named last; see `pane.py`), and a picker offers the
other recent runs. To see the same page without Antigravity:

```sh
python3 -I animation/play.py --pane --port 8766
```

By hand, from the repo root:

```sh
# terminal (full size at 96x28 or more; a smaller one gets the picture shrunk to fit, down to 48x14;
# below that it shows the caption until the window is widened; when piped, it prints only the captions)
python3 -I animation/play.py --replay animation/sample-progress.log
python3 -I animation/play.py --replay animation/sample-progress.log --speed 2
python3 -I animation/play.py --follow <RUN_DIR>/progress.log
python3 -I animation/play.py --demo

# browser: open the printed URL; the port is --port, else $ANTIGRAVITY_SIDECAR_WEB_PORT, else 8765
python3 -I animation/play.py --replay animation/sample-progress.log --web --port 8765

# key frames as PNG files (no display), with captions.txt
python3 -I animation/play.py --demo --png-dir /tmp/frames
```

Ctrl-C stops any of them. `sample-progress.log` is a recorded five-task run (one failed gate, one stage 2 FIX,
one escalation) built from the real log formats. Tests: `tests/test_animation.py`.
