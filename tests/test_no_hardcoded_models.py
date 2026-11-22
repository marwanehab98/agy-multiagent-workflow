"""No model names outside the adapter (Global Constraints).

Only scripts/harnesses.py may name a model; everything else gets them from the picks
and the detected catalog. SKILL.md's frontmatter description is exempt for trigger words.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = re.compile(r"(?i)\b(opus|sonnet|haiku|fable|flash|hydrogen|argon|gemini|claude)\b")
EXEMPT = re.compile(r"\bCLAUDE\.md\b")  # the project instructions file, exactly as spelled; not a model/harness name
CHECKED = ["skills", "agents", "resources", "references/capsule-communicator.md", "references/flight-director.md", "references/templates", "scripts"]
ALLOWED = {"scripts/harnesses.py"}
TRIGGER_SKILLS = {"skills/mission/SKILL.md", "skills/launch-plan/SKILL.md"}


def files():
    for entry in CHECKED:
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
    def test_pattern_covers_models_but_not_claude_md(self):
        for line in ("start a claude worker", "use Claude here", "gemini-3-flash", "Flash is cheap"):
            self.assertTrue(NAMES.search(EXEMPT.sub("", line)), line)
        for line in ("read CLAUDE.md first", "the project's CLAUDE.md and AGENTS.md"):
            self.assertFalse(NAMES.search(EXEMPT.sub("", line)), line)
        self.assertTrue(NAMES.search(EXEMPT.sub("", "read claude.md first")))  # only the exact spelling is exempt

    def test_no_model_names(self):
        hits = []
        for rel, p in files():
            exempt = description_line(p) if rel in TRIGGER_SKILLS else None
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if n != exempt and NAMES.search(EXEMPT.sub("", line)):
                    hits.append(f"{rel}:{n}: {line.strip()[:120]}")
        if hits:
            self.fail("model names found:\n" + "\n".join(hits))

    def test_description_keeps_harness_trigger_words(self):
        for rel in sorted(TRIGGER_SKILLS):
            p = ROOT / rel
            n = description_line(p)
            line = p.read_text(encoding="utf-8").splitlines()[n - 1]
            for word in ("Antigravity", "Gemini", "agy"):
                self.assertIn(word, line, rel)

    def test_checked_paths_exist(self):
        for entry in CHECKED + sorted(ALLOWED):
            self.assertTrue((ROOT / entry).exists(), entry)


if __name__ == "__main__":
    unittest.main()
