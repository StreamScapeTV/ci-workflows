from __future__ import annotations

import subprocess

import yaml

from tests import test_ci_helpers as _prior


class CiHelperTests(_prior.CiHelperTests):
    """Current shared-helper contract while retaining the complete helper suite."""

    def test_google_drive_resumable_upload_retries_only_bounded_transient_failures(self) -> None:
        text = (_prior.ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn("max_media_upload_attempts=5", text)
        self.assertIn("media_retry_delays=(3 5 8 13)", text)
        self.assertIn("408|429|5??", text)
        self.assertIn("retryable_media_failure", text)
        self.assertIn("failed after bounded recovery attempts", text)
        self.assertNotIn('sleep "${media_attempt}"', text)
        self.assertIn('media_curl_status}" -eq 92', text)
        self.assertIn('--http1.1', text)
        self.assertIn('switching remaining bounded recovery to HTTP/1.1', text)

    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((_prior.ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "apple_binary", "apple_swiftpm", "android", "python", "node", "flutter", "maven", "container_service", "public_native_image_chart", "oci_reproducibility", "branch_delete", "source_snapshot_delete", "source_snapshot", "source_checkpoint_publish", "central_dispatch", "ci_log_retention", "self_check", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive", "private_git", "source_snapshot"})
        self.assertEqual(set(inventory["scripts"]), {"oci_reproducibility", "ci_log_reconcile", "source_snapshot_delete", "source_checkpoint_publish", "swiftpm_binary", "release_tag_contract"})
        self.assertEqual(set(inventory["services"]), {"runner_images"})

    def test_only_three_custom_actions_exist(self) -> None:
        actions = {p.name for p in (_prior.ROOT / "actions").iterdir() if p.is_dir()}
        self.assertEqual(actions, {"agent-state", "google-drive", "private-git", "source-snapshot"})

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (_prior.ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 19)
        self.assertNotIn("broker.yml", names)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        self.assertIn("source-snapshot-delete.yml", names)
        self.assertIn("ci-log-retention.yml", names)
        self.assertIn("source-snapshot.yml", names)
        self.assertNotIn("source-bundle-publish.yml", names)
        self.assertIn("source-checkpoint-publish.yml", names)
        self.assertIn("apple-binary.yml", names)
        self.assertIn("apple-swiftpm.yml", names)
        self.assertNotIn("streamscape-media-release.yml", names)
        self.assertNotIn("streamscape-media-apple-binary.yml", names)

    def test_long_running_execution_jobs_have_five_hour_ceiling(self) -> None:
        workflows = [
            "apple.yml",
            "android.yml",
            "python.yml",
            "node.yml",
            "flutter.yml",
            "maven.yml",
            "public-native-image-chart.yml",
        ]
        for filename in workflows:
            workflow = yaml.safe_load((_prior.ROOT / ".github/workflows" / filename).read_text())
            for job in workflow.get("jobs", {}).values():
                if "runs-on" in job and "timeout-minutes" in job:
                    self.assertLessEqual(job["timeout-minutes"], 300, filename)

    def test_fixed_profiles_replace_arbitrary_command_transport(self) -> None:
        super().test_fixed_profiles_replace_arbitrary_command_transport()
        action = (_prior.ROOT / "actions/agent-state/action.yml").read_text()
        self.assertNotIn("commands_json", action)

    def test_source_bundle_publish_is_retired(self) -> None:
        super().test_source_bundle_publish_is_retired()
        inventory = (_prior.ROOT / "INVENTORY.yaml").read_text()
        self.assertNotIn("source_bundle", inventory)

    def test_agent_state_workflows_use_private_drive_logs_without_public_command_tee(self) -> None:
        super().test_agent_state_workflows_use_private_drive_logs_without_public_command_tee()
        for path in (_prior.ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text()
            self.assertNotIn("tee -a \"${CI_LOG}\"", text, path.name)

    def test_agent_state_action_has_claim_start_observe_finish_lifecycle(self) -> None:
        super().test_agent_state_action_has_claim_start_observe_finish_lifecycle()
        text = (_prior.ROOT / "actions/agent-state/action.yml").read_text()
        self.assertIn("cancel-if-active", text)

    def test_central_dispatch_preserves_newest_run_wins_with_snapshot_isolation(self) -> None:
        dispatch = yaml.safe_load((_prior.ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        for name, job in dispatch["jobs"].items():
            if name in {"branch_delete", "source_checkpoint_publish"}:
                self.assertFalse(job["concurrency"]["cancel-in-progress"])
            elif name == "source_snapshot":
                self.assertTrue(job["concurrency"]["cancel-in-progress"])

    def test_product_release_uses_central_private_registry(self) -> None:
        super().test_product_release_uses_central_private_registry()
        text = (_prior.ROOT / ".github/workflows/maven.yml").read_text()
        self.assertIn("MAVEN_PUBLISH_USERNAME", text)
        self.assertIn("MAVEN_PUBLISH_TOKEN", text)
