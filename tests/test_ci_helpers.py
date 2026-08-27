from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CiHelperTests(unittest.TestCase):
    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "android", "python", "node", "flutter", "central_dispatch", "self_check", "broker_release", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive", "private_git"})
        self.assertFalse((ROOT / "PYTHON_INVENTORY.yml").exists())
        self.assertFalse((ROOT / "contracts").exists())

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 9)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        for name in ("apple.yml", "android.yml", "python.yml", "node.yml", "flutter.yml"):
            self.assertIn(name, names)

    def test_only_three_custom_actions_exist(self) -> None:
        actions = {p.name for p in (ROOT / "actions").iterdir() if p.is_dir()}
        self.assertEqual(actions, {"agent-state", "google-drive", "private-git"})

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

    def test_private_git_action_is_one_fixed_tailscale_boundary(self) -> None:
        text = (ROOT / "actions/private-git/action.yml").read_text()
        self.assertIn("tailscale/github-action@v4", text)
        self.assertIn("tags: tag:github-ci", text)
        self.assertIn('("git.faruqi.dev", 443)', text)
        self.assertIn("TS_OAUTH_CLIENT_ID", text)
        self.assertIn("TS_OAUTH_SECRET", text)
        self.assertNotIn("inputs:", text)
        android = (ROOT / ".github/workflows/android.yml").read_text()
        self.assertIn("actions/private-git@", android)
        self.assertNotIn("tailscale/github-action@v4", android)

    def test_agent_state_workflows_use_private_drive_logs_without_public_command_tee(self) -> None:
        for name in ("apple", "android", "python", "node", "flutter"):
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
        self.assertGreaterEqual(text.count("ref: ${{ needs.request.outputs.ref }}"), 6)

    def test_central_dispatch_is_newest_run_wins_per_active_branch_key(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertIn("group: central-ci-${{ inputs.active_key }}", text)
        self.assertIn("cancel-in-progress: true", text)
        broker = (ROOT / "ci-broker/app.py").read_text()
        active_key = broker[broker.index("def active_key"): broker.index("def dispatch_inputs")]
        self.assertIn('{"repository": self.repository, "ref": self.ref, "is_tag": self.is_tag}', active_key)
        self.assertNotIn("ci_run_id", active_key)
        self.assertNotIn("test_profile", active_key)
        self.assertNotIn("workflow_key", active_key)

    def test_fixed_profiles_replace_arbitrary_command_transport(self) -> None:
        forbidden = ("prepare_command", "build_command", "test_command", "release_command", "bash -lc")
        for name in ("apple", "android", "python", "node", "flutter"):
            text = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
            for value in forbidden:
                self.assertNotIn(value, text)
            self.assertIn("Scrub configured CI secrets from private log", text)
            self.assertIn("steps.scrub.outcome == 'success'", text)
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        for value in forbidden[:-1]:
            self.assertNotIn(value, dispatch)

    def test_android_owner_profiles_and_gitops_retirement_are_explicit(self) -> None:
        android = (ROOT / ".github/workflows/android.yml").read_text()
        for profile in ("smoke)", "compile)", "unit)", "targeted-unit)", "lint)", "assemble)", "full)", "release)"):
            self.assertIn(profile, android)
        self.assertIn("CIW_MAVEN_PACKAGE_READ_TOKEN", android)
        self.assertIn("compileDebugKotlin testDebugUnitTest lintDebug assembleDebug", android)
        self.assertFalse((ROOT / ".github/workflows/gitops.yml").exists())
        broker = (ROOT / "ci-broker/app.py").read_text()
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn('"validation.gitops"', broker)
        self.assertNotIn("workflow_key == 'validation.gitops'", dispatch)

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
