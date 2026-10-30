from __future__ import annotations

import hashlib
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
    checkpoint_commit_message,
    checkpoint_filename,
    cleanup_consumed_checkpoints,
    consumed_checkpoint_files,
    load_latest_checkpoint,
    materialize_checkpoint,
    resolve_cleanup_boundary,
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
        self.deleted = []

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
            return [value for value in self._children if value["id"] not in self.deleted]
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

    def delete(self, file_id: str):
        self.deleted.append(file_id)


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

    def test_consumed_checkpoint_cleanup_deletes_through_selected_and_preserves_newer(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        children = [
            {"id": "a"*10, "name": prefix + "000001.zip", "mimeType": "application/zip"},
            {"id": "b"*10, "name": prefix + "000001.zip", "mimeType": "application/zip"},
            {"id": "c"*10, "name": prefix + "000002.zip", "mimeType": "application/zip"},
            {"id": "d"*10, "name": prefix + "000003.zip", "mimeType": "application/zip"},
            {"id": "e"*10, "name": "manifest.json", "mimeType": "application/json"},
        ]
        drive = FakeDrive(b"unused", children=children)
        deleted = cleanup_consumed_checkpoints(
            drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
            branch="feature/checkpoint", through_sequence=2,
        )
        self.assertEqual(deleted, 3)
        self.assertEqual(drive.deleted, ["a"*10, "b"*10, "c"*10])
        self.assertEqual(
            [value["id"] for value in drive.children("ref_folder_12345") if value["name"].startswith(prefix)],
            ["d"*10],
        )
        self.assertEqual(
            cleanup_consumed_checkpoints(
                drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                branch="feature/checkpoint", through_sequence=2,
            ),
            0,
        )

    def test_consumed_checkpoint_cleanup_fails_closed_on_malformed_matching_name(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        children = [
            {"id": "a"*10, "name": prefix + "000001.zip", "mimeType": "application/zip"},
            {"id": "b"*10, "name": prefix + "2.zip", "mimeType": "application/zip"},
        ]
        drive = FakeDrive(b"unused", children=children)
        with self.assertRaisesRegex(CheckpointPublishError, "malformed"):
            cleanup_consumed_checkpoints(
                drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                branch="feature/checkpoint", through_sequence=1,
            )
        self.assertEqual(drive.deleted, [])

    def test_cleanup_selection_accepts_duplicate_consumed_sequences_but_not_newer(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        values = consumed_checkpoint_files(
            [
                {"id": "a"*10, "name": prefix + "000002.zip", "mimeType": "application/zip"},
                {"id": "b"*10, "name": prefix + "000002.zip", "mimeType": "application/zip"},
                {"id": "c"*10, "name": prefix + "000003.zip", "mimeType": "application/zip"},
            ],
            repository="StreamScapeTV/example", branch="feature/checkpoint", through_sequence=2,
        )
        self.assertEqual([item[1]["id"] for item in values], ["a"*10, "b"*10])

    def test_remote_classification_publish_and_idempotent_repeat(self) -> None:
        expected_head = "a" * 40
        expected_tree = "b" * 40
        archive_sha256 = "c" * 64
        archive_size_bytes = 1234
        message = "Publish checkpoint"
        kwargs = dict(
            branch="feature/checkpoint",
            expected_head=expected_head,
            expected_tree=expected_tree,
            archive_sha256=archive_sha256,
            archive_size_bytes=archive_size_bytes,
            commit_message=message,
        )
        publish = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": expected_head}},
        ])
        state = publish.classify_branch(**kwargs)
        self.assertEqual((state.action, state.observed_head, state.checkpoint_sequence), ("publish", expected_head, None))

        published_head = "d" * 40
        legacy_repeat = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": published_head}},
            {
                "sha": published_head,
                "parents": [{"sha": expected_head}],
                "commit": {"tree": {"sha": expected_tree}, "message": message},
            },
        ])
        state = legacy_repeat.classify_branch(**kwargs)
        self.assertEqual((state.action, state.observed_head, state.checkpoint_sequence), ("already-published", published_head, None))

        metadata_message = checkpoint_commit_message(
            message, sequence=2, archive_sha256=archive_sha256, archive_size_bytes=archive_size_bytes
        )
        repeat = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": published_head}},
            {
                "sha": published_head,
                "parents": [{"sha": expected_head}],
                "commit": {"tree": {"sha": expected_tree}, "message": metadata_message},
            },
        ])
        state = repeat.classify_branch(**kwargs)
        self.assertEqual((state.action, state.observed_head, state.checkpoint_sequence), ("already-published", published_head, 2))

        wrong_archive = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": published_head}},
            {
                "sha": published_head,
                "parents": [{"sha": expected_head}],
                "commit": {"tree": {"sha": expected_tree}, "message": metadata_message},
            },
        ])
        with self.assertRaisesRegex(CheckpointPublishError, "archive identity"):
            wrong_archive.classify_branch(**{**kwargs, "archive_sha256": "e" * 64})

        stale = StubGitHubClient([
            {"full_name": "StreamScapeTV/example", "default_branch": "main"},
            {"name": "feature/checkpoint", "protected": False, "commit": {"sha": published_head}},
            {
                "sha": published_head,
                "parents": [{"sha": "f"*40}],
                "commit": {"tree": {"sha": expected_tree}, "message": message},
            },
        ])
        with self.assertRaisesRegex(CheckpointPublishError, "stale"):
            stale.classify_branch(**kwargs)

    def test_remote_guard_refuses_default_and_protected_branch(self) -> None:
        kwargs = dict(
            expected_head="a"*40, expected_tree="b"*40, archive_sha256="c"*64,
            archive_size_bytes=1, commit_message="msg",
        )
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

    def test_workflow_keeps_numbered_checkpoint_contract_and_legacy_transport(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(inventory["workflows"]["source_checkpoint_publish"], ".github/workflows/source-checkpoint-publish.yml")
        self.assertEqual(inventory["scripts"]["source_checkpoint_publish"], "scripts/ci/source_checkpoint_publish.py")

        workflow = yaml.safe_load(WORKFLOW.read_text())
        call = workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "branch", "expected_head", "expected_tree", "operation", "archive_sha256", "archive_size_bytes", "commit_message", "ci_run_id"},
        )
        self.assertNotIn("workflow_dispatch", workflow["on"])
        text = WORKFLOW.read_text()
        self.assertIn("download-latest", text)
        self.assertIn("Select and download unique latest numbered Drive checkpoint", text)
        self.assertNotIn("checkpoint_sequence: {type: number, required: true}", text)
        self.assertNotIn("source-checkpoint-manifest", text)
        self.assertNotIn("patch", text.lower())

        dispatch = yaml.safe_load(DISPATCH.read_text())
        request_steps = {step.get("name"): step for step in dispatch["jobs"]["request"]["steps"] if step.get("name")}
        admission = request_steps["Validate canonical source checkpoint publication request"]
        self.assertIn('required = {"expected_head", "expected_tree"}', admission["run"])
        self.assertIn('required |= {"archive_sha256", "archive_size_bytes", "commit_message"}', admission["run"])
        self.assertNotIn('checkpoint_sequence', admission["run"])
        job = dispatch["jobs"]["source_checkpoint_publish"]
        self.assertEqual(
            set(job["with"]),
            {"repository", "branch", "expected_head", "expected_tree", "operation", "archive_sha256", "archive_size_bytes", "commit_message", "ci_run_id"},
        )
        snapshot_names = [step.get("name") for step in dispatch["jobs"]["source_snapshot"]["steps"]]
        self.assertNotIn("Protect unpublished canonical Drive checkpoint", snapshot_names)
        self.assertNotIn("checkpoint_resume", DISPATCH.read_text())
        self.assertNotIn("resume-action", DISPATCH.read_text())
        combined=(WORKFLOW.read_text() + DISPATCH.read_text() + (ROOT / "scripts/ci/source_checkpoint_publish.py").read_text()).lower()
        for retired in ("patch-transport", "patch chain", "resume-action"):
            self.assertNotIn(retired, combined)


    def test_already_published_replay_resolves_boundary_from_archive_identity_without_request_sequence(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        target = b"published-checkpoint-bytes"
        newer = b"newer-checkpoint-bytes"
        children = [
            {"id": "checkpoint_2_12345", "name": prefix + "000002.zip", "mimeType": "application/zip", "size": str(len(target))},
            {"id": "checkpoint_3_12345", "name": prefix + "000003.zip", "mimeType": "application/zip", "size": str(len(newer))},
        ]
        drive = FakeDrive(target, children=children)
        original_media = drive.media
        drive.media = lambda file_id: target if file_id == "checkpoint_2_12345" else newer
        boundary = resolve_cleanup_boundary(
            drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
            branch="feature/checkpoint", expected_sha256=hashlib.sha256(target).hexdigest(),
            expected_size_bytes=len(target),
        )
        self.assertFalse(boundary.already_consumed)
        self.assertEqual(boundary.sequence, 2)
        self.assertEqual(boundary.filename, prefix + "000002.zip")

    def test_legacy_replay_without_boundary_fails_closed_while_numbered_checkpoints_remain(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        published = b"published-checkpoint-bytes"
        newer = b"newer-checkpoint-bytes"
        children = [
            {"id": "checkpoint_3_12345", "name": prefix + "000003.zip", "mimeType": "application/zip", "size": str(len(newer))},
        ]
        drive = FakeDrive(newer, children=children)
        with self.assertRaisesRegex(CheckpointPublishError, "cannot be proven"):
            resolve_cleanup_boundary(
                drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                branch="feature/checkpoint", expected_sha256=hashlib.sha256(published).hexdigest(),
                expected_size_bytes=len(published),
            )
        self.assertEqual(drive.deleted, [])
        self.assertEqual([item["id"] for item in drive.children("ref_folder_12345")], ["checkpoint_3_12345"])

    def test_metadata_replay_cleans_older_after_boundary_deleted_and_preserves_newer(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        children = [
            {"id": "checkpoint_1_12345", "name": prefix + "000001.zip", "mimeType": "application/zip"},
            {"id": "checkpoint_3_12345", "name": prefix + "000003.zip", "mimeType": "application/zip"},
        ]
        drive = FakeDrive(b"unused", children=children)
        with self.assertRaisesRegex(CheckpointPublishError, "boundary is missing"):
            cleanup_consumed_checkpoints(
                drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                branch="feature/checkpoint", through_sequence=2,
            )
        deleted = cleanup_consumed_checkpoints(
            drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
            branch="feature/checkpoint", through_sequence=2, allow_missing_boundary=True,
        )
        self.assertEqual(deleted, 1)
        self.assertEqual(drive.deleted, ["checkpoint_1_12345"])
        self.assertEqual([item["id"] for item in drive.children("ref_folder_12345")], ["checkpoint_3_12345"])

    def test_partial_cleanup_replay_resolves_remaining_boundary_then_preserves_newer(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        target = b"published-checkpoint-bytes"
        newer = b"newer-checkpoint-bytes"
        children = [
            {"id": "checkpoint_1_12345", "name": prefix + "000001.zip", "mimeType": "application/zip", "size": "5"},
            {"id": "checkpoint_2_12345", "name": prefix + "000002.zip", "mimeType": "application/zip", "size": str(len(target))},
            {"id": "checkpoint_3_12345", "name": prefix + "000003.zip", "mimeType": "application/zip", "size": str(len(newer))},
        ]
        drive = FakeDrive(target, children=children)
        drive.media = lambda file_id: target if file_id == "checkpoint_2_12345" else newer
        boundary = resolve_cleanup_boundary(
            drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
            branch="feature/checkpoint", expected_sha256=hashlib.sha256(target).hexdigest(),
            expected_size_bytes=len(target),
        )
        self.assertEqual(boundary.sequence, 2)
        deleted = cleanup_consumed_checkpoints(
            drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
            branch="feature/checkpoint", through_sequence=boundary.sequence,
        )
        self.assertEqual(deleted, 2)
        self.assertEqual(drive.deleted, ["checkpoint_1_12345", "checkpoint_2_12345"])
        self.assertEqual([item["id"] for item in drive.children("ref_folder_12345")], ["checkpoint_3_12345"])

    def test_cleanup_keeps_boundary_until_older_checkpoints_are_proven_gone(self) -> None:
        prefix = "example-feature%2Fcheckpoint-checkpoint-"
        children = [
            {"id": "checkpoint_1_12345", "name": prefix + "000001.zip", "mimeType": "application/zip"},
            {"id": "checkpoint_2_12345", "name": prefix + "000002.zip", "mimeType": "application/zip"},
        ]
        class StickyOlderDrive(FakeDrive):
            def delete(self, file_id: str):
                if file_id == "checkpoint_1_12345":
                    self.deleted.append("attempted:" + file_id)
                    return
                super().delete(file_id)
            def children(self, parent: str):
                values = super().children(parent)
                # An attempted-but-not-deleted older object remains visible.
                if "attempted:checkpoint_1_12345" in self.deleted:
                    return [item for item in self._children if item["id"] != "checkpoint_2_12345" or item["id"] not in self.deleted]
                return values
        drive = StickyOlderDrive(b"unused", children=children)
        with self.assertRaisesRegex(CheckpointPublishError, "older consumed checkpoints remain"):
            cleanup_consumed_checkpoints(
                drive, root_folder_id="root_folder_12345", repository="StreamScapeTV/example",
                branch="feature/checkpoint", through_sequence=2,
            )
        self.assertNotIn("checkpoint_2_12345", drive.deleted)

    def test_replay_workflow_keeps_five_field_agent_state_contract_and_skips_deleted_boundary(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        download = by_name["Select and download unique latest numbered Drive checkpoint"]
        self.assertEqual(download["if"], "${{ steps.remote.outputs.action == 'publish' }}")
        self.assertNotIn("CHECKPOINT_SEQUENCE", download["env"])
        self.assertNotIn("--checkpoint-sequence", download["run"])
        remote = by_name["Classify exact target branch"]
        self.assertIn('--archive-sha256 "${ARCHIVE_SHA256}"', remote["run"])
        self.assertIn('--archive-size-bytes "${ARCHIVE_SIZE_BYTES}"', remote["run"])
        commit = by_name["Create one normal Git commit from checkpoint"]
        self.assertIn("render-message", commit["run"])
        self.assertEqual(commit["env"]["CHECKPOINT_SEQUENCE"], "${{ steps.checkpoint.outputs.checkpoint_sequence }}")
        replay = by_name["Resolve checkpoint cleanup boundary for published replay"]
        self.assertEqual(
            replay["if"],
            "${{ steps.remote.outputs.action == 'already-published' && steps.remote.outputs.checkpoint_sequence == '' }}",
        )
        self.assertIn("resolve-cleanup", replay["run"])
        cleanup = by_name["Delete consumed numbered Drive checkpoints"]
        self.assertIn("steps.remote.outputs.checkpoint_sequence != ''", cleanup["if"])
        self.assertEqual(
            cleanup["env"]["CHECKPOINT_SEQUENCE"],
            "${{ steps.checkpoint.outputs.checkpoint_sequence || steps.remote.outputs.checkpoint_sequence || steps.replay_checkpoint.outputs.checkpoint_sequence }}",
        )
        self.assertEqual(
            cleanup["env"]["ALLOW_MISSING_BOUNDARY"],
            "${{ steps.remote.outputs.action == 'already-published' && steps.remote.outputs.checkpoint_sequence != '' && 'true' || 'false' }}",
        )
        self.assertIn("--allow-missing-boundary", cleanup["run"])
        summary = by_name["Record checkpoint publication result"]
        self.assertEqual(
            summary["env"]["CHECKPOINT_FILENAME"],
            "${{ steps.checkpoint.outputs.checkpoint_filename || steps.remote.outputs.checkpoint_filename || steps.replay_checkpoint.outputs.checkpoint_filename }}",
        )

    def test_workflow_deletes_consumed_checkpoints_only_after_exact_github_readback(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        names = [step.get("name") for step in steps]
        verify_index = names.index("Verify exact GitHub head and remote tree readback")
        cleanup_index = names.index("Delete consumed numbered Drive checkpoints")
        self.assertLess(verify_index, cleanup_index)
        cleanup = steps[cleanup_index]
        self.assertIn("if", cleanup)
        self.assertIn("cleanup-consumed", cleanup["run"])
        self.assertEqual(
            cleanup["env"]["CHECKPOINT_SEQUENCE"],
            "${{ steps.checkpoint.outputs.checkpoint_sequence || steps.remote.outputs.checkpoint_sequence || steps.replay_checkpoint.outputs.checkpoint_sequence }}",
        )
        self.assertNotIn("always()", str(cleanup))

class DeltaCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, _, _ = make_git_checkpoint(self.root)
        self.source = self.root / "source"
        # Multiple unpublished commits, binaries, rename/delete, modes and symlinks.
        git(self.source, "mv", "base.txt", "renamed.txt")
        (self.source / "binary.dat").write_bytes(bytes(range(256)) * 32)
        (self.source / "base-link").unlink()
        git(self.source, "add", "-A")
        git(self.source, "commit", "-m", "second unpublished contribution")
        self.tree = git(self.source, "rev-parse", "HEAD^{tree}")
        self.data = subprocess.run(
            ["git", "-C", str(self.source), "diff", "--binary", "--full-index", "--no-renames",
             "--no-ext-diff", "--no-textconv", self.base, "HEAD", "--"],
            capture_output=True, check=True).stdout
        self.manifest = {"format": "streamscape.patch.v1", "repository": "StreamScapeTV/example",
                         "branch": "feature/checkpoint", "base_commit": self.base,
                         "base_tree": git(self.source, "rev-parse", self.base + "^{tree}"),
                         "candidate_tree": self.tree, "sandbox_commit": git(self.source, "rev-parse", "HEAD"),
                         "patch_sha256": hashlib.sha256(self.data).hexdigest(), "message": "Publish delta"}
        self.target = self.root / "target"
        subprocess.run(["git", "clone", "--quiet", str(self.source), str(self.target)], check=True)
        git(self.target, "reset", "--hard", self.base)
        self.archive = self.root / "delta.zip"

    def write(self):
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(self.manifest))
            z.writestr("change.patch", self.data)

    def apply(self, **kwargs):
        self.write()
        values = dict(repository="StreamScapeTV/example", branch="feature/checkpoint",
                      expected_head=self.base, expected_tree=self.tree)
        values.update(kwargs)
        materialize_checkpoint(self.archive, self.target, **values)

    def unchanged(self):
        self.assertEqual(git(self.target, "status", "--porcelain"), "")
        self.assertEqual(git(self.target, "rev-parse", "HEAD"), self.base)

    def test_all_committed_changes_reconstruct_exact_tree(self):
        self.apply()
        self.assertEqual(stage_and_verify_tree(self.target, expected_head=self.base,
                                              expected_tree=self.tree), self.tree)
        self.assertEqual((self.target / "binary.dat").read_bytes(), bytes(range(256)) * 32)
        self.assertFalse((self.target / "base.txt").exists())
        self.assertEqual((self.target / "bin/run.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual(git(self.target, "rev-parse", "HEAD"), self.base)  # publisher owns commit

    def test_wrong_identity_rejected_before_mutation(self):
        for key, wrong in (("repository", "StreamScapeTV/example-other"), ("branch", "other"),
                           ("base_commit", "a" * 40), ("candidate_tree", "b" * 40)):
            with self.subTest(key=key):
                previous = self.manifest[key]; self.manifest[key] = wrong
                with self.assertRaises(CheckpointPublishError): self.apply()
                self.unchanged(); self.manifest[key] = previous

    def test_tree_proven_in_temporary_index_before_mutation(self):
        self.manifest["candidate_tree"] = "b" * 40
        with self.assertRaisesRegex(CheckpointPublishError, "candidate tree mismatch"):
            self.apply(expected_tree="b" * 40)
        self.unchanged()

    def test_corrupt_patch_and_base_tree_rejected(self):
        for key, bad in (("patch_sha256", "0" * 64), ("base_tree", "a" * 40),
                         ("format", "streamscape.patch.v2")):
            with self.subTest(key=key):
                prior = self.manifest[key]; self.manifest[key] = bad
                with self.assertRaises(CheckpointPublishError): self.apply()
                self.unchanged(); self.manifest[key] = prior

    def test_dirty_destination_is_preserved(self):
        (self.target / "local.txt").write_text("do not discard")
        with self.assertRaisesRegex(CheckpointPublishError, "clean"):
            self.apply()
        self.assertEqual((self.target / "local.txt").read_text(), "do not discard")

    def test_protected_branch_and_stale_parent_rejected(self):
        self.manifest["branch"] = "main"
        with self.assertRaisesRegex(CheckpointPublishError, "protected"):
            self.apply(branch="main")
        self.manifest["branch"] = "feature/checkpoint"
        self.manifest["base_commit"] = "a" * 40
        with self.assertRaisesRegex(CheckpointPublishError, "stale"):
            self.apply(expected_head="a" * 40)
        self.unchanged()

    def test_unsafe_patch_path_rejected_without_filesystem_write(self):
        self.data = b"diff --git a/../escape b/../escape\nnew file mode 100644\n--- /dev/null\n+++ b/../escape\n@@ -0,0 +1 @@\n+bad\n"
        self.manifest["patch_sha256"] = hashlib.sha256(self.data).hexdigest()
        with self.assertRaises(CheckpointPublishError): self.apply()
        self.assertFalse((self.root / "escape").exists()); self.unchanged()

    def test_same_two_names_remain_valid_legacy_full_tree(self):
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("manifest.json", '{"format":"ordinary-product-data"}')
            z.writestr("change.patch", "ordinary tracked file")
        materialize_checkpoint(self.archive, self.target)
        self.assertEqual((self.target / "change.patch").read_text(), "ordinary tracked file")

    def refresh_patch(self):
        git(self.source, "add", "-f", "-A")
        git(self.source, "commit", "-m", "next contribution")
        self.tree = git(self.source, "rev-parse", "HEAD^{tree}")
        self.data = subprocess.run(
            ["git", "-C", str(self.source), "diff", "--binary", "--full-index", "--no-renames",
             "--no-ext-diff", "--no-textconv", self.base, "HEAD", "--"],
            capture_output=True, check=True).stdout
        self.manifest.update(candidate_tree=self.tree, patch_sha256=hashlib.sha256(self.data).hexdigest())

    def test_delta_retains_full_tree_path_limits(self):
        (self.source / "not\\canonical.txt").write_text("invalid checkpoint path")
        self.refresh_patch()
        with self.assertRaisesRegex(CheckpointPublishError, "path is invalid"):
            self.apply()
        self.unchanged()

    def test_delta_retains_full_tree_size_limits(self):
        from unittest.mock import patch
        self.write()
        with zipfile.ZipFile(self.archive) as bundle:
            limit = sum(info.file_size for info in bundle.infolist()) + 1
        self.assertLess(limit, 8192)
        with patch("scripts.ci.source_checkpoint_publish.MAX_UNCOMPRESSED_BYTES", limit):
            with self.assertRaisesRegex(CheckpointPublishError, "checkpoint limits"):
                self.apply()
        self.unchanged()

    def test_submodule_is_not_a_checkpoint_file(self):
        git(self.source, "update-index", "--add", "--cacheinfo", "160000", self.base, "external-module")
        git(self.source, "commit", "-m", "submodule")
        self.tree = git(self.source, "rev-parse", "HEAD^{tree}")
        self.data = subprocess.run(
            ["git", "-C", str(self.source), "diff", "--binary", "--full-index", "--no-renames",
             "--no-ext-diff", "--no-textconv", self.base, "HEAD", "--"], capture_output=True, check=True).stdout
        self.manifest.update(candidate_tree=self.tree, patch_sha256=hashlib.sha256(self.data).hexdigest())
        with self.assertRaisesRegex(CheckpointPublishError, "unsupported file type"):
            self.apply()
        self.unchanged()


if __name__ == "__main__":
    unittest.main()
