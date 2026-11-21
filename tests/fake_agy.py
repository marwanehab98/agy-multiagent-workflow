"""Fake `agy` CLI for unit tests. Reads FAKE_AGY_WORLD JSON and executes scripted headless `-p` behaviors."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_cli import run_fake_cli  # noqa: E402


def _response_payload(cid, beh):
    status = beh.get("status", "SUCCESS")
    rc = beh.get("rc", 0 if status == "SUCCESS" else 1)
    return rc, {
        "conversation_id": cid,
        "status": status,
        "response": beh.get("text", ""),
        "error": beh.get("error", ""),
    }


def main():
    return run_fake_cli(
        world_env="FAKE_AGY_WORLD",
        prompt_flags=("-p", "--print", "--prompt"),
        resume_flags=("--conversation",),
        resume_dest="conversation",
        id_prefix="conv",
        expired_keys=("expired_conversations",),
        expired_error="conversation {id} not found or expired",
        usage_error="usageLimitReached: You've hit your 5-hour limit",
        ok_payload=lambda cid, resp: {"conversation_id": cid, "status": "SUCCESS", "response": resp},
        err_payload=lambda cid, err: {"conversation_id": cid, "status": "ERROR", "response": "", "error": err},
        response_payload=_response_payload,
    )


if __name__ == "__main__":
    sys.exit(main())
