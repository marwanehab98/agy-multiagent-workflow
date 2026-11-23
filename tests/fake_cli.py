"""Shared scripted runner for fake headless CLIs (`fake_agy.py` and `fake_claude.py`)."""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def git(*args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True, text=True)


def output_file_from_prompt(prompt):
    for pat in (
        r"Write your review to ([^\s:]+?)(?::|\s|$)",
        r"Write your ranked questions .*? to ([^\s]+)",
        r"Write every finding to ([^\s,]+)",
        r"Write the synthesized findings to ([^\s,]+)",
        r"Write the consolidated findings to ([^\s,]+)",
        r"Write every finding .*? to ([^\s,]+)",
    ):
        m = re.search(pat, prompt)
        if m:
            return Path(m.group(1).rstrip(".,;:"))
    return None


def match_rule(rule, model, effort):
    return rule.get("model") == model and rule.get("effort", "default") == (effort or "default")


def run_fake_cli(
    *,
    world_env,
    prompt_flags,
    resume_flags,
    resume_dest,
    id_prefix,
    expired_keys,
    expired_error,
    usage_error,
    ok_payload,
    err_payload,
    response_payload,
):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(*prompt_flags, dest="prompt")
    p.add_argument("--output-format", default="text")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--dangerously-skip-permissions", action="store_true")
    p.add_argument("--permission-mode", default="")
    p.add_argument("--allowedTools", default="")
    p.add_argument("--strict-mcp-config", action="store_true")
    p.add_argument("--sandbox", action="store_true")
    p.add_argument("--settings", default="")
    p.add_argument("--add-dir", action="append", default=[])
    p.add_argument("--model", default="")
    p.add_argument("--effort", default="")
    p.add_argument(*resume_flags, dest=resume_dest, default="")
    ns = p.parse_args()

    world_path = Path(os.environ[world_env])
    w = json.loads(world_path.read_text(encoding="utf-8"))
    w["seq"] = w.get("seq", 0) + 1
    seq = w["seq"]
    resume_val = getattr(ns, resume_dest)

    w.setdefault("calls", []).append({
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "prompt": ns.prompt,
        "output_format": ns.output_format,
        "verbose": ns.verbose,
        "model": ns.model,
        "effort": ns.effort,
        "skip_permissions": ns.dangerously_skip_permissions,
        "permission_mode": ns.permission_mode,
        "allowed_tools": ns.allowedTools,
        "sandbox": ns.sandbox,
        resume_dest: resume_val,
    })

    def save_world():
        world_path.write_text(json.dumps(w), encoding="utf-8")

    def emit(rc, payload, prefix="", pretty=False, stream_events=None, wrap_event=None):
        save_world()
        if prefix:
            print(prefix, flush=True)
        for ev in (stream_events or []):
            print(json.dumps(ev), flush=True)
        final_obj = {wrap_event: payload, "event": wrap_event} if wrap_event else payload
        print(json.dumps(final_obj, indent=2 if pretty else None), flush=True)
        return rc

    effective_prompt = ns.prompt or ""
    ptr = re.fullmatch(r"Read and follow all instructions in (.+?) exactly\.", effective_prompt)
    if ptr and Path(ptr.group(1)).is_file():
        effective_prompt = Path(ptr.group(1)).read_text(encoding="utf-8")

    if any(match_rule(r, ns.model, ns.effort) for r in w.get("reject_rules", [])):
        return emit(1, err_payload("", f"invalid model selection (--model {ns.model!r} --effort {ns.effort!r}): does not support effort {ns.effort}"))

    if w.get("launch_rules") is not None:
        if not any(match_rule(r, ns.model, ns.effort) for r in w["launch_rules"]):
            return emit(1, err_payload("", f"Model '{ns.model}' is not available for the current authentication method"))

    expired = set()
    for k in expired_keys:
        expired.update(w.get(k, []))
    if resume_val and resume_val in expired:
        return emit(1, err_payload("", expired_error.format(id=resume_val)))

    beh = w.get("behaviors", []).pop(0) if w.get("behaviors") else {"do": "ok"}
    do = beh["do"] if isinstance(beh, dict) else beh
    cid = (
        (beh.get("session_id") or beh.get("conv_id"))
        if isinstance(beh, dict) and (beh.get("session_id") or beh.get("conv_id"))
        else (resume_val or f"{id_prefix}_{seq}")
    )

    if do == "ok":
        return emit(0, ok_payload(cid, "OK\n"))

    if do == "ok-edit":
        Path("probe_edit.txt").write_text("stray edit\n", encoding="utf-8")
        return emit(0, ok_payload(cid, "OK\n"))

    if do in ("fail", "fail-formatted"):
        line = "**FAILED:** missing tool xyz" if do == "fail-formatted" else "FAILED: could not make the test pass"
        return emit(0, ok_payload(cid, f"{line}\nAssertionError: 1 != 2\n"))

    if do in ("error", "stopped"):
        return emit(1, err_payload(cid, "worker crashed unexpectedly"))

    if do in ("usage", "usage-edit"):
        if do == "usage-edit":
            Path("stray_usage_edit.txt").write_text("stray edit before quota\n", encoding="utf-8")
        return emit(1, err_payload(cid, usage_error))

    if do in ("ask", "ask-formatted"):
        q = beh.get("question", "Which edge case behavior should I use?") if isinstance(beh, dict) else "Which edge case behavior should I use?"
        text = f"`ASK: {q}`\n" if do == "ask-formatted" else f"ASK: {q}\n"
        return emit(0, ok_payload(cid, text))

    if do == "response":
        if isinstance(beh, dict) and beh.get("spawn_child_sleep"):
            subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({float(beh['spawn_child_sleep'])})"])
        if isinstance(beh, dict) and beh.get("sleep"):
            save_world()
            time.sleep(float(beh["sleep"]))
        if isinstance(beh, dict) and beh.get("edit"):
            p_edit = Path(beh["edit"])
            p_edit.parent.mkdir(parents=True, exist_ok=True)
            p_edit.write_text(beh.get("edit_text", "edited\n"), encoding="utf-8")
        if isinstance(beh, dict) and ("raw_stdout" in beh or "raw_stderr" in beh):
            save_world()
            if beh.get("raw_stdout"):
                print(beh["raw_stdout"])
            if beh.get("raw_stderr"):
                print(beh["raw_stderr"], file=sys.stderr)
            return beh.get("rc", 1)
        rc, payload = response_payload(cid, beh if isinstance(beh, dict) else {})
        prefix = beh.get("stdout_prefix", "") if isinstance(beh, dict) else ""
        pretty = bool(beh.get("pretty")) if isinstance(beh, dict) else False
        stream_events = beh.get("stream_events") if isinstance(beh, dict) else None
        wrap_event = beh.get("wrap_event") if isinstance(beh, dict) else None
        return emit(rc, payload, prefix=prefix, pretty=pretty, stream_events=stream_events, wrap_event=wrap_event)

    if do == "commit":
        f = Path(f"work_{seq}.txt")
        f.write_text(f"work {seq}\n", encoding="utf-8")
        git("add", str(f))
        git("commit", "-qm", f"work {seq}")
        return emit(0, ok_payload(cid, f"committed {f}\n"))

    if do == "dirty":
        Path(f"dirty_{seq}.txt").write_text("uncommitted\n", encoding="utf-8")
        return emit(0, ok_payload(cid, "done\n"))

    if do == "commit-all":
        git("add", "-A")
        git("commit", "-qm", "commit all")
        return emit(0, ok_payload(cid, "committed all\n"))

    if do == "commit-and-junk":
        Path("work.py").write_text("x = 1\n", encoding="utf-8")
        git("add", "work.py")
        git("commit", "-qm", "work.py")
        Path("__pycache__").mkdir(exist_ok=True)
        Path("__pycache__/work.cpython-313.pyc").write_text("pyc\n", encoding="utf-8")
        return emit(0, ok_payload(cid, "committed work.py\n"))

    if do == "commit-broken":
        Path("BROKEN").write_text("broken\n", encoding="utf-8")
        git("add", "BROKEN")
        git("commit", "-qm", "broken")
        return emit(0, ok_payload(cid, "committed BROKEN\n"))

    if do == "unbreak":
        if Path("BROKEN").exists():
            git("rm", "-q", "BROKEN")
            git("commit", "-qm", "unbreak")
        return emit(0, ok_payload(cid, "unbroken\n"))

    if do == "nocommit":
        return emit(0, ok_payload(cid, "done\n"))

    if do in ("pass", "fix", "noverdict", "edit-and-pass", "pass-body-only", "fix-formatted"):
        out = output_file_from_prompt(effective_prompt)
        if do == "edit-and-pass":
            Path("stray_review_edit.txt").write_text("oops\n", encoding="utf-8")
        verdict = {"pass": "VERDICT: PASS", "edit-and-pass": "VERDICT: PASS",
                   "fix": "VERDICT: FIX", "fix-formatted": "**VERDICT: FIX**",
                   "noverdict": "Looks fine.", "pass-body-only": "VERDICT: PASS"}[do]
        if out and do != "pass-body-only":
            out.parent.mkdir(parents=True, exist_ok=True)
            body = "Critical: bug in foo.py:1.\n" if do == "fix-formatted" else "Review notes.\n"
            out.write_text(f"{body}{verdict}\n", encoding="utf-8")
        resp = "Summary only." if do == "fix-formatted" else (
            f"Review complete.\n{verdict}\n" if do != "noverdict" else "Review complete.\nLooks fine.\n"
        )
        return emit(0, ok_payload(cid, resp))

    if do in ("write-output", "write-empty"):
        out = output_file_from_prompt(effective_prompt)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("" if do == "write-empty" else "Important: x.md:1 unclear.\n", encoding="utf-8")
        if isinstance(beh, dict) and beh.get("edit"):
            Path("stray_design_edit.txt").write_text("oops\n", encoding="utf-8")
        return emit(0, ok_payload(cid, f"written {out}\n"))

    return emit(1, err_payload(cid, f"unknown fake behavior {do}"))
