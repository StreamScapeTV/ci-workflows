from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from ci_workflows.network_primitives import (
    NetworkPrimitiveError,
    NetworkTransportError,
    download_file,
    extract_archive,
    http_get,
    http_head,
    http_json,
    verify_file,
)


class FakeStream:
    def __init__(self, status=200, body=b"", headers=None, url="https://example.test/data", close_error=False):
        self.status = status
        self.body = io.BytesIO(body)
        self.headers = dict(headers or {})
        self.url = url
        self.closed = False
        self.close_error = close_error

    def read(self, size=-1):
        return self.body.read(size)

    def close(self):
        self.closed = True
        if self.close_error:
            raise OSError("close failed")


class FakeTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def open(self, method, url, headers, timeout_seconds):
        self.calls.append((method, url, dict(headers), timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class NetworkPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        self.extracts = self.root / "extracts"
        self.extracts.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_get_retries_retryable_status_and_returns_bounded_metadata(self):
        sleeps = []
        first = FakeStream(503, b"busy")
        second = FakeStream(
            200,
            b"hello",
            {"Content-Type": "text/plain; charset=utf-8", "Content-Length": "5", "ETag": '"v1"'},
        )
        result = http_get(
            "https://example.test/data",
            transport=FakeTransport([first, second]),
            max_attempts=2,
            initial_backoff_seconds=0.5,
            maximum_backoff_seconds=1.0,
            sleeper=sleeps.append,
        )
        self.assertEqual(2, result.attempts)
        self.assertEqual(200, result.status)
        self.assertEqual(b"hello", result.body)
        self.assertEqual("text/plain", result.content_type)
        self.assertEqual(hashlib.sha256(b"hello").hexdigest(), result.body_sha256)
        self.assertEqual([0.5], sleeps)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)

    def test_head_and_json_are_typed_and_json_body_is_not_in_repr(self):
        head = http_head(
            "https://example.test/meta",
            transport=FakeTransport([FakeStream(200, b"ignored", {"Content-Length": "123"})]),
        )
        self.assertEqual(0, head.body_size)
        payload = b'{"ok":true,"count":2}'
        parsed = http_json(
            "https://example.test/meta",
            transport=FakeTransport(
                [FakeStream(200, payload, {"Content-Type": "application/problem+json", "Content-Length": str(len(payload))})]
            ),
        )
        self.assertEqual({"ok": True, "count": 2}, parsed.value)
        self.assertNotIn('"ok"', repr(parsed))

    def test_fixed_secret_environment_is_injected_without_repr_or_error_leakage(self):
        transport = FakeTransport([FakeStream(200, b"ok", {"Content-Length": "2"})])
        result = http_get(
            "https://example.test/private",
            transport=transport,
            secret_header_name="Authorization",
            environment={"CI_HTTP_AUTH_HEADER_VALUE": "Bearer super-secret-value"},
        )
        self.assertEqual("Bearer super-secret-value", transport.calls[0][2]["Authorization"])
        self.assertNotIn("super-secret-value", repr(result))
        with self.assertRaises(NetworkPrimitiveError) as caught:
            http_get(
                "https://example.test/private",
                transport=FakeTransport([NetworkTransportError()]),
                secret_header_name="Authorization",
                environment={"CI_HTTP_AUTH_HEADER_VALUE": "Bearer super-secret-value"},
                max_attempts=1,
            )
        self.assertEqual("http_transport_failed", caught.exception.code)
        self.assertNotIn("super-secret-value", str(caught.exception))

    def test_cross_origin_redirect_strips_secret_when_explicitly_allowed(self):
        redirect = FakeStream(302, headers={"Location": "https://cdn.example.net/file"})
        final = FakeStream(200, b"x", {"Content-Length": "1"}, url="https://cdn.example.net/file")
        transport = FakeTransport([redirect, final])
        result = http_get(
            "https://example.test/file",
            transport=transport,
            headers={"Accept": "application/octet-stream"},
            secret_header_name="X-Api-Key",
            environment={"CI_HTTP_AUTH_HEADER_VALUE": "hidden"},
            allow_cross_origin_redirects=True,
        )
        self.assertEqual(1, result.redirects)
        self.assertEqual("hidden", transport.calls[0][2]["X-Api-Key"])
        self.assertNotIn("X-Api-Key", transport.calls[1][2])
        self.assertEqual("application/octet-stream", transport.calls[1][2]["Accept"])

    def test_cross_origin_and_https_downgrade_redirects_fail_closed(self):
        with self.assertRaisesRegex(NetworkPrimitiveError, "http_cross_origin_redirect_rejected"):
            http_get(
                "https://example.test/a",
                transport=FakeTransport([FakeStream(302, headers={"Location": "https://other.test/b"})]),
            )
        with self.assertRaisesRegex(NetworkPrimitiveError, "http_redirect_downgrade_rejected"):
            http_get(
                "https://example.test/a",
                transport=FakeTransport([FakeStream(302, headers={"Location": "http://example.test/b"})]),
                allow_http=True,
            )

    def test_malformed_response_and_download_close_failure_leave_no_state(self):
        malformed = FakeStream("200", b"x")
        with self.assertRaisesRegex(NetworkPrimitiveError, "http_status_invalid"):
            http_get(
                "https://example.test/a",
                transport=FakeTransport([malformed]),
            )
        self.assertTrue(malformed.closed)

        body = b"ok"
        failing_close = FakeStream(
            200,
            body,
            {"Content-Length": str(len(body))},
            close_error=True,
        )
        with self.assertRaisesRegex(NetworkPrimitiveError, "download_cleanup_failed"):
            download_file(
                "https://example.test/file",
                destination_root=self.downloads,
                relative_path="file.bin",
                transport=FakeTransport([failing_close]),
                expected_sha256=hashlib.sha256(body).hexdigest(),
            )
        self.assertFalse((self.downloads / "file.bin").exists())
        self.assertFalse(any(".partial" in item.name for item in self.downloads.iterdir()))

    def test_download_verifies_content_size_checksum_and_atomically_finalizes(self):
        body = b"archive-bytes"
        digest = hashlib.sha256(body).hexdigest()
        result = download_file(
            "https://example.test/tool.zip",
            destination_root=self.downloads,
            relative_path="tool.zip",
            transport=FakeTransport(
                [
                    FakeStream(
                        200,
                        body,
                        {
                            "Content-Length": str(len(body)),
                            "Content-Type": "application/zip",
                            "Content-Encoding": "identity",
                        },
                    )
                ]
            ),
            expected_sha256=digest,
            expected_size=len(body),
            expected_content_type="application/zip",
        )
        self.assertEqual(digest, result.sha256)
        self.assertEqual(body, (self.downloads / "tool.zip").read_bytes())
        self.assertEqual([], list(self.downloads.glob("*.partial")))
        self.assertEqual(digest, verify_file(self.downloads / "tool.zip", expected_sha256=digest).sha256)

    def test_download_failure_removes_partial_and_never_replaces_target(self):
        body = b"wrong"
        with self.assertRaisesRegex(NetworkPrimitiveError, "checksum_mismatch"):
            download_file(
                "https://example.test/tool",
                destination_root=self.downloads,
                relative_path="tool.bin",
                transport=FakeTransport([FakeStream(200, body, {"Content-Length": str(len(body))})]),
                expected_sha256="0" * 64,
            )
        self.assertFalse((self.downloads / "tool.bin").exists())
        self.assertFalse(any(".partial" in item.name for item in self.downloads.iterdir()))

    def test_download_rejects_traversal_symlink_parent_and_direct_secret_header(self):
        with self.assertRaisesRegex(NetworkPrimitiveError, "download_path_invalid"):
            download_file(
                "https://example.test/a",
                destination_root=self.downloads,
                relative_path="../escape",
                transport=FakeTransport([]),
            )
        outside = self.root / "outside"
        outside.mkdir()
        (self.downloads / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(NetworkPrimitiveError, "download_path_invalid"):
            download_file(
                "https://example.test/a",
                destination_root=self.downloads,
                relative_path="link/file",
                transport=FakeTransport([]),
            )
        with self.assertRaisesRegex(NetworkPrimitiveError, "http_secret_header_requires_environment"):
            http_get(
                "https://example.test/a",
                headers={"Authorization": "secret"},
                transport=FakeTransport([]),
            )

    def _zip(self, name="archive.zip", members=None):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for item_name, body in members or [("dir/a.txt", b"a"), ("b.txt", b"bb")]:
                archive.writestr(item_name, body)
        return path

    def _tar(self, name="archive.tar", symlink=False):
        path = self.root / name
        with tarfile.open(path, "w") as archive:
            data = b"hello"
            info = tarfile.TarInfo("pkg/file.txt")
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
            if symlink:
                link = tarfile.TarInfo("pkg/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "../outside"
                archive.addfile(link)
        return path

    def test_zip_extracts_safely_and_returns_deterministic_metadata(self):
        archive = self._zip()
        result = extract_archive(
            archive,
            archive_format="zip",
            destination_root=self.extracts,
            relative_destination="unzipped",
        )
        self.assertEqual(2, result.file_count)
        self.assertEqual(3, result.total_bytes)
        self.assertEqual(b"a", (self.extracts / "unzipped" / "dir" / "a.txt").read_bytes())
        self.assertEqual(64, len(result.manifest_sha256))
        self.assertFalse(any(item.name.startswith("ciw-extract-") for item in self.extracts.iterdir()))

    def test_zip_traversal_and_zip_symlink_are_rejected_without_stage_residue(self):
        bad = self._zip("bad.zip", [("../escape", b"x")])
        with self.assertRaisesRegex(NetworkPrimitiveError, "archive_member_path_invalid"):
            extract_archive(
                bad,
                archive_format="zip",
                destination_root=self.extracts,
                relative_destination="bad",
            )
        symlink_zip = self.root / "symlink.zip"
        with zipfile.ZipFile(symlink_zip, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../outside")
        with self.assertRaisesRegex(NetworkPrimitiveError, "archive_special_member_rejected"):
            extract_archive(
                symlink_zip,
                archive_format="zip",
                destination_root=self.extracts,
                relative_destination="bad2",
            )
        self.assertEqual([], list(self.extracts.iterdir()))

    def test_tar_extracts_regular_files_and_rejects_symlink(self):
        good = self._tar()
        result = extract_archive(
            good,
            archive_format="tar",
            destination_root=self.extracts,
            relative_destination="tarred",
        )
        target = self.extracts / "tarred" / "pkg" / "file.txt"
        self.assertEqual(b"hello", target.read_bytes())
        self.assertTrue(target.stat().st_mode & stat.S_IXUSR)
        bad = self._tar("bad.tar", symlink=True)
        with self.assertRaisesRegex(NetworkPrimitiveError, "archive_special_member_rejected"):
            extract_archive(
                bad,
                archive_format="tar",
                destination_root=self.extracts,
                relative_destination="bad",
            )
        self.assertFalse((self.extracts / "bad").exists())

    def test_archive_member_and_expansion_limits_fail_before_finalize(self):
        archive = self._zip("limits.zip", [("a", b"12"), ("b", b"34")])
        with self.assertRaisesRegex(NetworkPrimitiveError, "archive_too_many_members"):
            extract_archive(
                archive,
                archive_format="zip",
                destination_root=self.extracts,
                relative_destination="count",
                maximum_members=1,
            )
        with self.assertRaisesRegex(NetworkPrimitiveError, "archive_expansion_too_large"):
            extract_archive(
                archive,
                archive_format="zip",
                destination_root=self.extracts,
                relative_destination="size",
                maximum_total_bytes=3,
            )
        self.assertEqual([], list(self.extracts.iterdir()))


if __name__ == "__main__":
    unittest.main()
