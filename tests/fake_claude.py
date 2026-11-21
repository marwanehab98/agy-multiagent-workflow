"""Fake `claude` CLI for unit tests. Reads FAKE_CLAUDE_WORLD JSON and executes scripted headless `-p` behaviors."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_cli import run_fake_cli  # noqa: E402


def _ok_payload(sid, result):
    return {"type": "result", "subtype": "success", "is_error": False, "result": result, "session_id": sid}


def _err_payload(sid, errors, subtype="error_during_execution"):
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": True,
        "result": "",
        "session_id": sid,
        "errors": errors if isinstance(errors, list) else [errors],
    }


def _response_payload(sid, beh):
    is_err = bool(beh.get("is_error", False))
    subtype = beh.get("subtype", "error_during_execution" if is_err else "success")
    rc = beh.get("rc", 1 if (is_err or subtype != "success") else 0)
    payload = {"type": "result", "subtype": subtype, "is_error": is_err, "result": beh.get("text", ""), "session_id": sid}
    if "errors" in beh:
        payload["errors"] = beh["errors"]
    if "error" in beh:
        payload["error"] = beh["error"]
    return rc, payload


def main():
    return run_fake_cli(
        world_env="FAKE_CLAUDE_WORLD",
        prompt_flags=("-p", "--print"),
        resume_flags=("-r", "--resume"),
        resume_dest="resume",
        id_prefix="sess",
        expired_keys=("expired_sessions", "expired_conversations"),
        expired_error="No conversation found with session ID: {id}",
        usage_error="You've hit your limit · resets 2am (overloaded_error)",
        ok_payload=_ok_payload,
        err_payload=_err_payload,
        response_payload=_response_payload,
    )


if __name__ == "__main__":
    sys.exit(main())
