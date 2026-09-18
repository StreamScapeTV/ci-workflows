from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.ci.source_snapshot_lifecycle import (
    SourceSnapshotLifecycleError,
    preflight,
    prune,
)


class FakeDrive:
    def __init__(self) -> None:
        self.folder_id = "ref_folder_12345"
        self.manifest_id = "manifest_file_12345"
        self.files = {
            self.manifest_id: {"id": self.manifest_id, "name": "manifest.json", "mimeType": "application/json"},
            "old_archive_12345": {
                "id": "old_archive_12345",
                "name": "example-main.zip",
                "mimeType": "application/zip",
                "size": "3",
            },
        }
        self.manifest = {
            "repository": "StreamScapeTV/example",
            "requested_ref": "main",
            "is_tag": False,
            "folder_id": self.folder_id,
            "archive_filename": "example-main.zip",
            "archive_file_id": "old_archive_12345",
            "source_zip_file_id": "old_archive_12345",
            "archive_size_bytes": 3,
        }
        self.deleted: list[str] = []

    def exact_folders(self, parent: str, name: str):
        if parent == "root_folder_12345" and name == "example":
            return [{"id": "repo_folder_12345", "name": name, "mimeType": "application/vnd.google-apps.folder"}]
        if parent == "repo_folder_12345" and name == "main":
            return [{"id": self.folder_id, "name": name, "mimeType": "application/vnd.google-apps.folder"}]
        return []

    def exact_files(self, parent: str, name: str):
        if parent != self.folder_id:
            return []
        return [value.copy() for value in self.files.values() if value["name"] == name]

    def media(self, file_id: str) -> bytes:
        if file_id != self.manifest_id:
            raise AssertionError(file_id)
        return (json.dumps(self.manifest, separators=(",", ":")) + "\n").encode()

    def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)
        self.files.pop(file_id, None)


class SourceSnapshotLifecycleTests(unittest.TestCase):
    def _preflight(self, drive: FakeDrive, state: Path) -> None:
        preflight(
            drive,
            root_folder_id="root_folder_12345",
            repository="StreamScapeTV/example",
            ref="main",
            is_tag=False,
            archive_filename="example-main.zip",
            repository_folder_id="repo_folder_12345",
            state_path=state,
        )

    def _write_new_manifest(self, drive: FakeDrive, manifest_path: Path, new_id: str = "new_archive_12345") -> None:
        drive.files[new_id] = {
            "id": new_id,
            "name": "example-main.zip",
            "mimeType": "application/zip",
            "size": "3",
        }
        drive.manifest.update(
            {
                "archive_file_id": new_id,
                "source_zip_file_id": new_id,
                "manifest_file_id": drive.manifest_id,
            }
        )
        manifest_path.write_text(json.dumps(drive.manifest, separators=(",", ":")) + "\n")

    def test_verified_replacement_deletes_previous_archive_and_leaves_one_new_id(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            manifest = Path(td) / "manifest.json"
            self._preflight(drive, state)
            self._write_new_manifest(drive, manifest)
            deleted = prune(
                drive,
                root_folder_id="root_folder_12345",
                repository="StreamScapeTV/example",
                ref="main",
                is_tag=False,
                archive_filename="example-main.zip",
                repository_folder_id="repo_folder_12345",
                state_path=state,
                manifest_path=manifest,
            )
            self.assertEqual(deleted, 1)
            self.assertEqual(drive.deleted, ["old_archive_12345"])
            self.assertEqual([v["id"] for v in drive.exact_files(drive.folder_id, "example-main.zip")], ["new_archive_12345"])

    def test_repeated_refresh_replaces_object_identity_without_accumulating_siblings(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for index in (1, 2):
                state = root / f"state-{index}.json"
                manifest = root / f"manifest-{index}.json"
                self._preflight(drive, state)
                new_id = f"new_archive_{index}_12345"
                self._write_new_manifest(drive, manifest, new_id)
                prune(
                    drive,
                    root_folder_id="root_folder_12345",
                    repository="StreamScapeTV/example",
                    ref="main",
                    is_tag=False,
                    archive_filename="example-main.zip",
                    repository_folder_id="repo_folder_12345",
                    state_path=state,
                    manifest_path=manifest,
                )
                current = drive.exact_files(drive.folder_id, "example-main.zip")
                self.assertEqual([value["id"] for value in current], [new_id])
                self.assertEqual(drive.manifest["source_zip_file_id"], new_id)

    def test_preflight_never_mutates_old_archive_if_new_upload_does_not_complete(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            self._preflight(drive, state)
            self.assertEqual(drive.deleted, [])
            self.assertIn("old_archive_12345", drive.files)
            recorded = json.loads(state.read_text())
            self.assertEqual(recorded["previous_archive_file_id"], "old_archive_12345")

    def test_concurrent_same_name_object_is_never_deleted_by_older_preflight(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            manifest = Path(td) / "manifest.json"
            self._preflight(drive, state)
            self._write_new_manifest(drive, manifest)
            drive.files["concurrent_12345"] = {
                "id": "concurrent_12345",
                "name": "example-main.zip",
                "mimeType": "application/zip",
                "size": "3",
            }
            with self.assertRaisesRegex(SourceSnapshotLifecycleError, "did not converge"):
                prune(
                    drive,
                    root_folder_id="root_folder_12345",
                    repository="StreamScapeTV/example",
                    ref="main",
                    is_tag=False,
                    archive_filename="example-main.zip",
                    repository_folder_id="repo_folder_12345",
                    state_path=state,
                    manifest_path=manifest,
                )
            self.assertNotIn("concurrent_12345", drive.deleted)
            self.assertIn("concurrent_12345", drive.files)

    def test_manifest_identity_mismatch_fails_before_deletion(self) -> None:
        drive = FakeDrive()
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            manifest = Path(td) / "manifest.json"
            self._preflight(drive, state)
            self._write_new_manifest(drive, manifest)
            value = json.loads(manifest.read_text())
            value["requested_ref"] = "other"
            manifest.write_text(json.dumps(value))
            with self.assertRaisesRegex(SourceSnapshotLifecycleError, "identity mismatch"):
                prune(
                    drive,
                    root_folder_id="root_folder_12345",
                    repository="StreamScapeTV/example",
                    ref="main",
                    is_tag=False,
                    archive_filename="example-main.zip",
                    repository_folder_id="repo_folder_12345",
                    state_path=state,
                    manifest_path=manifest,
                )
            self.assertEqual(drive.deleted, [])


if __name__ == "__main__":
    unittest.main()
