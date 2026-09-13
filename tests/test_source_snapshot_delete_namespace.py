from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_root = _load("source_snapshot_root_881", "scripts/ci/source_snapshot_root.py")
_delete = _load("source_snapshot_delete_881", "scripts/ci/source_snapshot_delete.py")


class FakeDriveClient:
    def __init__(self, *, missing_ref: bool = False, unexpected: bool = False, manifest_mismatch: bool = False) -> None:
        self.missing_ref = missing_ref
        self.unexpected = unexpected
        self.manifest_mismatch = manifest_mismatch
        self.folder_queries: list[tuple[str, str]] = []
        self.trashed: list[str] = []

    def exact_folders(self, parent: str, name: str) -> list[dict[str, str]]:
        self.folder_queries.append((parent, name))
        mapping = {
            ("repositories-root", "example"): [{"id": "source-repo", "name": "example", "mimeType": _delete.FOLDER_MIME}],
            ("ci-logs-root", "example"): [{"id": "diagnostic-repo", "name": "example", "mimeType": _delete.FOLDER_MIME}],
            ("diagnostic-repo", "feature/cleanup"): [{"id": "diagnostic-ref", "name": "feature/cleanup", "mimeType": _delete.FOLDER_MIME}],
        }
        if not self.missing_ref:
            mapping[("source-repo", "feature/cleanup")] = [
                {"id": "source-ref", "name": "feature/cleanup", "mimeType": _delete.FOLDER_MIME}
            ]
        return mapping.get((parent, name), [])

    def children(self, parent: str) -> list[dict[str, str]]:
        if parent == "diagnostic-ref":
            return [{"id": "diagnostic-text", "name": "34691496090-1.txt", "mimeType": "text/plain"}]
        if parent != "source-ref":
            raise AssertionError(parent)
        children = [
            {"id": "manifest", "name": "manifest.json", "mimeType": "application/json"},
            {"id": "archive", "name": "example-feature%2Fcleanup.zip", "mimeType": "application/zip"},
        ]
        if self.unexpected:
            children.append({"id": "unexpected", "name": "notes.txt", "mimeType": "text/plain"})
        return children

    def media(self, file_id: str) -> bytes:
        if file_id != "manifest":
            raise AssertionError(file_id)
        value = {
            "repository": "StreamScapeTV/other" if self.manifest_mismatch else "StreamScapeTV/example",
            "requested_ref": "feature/cleanup",
            "is_tag": False,
            "resolved_source_sha": "a" * 40,
            "archive_filename": "example-feature%2Fcleanup.zip",
        }
        return json.dumps(value).encode()

    def trash(self, file_id: str) -> None:
        self.trashed.append(file_id)


class SourceSnapshotDeleteNamespaceTests(unittest.TestCase):
    def test_root_resolution_is_deterministic_and_not_a_ref_name_search(self) -> None:
        values = [
            {
                "id": "later-repositories-root",
                "name": "repositories",
                "mimeType": _root.FOLDER_MIME,
                "createdTime": "2026-08-30T00:00:00.000Z",
                "trashed": False,
            },
            {
                "id": "repositories-root",
                "name": "repositories",
                "mimeType": _root.FOLDER_MIME,
                "createdTime": "2026-08-27T00:00:00.000Z",
                "trashed": False,
            },
            {
                "id": "ci-logs-root",
                "name": "ci-logs",
                "mimeType": _root.FOLDER_MIME,
                "createdTime": "2026-08-26T00:00:00.000Z",
                "trashed": False,
            },
        ]
        selected = _root.select_canonical_repositories_folder(values)
        self.assertEqual(selected["id"], "repositories-root")

    def test_duplicate_ref_in_diagnostics_namespace_cannot_be_selected(self) -> None:
        client = FakeDriveClient()
        result = _delete.delete_snapshot(
            client,
            root_folder_id="repositories-root",
            repository="StreamScapeTV/example",
            ref="feature/cleanup",
            expected_source_sha="a" * 40,
        )
        self.assertEqual(result, "trashed")
        self.assertEqual(client.trashed, ["source-ref"])
        self.assertNotIn(("ci-logs-root", "example"), client.folder_queries)
        self.assertNotIn(("diagnostic-repo", "feature/cleanup"), client.folder_queries)
        self.assertNotIn("diagnostic-ref", client.trashed)

    def test_absent_canonical_ref_is_idempotent_even_when_diagnostics_duplicate_exists(self) -> None:
        client = FakeDriveClient(missing_ref=True)
        result = _delete.delete_snapshot(
            client,
            root_folder_id="repositories-root",
            repository="StreamScapeTV/example",
            ref="feature/cleanup",
        )
        self.assertEqual(result, "already-absent")
        self.assertEqual(client.trashed, [])
        self.assertNotIn(("diagnostic-repo", "feature/cleanup"), client.folder_queries)

    def test_unexpected_content_in_real_canonical_folder_still_fails_closed(self) -> None:
        client = FakeDriveClient(unexpected=True)
        with self.assertRaisesRegex(_delete.SnapshotDeleteError, "unexpected non-snapshot file"):
            _delete.delete_snapshot(
                client,
                root_folder_id="repositories-root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha="a" * 40,
            )
        self.assertEqual(client.trashed, [])

    def test_manifest_repository_ref_identity_remains_bounded(self) -> None:
        client = FakeDriveClient(manifest_mismatch=True)
        with self.assertRaisesRegex(_delete.SnapshotDeleteError, "repository/ref identity mismatch"):
            _delete.delete_snapshot(
                client,
                root_folder_id="repositories-root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha="a" * 40,
            )
        self.assertEqual(client.trashed, [])

    def test_root_candidate_requires_stable_provider_identity(self) -> None:
        with self.assertRaisesRegex(_root.SnapshotRootError, "incomplete provider identity"):
            _root.select_canonical_repositories_folder(
                [{"id": "root", "name": "repositories", "mimeType": _root.FOLDER_MIME, "trashed": False}]
            )

    def test_workflow_resolves_canonical_root_before_existing_delete_helper(self) -> None:
        text = (ROOT / ".github/workflows/source-snapshot-delete.yml").read_text(encoding="utf-8")
        self.assertIn('GOOGLE_DRIVE_ROOT_FOLDER_ID="$(python3 central/scripts/ci/source_snapshot_root.py)"', text)
        self.assertIn("export GOOGLE_DRIVE_ROOT_FOLDER_ID", text)
        self.assertIn('python3 central/scripts/ci/source_snapshot_delete.py "${args[@]}"', text)
        self.assertNotIn("GOOGLE_DRIVE_ROOT_FOLDER_ID: ${{ secrets.GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID }}", text)
        self.assertIn("assert token; print(token)'\n          )\"", text)
        self.assertNotIn("assert token; print(token)')\"", text)


if __name__ == "__main__":
    unittest.main()
