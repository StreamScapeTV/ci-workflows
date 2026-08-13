from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
RELEASE_TAG_SHA = "2b0443fdad002d47625386a959ebe68545cfe022"
OCI_SHA = "29cb88e406a0490834bd556bb825d0e227c862ac"
PUBLISH_SHA = "1661da705cac03206ba7f41598457bb7726c0dc9"


class OciPublicationWorkflowContractTests(unittest.TestCase):
    def test_public_workflow_is_call_only_and_has_bounded_api(self) -> None:
        text = (ROOT / ".github/workflows/reusable-oci-publish.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_call:", text)
        self.assertNotIn("workflow_dispatch:\n", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("secrets: inherit", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("docker://latest", text)
        self.assertNotIn("runner_labels:", text)
        self.assertNotIn("registry_host:", text)
        self.assertNotIn("registry_repository:", text)
        self.assertIn("registry_username:", text)
        self.assertIn("registry_token:", text)
        self.assertIn("platform_set:", text)
        self.assertIn("name: Release / OCI publication", text)
        for stage in (
            "Resolve exact caller release authority",
            "Verify authority matches the public release request",
            "Resolve contract-owned product, destination, and runner",
            "Rebuild and inspect exact source through OCI build contract",
            "Authenticate with workflow-scoped named registry credentials",
            "Publish or verify immutable version and source identities",
            "Read back registry manifests through independent Skopeo inspection",
            "Verify exact manifest, platform, metadata, and assertion parity",
            "Remove registry auth, read-back layouts, and publication state",
        ):
            self.assertIn(stage, text)

    def test_private_helpers_are_immutable_and_no_central_clone_remains(self) -> None:
        text = (ROOT / ".github/workflows/reusable-oci-publish.yml").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "repository: ${{ job.workflow_repository }}",
            "ref: ${{ job.workflow_sha }}",
            "repository: StreamScapeTV/ci-workflows",
            "path: .ciw",
            "./.ciw/actions/",
            "actions/checkout@",
            "github.workflow_sha",
            "GITHUB_WORKFLOW_SHA",
        ):
            self.assertNotIn(forbidden, text)
        expected = {
            "StreamScapeTV/ci-workflows/actions/resolve-release-tag": RELEASE_TAG_SHA,
            "StreamScapeTV/ci-workflows/actions/publish-oci": PUBLISH_SHA,
            "StreamScapeTV/ci-workflows/actions/exact-checkout": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/prepare-workspace": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/render-evidence": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/cleanup-workspace": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/validate-oci": OCI_SHA,
        }
        for helper, sha in expected.items():
            self.assertIn(f"uses: {helper}@{sha}", text)
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn("'existing-tag' || 'tag-push'", text)
        self.assertIn("release_mode: ${{ needs.plan.outputs.release_mode }}", text)
        self.assertIn("Verify revalidated release authority stayed exact", text)

    def test_composite_action_is_thin_and_has_no_caller_destination_or_command(self) -> None:
        text = (ROOT / "actions/publish-oci/action.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/ci/oci_publish.py", text)
        self.assertNotIn("registry_repository:", text)
        self.assertNotIn("registry_host:", text)
        self.assertNotIn("command:", text)
        self.assertNotIn("runner_labels:", text)
        self.assertNotIn("secrets: inherit", text)

    def test_mock_smoke_has_no_registry_credentials_and_proves_zero_artifacts(self) -> None:
        text = (ROOT / ".github/workflows/oci-publish-smoke.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request:", text)
        self.assertIn("[linux, amd64, general]", text)
        self.assertNotIn("registry_token", text)
        self.assertNotIn("registry_username", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("packages: write", text)
        self.assertIn("actions: read", text)
        self.assertIn("tests.test_oci_publication_recovery", text)
        self.assertIn("tests.test_oci_publication_filesystem", text)
        self.assertIn("/artifacts?per_page=100", text)

    def test_publication_schema_excludes_destination_and_closes_nested_results(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/oci-publication.schema.json").read_text(
                encoding="utf-8"
            )
        )
        request = schema["$defs"]["request"]
        self.assertFalse(request["additionalProperties"])
        self.assertEqual(
            set(request["properties"]),
            {"admitted_sha", "product_id", "release_version", "platform_set"},
        )
        result = schema["$defs"]["result"]
        self.assertFalse(result["additionalProperties"])
        immutable = result["properties"]["immutable_references"]
        self.assertFalse(immutable["additionalProperties"])
        self.assertFalse(schema["$defs"]["targetReference"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["platformEvidence"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["fluxSelection"]["additionalProperties"])

    def test_public_workflow_exposes_exact_registered_outputs(self) -> None:
        text = (ROOT / ".github/workflows/reusable-oci-publish.yml").read_text(
            encoding="utf-8"
        )
        call_section = text.split("permissions:", 1)[0]
        for name in (
            "result",
            "image_digest",
            "platform_digests_json",
            "immutable_references_json",
        ):
            self.assertIn(f"      {name}:\n", call_section)
        for old in (
            "repositories_json",
            "version_references_json",
            "source_references_json",
            "replayed",
            "evidence_id",
            "canary_id",
            "rollback_id",
        ):
            self.assertNotIn(f"      {old}:\n", call_section)


if __name__ == "__main__":
    unittest.main()
