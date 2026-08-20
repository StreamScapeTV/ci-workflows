from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows import runner_images, runners, service_runner_smoke  # noqa: E402


class ServiceRunnerCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = runners.load_runner_contract(ROOT)
        cls.profiles = runners.profile_index(cls.contract)
        cls.service = cls.profiles["service-small"]
        cls.dockerfile = (ROOT / "runner-images/service/Dockerfile").read_text(encoding="utf-8")
        cls.smoke = (ROOT / "runner-images/service/smoke.sh").read_text(encoding="utf-8")
        cls.product = json.loads((ROOT / "runner-images/service/product.json").read_text(encoding="utf-8"))
        cls.lock = json.loads((ROOT / "runner-images/service/toolchain.lock.json").read_text(encoding="utf-8"))
        cls.canary = (ROOT / ".github/workflows/service-runner-smoke.yml").read_text(encoding="utf-8")
        cls.validation_workflow = (ROOT / ".github/workflows/runner-images-validation.yml").read_text(encoding="utf-8")
        cls.release_workflow = (ROOT / ".github/workflows/runner-images-release.yml").read_text(encoding="utf-8")
        cls.dependabot = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")

    def test_service_profile_is_fixed_pr_safe_semantic_capacity(self) -> None:
        self.assertEqual(self.service["internal_selectors"], [["linux", "amd64", "service", "small"]])
        self.assertEqual(self.service["allowed_workflow_apis"], ["validation.service-compose"])
        self.assertEqual(
            self.service["trust"]["allowed_source_trust"],
            ["trusted-pr", "trusted-exact"],
        )
        self.assertTrue(self.service["trust"]["executes_caller_source"])
        self.assertFalse(any(self.service["privilege"].values()))

    def test_untrusted_fork_is_rejected(self) -> None:
        with self.assertRaisesRegex(runners.RunnerContractError, "source-trust-not-allowed"):
            runners.resolve_runner_profile(
                self.contract,
                workflow_api="validation.service-compose",
                source_trust="untrusted-fork",
                requested_profile="service-small",
            )

    def test_service_profile_has_no_publication_authority(self) -> None:
        self.assertNotIn("oci.build", self.service["allowed_workflow_apis"])
        self.assertNotIn("oci.publish", self.service["allowed_workflow_apis"])
        tool_names = {tool["name"] for tool in self.service["tools"]}
        self.assertEqual(tool_names, {"github-actions-runner", "podman", "podman-compose"})
        forbidden_words = {
            word.rstrip(",")
            for item in self.service["forbidden_uses"]
            for word in item.split()
        }
        self.assertTrue({"Docker", "Buildah", "Skopeo"} <= forbidden_words)

    def test_general_small_remains_engine_free(self) -> None:
        general = self.profiles["general-small"]
        tool_names = " ".join(tool["name"].lower() for tool in general["tools"])
        for engine in ("docker", "podman", "buildah", "skopeo"):
            self.assertNotIn(engine, tool_names)
        self.assertIn("assuming a container engine is present", general["forbidden_uses"])

    def test_buildah_profiles_remain_privileged_exact_source_only(self) -> None:
        for profile_id in self.contract["buildah_escalation"]["order"]:
            profile = self.profiles[profile_id]
            with self.subTest(profile=profile_id):
                self.assertTrue(profile["privilege"]["privileged_container"])
                self.assertEqual(profile["trust"]["allowed_source_trust"], ["trusted-exact"])
                self.assertNotIn("validation.service-compose", profile["allowed_workflow_apis"])

    def test_service_image_is_rootless_daemonless_and_vfs(self) -> None:
        for expected in (
            "PODMAN_PACKAGE_VERSION=4.9.3+ds1-1ubuntu0.2",
            "PODMAN_COMPOSE_PACKAGE_VERSION=1.0.6-1",
            "STORAGE_DRIVER=vfs",
            "XDG_RUNTIME_DIR=/home/runner/.local/run",
            "runner:100000:65536",
            'driver = \"vfs\"',
            "USER runner",
        ):
            self.assertIn(expected, self.dockerfile)
        for forbidden in (
            "! command -v docker",
            "! command -v dockerd",
            "! command -v containerd",
            "! command -v buildah",
            "! command -v skopeo",
            "! test -e /var/run/docker.sock",
        ):
            self.assertIn(forbidden, self.dockerfile + self.smoke)

    def test_product_metadata_and_lock_agree(self) -> None:
        self.assertEqual(self.product["product_id"], "runner-service")
        self.assertEqual(self.lock["product_id"], self.product["product_id"])
        self.assertEqual(self.product["platform"], "linux/amd64")
        self.assertEqual(self.lock["platform"], self.product["platform"])
        self.assertEqual(self.lock["packages"]["podman"], "4.9.3+ds1-1ubuntu0.2")
        self.assertEqual(self.lock["packages"]["podman-compose"], "1.0.6-1")
        self.assertEqual(
            set(self.lock["forbidden_tools"]),
            {"docker", "dockerd", "containerd", "buildah", "skopeo"},
        )

    def test_service_image_is_in_central_release_matrix(self) -> None:
        self.assertIn("service", runner_images.release_matrix())
        plan = runner_images.build_plan(
            ROOT,
            image_id="service",
            source_sha="0123456789abcdef0123456789abcdef01234567",
        )
        self.assertEqual(plan.context_path, "runner-images/service")
        self.assertEqual(
            plan.registry_repository,
            "ghcr.io/streamscapetv/github-actions-runner-service",
        )

    def test_service_image_lifecycle_is_registered(self) -> None:
        self.assertEqual(self.validation_workflow.count("          - service"), 1)
        self.assertEqual(self.release_workflow.count("          - service"), 1)
        self.assertIn('directory: "/runner-images/service"', self.dependabot)
        self.assertIn('"runner-images/service/**"', self.validation_workflow)

    def test_named_compose_fixture_contains_two_services_and_owned_volume(self) -> None:
        document = service_runner_smoke.compose_document()
        self.assertIn("backend:", document)
        self.assertIn("client:", document)
        self.assertIn("depends_on:\n      - backend", document)
        self.assertIn("backend-data:/data", document)
        self.assertIn("volumes:\n  backend-data: {}", document)
        with tempfile.TemporaryDirectory() as directory:
            path = service_runner_smoke.write_compose_file(Path(directory) / "canary")
            self.assertEqual(path.read_text(encoding="utf-8"), document)
            self.assertFalse(path.is_symlink())

    def test_project_identity_is_bounded_to_service_canary_namespace(self) -> None:
        self.assertEqual(
            service_runner_smoke.validate_project_name("ciw-service-123-1"),
            "ciw-service-123-1",
        )
        for invalid in ("", "other-123", "ciw-service-a/b", "ciw-service-a b"):
            with self.subTest(value=invalid), self.assertRaises(
                service_runner_smoke.ServiceRunnerSmokeError
            ):
                service_runner_smoke.validate_project_name(invalid)

    def test_cleanup_removes_work_dir_after_zero_residue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "canary"
            service_runner_smoke.write_compose_file(work)
            with (
                patch.object(service_runner_smoke, "_run") as run,
                patch.object(
                    service_runner_smoke,
                    "_residual_resources",
                    return_value={"containers": "", "pods": "", "volumes": ""},
                ),
            ):
                service_runner_smoke.cleanup_smoke(
                    project_name="ciw-service-123-1",
                    work_dir=work,
                )
            run.assert_called_once()
            self.assertFalse(work.exists())

    def test_cleanup_fails_closed_when_owned_resources_remain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "canary"
            work.mkdir()
            with patch.object(
                service_runner_smoke,
                "_residual_resources",
                return_value={"containers": "abc", "pods": "", "volumes": ""},
            ):
                with self.assertRaisesRegex(
                    service_runner_smoke.ServiceRunnerSmokeError,
                    "left run-owned resources",
                ):
                    service_runner_smoke.cleanup_smoke(
                        project_name="ciw-service-123-1",
                        work_dir=work,
                    )
            self.assertTrue(work.exists())

    def test_exact_source_canary_is_thin_and_uses_trusted_planner_output(self) -> None:
        for expected in (
            "on:\n  workflow_dispatch:",
            "--api validation.service-compose",
            "--source-trust trusted-exact",
            "--profile service-small",
            "runs_on_json",
            "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            "scripts/ci/service_runner_smoke.py verify-runtime",
            "scripts/ci/service_runner_smoke.py run",
            "scripts/ci/service_runner_smoke.py cleanup",
            "name: Cleanup run-owned service canary state",
            "if: always()",
        ):
            self.assertIn(expected, self.canary)
        self.assertNotIn("pull_request:", self.canary)
        self.assertNotIn("push:", self.canary)
        self.assertNotIn("for i in", self.canary)
        self.assertNotIn("cat >", self.canary)
        for forbidden in (
            "actions/cache",
            "upload-artifact",
            "registry_username",
            "registry_token",
            "secrets: inherit",
            "buildah bud",
            "buildah push",
            "skopeo copy",
            "kubectl ",
            "flux ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.canary)


if __name__ == "__main__":
    unittest.main()
