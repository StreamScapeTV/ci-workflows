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

    def test_agent_state_target_matches_backend_image_context(self) -> None:
        target = self.contract["products"]["agent-state-image"]["targets"][0]
        self.assertEqual("backend", target["context_path"])
        self.assertEqual("backend/Dockerfile", target["dockerfile_path"])
        self.assertIsNone(target["smoke_script"])

    def test_backend_target_does_not_reference_retired_smoke_script(self) -> None:
        target = self.contract["products"]["iptv-backend-image"]["targets"][0]
        self.assertEqual(".", target["context_path"])
        self.assertEqual("Dockerfile", target["dockerfile_path"])
        self.assertIsNone(target["smoke_script"])
        self.assertNotIn(
            "docker/verify-image.sh",
            json.dumps(self.contract["products"]["iptv-backend-image"], sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
