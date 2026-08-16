from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_DOC = ROOT / "docs/architecture/ownership-boundaries.md"


class OwnershipBoundaryDocumentationTests(unittest.TestCase):
    def test_runner_image_product_and_live_flux_authority_are_separate(self) -> None:
        guide = OWNERSHIP_DOC.read_text(encoding="utf-8")

        self.assertIn(
            "runner-image Dockerfiles, image composition, reviewed upstream/tool pins "
            "and freshness, build, validation, publication, and registry read-back",
            guide,
        )
        self.assertIn(
            "live ARC desired state, selected immutable runner image, labels, resources, "
            "storage, runtime service-account or sidecar wiring, canary, health, rollback, "
            "and cluster policy",
            guide,
        )
        self.assertNotIn(
            "runner product definitions, exact bases/upstreams, desired scale-set "
            "selection, quotas, and live infrastructure policy",
            guide,
        )

    def test_flux_keeps_cluster_and_selection_authority(self) -> None:
        guide = OWNERSHIP_DOC.read_text(encoding="utf-8")

        for phrase in (
            "Kubernetes and Flux desired state",
            "cluster credentials, environments, live reconciliation, health, canary, "
            "selection, rollback, and incident acceptance",
            "Publishing a runner image or chart never mutates the live scale set automatically",
        ):
            self.assertIn(phrase, guide)


if __name__ == "__main__":
    unittest.main()
