import json
import unittest

from src.capabilities import build_capabilities


class CapabilitiesTest(unittest.TestCase):
    def test_describes_component_tools_and_skills(self) -> None:
        capabilities = build_capabilities()
        serialized = json.dumps(capabilities, ensure_ascii=False)

        self.assertEqual(capabilities["component"]["kind"], "cluster-operations-agent")
        self.assertEqual(capabilities["component"]["protocol_version"], "1.0")
        self.assertIn("cluster_snapshot", capabilities["data_sources"] + [
            tool["name"] for tool in capabilities["tools"]
        ])
        self.assertIn(
            "diagnose-service",
            [skill["id"] for skill in capabilities["skills"]],
        )
        self.assertNotIn("api_key", serialized.lower())
        self.assertNotIn("kubeconfig", serialized.lower())


if __name__ == "__main__":
    unittest.main()
