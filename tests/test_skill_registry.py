import tempfile
import unittest
from pathlib import Path

from src.skill_registry import SkillRegistry


def _write_skill(root: Path, folder: str, content: str) -> None:
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


class SkillRegistryTest(unittest.TestCase):
    def _registry(self, root: Path) -> SkillRegistry:
        _write_skill(
            root,
            "diagnose",
            """---
id: diagnose
name: diagnose
triggers: [OOM]
required_tools: [cluster_snapshot]
allowed_tools: [cluster_snapshot]
priority: 40
status: enabled
---
Diagnose.
""",
        )
        _write_skill(
            root,
            "remediate",
            """---
id: remediate
name: remediate
triggers: [restart]
required_tools: [restart_pod]
allowed_tools: [restart_pod]
priority: 80
conflict_policy: exclusive
status: enabled
---
Remediate.
""",
        )
        return SkillRegistry(
            str(root), known_tools={"cluster_snapshot", "restart_pod"}
        )

    def test_matches_and_restricts_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self._registry(Path(temp_dir))
            selection = registry.select("service has OOM")

        self.assertEqual([skill["id"] for skill in selection.active], ["diagnose"])
        self.assertEqual(selection.allowed_tools, frozenset({"cluster_snapshot"}))

    def test_exclusive_skill_wins_by_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self._registry(Path(temp_dir))
            selection = registry.select("restart the service after OOM")

        self.assertEqual([skill["id"] for skill in selection.active], ["remediate"])
        self.assertEqual(selection.allowed_tools, frozenset({"restart_pod"}))


if __name__ == "__main__":
    unittest.main()
