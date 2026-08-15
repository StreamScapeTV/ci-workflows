from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows import runners


ROOT = Path(__file__).resolve().parents[1]


class HelmRunnerSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = runners.load_runner_contract(ROOT)

    def test_validation_stays_portable_and_publication_uses_buildah_tiny(self) -> None:
        validation = runners.resolve_runner_profile(
            self.contract,
            workflow_api="helm.validate",
            source_trust="trusted-pr",
            requested_profile="portable",
        )
        publication = runners.resolve_runner_profile(
            self.contract,
            workflow_api="helm.publish",
            source_trust="trusted-exact",
            requested_profile="buildah-tiny",
        )
        self.assertEqual(validation.profile, "portable")
        self.assertEqual(validation.runs_on, ("linux", "amd64", "general"))
        self.assertEqual(publication.profile, "buildah-tiny")
        self.assertEqual(
            publication.runs_on,
            ("linux", "amd64", "buildah", "tiny"),
        )
        self.assertIn("chart_digest", publication.evidence_fields)

    def test_publication_runner_is_trusted_exact_only(self) -> None:
        for trust in ("untrusted-fork", "trusted-pr"):
            with self.subTest(trust=trust):
                with self.assertRaisesRegex(
                    runners.RunnerContractError,
                    "source-trust-not-allowed",
                ):
                    runners.resolve_runner_profile(
                        self.contract,
                        workflow_api="helm.publish",
                        source_trust=trust,
                        requested_profile="buildah-tiny",
                    )

    def test_portable_cannot_be_selected_for_helm_publication(self) -> None:
        with self.assertRaisesRegex(
            runners.RunnerContractError,
            "profile-not-allowed",
        ):
            runners.resolve_runner_profile(
                self.contract,
                workflow_api="helm.publish",
                source_trust="trusted-exact",
                requested_profile="portable",
            )

    def test_generated_mapping_matches_contract_projection(self) -> None:
        expected = runners.generate_runner_mappings(self.contract)
        actual = json.loads(
            (ROOT / "generated/runner-mappings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(actual, expected)

    def test_tiny_is_only_accepted_after_measured_capacity_fits(self) -> None:
        # The selector helper proves the policy boundary independently of the
        # final exact-head measurement values. Production evidence must supply
        # real peaks before #18 is considered final-candidate ready.
        tiny = runners.select_buildah_tier(
            self.contract,
            peak_memory_bytes=256 * 1024 * 1024,
            peak_local_storage_bytes=1024 * 1024 * 1024,
            headroom_percent=25,
        )
        self.assertEqual(tiny, "buildah-tiny")
        small = runners.select_buildah_tier(
            self.contract,
            peak_memory_bytes=900 * 1024 * 1024,
            peak_local_storage_bytes=7 * 1024 * 1024 * 1024,
            headroom_percent=20,
        )
        self.assertEqual(small, "buildah-small")


if __name__ == "__main__":
    unittest.main()
