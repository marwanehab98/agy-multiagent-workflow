"""No model or harness names outside the adapters (design anti-goal; Global Constraints).

Only scripts/harnesses.py may name a model or harness; everything else gets them from the picks
and the detected catalog. SKILL.md's frontmatter description is exempt (ruling R9): its harness words
only decide when the skill loads, and users say "run this through Antigravity". PENDING lists paths
still being rewritten; it is empty since Task 11 rewrote architect.md and the templates.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = re.compile(r"(?i)\b(opus|sonnet|haiku|fable|hydrogen|argon|gemini|antigravity|agy)\b")
CHECKED = ["SKILL.md", "references/coordinator.md", "references/architect.md", "references/templates", "scripts"]
ALLOWED = {"scripts/harnesses.py"}
PENDING = set()


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


def description_line(path):
    """The 1-based line number of the frontmatter `description:` line, or None."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return None
    for n, line in enumerate(lines[1:], 2):
        if line == "---":
            return None
        if line.startswith("description:"):
            return n
    return None


class NoHardcodedModels(unittest.TestCase):
    def test_no_model_or_harness_names(self):
        hits = []
        for rel, p in files():
            exempt = description_line(p) if rel == "SKILL.md" else None
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if n != exempt and NAMES.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()[:120]}")
        if hits:
            self.fail("model or harness names found:\n" + "\n".join(hits))

    def test_description_keeps_harness_trigger_words(self):
        n = description_line(ROOT / "SKILL.md")
        line = (ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()[n - 1]
        for word in ("Orca", "Antigravity", "Gemini"):
            self.assertIn(word, line)

    def test_checked_paths_exist(self):
        for entry in CHECKED + sorted(ALLOWED):
            self.assertTrue((ROOT / entry).exists(), entry)


if __name__ == "__main__":
    unittest.main()
