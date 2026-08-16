from __future__ import annotations

import unittest
from pathlib import Path

from ci_workflows import gradle_seed

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions" / "upload-gradle-seed" / "action.yml"
README = ROOT / "actions" / "upload-gradle-seed" / "README.md"
MODULE = ROOT / "src" / "ci_workflows" / "gradle_seed.py"


class GradleSeedActionContractTests(unittest.TestCase):
    def test_action_is_thin_and_cannot_grant_oidc_or_event_authority(self) -> None:
        source = ACTION.read_text(encoding="utf-8")
        input_block = source.split("inputs:\n", 1)[1].split("\noutputs:\n", 1)[0]
        self.assertIn("  source_sha:\n", input_block)
        self.assertEqual(1, sum(
            1
            for line in input_block.splitlines()
            if line.startswith("  ") and not line.startswith("    ") and line.endswith(":")
        ))
        self.assertNotIn("permissions:", source)
        self.assertNotIn("id-token: write", source)
        self.assertNotIn("\non:", source)
        self.assertNotIn("runs-on:", source)
        self.assertNotIn("workflow_dispatch", source)
        self.assertNotIn("pull_request", source)
        self.assertNotIn("endpoint:", input_block)
        self.assertNotIn("audience:", input_block)
        self.assertNotIn("token:", input_block)
        self.assertIn('scripts/ci/ciw.py" gradle-seed upload', source)

    def test_fixed_client_contract_matches_reviewed_flux_protocol(self) -> None:
        self.assertEqual(
            "streamscapetv-gradle-seed-v1",
            gradle_seed.OIDC_AUDIENCE,
        )
        self.assertEqual(
            "arc-gradle-seed-promoter.github-actions-runners.svc.cluster.local",
            gradle_seed.FLUX_HOST,
        )
        self.assertEqual(8080, gradle_seed.FLUX_PORT)
        self.assertEqual("/v1/gradle-seed", gradle_seed.FLUX_PATH)
        self.assertEqual(
            "application/vnd.faruqi.gradle-seed-v1",
            gradle_seed.CONTENT_TYPE,
        )
        self.assertEqual(4 * 1024 * 1024 * 1024, gradle_seed.MAX_UPLOAD_BYTES)
        self.assertEqual("StreamScapeTV/iptv-android", gradle_seed.EXPECTED_REPOSITORY)
        self.assertEqual("1310373430", gradle_seed.EXPECTED_REPOSITORY_ID)
        self.assertEqual("refs/heads/develop", gradle_seed.EXPECTED_REF)
        self.assertEqual("push", gradle_seed.EXPECTED_EVENT)
        self.assertEqual(
            (
                "StreamScapeTV/iptv-android/.github/workflows/"
                "android-ci.yml@refs/heads/develop"
            ),
            gradle_seed.EXPECTED_WORKFLOW_REF,
        )

    def test_no_long_lived_or_fallback_cache_transport_is_present(self) -> None:
        combined = (
            ACTION.read_text(encoding="utf-8")
            + "\n"
            + MODULE.read_text(encoding="utf-8")
        ).lower()
        for forbidden in (
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "s3://",
            "amazon s3",
            "ghcr.io",
            "deploy key",
            "private key",
            "github_token",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertIn("actions_id_token_request_url", combined)
        self.assertIn("actions_id_token_request_token", combined)

    def test_action_outputs_only_bounded_promotion_evidence(self) -> None:
        source = ACTION.read_text(encoding="utf-8")
        output_block = source.split("outputs:\n", 1)[1].split("\nruns:\n", 1)[0]
        names = {
            line.strip().removesuffix(":")
            for line in output_block.splitlines()
            if line.startswith("  ")
            and not line.startswith("    ")
            and line.endswith(":")
        }
        self.assertEqual(
            {
                "result",
                "source_sha",
                "generation",
                "file_count",
                "total_bytes",
                "evidence_id",
                "cleanup_result",
            },
            names,
        )
        lowered = output_block.lower()
        for forbidden in ("token", "endpoint", "path", "sha256_json", "file_json"):
            self.assertNotIn(forbidden, lowered)

    def test_consumer_example_keeps_oidc_permission_in_protected_push_lane(self) -> None:
        source = README.read_text(encoding="utf-8")
        self.assertIn("github.event_name == 'push'", source)
        self.assertIn("github.ref == 'refs/heads/develop'", source)
        self.assertIn("needs: validate", source)
        self.assertIn("runs-on: mobile", source)
        self.assertIn("contents: read", source)
        self.assertIn("id-token: write", source)
        self.assertIn("profile: gradle", source)
        self.assertIn("cache_mode: disabled", source)
        self.assertIn("actions/upload-gradle-seed@<issue-251-immutable-sha>", source)
        self.assertIn("source_sha: ${{ github.sha }}", source)
        self.assertIn("same job after execute and before cleanup", source)
        self.assertIn("No artifact/cache transports bridge jobs", source)
        validate_block = source.split("  validate:\n", 1)[1].split("\n  warm_gradle_seed:\n", 1)[0]
        self.assertNotIn("id-token: write", validate_block)
        warm_block = source.split("  warm_gradle_seed:\n", 1)[1]
        self.assertLess(warm_block.index("- id: execute"), warm_block.index("- id: promote"))
        self.assertLess(warm_block.index("- id: promote"), warm_block.index("- id: android_cleanup"))


if __name__ == "__main__":
    unittest.main()
