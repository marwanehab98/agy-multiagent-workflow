"""No model or harness names outside the adapters (design anti-goal; Global Constraints).

Only scripts/harnesses.py may name a model or harness; everything else gets them from the picks
and the detected catalog. PENDING lists paths still being rewritten (Task 11 empties it).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = re.compile(r"(?i)\b(opus|sonnet|haiku|fable|hydrogen|argon|gemini|antigravity|agy)\b")
CHECKED = ["SKILL.md", "references/coordinator.md", "references/architect.md", "references/templates", "scripts"]
ALLOWED = {"scripts/harnesses.py"}
PENDING = {"references/architect.md", "references/templates"}


def files():
    for entry in CHECKED:
        if entry in PENDING:
            continue
        path = ROOT / entry
        found = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for p in found:
            rel = p.relative_to(ROOT).as_posix()
            if rel not in ALLOWED and "__pycache__" not in p.parts:
                yield rel, p


class NoHardcodedModels(unittest.TestCase):
    def test_no_model_or_harness_names(self):
        hits = []
        for rel, p in files():
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if NAMES.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()[:120]}")
        if hits:
            self.fail("model or harness names found:\n" + "\n".join(hits))

    def test_checked_paths_exist(self):
        for entry in CHECKED + sorted(ALLOWED):
            self.assertTrue((ROOT / entry).exists(), entry)


if __name__ == "__main__":
    unittest.main()
