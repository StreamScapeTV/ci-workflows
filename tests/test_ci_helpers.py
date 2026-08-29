from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CiHelperTests(unittest.TestCase):
    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "android", "python", "node", "flutter", "public_native_image_chart", "central_dispatch", "self_check", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive", "private_git"})
        self.assertEqual(set(inventory["services"]), {"runner_images"})
        self.assertFalse((ROOT / "ci-broker").exists())
        self.assertFalse((ROOT / "PYTHON_INVENTORY.yml").exists())
        self.assertFalse((ROOT / "contracts").exists())

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 9)
        self.assertNotIn("broker.yml", names)
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
        self.assertIn("repository_folder_id:", text)
        self.assertIn("DRIVE_REPOSITORY_FOLDER_ID", text)
        self.assertIn("configured repository folder is not directly below the configured root", text)
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
        plain_text_logs = {"android", "node", "flutter"}
        for name in ("apple", "android", "python", "node", "flutter"):
            text = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
            self.assertIn("GOOGLE_DRIVE_CI_LOGS_FOLDER_ID", text)
            self.assertIn("actions/google-drive@", text)
            if name in plain_text_logs:
                self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}.txt", text)
                self.assertIn("mime_type: text/plain", text)
                self.assertNotIn("gzip: 'true'", text)
            else:
                self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}.log.gz", text)
                self.assertIn("mime_type: application/gzip", text)
                self.assertIn("gzip: 'true'", text)
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

    def test_android_dependency_cache_is_iptv_android_develop_only(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text())
        steps = workflow["jobs"]["ci"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        scope = by_name["Resolve IPTV Android develop dependency cache scope"]
        restore = by_name["Restore IPTV Android develop dependency cache"]
        save = by_name["Save IPTV Android develop dependency cache"]
        expected_paths = "~/.gradle/wrapper\n~/.gradle/caches/modules-2\n"

        self.assertIn("StreamScapeTV/iptv-android", scope["run"])
        self.assertIn('test "${source_ref}" = develop', scope["run"])
        self.assertIn('source_ref="${source_ref#refs/heads/}"', scope["run"])
        self.assertIn("inputs.repository || github.repository", scope["env"]["SOURCE_REPOSITORY"])
        self.assertIn("inputs.ref", scope["env"]["REQUESTED_REF"])
        self.assertEqual(restore["if"], "${{ steps.android_develop_cache_scope.outputs.enabled == 'true' }}")
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertEqual(restore["with"]["path"], expected_paths)
        self.assertEqual(save["with"]["path"], expected_paths)
        self.assertIn("iptv-android-develop-gradle-deps-v1-", restore["with"]["key"])
        self.assertNotIn("inputs.ref", restore["with"]["key"])
        self.assertNotIn("github.ref", restore["with"]["key"])
        self.assertEqual(save["with"]["key"], "${{ steps.gradle_dependency_cache.outputs.cache-primary-key }}")
        self.assertIn("steps.commands.outcome == 'success'", save["if"])
        finish = by_name["Finish Agent State run"]
        self.assertIn("steps.gradle_dependency_cache_save.outcome == 'success'", finish["with"]["status"])
        self.assertIn("steps.gradle_dependency_cache_save.outcome == 'skipped'", finish["with"]["status"])

    def test_android_owner_profiles_and_gitops_retirement_are_explicit(self) -> None:
        android = (ROOT / ".github/workflows/android.yml").read_text()
        for profile in ("smoke)", "compile)", "unit)", "targeted-unit)", "lint)", "assemble)", "full)", "release)"):
            self.assertIn(profile, android)
        self.assertIn("CIW_MAVEN_PACKAGE_READ_TOKEN", android)
        self.assertIn("compileDebugKotlin testDebugUnitTest lintDebug assembleDebug", android)
        self.assertFalse((ROOT / ".github/workflows/gitops.yml").exists())
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn("workflow_key == 'validation.gitops'", dispatch)

    def test_product_release_uses_central_private_registry(self) -> None:
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        release = (ROOT / ".github/workflows/public-native-image-chart.yml").read_text()
        self.assertIn("workflow_key == 'release.public-native-image-chart'", dispatch)
        self.assertIn('uses: ./.github/workflows/public-native-image-chart.yml', dispatch)
        self.assertIn('repository: ${{ needs.request.outputs.repository }}', dispatch)
        self.assertIn('ref: ${{ needs.request.outputs.ref }}', dispatch)
        self.assertIn('repository: ${{ inputs.repository }}', release)
        self.assertIn('ref: ${{ inputs.ref }}', release)
        self.assertIn('test "${{ inputs.publish_latest_image }}" = false', release)
        self.assertIn('REGISTRY: git.faruqi.dev', release)
        self.assertIn('FORGEJO_REGISTRY_USERNAME', release)
        self.assertIn('FORGEJO_REGISTRY_TOKEN', release)
        self.assertIn('actions/private-git@main', release)
        self.assertIn('Authenticated private registry read-back', release)
        self.assertNotIn('ghcr.io', release)
        self.assertIn('phase: start', release)
        self.assertIn('phase: finish', release)
        self.assertIn('actions/google-drive@main', release)
        self.assertNotIn('kubectl', release)
        self.assertNotIn('helm upgrade', release)

    def test_source_snapshot_reuses_drive_helper_and_updates_manifest_in_place(self) -> None:
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("workflow_key == 'source.snapshot'", dispatch)
        self.assertIn("git -C source archive --format=zip", dispatch)
        self.assertIn("GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID", dispatch)
        self.assertIn("Resolve durable Drive repository folder", dispatch)
        self.assertIn("Google Drive repository folder ID", dispatch)
        self.assertEqual(dispatch.count("repository_folder_id: ${{ steps.drive_repository.outputs.folder_id }}"), 3)
        self.assertIn("Google Drive repository folder ID: `1--JcV6RK8jdIIP3ONWw420QDVpNTQ7L8`", agents)
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
