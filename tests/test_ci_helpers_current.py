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
            {"apple", "android", "python", "node", "flutter", "maven", "container_service", "public_native_image_chart", "oci_reproducibility", "branch_delete", "source_snapshot_delete", "central_dispatch", "self_check", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive", "private_git"})
        self.assertEqual(set(inventory["scripts"]), {"oci_reproducibility", "source_snapshot_delete"})
        self.assertEqual(set(inventory["services"]), {"runner_images"})

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (_prior.ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 14)
        self.assertNotIn("broker.yml", names)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        self.assertIn("source-snapshot-delete.yml", names)

    def test_branch_delete_capability_is_bounded_and_fail_closed(self) -> None:
        workflow = yaml.safe_load((_prior.ROOT / ".github/workflows/branch-delete.yml").read_text())
        call = workflow["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {"repository", "branch", "expected_head", "ci_run_id"})
        self.assertNotIn("workflow_dispatch", workflow["on"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        delete = workflow["jobs"]["delete"]
        steps = delete["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        validate = by_name["Validate bounded branch deletion inputs"]
        token = by_name["Create exact target repository token"]
        delete_step = by_name["Delete exact eligible branch"]
        self.assertIn('branch in {"main", "develop"}', validate["run"])
        self.assertEqual(token["with"]["permission-contents"], "write")
        self.assertEqual(token["with"]["permission-metadata"], "read")
        self.assertIn("repository live default branch", delete_step["run"])
        self.assertIn('branch_value.get("protected") is not False', delete_step["run"])
        self.assertIn('rule.get("type") == "deletion"', delete_step["run"])
        self.assertIn('label="branch rules"', delete_step["run"])
        self.assertIn("GitHub branch maintenance {label} was refused", delete_step["run"])
        self.assertIn("X-Accepted-GitHub-Permissions", delete_step["run"])
        self.assertIn("accepted-permissions=", delete_step["run"])
        self.assertIn("private_rules_unavailable_message", delete_step["run"])
        self.assertIn("Upgrade to GitHub Pro or make this repository public to enable this feature.", delete_step["run"])
        self.assertIn('repository_value.get("private") is True', delete_step["run"])
        self.assertIn("allow_private_feature_unavailable=private_repository", delete_step["run"])
        self.assertIn("branch_was_present=false", delete_step["run"])
        self.assertIn("branch_was_present=true", delete_step["run"])
        cleanup = workflow["jobs"]["snapshot_cleanup"]
        self.assertEqual(cleanup["uses"], "./.github/workflows/source-snapshot-delete.yml")
        self.assertEqual(cleanup["with"]["ref"], "${{ inputs.branch }}")
        self.assertEqual(cleanup["with"]["expected_source_sha"], "")
        finish = workflow["jobs"]["finish"]
        self.assertEqual(finish["if"], "${{ always() }}")
        self.assertIn("needs.snapshot_cleanup.result == 'success'", finish["steps"][0]["with"]["status"])

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
        snapshot = by_name["Create exact tracked-source snapshot"]
        upload = by_name["Upload repository snapshot archive"]
        finish = by_name["Finish Agent State run"]
        self.assertIn('source_sha="$(git -C source rev-parse HEAD)"', identity["run"])
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
