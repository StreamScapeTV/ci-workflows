from __future__ import annotations

from email.message import Message
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile

import yaml

from ci_workflows.private_release_asset import (
    PrivateReleaseAssetError,
    PrivateReleaseAssetSpec,
    cleanup_private_release_asset,
    materialize_private_release_asset,
    run_phase,
)
from ci_workflows.private_release_asset_action import hydrate_environment

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/materialize-private-release-asset/action.yml"
GATEWAY = ROOT / "scripts/ci/ciw.py"
COMMIT = "a" * 40
TOKEN = "synthetic-installation-token"


class Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.offset = 0

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            result = self.body[self.offset :]
            self.offset = len(self.body)
            return result
        result = self.body[self.offset : self.offset + amount]
        self.offset += len(result)
        return result

    def getcode(self) -> int:
        return self.status


class Provider:
    def __init__(self, token: str) -> None:
        self.token = token
        self.commits: list[tuple[str, str]] = []

    def tag_ref(self, repository: str, tag_name: str) -> dict[str, object]:
        return {"object": {"type": "commit", "sha": COMMIT}}

    def tag_object(self, repository: str, sha: str) -> dict[str, object]:
        raise AssertionError("lightweight tag must not read a tag object")

    def commit(self, repository: str, sha: str) -> dict[str, object]:
        self.commits.append((repository, sha))
        return {"sha": sha}


def spec(*, sha256: str, repository: str = "StreamScapeTV/example-media") -> PrivateReleaseAssetSpec:
    return PrivateReleaseAssetSpec.parse(
        {
            "repository": repository,
            "tag": "v1.2.1",
            "commit_sha": COMMIT,
            "asset_name": "example-media-1.2.1-apple-binary.zip",
            "sha256": sha256,
            "archive_subpath": "ExampleMediaApple",
            "destination": "Vendor/ExampleMediaApple",
            "id": "example-media-binary",
        }
    )


def archive_bytes(*, unsafe: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if unsafe:
            archive.writestr("../escape.txt", "bad")
        else:
            archive.writestr("ExampleMediaApple/Package.swift", "// synthetic package\n")
            archive.writestr("ExampleMediaApple/Distribution.json", "{}\n")
    return buffer.getvalue()


def extractor(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)


def initialize_source(root: Path) -> tuple[Path, Path]:
    source = root / "source"
    state = root / "state"
    source.mkdir()
    state.mkdir()
    (source / ".gitignore").write_text("Vendor/\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q"],
        cwd=source,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return source, state


class ApiOpener:
    def __init__(self, archive: bytes) -> None:
        self.archive = archive
        self.requests: list[object] = []

    def __call__(self, request: object, timeout: int = 0) -> Response:
        self.requests.append(request)
        url = str(request.full_url)  # type: ignore[attr-defined]
        if "/releases/tags/" in url:
            body = json.dumps(
                {
                    "tag_name": "v1.2.1",
                    "draft": False,
                    "assets": [
                        {
                            "id": 71,
                            "name": "example-media-1.2.1-apple-binary.zip",
                            "size": len(self.archive),
                            "state": "uploaded",
                        }
                    ],
                }
            ).encode("utf-8")
            return Response(body)
        if "/releases/assets/71" in url:
            return Response(self.archive)
        raise AssertionError(url)


class PrivateReleaseAssetTests(unittest.TestCase):
    def test_exact_release_asset_is_verified_materialized_and_cleanup_is_source_bounded(self) -> None:
        payload = archive_bytes()
        digest = __import__("hashlib").sha256(payload).hexdigest()
        api = ApiOpener(payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state = initialize_source(root)
            result = materialize_private_release_asset(
                spec=spec(sha256=digest),
                token=TOKEN,
                source_root=source,
                state_root=state,
                api_opener=api,
                provider_factory=Provider,
                extractor=extractor,
            )
            package = source / "Vendor/ExampleMediaApple"
            self.assertEqual(result.release_commit, COMMIT)
            self.assertEqual(result.sha256, digest)
            self.assertEqual(result.downloaded_bytes, len(payload))
            self.assertTrue((package / "Package.swift").is_file())
            self.assertFalse((state / "release-asset").exists())

            cleanup_private_release_asset(result)
            self.assertFalse(package.exists())
            self.assertTrue(source.is_dir())
            self.assertTrue((source / ".gitignore").is_file())

        for request in api.requests:
            headers = {
                str(key).lower(): str(value)
                for key, value in request.header_items()  # type: ignore[attr-defined]
            }
            self.assertEqual(headers.get("authorization"), f"Bearer {TOKEN}")

    def test_full_owner_name_is_supported_without_streamscapetv_prefix(self) -> None:
        release = spec(sha256="1" * 64, repository="OtherOrg/private-media")
        self.assertEqual(release.repository, "OtherOrg/private-media")

    def test_hashes_are_exact_lowercase_not_silently_normalized(self) -> None:
        raw = spec(sha256="1" * 64).as_payload()
        for patch, code in (
            ({"commit_sha": COMMIT.upper()}, "release_commit_invalid"),
            ({"sha256": ("a" * 64).upper()}, "release_asset_checksum_invalid"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(PrivateReleaseAssetError, code):
                PrivateReleaseAssetSpec.parse({**raw, **patch})

    def test_checksum_failure_leaves_no_destination(self) -> None:
        payload = archive_bytes()
        api = ApiOpener(payload)
        with tempfile.TemporaryDirectory() as temporary:
            source, state = initialize_source(Path(temporary))
            with self.assertRaisesRegex(PrivateReleaseAssetError, "release_asset_checksum_mismatch"):
                materialize_private_release_asset(
                    spec=spec(sha256="0" * 64),
                    token=TOKEN,
                    source_root=source,
                    state_root=state,
                    api_opener=api,
                    provider_factory=Provider,
                    extractor=extractor,
                )
            self.assertFalse((source / "Vendor/ExampleMediaApple").exists())
            self.assertFalse((state / "release-asset").exists())

    def test_archive_traversal_is_rejected_before_extraction(self) -> None:
        payload = archive_bytes(unsafe=True)
        digest = __import__("hashlib").sha256(payload).hexdigest()
        api = ApiOpener(payload)
        called = False

        def unexpected_extractor(_archive: Path, _destination: Path) -> None:
            nonlocal called
            called = True

        with tempfile.TemporaryDirectory() as temporary:
            source, state = initialize_source(Path(temporary))
            with self.assertRaisesRegex(PrivateReleaseAssetError, "release_archive_path_invalid"):
                materialize_private_release_asset(
                    spec=spec(sha256=digest),
                    token=TOKEN,
                    source_root=source,
                    state_root=state,
                    api_opener=api,
                    provider_factory=Provider,
                    extractor=unexpected_extractor,
                )
        self.assertFalse(called)

    def test_release_asset_redirect_does_not_forward_github_token(self) -> None:
        payload = archive_bytes()
        digest = __import__("hashlib").sha256(payload).hexdigest()
        asset_requests: list[object] = []

        def api_opener(request: object, timeout: int = 0) -> Response:
            url = str(request.full_url)  # type: ignore[attr-defined]
            if "/releases/tags/" in url:
                return Response(
                    json.dumps(
                        {
                            "tag_name": "v1.2.1",
                            "draft": False,
                            "assets": [
                                {
                                    "id": 71,
                                    "name": "example-media-1.2.1-apple-binary.zip",
                                    "size": len(payload),
                                    "state": "uploaded",
                                }
                            ],
                        }
                    ).encode("utf-8")
                )
            headers = Message()
            headers["Location"] = "https://release-assets.githubusercontent.com/signed/object?sig=synthetic"
            raise urllib.error.HTTPError(url, 302, "Found", headers, None)

        def asset_opener(request: object, timeout: int = 0) -> Response:
            asset_requests.append(request)
            return Response(payload)

        with tempfile.TemporaryDirectory() as temporary:
            source, state = initialize_source(Path(temporary))
            result = materialize_private_release_asset(
                spec=spec(sha256=digest),
                token=TOKEN,
                source_root=source,
                state_root=state,
                api_opener=api_opener,
                asset_opener=asset_opener,
                provider_factory=Provider,
                extractor=extractor,
            )
            cleanup_private_release_asset(result)

        self.assertEqual(len(asset_requests), 1)
        headers = {
            str(key).lower(): str(value)
            for key, value in asset_requests[0].header_items()  # type: ignore[attr-defined]
        }
        self.assertNotIn("authorization", headers)

    def test_spec_is_exact_and_plan_rejects_mixed_dependency_kinds(self) -> None:
        raw = spec(sha256="1" * 64).as_payload()
        with self.assertRaisesRegex(PrivateReleaseAssetError, "private_release_asset_invalid"):
            PrivateReleaseAssetSpec.parse({**raw, "token": "forbidden"})

        env = {
            "INPUT_RELEASE_ASSET_REPOSITORY": raw["repository"],
            "INPUT_RELEASE_ASSET_TAG": raw["tag"],
            "INPUT_RELEASE_ASSET_COMMIT_SHA": raw["commit_sha"],
            "INPUT_RELEASE_ASSET_NAME": raw["asset_name"],
            "INPUT_RELEASE_ASSET_SHA256": raw["sha256"],
            "INPUT_RELEASE_ASSET_ARCHIVE_SUBPATH": raw["archive_subpath"],
            "INPUT_RELEASE_ASSET_DESTINATION": raw["destination"],
            "INPUT_RELEASE_ASSET_ID": raw["id"],
            "INPUT_PRIVATE_DEPENDENCY_REPOSITORY": "StreamScapeTV/source-dependency",
        }
        with self.assertRaisesRegex(PrivateReleaseAssetError, "private_dependency_kind_conflict"):
            run_phase("plan", env)

    def test_config_source_accepts_identical_profiles_and_rejects_ambiguity_or_explicit_mix(self) -> None:
        raw = spec(sha256="1" * 64).as_payload()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            github = source / ".github"
            github.mkdir(parents=True)
            config = github / "central-ci.json"
            base_profile = {
                "workflow_key": "validation.apple",
                "capability": "apple-host-test",
                "workspace": "Example.xcworkspace",
                "scheme": "Example",
                "test_target": "ExampleTests/Smoke",
            }
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "project_key": "example-project",
                        "profiles": {
                            "host": {**base_profile, "private_release_asset": raw},
                            "tag": {**base_profile, "private_release_asset": raw},
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "INPUT_CONFIG_WORKFLOW_KEY": "validation.apple",
                "INPUT_SOURCE_ROOT": str(source),
            }
            hydrated = hydrate_environment(env)
            self.assertEqual(hydrated["INPUT_RELEASE_ASSET_REPOSITORY"], raw["repository"])
            self.assertEqual(hydrated["INPUT_RELEASE_ASSET_SHA256"], raw["sha256"])

            different = {**raw, "id": "other-binary"}
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "project_key": "example-project",
                        "profiles": {
                            "host": {**base_profile, "private_release_asset": raw},
                            "tag": {**base_profile, "private_release_asset": different},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PrivateReleaseAssetError, "private_release_asset_ambiguous"):
                hydrate_environment(env)

            with self.assertRaisesRegex(PrivateReleaseAssetError, "private_release_asset_source_conflict"):
                hydrate_environment(
                    {
                        **env,
                        "INPUT_RELEASE_ASSET_REPOSITORY": raw["repository"],
                    }
                )

    def test_execute_cleanup_and_residue_use_marker_without_retaining_token(self) -> None:
        payload = archive_bytes()
        digest = __import__("hashlib").sha256(payload).hexdigest()
        release = spec(sha256=digest)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state = initialize_source(root)
            output = root / "output"
            output.touch()
            env = {
                "INPUT_RELEASE_ASSET_REPOSITORY": release.repository,
                "INPUT_RELEASE_ASSET_TAG": release.tag,
                "INPUT_RELEASE_ASSET_COMMIT_SHA": release.commit_sha,
                "INPUT_RELEASE_ASSET_NAME": release.asset_name,
                "INPUT_RELEASE_ASSET_SHA256": release.sha256,
                "INPUT_RELEASE_ASSET_ARCHIVE_SUBPATH": release.archive_subpath,
                "INPUT_RELEASE_ASSET_DESTINATION": release.destination,
                "INPUT_RELEASE_ASSET_ID": release.dependency_id,
                "INPUT_PRIVATE_DEPENDENCY_REPOSITORY": "",
                "INPUT_SOURCE_ROOT": str(source),
                "CI_WORKFLOW_ROOT": str(state),
                "PRIVATE_RELEASE_ASSET_TOKEN": TOKEN,
                "GITHUB_OUTPUT": str(output),
            }
            materialized = mock.Mock(
                source_root=source,
                destination=source / "Vendor/ExampleMediaApple",
                release_commit=release.commit_sha,
                sha256=release.sha256,
                downloaded_bytes=123,
            )
            materialized.destination.mkdir(parents=True)
            with mock.patch(
                "ci_workflows.private_release_asset.materialize_private_release_asset",
                return_value=materialized,
            ):
                run_phase("execute", env)
            marker = state / "release-assets/example-media-binary.json"
            self.assertTrue(marker.is_file())
            self.assertNotIn(TOKEN, marker.read_text(encoding="utf-8"))

            run_phase("cleanup", env)
            release_state = state / "release-assets"
            self.assertFalse(release_state.exists())
            run_phase("residue", env)
            self.assertFalse(release_state.exists(), "residue check must not create state")
            self.assertFalse(materialized.destination.exists())
            self.assertFalse(marker.exists())

    def test_composite_action_and_gateway_are_thin_and_secret_name_neutral(self) -> None:
        action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
        self.assertEqual(action["runs"]["using"], "composite")
        self.assertIn("config_workflow_key", action["inputs"])
        self.assertNotIn("token", action["inputs"])
        text = ACTION.read_text(encoding="utf-8")
        self.assertIn("release-asset", text)
        self.assertIn("scripts/ci/ciw.py", text)
        self.assertNotIn("${{ inputs.token }}", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("curl ", text)
        self.assertNotIn("gh ", text)
        gateway = GATEWAY.read_text(encoding="utf-8")
        self.assertIn('arguments[:1] == ["release-asset"]', gateway)
        self.assertIn("private_release_asset_main(arguments[1:])", gateway)


if __name__ == "__main__":
    unittest.main()
