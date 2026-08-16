from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import gradle_seed

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions" / "upload-gradle-seed" / "action.yml"
README = ROOT / "actions" / "upload-gradle-seed" / "README.md"
MODULE = ROOT / "src" / "ci_workflows" / "gradle_seed.py"
SCRIPT = ROOT / "scripts" / "ci" / "gradle_seed.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("ciw_gradle_seed_script", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load Gradle seed script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        self.assertIn('scripts/ci/gradle_seed.py"', source)
        self.assertNotIn("ciw.py", source)

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
            + SCRIPT.read_text(encoding="utf-8")
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
        self.assertIn(
            "actions/upload-gradle-seed@b51754fcd9d2df0f4aa71f097287b019bc6bedcb",
            source,
        )
        self.assertIn("source_sha: ${{ github.sha }}", source)
        self.assertIn("same job after execute and before cleanup", source)
        self.assertIn("No artifact/cache transports bridge jobs", source)
        validate_block = source.split("  validate:\n", 1)[1].split("\n  warm_gradle_seed:\n", 1)[0]
        self.assertNotIn("id-token: write", validate_block)
        warm_block = source.split("  warm_gradle_seed:\n", 1)[1]
        self.assertLess(warm_block.index("- id: execute"), warm_block.index("- id: promote"))
        self.assertLess(warm_block.index("- id: promote"), warm_block.index("- id: android_cleanup"))

    def test_script_requires_registered_gradle_workspace_home(self) -> None:
        script = load_script_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "INPUT_SOURCE_SHA": "a" * 40,
                "CI_WORKFLOW_ROOT": str(root),
                "GRADLE_USER_HOME": str(root / "gradle"),
            }
            result = mock.Mock()
            result.output_values.return_value = {"result": "promoted"}
            with mock.patch.object(
                script,
                "promote_gradle_seed",
                return_value=result,
            ) as promote:
                code = script.main([], environment=environment)
            self.assertEqual(0, code)
            self.assertEqual("result=promoted\n", output.read_text(encoding="utf-8"))
            promote.assert_called_once_with(
                source_sha="a" * 40,
                environment=environment,
            )

            bad_environments = (
                {**environment, "CI_WORKFLOW_ROOT": ""},
                {**environment, "GRADLE_USER_HOME": str(root / "other")},
                {**environment, "CI_WORKFLOW_ROOT": "relative-root"},
            )
            for bad_environment in bad_environments:
                with self.subTest(environment=bad_environment):
                    with mock.patch.object(script, "promote_gradle_seed") as promote_bad:
                        code = script.main([], environment=bad_environment)
                    self.assertEqual(2, code)
                    promote_bad.assert_not_called()


if __name__ == "__main__":
    unittest.main()
