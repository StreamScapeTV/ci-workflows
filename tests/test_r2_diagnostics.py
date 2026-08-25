from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ci_workflows import r2_diagnostics
from ci_workflows.r2_diagnostics import (
    R2DiagnosticError,
    download_private_diagnostic,
    upload_private_diagnostic,
)

ROOT = Path(__file__).resolve().parents[1]


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class R2DiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = "a" * 32
        self.bucket = "ci-diagnostics"
        self.access_key = "ACCESSKEY"
        self.secret_key = "synthetic-secret"
        self.when = datetime(2026, 8, 23, 3, 0, tzinfo=timezone.utc)

    def _upload(self, path: Path, opener: object, *, request_id: str = "request-1"):
        return upload_private_diagnostic(
            diagnostic_path=path,
            request_id=request_id,
            run_id=123,
            attempt=1,
            account_id=self.account,
            bucket=self.bucket,
            access_key_id=self.access_key,
            secret_access_key=self.secret_key,
            opener=opener,
            clock=lambda: self.when,
        )

    def test_contract_matches_fixed_runtime_bounds(self) -> None:
        contract = json.loads(
            (ROOT / "contracts/r2-diagnostics.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["provider"], "cloudflare-r2")
        self.assertEqual(contract["api"], "s3-compatible")
        self.assertEqual(contract["region"], "auto")
        self.assertEqual(contract["storage_class"], r2_diagnostics.STORAGE_CLASS)
        self.assertEqual(contract["object_prefix"], r2_diagnostics.OBJECT_PREFIX)
        self.assertEqual(
            contract["object_format"],
            "ci-diagnostics/<request_id>/<128-bit-capability>/<run_id>-<attempt>.log.gz",
        )
        self.assertEqual(contract["object_capability"]["bits"], 128)
        self.assertEqual(
            contract["object_capability"]["key_authority"],
            "r2-write-secret-only",
        )
        self.assertFalse(contract["object_capability"]["read_service_can_mint"])
        self.assertEqual(contract["max_raw_bytes"], r2_diagnostics.MAX_RAW_BYTES)
        self.assertEqual(
            contract["max_compressed_bytes"],
            r2_diagnostics.MAX_COMPRESSED_BYTES,
        )
        self.assertEqual(contract["max_objects_per_request"], 1)
        self.assertTrue(contract["readback_required"])
        self.assertEqual(
            contract["environment"],
            {
                "account_id": "R2_ACCOUNT_ID",
                "bucket": "R2_BUCKET",
                "access_key_id": "R2_ACCESS_KEY_ID",
                "secret_access_key": "R2_SECRET_ACCESS_KEY",
            },
        )
        self.assertEqual(contract["runtime_operations"], ["put-object", "get-object"])
        self.assertEqual(contract["required_remote_lifecycle_max_days"], 7)
        self.assertIn("lifecycle-mutation", contract["forbidden_runtime_operations"])
        self.assertIn("presigned-url", contract["forbidden_runtime_operations"])

    def test_success_puts_capability_object_and_verifies_readback(self) -> None:
        stored: dict[str, bytes] = {}
        requests: list[object] = []

        def opener(request: object, timeout: int) -> _Response:
            self.assertEqual(timeout, 30)
            requests.append(request)
            if request.get_method() == "PUT":  # type: ignore[attr-defined]
                stored["data"] = request.data  # type: ignore[attr-defined]
                return _Response(b"")
            return _Response(stored["data"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.log"
            path.write_text("private diagnostics\n", encoding="utf-8")
            result = self._upload(path, opener)

        self.assertEqual(
            [request.get_method() for request in requests],  # type: ignore[attr-defined]
            ["PUT", "GET"],
        )
        self.assertEqual(
            requests[0].get_header("X-amz-storage-class"),  # type: ignore[attr-defined]
            "STANDARD",
        )
        authorization = requests[0].get_header("Authorization")  # type: ignore[attr-defined]
        self.assertTrue(authorization.startswith("AWS4-HMAC-SHA256 "))
        self.assertNotIn(self.secret_key, authorization)
        self.assertTrue(r2_diagnostics.is_capability_object_key(result.object_key))
        parts = result.object_key.split("/")
        self.assertEqual(parts[0:2], ["ci-diagnostics", "request-1"])
        self.assertRegex(parts[2], r"^[0-9a-f]{32}$")
        self.assertEqual(parts[3], "123-1.log.gz")
        self.assertNotIn(self.secret_key, result.object_key)
        self.assertEqual(len(result.sha256), 64)
        self.assertEqual(result.compressed_bytes, len(stored["data"]))

    def test_object_capability_is_deterministic_and_write_secret_bound(self) -> None:
        kwargs = {
            "request_id": "request-1",
            "run_id": 123,
            "attempt": 1,
            "digest": "a" * 64,
        }
        first = r2_diagnostics._object_capability(  # type: ignore[attr-defined]
            **kwargs,
            secret_access_key="write-secret-one",
        )
        replay = r2_diagnostics._object_capability(  # type: ignore[attr-defined]
            **kwargs,
            secret_access_key="write-secret-one",
        )
        other = r2_diagnostics._object_capability(  # type: ignore[attr-defined]
            **kwargs,
            secret_access_key="write-secret-two",
        )
        self.assertEqual(first, replay)
        self.assertNotEqual(first, other)
        self.assertRegex(first, r"^[0-9a-f]{32}$")

    def test_legacy_object_remains_internal_download_compatible(self) -> None:
        compressed = b"historical-compressed-object"
        digest = hashlib.sha256(compressed).hexdigest()
        requests: list[object] = []

        def opener(request: object, timeout: int) -> _Response:
            requests.append(request)
            self.assertEqual(timeout, 30)
            return _Response(compressed)

        result = download_private_diagnostic(
            object_key="ci-diagnostics/request-1/123-1.log.gz",
            expected_sha256=digest,
            account_id=self.account,
            bucket=self.bucket,
            access_key_id=self.access_key,
            secret_access_key=self.secret_key,
            opener=opener,
            clock=lambda: self.when,
        )
        self.assertEqual(result, compressed)
        self.assertEqual(len(requests), 1)

    def test_unsafe_request_id_is_rejected_before_network_access(self) -> None:
        calls: list[object] = []

        def opener(*_args: object, **_kwargs: object) -> _Response:
            calls.append(object())
            raise AssertionError("network must not be reached")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.log"
            path.write_bytes(b"x")
            with self.assertRaises(R2DiagnosticError) as raised:
                self._upload(path, opener, request_id="../private")

        self.assertEqual(raised.exception.code, "invalid_request_id")
        self.assertEqual(calls, [])

    def test_raw_oversize_is_rejected_before_network_access(self) -> None:
        calls: list[object] = []

        def opener(*_args: object, **_kwargs: object) -> _Response:
            calls.append(object())
            raise AssertionError("network must not be reached")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.log"
            path.write_bytes(b"abcd")
            with mock.patch.object(r2_diagnostics, "MAX_RAW_BYTES", 3):
                with self.assertRaises(R2DiagnosticError) as raised:
                    self._upload(path, opener)

        self.assertEqual(raised.exception.code, "diagnostic_raw_too_large")
        self.assertEqual(calls, [])

    def test_http_failure_projects_only_sanitized_error_code(self) -> None:
        def opener(_request: object, timeout: int) -> _Response:
            self.assertEqual(timeout, 30)
            raise urllib.error.HTTPError(
                "https://example.invalid/private",
                403,
                "forbidden",
                {},
                None,
            )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.log"
            path.write_bytes(b"private")
            with self.assertRaises(R2DiagnosticError) as raised:
                self._upload(path, opener)

        self.assertEqual(raised.exception.code, "r2_upload_http_403")
        rendered = str(raised.exception)
        for sensitive in (
            self.bucket,
            "request-1",
            self.access_key,
            self.secret_key,
        ):
            self.assertNotIn(sensitive, rendered)

    def test_readback_mismatch_fails_closed(self) -> None:
        def opener(request: object, timeout: int) -> _Response:
            self.assertEqual(timeout, 30)
            if request.get_method() == "PUT":  # type: ignore[attr-defined]
                return _Response(b"")
            return _Response(b"wrong")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.log"
            path.write_bytes(b"private")
            with self.assertRaises(R2DiagnosticError) as raised:
                self._upload(path, opener)

        self.assertIn(
            raised.exception.code,
            {
                "r2_readback_size_mismatch",
                "r2_readback_digest_mismatch",
            },
        )

    def test_invalid_account_and_bucket_fail_before_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.log"
            path.write_bytes(b"x")
            cases = (
                ("bad", self.bucket, "invalid_r2_account"),
                (self.account, "Bad_Bucket", "invalid_r2_bucket"),
            )
            for account, bucket, expected in cases:
                with self.subTest(expected=expected):
                    with self.assertRaises(R2DiagnosticError) as raised:
                        upload_private_diagnostic(
                            diagnostic_path=path,
                            request_id="request-1",
                            run_id=123,
                            attempt=1,
                            account_id=account,
                            bucket=bucket,
                            access_key_id=self.access_key,
                            secret_access_key=self.secret_key,
                            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                                AssertionError("network must not be reached")
                            ),
                            clock=lambda: self.when,
                        )
                    self.assertEqual(raised.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
