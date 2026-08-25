from __future__ import annotations

import gzip
import json
from pathlib import Path
import unittest
from unittest import mock

from ci_workflows.ci_diagnostics import (
    DiagnosticReadConfig,
    DiagnosticReadError,
    DiagnosticReader,
    DiagnosticReceipt,
    decode_receipt_capability,
    encode_receipt_capability,
    self_check,
)
from ci_workflows.r2_diagnostics import R2DiagnosticError

ROOT = Path(__file__).resolve().parents[1]
OBJECT_KEY = (
    "ci-diagnostics/11111111-2222-4333-8444-555555555555/"
    + "b" * 32
    + "/32885563040-1.log.gz"
)
RECEIPT = f"r2:{OBJECT_KEY}#sha256=" + "a" * 64


class DiagnosticCapabilityTests(unittest.TestCase):
    def test_exact_agent_state_receipt_round_trips_as_opaque_url_capability(self) -> None:
        token = encode_receipt_capability(RECEIPT)
        self.assertNotIn("/", token)
        self.assertNotIn("#", token)
        self.assertNotIn("=", token)
        parsed = decode_receipt_capability(token)
        self.assertEqual(parsed.render(), RECEIPT)
        self.assertEqual(parsed.object_key, OBJECT_KEY)
        self.assertEqual(parsed.sha256, "a" * 64)

    def test_receipt_and_capability_fail_closed_on_shape_drift(self) -> None:
        legacy = (
            "r2:ci-diagnostics/11111111-2222-4333-8444-555555555555/"
            "32885563040-1.log.gz#sha256=" + "a" * 64
        )
        cases = (
            "",
            "r2:other/key#sha256=" + "a" * 64,
            legacy,
            "r2:ci-diagnostics/id/" + "b" * 32 + "/1-1.log.gz#sha256=UPPER",
            RECEIPT + "&extra=true",
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(DiagnosticReadError):
                DiagnosticReceipt.parse(value)
        for token in ("", "!not-base64!", encode_receipt_capability(RECEIPT) + "="):
            with self.subTest(token=token), self.assertRaises(DiagnosticReadError):
                decode_receipt_capability(token)

    def test_self_check_contains_only_reader_routes(self) -> None:
        result = self_check()
        self.assertEqual(result["mode"], "receipt-bound-r2-reader")
        self.assertEqual(result["routes"], ["/healthz", "/diagnostics/<capability>"])


class DiagnosticReaderTests(unittest.TestCase):
    @staticmethod
    def config() -> DiagnosticReadConfig:
        return DiagnosticReadConfig(
            account_id="a" * 32,
            bucket="private-ci-logs",
            access_key_id="read-access",
            secret_access_key="read-secret",
        )

    def test_reader_uses_only_exact_receipt_and_read_credentials_then_decompresses(self) -> None:
        raw = b"private compiler detail\nprivate test detail\n"
        compressed = gzip.compress(raw, mtime=0)
        token = encode_receipt_capability(RECEIPT)
        with mock.patch(
            "ci_workflows.ci_diagnostics.download_private_diagnostic",
            return_value=compressed,
        ) as download:
            result = DiagnosticReader(self.config()).retrieve(token)
        self.assertEqual(result, raw)
        download.assert_called_once_with(
            object_key=OBJECT_KEY,
            expected_sha256="a" * 64,
            account_id="a" * 32,
            bucket="private-ci-logs",
            access_key_id="read-access",
            secret_access_key="read-secret",
        )

    def test_missing_or_digest_mismatched_object_is_indistinguishable(self) -> None:
        token = encode_receipt_capability(RECEIPT)
        for code in ("r2_download_http_404", "r2_download_digest_mismatch"):
            with self.subTest(code=code), mock.patch(
                "ci_workflows.ci_diagnostics.download_private_diagnostic",
                side_effect=R2DiagnosticError(code),
            ), self.assertRaisesRegex(DiagnosticReadError, "diagnostic_not_found") as caught:
                DiagnosticReader(self.config()).retrieve(token)
            self.assertEqual(caught.exception.status, 404)

    def test_r2_outage_or_invalid_gzip_is_generic_unavailable(self) -> None:
        token = encode_receipt_capability(RECEIPT)
        with mock.patch(
            "ci_workflows.ci_diagnostics.download_private_diagnostic",
            side_effect=R2DiagnosticError("r2_download_unavailable"),
        ), self.assertRaisesRegex(DiagnosticReadError, "diagnostic_unavailable") as caught:
            DiagnosticReader(self.config()).retrieve(token)
        self.assertEqual(caught.exception.status, 502)
        with mock.patch(
            "ci_workflows.ci_diagnostics.download_private_diagnostic",
            return_value=b"not-gzip",
        ), self.assertRaisesRegex(DiagnosticReadError, "diagnostic_unavailable"):
            DiagnosticReader(self.config()).retrieve(token)

    def test_runtime_config_accepts_only_fixed_read_environment_names(self) -> None:
        config = DiagnosticReadConfig.from_environment(
            {
                "R2_ACCOUNT_ID": "a" * 32,
                "R2_BUCKET": "private-ci-logs",
                "R2_READ_ACCESS_KEY_ID": "read-access",
                "R2_READ_SECRET_ACCESS_KEY": "read-secret",
                "CI_DIAGNOSTICS_PORT": "8081",
                "R2_ACCESS_KEY_ID": "write-key-must-not-be-used",
                "R2_SECRET_ACCESS_KEY": "write-secret-must-not-be-used",
            }
        )
        self.assertEqual(config.access_key_id, "read-access")
        self.assertEqual(config.secret_access_key, "read-secret")
        self.assertEqual(config.port, 8081)


class DiagnosticDeploymentContractTests(unittest.TestCase):
    def test_thin_relay_and_diagnostics_have_disjoint_secret_projection(self) -> None:
        relay = (ROOT / "charts/ci-broker/templates/deployment.yaml").read_text(encoding="utf-8")
        diagnostics = (ROOT / "charts/ci-broker/templates/diagnostics-deployment.yaml").read_text(encoding="utf-8")
        self.assertNotIn("envFrom:", relay)
        for required in (
            "GITHUB_DISPATCH_APP_ID",
            "GITHUB_DISPATCH_APP_PRIVATE_KEY",
            "AGENT_STATE_SUPABASE_URL",
            "AGENT_STATE_SUPABASE_SECRET_KEY",
            "AGENT_STATE_WEBHOOK_SECRET",
        ):
            self.assertIn(f"key: {required}", relay)
        for forbidden in (
            "GITHUB_SOURCE_APP_ID",
            "GITHUB_SOURCE_APP_PRIVATE_KEY",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_READ_ACCESS_KEY_ID",
            "R2_READ_SECRET_ACCESS_KEY",
        ):
            self.assertNotIn(f"key: {forbidden}", relay)

        for required in (
            "R2_ACCOUNT_ID",
            "R2_BUCKET",
            "R2_READ_ACCESS_KEY_ID",
            "R2_READ_SECRET_ACCESS_KEY",
        ):
            self.assertIn(f"key: {required}", diagnostics)
        for forbidden in (
            "AGENT_STATE_",
            "GITHUB_SOURCE_",
            "GITHUB_DISPATCH_",
            "key: R2_ACCESS_KEY_ID",
            "key: R2_SECRET_ACCESS_KEY",
        ):
            self.assertNotIn(forbidden, diagnostics)
        self.assertIn("/opt/ci-broker/scripts/ci/ci_diagnostics.py", diagnostics)
        self.assertIn("automountServiceAccountToken: false", diagnostics)
        self.assertIn("readOnlyRootFilesystem: true", diagnostics)

    def test_container_runs_both_self_checks_but_defaults_to_thin_relay(self) -> None:
        container = (ROOT / "broker/Containerfile").read_text(encoding="utf-8")
        self.assertIn("ci_broker.py self-check", container)
        self.assertIn("ci_diagnostics.py self-check", container)
        self.assertIn(
            'ENTRYPOINT ["python3", "/opt/ci-broker/scripts/ci/ci_broker.py", "server"]',
            container,
        )

    def test_diagnostics_contract_keeps_agent_state_metadata_only(self) -> None:
        contract = json.loads((ROOT / "contracts/ci-diagnostics.json").read_text(encoding="utf-8"))
        retrieval = contract["retrieval"]
        self.assertEqual(retrieval["mode"], "receipt-capability")
        self.assertEqual(retrieval["service_authority"], "r2-read-only")
        self.assertTrue(retrieval["requires_exact_agent_state_receipt"])
        self.assertTrue(retrieval["requires_capability_object_key"])
        self.assertTrue(retrieval["compressed_sha256_verified_before_decompression"])
        self.assertFalse(retrieval["agent_state_lookup"])
        self.assertFalse(retrieval["thin_relay_authority"])
        self.assertTrue(contract["agent_state"]["raw_logs_forbidden"])
        self.assertFalse(contract["write_policy"]["github_actions_artifact"])

        docs = (ROOT / "docs/workflows/ci-diagnostics.md").read_text(encoding="utf-8").lower()
        self.assertIn("receipt-capability", docs)
        self.assertIn("read-only", docs)
        self.assertIn("thin relay", docs)
        self.assertIn("no-store", docs)
        self.assertIn("bearer", docs)
        self.assertIn("128-bit", docs)


if __name__ == "__main__":
    unittest.main()
