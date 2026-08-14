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
from ci_workflows.helm_registry import publish_and_read_back
from ci_workflows.helm_types import HelmValidationError, HelmValidationResult


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/helm-validation/backend"
SHA = "a" * 40


def _request_environment() -> dict[str, str]:
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


def _fixture(root: Path):
    source = root / "source"
    shutil.copytree(FIXTURE, source)
    state = root / "state"
    state.mkdir()
    plan = resolve_validation_plan(
        source,
        load_helm_contract(ROOT),
        request_from_environment(_request_environment()),
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
        "INPUT_RELEASE_MODE": "existing-tag",
    }
    return source, state, plan, validation, runtime


class HelmVerifyOnlyTests(unittest.TestCase):
    def test_manual_replay_missing_remote_never_pushes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state, plan, validation, runtime = _fixture(root)
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
                calls.append(list(argv))
                if argv[:2] == ["helm", "pull"]:
                    return subprocess.CompletedProcess(
                        argv,
                        1,
                        "",
                        "manifest unknown",
                    )
                return subprocess.CompletedProcess(argv, 0, "", "")

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

    def test_manual_replay_existing_identical_remote_is_read_back_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state, plan, validation, runtime = _fixture(root)
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
                calls.append(list(argv))
                if argv[:2] == ["helm", "pull"]:
                    destination = Path(argv[argv.index("--destination") + 1])
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(
                        validation.archive_path,
                        destination / "iptv-backend-1.2.3.tgz",
                    )
                return subprocess.CompletedProcess(argv, 0, "", "")

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

    def test_workflow_derives_manual_existing_tag_mode_and_propagates_it(self) -> None:
        workflow = (
            ROOT / ".github/workflows/reusable-helm-publish.yml"
        ).read_text(encoding="utf-8")
        action = (ROOT / "actions/publish-helm/action.yml").read_text(encoding="utf-8")
        registry = (ROOT / "src/ci_workflows/helm_registry.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("'existing-tag'", workflow)
        self.assertIn("release_mode: ${{ needs.plan.outputs.release_mode }}", workflow)
        self.assertIn("release_mode:", action)
        self.assertIn("INPUT_RELEASE_MODE", action)
        self.assertIn('allow_publish = release_mode == "tag-push"', registry)
        self.assertIn('require(allow_publish, "remote_version_missing")', registry)


if __name__ == "__main__":
    unittest.main()
