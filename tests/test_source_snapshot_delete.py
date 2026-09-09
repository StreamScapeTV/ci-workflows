from __future__ import annotations

import http.server
import importlib.util
import json
from pathlib import Path
import sys
import threading
import urllib.parse
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/source_snapshot_delete.py"
_spec = importlib.util.spec_from_file_location("source_snapshot_delete", SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


class SourceSnapshotDeleteTests(unittest.TestCase):
    def test_workflow_is_bounded_and_main_develop_are_refused(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/source-snapshot-delete.yml").read_text())
        call = workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "ref", "expected_source_sha", "allow_checkpoint_only_without_manifest"},
        )
        self.assertIs(call["inputs"]["allow_checkpoint_only_without_manifest"]["default"], False)
        self.assertNotIn("workflow_dispatch", workflow["on"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(
            set(call["secrets"]),
            {
                "GOOGLE_DRIVE_CLIENT_ID",
                "GOOGLE_DRIVE_CLIENT_SECRET",
                "GOOGLE_DRIVE_REFRESH_TOKEN",
                "GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID",
            },
        )
        self.assertNotIn("repository_folder_id", call["inputs"])
        for ref in ("main", "develop"):
            with self.assertRaises(_mod.SnapshotDeleteError):
                _mod.validate_request("StreamScapeTV/example", ref, "a" * 40)
        _mod.validate_request("StreamScapeTV/example", "feature/cleanup", "a" * 40)

    def test_duplicate_media_reads_have_a_bounded_large_file_timeout(self) -> None:
        self.assertEqual(_mod.REQUEST_TIMEOUT_SECONDS, 20)
        self.assertEqual(_mod.DUPLICATE_MEDIA_TIMEOUT_SECONDS, 120)

    def test_branch_delete_always_routes_same_ref_snapshot_cleanup(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/branch-delete.yml").read_text())
        delete = workflow["jobs"]["delete"]
        cleanup = workflow["jobs"]["snapshot_cleanup"]
        finish = workflow["jobs"]["finish"]
        self.assertEqual(delete["outputs"]["branch_was_present"], "${{ steps.delete_branch.outputs.branch_was_present }}")
        self.assertEqual(cleanup["uses"], "./.github/workflows/source-snapshot-delete.yml")
        self.assertEqual(delete["outputs"]["repository"], "${{ steps.request.outputs.repository }}")
        self.assertEqual(delete["outputs"]["branch"], "${{ steps.request.outputs.branch }}")
        self.assertEqual(delete["outputs"]["deletion_source"], "${{ steps.request.outputs.deletion_source }}")
        self.assertEqual(cleanup["with"]["repository"], "${{ needs.delete.outputs.repository }}")
        self.assertEqual(cleanup["with"]["ref"], "${{ needs.delete.outputs.branch }}")
        self.assertEqual(cleanup["with"]["expected_source_sha"], "")
        self.assertIs(cleanup["with"]["allow_checkpoint_only_without_manifest"], True)
        self.assertEqual(cleanup["secrets"], "inherit")
        self.assertIn("needs.snapshot_cleanup.result == 'success'", finish["steps"][0]["with"]["status"])
        self.assertIn("github.event_name != 'delete'", finish["if"])
        self.assertIn("inputs.deletion_source != 'github-delete-event'", finish["if"])
        delete_script = next(step for step in delete["steps"] if step.get("name") == "Delete exact eligible branch")["run"]
        self.assertIn('output.write("branch_was_present=false\\n")', delete_script)
        self.assertIn('output.write("branch_was_present=true\\n")', delete_script)


    def test_checkpoint_only_retirement_is_explicit_and_fail_closed(self) -> None:
        class FakeDriveClient:
            def __init__(self, children: list[dict[str, str]], media: dict[str, bytes] | None = None) -> None:
                self._children = children
                self._media = media or {}
                self.trashed: list[str] = []

            def exact_folders(self, parent: str, name: str) -> list[dict[str, str]]:
                if parent == "root" and name == "example":
                    return [{"id": "repo-folder", "name": "example", "mimeType": _mod.FOLDER_MIME}]
                if parent == "repo-folder" and name == "feature/cleanup":
                    return [{"id": "ref-folder", "name": "feature/cleanup", "mimeType": _mod.FOLDER_MIME}]
                return []

            def children(self, parent: str) -> list[dict[str, str]]:
                self.assert_ref(parent)
                return list(self._children)

            def media(self, file_id: str) -> bytes:
                return self._media[file_id]

            def trash(self, file_id: str) -> None:
                self.assert_ref(file_id)
                self.trashed.append(file_id)

            @staticmethod
            def assert_ref(value: str) -> None:
                if value != "ref-folder":
                    raise AssertionError(value)

        checkpoints = [
            {
                "id": "cp-1",
                "name": "example-feature%2Fcleanup-checkpoint-000001.zip",
                "mimeType": "application/zip",
            },
            {
                "id": "cp-2",
                "name": "example-feature%2Fcleanup-checkpoint-000002.zip",
                "mimeType": "application/zip",
            },
        ]

        client = FakeDriveClient(checkpoints)
        with self.assertRaisesRegex(_mod.SnapshotDeleteError, "exactly one manifest"):
            _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
            )
        self.assertEqual(client.trashed, [])

        result = _mod.delete_snapshot(
            client,
            root_folder_id="root",
            repository="StreamScapeTV/example",
            ref="feature/cleanup",
            allow_checkpoint_only_without_manifest=True,
        )
        self.assertEqual(result, "trashed-checkpoint-only")
        self.assertEqual(client.trashed, ["ref-folder"])

        client = FakeDriveClient(checkpoints)
        with self.assertRaisesRegex(_mod.SnapshotDeleteError, "cannot verify an expected source SHA"):
            _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha="a" * 40,
                allow_checkpoint_only_without_manifest=True,
            )
        self.assertEqual(client.trashed, [])

        bad_mime = [dict(checkpoints[0], mimeType="application/octet-stream")]
        client = FakeDriveClient(bad_mime)
        with self.assertRaisesRegex(_mod.SnapshotDeleteError, "ZIP checkpoint files only"):
            _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                allow_checkpoint_only_without_manifest=True,
            )
        self.assertEqual(client.trashed, [])

        unexpected = [{"id": "notes", "name": "notes.txt", "mimeType": "application/zip"}]
        client = FakeDriveClient(unexpected)
        with self.assertRaisesRegex(_mod.SnapshotDeleteError, "unexpected non-snapshot file"):
            _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                allow_checkpoint_only_without_manifest=True,
            )
        self.assertEqual(client.trashed, [])

        baseline_without_manifest = [
            {"id": "archive", "name": "example-feature%2Fcleanup.zip", "mimeType": "application/zip"}
        ]
        client = FakeDriveClient(baseline_without_manifest)
        with self.assertRaisesRegex(_mod.SnapshotDeleteError, "unexpected non-snapshot file"):
            _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                allow_checkpoint_only_without_manifest=True,
            )
        self.assertEqual(client.trashed, [])

        duplicate = [checkpoints[0], dict(checkpoints[0], id="cp-1-copy")]
        client = FakeDriveClient(duplicate, {"cp-1": b"same", "cp-1-copy": b"same"})
        result = _mod.delete_snapshot(
            client,
            root_folder_id="root",
            repository="StreamScapeTV/example",
            ref="feature/cleanup",
            allow_checkpoint_only_without_manifest=True,
        )
        self.assertEqual(result, "trashed-checkpoint-only")

        client = FakeDriveClient(duplicate, {"cp-1": b"left", "cp-1-copy": b"right"})
        with self.assertRaisesRegex(_mod.SnapshotDeleteError, "conflicting duplicate checkpoint sequence"):
            _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                allow_checkpoint_only_without_manifest=True,
            )
        self.assertEqual(client.trashed, [])

    def test_exact_manifest_identity_is_required_and_cleanup_is_idempotent(self) -> None:
        expected = "a" * 40
        records: list[tuple[str, str]] = []
        state = {
            "missing_ref": False,
            "bad_sha": False,
            "manifest_only": False,
            "numbered": False,
            "duplicate_numbered": False,
            "conflicting_duplicate": False,
            "unexpected": False,
        }

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _json(self, status: int, value: object) -> None:
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                records.append(("GET", parsed.path))
                media = urllib.parse.parse_qs(parsed.query).get("alt") == ["media"]
                if media and parsed.path in {"/files/cp-1", "/files/cp-1-duplicate"}:
                    payload = b"checkpoint-one"
                    if parsed.path.endswith("duplicate") and state["conflicting_duplicate"]:
                        payload = b"checkpoint-conflict"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if parsed.path == "/files/manifest-id" and media:
                    manifest = {
                        "repository": "StreamScapeTV/example",
                        "requested_ref": "feature/cleanup",
                        "is_tag": False,
                        "resolved_source_sha": "b" * 40 if state["bad_sha"] else expected,
                        "folder_id": "copied-from-old-folder",
                        "manifest_file_id": "copied-from-old-manifest",
                        "archive_file_id": "copied-from-old-archive",
                        "source_zip_file_id": "copied-from-old-archive",
                        "archive_filename": "example-feature%2Fcleanup.zip",
                    }
                    self._json(200, manifest)
                    return
                if parsed.path != "/files":
                    self._json(404, {"message": "Not Found"})
                    return
                q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
                if "name = 'example'" in q:
                    self._json(200, {"files": [{"id": "repo-folder", "name": "example", "mimeType": _mod.FOLDER_MIME}]})
                elif "name = 'feature/cleanup'" in q:
                    files = [] if state["missing_ref"] else [{"id": "ref-folder", "name": "feature/cleanup", "mimeType": _mod.FOLDER_MIME}]
                    self._json(200, {"files": files})
                elif "'ref-folder' in parents" in q:
                    children = [
                        {"id": "manifest-id", "name": "manifest.json", "mimeType": "application/json"},
                    ]
                    if not state["manifest_only"]:
                        children.append({"id": "archive-id", "name": "example-feature%2Fcleanup.zip", "mimeType": "application/zip"})
                    if state["numbered"]:
                        children.extend([
                            {"id": "cp-1", "name": "example-feature%2Fcleanup-checkpoint-000001.zip", "mimeType": "application/zip"},
                            {"id": "cp-2", "name": "example-feature%2Fcleanup-checkpoint-000002.zip", "mimeType": "application/zip"},
                        ])
                    if state["duplicate_numbered"]:
                        children.append(
                            {"id": "cp-1-duplicate", "name": "example-feature%2Fcleanup-checkpoint-000001.zip", "mimeType": "application/zip"}
                        )
                    if state["unexpected"]:
                        children.append({"id": "other", "name": "notes.txt", "mimeType": "text/plain"})
                    self._json(200, {"files": children})
                else:
                    self._json(200, {"files": []})

            def do_PATCH(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                records.append(("PATCH", parsed.path))
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if parsed.path == "/files/ref-folder" and body == {"trashed": True}:
                    self._json(200, {"id": "ref-folder", "trashed": True})
                else:
                    self._json(400, {"message": "Bad Request"})

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = _mod.DriveClient("masked", api_root=f"http://127.0.0.1:{server.server_port}")
            result = _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha=expected,
            )
            self.assertEqual(result, "trashed")
            self.assertIn(("PATCH", "/files/ref-folder"), records)

            records.clear()
            state["manifest_only"] = True
            result = _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha=expected,
            )
            self.assertEqual(result, "trashed")
            self.assertIn(("PATCH", "/files/ref-folder"), records)
            state["manifest_only"] = False

            records.clear()
            state["numbered"] = True
            result = _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha=expected,
            )
            self.assertEqual(result, "trashed")
            self.assertIn(("PATCH", "/files/ref-folder"), records)

            records.clear()
            state["duplicate_numbered"] = True
            result = _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha=expected,
            )
            self.assertEqual(result, "trashed")
            self.assertIn(("GET", "/files/cp-1"), records)
            self.assertIn(("GET", "/files/cp-1-duplicate"), records)
            self.assertIn(("PATCH", "/files/ref-folder"), records)

            records.clear()
            state["conflicting_duplicate"] = True
            with self.assertRaisesRegex(_mod.SnapshotDeleteError, "conflicting duplicate checkpoint sequence"):
                _mod.delete_snapshot(
                    client,
                    root_folder_id="root",
                    repository="StreamScapeTV/example",
                    ref="feature/cleanup",
                    expected_source_sha=expected,
                )
            self.assertFalse(any(method == "PATCH" for method, _ in records))
            state["duplicate_numbered"] = False
            state["conflicting_duplicate"] = False

            records.clear()
            state["unexpected"] = True
            with self.assertRaisesRegex(_mod.SnapshotDeleteError, "unexpected non-snapshot file"):
                _mod.delete_snapshot(
                    client,
                    root_folder_id="root",
                    repository="StreamScapeTV/example",
                    ref="feature/cleanup",
                    expected_source_sha=expected,
                )
            self.assertFalse(any(method == "PATCH" for method, _ in records))
            state["unexpected"] = False
            state["numbered"] = False

            records.clear()
            state["missing_ref"] = True
            result = _mod.delete_snapshot(
                client,
                root_folder_id="root",
                repository="StreamScapeTV/example",
                ref="feature/cleanup",
                expected_source_sha=expected,
            )
            self.assertEqual(result, "already-absent")
            self.assertFalse(any(method == "PATCH" for method, _ in records))

            state["missing_ref"] = False
            state["bad_sha"] = True
            records.clear()
            with self.assertRaisesRegex(_mod.SnapshotDeleteError, "does not match expected"):
                _mod.delete_snapshot(
                    client,
                    root_folder_id="root",
                    repository="StreamScapeTV/example",
                    ref="feature/cleanup",
                    expected_source_sha=expected,
                )
            self.assertFalse(any(method == "PATCH" for method, _ in records))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
