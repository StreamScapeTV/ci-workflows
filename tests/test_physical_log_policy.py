"""Policy tests for durable physical-device CI evidence and platform log hygiene."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.foundation_types import FoundationError
from ci_workflows.physical_log_policy import (
    platform_log_boundary,
    render_stable_evidence,
    validate_durable_text,
    validate_stable_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
DEVICE_EVIDENCE = ROOT / "src/ci_workflows/device_evidence.py"
DOC = ROOT / "docs/security/physical-ci-log-hygiene.md"


def safe_payload() -> dict[str, object]:
    return {
        "repository": "StreamScapeTV/streamscape-media",
        "source_sha": "a" * 40,
        "workflow_run_id": 31739574192,
        "job_id": 94579466662,
        "device_family": "ios",
        "request_id": "physical-proof-137",
        "result": "success",
        "cleanup_result": "success",
        "evidence_id": "evidence-137",
        "validation_profile": "device-certification",
        "toolchain_profile": "apple-physical",
    }


class PhysicalLogPolicyTests(unittest.TestCase):
    def test_stable_evidence_preserves_exact_public_ids_without_host_metadata(self) -> None:
        payload = safe_payload()
        normalized = validate_stable_evidence(payload, contract_root=ROOT)
        self.assertEqual(normalized, payload)
        rendered = render_stable_evidence(payload, contract_root=ROOT)
        self.assertEqual(json.loads(rendered), payload)
        self.assertEqual(json.loads(rendered)["source_sha"], "a" * 40)
        self.assertEqual(json.loads(rendered)["workflow_run_id"], 31739574192)
        self.assertEqual(json.loads(rendered)["job_id"], 94579466662)
        for fragment in (
            "runner_name",
            "machine_name",
            "/home/",
            "/users/",
            "udid",
            "serial",
        ):
            self.assertNotIn(fragment, rendered.casefold())

    def test_durable_evidence_rejects_extra_private_metadata_fields(self) -> None:
        for field, value in (
            ("runner_name", "physical-runner"),
            ("workspace_path", "/home/example/work/repository"),
            ("device_serial", "private-device-123"),
        ):
            payload = safe_payload()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(FoundationError):
                validate_stable_evidence(payload, contract_root=ROOT)

    def test_durable_identifiers_reject_runner_provider_and_device_identifiers(self) -> None:
        for value in (
            "homelab-physical-runner",
            "machine-physical-01",
            "device-id-1234",
            "provider-device-01",
            "ios-udid-1234",
        ):
            payload = safe_payload()
            payload["request_id"] = value
            with self.subTest(value=value), self.assertRaises(FoundationError) as failure:
                validate_stable_evidence(payload, contract_root=ROOT)
            self.assertEqual(
                failure.exception.instruction,
                "physical_evidence_private_metadata",
            )

    def test_raw_durable_text_rejects_host_paths_credentials_and_platform_ids(self) -> None:
        rejected = (
            "Runner name: physical-runner-01",
            "workspace=/home/example/work/private/repository",
            "workspace=/Users/example/private/repository",
            "token=example-redact-me",
            "authorization=Bearer example-redact-me",
            "machine_name=physical-host-01",
            "device_serial=example-device-01",
            "https://example-user:example-password@example.invalid/private",
        )
        for text in rejected:
            with self.subTest(text=text), self.assertRaises(FoundationError) as failure:
                validate_durable_text(text, contract_root=ROOT)
            self.assertEqual(
                failure.exception.instruction,
                "physical_evidence_private_metadata",
            )
        accepted = "repository=StreamScapeTV/streamscape-media source_sha=" + "b" * 40
        self.assertEqual(
            validate_durable_text(accepted, contract_root=ROOT),
            accepted,
        )

    def test_unavoidable_platform_logs_have_minimum_retention_and_no_copy_boundary(self) -> None:
        boundary = platform_log_boundary(contract_root=ROOT)
        self.assertFalse(boundary["workflow_source_can_suppress_runner_bootstrap_metadata"])
        self.assertEqual(boundary["retention_requirement"], "shortest_repository_supported")
        self.assertEqual(boundary["access_requirement"], "repository_maintainers_only")
        self.assertEqual(boundary["raw_log_excerpt_copy"], "forbidden")
        self.assertEqual(boundary["raw_log_attachment"], "forbidden")
        self.assertEqual(boundary["product_artifact_retention"], "zero_by_default")
        self.assertEqual(
            boundary["stable_evidence_reference"],
            "repository_source_sha_run_id_job_id_only",
        )

    def test_device_evidence_must_call_durable_boundary_once_device_source_lands(self) -> None:
        # Issue #14 is developed on a separately owned branch. This future gate
        # takes no ownership of that branch, but makes its eventual reconciliation
        # with main fail closed unless durable publication routes through #137.
        if DEVICE_EVIDENCE.exists():
            text = DEVICE_EVIDENCE.read_text(encoding="utf-8")
            self.assertIn("physical_log_policy", text)
            self.assertRegex(
                text,
                r"\b(?:validate_stable_evidence|render_stable_evidence)\s*\(",
            )

    def test_documentation_records_platform_boundary_and_durable_allowlist(self) -> None:
        text = DOC.read_text(encoding="utf-8").casefold()
        for phrase in (
            "stable product evidence",
            "github platform logs",
            "shortest repository-supported retention",
            "repository maintainers",
            "raw log excerpts",
            "zero routine artifacts",
            "repository + source sha + run id + job id",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
