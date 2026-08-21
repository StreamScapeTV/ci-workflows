from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OciPublicationWorkflowContractTests(unittest.TestCase):
    def test_public_workflow_is_call_only_and_has_bounded_api(self) -> None:
        text = (ROOT / ".github/workflows/reusable-oci-publish.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)
        self.assertNotIn("workflow_dispatch:", text)
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
            "Resolve contract-owned product, destination, and runner",
            "Rebuild and inspect exact source through OCI build contract",
            "Authenticate with workflow-scoped named registry credentials",
            "Publish immutable version and source identities idempotently",
            "Read back registry manifests through independent Skopeo inspection",
            "Verify exact manifest, platform, metadata, and assertion parity",
            "Remove registry auth, read-back layouts, and publication state",
        ):
            self.assertIn(stage, text)

    def test_composite_action_is_thin_and_has_no_caller_destination_or_command(self) -> None:
        text = (ROOT / "actions/publish-oci/action.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/ci/ciw.py", text)
        self.assertIn("oci publish --phase", text)
        self.assertNotIn("scripts/ci/oci_publish.py", text)
        self.assertNotIn("registry_repository:", text)
        self.assertNotIn("registry_host:", text)
        self.assertNotIn("command:", text)
        self.assertNotIn("runner_labels:", text)
        self.assertNotIn("secrets: inherit", text)

    def test_mock_smoke_has_no_registry_credentials_or_artifacts(self) -> None:
        text = (ROOT / ".github/workflows/oci-publish-smoke.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: [ubuntu-latest]", text)
        self.assertNotIn("runs-on: [linux, amd64", text)
        self.assertNotIn("registry_token", text)
        self.assertNotIn("registry_username", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("packages: write", text)

    def test_publication_schema_excludes_destination_and_command_inputs(self) -> None:
        schema = json.loads((ROOT / "contracts/oci-publication.schema.json").read_text(encoding="utf-8"))
        request = schema["$defs"]["request"]
        self.assertFalse(request["additionalProperties"])
        self.assertEqual(
            set(request["properties"]),
            {"admitted_sha", "product_id", "release_version", "platform_set"},
        )

    def test_public_workflow_exposes_exact_registered_outputs(self) -> None:
        text = (ROOT / ".github/workflows/reusable-oci-publish.yml").read_text(encoding="utf-8")
        call_section = text.split("permissions:", 1)[0]
        for name in ("result", "image_digest", "platform_digests_json", "immutable_references_json"):
            self.assertIn(f"      {name}:\n", call_section)
        for old in ("repositories_json", "version_references_json", "source_references_json", "replayed", "evidence_id", "canary_id", "rollback_id"):
            self.assertNotIn(f"      {old}:\n", call_section)


if __name__ == "__main__":
    unittest.main()
