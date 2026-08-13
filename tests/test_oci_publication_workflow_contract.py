from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
RELEASE_TAG_SHA = "2b0443fdad002d47625386a959ebe68545cfe022"
OCI_SHA = "be0ec9505800bb5678083fc7ce912be83a90f139"
PUBLISH_SHA = "be0ec9505800bb5678083fc7ce912be83a90f139"


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
        self.assertNotIn("publication_repository:", text)
        self.assertIn("registry_username:", text)
        self.assertIn("registry_token:", text)
        for forbidden in ("kubeconfig", "sops", "kubectl", "service_account", "namespace"):
            self.assertNotIn(forbidden, text.lower())
        self.assertIn("platform_set:", text)
        self.assertIn("name: Release / OCI publication", text)
        self.assertNotIn("concurrency:", text)
        for stage in (
            "Resolve exact caller release authority",
            "Verify authority matches the public release request",
            "Resolve contract-owned product, destination, and runner",
            "Verify exact OCI builder host toolchain",
            "Rebuild and inspect exact source through OCI build contract",
            "Revalidate exact release authority before registry authentication",
            "Verify pre-authentication release authority stayed exact",
            "Authenticate with workflow-scoped named registry credentials",
            "Revalidate exact release authority immediately before publication",
            "Verify pre-publication release authority stayed exact",
            "Publish or verify immutable version and source identities",
            "Remove built images and local layouts before independent read-back",
            "Verify zero OCI build residue before independent read-back",
            "Read back registry manifests through independent Skopeo inspection",
            "Verify exact manifest, platform, metadata, and assertion parity",
            "Remove registry auth, read-back layouts, and publication state",
            "Render deterministic redacted publication evidence after OCI cleanup",
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
            "GITHUB_WORKFLOW_SHA",
        ):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("github.workflow_sha", text)
        self.assertEqual(1, text.count("job.workflow_sha"))
        self.assertIn(
            "central_workflow_sha: ${{ job.workflow_sha }}",
            text,
        )
        expected = {
            "StreamScapeTV/ci-workflows/actions/resolve-release-tag": RELEASE_TAG_SHA,
            "StreamScapeTV/ci-workflows/actions/publish-oci": PUBLISH_SHA,
            "StreamScapeTV/ci-workflows/actions/exact-checkout": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/prepare-workspace": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/verify-oci-toolchain": PUBLISH_SHA,
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
        self.assertNotIn("tool_set: oci-builder", text)
        self.assertNotIn("capability_profile: linux", text)
        self.assertLess(
            text.index("Prepare isolated publication workspace"),
            text.index("Verify exact OCI builder host toolchain"),
        )
        self.assertLess(
            text.index("Verify exact OCI builder host toolchain"),
            text.index("Rebuild and inspect exact source through OCI build contract"),
        )
        self.assertLess(
            text.index("Rebuild and inspect exact source through OCI build contract"),
            text.index(
                "Revalidate exact release authority before registry authentication"
            ),
        )
        self.assertLess(
            text.index(
                "Revalidate exact release authority before registry authentication"
            ),
            text.index("Authenticate with workflow-scoped named registry credentials"),
        )
        self.assertLess(
            text.index("Authenticate with workflow-scoped named registry credentials"),
            text.index(
                "Revalidate exact release authority immediately before publication"
            ),
        )
        self.assertIn(
            "release_authority_sha: ${{ steps.credential_authority.outputs.release_source_sha }}",
            text,
        )
        self.assertIn(
            "release_authority_sha: ${{ steps.publication_authority.outputs.release_source_sha }}",
            text,
        )

    def test_composite_action_is_thin_and_has_no_caller_destination_or_command(self) -> None:
        text = (ROOT / "actions/publish-oci/action.yml").read_text(encoding="utf-8")
        outputs = text.split("runs:", 1)[0]
        self.assertIn("  manifest_digests_json:\n", outputs)
        self.assertNotIn("  image_digest:\n", outputs)
        self.assertIn("scripts/ci/ciw.py", text)
        self.assertIn("oci publish --phase", text)
        self.assertIn("INPUT_PHASE: ${{ inputs.phase }}", text)
        self.assertIn('--phase "${INPUT_PHASE}"', text)
        self.assertIn("INPUT_CENTRAL_WORKFLOW_SHA", text)
        self.assertIn("INPUT_PUBLICATION_HELPER_SHA", text)
        self.assertIn("INPUT_WORKSPACE_CLEANUP_OUTCOME", text)
        self.assertIn("  supply_evidence_id:\n", outputs)
        self.assertNotIn('--phase "${{ inputs.phase }}"', text)
        self.assertNotIn("registry_repository:", text)
        self.assertNotIn("registry_host:", text)
        self.assertNotIn("publication_repository:", text)
        self.assertNotIn("command:", text)
        self.assertNotIn("runner_labels:", text)
        self.assertNotIn("secrets: inherit", text)

    def test_oci_toolchain_action_has_no_caller_control_surface(self) -> None:
        text = (ROOT / "actions/verify-oci-toolchain/action.yml").read_text(
            encoding="utf-8"
        )
        header = text.split("outputs:", 1)[0]
        self.assertNotIn("inputs:", header)
        self.assertEqual(1, text.count("    - id: verify"))
        self.assertIn("INPUT_TOOL_SET: oci-builder", text)
        self.assertIn("INPUT_CAPABILITY_PROFILE: linux", text)
        self.assertIn('scripts/ci/ciw.py" tooling verify', text)
        for forbidden in ("${{ inputs.", "curl ", "sudo ", "docker ", "eval "):
            self.assertNotIn(forbidden, text)

    def test_documented_callers_serialize_every_product_publication(self) -> None:
        text = (ROOT / "docs/workflows/oci-publish.md").read_text(encoding="utf-8")
        for product in (
            "iptv-backend-image",
            "agent-state-image",
            "flux-runner-images",
        ):
            self.assertIn(
                f"group: oci-publish-${{{{ github.repository }}}}-{product}",
                text,
            )
        self.assertEqual(3, text.count("cancel-in-progress: false"))
        self.assertNotIn(
            "group: oci-publish-${{ github.repository }}-${{ github.ref }}",
            text,
        )

    def test_mock_smoke_has_no_registry_credentials_and_proves_zero_artifacts(self) -> None:
        text = (ROOT / ".github/workflows/oci-publish-smoke.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request:", text)
        self.assertIn("[linux, amd64, buildah, tiny]", text)
        self.assertNotIn("registry_token", text)
        self.assertNotIn("registry_username", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("packages: write", text)
        self.assertIn("actions: read", text)
        self.assertIn("tests.test_oci_publication_recovery", text)
        self.assertIn("tests.test_oci_publication_filesystem", text)
        self.assertIn(
            f"uses: StreamScapeTV/ci-workflows/actions/publish-oci@{PUBLISH_SHA}",
            text,
        )
        self.assertIn("phase: residue", text)
        self.assertIn("CHECKPOINT_RESULT", text)
        self.assertIn("/var/lib/containers/storage", text)
        self.assertIn("/run/containers/storage", text)
        self.assertIn("/var/tmp/buildah", text)
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
        self.assertIn("manifest_digests", result["required"])
        self.assertNotIn("image_digest", result["properties"])
        immutable = result["properties"]["immutable_references"]
        self.assertFalse(immutable["additionalProperties"])
        self.assertFalse(schema["$defs"]["targetReference"]["additionalProperties"])
        target_reference = schema["$defs"]["targetReference"]
        self.assertIn("source_reference", target_reference["required"])
        self.assertIn("resolved_inputs", target_reference["required"])
        self.assertNotIn("base_references", target_reference["properties"])
        self.assertIn("assertions", target_reference["required"])
        self.assertNotIn("source_sha", target_reference["properties"])
        assertions = schema["$defs"]["assertionEvidence"]
        self.assertFalse(assertions["additionalProperties"])
        self.assertEqual(assertions["properties"]["result"]["const"], "passed")
        self.assertIn("contract_digest", assertions["required"])
        self.assertIn("verified_platforms", assertions["required"])
        release = result["properties"]["immutable_references"]["properties"]["release"]
        self.assertIn("source_sha", release["required"])
        self.assertFalse(schema["$defs"]["platformEvidence"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["fluxSelection"]["additionalProperties"])

    def test_public_workflow_exposes_exact_registered_outputs(self) -> None:
        text = (ROOT / ".github/workflows/reusable-oci-publish.yml").read_text(
            encoding="utf-8"
        )
        call_section = text.split("permissions:", 1)[0]
        for name in (
            "result",
            "manifest_digests_json",
            "platform_digests_json",
            "immutable_references_json",
        ):
            self.assertIn(f"      {name}:\n", call_section)
        self.assertNotIn("      image_digest:\n", call_section)
        self.assertIn("result: ${{ steps.terminal.outputs.result }}", text)
        self.assertIn(
            "manifest_digests_json: ${{ steps.terminal.outputs.manifest_digests_json }}",
            text,
        )
        self.assertLess(
            text.index("Remove registry auth, read-back layouts, and publication state"),
            text.index("Project terminal trusted publication status"),
        )
        self.assertLess(
            text.index("Render deterministic redacted publication evidence after OCI cleanup"),
            text.index("Remove and verify registered workspace state"),
        )
        self.assertLess(
            text.index("Remove and verify registered workspace state"),
            text.index(
                "Append canonical terminal OCI supply evidence after workspace cleanup"
            ),
        )
        self.assertLess(
            text.index(
                "Append canonical terminal OCI supply evidence after workspace cleanup"
            ),
            text.index("Project terminal trusted publication status"),
        )
        self.assertIn("EVIDENCE_OUTCOME: ${{ steps.evidence.outcome }}", text)
        self.assertIn(
            "EVIDENCE_SUMMARY_OUTCOME: ${{ steps.evidence_summary.outcome }}",
            text,
        )
        self.assertIn(
            "Persist redacted toolchain and cleanup evidence in the run summary",
            text,
        )
        self.assertIn(
            "EVIDENCE_JSON: ${{ steps.evidence.outputs.evidence_json }}",
            text,
        )
        self.assertIn('>> "${GITHUB_STEP_SUMMARY}"', text)
        self.assertLess(
            text.index("Persist redacted toolchain and cleanup evidence in the run summary"),
            text.index("Remove and verify registered workspace state"),
        )
        self.assertIn("SOURCE_CLEAN_OUTCOME: ${{ steps.source_clean.outcome }}", text)
        self.assertIn(
            "toolchain_json: ${{ steps.tools.outputs.toolchain_json }}", text
        )
        self.assertNotIn(
            '{"buildah":"1.33.7","skopeo":"1.13.3","publication":"immutable"}',
            text,
        )
        self.assertIn("TOOLS_OUTCOME: ${{ steps.tools.outcome }}", text)
        self.assertIn('test "${TOOLS_OUTCOME}" = success', text)
        self.assertIn(
            "FINAL_SUPPLY_EVIDENCE_OUTCOME: ${{ steps.final_supply_evidence.outcome }}",
            text,
        )
        self.assertIn(
            'test "${FINAL_SUPPLY_EVIDENCE_OUTCOME}" = success', text
        )
        self.assertIn(
            "publication_evidence_id: ${{ steps.verify.outputs.evidence_id }}",
            text,
        )
        self.assertIn(
            "foundation_evidence_id: ${{ steps.evidence.outputs.evidence_id }}",
            text,
        )
        self.assertIn(
            "publication_helper_sha: " + PUBLISH_SHA,
            text,
        )
        self.assertLess(
            text.index("Remove built images and local layouts before independent read-back"),
            text.index("Read back registry manifests through independent Skopeo inspection"),
        )
        self.assertNotIn("cleanup_state: not-run", text)
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
