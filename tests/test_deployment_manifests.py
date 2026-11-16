import unittest
from pathlib import Path

import yaml


class KubernetesManifestTest(unittest.TestCase):
    def test_contains_core_resources(self) -> None:
        manifest = Path("deploy/kubernetes/ops-ai.yaml").read_text(encoding="utf-8")
        resources = list(yaml.safe_load_all(manifest))
        kinds = [resource.get("kind") for resource in resources if resource]

        self.assertEqual(
            kinds,
            [
                "ServiceAccount",
                "ConfigMap",
                "Role",
                "RoleBinding",
                "PersistentVolumeClaim",
                "PersistentVolumeClaim",
                "Deployment",
                "Service",
            ],
        )
        deployment = next(
            resource for resource in resources if resource.get("kind") == "Deployment"
        )
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(
            deployment["spec"]["strategy"]["type"],
            "Recreate",
        )


if __name__ == "__main__":
    unittest.main()
