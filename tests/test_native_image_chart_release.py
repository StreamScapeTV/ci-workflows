from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-native-image-chart.yml"
CALLER_SMOKE_PATH = ROOT / ".github/workflows/native-image-chart-call-parse-smoke.yml"
PRODUCTS_PATH = ROOT / "contracts/public-workflows/products.json"
AGGREGATE_PATH = ROOT / "contracts/public-workflows.json"
REFERENCE_PATH = ROOT / "docs/workflows/public-api-reference.md"


class NativeImageChartNormalReleaseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.caller_smoke = CALLER_SMOKE_PATH.read_text(encoding="utf-8")
        cls.reference = REFERENCE_PATH.read_text(encoding="utf-8")
        products = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
        aggregate = json.loads(AGGREGATE_PATH.read_text(encoding="utf-8"))
        cls.contract = next(
            row
            for row in products["workflows"]
            if row["api_name"] == "release.native-image-chart"
        )
        cls.bootstrap_contract = next(
            row
            for row in products["workflows"]
            if row["api_name"] == "release.tag-image-chart-bootstrap"
        )
        cls.aggregate = next(
            row
            for row in aggregate["workflows"]
            if row["api_name"] == "release.native-image-chart"
        )

    def test_public_api_v2_adds_only_optional_execution_backend(self) -> None:
        self.assertEqual("2.0.0", self.contract["api_version"])
        self.assertEqual("2.0.0", self.aggregate["api_version"])
        self.assertEqual("implemented", self.contract["status"])
        self.assertEqual(
            ["tag-push", "workflow_call"],
            self.contract["permitted_events"],
        )
        inputs = {row["name"]: row for row in self.contract["inputs"]}
        self.assertEqual(
            {
                "execution_backend",
                "image_name",
                "chart_name",
                "chart_path",
                "dockerfile_path",
                "build_context",
            },
            set(inputs),
        )
        self.assertFalse(inputs["execution_backend"]["required"])
        self.assertEqual("organization", inputs["execution_backend"]["default"])
        for required in ("image_name", "chart_name", "chart_path"):
            self.assertTrue(inputs[required]["required"])
        self.assertEqual("Dockerfile", inputs["dockerfile_path"]["default"])
        self.assertEqual(".", inputs["build_context"]["default"])

    def test_recovery_semantics_remain_on_the_separate_deprecated_surface(self) -> None:
        self.assertEqual(
            ["tag-push", "workflow_call", "workflow_dispatch-existing-tag"],
            self.bootstrap_contract["permitted_events"],
        )
        bootstrap_inputs = {row["name"] for row in self.bootstrap_contract["inputs"]}
        self.assertTrue(
            {"release_mode", "release_version", "release_source_sha"} <= bootstrap_inputs
        )

    def test_generated_reference_matches_normal_native_api(self) -> None:
        self.assertTrue(self.reference.endswith("\n"))
        section = self.reference.split("### `release.native-image-chart`", 1)[1].split(
            "### `release.orchestrate`", 1
        )[0]
        self.assertIn("- Events: `tag-push`, `workflow_call`", section)
        self.assertIn(
            "- Inputs: `execution_backend` (default `organization`), "
            "`image_name` (required), `chart_name` (required), "
            "`chart_path` (required), `dockerfile_path` (default `Dockerfile`), "
            "`build_context` (default `.`)",
            section,
        )
        self.assertNotIn("workflow_dispatch-existing-tag", section)
        self.assertNotIn("release_mode", section)
        self.assertNotIn("release_source_sha", section)
        self.assertIn(
            "| `release.native-image-chart` `2.0.0` |",
            self.reference,
        )

    def test_initial_authority_is_fixed_to_normal_tag_push(self) -> None:
        caller_inputs = self.workflow.split("  workflow_call:\n    inputs:\n", 1)[1].split(
            "    secrets:\n", 1
        )[0]
        self.assertNotIn("release_mode:", caller_inputs)
        self.assertNotIn("release_version:", caller_inputs)
        self.assertNotIn("release_source_sha:", caller_inputs)
        authority = self.workflow.split("- id: authority", 1)[1].split("\n\n  publish:", 1)[0]
        self.assertIn("uses: ./.ciw/actions/resolve-release-tag", authority)
        self.assertIn("release_mode: tag-push", authority)
        self.assertNotIn("inputs.release_mode", authority)
        self.assertNotIn("inputs.release_version", authority)
        self.assertNotIn("inputs.release_source_sha", authority)

    def test_both_privileged_revalidations_preserve_admitted_exact_tuple(self) -> None:
        self.assertEqual(
            3,
            self.workflow.count("uses: ./.ciw/actions/resolve-release-tag"),
        )
        self.assertEqual(3, self.workflow.count("release_mode: tag-push"))
        self.assertEqual(
            2,
            self.workflow.count("release_version: ${{ needs.admit.outputs.version }}"),
        )
        self.assertEqual(
            2,
            self.workflow.count("release_source_sha: ${{ needs.admit.outputs.source_sha }}"),
        )
        self.assertEqual(
            2,
            self.workflow.count("expected_tag_object_sha: ${{ needs.admit.outputs.tag_object_sha }}"),
        )
        self.assertEqual(
            2,
            self.workflow.count("expected_tag_commit_sha: ${{ needs.admit.outputs.tag_commit_sha }}"),
        )

    def test_normal_release_keeps_native_amd64_publication_contract(self) -> None:
        self.assertIn("runs-on: ubuntu-latest", self.workflow)
        self.assertEqual(
            2,
            self.workflow.count("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"),
        )
        self.assertIn('test "$(uname -m)" = x86_64', self.workflow)
        self.assertIn("--platform linux/amd64", self.workflow)
        self.assertIn('data.get("Architecture") != "amd64"', self.workflow)
        self.assertIn('data.get("Os") != "linux"', self.workflow)
        self.assertNotRegex(self.workflow, re.compile(r"arm64|linux/arm64", re.IGNORECASE))
        self.assertIn("Independently read back immutable image and chart identities", self.workflow)
        self.assertIn("chart_package_sha256", self.workflow)

    def test_hosted_publication_uses_ghcr_github_token_and_anonymous_readback(self) -> None:
        self.assertIn("execution_backend:", self.workflow)
        self.assertIn("default: organization", self.workflow)
        self.assertIn("github.token", self.workflow)
        self.assertIn("github.actor", self.workflow)
        self.assertIn("packages: write", self.workflow)
        self.assertIn('test "${REGISTRY}" = ghcr.io', self.workflow)
        self.assertIn('test "${REGISTRY_NAMESPACE}" = streamscapetv', self.workflow)
        self.assertIn('test "${CHART_NAMESPACE}" = streamscapetv/helm-charts', self.workflow)
        self.assertIn('test -z "${PRIVATE_REGISTRY_USERNAME}"', self.workflow)
        self.assertIn('test -z "${PRIVATE_REGISTRY_TOKEN}"', self.workflow)
        self.assertIn('readback_auth="${state}/anonymous-auth.json"', self.workflow)
        self.assertIn('printf \'{}\\n\' > "${readback_auth}"', self.workflow)

    def test_organization_default_keeps_private_registry_contract(self) -> None:
        self.assertIn('test "${REGISTRY}" = git.faruqi.dev', self.workflow)
        self.assertIn('test "${REGISTRY_NAMESPACE}" = mimranfaruqi', self.workflow)
        self.assertIn('test "${CHART_NAMESPACE}" = mimranfaruqi/helm-charts', self.workflow)
        self.assertIn('test -n "${PRIVATE_REGISTRY_USERNAME}"', self.workflow)
        self.assertIn('test -n "${PRIVATE_REGISTRY_TOKEN}"', self.workflow)
        secret_block = self.workflow.split("    secrets:\n", 1)[1].split("    outputs:\n", 1)[0]
        self.assertEqual(2, secret_block.count("required: false"))

    def test_workflow_consumes_called_reusable_identity_not_caller_identity(self) -> None:
        self.assertEqual(
            3,
            self.workflow.count("repository: ${{ job.workflow_repository }}"),
        )
        self.assertEqual(
            3,
            self.workflow.count("ref: ${{ job.workflow_sha }}"),
        )
        self.assertEqual(
            4,
            self.workflow.count('test "$(git rev-parse HEAD)" = "${{ job.workflow_sha }}"'),
        )
        self.assertNotIn("${{ github.workflow_sha }}", self.workflow)
        self.assertNotIn("${GITHUB_WORKFLOW_SHA}", self.workflow)
        self.assertNotIn("repository: StreamScapeTV/ci-workflows", self.workflow)
        self.assertNotIn("reusable-tag-image-chart.yml", self.workflow)
        self.assertNotRegex(
            self.workflow,
            re.compile(r"agent-state-dashboard|0\.1\.1|faruqi\.dev/mimranfaruqi/agent-state-dashboard"),
        )

    def test_runner_temp_paths_are_initialized_at_runtime(self) -> None:
        self.assertNotIn("${{ runner.temp }}", self.workflow)
        initialize = self.workflow.split(
            "- name: Initialize runner-local publication paths", 1
        )[1].split("\n\n      - id: revalidate", 1)[0]
        self.assertIn('state_root="${RUNNER_TEMP}/native-image-chart"', initialize)
        self.assertIn(
            "printf 'REGISTRY_AUTH_FILE=%s\\n' \"${state_root}/auth.json\"",
            initialize,
        )
        self.assertIn(
            "printf 'HELM_REGISTRY_CONFIG=%s\\n' \"${state_root}/helm-registry.json\"",
            initialize,
        )
        self.assertIn(
            "printf 'PACKAGE_ROOT=%s\\n' \"${state_root}/packages\"",
            initialize,
        )
        self.assertIn('} >> "${GITHUB_ENV}"', initialize)

    def test_github_smoke_compiles_thin_main_caller_without_publication(self) -> None:
        self.assertIn("pull_request:", self.caller_smoke)
        self.assertIn(
            '".github/workflows/reusable-native-image-chart.yml"',
            self.caller_smoke,
        )
        self.assertIn("if: ${{ false }}", self.caller_smoke)
        self.assertNotIn(
            "uses: ./.github/workflows/reusable-native-image-chart.yml",
            self.caller_smoke,
        )
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/"
            "reusable-native-image-chart.yml@main",
            self.caller_smoke,
        )
        self.assertNotIn("release_mode:", self.caller_smoke)
        self.assertNotIn("release_version:", self.caller_smoke)
        self.assertNotIn("release_source_sha:", self.caller_smoke)
        for required in ("image_name: parser-smoke", "chart_name: parser-smoke", "chart_path: ."):
            self.assertIn(required, self.caller_smoke)
        self.assertIn(
            'test "${{ needs.reusable_call.result }}" = "skipped"',
            self.caller_smoke,
        )


if __name__ == "__main__":
    unittest.main()
