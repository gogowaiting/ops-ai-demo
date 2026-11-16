import tempfile
import unittest
from pathlib import Path

from src.skill_loader import SkillValidationError, load_skills


def _write_skill(root: Path, folder: str, content: str) -> None:
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


class SkillLoaderTest(unittest.TestCase):
    def test_loads_extended_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_skill(
                root,
                "diagnose",
                """---
id: diagnose-oom
name: diagnose_oom
version: 2.1.0
description: Diagnose OOM
risk: low
write_op: false
triggers: [OOM, CrashLoopBackOff]
required_tools: [cluster_snapshot]
allowed_tools: [cluster_snapshot, wiki_search]
approval: not_required
priority: 60
conflict_policy: merge
status: enabled
---
Check metrics, logs, and pods.
""",
            )
            skills = load_skills(temp_dir, known_tools={"cluster_snapshot", "wiki_search"})

        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["id"], "diagnose-oom")
        self.assertEqual(skills[0]["version"], "2.1.0")
        self.assertEqual(skills[0]["priority"], 60)
        self.assertEqual(skills[0]["body"], "Check metrics, logs, and pods.")
        self.assertEqual(len(skills[0]["content_hash"]), 64)

    def test_rejects_unknown_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_skill(
                root,
                "invalid",
                """---
name: invalid_skill
triggers: [alert]
required_tools: [missing_tool]
---
Body
""",
            )
            with self.assertRaises(SkillValidationError):
                load_skills(temp_dir, known_tools={"cluster_snapshot"})

    def test_rejects_duplicate_names(self) -> None:
        content = """---
name: duplicate
triggers: [alert]
required_tools: []
---
Body
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_skill(root, "one", content)
            _write_skill(root, "two", content)
            with self.assertRaises(SkillValidationError):
                load_skills(temp_dir)


if __name__ == "__main__":
    unittest.main()
