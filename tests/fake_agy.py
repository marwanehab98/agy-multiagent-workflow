"""Fake `agy` CLI for unit tests. Reads FAKE_AGY_WORLD JSON and executes scripted headless `-p` behaviors."""
import argparse
import json
import os
import re
import subprocess
import sys
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


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-p", "--print", "--prompt", dest="prompt")
    p.add_argument("--output-format", default="text")
    p.add_argument("--dangerously-skip-permissions", action="store_true")
    p.add_argument("--model", default="")
    p.add_argument("--effort", default="")
    p.add_argument("--conversation", default="")
    ns, rest = p.parse_known_args()

    world_path = Path(os.environ["FAKE_AGY_WORLD"])
    w = json.loads(world_path.read_text(encoding="utf-8"))
    w["seq"] = w.get("seq", 0) + 1
    seq = w["seq"]

    w.setdefault("calls", []).append({
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "prompt": ns.prompt,
        "model": ns.model,
        "effort": ns.effort,
        "conversation": ns.conversation,
    })

    def save_world():
        world_path.write_text(json.dumps(w), encoding="utf-8")

    def emit(rc, payload, prefix=""):
        save_world()
        if prefix:
            print(prefix)
        print(json.dumps(payload))
        return rc

    effective_prompt = ns.prompt or ""
    ptr = re.fullmatch(r"Read and follow all instructions in (.+?) exactly\.", effective_prompt)
    if ptr and Path(ptr.group(1)).is_file():
        effective_prompt = Path(ptr.group(1)).read_text(encoding="utf-8")

    if any(match_rule(r, ns.model, ns.effort) for r in w.get("reject_rules", [])):
        return emit(1, {
            "conversation_id": "",
            "status": "ERROR",
            "response": "",
            "error": f"invalid model selection (--model {ns.model!r} --effort {ns.effort!r}): does not support effort {ns.effort}",
        })

    if w.get("launch_rules") is not None:
        if not any(match_rule(r, ns.model, ns.effort) for r in w["launch_rules"]):
            return emit(1, {
                "conversation_id": "",
                "status": "ERROR",
                "response": "",
                "error": f"Model '{ns.model}' is not available for the current authentication method",
            })

    if ns.conversation and ns.conversation in w.get("expired_conversations", []):
        return emit(1, {
            "conversation_id": "",
            "status": "ERROR",
            "response": "",
            "error": f"conversation {ns.conversation} not found or expired",
        })

    beh = w.get("behaviors", []).pop(0) if w.get("behaviors") else {"do": "ok"}
    do = beh["do"] if isinstance(beh, dict) else beh
    conv_id = beh.get("conv_id") if isinstance(beh, dict) and beh.get("conv_id") else (ns.conversation or f"conv_{seq}")

    if do == "ok":
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "OK\n"})

    if do == "ok-edit":
        Path("probe_edit.txt").write_text("stray edit\n", encoding="utf-8")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "OK\n"})

    if do in ("fail", "fail-formatted"):
        line = "**FAILED:** missing tool xyz" if do == "fail-formatted" else "FAILED: could not make the test pass"
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS",
                        "response": f"{line}\nAssertionError: 1 != 2\n"})

    if do in ("error", "stopped"):
        return emit(1, {"conversation_id": conv_id, "status": "ERROR",
                        "response": "", "error": "worker crashed unexpectedly"})

    if do in ("usage", "usage-edit"):
        if do == "usage-edit":
            Path("stray_usage_edit.txt").write_text("stray edit before quota\n", encoding="utf-8")
        return emit(1, {"conversation_id": conv_id, "status": "ERROR",
                        "response": "", "error": "usageLimitReached: You've hit your 5-hour limit"})

    if do in ("ask", "ask-formatted"):
        q = beh.get("question", "Which edge case behavior should I use?") if isinstance(beh, dict) else "Which edge case behavior should I use?"
        text = f"`ASK: {q}`\n" if do == "ask-formatted" else f"ASK: {q}\n"
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": text})

    if do == "response":
        if isinstance(beh, dict) and beh.get("edit"):
            p_edit = Path(beh["edit"])
            p_edit.parent.mkdir(parents=True, exist_ok=True)
            p_edit.write_text(beh.get("edit_text", "edited\n"), encoding="utf-8")
        status = beh.get("status", "SUCCESS") if isinstance(beh, dict) else "SUCCESS"
        rc = beh.get("rc", 0 if status == "SUCCESS" else 1) if isinstance(beh, dict) else 0
        prefix = beh.get("stdout_prefix", "") if isinstance(beh, dict) else ""
        return emit(rc, {
            "conversation_id": conv_id,
            "status": status,
            "response": beh.get("text", "") if isinstance(beh, dict) else "",
            "error": beh.get("error", "") if isinstance(beh, dict) else "",
        }, prefix=prefix)

    if do == "commit":
        f = Path(f"work_{seq}.txt")
        f.write_text(f"work {seq}\n", encoding="utf-8")
        git("add", str(f))
        git("commit", "-qm", f"work {seq}")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": f"committed {f}\n"})

    if do == "dirty":
        Path(f"dirty_{seq}.txt").write_text("uncommitted\n", encoding="utf-8")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "done\n"})

    if do == "commit-all":
        git("add", "-A")
        git("commit", "-qm", "commit all")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "committed all\n"})

    if do == "commit-and-junk":
        Path("work.py").write_text("x = 1\n", encoding="utf-8")
        git("add", "work.py")
        git("commit", "-qm", "work.py")
        Path("__pycache__").mkdir(exist_ok=True)
        Path("__pycache__/work.cpython-313.pyc").write_text("pyc\n", encoding="utf-8")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "committed work.py\n"})

    if do == "commit-broken":
        Path("BROKEN").write_text("broken\n", encoding="utf-8")
        git("add", "BROKEN")
        git("commit", "-qm", "broken")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "committed BROKEN\n"})

    if do == "unbreak":
        if Path("BROKEN").exists():
            git("rm", "-q", "BROKEN")
            git("commit", "-qm", "unbreak")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "unbroken\n"})

    if do == "nocommit":
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": "done\n"})

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
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": resp})

    if do in ("write-output", "write-empty"):
        out = output_file_from_prompt(effective_prompt)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("" if do == "write-empty" else "Important: x.md:1 unclear.\n", encoding="utf-8")
        if isinstance(beh, dict) and beh.get("edit"):
            Path("stray_design_edit.txt").write_text("oops\n", encoding="utf-8")
        return emit(0, {"conversation_id": conv_id, "status": "SUCCESS", "response": f"written {out}\n"})

    return emit(1, {"conversation_id": conv_id, "status": "ERROR", "response": "", "error": f"unknown fake behavior {do}"})


if __name__ == "__main__":
    sys.exit(main())
