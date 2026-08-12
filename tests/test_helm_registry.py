from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import helm
from ci_workflows.helm_contract import (
    load_helm_contract,
    request_from_environment,
    resolve_validation_plan,
)
from ci_workflows.helm_registry import publish_and_read_back
from ci_workflows.helm_types import HelmValidationError, HelmValidationResult


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/helm-validation/backend"
SHA = "a" * 40


def environment() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "StreamScapeTV/iptv-backend",
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_PRODUCT_ID": "iptv-backend-chart",
        "INPUT_RELEASE_VERSION": "1.2.3",
        "INPUT_VALUES_PROFILE": "default",
        "INPUT_POLICY_PATH": "",
        "INPUT_ARTIFACT_EXCEPTION_ID": "",
        "INPUT_SOURCE_TRUST": "trusted-exact",
    }


class HelmRegistryPublicationTests(unittest.TestCase):
    def plan_and_validation(self, root: Path):
        contract = load_helm_contract(ROOT)
        source = root / "source"
        shutil.copytree(FIXTURE, source)
        state = root / "state"
        state.mkdir()
        plan = resolve_validation_plan(
            source,
            contract,
            request_from_environment(environment()),
        )
        package = root / "package.tgz"
        package.write_bytes(b"synthetic-package")
        validation = HelmValidationResult(
            "sha256:" + "b" * 64,
            "b" * 64,
            "{}",
            package,
        )
        runtime = {
            "PATH": "/usr/bin",
            "HOME": str(root),
            "INPUT_REGISTRY_USERNAME": "user",
            "INPUT_REGISTRY_TOKEN": "token",
            "INPUT_RELEASE_MODE": "tag-push",
        }
        return source, state, plan, validation, runtime

    @staticmethod
    def write_remote(argv: list[str], validation: HelmValidationResult) -> None:
        destination = Path(argv[argv.index("--destination") + 1])
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            validation.archive_path,
            destination / "iptv-backend-1.2.3.tgz",
        )

    def test_registry_error_codes_are_the_only_absence_that_can_push(self) -> None:
        for marker in (
            "MANIFEST_UNKNOWN: manifest unknown",
            "NAME_UNKNOWN: repository name not known to registry",
        ):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, state, plan, validation, runtime = self.plan_and_validation(root)
                calls: list[list[str]] = []
                pulls = 0

                def fake_run(
                    argv,
                    *,
                    cwd,
                    environment,
                    timeout,
                    code,
                    stdin=None,
                    check=True,
                ):
                    nonlocal pulls
                    command = list(argv)
                    calls.append(command)
                    if command[:2] == ["helm", "pull"]:
                        pulls += 1
                        if pulls == 1:
                            return subprocess.CompletedProcess(command, 1, "", marker)
                        self.write_remote(command, validation)
                    return subprocess.CompletedProcess(command, 0, "", "")

                with (
                    patch("ci_workflows.helm_registry._run", side_effect=fake_run),
                    patch(
                        "ci_workflows.helm_registry.normalize_chart_archive",
                        return_value="b" * 64,
                    ),
                ):
                    result = publish_and_read_back(
                        source,
                        state,
                        plan,
                        validation,
                        runtime,
                    )
                self.assertTrue(result.published)
                self.assertEqual(
                    sum(command[:2] == ["helm", "push"] for command in calls),
                    1,
                )

    def test_generic_proxy_or_auth_404_never_becomes_a_push(self) -> None:
        for message in (
            "failed to authorize: GET https://git.faruqi.dev/oauth/token: 404 Not Found",
            "authentication failed: username unknown",
            "temporary registry lookup returned 404 not found",
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, state, plan, validation, runtime = self.plan_and_validation(root)
                calls: list[list[str]] = []

                def fake_run(
                    argv,
                    *,
                    cwd,
                    environment,
                    timeout,
                    code,
                    stdin=None,
                    check=True,
                ):
                    command = list(argv)
                    calls.append(command)
                    if command[:2] == ["helm", "pull"]:
                        return subprocess.CompletedProcess(command, 1, "", message)
                    return subprocess.CompletedProcess(command, 0, "", "")

                with patch("ci_workflows.helm_registry._run", side_effect=fake_run):
                    with self.assertRaisesRegex(
                        HelmValidationError,
                        "registry_lookup_failed",
                    ):
                        publish_and_read_back(
                            source,
                            state,
                            plan,
                            validation,
                            runtime,
                        )
                self.assertFalse(
                    any(command[:2] == ["helm", "push"] for command in calls)
                )

    def test_existing_tag_missing_version_is_read_back_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state, plan, validation, runtime = self.plan_and_validation(root)
            runtime["INPUT_RELEASE_MODE"] = "existing-tag"
            calls: list[list[str]] = []

            def fake_run(
                argv,
                *,
                cwd,
                environment,
                timeout,
                code,
                stdin=None,
                check=True,
            ):
                command = list(argv)
                calls.append(command)
                if command[:2] == ["helm", "pull"]:
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        "",
                        "MANIFEST_UNKNOWN: manifest unknown",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("ci_workflows.helm_registry._run", side_effect=fake_run):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_version_missing",
                ):
                    publish_and_read_back(
                        source,
                        state,
                        plan,
                        validation,
                        runtime,
                    )
            self.assertFalse(
                any(command[:2] == ["helm", "push"] for command in calls)
            )

    def test_existing_identical_version_is_verified_without_push(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state, plan, validation, runtime = self.plan_and_validation(root)
            runtime["INPUT_RELEASE_MODE"] = "existing-tag"
            calls: list[list[str]] = []

            def fake_run(
                argv,
                *,
                cwd,
                environment,
                timeout,
                code,
                stdin=None,
                check=True,
            ):
                command = list(argv)
                calls.append(command)
                if command[:2] == ["helm", "pull"]:
                    self.write_remote(command, validation)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("ci_workflows.helm_registry._run", side_effect=fake_run),
                patch(
                    "ci_workflows.helm_registry.normalize_chart_archive",
                    return_value="b" * 64,
                ),
            ):
                result = publish_and_read_back(
                    source,
                    state,
                    plan,
                    validation,
                    runtime,
                )
            self.assertFalse(result.published)
            self.assertFalse(
                any(command[:2] == ["helm", "push"] for command in calls)
            )

    def test_named_helm_facade_and_release_adapter_use_hardened_publication_path(self) -> None:
        self.assertIs(helm.publish_and_read_back, publish_and_read_back)
        script = (ROOT / "scripts/ci/helm_release.py").read_text(encoding="utf-8")
        self.assertIn("from ci_workflows.helm import publish as publish_chart", script)
        self.assertNotIn(
            "from ci_workflows.helm_registry import publish_and_read_back",
            script,
        )
        facade = (ROOT / "src/ci_workflows/helm.py").read_text(encoding="utf-8")
        self.assertIn("from .helm_registry import publish_and_read_back", facade)
        self.assertIn("from .helm_manifest import remote_chart_manifest_digest", facade)
        contract = (ROOT / "contracts/helm-publication.json").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '"remote_absence_classifier": "docker-distribution-manifest-or-name-unknown-only"',
            contract,
        )


if __name__ == "__main__":
    unittest.main()
