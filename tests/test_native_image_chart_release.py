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
PARSER_FIX_CHECKPOINT = "6fa19fe73c709c6fb81a30b926edac95a2fb674e"


class NativeImageChartExistingTagContractTest(unittest.TestCase):
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
        cls.aggregate = next(
            row
            for row in aggregate["workflows"]
            if row["api_name"] == "release.native-image-chart"
        )

    def test_public_api_adds_only_optional_existing_tag_authority_inputs(self) -> None:
        self.assertEqual("1.0.0", self.contract["api_version"])
        self.assertEqual("1.0.0", self.aggregate["api_version"])
        self.assertEqual("implemented", self.contract["status"])
        self.assertEqual(
            ["tag-push", "workflow_call", "workflow_dispatch-existing-tag"],
            self.contract["permitted_events"],
        )
        inputs = {row["name"]: row for row in self.contract["inputs"]}
        self.assertEqual("tag-push", inputs["release_mode"]["default"])
        self.assertFalse(inputs["release_mode"]["required"])
        self.assertFalse(inputs["release_version"]["required"])
        self.assertFalse(inputs["release_source_sha"]["required"])
        for required in ("image_name", "chart_name", "chart_path"):
            self.assertTrue(inputs[required]["required"])

    def test_generated_reference_matches_additive_native_api(self) -> None:
        self.assertTrue(self.reference.endswith("\n"))
        section = self.reference.split("### `release.native-image-chart`", 1)[1].split(
            "### `release.orchestrate`", 1
        )[0]
        self.assertIn(
            "- Events: `tag-push`, `workflow_call`, `workflow_dispatch-existing-tag`",
            section,
        )
        self.assertIn(
            "- Inputs: `release_mode` (default `tag-push`), `release_version`, "
            "`release_source_sha`, `image_name` (required), `chart_name` (required), "
            "`chart_path` (required), `dockerfile_path` (default `Dockerfile`), "
            "`build_context` (default `.`)",
            section,
        )
        self.assertIn(
            "| `release.native-image-chart` `1.0.0` |",
            self.reference,
        )

    def test_initial_authority_uses_caller_mode_and_exact_optional_tuple(self) -> None:
        self.assertRegex(
            self.workflow,
            r"release_mode:\n"
            r"\s+description: tag-push by default, or existing-tag with the complete exact tuple\n"
            r"\s+required: false\n"
            r"\s+default: tag-push",
        )
        self.assertIn("release_mode: ${{ steps.authority.outputs.release_mode }}", self.workflow)
        authority = self.workflow.split("- id: authority", 1)[1].split("\n\n  publish:", 1)[0]
        self.assertIn("uses: ./.ciw/actions/resolve-release-tag", authority)
        self.assertIn("release_mode: ${{ inputs.release_mode }}", authority)
        self.assertIn("release_version: ${{ inputs.release_version }}", authority)
        self.assertIn("release_source_sha: ${{ inputs.release_source_sha }}", authority)

    def test_both_privileged_revalidations_preserve_admitted_exact_tuple(self) -> None:
        self.assertEqual(
            3,
            self.workflow.count("uses: ./.ciw/actions/resolve-release-tag"),
        )
        self.assertEqual(
            2,
            self.workflow.count("release_mode: ${{ needs.admit.outputs.release_mode }}"),
        )
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

    def test_existing_tag_extension_keeps_native_amd64_publication_contract(self) -> None:
        self.assertEqual(
            2,
            self.workflow.count("runs-on: [linux, amd64, buildah, high]"),
        )
        self.assertIn('test "$(uname -m)" = x86_64', self.workflow)
        self.assertIn("--platform linux/amd64", self.workflow)
        self.assertIn('data.get("Architecture") != "amd64"', self.workflow)
        self.assertIn('data.get("Os") != "linux"', self.workflow)
        self.assertNotRegex(self.workflow, re.compile(r"arm64|linux/arm64", re.IGNORECASE))
        self.assertIn("Independently read back immutable image and chart identities", self.workflow)
        self.assertIn("chart_package_sha256", self.workflow)

    def test_workflow_consumes_called_reusable_identity_not_caller_identity(self) -> None:
        self.assertEqual(
            2,
            self.workflow.count("repository: ${{ job.workflow_repository }}"),
        )
        self.assertEqual(
            2,
            self.workflow.count("ref: ${{ job.workflow_sha }}"),
        )
        self.assertEqual(
            3,
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

    def test_github_smoke_compiles_the_exact_fixed_reusable_without_publication(self) -> None:
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
            f"reusable-native-image-chart.yml@{PARSER_FIX_CHECKPOINT}",
            self.caller_smoke,
        )
        self.assertIn("release_mode: existing-tag", self.caller_smoke)
        self.assertIn('release_version: "0.0.0"', self.caller_smoke)
        self.assertIn(
            'release_source_sha: "0000000000000000000000000000000000000000"',
            self.caller_smoke,
        )
        self.assertIn(
            'test "${{ needs.reusable_call.result }}" = "skipped"',
            self.caller_smoke,
        )


if __name__ == "__main__":
    unittest.main()
