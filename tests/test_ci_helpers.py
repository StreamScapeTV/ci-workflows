# LEGACY_MIGRATION_RESIDUE: This file verifies still-live compatibility whose source currently contains concrete consumer identity; do not extend that identity coupling.
from pathlib import Path
import http.server
import json
import os
import subprocess
import tempfile
import threading
import urllib.parse
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CiHelperTests(unittest.TestCase):
    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 13)
        self.assertNotIn("broker.yml", names)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        for name in ("apple.yml", "android.yml", "node.yml", "flutter.yml", "maven.yml", "container-service.yml", "branch-delete.yml"):
            self.assertIn(name, names)

    def test_agent_state_action_has_claim_start_observe_finish_lifecycle(self) -> None:
        path = ROOT / "actions/agent-state/action.yml"
        action = yaml.safe_load(path.read_text())
        text = path.read_text()
        self.assertIn("claim_ci_run", text)
        self.assertIn("transition_ci_run", text)
        self.assertIn("external_repository:$repository", text)
        self.assertIn("external_run_url:$run_url", text)
        self.assertIn("https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}", text)
        self.assertIn("observed_source_sha", action["inputs"])
        self.assertIn("observe-source", action["inputs"]["phase"]["description"])
        self.assertIn("cancel-if-active", action["inputs"]["phase"]["description"])
        self.assertIn('[[ "${OBSERVED_SOURCE_SHA}" =~ ^[0-9A-Fa-f]{40}$ ]] || exit 2', text)
        self.assertIn("p_patch:{observed_source_sha:$sha}", text)
        self.assertIn('p_patch:{status:"cancelled"}', text)
        self.assertIn("already_terminal", text)
        self.assertIn("succeeded|failed|cancelled|timed_out) exit 0", text)
        self.assertIn("Agent State cancellation settlement failed", text)
        self.assertNotIn("diagnostic_", text)

    def test_private_git_action_is_one_fixed_runner_aware_tailscale_boundary(self) -> None:
        path = ROOT / "actions/private-git/action.yml"
        action = yaml.safe_load(path.read_text())
        text = path.read_text()
        steps = {step["name"]: step for step in action["runs"]["steps"]}
        require = steps["Require fixed Central Tailscale credentials"]
        connect = steps["Connect to private Git service"]
        verify = steps["Verify private Git service reachability"]
        hosted = "${{ runner.environment == 'github-hosted' }}"
        self.assertEqual(require["if"], hosted)
        self.assertEqual(connect["if"], hosted)
        self.assertEqual(connect["uses"], "tailscale/github-action@v4")
        self.assertEqual(connect["with"]["tags"], "tag:github-ci")
        self.assertNotIn("if", verify)
        self.assertIn('("git.faruqi.dev", 443)', verify["run"])
        self.assertIn("TS_OAUTH_CLIENT_ID", require["run"] + str(connect["with"]))
        self.assertIn("TS_OAUTH_SECRET", require["run"] + str(connect["with"]))
        self.assertNotIn("sudo", text)
        self.assertNotIn("inputs:", text)
        android = (ROOT / ".github/workflows/android.yml").read_text()
        self.assertIn("actions/private-git@", android)
        self.assertNotIn("tailscale/github-action@v4", android)

    def test_agent_state_workflows_use_private_s3_logs_without_public_command_tee(self) -> None:
        plain_text_logs = {"android"}
        for name in ("apple", "android"):
            text = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
            self.assertIn("S3_GITHUB_TRANSPORT_ACCESS_KEY", text)
            self.assertIn("actions/s3-upload@", text)
            self.assertNotIn("GOOGLE_DRIVE", text)
            if name in plain_text_logs:
                self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}.txt", text)
                self.assertIn("content_type: text/plain", text)
                self.assertNotIn("gzip: 'true'", text)
            elif name == "apple":
                self.assertIn(
                    "${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.lane }}.txt",
                    text,
                )
                self.assertIn("content_type: text/plain", text)
                self.assertNotIn("gzip: 'true'", text)
            else:
                self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}.log.gz", text)
                self.assertIn("content_type: application/gzip", text)
                self.assertIn("gzip: 'true'", text)
            self.assertIn("inspect the private CI log", text)
            self.assertNotIn("R2_", text)
            self.assertNotIn("r2-upload", text)
            self.assertNotIn("tee -a", text)


    def test_central_dispatch_passes_the_human_ref_directly(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn("refs/tags/{0}", text)
        self.assertGreaterEqual(text.count("ref: ${{ needs.request.outputs.ref }}"), 6)

    def test_central_dispatch_routes_only_bounded_oci_reproducibility_inputs(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        job = workflow["jobs"]["oci_reproducibility"]
        self.assertEqual(job["if"], "${{ needs.request.outputs.workflow_key == 'oci.reproducibility' }}")
        self.assertEqual(job["uses"], "./.github/workflows/oci-reproducibility.yml")
        self.assertEqual(
            set(job["with"]),
            {"repository", "ref", "dockerfile_path", "build_context", "ci_run_id"},
        )
        self.assertEqual(job["with"]["repository"], "${{ needs.request.outputs.repository }}")
        self.assertEqual(job["with"]["ref"], "${{ needs.request.outputs.ref }}")
        self.assertEqual(
            job["with"]["dockerfile_path"],
            "${{ fromJSON(needs.request.outputs.inputs_json).dockerfile_path }}",
        )
        self.assertEqual(
            job["with"]["build_context"],
            "${{ fromJSON(needs.request.outputs.inputs_json).build_context }}",
        )
        self.assertEqual(job["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        self.assertTrue(job["secrets"] == "inherit")

    def test_fixed_profiles_replace_arbitrary_command_transport(self) -> None:
        forbidden = ("prepare_command", "build_command", "test_command", "release_command", "bash -lc")
        for name in ("apple", "android"):
            text = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
            for value in forbidden:
                self.assertNotIn(value, text)
            self.assertIn("Scrub configured CI secrets from private log", text)
            self.assertIn("steps.scrub.outcome == 'success'", text)
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        for value in forbidden[:-1]:
            self.assertNotIn(value, dispatch)

    def test_android_dependency_cache_uses_github_default_branch_writer_policy(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text())
        steps = workflow["jobs"]["ci"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        scope = by_name["Resolve IPTV Android default-branch cache scope"]
        restore = by_name["Restore IPTV Android default-branch dependency cache"]
        save = by_name["Save IPTV Android default-branch dependency cache"]
        expected_paths = "~/.gradle/wrapper\n~/.gradle/caches/modules-2\n"

        script = scope["run"]
        self.assertIn("StreamScapeTV/iptv-android", script)
        self.assertIn("https://api.github.com/repos/${SOURCE_REPOSITORY}", script)
        self.assertIn('get("default_branch", "")', script)
        self.assertIn('test "${source_ref}" = "${default_branch}"', script)
        self.assertIn('test "${checkout_branch}" = "${default_branch}"', script)
        self.assertNotIn('test "${source_ref}" = develop', script)
        self.assertIn('source_ref="${source_ref#refs/heads/}"', script)
        self.assertIn("inputs.repository || github.repository", scope["env"]["SOURCE_REPOSITORY"])
        self.assertIn("inputs.ref", scope["env"]["REQUESTED_REF"])
        self.assertEqual(restore["if"], "${{ steps.android_default_cache_scope.outputs.enabled == 'true' }}")
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertEqual(restore["with"]["path"], expected_paths)
        self.assertEqual(save["with"]["path"], expected_paths)
        self.assertIn("iptv-android-default-gradle-deps-v2-", restore["with"]["key"])
        self.assertNotIn("inputs.ref", restore["with"]["key"])
        self.assertNotIn("github.ref", restore["with"]["key"])
        self.assertEqual(save["with"]["key"], "${{ steps.gradle_dependency_cache.outputs.cache-primary-key }}")
        self.assertIn("steps.commands.outcome == 'success'", save["if"])
        diagnostic = by_name["Classify Android terminal diagnostic"]
        self.assertIn("CACHE_SAVE_OUTCOME", diagnostic["env"])
        self.assertIn('test "${CACHE_SAVE_OUTCOME}" = success', diagnostic["run"])
        self.assertIn('test "${CACHE_SAVE_OUTCOME}" = skipped', diagnostic["run"])
        finish = by_name["Finish Agent State run"]
        self.assertEqual(finish["with"]["status"], "${{ steps.terminal_diagnostic.outputs.success == 'true' && 'succeeded' || 'failed' }}")

    def test_android_owner_profiles_and_gitops_retirement_are_explicit(self) -> None:
        android = (ROOT / ".github/workflows/android.yml").read_text()
        for profile in ("smoke)", "compile)", "unit)", "targeted-unit)", "targeted-tests)", "lint)", "assemble)", "release)"):
            self.assertIn(profile, android)
        self.assertIn("CIW_MAVEN_PACKAGE_READ_TOKEN", android)
        self.assertNotIn("android-full-correctness.init.gradle", android)
        self.assertFalse((ROOT / ".github/workflows/gitops.yml").exists())
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        self.assertNotIn("workflow_key == 'validation.gitops'", dispatch)

    def test_product_release_uses_central_private_registry(self) -> None:
        dispatch = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        release = (ROOT / ".github/workflows/public-native-image-chart.yml").read_text()
        self.assertIn("workflow_key == 'release.public-native-image-chart'", dispatch)
        self.assertIn("workflow_key == 'release.public-native-image-chart-primary'", dispatch)
        self.assertIn("workflow_key == 'release.public-native-image-chart-secondary'", dispatch)
        self.assertIn('uses: ./.github/workflows/public-native-image-chart.yml', dispatch)
        self.assertIn('repository: ${{ needs.request.outputs.repository }}', dispatch)
        self.assertIn('ref: ${{ needs.request.outputs.ref }}', dispatch)
        self.assertIn('repository: ${{ inputs.repository }}', release)
        self.assertIn('ref: ${{ steps.requested_ref.outputs.full_ref }}', release)
        self.assertIn('test "${{ inputs.publish_latest_image }}" = false', release)
        self.assertIn('REGISTRY: git.faruqi.dev', release)
        self.assertIn('FORGEJO_REGISTRY_USERNAME', release)
        self.assertIn('FORGEJO_REGISTRY_TOKEN', release)
        self.assertIn('actions/private-git@main', release)
        self.assertIn('Authenticated private registry read-back', release)
        self.assertNotIn('ghcr.io', release)
        self.assertIn('phase: start', release)
        self.assertIn('phase: finish', release)
        self.assertIn('actions/s3-upload@main', release)
        self.assertNotIn('kubectl', release)
        self.assertNotIn('helm upgrade', release)




if __name__ == "__main__":
    unittest.main()
