from __future__ import annotations

import os
import subprocess
import tempfile

import yaml

from tests import test_ci_helpers as _prior


class CiHelperTests(_prior.CiHelperTests):
    """Current shared-helper contract while retaining the complete helper suite."""

    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((_prior.ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "apple_credential_preflight", "repository", "repository_plan", "apple_binary", "apple_swiftpm", "library_package_release", "android", "cluster_inspector", "maven", "public_native_image_chart", "oci_reproducibility", "branch_delete", "ci_cancel", "central_dispatch", "central_ci_settle", "github_issue_dependency_projection", "self_check"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "s3_upload", "s3_log_stream", "private_git", "dockerhub_read"})
        self.assertEqual(set(inventory["scripts"]), {"ci_cancel", "s3_put", "oci_reproducibility", "github_issue_dependency_projection", "repository_log_checkpoint", "repository_log_capture", "repository_test_fixtures", "repository_log_timeline", "repository_progress_relay", "repository_runtime_probe", "repository_execution_plan", "protected_deployed_conformance", "repository_apple_physical_device", "repository_macos_netrc", "apple_credential_preflight", "swiftpm_binary", "apple_dependency_cache", "hosted_macos_dns"})
        self.assertEqual(set(inventory["contracts"]), {"repository_ci_v1"})
        self.assertEqual(inventory["contracts"]["repository_ci_v1"], "contracts/repository-ci-v1.json")
        self.assertNotIn("services", inventory)

    def test_unused_arc_runner_image_subsystem_is_retired_without_deleting_product_oci(self) -> None:
        root = _prior.ROOT
        self.assertFalse((root / "runner-images").exists())
        self.assertFalse((root / ".github/workflows/runner-images.yml").exists())
        self.assertFalse((root / ".github/dependabot.yml").exists())
        self.assertFalse((root / "tests/test_runner_images_workflow.py").exists())
        inventory = yaml.safe_load((root / "INVENTORY.yaml").read_text())
        self.assertNotIn("services", inventory)
        for key in ("repository", "repository_plan", "oci_reproducibility", "public_native_image_chart"):
            self.assertTrue((root / inventory["workflows"][key]).is_file())
        for path in ("docs/repository-ci.md", "contracts/repository-ci-v1.json"):
            self.assertTrue((root / path).is_file())
        self.assertIn("registry_oci_publish", (root / "contracts/repository-ci-v1.json").read_text())

    def test_only_three_custom_actions_exist(self) -> None:
        actions = {p.name for p in (_prior.ROOT / "actions").iterdir() if p.is_dir()}
        legacy = {"agent-state", "s3-upload", "s3-log-stream", "private-git", "dockerhub-read"}
        # per-purpose workflow building blocks (docs/per-purpose-ci-workflows.md)
        added = {"ci-setup", "ci-cache", "setup-tailscale", "ci-release"}
        self.assertTrue(legacy <= actions <= legacy | added, actions ^ (legacy | added))

    def test_workflows_use_no_reusable_prefix(self) -> None:
        every = {p.name for p in (_prior.ROOT / ".github/workflows").glob("*.yml")}
        # the per-purpose ecosystem workflows (ci-<ecosystem>[-stage].yml) are checked in test_ci_per_purpose_workflows
        names = {n for n in every if not n.startswith(("ci-gradle", "ci-xcode", "ci-python", "ci-node", "ci-oci"))}
        self.assertEqual(len(names), 18)
        self.assertIn("apple-credential-preflight.yml", names)
        self.assertNotIn("broker.yml", names)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        self.assertIn("repository-plan.yml", names)
        self.assertIn("github-issue-dependency-projection.yml", names)
        self.assertNotIn("source-bundle-publish.yml", names)
        self.assertIn("cluster-inspector.yml", names)
        self.assertIn("apple-binary.yml", names)
        self.assertIn("apple-swiftpm.yml", names)
        self.assertIn("library-package-release.yml", names)

    def test_long_running_execution_jobs_have_five_hour_ceiling(self) -> None:
        expected = {
            "apple.yml": ("execute",),
            "repository.yml": ("execute",),
            "android.yml": ("ci",),
            "maven.yml": ("publish",),
            "apple-binary.yml": ("publish",),
            "apple-swiftpm.yml": ("publish",),
            "oci-reproducibility.yml": ("prove",),
            "public-native-image-chart.yml": ("publish",),
        }
        workflows = _prior.ROOT / ".github/workflows"
        for filename, jobs in expected.items():
            workflow = yaml.safe_load((workflows / filename).read_text())
            for job in jobs:
                self.assertEqual(workflow["jobs"][job]["timeout-minutes"], 300, f"{filename}:{job}")

        apple = yaml.safe_load((workflows / "apple.yml").read_text())
        self.assertEqual(apple["jobs"]["plan"]["timeout-minutes"], 10)
        self.assertEqual(apple["jobs"]["finish"]["timeout-minutes"], 10)
        for filename, job, minutes in (
            ("branch-delete.yml", "delete", 10),
            ("cluster-inspector.yml", "inspect", 30),
        ):
            workflow = yaml.safe_load((workflows / filename).read_text())
            self.assertEqual(workflow["jobs"][job]["timeout-minutes"], minutes, f"{filename}:{job}")

    def test_central_dispatch_preserves_newest_run_wins_with_snapshot_isolation(self) -> None:
        workflow = yaml.safe_load((_prior.ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        jobs = workflow["jobs"]
        request = jobs["request"]
        validation_jobs = (
            "repository",
            "android",
            "cluster_inspector",
            "public_native_image_chart",
            "oci_reproducibility",
        )
        serialized_release_jobs = (
            "repository_release",
            "apple_release",
            "android_release",
            "maven",
            "apple_binary",
            "apple_swiftpm",
            "library_package_release",
        )
        self.assertNotIn("concurrency", workflow)
        self.assertNotIn("concurrency", request)

        self.assertEqual(
            request["outputs"]["validation_concurrency_key"],
            "${{ steps.validation_lane.outputs.concurrency_key }}",
        )
        self.assertEqual(
            request["outputs"]["validation_cancel_in_progress"],
            "${{ steps.validation_lane.outputs.cancel_in_progress }}",
        )
        request_by_name = {
            step.get("name"): step
            for step in request["steps"]
            if step.get("name")
        }
        lane = request_by_name["Resolve bounded validation concurrency lane"]
        self.assertEqual(lane["id"], "validation_lane")
        lane_script = lane["run"]
        self.assertIn("central-ordinary-branch-validation-v2", lane_script)
        self.assertIn("central-validation-lane-v1", lane_script)
        self.assertIn("SOURCE_REPOSITORY", lane["env"])
        self.assertIn("SOURCE_REF", lane["env"])
        self.assertIn("SOURCE_IS_TAG", lane["env"])
        self.assertIn("WORKFLOW_KEY", lane["env"])
        self.assertIn("TEST_PROFILE", lane["env"])
        self.assertNotIn("active_key", lane_script)
        self.assertNotIn("source_sha", lane_script.lower())

        expected_group = "central-ci-${{ needs.request.outputs.validation_concurrency_key }}"
        expected_cancel = "${{ needs.request.outputs.validation_cancel_in_progress == 'true' }}"
        for name in validation_jobs:
            self.assertEqual(jobs[name]["concurrency"]["group"], expected_group)
            self.assertEqual(jobs[name]["concurrency"]["cancel-in-progress"], expected_cancel)
            self.assertNotIn("inputs.active_key", jobs[name]["concurrency"]["group"])

        for name in serialized_release_jobs:
            self.assertEqual(
                jobs[name]["concurrency"]["group"],
                "central-release-${{ needs.request.outputs.repository }}-${{ needs.request.outputs.workflow_key }}-${{ needs.request.outputs.test_profile }}",
            )
            self.assertFalse(jobs[name]["concurrency"]["cancel-in-progress"])

        def resolve_lane(
            *,
            repository: str = "StreamScapeTV/example",
            ref: str = "develop",
            is_tag: str = "false",
            workflow_key: str = "validation.repository",
            profile: str = "test",
        ) -> dict[str, str]:
            with tempfile.TemporaryDirectory() as td:
                output = _prior.Path(td) / "github-output"
                output.write_text("", encoding="utf-8")
                result = subprocess.run(
                    ["bash", "-c", lane_script],
                    cwd=_prior.ROOT,
                    env={
                        **os.environ,
                        "SOURCE_REPOSITORY": repository,
                        "SOURCE_REF": ref,
                        "SOURCE_IS_TAG": is_tag,
                        "WORKFLOW_KEY": workflow_key,
                        "TEST_PROFILE": profile,
                        "GITHUB_OUTPUT": str(output),
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(
                    line.split("=", 1)
                    for line in output.read_text(encoding="utf-8").splitlines()
                )
                self.assertRegex(values["concurrency_key"], r"^[0-9a-f]{64}$")
                return values

        # Both the admission lane and GitHub job group must isolate cache/lint
        # work. A build or lint may replace only an older run of the same kind.
        warmup = resolve_lane(profile="build")
        lint = resolve_lane(profile="lint")
        self.assertNotEqual(warmup["concurrency_key"], lint["concurrency_key"])
        for profile, expected in (("build", warmup), ("lint", lint)):
            self.assertEqual(expected, resolve_lane(profile=profile))
            self.assertEqual(expected["cancel_in_progress"], "true")
            for acceptance in ("test", "full", "ui-test"):
                self.assertNotEqual(expected["concurrency_key"],
                                    resolve_lane(profile=acceptance)["concurrency_key"])
        branch_a = resolve_lane()
        duplicate_a = resolve_lane()
        branch_b = resolve_lane(ref="feature/new-head")
        different_profile = resolve_lane(profile="full")
        different_workflow = resolve_lane(workflow_key="validation.repository", profile="ui-test")
        tag = resolve_lane(ref="1.2.3", is_tag="true")
        physical = resolve_lane(workflow_key="validation.android", profile="physical-performance")
        device = resolve_lane(workflow_key="validation.repository", profile="device-test")
        inspector = resolve_lane(workflow_key="inspection.cluster", profile="flux")
        maintenance = resolve_lane(workflow_key="maintenance.branch-delete", profile="delete")
        publication = resolve_lane(workflow_key="source.checkpoint-publish", profile="publish")
        reproducibility = resolve_lane(workflow_key="oci.reproducibility", profile="prove")
        second_physical = resolve_lane(
            workflow_key="validation.repository", profile="device-test",
        )
        other_repository = resolve_lane(repository="StreamScapeTV/example-second")

        self.assertEqual(branch_a["concurrency_key"], duplicate_a["concurrency_key"])
        self.assertEqual(branch_a["cancel_in_progress"], "true")
        self.assertNotEqual(branch_a["concurrency_key"], branch_b["concurrency_key"])
        for val in (different_profile, different_workflow):
            self.assertEqual(branch_a["concurrency_key"], val["concurrency_key"])
            self.assertEqual(val["cancel_in_progress"], "true")
        for val in (tag, physical, device, inspector, maintenance, publication, reproducibility):
            self.assertNotEqual(branch_a["concurrency_key"], val["concurrency_key"])
            self.assertEqual(val["cancel_in_progress"], "false")
        self.assertNotEqual(physical["concurrency_key"], device["concurrency_key"])
        self.assertEqual(device["concurrency_key"], second_physical["concurrency_key"])
        self.assertNotEqual(branch_a["concurrency_key"], other_repository["concurrency_key"])
        self.assertNotEqual(maintenance["concurrency_key"], publication["concurrency_key"])
        self.assertNotEqual(maintenance["concurrency_key"], inspector["concurrency_key"])
        self.assertNotEqual(tag["concurrency_key"], branch_a["concurrency_key"])

        branch_delete = jobs["branch_delete"]["concurrency"]
        self.assertEqual(branch_delete["group"], "central-ci-maintenance-${{ inputs.active_key }}")
        self.assertFalse(branch_delete["cancel-in-progress"])

        self.assertNotIn("source_checkpoint_publish", jobs)
        self.assertNotIn("source_snapshot", jobs)

        settlement = jobs["settle_cancelled"]
        self.assertNotIn("concurrency", settlement)
        expected = {"request", *validation_jobs, *serialized_release_jobs, "ci_cancel", "branch_delete"}
        self.assertEqual(set(settlement["needs"]), expected)
        self.assertIn("always()", settlement["if"])
        self.assertIn("needs.request.result != 'success'", settlement["if"])
        for name in (*validation_jobs, *serialized_release_jobs, "ci_cancel", "branch_delete"):
            self.assertIn(f"needs.{name}.result == 'cancelled'", settlement["if"])
            self.assertIn(f"needs.{name}.result == 'failure'", settlement["if"])
            self.assertNotIn(f"needs.{name}.result != 'success'", settlement["if"])
            self.assertNotIn(f"needs.{name}.result == 'skipped'", settlement["if"])
        cancel = settlement["steps"][-1]
        self.assertEqual(cancel["with"]["phase"], "cancel-if-active")
        self.assertIn("contains(needs.*.result, 'cancelled')", cancel["with"]["status"])
        self.assertIn("execution was cancelled", cancel["with"]["error_summary"])
        self.assertIn("execution failed", cancel["with"]["error_summary"])
        self.assertIn("dispatch request failed", cancel["with"]["error_summary"])

    def test_retired_legacy_profile_pairs_fail_closed_with_repository_replacements(self) -> None:
        workflow = yaml.safe_load((_prior.ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        step = next(
            step for step in workflow["jobs"]["request"]["steps"]
            if step.get("name") == "Reject retired legacy validation profile pair"
        )
        condition = step["if"]
        script = step["run"]
        for workflow_key, profile, replacement in (
            ("validation.android", "full", "validation.repository/full"),
            ("validation.android", "test", "validation.repository/test"),
            ("validation.android", "build", "validation.repository/build"),
            ("validation.apple", "build", "validation.repository/build"),
            ("validation.apple", "test", "validation.repository/test"),
            ("validation.apple", "host", "validation.repository/full"),
            ("validation.python", "release-gates", "validation.repository/full"),
        ):
            self.assertIn(workflow_key, condition)
            self.assertIn(profile, condition)
            self.assertIn(replacement, script)

    def test_android_release_is_bounded_to_play_internal_draft(self) -> None:
        dispatch_path = _prior.ROOT / ".github/workflows/central-ci-dispatch.yml"
        dispatch = yaml.safe_load(dispatch_path.read_text())
        request_steps = dispatch["jobs"]["request"]["steps"]
        request_by_name = {step.get("name"): step for step in request_steps if step.get("name")}
        admission = request_by_name["Validate Android release request"]
        self.assertEqual(admission["if"], "${{ steps.claim.outputs.workflow_key == 'release.android' }}")
        script = admission["run"]
        self.assertIn("release.android supports only the play profile", script)
        self.assertIn('set(inputs) != {"build_number"}', script)
        self.assertIn(r'[1-9][0-9]{0,9}', script)
        self.assertIn("2100000000", script)
        for forbidden in ("track", "status", "userFraction", "production"):
            self.assertNotIn(f'inputs["{forbidden}"]', script)

        job = dispatch["jobs"]["android_release"]
        self.assertEqual(
            job["if"],
            "${{ needs.request.outputs.workflow_key == 'release.android' && needs.request.outputs.test_profile == 'play' }}",
        )
        self.assertEqual(job["uses"], "./.github/workflows/android.yml")
        self.assertEqual(job["with"]["source_is_tag"], "${{ needs.request.outputs.is_tag == 'true' }}")
        self.assertEqual(job["with"]["test_profile"], "play")
        self.assertEqual(job["with"]["release_version"], "${{ needs.request.outputs.release_version }}")
        self.assertEqual(
            job["with"]["build_number"],
            "${{ fromJSON(needs.request.outputs.inputs_json).build_number }}",
        )
        self.assertNotIn("track", job["with"])
        self.assertNotIn("status", job["with"])
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        self.assertEqual(
            job["concurrency"]["group"],
            "central-release-${{ needs.request.outputs.repository }}-${{ needs.request.outputs.workflow_key }}-${{ needs.request.outputs.test_profile }}",
        )

    def test_source_bundle_publish_is_retired(self) -> None:
        inventory = yaml.safe_load((_prior.ROOT / "INVENTORY.yaml").read_text())
        self.assertNotIn("source_bundle_publish", inventory["workflows"])
        self.assertNotIn("source_bundle_publish", inventory["scripts"])
        self.assertFalse((_prior.ROOT / ".github/workflows/source-bundle-publish.yml").exists())
        self.assertFalse((_prior.ROOT / "scripts/ci/source_bundle_publish.py").exists())
        self.assertFalse((_prior.ROOT / "tests/test_source_bundle_publish.py").exists())

        dispatch_text = (_prior.ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        dispatch = yaml.safe_load(dispatch_text)
        request_steps = dispatch["jobs"]["request"]["steps"]
        request_names = {step.get("name") for step in request_steps if step.get("name")}
        self.assertNotIn("Validate source bundle publication request", request_names)
        self.assertNotIn("source_bundle_publish", dispatch["jobs"])
        for retired in (
            "source.bundle-publish",
            "drive_bundle_file_id",
            "bundle_sha256",
            "source-bundle-publish.yml",
            "source_bundle_publish.py",
        ):
            self.assertNotIn(retired, dispatch_text)

    def test_agent_state_action_has_claim_start_observe_finish_lifecycle(self) -> None:
        path = _prior.ROOT / "actions/agent-state/action.yml"
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
        self.assertIn('p_patch:{status:"failed",terminal_reason:$terminal_reason,error_summary:$error_summary}', text)
        self.assertIn('cancellation_summary="${ERROR_SUMMARY:-${default_summary}}"', text)
        self.assertIn("already_terminal", text)
        self.assertIn("succeeded|failed) exit 0", text)
        self.assertIn('cancelled) agent_state_status=failed', text)
        self.assertNotIn("timed_out", text)
        self.assertIn('succeeded|failed) agent_state_status="${TERMINAL_STATUS}"', text)
        self.assertNotIn('p_patch:{status:"cancelled"}', text)
        self.assertIn("Agent State cancellation settlement failed", text)
        for name in ("error_summary", "diagnostic_key", "diagnostic_status"):
            self.assertIn(name, action["inputs"])
            self.assertFalse(action["inputs"][name]["required"])
        self.assertIn("error_summary:$error_summary", text)
        self.assertIn("diagnostic_key:$diagnostic_key", text)
        self.assertIn("diagnostic_status:$diagnostic_status", text)

    def test_agent_state_rpc_retries_only_bounded_transient_failures(self) -> None:
        text = (_prior.ROOT / "actions/agent-state/action.yml").read_text()
        self.assertIn("local max_attempts=4", text)
        self.assertIn("local retry_delays=(2 4 8)", text)
        self.assertIn("--connect-timeout 10", text)
        self.assertIn("--max-time 25", text)
        self.assertIn("408", text)
        self.assertIn("429", text)
        self.assertIn('[[ "${http_code}" =~ ^5[0-9][0-9]$ ]]', text)
        self.assertIn("5|6|7|18|28|35|52|55|56|92", text)
        self.assertIn("transient transport failure", text)
        self.assertIn("after %s attempt(s)", text)
        self.assertNotIn('[[ "${http_code}" =~ ^4[0-9][0-9]$ ]]', text)
        self.assertNotIn("--retry-all-errors", text)
        self.assertNotIn("--retry-connrefused", text)

    def test_persistent_dependency_cache_is_limited_to_remaining_legacy_native_lanes(self) -> None:
        cache_capable = ("apple", "android")
        for name in cache_capable:
            text = (_prior.ROOT / ".github/workflows" / f"{name}.yml").read_text()
            self.assertIn("actions/cache/restore@v4", text, name)
            self.assertIn("actions/cache/save@v4", text, name)

        apple = (_prior.ROOT / ".github/workflows/apple.yml").read_text()
        self.assertNotIn("dependency-cache", apple)
        self.assertNotIn("apple-develop-cache", apple)


    def test_android_dependency_cache_uses_github_default_branch_writer_policy(self) -> None:
        workflow = yaml.safe_load((_prior.ROOT / ".github/workflows/android.yml").read_text())
        steps = workflow["jobs"]["ci"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        scope = by_name["Resolve IPTV Android default-branch cache scope"]
        restore = by_name["Restore IPTV Android default-branch dependency cache"]
        self.assertIn("SOURCE_REPOSITORY", scope["env"])
        self.assertIn('test "${SOURCE_REPOSITORY}" = ', scope["run"])
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertNotIn("Save IPTV Android default-branch dependency cache", by_name)
        self.assertNotIn("actions/cache/save@v4", (_prior.ROOT / ".github/workflows/android.yml").read_text())

    def test_android_owner_profiles_and_gitops_retirement_are_explicit(self) -> None:
        android = (_prior.ROOT / ".github/workflows/android.yml").read_text()
        for kept in ("physical-performance", "run-android-play-release.sh"):
            self.assertIn(kept, android)
        for retired in (
            "screenshot-review", "targeted-tests", "targeted-unit", "search-performance", "test_filter",
            "test_platform", "room_schema",
        ):
            self.assertNotIn(retired, android)
        self.assertFalse((_prior.ROOT / ".github/workflows/gitops.yml").exists())

    def test_retired_legacy_profile_pairs_fail_closed_with_repository_replacements(self) -> None:
        workflow = yaml.safe_load((_prior.ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        step = next(
            step for step in workflow["jobs"]["request"]["steps"]
            if step.get("name") == "Validate retained legacy validation profile"
        )
        script = step["run"]
        for kept in (
            "validation.android/physical-performance",
        ):
            self.assertIn(kept, script)
        self.assertNotIn("validation.android/screenshot-review", script)
        for message in (
            "retired Android validation profile",
        ):
            self.assertIn(message, script)

    def test_persistent_dependency_cache_is_limited_to_remaining_legacy_native_lanes(self) -> None:
        apple = (_prior.ROOT / ".github/workflows/apple.yml").read_text()
        android = (_prior.ROOT / ".github/workflows/android.yml").read_text()
        self.assertIn("actions/cache/restore@v4", apple)
        self.assertIn("actions/cache/save@v4", apple)
        self.assertIn("actions/cache/restore@v4", android)
        self.assertNotIn("actions/cache/save@v4", android)

    def test_python_lane_is_retired_with_actionable_generic_replacement(self) -> None:
        root = _prior.ROOT
        workflow = yaml.safe_load((root / ".github/workflows/central-ci-dispatch.yml").read_text())
        self.assertNotIn("python", workflow["jobs"])
        self.assertNotIn("python", workflow["jobs"]["settle_cancelled"]["needs"])
        self.assertFalse((root / ".github/workflows/python.yml").exists())
        rejection = next(step for step in workflow["jobs"]["request"]["steps"]
                         if step.get("name") == "Reject retired legacy validation workflow")
        self.assertIn("validation.python", rejection["if"])
        result = subprocess.run(["bash", "-c", rejection["run"]], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("validation.repository", result.stderr)
        self.assertIn(".ci entrypoint", result.stderr)
