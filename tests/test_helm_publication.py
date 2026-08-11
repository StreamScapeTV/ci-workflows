from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_contract import (
    load_helm_contract,
    request_from_environment,
    resolve_validation_plan,
)
from ci_workflows.helm_execution import publish_and_read_back
from ci_workflows.helm_types import HelmPublicationResult, HelmValidationError, HelmValidationResult


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


class HelmPublicationTests(unittest.TestCase):
    def test_missing_version_is_pushed_once_then_read_back(self) -> None:
        contract = load_helm_contract(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(FIXTURE, source)
            state = root / "state"
            state.mkdir()
            plan = resolve_validation_plan(source, contract, request_from_environment(environment()))
            package = root / "package.tgz"
            package.write_bytes(b"synthetic-package")
            validation = HelmValidationResult("sha256:" + "b" * 64, "b" * 64, "{}", package)
            calls: list[list[str]] = []
            pulls = 0

            def fake_run(argv, *, cwd, environment, timeout, code, stdin=None, check=True):
                nonlocal pulls
                calls.append(list(argv))
                if argv[:2] == ["helm", "pull"]:
                    pulls += 1
                    if pulls == 1:
                        return subprocess.CompletedProcess(argv, 1, "", "manifest unknown")
                    destination = Path(argv[argv.index("--destination") + 1])
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(package, destination / "iptv-backend-1.2.3.tgz")
                return subprocess.CompletedProcess(argv, 0, "", "")

            runtime = {
                "PATH": "/usr/bin",
                "HOME": str(root),
                "INPUT_REGISTRY_USERNAME": "user",
                "INPUT_REGISTRY_TOKEN": "token",
            }
            with patch("ci_workflows.helm_execution._run", side_effect=fake_run), patch("ci_workflows.helm_execution.normalize_chart_archive", return_value="b" * 64):
                result = publish_and_read_back(source, state, plan, validation, runtime)
            self.assertTrue(result.published)
            self.assertEqual(sum(command[:2] == ["helm", "push"] for command in calls), 1)
            self.assertIn("oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend:1.2.3", result.immutable_references_json)

    def test_lookup_failure_never_becomes_a_publication_attempt(self) -> None:
        contract = load_helm_contract(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(FIXTURE, source)
            state = root / "state"
            state.mkdir()
            plan = resolve_validation_plan(source, contract, request_from_environment(environment()))
            package = root / "package.tgz"
            package.write_bytes(b"synthetic-package")
            validation = HelmValidationResult("sha256:" + "d" * 64, "d" * 64, "{}", package)
            calls: list[list[str]] = []

            def fake_run(argv, *, cwd, environment, timeout, code, stdin=None, check=True):
                calls.append(list(argv))
                if argv[:2] == ["helm", "pull"]:
                    return subprocess.CompletedProcess(argv, 1, "", "temporary network failure")
                return subprocess.CompletedProcess(argv, 0, "", "")

            runtime = {"PATH": "/usr/bin", "HOME": str(root), "INPUT_REGISTRY_USERNAME": "user", "INPUT_REGISTRY_TOKEN": "token"}
            with patch("ci_workflows.helm_execution._run", side_effect=fake_run):
                with self.assertRaisesRegex(HelmValidationError, "registry_lookup_failed"):
                    publish_and_read_back(source, state, plan, validation, runtime)
            self.assertFalse(any(command[:2] == ["helm", "push"] for command in calls))

    def test_missing_credential_and_noncanonical_version_fail_closed(self) -> None:
        contract = load_helm_contract(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(FIXTURE, source)
            state = root / "state"
            state.mkdir()
            request = request_from_environment(environment())
            plan = resolve_validation_plan(source, contract, request)
            validation = HelmValidationResult("sha256:" + "c" * 64, "c" * 64, "{}", root / "package.tgz")
            with self.assertRaisesRegex(HelmValidationError, "registry_auth_missing"):
                publish_and_read_back(source, state, plan, validation, {"PATH": "/usr/bin", "HOME": str(root)})
            invalid = environment()
            invalid["INPUT_RELEASE_VERSION"] = "latest"
            with self.assertRaisesRegex(HelmValidationError, "invalid_input"):
                request_from_environment(invalid)


if __name__ == "__main__":
    unittest.main()
