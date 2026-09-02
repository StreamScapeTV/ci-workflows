from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import yaml

from scripts.ci.source_checkpoint_publish import (
    CheckpointPublishError,
    GitHubClient,
    load_canonical_checkpoint,
    materialize_checkpoint,
    resume_snapshot_action,
    stage_and_verify_tree,
    validate_request,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/source-checkpoint-publish.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, text=True, capture_output=True).stdout.strip()


def make_git_checkpoint(root: Path) -> tuple[str, str, bytes]:
    repo = root / "source"
    repo.mkdir()
    git(repo, "init", "-b", "feature/checkpoint")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    script = repo / "bin" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho checkpoint\n")
    os.chmod(script, 0o755)
    os.symlink("base.txt", repo / "base-link")
    (repo / "base.txt").write_text("changed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "checkpoint")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    archive = root / "checkpoint.zip"
    subprocess.run(["git", "-C", str(repo), "archive", "--format=zip", f"--output={archive}", "HEAD"], check=True)
    return base, tree, archive.read_bytes()


def manifest_for(base: str, tree: str, archive: bytes, *, branch: str = "feature/checkpoint") -> dict:
    digest = hashlib.sha256(archive).hexdigest()
    return {
        "repository": "StreamScapeTV/example",
        "repository_name": "example",
        "requested_ref": branch,
        "is_tag": False,
        "resolved_source_sha": base,
        "tree_sha": tree,
        "archive_format": "zip",
        "archive_format_version": 1,
        "archive_filename": "example-feature%2Fcheckpoint.zip",
        "archive_sha256": digest,
        "archive_size_bytes": len(archive),
        "source_zip_sha256": digest,
        "source_zip_size_bytes": len(archive),
        "archive_file_id": "archive_file_12345",
        "source_zip_file_id": "archive_file_12345",
        "folder_id": "ref_folder_12345",
        "manifest_file_id": "manifest_file_12345",
        "checkpoint_format_version": 1,
        "checkpoint_base_sha": base,
        "checkpoint_commit_message": "Publish local checkpoint\n\nReviewed locally.",
    }


class FakeDrive:
    def __init__(self, manifest: dict, archive: bytes):
        self.manifest = manifest
        self.archive = archive

    def exact_folders(self, parent: str, name: str):
        if parent == "root_folder_12345" and name == "example":
            return [{"id": "repo_folder_12345", "name": "example", "mimeType": "application/vnd.google-apps.folder"}]
        if parent == "repo_folder_12345" and name == "feature/checkpoint":
            return [{"id": "ref_folder_12345", "name": name, "mimeType": "application/vnd.google-apps.folder"}]
        return []

    def children(self, parent: str):
        if parent != "ref_folder_12345":
            raise AssertionError(parent)
        return [
            {"id": "manifest_file_12345", "name": "manifest.json", "mimeType": "application/json"},
            {"id": "archive_file_12345", "name": "example-feature%2Fcheckpoint.zip", "mimeType": "application/zip"},
        ]

    def media(self, file_id: str):
        if file_id == "manifest_file_12345":
            return (json.dumps(self.manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if file_id == "archive_file_12345":
            return self.archive
        raise AssertionError(file_id)


class StubGitHubClient(GitHubClient):
    def __init__(self, values):
        super().__init__("token", "StreamScapeTV/example", "https://example.invalid")
        self.values = list(values)

    def _json(self, path: str):
        if not self.values:
            raise AssertionError(path)
        return self.values.pop(0)


class SourceCheckpointPublishTests(unittest.TestCase):
    def test_request_is_branch_only_and_compact(self) -> None:
        validate_request("StreamScapeTV/example", "feature/checkpoint", "a" * 40)
        for branch in ("main", "develop"):
            with self.assertRaisesRegex(CheckpointPublishError, "integration branch"):
                validate_request("StreamScapeTV/example", branch, "a" * 40)
        with self.assertRaisesRegex(CheckpointPublishError, "branch name"):
            validate_request("StreamScapeTV/example", "refs/heads/feature", "a" * 40)
        with self.assertRaisesRegex(CheckpointPublishError, "expected head"):
            validate_request("StreamScapeTV/example", "feature", "not-a-sha")

    def test_remote_guard_refuses_default_protected_or_stale_branch(self) -> None:
        expected = "a" * 40
        client = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "feature/checkpoint"},
        ])
        with self.assertRaisesRegex(CheckpointPublishError, "default branch"):
            client.verify_branch("feature/checkpoint", expected)

        client = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": True, "commit": {"sha": expected}},
        ])
        with self.assertRaisesRegex(CheckpointPublishError, "protected"):
            client.verify_branch("feature/checkpoint", expected)

        client = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": "b" * 40}},
        ])
        with self.assertRaisesRegex(CheckpointPublishError, "stale"):
            client.verify_branch("feature/checkpoint", expected)

    def test_canonical_drive_checkpoint_is_discovered_without_agent_file_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base, tree, archive = make_git_checkpoint(Path(td))
            value = manifest_for(base, tree, archive)
            checkpoint = load_canonical_checkpoint(
                FakeDrive(value, archive),
                root_folder_id="root_folder_12345",
                repository="StreamScapeTV/example",
                branch="feature/checkpoint",
                expected_head=base,
            )
            self.assertEqual(checkpoint.tree_sha, tree)
            self.assertEqual(checkpoint.commit_message, value["checkpoint_commit_message"])
            self.assertEqual(checkpoint.archive_bytes, archive)

    def test_resume_preserves_unpublished_checkpoint_and_refreshes_published_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base, tree, archive = make_git_checkpoint(root)
            value = manifest_for(base, tree, archive)
            client = FakeDrive(value, archive)
            base_tree = git(root / "source", "rev-parse", f"{base}^{{tree}}")
            self.assertEqual(
                resume_snapshot_action(
                    client, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                    branch="feature/checkpoint", is_tag=False, source_head=base, source_tree=base_tree,
                ),
                "preserve",
            )
            self.assertEqual(
                resume_snapshot_action(
                    client, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                    branch="feature/checkpoint", is_tag=False, source_head="b" * 40, source_tree=tree,
                ),
                "refresh",
            )
            with self.assertRaisesRegex(CheckpointPublishError, "refusing to overwrite"):
                resume_snapshot_action(
                    client, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                    branch="feature/checkpoint", is_tag=False, source_head="b" * 40, source_tree="c" * 40,
                )

    def test_resume_refreshes_normal_snapshot_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base, tree, archive = make_git_checkpoint(root)
            value = manifest_for(base, tree, archive)
            for key in ("checkpoint_format_version", "checkpoint_base_sha", "checkpoint_commit_message"):
                value.pop(key)
            self.assertEqual(
                resume_snapshot_action(
                    FakeDrive(value, archive), root_folder_id="root_folder_12345",
                    repository="StreamScapeTV/example", branch="feature/checkpoint", is_tag=False,
                    source_head=base, source_tree=tree,
                ),
                "refresh",
            )
            self.assertEqual(
                resume_snapshot_action(
                    FakeDrive(manifest_for(base, tree, archive), archive), root_folder_id="root_folder_12345",
                    repository="StreamScapeTV/example", branch="feature/checkpoint", is_tag=True,
                    source_head=base, source_tree=tree,
                ),
                "refresh",
            )

    def test_checkpoint_manifest_base_digest_and_drive_identity_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base, tree, archive = make_git_checkpoint(Path(td))
            cases = [
                ("checkpoint_base_sha", "b" * 40, "base"),
                ("archive_sha256", "0" * 64, "aliases"),
                ("archive_file_id", "other_file_12345", "Drive identity"),
            ]
            for key, replacement, message in cases:
                value = manifest_for(base, tree, archive)
                value[key] = replacement
                with self.subTest(key=key), self.assertRaisesRegex(CheckpointPublishError, message):
                    load_canonical_checkpoint(
                        FakeDrive(value, archive),
                        root_folder_id="root_folder_12345",
                        repository="StreamScapeTV/example",
                        branch="feature/checkpoint",
                        expected_head=base,
                    )

    def test_materialization_preserves_executable_symlink_and_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base, tree, archive = make_git_checkpoint(root)
            worktree = root / "target"
            source = root / "source"
            subprocess.run(["git", "clone", "--quiet", str(source), str(worktree)], check=True)
            git(worktree, "checkout", "--detach", base)
            archive_path = root / "input.zip"
            archive_path.write_bytes(archive)
            materialize_checkpoint(archive_path, worktree)
            self.assertTrue((worktree / "base-link").is_symlink())
            self.assertEqual(os.readlink(worktree / "base-link"), "base.txt")
            self.assertTrue((worktree / "bin" / "run.sh").stat().st_mode & 0o111)
            self.assertEqual(stage_and_verify_tree(worktree, expected_head=base, expected_tree=tree), tree)

    def test_materialization_rejects_path_traversal_before_worktree_clear(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "target"
            repo.mkdir(); git(repo, "init")
            keep = repo / "keep.txt"; keep.write_text("keep")
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape", b"bad")
            with self.assertRaisesRegex(CheckpointPublishError, "unsafe"):
                materialize_checkpoint(archive, repo)
            self.assertEqual(keep.read_text(), "keep")

    def test_workflow_and_dispatch_use_canonical_drive_checkpoint(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(inventory["workflows"]["source_checkpoint_publish"], ".github/workflows/source-checkpoint-publish.yml")
        self.assertEqual(inventory["scripts"]["source_checkpoint_publish"], "scripts/ci/source_checkpoint_publish.py")

        workflow = yaml.safe_load(WORKFLOW.read_text())
        call = workflow["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {"repository", "branch", "expected_head", "ci_run_id"})
        self.assertNotIn("workflow_dispatch", workflow["on"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        steps = {step.get("name"): step for step in workflow["jobs"]["publish"]["steps"] if step.get("name")}
        token = steps["Create exact target repository token"]
        self.assertEqual(token["with"]["permission-contents"], "write")
        self.assertEqual(token["with"]["permission-workflows"], "write")
        self.assertEqual(token["with"]["permission-metadata"], "read")
        self.assertIn("GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID", workflow["on"]["workflow_call"]["secrets"])
        checkout = steps["Check out exact target branch head"]
        self.assertEqual(checkout["with"]["ref"], "refs/heads/${{ inputs.branch }}")
        self.assertIn("source_checkpoint_publish.py download", steps["Download and verify canonical Drive checkpoint"]["run"])
        self.assertNotIn("drive_bundle_file_id", WORKFLOW.read_text())
        self.assertNotIn("bundle_sha256", WORKFLOW.read_text())
        self.assertIn("git -C target -c", steps["Publish non-force fast-forward branch update"]["run"])
        self.assertIn("push --porcelain", steps["Publish non-force fast-forward branch update"]["run"])
        self.assertNotIn("--force", steps["Publish non-force fast-forward branch update"]["run"])
        self.assertIn("ls-remote", steps["Publish non-force fast-forward branch update"]["run"])
        self.assertEqual(steps["Record published source SHA"]["with"]["observed_source_sha"], "${{ steps.commit.outputs.commit_sha }}")
        self.assertIn("job.status == 'success'", steps["Finish Agent State run"]["with"]["status"])
        summary = steps["Record checkpoint publication result"]
        self.assertIn("TARGET_BRANCH", summary["env"])
        self.assertNotIn("${{ inputs.branch }}", summary["run"])

        dispatch = yaml.safe_load(DISPATCH.read_text())
        request_steps = {step.get("name"): step for step in dispatch["jobs"]["request"]["steps"] if step.get("name")}
        admission = request_steps["Validate canonical source checkpoint publication request"]
        self.assertIn("source.checkpoint-publish supports only the publish profile", admission["run"])
        self.assertIn('set(inputs) != {"expected_head"}', admission["run"])
        job = dispatch["jobs"]["source_checkpoint_publish"]
        self.assertEqual(job["uses"], "./.github/workflows/source-checkpoint-publish.yml")
        self.assertEqual(set(job["with"]), {"repository", "branch", "expected_head", "ci_run_id"})
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        self.assertIn("source_checkpoint_publish", dispatch["jobs"]["settle_cancelled"]["needs"])
        self.assertIn("tests.test_source_checkpoint_publish", (ROOT / ".github/workflows/self-check.yml").read_text())


if __name__ == "__main__":
    unittest.main()
