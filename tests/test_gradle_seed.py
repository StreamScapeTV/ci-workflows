from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from ci_workflows import gradle_seed


SOURCE_SHA = "a" * 40
OIDC_CAPABILITY = "synthetic-oidc-capability"
OIDC_TOKEN = ".".join(("synthetic-a", "synthetic-b", "synthetic-c"))


def protected_environment(root: Path) -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": gradle_seed.EXPECTED_REPOSITORY,
        "GITHUB_REPOSITORY_ID": gradle_seed.EXPECTED_REPOSITORY_ID,
        "GITHUB_REF": gradle_seed.EXPECTED_REF,
        "GITHUB_REF_TYPE": gradle_seed.EXPECTED_REF_TYPE,
        "GITHUB_EVENT_NAME": gradle_seed.EXPECTED_EVENT,
        "GITHUB_WORKFLOW_REF": gradle_seed.EXPECTED_WORKFLOW_REF,
        "GITHUB_SHA": SOURCE_SHA,
        "GRADLE_USER_HOME": str(root),
    }


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


class FakeOidcRequester:
    def __init__(self, token: str = OIDC_TOKEN) -> None:
        self.token = token
        self.environments: list[dict[str, str]] = []

    def request_token(self, environment):
        self.environments.append(dict(environment))
        return self.token


class FakeUploader:
    def __init__(self, *, response_factory=None) -> None:
        self.calls = []
        self.response_factory = response_factory

    def upload(self, *, token: str, source_sha: str, body):
        payload = b"".join(body)
        frames = parse_stream(payload)
        self.calls.append(
            {
                "token": token,
                "source_sha": source_sha,
                "payload": payload,
                "frames": frames,
            }
        )
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


class GradleSeedSelectionTests(unittest.TestCase):
    def test_selects_only_modules_delta_and_frames_sorted_sha256_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            alpha = write_module_file(
                home,
                "modules-2/files-2.1/z/example.jar",
                b"jar-data",
            )
            beta = write_module_file(
                home,
                "modules-2/metadata-2.106/a.bin",
                b"metadata",
            )
            write_module_file(home, "modules-2/gc.properties", b"mutable")
            write_module_file(home, "modules-2/cache.lock", b"mutable")
            write_module_file(home, "transforms-4/ignored.bin", b"ignored")

            files = gradle_seed.collect_seed_files(home)
            self.assertEqual(
                [
                    "modules-2/files-2.1/z/example.jar",
                    "modules-2/metadata-2.106/a.bin",
                ],
                [item.relative_path for item in files],
            )
            self.assertEqual(
                hashlib.sha256(alpha.read_bytes()).hexdigest(),
                files[0].sha256,
            )
            self.assertEqual(
                hashlib.sha256(beta.read_bytes()).hexdigest(),
                files[1].sha256,
            )

            frames = parse_stream(b"".join(gradle_seed.framed_seed_stream(files)))
            self.assertEqual(
                [item.relative_path for item in files],
                [str(header["path"]) for header, _content in frames],
            )
            for seed_file, (header, content) in zip(files, frames, strict=True):
                self.assertEqual(seed_file.size, header["size"])
                self.assertEqual(seed_file.sha256, header["sha256"])
                self.assertEqual(seed_file.source_path.read_bytes(), content)

    def test_symlink_and_unsupported_entries_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            modules = home / "caches" / "modules-2"
            modules.mkdir(parents=True)
            outside = home / "outside"
            outside.write_text("secret", encoding="utf-8")
            os.symlink(outside, modules / "linked")
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_symlink_rejected",
            ):
                gradle_seed.collect_seed_files(home)

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                modules = home / "caches" / "modules-2"
                modules.mkdir(parents=True)
                os.mkfifo(modules / "pipe")
                with self.assertRaisesRegex(
                    gradle_seed.GradleSeedError,
                    "gradle_seed_entry_unsupported",
                ):
                    gradle_seed.collect_seed_files(home)

    def test_top_level_modules_symlink_and_relative_home_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            caches = home / "caches"
            caches.mkdir()
            real = home / "real-modules"
            real.mkdir()
            os.symlink(real, caches / "modules-2")
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_path_rejected",
            ):
                gradle_seed.collect_seed_files(home)
        with self.assertRaisesRegex(
            gradle_seed.GradleSeedError,
            "gradle_seed_home_rejected",
        ):
            gradle_seed.collect_seed_files(Path("relative"))

    def test_four_gib_boundary_is_enforced_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_module_file(home, "modules-2/a.bin", b"1234")
            with (
                mock.patch.object(gradle_seed, "MAX_UPLOAD_BYTES", 3),
                self.assertRaisesRegex(
                    gradle_seed.GradleSeedError,
                    "gradle_seed_payload_too_large",
                ),
            ):
                gradle_seed.collect_seed_files(home)

    def test_file_change_after_collection_aborts_before_terminator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = write_module_file(home, "modules-2/a.bin", b"aaaa")
            files = gradle_seed.collect_seed_files(home)
            target.write_bytes(b"bbbb")
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_file_changed",
            ):
                b"".join(gradle_seed.framed_seed_stream(files))


class GithubOidcRequesterTests(unittest.TestCase):
    def test_requests_only_exact_audience_without_ambient_endpoint_authority(self) -> None:
        response = mock.Mock()
        response.status = 200
        response.read.return_value = json.dumps({"value": OIDC_TOKEN}).encode()
        response.getheader.return_value = "application/json"

        connection = mock.Mock()
        connection.getresponse.return_value = response
        environment = {
            "ACTIONS_ID_TOKEN_REQUEST_URL": (
                "https://vstoken.actions.githubusercontent.com/example"
                "?api-version=2.0&audience=old"
            ),
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_CAPABILITY,
        }
        with mock.patch.object(
            gradle_seed.http.client,
            "HTTPSConnection",
            return_value=connection,
        ) as factory:
            token = gradle_seed.GithubOidcRequester().request_token(environment)

        self.assertEqual(OIDC_TOKEN, token)
        factory.assert_called_once_with(
            "vstoken.actions.githubusercontent.com",
            443,
            timeout=gradle_seed.HTTP_TIMEOUT_SECONDS,
        )
        method, target = connection.request.call_args.args[:2]
        headers = connection.request.call_args.kwargs["headers"]
        self.assertEqual("GET", method)
        query = parse_qs(urlsplit(target).query)
        self.assertEqual([gradle_seed.OIDC_AUDIENCE], query["audience"])
        self.assertEqual(["2.0"], query["api-version"])
        self.assertEqual(
            f"Bearer {OIDC_CAPABILITY}",
            headers["Authorization"],
        )
        connection.close.assert_called_once()

    def test_missing_capability_or_untrusted_oidc_host_is_rejected(self) -> None:
        requester = gradle_seed.GithubOidcRequester()
        with self.assertRaisesRegex(
            gradle_seed.GradleSeedError,
            "gradle_seed_oidc_capability_missing",
        ):
            requester.request_token({})
        with self.assertRaisesRegex(
            gradle_seed.GradleSeedError,
            "gradle_seed_oidc_url_rejected",
        ):
            requester.request_token(
                {
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://attacker.example/token",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-capability",
                }
            )

    def test_flux_uploader_uses_only_fixed_service_and_bearer_credential(self) -> None:
        response = mock.Mock()
        response.status = 200
        response.read.return_value = b"{}"
        response.getheader.return_value = "application/json"

        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch.object(
            gradle_seed.http.client,
            "HTTPConnection",
            return_value=connection,
        ) as factory:
            returned = gradle_seed.FluxSeedUploader().upload(
                token=OIDC_TOKEN,
                source_sha=SOURCE_SHA,
                body=(b"one", b"two"),
            )

        factory.assert_called_once_with(
            gradle_seed.FLUX_HOST,
            gradle_seed.FLUX_PORT,
            timeout=gradle_seed.HTTP_TIMEOUT_SECONDS,
        )
        args = connection.request.call_args.args
        kwargs = connection.request.call_args.kwargs
        self.assertEqual(("POST", gradle_seed.FLUX_PATH), args[:2])
        self.assertEqual((b"one", b"two"), kwargs["body"])
        self.assertTrue(kwargs["encode_chunked"])
        self.assertEqual(
            f"Bearer {OIDC_TOKEN}",
            kwargs["headers"]["Authorization"],
        )
        self.assertEqual(
            gradle_seed.CONTENT_TYPE,
            kwargs["headers"]["Content-Type"],
        )
        self.assertEqual(SOURCE_SHA, kwargs["headers"]["X-Gradle-Source-Sha"])
        self.assertEqual(200, returned.status)
        connection.close.assert_called_once()


class GradleSeedPromotionTests(unittest.TestCase):
    def _seed_home(self, directory: str) -> Path:
        home = Path(directory)
        write_module_file(home, "modules-2/files-2.1/a.jar", b"alpha")
        write_module_file(home, "modules-2/metadata-2.106/b.bin", b"beta")
        return home

    def test_protected_push_upload_verifies_response_and_emits_redacted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._seed_home(directory)
            before = sorted(
                path.relative_to(home).as_posix()
                for path in home.rglob("*")
            )
            requester = FakeOidcRequester(OIDC_TOKEN)
            uploader = FakeUploader()

            result = gradle_seed.promote_gradle_seed(
                source_sha=SOURCE_SHA,
                environment=protected_environment(home),
                oidc_requester=requester,
                uploader=uploader,
            )
            after = sorted(
                path.relative_to(home).as_posix()
                for path in home.rglob("*")
            )

            self.assertEqual(before, after)
            self.assertEqual(SOURCE_SHA, result.source_sha)
            self.assertEqual("sha256-" + "b" * 64, result.generation)
            self.assertEqual(2, result.file_count)
            self.assertEqual(len(b"alpha") + len(b"beta"), result.total_bytes)
            self.assertRegex(result.evidence_id, r"^[a-f0-9]{64}$")
            outputs = result.output_values()
            self.assertNotIn(OIDC_TOKEN, json.dumps(outputs))
            self.assertNotIn("path", json.dumps(outputs).lower())
            self.assertEqual("clean", outputs["cleanup_result"])
            self.assertEqual(OIDC_TOKEN, uploader.calls[0]["token"])
            self.assertEqual(SOURCE_SHA, uploader.calls[0]["source_sha"])

    def test_context_rejects_pr_dispatch_wrong_ref_workflow_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._seed_home(directory)
            base = protected_environment(home)
            cases = {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REF": "refs/heads/feature",
                "GITHUB_WORKFLOW_REF": (
                    "StreamScapeTV/iptv-android/.github/workflows/other.yml"
                    "@refs/heads/develop"
                ),
                "GITHUB_REPOSITORY": "StreamScapeTV/other",
                "GITHUB_SHA": "b" * 40,
            }
            for name, bad_value in cases.items():
                with self.subTest(name=name):
                    environment = dict(base)
                    environment[name] = bad_value
                    with self.assertRaisesRegex(
                        gradle_seed.GradleSeedError,
                        "gradle_seed_context_rejected",
                    ):
                        gradle_seed.promote_gradle_seed(
                            source_sha=SOURCE_SHA,
                            environment=environment,
                            oidc_requester=FakeOidcRequester(),
                            uploader=FakeUploader(),
                        )

    def test_response_source_generation_and_counts_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._seed_home(directory)
            environment = protected_environment(home)

            variants = (
                (
                    "gradle_seed_response_source_mismatch",
                    {
                        "status": "promoted",
                        "sourceSha": "b" * 40,
                        "generation": "sha256-" + "c" * 64,
                        "fileCount": 2,
                        "totalBytes": 9,
                    },
                ),
                (
                    "gradle_seed_response_generation_invalid",
                    {
                        "status": "promoted",
                        "sourceSha": SOURCE_SHA,
                        "generation": "mutable-generation",
                        "fileCount": 2,
                        "totalBytes": 9,
                    },
                ),
                (
                    "gradle_seed_response_counts_mismatch",
                    {
                        "status": "promoted",
                        "sourceSha": SOURCE_SHA,
                        "generation": "sha256-" + "c" * 64,
                        "fileCount": 999,
                        "totalBytes": 9,
                    },
                ),
            )
            for expected_code, payload in variants:
                def response_factory(_frames, payload=payload):
                    return gradle_seed.UploadResponse(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(payload).encode(),
                    )

                with (
                    self.subTest(code=expected_code),
                    self.assertRaisesRegex(
                        gradle_seed.GradleSeedError,
                        expected_code,
                    ),
                ):
                    gradle_seed.promote_gradle_seed(
                        source_sha=SOURCE_SHA,
                        environment=environment,
                        oidc_requester=FakeOidcRequester(),
                        uploader=FakeUploader(response_factory=response_factory),
                    )

    def test_transport_failure_has_no_cache_artifact_or_registry_fallback(self) -> None:
        class FailedUploader:
            def upload(self, **_kwargs):
                raise OSError("private network unavailable")

        with tempfile.TemporaryDirectory() as directory:
            home = self._seed_home(directory)
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_upload_failed",
            ):
                gradle_seed.promote_gradle_seed(
                    source_sha=SOURCE_SHA,
                    environment=protected_environment(home),
                    oidc_requester=FakeOidcRequester(),
                    uploader=FailedUploader(),
                )


if __name__ == "__main__":
    unittest.main()
