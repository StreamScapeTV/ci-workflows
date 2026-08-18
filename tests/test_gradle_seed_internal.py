from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import gradle_seed, gradle_seed_internal

SOURCE_SHA = "a" * 40


def write_module_file(root: Path, relative: str, payload: bytes) -> Path:
    target = root / "caches" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def parse_stream(payload: bytes) -> list[tuple[dict[str, object], bytes]]:
    if not payload.startswith(gradle_seed.MAGIC):
        raise AssertionError("missing magic")
    offset = len(gradle_seed.MAGIC)
    frames: list[tuple[dict[str, object], bytes]] = []
    while True:
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        offset += 4
        if length == 0:
            break
        header = json.loads(payload[offset : offset + length].decode("ascii"))
        offset += length
        size = int(header["size"])
        content = payload[offset : offset + size]
        offset += size
        frames.append((header, content))
    if offset != len(payload):
        raise AssertionError("trailing bytes")
    return frames


class FakeUploader:
    def __init__(self, *, response_factory=None) -> None:
        self.calls: list[dict[str, object]] = []
        self.response_factory = response_factory

    def upload(self, *, source_sha: str, body):
        payload = b"".join(body)
        frames = parse_stream(payload)
        self.calls.append({"source_sha": source_sha, "payload": payload, "frames": frames})
        if self.response_factory is not None:
            return self.response_factory(frames)
        total = sum(int(header["size"]) for header, _content in frames)
        return gradle_seed.UploadResponse(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "status": "promoted",
                    "sourceSha": source_sha,
                    "generation": "sha256-" + "b" * 64,
                    "fileCount": len(frames),
                    "totalBytes": total,
                }
            ).encode(),
        )


class InternalGradleSeedTests(unittest.TestCase):
    def _seed_home(self, directory: str) -> Path:
        home = Path(directory)
        write_module_file(home, "modules-2/files-2.1/a.jar", b"alpha")
        write_module_file(home, "modules-2/metadata-2.106/b.bin", b"beta")
        return home

    def test_sync_has_no_event_ref_or_oidc_gate_and_does_not_mutate_private_home(self) -> None:
        for event_name in ("pull_request", "workflow_dispatch", "push"):
            with self.subTest(event_name=event_name), tempfile.TemporaryDirectory() as directory:
                home = self._seed_home(directory)
                before = sorted(path.relative_to(home).as_posix() for path in home.rglob("*"))
                uploader = FakeUploader()
                selections: list[tuple[int, int]] = []
                result = gradle_seed_internal.sync_gradle_seed(
                    source_sha=SOURCE_SHA,
                    environment={
                        "GRADLE_USER_HOME": str(home),
                        "GITHUB_EVENT_NAME": event_name,
                        "GITHUB_REF": "refs/heads/arbitrary",
                    },
                    uploader=uploader,
                    report_selection=lambda count, size: selections.append((count, size)),
                )
                after = sorted(path.relative_to(home).as_posix() for path in home.rglob("*"))
                self.assertEqual(before, after)
                self.assertEqual([(2, len(b"alpha") + len(b"beta"))], selections)
                self.assertEqual(SOURCE_SHA, result.source_sha)
                self.assertEqual("sha256-" + "b" * 64, result.generation)
                self.assertEqual(2, result.file_count)
                self.assertEqual(len(b"alpha") + len(b"beta"), result.total_bytes)
                self.assertRegex(result.evidence_id, r"^[a-f0-9]{64}$")
                self.assertEqual(SOURCE_SHA, uploader.calls[0]["source_sha"])

    def test_internal_http_transport_sends_no_authorization_header(self) -> None:
        response = mock.Mock()
        response.status = 200
        response.read.return_value = b"{}"
        response.getheader.return_value = "application/json"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch.object(
            gradle_seed_internal.http.client,
            "HTTPConnection",
            return_value=connection,
        ) as factory:
            returned = gradle_seed_internal.InternalFluxSeedUploader().upload(
                source_sha=SOURCE_SHA,
                body=(b"one", b"two"),
            )
        factory.assert_called_once_with(
            gradle_seed_internal.FLUX_HOST,
            gradle_seed_internal.FLUX_PORT,
            timeout=gradle_seed_internal.HTTP_TIMEOUT_SECONDS,
        )
        kwargs = connection.request.call_args.kwargs
        self.assertEqual((b"one", b"two"), kwargs["body"])
        self.assertTrue(kwargs["encode_chunked"])
        self.assertEqual(gradle_seed_internal.CONTENT_TYPE, kwargs["headers"]["Content-Type"])
        self.assertEqual(SOURCE_SHA, kwargs["headers"]["X-Gradle-Source-Sha"])
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(200, returned.status)
        connection.close.assert_called_once()

    def test_sync_preserves_modules_only_filter_and_response_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._seed_home(directory)
            write_module_file(home, "transforms-4/ignored.bin", b"ignored")
            uploader = FakeUploader()
            gradle_seed_internal.sync_gradle_seed(
                source_sha=SOURCE_SHA,
                environment={"GRADLE_USER_HOME": str(home)},
                uploader=uploader,
            )
            frames = uploader.calls[0]["frames"]
            self.assertEqual(
                ["modules-2/files-2.1/a.jar", "modules-2/metadata-2.106/b.bin"],
                [str(header["path"]) for header, _content in frames],
            )
            for header, content in frames:
                self.assertEqual(hashlib.sha256(content).hexdigest(), header["sha256"])

            def bad_response(_frames):
                return gradle_seed.UploadResponse(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "status": "promoted",
                            "sourceSha": "b" * 40,
                            "generation": "sha256-" + "c" * 64,
                            "fileCount": 2,
                            "totalBytes": 9,
                        }
                    ).encode(),
                )

            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_response_source_mismatch",
            ):
                gradle_seed_internal.sync_gradle_seed(
                    source_sha=SOURCE_SHA,
                    environment={"GRADLE_USER_HOME": str(home)},
                    uploader=FakeUploader(response_factory=bad_response),
                )

    def test_rejection_statuses_distinguish_busy_from_promotion_failure(self) -> None:
        cases = (
            (409, "gradle_seed_writer_busy"),
            (422, "gradle_seed_promotion_rejected"),
            (503, "gradle_seed_upload_rejected"),
        )
        for status, code in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                home = self._seed_home(directory)

                def rejected(_frames, *, response_status=status):
                    return gradle_seed.UploadResponse(
                        status=response_status,
                        content_type="application/json",
                        body=b'{}',
                    )

                with self.assertRaisesRegex(gradle_seed.GradleSeedError, code):
                    gradle_seed_internal.sync_gradle_seed(
                        source_sha=SOURCE_SHA,
                        environment={"GRADLE_USER_HOME": str(home)},
                        uploader=FakeUploader(response_factory=rejected),
                    )

    def test_transport_failure_is_acceleration_only_to_the_caller(self) -> None:
        class FailedUploader:
            def upload(self, **_kwargs):
                raise OSError("internal cache unavailable")

        with tempfile.TemporaryDirectory() as directory:
            home = self._seed_home(directory)
            before = sorted(path.relative_to(home).as_posix() for path in home.rglob("*"))
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_upload_failed",
            ):
                gradle_seed_internal.sync_gradle_seed(
                    source_sha=SOURCE_SHA,
                    environment={"GRADLE_USER_HOME": str(home)},
                    uploader=FailedUploader(),
                )
            after = sorted(path.relative_to(home).as_posix() for path in home.rglob("*"))
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
