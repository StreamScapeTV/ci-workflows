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
        self.assertEqual(set(call["inputs"]), {"repository", "ref", "expected_source_sha"})
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

    def test_branch_delete_always_routes_same_ref_snapshot_cleanup(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/branch-delete.yml").read_text())
        delete = workflow["jobs"]["delete"]
        cleanup = workflow["jobs"]["snapshot_cleanup"]
        finish = workflow["jobs"]["finish"]
        self.assertEqual(delete["outputs"]["branch_was_present"], "${{ steps.delete_branch.outputs.branch_was_present }}")
        self.assertEqual(cleanup["uses"], "./.github/workflows/source-snapshot-delete.yml")
        self.assertEqual(cleanup["with"]["repository"], "${{ inputs.repository }}")
        self.assertEqual(cleanup["with"]["ref"], "${{ inputs.branch }}")
        self.assertEqual(
            cleanup["with"]["expected_source_sha"],
            "${{ needs.delete.outputs.branch_was_present == 'true' && '' || inputs.expected_head }}",
        )
        self.assertEqual(cleanup["secrets"], "inherit")
        self.assertIn("needs.snapshot_cleanup.result == 'success'", finish["steps"][0]["with"]["status"])
        delete_script = next(step for step in delete["steps"] if step.get("name") == "Delete exact eligible branch")["run"]
        self.assertIn('output.write("branch_was_present=false\\n")', delete_script)
        self.assertIn('output.write("branch_was_present=true\\n")', delete_script)

    def test_exact_manifest_identity_is_required_and_cleanup_is_idempotent(self) -> None:
        expected = "a" * 40
        records: list[tuple[str, str]] = []
        state = {"missing_ref": False, "bad_sha": False}

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
                if parsed.path == "/files/manifest-id" and urllib.parse.parse_qs(parsed.query).get("alt") == ["media"]:
                    manifest = {
                        "repository": "StreamScapeTV/example",
                        "requested_ref": "feature/cleanup",
                        "is_tag": False,
                        "resolved_source_sha": "b" * 40 if state["bad_sha"] else expected,
                        "folder_id": "ref-folder",
                        "manifest_file_id": "manifest-id",
                        "archive_file_id": "archive-id",
                        "source_zip_file_id": "archive-id",
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
                    self._json(200, {"files": [
                        {"id": "manifest-id", "name": "manifest.json", "mimeType": "application/json"},
                        {"id": "archive-id", "name": "example-feature%2Fcleanup.zip", "mimeType": "application/zip"},
                    ]})
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
