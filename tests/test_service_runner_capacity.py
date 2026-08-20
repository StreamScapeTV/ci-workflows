from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows import runner_images, runners  # noqa: E402


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
        self.assertTrue({"Docker", "Buildah", "Skopeo"} <= {word.rstrip(",") for item in self.service["forbidden_uses"] for word in item.split()})

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
            "git.faruqi.dev/mimranfaruqi/github-actions-runner-service",
        )

    def test_service_image_lifecycle_is_registered(self) -> None:
        self.assertEqual(self.validation_workflow.count("          - service"), 1)
        self.assertEqual(self.release_workflow.count("          - service"), 1)
        self.assertIn('directory: "/runner-images/service"', self.dependabot)
        self.assertIn('"runner-images/service/**"', self.validation_workflow)

    def test_exact_source_canary_proves_rootless_compose_and_cleanup(self) -> None:
        for expected in (
            "on:\n  workflow_dispatch:",
            "--api validation.service-compose",
            "--source-trust trusted-exact",
            "--profile service-small",
            "'[\"linux\",\"amd64\",\"service\",\"small\"]'",
            "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on) }}",
            'test "$(id -u)" -ne 0',
            "podman info --format '{{.Host.Security.Rootless}}'",
            'test "${STORAGE_DRIVER:-}" = vfs',
            "podman-compose -f compose.yml",
            "backend:",
            "client:",
            "down --volumes --remove-orphans",
            "podman ps -aq --filter",
            "podman pod ps -q --filter",
            "podman volume ls -q --filter",
        ):
            self.assertIn(expected, self.canary)
        self.assertNotIn("pull_request:", self.canary)
        self.assertNotIn("push:", self.canary)
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
