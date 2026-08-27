from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CiHelperTests(unittest.TestCase):
    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "android", "python", "node", "flutter", "gitops", "central_dispatch", "self_check", "broker_release", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive"})
        self.assertFalse((ROOT / "PYTHON_INVENTORY.yml").exists())
        self.assertFalse((ROOT / "contracts").exists())

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 10)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        for name in ("apple.yml", "android.yml", "python.yml", "node.yml", "flutter.yml", "gitops.yml"):
            self.assertIn(name, names)

    def test_only_two_custom_actions_exist(self) -> None:
        actions = {p.name for p in (ROOT / "actions").iterdir() if p.is_dir()}
        self.assertEqual(actions, {"agent-state", "google-drive"})

    def test_agent_state_action_has_only_claim_start_finish_lifecycle(self) -> None:
        text = (ROOT / "actions/agent-state/action.yml").read_text()
        self.assertIn("claim_ci_run", text)
        self.assertIn("transition_ci_run", text)
        self.assertIn("external_repository:$repository", text)
        self.assertIn("external_run_url:$run_url", text)
        self.assertIn("https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}", text)
        self.assertNotIn("observed_source_sha", text)
        self.assertNotIn("diagnostic_", text)

    def test_google_drive_action_is_parent_scoped_resumable_and_in_place(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn("https://oauth2.googleapis.com/token", text)
        self.assertIn("GOOGLE_DRIVE_ROOT_FOLDER_ID", text)
        self.assertIn("in parents", text)
        self.assertIn("uploadType=resumable", text)
        self.assertIn("--request PATCH", text)
        self.assertIn("--request POST", text)
        self.assertNotIn("cloudflarestorage.com", text)
        self.assertNotIn("R2_", text)

    def test_agent_state_workflows_use_private_drive_logs_without_public_command_tee(self) -> None:
        for name in ("apple", "android", "python", "node", "flutter", "gitops"):
            text = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
            self.assertIn("GOOGLE_DRIVE_CI_LOGS_FOLDER_ID", text)
            self.assertIn("actions/google-drive@", text)
            self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}.log.gz", text)
            self.assertIn("inspect the private Google Drive CI log", text)
            self.assertNotIn("R2_", text)
            self.assertNotIn("r2-upload", text)
            self.assertNotIn("tee -a", text)

    def test_central_dispatch_passes_the_human_ref_directly(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn("refs/tags/{0}", text)
        self.assertGreaterEqual(text.count("ref: ${{ needs.request.outputs.ref }}"), 7)

    def test_source_snapshot_reuses_drive_helper_and_updates_manifest_in_place(self) -> None:
        broker = (ROOT / "ci-broker/app.py").read_text()
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertIn('"source.snapshot"', broker)
        self.assertIn("workflow_key == 'source.snapshot'", dispatch)
        self.assertIn("git -C source archive --format=zip", dispatch)
        self.assertIn("GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID", dispatch)
        self.assertIn("file_name: source.zip", dispatch)
        self.assertEqual(dispatch.count("file_name: manifest.json"), 2)
        for key in (
            '"repository_name"',
            '"archive_format": "zip"',
            '"archive_format_version": 1',
            '"resolved_source_sha"',
            '"tree_sha"',
            '"source_zip_sha256"',
            '"source_zip_size_bytes"',
            '"manifest_file_id"',
            '"source_zip_file_id"',
            '"folder_id"',
        ):
            self.assertIn(key, dispatch)
        self.assertIn('test "${CREATED_ID}" = "${UPDATED_ID}"', dispatch)
        self.assertIn("Clean snapshot workspace", dispatch)


if __name__ == "__main__":
    unittest.main()
