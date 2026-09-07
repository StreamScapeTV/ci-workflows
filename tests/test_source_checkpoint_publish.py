from __future__ import annotations

import hashlib
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
    checkpoint_filename,
    load_latest_checkpoint,
    materialize_checkpoint,
    select_latest_checkpoint_file,
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
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "base.txt").write_text("base\n")
    (repo / "ignored.txt").write_text("tracked despite ignore\n")
    git(repo, "add", ".gitignore", "base.txt")
    git(repo, "add", "-f", "ignored.txt")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")

    script = repo / "bin" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho checkpoint\n")
    os.chmod(script, 0o755)
    os.symlink("base.txt", repo / "base-link")
    (repo / "base.txt").write_text("changed\n")
    (repo / "ignored.txt").write_text("changed while ignored\n")
    git(repo, "add", "-f", "-A", "--", ".")
    git(repo, "commit", "-m", "checkpoint")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    archive = root / "checkpoint.zip"
    subprocess.run(["git", "-C", str(repo), "archive", "--format=zip", f"--output={archive}", "HEAD"], check=True)
    return base, tree, archive.read_bytes()


class FakeDrive:
    def __init__(self, archive: bytes, *, children=None):
        self.archive = archive
        self._children = children

    def exact_folders(self, parent: str, name: str):
        if parent == "root_folder_12345" and name == "example":
            return [{"id": "repo_folder_12345", "name": "example", "mimeType": "application/vnd.google-apps.folder"}]
        if parent == "repo_folder_12345" and name == "feature/checkpoint":
            return [{"id": "ref_folder_12345", "name": name, "mimeType": "application/vnd.google-apps.folder"}]
        return []

    def children(self, parent: str):
        if parent != "ref_folder_12345":
            raise AssertionError(parent)
        if self._children is not None:
            return self._children
        return [
            {"id": "manifest_file_12345", "name": "manifest.json", "mimeType": "application/json"},
            {"id": "base_archive_12345", "name": "example-feature%2Fcheckpoint.zip", "mimeType": "application/zip"},
            {"id": "checkpoint_1_12345", "name": "example-feature%2Fcheckpoint-checkpoint-000001.zip", "mimeType": "application/zip", "size": str(len(self.archive))},
            {"id": "checkpoint_2_12345", "name": "example-feature%2Fcheckpoint-checkpoint-000002.zip", "mimeType": "application/zip", "size": str(len(self.archive))},
            {"id": "unrelated_12345", "name": "notes.txt", "mimeType": "text/plain"},
        ]

    def media(self, file_id: str):
        if file_id.startswith("checkpoint_"):
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
    def test_request_is_bounded_full_zip_identity(self) -> None:
        validate_request(
            "StreamScapeTV/example", "feature/checkpoint", "a" * 40, "b" * 40,
            "c" * 64, 1234, "Publish checkpoint",
        )
        for branch in ("main", "develop"):
            with self.assertRaisesRegex(CheckpointPublishError, "integration branch"):
                validate_request("StreamScapeTV/example", branch, "a"*40, "b"*40, "c"*64, 1, "msg")
        with self.assertRaisesRegex(CheckpointPublishError, "expected tree"):
            validate_request("StreamScapeTV/example", "feature", "a"*40, "bad", "c"*64, 1, "msg")
        with self.assertRaisesRegex(CheckpointPublishError, "SHA-256"):
            validate_request("StreamScapeTV/example", "feature", "a"*40, "b"*40, "bad", 1, "msg")
        with self.assertRaisesRegex(CheckpointPublishError, "archive size"):
            validate_request("StreamScapeTV/example", "feature", "a"*40, "b"*40, "c"*64, 0, "msg")
        with self.assertRaisesRegex(CheckpointPublishError, "commit message"):
            validate_request("StreamScapeTV/example", "feature", "a"*40, "b"*40, "c"*64, 1, "")

    def test_latest_numbered_checkpoint_is_selected_automatically(self) -> None:
        children = [
            {"id": "a"*10, "name": "manifest.json", "mimeType": "application/json"},
            {"id": "b"*10, "name": "example-feature%2Fcheckpoint.zip", "mimeType": "application/zip"},
            {"id": "c"*10, "name": "example-feature%2Fcheckpoint-checkpoint-000001.zip", "mimeType": "application/zip"},
            {"id": "d"*10, "name": "example-feature%2Fcheckpoint-checkpoint-000003.zip", "mimeType": "application/zip"},
            {"id": "e"*10, "name": "example-feature%2Fcheckpoint-checkpoint-000002.zip", "mimeType": "application/zip"},
            {"id": "f"*10, "name": "other-checkpoint-999999.zip", "mimeType": "application/zip"},
        ]
        sequence, item = select_latest_checkpoint_file(children, repository="StreamScapeTV/example", branch="feature/checkpoint")
        self.assertEqual(sequence, 3)
        self.assertEqual(item["id"], "d" * 10)
        self.assertEqual(checkpoint_filename("StreamScapeTV/example", "agent1/xyz", 1), "example-agent1%2Fxyz-checkpoint-000001.zip")

    def test_malformed_or_duplicate_matching_sequence_fails_closed(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        with self.assertRaisesRegex(CheckpointPublishError, "malformed"):
            select_latest_checkpoint_file(
                [{"id": "a"*10, "name": prefix + "1.zip", "mimeType": "application/zip"}],
                repository="StreamScapeTV/example", branch="feature/checkpoint",
            )
        with self.assertRaisesRegex(CheckpointPublishError, "duplicate"):
            select_latest_checkpoint_file(
                [
                    {"id": "a"*10, "name": prefix + "000001.zip", "mimeType": "application/zip"},
                    {"id": "b"*10, "name": prefix + "000001.zip", "mimeType": "application/zip"},
                ],
                repository="StreamScapeTV/example", branch="feature/checkpoint",
            )
        with self.assertRaisesRegex(CheckpointPublishError, "positive"):
            select_latest_checkpoint_file(
                [{"id": "a"*10, "name": prefix + "000000.zip", "mimeType": "application/zip"}],
                repository="StreamScapeTV/example", branch="feature/checkpoint",
            )

    def test_drive_download_verifies_latest_hash_and_size_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, _, archive = make_git_checkpoint(Path(td))
            digest = hashlib.sha256(archive).hexdigest()
            latest = load_latest_checkpoint(
                FakeDrive(archive), root_folder_id="root_folder_12345",
                repository="StreamScapeTV/example", branch="feature/checkpoint",
                expected_sha256=digest, expected_size_bytes=len(archive),
            )
            self.assertEqual(latest.sequence, 2)
            self.assertEqual(latest.archive_bytes, archive)
            with self.assertRaisesRegex(CheckpointPublishError, "size"):
                load_latest_checkpoint(
                    FakeDrive(archive), root_folder_id="root_folder_12345",
                    repository="StreamScapeTV/example", branch="feature/checkpoint",
                    expected_sha256=digest, expected_size_bytes=len(archive)+1,
                )
            with self.assertRaisesRegex(CheckpointPublishError, "SHA-256"):
                load_latest_checkpoint(
                    FakeDrive(archive), root_folder_id="root_folder_12345",
                    repository="StreamScapeTV/example", branch="feature/checkpoint",
                    expected_sha256="0"*64, expected_size_bytes=len(archive),
                )

    def test_remote_classification_publish_and_idempotent_repeat(self) -> None:
        expected_head = "a" * 40
        expected_tree = "b" * 40
        message = "Publish checkpoint"
        publish = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": expected_head}},
        ])
        state = publish.classify_branch(branch="feature/checkpoint", expected_head=expected_head, expected_tree=expected_tree, commit_message=message)
        self.assertEqual((state.action, state.observed_head), ("publish", expected_head))

        published_head = "c" * 40
        repeat = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": published_head}},
            {
                "sha": published_head,
                "parents": [{"sha": expected_head}],
                "commit": {"tree": {"sha": expected_tree}, "message": message},
            },
        ])
        state = repeat.classify_branch(branch="feature/checkpoint", expected_head=expected_head, expected_tree=expected_tree, commit_message=message)
        self.assertEqual((state.action, state.observed_head), ("already-published", published_head))

        stale = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": published_head}},
            {
                "sha": published_head,
                "parents": [{"sha": "d"*40}],
                "commit": {"tree": {"sha": expected_tree}, "message": message},
            },
        ])
        with self.assertRaisesRegex(CheckpointPublishError, "stale"):
            stale.classify_branch(branch="feature/checkpoint", expected_head=expected_head, expected_tree=expected_tree, commit_message=message)

    def test_remote_guard_refuses_default_and_protected_branch(self) -> None:
        kwargs = dict(expected_head="a"*40, expected_tree="b"*40, commit_message="msg")
        default = StubGitHubClient([{"full_name": "StreamScapeTV/example", "default_branch": "feature/checkpoint"}])
        with self.assertRaisesRegex(CheckpointPublishError, "default branch"):
            default.classify_branch(branch="feature/checkpoint", **kwargs)
        protected = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": True, "commit": {"sha": "a"*40}},
        ])
        with self.assertRaisesRegex(CheckpointPublishError, "protected"):
            protected.classify_branch(branch="feature/checkpoint", **kwargs)

    def test_materialization_preserves_executable_symlink_tracked_ignored_and_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base, tree, archive = make_git_checkpoint(root)
            worktree = root / "target"
            subprocess.run(["git", "clone", "--quiet", str(root / "source"), str(worktree)], check=True)
            git(worktree, "checkout", "--detach", base)
            archive_path = root / "input.zip"
            archive_path.write_bytes(archive)
            materialize_checkpoint(archive_path, worktree)
            self.assertTrue((worktree / "base-link").is_symlink())
            self.assertTrue((worktree / "bin" / "run.sh").stat().st_mode & 0o111)
            self.assertEqual((worktree / "ignored.txt").read_text(), "changed while ignored\n")
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

    def test_workflow_dispatch_and_snapshot_use_numbered_full_zip_contract_only(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(inventory["workflows"]["source_checkpoint_publish"], ".github/workflows/source-checkpoint-publish.yml")
        self.assertEqual(inventory["scripts"]["source_checkpoint_publish"], "scripts/ci/source_checkpoint_publish.py")

        workflow = yaml.safe_load(WORKFLOW.read_text())
        call = workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "branch", "expected_head", "expected_tree", "archive_sha256", "archive_size_bytes", "commit_message", "ci_run_id"},
        )
        self.assertNotIn("workflow_dispatch", workflow["on"])
        text = WORKFLOW.read_text()
        self.assertIn("download-latest", text)
        self.assertIn("Select and download unique latest numbered Drive checkpoint", text)
        self.assertNotIn("checkpoint_sequence: {type", text)
        self.assertNotIn("source-checkpoint-manifest", text)
        self.assertNotIn("patch", text.lower())

        dispatch = yaml.safe_load(DISPATCH.read_text())
        request_steps = {step.get("name"): step for step in dispatch["jobs"]["request"]["steps"] if step.get("name")}
        admission = request_steps["Validate canonical source checkpoint publication request"]
        self.assertIn('{"expected_head", "expected_tree", "archive_sha256", "archive_size_bytes", "commit_message"}', admission["run"])
        job = dispatch["jobs"]["source_checkpoint_publish"]
        self.assertEqual(
            set(job["with"]),
            {"repository", "branch", "expected_head", "expected_tree", "archive_sha256", "archive_size_bytes", "commit_message", "ci_run_id"},
        )
        snapshot_names = [step.get("name") for step in dispatch["jobs"]["source_snapshot"]["steps"]]
        self.assertNotIn("Protect unpublished canonical Drive checkpoint", snapshot_names)
        self.assertNotIn("checkpoint_resume", DISPATCH.read_text())
        self.assertNotIn("resume-action", DISPATCH.read_text())
        combined=(WORKFLOW.read_text() + DISPATCH.read_text() + (ROOT / "scripts/ci/source_checkpoint_publish.py").read_text()).lower()
        for retired in (".patch", "patch-transport", "patch chain", "apply patch"):
            self.assertNotIn(retired, combined)


if __name__ == "__main__":
    unittest.main()
