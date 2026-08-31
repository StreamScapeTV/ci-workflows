from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.ci.source_bundle_publish import (
    BundlePublishError,
    GitHubClient,
    SourceBundle,
    git_blob_digest,
    load_bundle,
    publish_bundle,
    validate_request,
)


class FakeGitHub:
    def __init__(self, *, protected: bool = False, move_before_update: bool = False) -> None:
        self.repository = "StreamScapeTV/example"
        self.branch = "feature/exact-bundle"
        self.expected = "a" * 40
        self.head = self.expected
        self.protected = protected
        self.move_before_update = move_before_update
        self.blob_bytes: list[bytes] = []
        self.updated_to: str | None = None
        self.tree_files = None

    def repository_metadata(self):
        return {"full_name": self.repository, "default_branch": "main", "private": True}

    def branch_metadata(self, branch: str):
        return {"name": branch, "protected": self.protected, "commit": {"sha": self.head}}

    def commit_metadata(self, sha: str):
        self.assert_expected(sha)
        return {"sha": sha, "tree": {"sha": "d" * 40}}

    def create_blob(self, data: bytes) -> str:
        self.blob_bytes.append(data)
        return git_blob_digest(data, "sha1")

    def create_tree(self, base_tree: str, files):
        if base_tree != "d" * 40:
            raise AssertionError(base_tree)
        self.tree_files = files
        return "b" * 40

    def create_commit(self, tree_sha: str, parent_sha: str) -> str:
        if tree_sha != "b" * 40 or parent_sha != self.expected:
            raise AssertionError((tree_sha, parent_sha))
        if self.move_before_update:
            self.head = "e" * 40
        return "c" * 40

    def update_branch(self, branch: str, sha: str) -> None:
        if branch != self.branch:
            raise AssertionError(branch)
        self.updated_to = sha
        self.head = sha

    def assert_expected(self, sha: str) -> None:
        if sha != self.expected:
            raise AssertionError(sha)


def _write_bundle(
    path: Path,
    files: list[tuple[str, str, bytes, str]],
    *,
    extra_member: tuple[str, bytes] | None = None,
    symlink_member: str | None = None,
) -> str:
    manifest_files = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for repo_path, mode, data, algorithm in files:
            key = "blob_sha1" if algorithm == "sha1" else "blob_sha256"
            manifest_files.append(
                {"path": repo_path, "mode": mode, key: git_blob_digest(data, algorithm)}
            )
            archive.writestr(f"payload/{repo_path}", data)
        archive.writestr(
            "manifest.json",
            json.dumps({"version": 1, "files": manifest_files}, separators=(",", ":")),
        )
        if extra_member is not None:
            archive.writestr(extra_member[0], extra_member[1])
        if symlink_member is not None:
            info = zipfile.ZipInfo(symlink_member)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"target")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StubGitHubClient(GitHubClient):
    def __init__(self, responses):
        super().__init__("token", "StreamScapeTV/example", api_root="https://example.invalid")
        self.responses = list(responses)
        self.requests = []

    def _request(self, path, *, method="GET", body=None, expected=(200,)):
        self.requests.append((path, method, body, tuple(expected)))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class SourceBundlePublishTests(unittest.TestCase):
    def test_github_commit_and_ref_api_are_exact_and_non_force(self) -> None:
        parent = "a" * 40
        tree = "b" * 40
        commit = "c" * 40
        client = StubGitHubClient(
            [
                {"sha": commit, "tree": {"sha": tree}, "parents": [{"sha": parent}]},
                {"ref": "refs/heads/feature/exact-bundle", "object": {"sha": commit}},
            ]
        )
        self.assertEqual(client.create_commit(tree, parent), commit)
        client.update_branch("feature/exact-bundle", commit)
        _, method, body, expected = client.requests[1]
        self.assertEqual(method, "PATCH")
        self.assertEqual(body, {"sha": commit, "force": False})
        self.assertEqual(expected, (200,))

        client = StubGitHubClient(
            [{"sha": commit, "tree": {"sha": tree}, "parents": [{"sha": "d" * 40}]}]
        )
        with self.assertRaisesRegex(BundlePublishError, "unexpected parent"):
            client.create_commit(tree, parent)

    def test_good_bundle_preserves_binary_bytes_and_accepts_sha1_or_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            binary = b"\x00\xffreviewed\nbytes\x00"
            digest = _write_bundle(
                path,
                [
                    ("assets/reviewed.bin", "100644", binary, "sha256"),
                    ("scripts/run.sh", "100755", b"#!/bin/sh\necho ok\n", "sha1"),
                ],
            )
            bundle = load_bundle(path, digest)
            self.assertEqual([value.path for value in bundle.files], ["assets/reviewed.bin", "scripts/run.sh"])
            self.assertEqual(bundle.files[0].data, binary)
            self.assertEqual(bundle.files[0].declared_algorithm, "sha256")
            self.assertEqual(bundle.files[1].declared_algorithm, "sha1")

            client = FakeGitHub()
            result = publish_bundle(
                client,
                repository=client.repository,
                branch=client.branch,
                expected_head=client.expected,
                bundle=bundle,
            )
            self.assertEqual(result, {"commit_sha": "c" * 40, "tree_sha": "b" * 40, "files": 2})
            self.assertEqual(client.blob_bytes[0], binary)
            self.assertEqual(client.updated_to, "c" * 40)

    def test_bundle_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            digest = _write_bundle(path, [("../escape", "100644", b"x", "sha1")])
            with self.assertRaisesRegex(BundlePublishError, "path is unsafe"):
                load_bundle(path, digest)

    def test_bundle_rejects_dot_git_and_file_directory_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dotgit.zip"
            digest = _write_bundle(path, [(".git/config", "100644", b"x", "sha1")])
            with self.assertRaisesRegex(BundlePublishError, "path is unsafe"):
                load_bundle(path, digest)

            path = Path(directory) / "collision.zip"
            digest = _write_bundle(
                path,
                [("config", "100644", b"file", "sha1"), ("config/item", "100644", b"child", "sha1")],
            )
            with self.assertRaisesRegex(BundlePublishError, "file/directory collision"):
                load_bundle(path, digest)

    def test_bundle_rejects_control_characters_in_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.zip"
            digest = _write_bundle(path, [("bad\nname", "100644", b"x", "sha1")])
            with self.assertRaisesRegex(BundlePublishError, "path is invalid"):
                load_bundle(path, digest)

    def test_bundle_rejects_symlink_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            digest = _write_bundle(
                path,
                [("safe.txt", "100644", b"safe", "sha1")],
                symlink_member="undeclared-link",
            )
            with self.assertRaisesRegex(BundlePublishError, "regular files only"):
                load_bundle(path, digest)

    def test_bundle_rejects_undeclared_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            digest = _write_bundle(
                path,
                [("safe.txt", "100644", b"safe", "sha1")],
                extra_member=("payload/extra.txt", b"extra"),
            )
            with self.assertRaisesRegex(BundlePublishError, "members not declared"):
                load_bundle(path, digest)

    def test_bundle_rejects_wrong_reviewed_bundle_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            _write_bundle(path, [("safe.txt", "100644", b"safe", "sha1")])
            with self.assertRaisesRegex(BundlePublishError, "ZIP SHA-256"):
                load_bundle(path, "0" * 64)

    def test_bundle_rejects_symlink_mode_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            digest = _write_bundle(path, [("link", "120000", b"target", "sha1")])
            with self.assertRaisesRegex(BundlePublishError, "100644 or 100755"):
                load_bundle(path, digest)

    def test_request_refuses_integration_names_and_invalid_file_id(self) -> None:
        with self.assertRaisesRegex(BundlePublishError, "integration branch"):
            validate_request("StreamScapeTV/example", "main", "a" * 40, "1abcdefghij", "b" * 64)
        with self.assertRaisesRegex(BundlePublishError, "Drive file ID"):
            validate_request("StreamScapeTV/example", "feature", "a" * 40, "short", "b" * 64)

    def test_publish_refuses_protected_branch(self) -> None:
        client = FakeGitHub(protected=True)
        with self.assertRaisesRegex(BundlePublishError, "protected branch"):
            publish_bundle(
                client,
                repository=client.repository,
                branch=client.branch,
                expected_head=client.expected,
                bundle=SourceBundle(tuple()),
            )
        self.assertIsNone(client.updated_to)

    def test_publish_refuses_head_move_before_ref_update(self) -> None:
        client = FakeGitHub(move_before_update=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            digest = _write_bundle(path, [("safe.txt", "100644", b"safe", "sha1")])
            bundle = load_bundle(path, digest)
            with self.assertRaisesRegex(BundlePublishError, "expected head is stale"):
                publish_bundle(
                    client,
                    repository=client.repository,
                    branch=client.branch,
                    expected_head=client.expected,
                    bundle=bundle,
                )
        self.assertIsNone(client.updated_to)


if __name__ == "__main__":
    unittest.main()
