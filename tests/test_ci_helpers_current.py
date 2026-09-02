from __future__ import annotations

import subprocess

import yaml

from tests import test_ci_helpers as _prior


class CiHelperTests(_prior.CiHelperTests):
    """Current shared-helper contract while retaining the complete helper suite."""

    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((_prior.ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "android", "python", "node", "flutter", "maven", "streamscape_media_release", "streamscape_media_apple_binary", "container_service", "public_native_image_chart", "oci_reproducibility", "branch_delete", "source_snapshot_delete", "source_checkpoint_publish", "central_dispatch", "self_check", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive", "private_git"})
        self.assertEqual(set(inventory["scripts"]), {"oci_reproducibility", "source_snapshot_delete", "source_checkpoint_publish", "streamscape_media_release"})
        self.assertEqual(set(inventory["services"]), {"runner_images"})

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (_prior.ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 17)
        self.assertNotIn("broker.yml", names)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        self.assertIn("source-snapshot-delete.yml", names)
        self.assertNotIn("source-bundle-publish.yml", names)
        self.assertIn("source-checkpoint-publish.yml", names)
        self.assertIn("streamscape-media-release.yml", names)
        self.assertIn("streamscape-media-apple-binary.yml", names)

    def test_branch_delete_capability_is_bounded_and_fail_closed(self) -> None:
        super().test_branch_delete_capability_is_bounded_and_fail_closed()
        workflow = yaml.safe_load((_prior.ROOT / ".github/workflows/branch-delete.yml").read_text())
        steps = workflow["jobs"]["delete"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        delete_step = by_name["Delete exact eligible branch"]
        self.assertIn("X-Accepted-GitHub-Permissions", delete_step["run"])
        self.assertIn("accepted-permissions=", delete_step["run"])
        self.assertIn("private_rules_unavailable_message", delete_step["run"])
        self.assertIn("Upgrade to GitHub Pro or make this repository public to enable this feature.", delete_step["run"])
        self.assertIn('repository_value.get("private") is True', delete_step["run"])
        self.assertIn("allow_private_feature_unavailable=private_repository", delete_step["run"])
        cleanup = workflow["jobs"]["snapshot_cleanup"]
        self.assertEqual(cleanup["with"]["repository"], "${{ needs.delete.outputs.repository }}")
        self.assertEqual(cleanup["with"]["ref"], "${{ needs.delete.outputs.branch }}")

    def test_central_dispatch_preserves_newest_run_wins_with_snapshot_isolation(self) -> None:
        workflow = yaml.safe_load((_prior.ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        jobs = workflow["jobs"]
        execution_jobs = (
            "apple",
            "apple_release",
            "android",
            "android_release",
            "python",
            "node",
            "flutter",
            "maven",
            "container_service",
            "public_native_image_chart",
            "oci_reproducibility",
        )
        self.assertNotIn("concurrency", workflow)
        self.assertNotIn("concurrency", jobs["request"])
        for name in execution_jobs:
            self.assertEqual(jobs[name]["concurrency"]["group"], "central-ci-${{ inputs.active_key }}")
            self.assertTrue(jobs[name]["concurrency"]["cancel-in-progress"])

        branch_delete = jobs["branch_delete"]["concurrency"]
        self.assertEqual(branch_delete["group"], "central-ci-maintenance-${{ inputs.active_key }}")
        self.assertFalse(branch_delete["cancel-in-progress"])

        streamscape_release = jobs["streamscape_media_release"]["concurrency"]
        self.assertEqual(streamscape_release["group"], "central-ci-streamscape-media-${{ inputs.active_key }}")
        self.assertFalse(streamscape_release["cancel-in-progress"])

        checkpoint_publish = jobs["source_checkpoint_publish"]["concurrency"]
        self.assertEqual(checkpoint_publish["group"], "central-ci-source-checkpoint-publish-${{ inputs.active_key }}")
        self.assertFalse(checkpoint_publish["cancel-in-progress"])

        snapshot = jobs["source_snapshot"]["concurrency"]
        self.assertEqual(snapshot["group"], "central-ci-snapshot-${{ inputs.active_key }}")
        self.assertTrue(snapshot["cancel-in-progress"])
        self.assertNotEqual(snapshot["group"], jobs["apple"]["concurrency"]["group"])

        settlement = jobs["settle_cancelled"]
        self.assertNotIn("concurrency", settlement)
        expected = {"request", *execution_jobs, "streamscape_media_release", "branch_delete", "source_checkpoint_publish", "source_snapshot"}
        self.assertEqual(set(settlement["needs"]), expected)
        self.assertIn("always()", settlement["if"])
        self.assertIn("needs.request.result != 'success'", settlement["if"])
        for name in (*execution_jobs, "streamscape_media_release", "branch_delete", "source_checkpoint_publish", "source_snapshot"):
            self.assertIn(f"needs.{name}.result == 'cancelled'", settlement["if"])
        self.assertEqual(settlement["steps"][-1]["with"]["phase"], "cancel-if-active")

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
        self.assertEqual(job["with"]["test_profile"], "play")
        self.assertEqual(
            job["with"]["build_number"],
            "${{ fromJSON(needs.request.outputs.inputs_json).build_number }}",
        )
        self.assertNotIn("track", job["with"])
        self.assertNotIn("status", job["with"])
        self.assertTrue(job["concurrency"]["cancel-in-progress"])

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
        self.assertIn('p_patch:{status:"failed"}', text)
        self.assertIn("already_terminal", text)
        self.assertIn("succeeded|failed) exit 0", text)
        self.assertIn('cancelled) agent_state_status=failed', text)
        self.assertNotIn("timed_out", text)
        self.assertIn('succeeded|failed) agent_state_status="${TERMINAL_STATUS}"', text)
        self.assertNotIn('p_patch:{status:"cancelled"}', text)
        self.assertIn("Agent State cancellation settlement failed", text)
        self.assertNotIn("diagnostic_", text)

    def test_source_snapshot_reuses_drive_helper_and_updates_manifest_in_place(self) -> None:
        dispatch = (_prior.ROOT / ".github/workflows/central-ci-dispatch.yml").read_text()
        agents = (_prior.ROOT / "AGENTS.md").read_text()
        self.assertIn("workflow_key == 'source.snapshot'", dispatch)
        self.assertIn("git -C source archive --format=zip", dispatch)
        self.assertIn("GOOGLE_DRIVE_REPOSITORIES_FOLDER_ID", dispatch)
        self.assertIn("Resolve durable Drive repository folder", dispatch)
        self.assertIn("Google Drive repository folder ID", dispatch)
        self.assertEqual(dispatch.count("repository_folder_id: ${{ steps.drive_repository.outputs.folder_id }}"), 3)
        self.assertIn("Google Drive repository folder ID: `1--JcV6RK8jdIIP3ONWw420QDVpNTQ7L8`", agents)
        self.assertIn("file_name: ${{ steps.snapshot.outputs.archive_filename }}", dispatch)
        self.assertIn("previous_file_name: source.zip", dispatch)
        self.assertIn('ref_slug = urllib.parse.quote(ref, safe="")', dispatch)
        self.assertIn('f"{repository_name}-{ref_slug}.zip"', dispatch)
        self.assertEqual(dispatch.count("file_name: manifest.json"), 2)
        for key in (
            '"repository_name"',
            '"archive_format": "zip"',
            '"archive_format_version": 1',
            '"archive_filename"',
            '"archive_sha256"',
            '"archive_size_bytes"',
            '"resolved_source_sha"',
            '"tree_sha"',
            '"source_zip_sha256"',
            '"source_zip_size_bytes"',
            '"manifest_file_id"',
            '"archive_file_id"',
            '"source_zip_file_id"',
            '"folder_id"',
        ):
            self.assertIn(key, dispatch)
        self.assertIn('test "${CREATED_ID}" = "${UPDATED_ID}"', dispatch)
        self.assertIn("Clean snapshot workspace", dispatch)

        workflow = yaml.safe_load(dispatch)
        steps = workflow["jobs"]["source_snapshot"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]
        identity = by_name["Resolve observed source SHA"]
        record = by_name["Record observed source SHA"]
        resume = by_name["Protect unpublished canonical Drive checkpoint"]
        snapshot = by_name["Create exact tracked-source snapshot"]
        upload = by_name["Upload repository snapshot archive"]
        finish = by_name["Finish Agent State run"]
        self.assertIn('source_sha="$(git -C source rev-parse HEAD)"', identity["run"])
        self.assertIn("HEAD^{tree}", identity["run"])
        self.assertIn("source_checkpoint_publish.py resume-action", resume["run"])
        self.assertEqual(resume["env"]["OBSERVED_TREE_SHA"], "${{ steps.source_identity.outputs.tree_sha }}")
        self.assertEqual(snapshot["if"], "${{ steps.checkpoint_resume.outputs.action != 'preserve' }}")
        self.assertEqual(upload["if"], "${{ steps.checkpoint_resume.outputs.action != 'preserve' }}")
        self.assertEqual(record["with"]["phase"], "observe-source")
        self.assertEqual(record["with"]["observed_source_sha"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertNotIn("github.sha", record["with"]["observed_source_sha"])
        self.assertEqual(snapshot["env"]["OBSERVED_SOURCE_SHA"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertIn('source_sha="${OBSERVED_SOURCE_SHA}"', snapshot["run"])
        self.assertIn('ref_slug = urllib.parse.quote(ref, safe="")', snapshot["run"])
        self.assertIn('archive_filename=%s\\n', snapshot["run"])
        self.assertEqual(upload["with"]["file_name"], "${{ steps.snapshot.outputs.archive_filename }}")
        self.assertEqual(upload["with"]["previous_file_name"], "source.zip")
        self.assertLess(names.index("Check out requested source"), names.index("Resolve observed source SHA"))
        self.assertLess(names.index("Resolve observed source SHA"), names.index("Record observed source SHA"))
        self.assertLess(names.index("Record observed source SHA"), names.index("Create exact tracked-source snapshot"))
        self.assertEqual(finish["if"], "${{ always() }}")

    def test_snapshot_archive_migration_reuses_legacy_file_and_refuses_ambiguous_siblings(self) -> None:
        action = yaml.safe_load((_prior.ROOT / "actions/google-drive/action.yml").read_text())
        self.assertEqual(action["inputs"]["previous_file_name"]["default"], "")
        script = action["runs"]["steps"][0]["run"]
        lines = script.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == "drive_list_url() {")
        end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line.strip() == "merge_folder_candidates() {")
        functions = "\n".join(lines[start:end])

        def run(current: str, previous: str) -> subprocess.CompletedProcess[str]:
            harness = f'''set -Eeuo pipefail
{functions}
access_token=masked
target_folder_id=folder
DRIVE_FILE_NAME=repo-main.zip
DRIVE_PREVIOUS_FILE_NAME=source.zip
CURRENT={current!r}
PREVIOUS={previous!r}
curl() {{
  url="${{@: -1}}"
  case "$url" in
    *repo-main.zip*) printf '%s' "$CURRENT" ;;
    *source.zip*) printf '%s' "$PREVIOUS" ;;
    *) return 97 ;;
  esac
}}
resolve_existing_file
'''
            return subprocess.run(
                ["bash", "-c", harness],
                cwd=_prior.ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        legacy_only = run('{"files":[]}', '{"files":[{"id":"legacy-id"}]}')
        self.assertEqual(legacy_only.returncode, 0, legacy_only.stderr)
        self.assertEqual(legacy_only.stdout, "legacy-id")

        ambiguous = run(
            '{"files":[{"id":"current-id"}]}',
            '{"files":[{"id":"legacy-id"}]}',
        )
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("refusing ambiguous migration", ambiguous.stderr)
