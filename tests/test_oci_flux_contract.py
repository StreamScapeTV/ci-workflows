from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OciProducerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )

    def test_flux_runner_targets_match_current_repository_image_families(self) -> None:
        product = self.contract["products"]["flux-runner-images"]
        targets = {target["target_id"]: target for target in product["targets"]}

        self.assertEqual({"runner-buildah", "runner-mobile"}, set(targets))
        self.assertEqual(
            "images/github-actions-runner-buildah",
            targets["runner-buildah"]["context_path"],
        )
        self.assertEqual(
            "images/github-actions-runner-buildah/Dockerfile",
            targets["runner-buildah"]["dockerfile_path"],
        )
        self.assertIsNone(targets["runner-buildah"]["smoke_script"])
        self.assertEqual(
            "images/github-actions-runner-mobile",
            targets["runner-mobile"]["context_path"],
        )
        self.assertEqual(
            "images/github-actions-runner-mobile/Dockerfile",
            targets["runner-mobile"]["dockerfile_path"],
        )
        self.assertIsNone(targets["runner-mobile"]["smoke_script"])

        serialized = json.dumps(product, sort_keys=True)
        for stale in (
            "images/buildah",
            "images/mobile",
            "images/portable",
            "runner-portable",
            "Containerfile",
            "verify-image.sh",
        ):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, serialized)

    def test_flux_publication_assertions_bind_capabilities_and_absence_policy(self) -> None:
        product = self.contract["products"]["flux-runner-images"]
        targets = {target["target_id"]: target for target in product["targets"]}
        publication = self.contract["publication_assertions"]["flux-runner-images"]
        self.assertEqual(set(targets), set(publication))

        self.assertEqual(
            {"buildah", "podman", "skopeo"},
            set(targets["runner-buildah"]["assertions"]["required_tools"]),
        )
        self.assertEqual(
            {"flutter", "java", "node"},
            set(targets["runner-mobile"]["assertions"]["required_tools"]),
        )
        for target_id in targets:
            self.assertIn("docker", targets[target_id]["assertions"]["forbidden_tools"])
            self.assertIn("dockerd", targets[target_id]["assertions"]["forbidden_tools"])
            forbidden_paths = set(publication[target_id]["forbidden_paths"])
            self.assertIn("/var/run/docker.sock", forbidden_paths)
            self.assertIn("/run/docker.sock", forbidden_paths)
            self.assertIn(
                "/home/runner/.config/containers/auth.json", forbidden_paths
            )
            self.assertIn("/root/.docker", forbidden_paths)
        self.assertEqual(
            {"/bin/bash", "/usr/bin/buildah", "/usr/bin/podman", "/usr/bin/skopeo"},
            set(publication["runner-buildah"]["required_executables"]),
        )
        self.assertEqual(
            {
                "/bin/bash",
                "/opt/flutter/bin/flutter",
                "/opt/java/openjdk/bin/java",
                "/usr/local/bin/node",
            },
            set(publication["runner-mobile"]["required_executables"]),
        )
        for row in publication.values():
            self.assertIsNone(row["healthcheck"])

    def test_real_product_runtime_config_is_bound_exactly(self) -> None:
        agent_state = self.contract["products"]["agent-state-image"]["targets"][0]
        agent_publication = self.contract["publication_assertions"][
            "agent-state-image"
        ]["agent-state-api"]
        self.assertEqual(
            ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7878"],
            agent_state["assertions"]["command"],
        )
        self.assertEqual(["7878/tcp"], agent_state["assertions"]["ports"])
        self.assertEqual(["uvicorn"], agent_state["assertions"]["required_tools"])
        self.assertEqual(
            ["/usr/local/bin/uvicorn"],
            agent_publication["required_executables"],
        )

        for target in self.contract["products"]["flux-runner-images"]["targets"]:
            self.assertEqual("runner", target["assertions"]["user"])
            self.assertEqual(["/bin/bash"], target["assertions"]["command"])

    def test_every_oci_product_uses_a_supported_workspace_profile(self) -> None:
        workspace = json.loads(
            (ROOT / "contracts/workspace-paths.json").read_text(encoding="utf-8")
        )
        profiles = set(workspace["profiles"])
        for product_id, product in self.contract["products"].items():
            with self.subTest(product=product_id):
                self.assertIn(product["workspace_profile"], profiles)
        self.assertEqual(
            "minimal",
            self.contract["products"]["ciw-oci-smoke"]["workspace_profile"],
        )
        for product_id in (
            "agent-state-image",
            "flux-runner-images",
            "iptv-backend-image",
        ):
            self.assertEqual(
                "container",
                self.contract["products"][product_id]["workspace_profile"],
            )

    def test_agent_state_target_matches_backend_image_context(self) -> None:
        target = self.contract["products"]["agent-state-image"]["targets"][0]
        self.assertEqual("backend", target["context_path"])
        self.assertEqual("backend/Dockerfile", target["dockerfile_path"])
        self.assertIsNone(target["smoke_script"])

    def test_backend_target_does_not_reference_retired_smoke_script(self) -> None:
        target = self.contract["products"]["iptv-backend-image"]["targets"][0]
        publication = self.contract["publication_assertions"][
            "iptv-backend-image"
        ]["iptv-backend"]
        self.assertEqual(".", target["context_path"])
        self.assertEqual("Dockerfile", target["dockerfile_path"])
        self.assertIsNone(target["smoke_script"])
        self.assertEqual(
            ["/app/docker/start.sh", "/usr/local/bin/python3"],
            publication["required_executables"],
        )
        self.assertNotIn(
            "docker/verify-image.sh",
            json.dumps(self.contract["products"]["iptv-backend-image"], sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
