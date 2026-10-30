from __future__ import annotations

import json
from pathlib import Path
import tempfile
import subprocess
import sys

import yaml
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
    def _historical(self, name="6fc9561-complete-source.zip"):
        drive = FakeDrive()
        drive.files["old_archive_12345"]["name"] = name
        drive.manifest["archive_filename"] = name
        return drive

    def test_historical_and_legacy_manifest_archives_refresh_to_canonical_pair(self):
        for name in ("6fc9561-complete-source.zip", "iptv-android-issue-1812-1096cad.zip", "source.zip"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                drive = self._historical(name)
                drive.files["unrelated_12345"] = dict(drive.files["old_archive_12345"], id="unrelated_12345", name="checkpoint-000001.zip")
                drive.manifest["archive_sha256"] = "a" * 64
                drive.files["old_archive_12345"]["sha256Checksum"] = "a" * 64
                state, manifest = Path(td) / "state.json", Path(td) / "manifest.json"
                self._preflight(drive, state)
                self.assertEqual(drive.deleted, [])
                self.assertEqual(json.loads(state.read_text())["prior_archive_file_ids"], ["old_archive_12345"])
                drive.manifest["archive_filename"] = "example-main.zip"
                self._write_new_manifest(drive, manifest)
                self.assertEqual(self._prune(drive, state, manifest), 1)
                self.assertEqual(drive.deleted, ["old_archive_12345"])
                self.assertIn(drive.manifest_id, drive.files)
                self.assertIn("unrelated_12345", drive.files)
                self._preflight(drive, state)

    def test_historical_collision_preserves_sibling_bytes_and_repeated_refresh(self):
        for sibling_name in ("example-main.zip", "source.zip"):
            with self.subTest(sibling_name=sibling_name), tempfile.TemporaryDirectory() as td:
                drive = self._historical()
                sibling = {"id": "preserved_12345", "name": sibling_name,
                           "mimeType": "application/zip", "size": "17", "bytes": b"retained evidence"}
                drive.files[sibling["id"]] = sibling.copy()
                drive.manifest["historical_source_preserved"] = {"file_id": sibling["id"], "head": "a" * 40}
                state, manifest = Path(td) / "state.json", Path(td) / "manifest.json"
                for index in (1, 2):
                    old_id = drive.manifest["archive_file_id"]
                    self._preflight(drive, state)
                    recorded = json.loads(state.read_text())
                    self.assertEqual(recorded["replacement_archive_filename"], "6fc9561-complete-source.zip")
                    self.assertEqual(recorded["prior_archive_file_ids"], [old_id])
                    # A preflight/unfinished upload never modifies either predecessor.
                    self.assertIn(old_id, drive.files)
                    self.assertEqual(drive.files[sibling["id"]], sibling)
                    self._write_new_manifest(drive, manifest, f"new_archive_{index}_12345")
                    self.assertEqual(self._prune(drive, state, manifest), 1)
                    self.assertEqual(drive.files[sibling["id"]], sibling)
                    self.assertNotIn(sibling["id"], drive.deleted)
                    selected = drive.exact_files(drive.folder_id, "6fc9561-complete-source.zip")
                    self.assertEqual([value["id"] for value in selected], [drive.manifest["archive_file_id"]])

    def test_both_callers_publish_preflight_selected_manifest_filename(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("actions/source-snapshot/action.yml", ".github/workflows/central-ci-dispatch.yml"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as td:
                config = yaml.safe_load((root / filename).read_text())
                steps = config["runs"]["steps"] if "runs" in config else config["jobs"]["source_snapshot"]["steps"]
                by_name = {step.get("name"): step for step in steps}
                script = by_name["Validate existing canonical source archive state"]["run"]
                selected = script.split("<<'PY_SELECTED'\n", 1)[1].split("\nPY_SELECTED", 1)[0]
                state, manifest, output = (Path(td) / name for name in ("state", "manifest", "output"))
                state.write_text(json.dumps({"replacement_archive_filename": "6fc9561-complete-source.zip"}))
                manifest.write_text(json.dumps({"archive_filename": "example-main.zip", "repository": "StreamScapeTV/example"}))
                subprocess.run([sys.executable, "-c", selected, str(state), str(manifest), str(output)], check=True)
                self.assertEqual(json.loads(manifest.read_text()), {
                    "archive_filename": "6fc9561-complete-source.zip", "repository": "StreamScapeTV/example"})
                self.assertEqual(output.read_text(), "archive_filename=6fc9561-complete-source.zip\n")
                self.assertEqual(by_name["Upload repository snapshot archive"]["with"]["file_name"],
                                 "${{ steps.source_lifecycle.outputs.archive_filename }}")

    def test_historical_preflight_rejects_missing_ambiguous_or_wrong_archive_identity(self):
        for variant in ("missing", "duplicate", "identity", "size", "hash", "manifest-id", "conflicting-ids", "multiple-manifests", "invalid-name"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as td:
                drive = self._historical()
                old = drive.files["old_archive_12345"]
                if variant == "missing":
                    del drive.files["old_archive_12345"]
                elif variant == "duplicate":
                    drive.files["duplicate_12345"] = dict(old, id="duplicate_12345")
                elif variant == "identity":
                    old["id"] = "different_12345"
                elif variant == "size":
                    old["size"] = "4"
                elif variant == "hash":
                    drive.manifest["archive_sha256"] = "a" * 64
                    old["sha256Checksum"] = "b" * 64
                elif variant == "manifest-id":
                    drive.manifest["manifest_file_id"] = "different_12345"
                elif variant == "multiple-manifests":
                    drive.files["manifest_other_12345"] = dict(drive.files[drive.manifest_id], id="manifest_other_12345")
                elif variant == "invalid-name":
                    drive.manifest["archive_filename"] = ["bad"]
                else:
                    drive.manifest["archive_file_id"] = "different_12345"
                with self.assertRaises(SourceSnapshotLifecycleError):
                    self._preflight(drive, Path(td) / "state.json")
                self.assertEqual(drive.deleted, [])

    def test_historical_manifest_identity_mismatches_fail_before_mutation(self):
        for key, value in (("repository", "StreamScapeTV/other"), ("requested_ref", "other"),
                           ("is_tag", True), ("folder_id", "different_12345")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as td:
                drive = self._historical()
                drive.manifest[key] = value
                with self.assertRaisesRegex(SourceSnapshotLifecycleError, "identity mismatch"):
                    self._preflight(drive, Path(td) / "state.json")
                self.assertEqual(drive.deleted, [])

    def test_historical_archive_and_unrelated_zip_survive_unfinished_upload(self):
        drive = self._historical()
        drive.files["unrelated_12345"] = dict(drive.files["old_archive_12345"], id="unrelated_12345", name="checkpoint-000001.zip")
        with tempfile.TemporaryDirectory() as td:
            self._preflight(drive, Path(td) / "state.json")
            self.assertEqual(drive.deleted, [])
            self.assertIn("old_archive_12345", drive.files)
            self.assertIn("unrelated_12345", drive.files)

    def test_historical_prune_requires_exact_remote_manifest_bytes(self):
        drive = self._historical()
        with tempfile.TemporaryDirectory() as td:
            state, manifest = Path(td) / "state.json", Path(td) / "manifest.json"
            self._preflight(drive, state)
            drive.manifest["archive_filename"] = "example-main.zip"
            self._write_new_manifest(drive, manifest)
            manifest.write_bytes(manifest.read_bytes() + b" ")
            with self.assertRaisesRegex(SourceSnapshotLifecycleError, "raw Drive readback mismatch"):
                self._prune(drive, state, manifest)
            self.assertEqual(drive.deleted, [])
            self.assertIn("old_archive_12345", drive.files)

    def _prune(self, drive: FakeDrive, state: Path, manifest: Path) -> int:
        return prune(
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

    def test_retry_after_interrupted_manifest_upload_prunes_orphans_only_after_publication(self) -> None:
        drive = FakeDrive()
        del drive.files[drive.manifest_id]
        with tempfile.TemporaryDirectory() as td:
            state, manifest = Path(td) / "state.json", Path(td) / "manifest.json"
            self._preflight(drive, state)
            self._write_new_manifest(drive, manifest, "interrupted_archive_12345")
            # Manifest upload did not commit (as in the recorded HTTP 410).
            self.assertEqual(drive.deleted, [])
            self._preflight(drive, state)
            self.assertEqual(drive.deleted, [])
            self.assertEqual(set(json.loads(state.read_text())["prior_archive_file_ids"]),
                             {"old_archive_12345", "interrupted_archive_12345"})
            self._write_new_manifest(drive, manifest)
            drive.files[drive.manifest_id] = {
                "id": drive.manifest_id, "name": "manifest.json", "mimeType": "application/json",
            }
            self.assertEqual(self._prune(drive, state, manifest), 2)
            self.assertEqual([f["id"] for f in drive.exact_files(drive.folder_id, "example-main.zip")],
                             ["new_archive_12345"])
            self._preflight(drive, state)  # subsequent requests see a coherent pair

    def test_manifest_without_archive_is_repaired_without_deleting_manifest(self) -> None:
        drive = FakeDrive()
        del drive.files["old_archive_12345"]
        with tempfile.TemporaryDirectory() as td:
            state, manifest = Path(td) / "state.json", Path(td) / "manifest.json"
            self._preflight(drive, state)
            self.assertEqual(drive.deleted, [])
            self._write_new_manifest(drive, manifest)
            self.assertEqual(self._prune(drive, state, manifest), 0)
            self.assertIn(drive.manifest_id, drive.files)
            self._preflight(drive, state)

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
            "name": drive.manifest["archive_filename"],
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
