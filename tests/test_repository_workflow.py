from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/repository.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
INVENTORY = ROOT / "INVENTORY.yaml"


class RepositoryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")
        cls.dispatch_text = DISPATCH.read_text(encoding="utf-8")

    def test_reusable_api_has_only_bounded_semantic_inputs(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {
                "repository",
                "ref",
                "source_is_tag",
                "operation",
                "platform",
                "ui_suite",
                "build_number",
                "release_kind",
                "release_authorized",
                "ci_run_id",
                "upload_private_log",
            },
        )
        for forbidden in (
            "command",
            "script",
            "script_path",
            "runner",
            "runner_label",
            "timeout",
            "environment",
            "env",
            "secret_name",
            "workspace",
            "scheme",
            "gradle_task",
            "xcodebuild_args",
        ):
            self.assertNotIn(forbidden, inputs)

        execute = self.workflow["jobs"]["execute"]
        self.assertEqual(
            execute["runs-on"],
            "${{ inputs.platform == 'linux' && 'ubuntu-24.04' || 'macos-latest' }}",
        )
        self.assertEqual(execute["timeout-minutes"], 300)

    def test_fixed_entrypoints_are_the_only_executable_mapping(self) -> None:
        by_name = {
            step.get("name"): step
            for step in self.workflow["jobs"]["execute"]["steps"]
            if step.get("name")
        }
        request = by_name["Validate bounded repository operation"]["run"]
        for mapping in (
            "build) entrypoint=.ci/build.sh",
            "test) entrypoint=.ci/test.sh",
            "full) entrypoint=.ci/test-full.sh",
            "ui-test) entrypoint=.ci/test-ui.sh",
            "entrypoint=.ci/release.sh",
        ):
            self.assertIn(mapping, request)
        self.assertIn("repository release requires a reviewed StreamScapeTV/ci-workflows main caller", request)
        self.assertIn('test "${CALLER_REPOSITORY}" = StreamScapeTV/ci-workflows', request)
        self.assertIn('test "${CALLER_REF}" = refs/heads/main', request)
        self.assertIn("repository release requires a separately authorized Central caller", request)
        self.assertIn("repository release requires an admitted immutable tag source", request)
        self.assertIn("repository release supports only the prepare semantic kind", request)
        self.assertIn("linux|macos|ios|tvos", request)
        self.assertIn('""|smoke|full-ordinary', request)

        execute = by_name["Execute fixed repository-owned entrypoint"]["run"]
        self.assertIn('"./${ENTRYPOINT}" "${args[@]}"', execute)
        for product_command in (
            "xcodebuild",
            "gradlew",
            "./gradlew",
            "pytest",
            "mvn ",
            "swift test",
            "streamscapetvTests",
            "streamscapetv.xcworkspace",
        ):
            self.assertNotIn(product_command, execute)

    def test_entrypoint_is_tracked_regular_executable_and_cannot_escape_checkout(self) -> None:
        step = next(
            step
            for step in self.workflow["jobs"]["execute"]["steps"]
            if step.get("name") == "Verify exact source and fixed tracked entrypoint"
        )
        script = step["run"]
        self.assertIn("git -C source ls-files --error-unmatch", script)
        self.assertIn("test \"${mode}\" = 100755", script)
        self.assertIn('test -f "${path}" && test ! -L "${path}" && test -x "${path}"', script)
        self.assertIn("Path(sys.argv[1]).resolve(strict=True)", script)
        self.assertIn("os.path.commonpath", script)
        self.assertIn("fixed repository entrypoint escapes the exact checkout", script)
        self.assertIn('test -z "$(git -C source status --porcelain --untracked-files=all)"', script)

    def test_registry_auth_is_a_central_fixed_capability_not_caller_configuration(self) -> None:
        by_name = {
            step.get("name"): step
            for step in self.workflow["jobs"]["execute"]["steps"]
            if step.get("name")
        }
        capability = by_name["Resolve reviewed registry capability class"]["run"]
        self.assertIn("StreamScapeTV/iptv-apple) capability=private-swiftpm", capability)
        self.assertIn("StreamScapeTV/iptv-android) capability=private-maven", capability)
        self.assertIn("*) capability=none", capability)

        connect = by_name["Connect approved private registry network"]
        self.assertEqual(connect["uses"], "StreamScapeTV/ci-workflows/actions/private-git@main")
        auth = by_name["Configure ephemeral approved registry authentication"]["run"]
        self.assertIn("machine github.com", auth)
        self.assertIn("machine git.faruqi.dev", auth)
        self.assertIn("github.com)", auth)
        self.assertIn("git.faruqi.dev)", auth)
        self.assertIn("credential.helper", auth)
        self.assertIn('dirname "${BASH_SOURCE[0]}"', auth)
        self.assertNotIn("CENTRAL_REGISTRY_AUTH_ROOT:?", auth)
        self.assertIn("CIW_MAVEN_PACKAGE_READ_TOKEN", auth)
        execute_env = by_name["Execute fixed repository-owned entrypoint"]["env"]
        self.assertNotIn("AUTH_ROOT", execute_env)
        self.assertNotIn("CENTRAL_REGISTRY_AUTH_ROOT", str(execute_env))
        self.assertNotIn("inputs.registry", self.workflow_text)
        self.assertNotIn("inputs.secret", self.workflow_text)
        self.assertNotIn("inputs.host", self.workflow_text)

    def test_central_owns_log_paths_scrubbing_transport_and_cleanup(self) -> None:
        by_name = {
            step.get("name"): step
            for step in self.workflow["jobs"]["execute"]["steps"]
            if step.get("name")
        }
        init = by_name["Initialize private execution paths"]["run"]
        for name in ("CI_LOG_DIR", "CI_ARTIFACT_DIR", "CI_PROGRESS_FILE"):
            self.assertIn(name, init)
        execute_env = by_name["Execute fixed repository-owned entrypoint"]["env"]
        self.assertNotIn("GOOGLE_DRIVE_CLIENT_ID", execute_env)
        self.assertNotIn("GOOGLE_DRIVE_REFRESH_TOKEN", execute_env)
        scrub = by_name["Scrub configured CI secrets from private text evidence"]
        self.assertEqual(scrub["if"], "${{ always() }}")
        self.assertIn('("CI_LOG_DIR", "CI_ARTIFACT_DIR")', scrub["run"])
        package = by_name["Package bounded repository CI evidence"]
        self.assertIn("evidence exceeds 256 files", package["run"])
        self.assertIn("evidence exceeds 64 MiB", package["run"])
        self.assertIn("must not contain symlinks", package["run"])
        upload = by_name["Upload private repository CI log to Google Drive"]
        self.assertEqual(upload["uses"], "StreamScapeTV/ci-workflows/actions/google-drive@main")
        self.assertEqual(upload["with"]["mime_type"], "text/plain")
        evidence_upload = by_name["Upload bounded repository CI evidence to Google Drive"]
        self.assertEqual(evidence_upload["uses"], "StreamScapeTV/ci-workflows/actions/google-drive@main")
        self.assertEqual(evidence_upload["with"]["mime_type"], "application/zip")
        cleanup = by_name["Cleanup ephemeral registry and repository evidence"]
        self.assertEqual(cleanup["if"], "${{ always() }}")
        self.assertIn("central-registry-auth", cleanup["run"])

    def test_agent_state_validation_route_cannot_authorize_release_or_inject_execution_detail(self) -> None:
        request_steps = self.dispatch["jobs"]["request"]["steps"]
        by_name = {step.get("name"): step for step in request_steps if step.get("name")}
        admission = by_name["Validate generic repository-owned CI request"]
        self.assertEqual(admission["if"], "${{ steps.claim.outputs.workflow_key == 'validation.repository' }}")
        script = admission["run"]
        self.assertIn('profile not in {"build", "test", "full", "ui-test"}', script)
        self.assertIn('allowed = {"platform", "ui_suite"} if profile == "ui-test" else {"platform"}', script)
        self.assertIn('{"linux", "macos", "ios", "tvos"}', script)
        self.assertNotIn("release", script.split("PY_VALIDATE_REPOSITORY", 1)[0])

        job = self.dispatch["jobs"]["repository"]
        self.assertEqual(job["if"], "${{ needs.request.outputs.workflow_key == 'validation.repository' }}")
        self.assertEqual(job["uses"], "./.github/workflows/repository.yml")
        self.assertEqual(
            set(job["with"]),
            {"repository", "ref", "source_is_tag", "operation", "platform", "ui_suite", "ci_run_id"},
        )
        self.assertNotIn("release_authorized", job["with"])
        self.assertNotIn("build_number", job["with"])
        self.assertNotIn("release_kind", job["with"])
        for forbidden in ("command", "script", "runner", "timeout", "environment", "secret_name"):
            self.assertNotIn(forbidden, str(job["with"]))

    def test_non_migrated_product_workflows_remain_selected_by_existing_lanes(self) -> None:
        jobs = self.dispatch["jobs"]
        self.assertEqual(jobs["apple"]["uses"], "./.github/workflows/apple.yml")
        self.assertEqual(jobs["android"]["uses"], "./.github/workflows/android.yml")
        self.assertEqual(jobs["maven"]["uses"], "./.github/workflows/maven.yml")
        self.assertIn("repository", jobs["settle_cancelled"]["needs"])
        self.assertIn("needs.repository.result == 'cancelled'", jobs["settle_cancelled"]["if"])

    def test_inventory_exposes_one_generic_repository_workflow(self) -> None:
        inventory = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
        self.assertEqual(inventory["workflows"]["repository"], ".github/workflows/repository.yml")
        self.assertFalse((ROOT / ".github/workflows/repository-build.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/repository-test.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/repository-release.yml").exists())


if __name__ == "__main__":
    unittest.main()
