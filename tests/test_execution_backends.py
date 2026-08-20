from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.execution_backends import (
    ExecutionBackendError,
    resolve_execution_backend,
)


class ExecutionBackendTests(unittest.TestCase):
    def test_organization_preserves_exact_existing_selector(self) -> None:
        resolved = resolve_execution_backend(
            workflow_api="validation.node",
            execution_backend="organization",
            execution_profile="general-small",
            organization_runs_on=("linux", "amd64", "general", "small"),
        )
        self.assertEqual(resolved.execution_backend, "organization")
        self.assertEqual(resolved.execution_profile, "general-small")
        self.assertEqual(
            resolved.runs_on,
            ("linux", "amd64", "general", "small"),
        )
        self.assertEqual(
            resolved.as_dict()["runs_on_json"],
            '["linux","amd64","general","small"]',
        )

    def test_supported_portable_apis_map_to_fixed_ubuntu_latest(self) -> None:
        cases = (
            ("source.resolve", "general-tiny"),
            ("validation.node", "general-small"),
            ("validation.python", "general-small"),
        )
        for workflow_api, execution_profile in cases:
            with self.subTest(workflow_api=workflow_api):
                resolved = resolve_execution_backend(
                    workflow_api=workflow_api,
                    execution_backend="github-hosted",
                    execution_profile=execution_profile,
                    organization_runs_on=("linux", "amd64", "general", "small"),
                )
                self.assertEqual(resolved.runs_on, ("ubuntu-latest",))
                self.assertEqual(
                    resolved.as_dict()["runs_on_json"],
                    '["ubuntu-latest"]',
                )

    def test_python_buildah_profiles_are_not_silently_reinterpreted(self) -> None:
        for profile in ("buildah-medium", "buildah-high"):
            with self.subTest(profile=profile):
                with self.assertRaises(ExecutionBackendError) as raised:
                    resolve_execution_backend(
                        workflow_api="validation.python",
                        execution_backend="github-hosted",
                        execution_profile=profile,
                        organization_runs_on=("linux", "amd64", "buildah", "medium"),
                    )
                self.assertEqual(
                    raised.exception.code,
                    "unsupported_execution_backend_profile",
                )

    def test_unknown_backend_fails_closed(self) -> None:
        with self.assertRaises(ExecutionBackendError) as raised:
            resolve_execution_backend(
                workflow_api="validation.node",
                execution_backend="some-runner-label",
                execution_profile="general-small",
                organization_runs_on=("linux", "amd64", "general", "small"),
            )
        self.assertEqual(raised.exception.code, "invalid_execution_backend")

    def test_organization_selector_cannot_contain_self_hosted(self) -> None:
        with self.assertRaises(ExecutionBackendError) as raised:
            resolve_execution_backend(
                workflow_api="validation.node",
                execution_backend="organization",
                execution_profile="general-small",
                organization_runs_on=("self-hosted",),
            )
        self.assertEqual(raised.exception.code, "invalid_organization_runner")


if __name__ == "__main__":
    unittest.main()
