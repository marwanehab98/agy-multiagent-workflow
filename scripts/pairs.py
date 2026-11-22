"""The pair format `<harness>:<model>@<effort>` and the escalation ladder (design 2.2, 2.3).

Pure functions, no I/O. A pair splits at the first `:` and the last `@`; a model with no
effort choice uses `@default`.
"""
import re
from typing import NamedTuple

EFFORTS = ("low", "medium", "high", "xhigh", "max")


class Pair(NamedTuple):
    harness: str
    model: str
    effort: str

    def __str__(self):
        return f"{self.harness}:{self.model}@{self.effort}"


def parse_pair(text):
    harness, colon, rest = text.partition(":")
    model, at, effort = rest.rpartition("@")
    if not (colon and at and harness and model and effort):
        raise ValueError(f"bad pair {text!r}: expected <harness>:<model>@<effort>")
    return Pair(harness, model, effort)


def pair_key(p):
    return re.sub(r"[^A-Za-z0-9]", "_", str(p))


def ladder(crew, top_effort, director, first):
    """Rungs after the first attempt: crew at its top effort, then the flight director pair.

    A rung is dropped when it equals `first`, an earlier rung, or (non-last rung) the flight director.
    """
    candidates = [Pair(crew.harness, crew.model, top_effort), director]
    rungs = []
    for i, rung in enumerate(candidates):
        last = i == len(candidates) - 1
        if rung == first or rung in rungs or (not last and rung == director):
            continue
        rungs.append(rung)
    return rungs
