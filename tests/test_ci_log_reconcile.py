from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/ci_log_reconcile.py"
spec = importlib.util.spec_from_file_location("ci_log_reconcile", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeDrive:
    def __init__(self, tree: dict[str, list[dict[str, str]]]) -> None:
        self.tree = {key: [dict(value) for value in values] for key, values in tree.items()}
        self.deleted: list[str] = []

    def children(self, parent_id: str) -> list[dict[str, str]]:
        return [dict(value) for value in self.tree.get(parent_id, [])]

    def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)
        self.tree.pop(file_id, None)
        for values in self.tree.values():
            values[:] = [value for value in values if value["id"] != file_id]


class FakeState:
    def __init__(self, current: set[mod.ExecutionIdentity]) -> None:
        self.current = current
        self.calls: list[list[mod.ExecutionIdentity]] = []

    def filter_current(self, candidates: list[mod.ExecutionIdentity]) -> set[mod.ExecutionIdentity]:
        self.calls.append(list(candidates))
        return set(candidates) & self.current


class CiLogReconcileTests(unittest.TestCase):
    def test_filename_parser_accepts_current_single_and_suffixed_logs(self) -> None:
        for name in (
            "123-2.txt",
            "123-2-apple-swiftpm.txt",
            "123-2-screenshot-phone-portrait.txt",
            "123-2-macos-targeted-tests.txt",
        ):
            self.assertEqual(mod.parse_log_filename(name), (123, 2))
        for name in ("123.txt", "0-1.txt", "123-0.txt", "123-2.log", "notes.txt", "123-2-.txt"):
            self.assertIsNone(mod.parse_log_filename(name))

    def test_reconcile_deletes_orphan_suffix_family_and_preserves_current_and_unknown(self) -> None:
        current = mod.ExecutionIdentity("StreamScapeTV/repo", "develop", 200, 1)
        orphan = mod.ExecutionIdentity("StreamScapeTV/repo", "develop", 100, 3)
        drive = FakeDrive(
            {
                "root": [
                    {"id": "repo", "name": "repo", "mimeType": mod.FOLDER_MIME},
                    {"id": "root-note", "name": "README.txt", "mimeType": "text/plain"},
                ],
                "repo": [{"id": "ref", "name": "develop", "mimeType": mod.FOLDER_MIME}],
                "ref": [
                    {"id": "orphan-a", "name": "100-3.txt", "mimeType": "text/plain"},
                    {"id": "orphan-b", "name": "100-3-screenshot-tv.txt", "mimeType": "text/plain"},
                    {"id": "current-a", "name": "200-1.txt", "mimeType": "text/plain"},
                    {"id": "current-b", "name": "200-1-ios-build.txt", "mimeType": "text/plain"},
                    {"id": "unknown", "name": "manual-note.txt", "mimeType": "text/plain"},
                ],
            }
        )
        state = FakeState({current})
        result = mod.reconcile(drive, state, root_folder_id="root")
        self.assertEqual(result["candidate_identities"], 2)
        self.assertEqual(result["deleted_files"], 2)
        self.assertEqual(drive.deleted, ["orphan-a", "orphan-b"])
        self.assertEqual(state.calls, [[orphan, current]])
        self.assertEqual({item["id"] for item in drive.children("ref")}, {"current-a", "current-b", "unknown"})

    def test_unknown_files_and_unrecognized_root_entries_fail_closed(self) -> None:
        drive = FakeDrive(
            {
                "root": [
                    {"id": "odd", "name": "repo with spaces", "mimeType": mod.FOLDER_MIME},
                    {"id": "loose", "name": "999-1.txt", "mimeType": "text/plain"},
                ],
                "odd": [{"id": "nested", "name": "1-1.txt", "mimeType": "text/plain"}],
            }
        )
        state = FakeState(set())
        result = mod.reconcile(drive, state, root_folder_id="root")
        self.assertEqual(result["discovered_files"], 0)
        self.assertEqual(drive.deleted, [])
        self.assertEqual(state.calls, [])

    def test_empty_ref_and_repository_folders_are_pruned_only_after_orphan_delete(self) -> None:
        orphan = mod.ExecutionIdentity("StreamScapeTV/repo", "feature/x", 300, 1)
        drive = FakeDrive(
            {
                "root": [{"id": "repo", "name": "repo", "mimeType": mod.FOLDER_MIME}],
                "repo": [{"id": "ref", "name": "feature/x", "mimeType": mod.FOLDER_MIME}],
                "ref": [{"id": "log", "name": "300-1.txt", "mimeType": "text/plain"}],
            }
        )
        result = mod.reconcile(drive, FakeState(set()), root_folder_id="root")
        self.assertEqual(result["deleted_files"], 1)
        self.assertEqual(result["deleted_ref_folders"], 1)
        self.assertEqual(result["deleted_repository_folders"], 1)
        self.assertEqual(drive.deleted, ["log", "ref", "repo"])
        self.assertNotIn("root", drive.deleted)

    def test_current_identity_prevents_folder_pruning(self) -> None:
        current = mod.ExecutionIdentity("StreamScapeTV/repo", "main", 400, 1)
        drive = FakeDrive(
            {
                "root": [{"id": "repo", "name": "repo", "mimeType": mod.FOLDER_MIME}],
                "repo": [{"id": "ref", "name": "main", "mimeType": mod.FOLDER_MIME}],
                "ref": [{"id": "log", "name": "400-1.txt", "mimeType": "text/plain"}],
            }
        )
        result = mod.reconcile(drive, FakeState({current}), root_folder_id="root")
        self.assertEqual(result["deleted_files"], 0)
        self.assertEqual(drive.deleted, [])


class CiLogReconcileSourceContractTests(unittest.TestCase):
    def test_scheduled_workflow_is_direct_and_creates_no_private_cleanup_log(self) -> None:
        text = (ROOT / ".github/workflows/ci-log-retention.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("scripts/ci/ci_log_reconcile.py", text)
        self.assertIn("AGENT_STATE_SUPABASE_SECRET_KEY", text)
        self.assertIn("GOOGLE_DRIVE_CI_LOGS_FOLDER_ID", text)
        self.assertNotIn("actions/agent-state", text)
        self.assertNotIn("GOOGLE_DRIVE_ROOT_FOLDER_ID", text)
        self.assertNotIn("file_name:", text)
        self.assertNotIn("Upload CI log", text)

    def test_central_dispatch_no_longer_knows_ci_log_cleanup_semantic(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text(encoding="utf-8")
        self.assertNotIn("maintenance.ci-log-delete", text)
        self.assertNotIn("ci_log_delete:", text)
        self.assertNotIn("Validate private CI-log deletion request", text)

    def test_inventory_tracks_reconciler(self) -> None:
        inventory = (ROOT / "INVENTORY.yaml").read_text(encoding="utf-8")
        self.assertIn("ci_log_retention: .github/workflows/ci-log-retention.yml", inventory)
        self.assertIn("ci_log_reconcile: scripts/ci/ci_log_reconcile.py", inventory)
        self.assertNotIn("ci_log_delete:", inventory)


if __name__ == "__main__":
    unittest.main()
