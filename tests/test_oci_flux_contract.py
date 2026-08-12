from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OciFluxContractTests(unittest.TestCase):
    def test_flux_runner_targets_match_current_repository_image_families(self) -> None:
        contract = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        product = contract["products"]["flux-runner-images"]
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


if __name__ == "__main__":
    unittest.main()
