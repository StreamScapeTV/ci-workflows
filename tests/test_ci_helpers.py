from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_oci_helper():
    path = ROOT / "scripts/ci/oci_reproducibility.py"
    spec = importlib.util.spec_from_file_location("ciw_oci_reproducibility", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load OCI reproducibility helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OCI_HELPER = _load_oci_helper()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _put_blob(layout: Path, data: bytes) -> tuple[str, int]:
    digest = hashlib.sha256(data).hexdigest()
    path = layout / "blobs" / "sha256" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"sha256:{digest}", len(data)


def _write_test_oci_layout(
    layout: Path,
    *,
    missing_arm64: bool = False,
    raw_config_variant: bool = False
) -> None:
    layout.mkdir(parents=True, exist_ok=True)
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    manifest_descriptors = []
    platforms = [("amd64", None)]
    if not missing_arm64:
        platforms.append(("arm64", "v8"))
    for architecture, variant in platforms:
        platform_name = f"linux/{architecture}" + (f"/{variant}" if variant else "")
        config = {
            "architecture": architecture,
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [f"sha256:{'1' * 64}"]},
        }
        config_bytes = _json_bytes(config)
        if raw_config_variant and architecture == "amd64":
            config_bytes = (
                b'{"os":"linux","rootfs":{"diff_ids":["sha256:'
                + (b"1" * 64)
                + b'"],"type":"layers"},"architecture":"amd64"}'
            )
        config_digest, config_size = _put_blob(layout, config_bytes)
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": config_size,
            },
            "layers": [],
        }
        manifest_bytes = _json_bytes(manifest)
        manifest_digest, manifest_size = _put_blob(layout, manifest_bytes)
        platform = {"os": "linux", "architecture": architecture}
        if variant:
            platform["variant"] = variant
        manifest_descriptors.append(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": manifest_digest,
                "size": manifest_size,
                "platform": platform,
            }
        )
    nested_index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": manifest_descriptors,
    }
    nested_bytes = _json_bytes(nested_index)
    nested_digest, nested_size = _put_blob(layout, nested_bytes)
    top_index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "digest": nested_digest,
                "size": nested_size,
                "annotations": {"org.opencontainers.image.ref.name": "proof"},
            }
        ],
    }
    (layout / "index.json").write_bytes(_json_bytes(top_index))


class CiHelperTests(unittest.TestCase):
    def test_inventory_is_the_only_inventory_and_matches_the_small_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text())
        self.assertEqual(
            set(inventory["workflows"]),
            {"apple", "android", "python", "node", "flutter", "public_native_image_chart", "oci_reproducibility", "central_dispatch", "self_check", "runner_images"},
        )
        self.assertEqual(set(inventory["actions"]), {"agent_state", "google_drive", "private_git"})
        self.assertEqual(set(inventory["scripts"]), {"oci_reproducibility"})
        self.assertEqual(set(inventory["services"]), {"runner_images"})
        self.assertFalse((ROOT / "ci-broker").exists())
        self.assertFalse((ROOT / "PYTHON_INVENTORY.yml").exists())
        self.assertFalse((ROOT / "contracts").exists())

    def test_workflows_use_no_reusable_prefix(self) -> None:
        names = {p.name for p in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(len(names), 10)
        self.assertNotIn("broker.yml", names)
        self.assertFalse(any(name.startswith("reusable-") for name in names))
        for name in ("apple.yml", "android.yml", "python.yml", "node.yml", "flutter.yml"):
            self.assertIn(name, names)

    def test_only_three_custom_actions_exist(self) -> None:
        actions = {p.name for p in (ROOT / "actions").iterdir() if p.is_dir()}
        self.assertEqual(actions, {"agent-state", "google-drive", "private-git"})

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
        self.assertIn('[[ "${OBSERVED_SOURCE_SHA}" =~ ^[0-9A-Fa-f]{40}$ ]] || exit 2', text)
        self.assertIn("p_patch:{observed_source_sha:$sha}", text)
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

    def test_google_drive_resumable_upload_retries_only_bounded_transient_failures(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn("max_media_upload_attempts=4", text)
        self.assertIn("408|429|5??", text)
        self.assertIn("retryable_media_failure", text)
        self.assertIn("failed after bounded recovery attempts", text)
        self.assertIn('sleep "${media_attempt}"', text)

    def test_google_drive_resumable_upload_fails_permanent_http_errors(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn("Google Drive resumable media upload failed with HTTP ${media_http_code}", text)
        self.assertIn("Google Drive resumable upload status query failed with HTTP ${session_http_code}", text)
        self.assertNotIn("--retry-all-errors", text)

    def test_google_drive_resumable_upload_reconciles_ambiguous_completion(self) -> None:
        text = (ROOT / "actions/google-drive/action.yml").read_text()
        self.assertIn('echo "::add-mask::${session_url}"', text)
        self.assertIn('Content-Range: bytes */${byte_size}', text)
        self.assertIn("reconcile_session", text)
        self.assertIn('if test "$1" -ne 0; then', text)
        self.assertIn("308)", text)
        self.assertIn("received_offset", text)
        self.assertIn("Range header", text)
        self.assertIn('Content-Range: bytes ${offset}-$((byte_size - 1))/${byte_size}', text)

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
        plain_text_logs = {"android", "python", "node", "flutter"}
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

    def test_python_records_source_sha_and_uploads_readable_log_before_finish(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/python.yml").read_text())
        self.assertEqual(
            set(workflow["on"]["workflow_call"]["inputs"]),
            {"repository", "ref", "test_profile", "ci_run_id", "upload_private_log"},
        )
        steps = workflow["jobs"]["ci"]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        identity = by_name["Resolve observed source SHA"]
        observe = by_name["Record observed source SHA"]
        commands = by_name["Run fixed Python profile"]
        scrub = by_name["Scrub configured CI secrets from private log"]
        drive = by_name["Upload CI log to Google Drive"]
        finish = by_name["Finish Agent State run"]

        self.assertEqual(identity["if"], "${{ inputs.ci_run_id != '' }}")
        self.assertIn('source_sha="$(git rev-parse HEAD)"', identity["run"])
        self.assertNotIn("github.sha", identity["run"])
        self.assertEqual(observe["if"], "${{ inputs.ci_run_id != '' }}")
        self.assertEqual(observe["uses"], "StreamScapeTV/ci-workflows/actions/agent-state@main")
        self.assertEqual(observe["with"]["phase"], "observe-source")
        self.assertEqual(observe["with"]["observed_source_sha"], "${{ steps.source_identity.outputs.source_sha }}")
        for profile in ("compile)", "unit)", "release-gates)"):
            self.assertIn(profile, commands["run"])
        self.assertEqual(drive["with"]["file_name"], "${{ github.run_id }}-${{ github.run_attempt }}.txt")
        self.assertEqual(drive["with"]["mime_type"], "text/plain")
        self.assertNotIn("gzip", drive["with"])
        self.assertIn("steps.drive.outcome == 'success'", finish["with"]["status"])
        self.assertLess(names.index("Check out source"), names.index("Resolve observed source SHA"))
        self.assertLess(names.index("Resolve observed source SHA"), names.index("Record observed source SHA"))
        self.assertLess(names.index("Record observed source SHA"), names.index("Run fixed Python profile"))
        self.assertLess(names.index("Run fixed Python profile"), names.index("Scrub configured CI secrets from private log"))
        self.assertLess(names.index("Scrub configured CI secrets from private log"), names.index("Upload CI log to Google Drive"))
        self.assertLess(names.index("Upload CI log to Google Drive"), names.index("Finish Agent State run"))
        self.assertEqual(scrub["if"], "${{ always() }}")
        self.assertEqual(finish["if"], "${{ always() && inputs.ci_run_id != '' }}")

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

        workflow = yaml.safe_load(dispatch)
        steps = workflow["jobs"]["source_snapshot"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]
        identity = by_name["Resolve observed source SHA"]
        record = by_name["Record observed source SHA"]
        snapshot = by_name["Create exact tracked-source snapshot"]
        finish = by_name["Finish Agent State run"]
        self.assertIn('source_sha="$(git -C source rev-parse HEAD)"', identity["run"])
        self.assertEqual(record["with"]["phase"], "observe-source")
        self.assertEqual(record["with"]["observed_source_sha"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertNotIn("github.sha", record["with"]["observed_source_sha"])
        self.assertEqual(snapshot["env"]["OBSERVED_SOURCE_SHA"], "${{ steps.source_identity.outputs.source_sha }}")
        self.assertIn('source_sha="${OBSERVED_SOURCE_SHA}"', snapshot["run"])
        self.assertLess(names.index("Check out requested source"), names.index("Resolve observed source SHA"))
        self.assertLess(names.index("Resolve observed source SHA"), names.index("Record observed source SHA"))
        self.assertLess(names.index("Record observed source SHA"), names.index("Create exact tracked-source snapshot"))
        self.assertEqual(finish["if"], "${{ always() }}")

    def test_oci_reproducibility_workflow_is_fixed_isolated_and_nonpublishing(self) -> None:
        path = ROOT / ".github/workflows/oci-reproducibility.yml"
        workflow = yaml.safe_load(path.read_text())
        text = path.read_text()
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {"repository", "ref", "dockerfile_path", "build_context", "ci_run_id"},
        )
        for forbidden in ("command", "platform", "runner", "registry", "credential", "secret_name"):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(workflow["jobs"]["prove"]["runs-on"], "ubuntu-24.04")
        steps = workflow["jobs"]["prove"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]
        build = by_name["Build two isolated dual-platform OCI layouts"]["run"]
        cleanup = by_name["Clean all run-owned OCI state"]["run"]
        finish = by_name["Finish Agent State run"]["with"]["status"]
        self.assertIn("for build_id in a b", build)
        self.assertIn("linux/amd64 linux/arm64/v8", build)
        self.assertIn('--root "${state_root}/graphroot"', build)
        self.assertIn('--runroot "${state_root}/runroot"', build)
        self.assertIn("--storage-driver vfs", build)
        self.assertIn('export TMPDIR="${platform_root}/tmp"', build)
        self.assertIn('export XDG_CACHE_HOME="${platform_root}/xdg-cache"', build)
        self.assertIn("--layers=false", build)
        self.assertIn("--pull=always", build)
        self.assertIn("--timestamp", build)
        self.assertIn("org.opencontainers.image.revision=${SOURCE_SHA}", build)
        self.assertIn('"oci:${layout_root}:proof"', build)
        self.assertIn("raw dual-platform OCI config identity", by_name["Compare raw dual-platform OCI config identity"]["run"])
        self.assertIn('rm -rf "${proof_root}"', cleanup)
        self.assertIn('docker image rm --force "${QEMU_IMAGE_ID}"', cleanup)
        self.assertIn('test ! -e "${proof_root}"', cleanup)
        self.assertIn("phase: observe-source", text)
        self.assertIn("job.workflow_repository", text)
        self.assertIn("job.workflow_sha", text)
        self.assertIn("docker/setup-qemu-action@v4", text)
        self.assertIn("cache-image: false", text)
        self.assertIn("actions/google-drive@main", text)
        self.assertIn("mime_type: text/plain", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("FORGEJO_", text)
        self.assertNotIn("actions/private-git", text)
        self.assertNotIn("docker://", text)
        self.assertNotIn("ghcr.io", text)
        self.assertNotIn("git.faruqi.dev", text)
        self.assertLess(names.index("Compare raw dual-platform OCI config identity"), names.index("Clean all run-owned OCI state"))
        self.assertLess(names.index("Clean all run-owned OCI state"), names.index("Upload private reproducibility log to Google Drive"))
        self.assertLess(names.index("Upload private reproducibility log to Google Drive"), names.index("Finish Agent State run"))
        for required in ("steps.build.outcome == 'success'", "steps.compare.outcome == 'success'", "steps.cleanup.outcome == 'success'", "steps.drive.outcome == 'success'"):
            self.assertIn(required, finish)

    def test_oci_reproducibility_helper_accepts_identical_dual_platform_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            _write_test_oci_layout(left)
            _write_test_oci_layout(right)
            result = OCI_HELPER.compare_layouts(left, right)
            self.assertEqual(result["status"], "reproducible")
            self.assertEqual(result["platforms_expected"], ["linux/amd64", "linux/arm64/v8"])
            self.assertEqual(set(result["platforms"]), {"linux/amd64", "linux/arm64/v8"})
            for platform in result["platforms"].values():
                self.assertTrue(platform["config_bytes_identical"])
                self.assertTrue(platform["config_digest"].startswith("sha256:"))
                self.assertEqual(platform["config_digest"], platform["config_raw_sha256"])

    def test_oci_reproducibility_helper_rejects_raw_config_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            _write_test_oci_layout(left)
            _write_test_oci_layout(right, raw_config_variant=True)
            with self.assertRaisesRegex(OCI_HELPER.ReproducibilityError, "raw config bytes mismatch for linux/amd64"):
                OCI_HELPER.compare_layouts(left, right)

    def test_oci_reproducibility_helper_rejects_missing_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary) / "layout"
            _write_test_oci_layout(layout, missing_arm64=True)
            with self.assertRaisesRegex(OCI_HELPER.ReproducibilityError, "platform set mismatch"):
                OCI_HELPER.inspect_layout(layout)



if __name__ == "__main__":
    unittest.main()
