from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-native-image-chart.yml"
PRODUCTS_PATH = ROOT / "contracts/public-workflows/products.json"


class NativeImageChartExistingTagContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        products = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
        cls.contract = next(
            row
            for row in products["workflows"]
            if row["api_name"] == "release.native-image-chart"
        )

    def test_public_api_adds_only_optional_existing_tag_authority_inputs(self) -> None:
        self.assertEqual("1.0.0", self.contract["api_version"])
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

    def test_workflow_consumes_its_exact_central_authority_source(self) -> None:
        self.assertEqual(
            2,
            self.workflow.count("ref: ${{ github.workflow_sha }}"),
        )
        self.assertEqual(
            2,
            self.workflow.count('test "$(git rev-parse HEAD)" = "${GITHUB_WORKFLOW_SHA}"'),
        )
        self.assertNotIn("reusable-tag-image-chart.yml", self.workflow)
        self.assertNotRegex(
            self.workflow,
            re.compile(r"agent-state-dashboard|0\.1\.1|faruqi\.dev/mimranfaruqi/agent-state-dashboard"),
        )


if __name__ == "__main__":
    unittest.main()
