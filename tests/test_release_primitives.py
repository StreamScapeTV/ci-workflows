from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

from ci_workflows.release_primitives import (
    GitHubAssetResult,
    GitHubReleaseRequest,
    GitHubReleaseResult,
    GitProcessResult,
    ReleasePrimitiveError,
    create_or_update_github_release,
    derive_version_from_ref,
    inspect_git_tag,
    upload_github_release_asset,
)


class FakeGitRunner:
    def __init__(
        self,
        *,
        tag_line: str,
        commit_sha: str,
        commit_line: str,
        fail_tag: bool = False,
    ) -> None:
        self.tag_line = tag_line
        self.commit_sha = commit_sha
        self.commit_line = commit_line
        self.fail_tag = fail_tag
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, arguments: tuple[str, ...], cwd: Path) -> GitProcessResult:
        self.calls.append(tuple(arguments))
        if arguments[0] == "for-each-ref":
            if self.fail_tag:
                return GitProcessResult(1, "", "sensitive stderr must not escape")
            return GitProcessResult(0, self.tag_line + "\n", "")
        if arguments[0] == "rev-parse":
            return GitProcessResult(0, self.commit_sha + "\n", "")
        if arguments[0] == "show":
            return GitProcessResult(0, self.commit_line + "\n", "")
        raise AssertionError(arguments)


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, amount: int = -1) -> bytes:
        return self._body if amount < 0 else self._body[:amount]

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: int) -> FakeResponse:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("unexpected GitHub request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome


class ReleasePrimitiveTests(unittest.TestCase):
    def test_version_derivation_accepts_tag_or_full_ref(self) -> None:
        direct = derive_version_from_ref("v1.2.3-rc.1+build.7")
        full = derive_version_from_ref("refs/tags/1.2.3")
        self.assertEqual(direct.tag, "v1.2.3-rc.1+build.7")
        self.assertEqual(direct.version, "1.2.3-rc.1+build.7")
        self.assertEqual(direct.ref, "refs/tags/v1.2.3-rc.1+build.7")
        self.assertEqual(full.tag, "1.2.3")
        self.assertEqual(full.version, "1.2.3")

    def test_version_derivation_rejects_branch_and_noncanonical_semver(self) -> None:
        for value in (
            "refs/heads/main",
            "refs/tags/01.2.3",
            "v1.02.3",
            "1.2",
            "1.2.3/other",
            " 1.2.3",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ReleasePrimitiveError):
                    derive_version_from_ref(value)

    def test_inspect_lightweight_tag_returns_tag_and_commit_metadata(self) -> None:
        commit_sha = "a" * 40
        runner = FakeGitRunner(
            tag_line=f"commit\x00{commit_sha}\x00\x00\x00\x00\x00Release subject",
            commit_sha=commit_sha,
            commit_line=(
                f"{commit_sha}\x001700000000\x00Alice\x00alice@example.com"
                "\x00Commit subject"
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_git_tag(Path(directory), "refs/tags/v2.3.4", runner=runner)
        self.assertEqual(result.tag, "v2.3.4")
        self.assertEqual(result.version, "2.3.4")
        self.assertFalse(result.annotated)
        self.assertEqual(result.object_type, "commit")
        self.assertEqual(result.object_sha, commit_sha)
        self.assertIsNone(result.tagger_name)
        self.assertIsNone(result.tagged_at)
        self.assertEqual(result.commit.sha, commit_sha)
        self.assertEqual(result.commit.committed_at, 1_700_000_000)
        self.assertEqual(result.commit.author_name, "Alice")
        self.assertEqual(result.commit.subject, "Commit subject")
        self.assertEqual([call[0] for call in runner.calls], ["for-each-ref", "rev-parse", "show"])

    def test_inspect_annotated_tag_fully_peels_nested_tag_to_commit(self) -> None:
        tag_sha = "b" * 40
        intermediate_sha = "d" * 40
        commit_sha = "c" * 40
        runner = FakeGitRunner(
            tag_line=(
                f"tag\x00{tag_sha}\x00{intermediate_sha}\x00Release Bot"
                "\x00<release@example.com>\x001700000001\x00Release v3"
            ),
            commit_sha=commit_sha,
            commit_line=(
                f"{commit_sha}\x001699999999\x00Bob\x00bob@example.com"
                "\x00Prepare release"
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_git_tag(Path(directory), "3.0.0", runner=runner)
        self.assertTrue(result.annotated)
        self.assertEqual(result.object_type, "tag")
        self.assertEqual(result.object_sha, tag_sha)
        self.assertEqual(result.tagger_name, "Release Bot")
        self.assertEqual(result.tagger_email, "release@example.com")
        self.assertEqual(result.tagged_at, 1_700_000_001)
        self.assertEqual(result.commit.sha, commit_sha)
        self.assertIn("refs/tags/3.0.0^{commit}", runner.calls[1])

    def test_git_failure_is_sanitized(self) -> None:
        runner = FakeGitRunner(
            tag_line="",
            commit_sha="a" * 40,
            commit_line="",
            fail_tag=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ReleasePrimitiveError) as caught:
                inspect_git_tag(Path(directory), "1.2.3", runner=runner)
        self.assertEqual(caught.exception.code, "git_tag_missing")
        self.assertNotIn("sensitive", str(caught.exception))

    @staticmethod
    def _release_payload(*, release_id: int = 7, title: str = "Release 1.2.3") -> dict[str, Any]:
        return {
            "id": release_id,
            "tag_name": "v1.2.3",
            "name": title,
            "html_url": "https://github.com/example/repo/releases/tag/v1.2.3",
            "upload_url": (
                f"https://uploads.github.com/repos/example/repo/releases/{release_id}/assets"
                "{?name,label}"
            ),
            "draft": False,
            "prerelease": False,
        }

    def test_create_github_release_reads_token_only_from_environment(self) -> None:
        get_url = "https://api.github.com/repos/example/repo/releases/tags/v1.2.3"
        opener = FakeOpener(
            [
                urllib.error.HTTPError(get_url, 404, "not found", None, None),
                FakeResponse(201, self._release_payload()),
            ]
        )
        result = create_or_update_github_release(
            GitHubReleaseRequest(
                repository="example/repo",
                tag="v1.2.3",
                title="Release 1.2.3",
                notes="Notes",
                target_commitish="a" * 40,
                generate_release_notes=True,
            ),
            environment={"GITHUB_TOKEN": "top-secret"},
            opener=opener,
        )
        self.assertEqual(result.action, "created")
        self.assertEqual(result.release_id, 7)
        self.assertEqual([request.get_method() for request in opener.requests], ["GET", "POST"])
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer top-secret")
        self.assertEqual(opener.requests[0].get_header("User-agent"), "StreamScapeTV-ci-workflows")
        payload = json.loads(opener.requests[1].data.decode("utf-8"))
        self.assertEqual(payload["tag_name"], "v1.2.3")
        self.assertEqual(payload["target_commitish"], "a" * 40)
        self.assertTrue(payload["generate_release_notes"])
        self.assertNotIn("top-secret", repr(result))

    def test_update_existing_github_release_uses_release_identity(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse(200, self._release_payload(release_id=23)),
                FakeResponse(200, self._release_payload(release_id=23, title="Updated")),
            ]
        )
        result = create_or_update_github_release(
            GitHubReleaseRequest(
                repository="example/repo",
                tag="v1.2.3",
                title="Updated",
                notes="Replacement notes",
                prerelease=False,
            ),
            environment={"RELEASE_TOKEN": "secret"},
            token_environment="RELEASE_TOKEN",
            opener=opener,
        )
        self.assertEqual(result.action, "updated")
        self.assertEqual(result.title, "Updated")
        self.assertEqual(opener.requests[1].get_method(), "PATCH")
        self.assertTrue(opener.requests[1].full_url.endswith("/releases/23"))
        payload = json.loads(opener.requests[1].data.decode("utf-8"))
        self.assertNotIn("generate_release_notes", payload)

    def test_existing_release_tag_mismatch_fails_before_patch(self) -> None:
        existing = self._release_payload(release_id=23)
        existing["tag_name"] = "v9.9.9"
        opener = FakeOpener([FakeResponse(200, existing)])
        with self.assertRaises(ReleasePrimitiveError) as caught:
            create_or_update_github_release(
                GitHubReleaseRequest("example/repo", "v1.2.3", "Release"),
                environment={"GITHUB_TOKEN": "secret"},
                opener=opener,
            )
        self.assertEqual(caught.exception.code, "github_release_tag_mismatch")
        self.assertEqual(len(opener.requests), 1)

    def test_github_release_requires_named_environment_token(self) -> None:
        opener = FakeOpener([])
        with self.assertRaises(ReleasePrimitiveError) as caught:
            create_or_update_github_release(
                GitHubReleaseRequest("example/repo", "1.2.3", "Release"),
                environment={},
                token_environment="GITHUB_TOKEN",
                opener=opener,
            )
        self.assertEqual(caught.exception.code, "github_token_required")
        self.assertEqual(opener.requests, [])

    def test_optional_asset_absence_is_structured_noop_without_token(self) -> None:
        release = GitHubReleaseResult(
            action="created",
            release_id=7,
            repository="example/repo",
            tag="v1.2.3",
            title="Release",
            url="https://github.com/example/repo/releases/tag/v1.2.3",
            upload_url="https://uploads.github.com/repos/example/repo/releases/7/assets",
            draft=False,
            prerelease=False,
        )
        result = upload_github_release_asset(release, None, environment={})
        self.assertEqual(result, GitHubAssetResult(False, False, "", 0, None, ""))

    def test_upload_asset_stream_boundary_returns_structured_result(self) -> None:
        release = GitHubReleaseResult(
            action="updated",
            release_id=8,
            repository="example/repo",
            tag="v1.2.3",
            title="Release",
            url="https://github.com/example/repo/releases/tag/v1.2.3",
            upload_url="https://uploads.github.com/repos/example/repo/releases/8/assets",
            draft=False,
            prerelease=False,
        )
        seen: dict[str, Any] = {}

        def uploader(url: str, headers: dict[str, str], path: Path, content_type: str) -> dict[str, Any]:
            seen.update(url=url, headers=dict(headers), path=path, content_type=content_type)
            return {
                "id": 99,
                "name": "bundle.zip",
                "size": path.stat().st_size,
                "browser_download_url": "https://github.com/example/repo/releases/download/v1.2.3/bundle.zip",
            }

        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "artifact.bin"
            asset.write_bytes(b"release-bytes")
            result = upload_github_release_asset(
                release,
                asset,
                environment={"GITHUB_TOKEN": "asset-secret"},
                asset_name="bundle.zip",
                content_type="application/zip",
                uploader=uploader,
            )
        self.assertTrue(result.present)
        self.assertTrue(result.uploaded)
        self.assertEqual(result.name, "bundle.zip")
        self.assertEqual(result.asset_id, 99)
        self.assertEqual(seen["headers"]["Authorization"], "Bearer asset-secret")
        self.assertEqual(seen["headers"]["User-Agent"], "StreamScapeTV-ci-workflows")
        self.assertEqual(seen["content_type"], "application/zip")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen["url"]).query)
        self.assertEqual(query, {"name": ["bundle.zip"]})
        self.assertNotIn("asset-secret", repr(result))

    def test_asset_path_and_response_are_fail_closed(self) -> None:
        release = GitHubReleaseResult(
            action="created",
            release_id=7,
            repository="example/repo",
            tag="v1.2.3",
            title="Release",
            url="https://github.com/example/repo/releases/tag/v1.2.3",
            upload_url="https://uploads.github.com/repos/example/repo/releases/7/assets",
            draft=False,
            prerelease=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.bin"
            target.write_bytes(b"x")
            link = root / "link.bin"
            link.symlink_to(target)
            with self.assertRaises(ReleasePrimitiveError) as symlink_error:
                upload_github_release_asset(
                    release,
                    link,
                    environment={"GITHUB_TOKEN": "secret"},
                    uploader=lambda *_args: {},
                )
            self.assertEqual(symlink_error.exception.code, "release_asset_path_invalid")

            with self.assertRaises(ReleasePrimitiveError) as response_error:
                upload_github_release_asset(
                    release,
                    target,
                    environment={"GITHUB_TOKEN": "secret"},
                    uploader=lambda *_args: {
                        "id": 1,
                        "name": "target.bin",
                        "size": 999,
                        "browser_download_url": "https://github.com/example/repo/file",
                    },
                )
            self.assertEqual(response_error.exception.code, "github_asset_size_mismatch")

    def test_untrusted_upload_host_is_rejected_before_uploader(self) -> None:
        release = GitHubReleaseResult(
            action="created",
            release_id=7,
            repository="example/repo",
            tag="v1.2.3",
            title="Release",
            url="https://github.com/example/repo/releases/tag/v1.2.3",
            upload_url="https://attacker.example/upload",
            draft=False,
            prerelease=False,
        )
        called = False

        def uploader(*_args: object) -> dict[str, Any]:
            nonlocal called
            called = True
            return {}

        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "asset.bin"
            asset.write_bytes(b"x")
            with self.assertRaises(ReleasePrimitiveError) as caught:
                upload_github_release_asset(
                    release,
                    asset,
                    environment={"GITHUB_TOKEN": "secret"},
                    uploader=uploader,
                )
        self.assertEqual(caught.exception.code, "github_release_upload_url_invalid")
        self.assertFalse(called)

    def test_module_contains_no_product_specific_release_policy(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src/ci_workflows/release_primitives.py"
        text = source.read_text(encoding="utf-8").casefold()
        for forbidden in (
            "iptv-backend",
            "streamscape-media",
            "streamscapeweb",
            "flux reconcile",
            "canary",
            "provenance",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
