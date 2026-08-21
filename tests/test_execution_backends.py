from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.execution_backends import ExecutionBackendError, resolve_execution_backend
from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]


class ExecutionBackendTests(unittest.TestCase):
    def test_contract_has_only_default_and_fixed_hosted_selector(self) -> None:
        contract = json.loads((ROOT / "contracts/runner-execution-backends.json").read_text())
        self.assertEqual(contract["default_backend"], "organization")
        self.assertEqual(contract["allowed_backends"], ["organization", "github-hosted"])
        self.assertEqual(contract["github-hosted"]["runs_on"], ["ubuntu-latest"])

    def test_organization_preserves_existing_selector_exactly(self) -> None:
        resolved = resolve_execution_backend(
            execution_backend="organization",
            execution_profile="general-small",
            organization_runs_on=("linux", "amd64", "general", "small"),
        )
        self.assertEqual(resolved.runs_on, ("linux", "amd64", "general", "small"))

    def test_hosted_maps_only_portable_general_profiles_to_ubuntu_latest(self) -> None:
        for profile in ("general-tiny", "general-small"):
            with self.subTest(profile=profile):
                resolved = resolve_execution_backend(
                    execution_backend="github-hosted",
                    execution_profile=profile,
                    organization_runs_on=("linux", "amd64", "general", "small"),
                )
                self.assertEqual(resolved.runs_on, ("ubuntu-latest",))
                self.assertEqual(resolved.as_dict()["runs_on_json"], '["ubuntu-latest"]')

    def test_hosted_rejects_python_buildah_profiles_instead_of_switching_engines(self) -> None:
        for profile in ("buildah-medium", "buildah-high"):
            with self.subTest(profile=profile), self.assertRaises(ExecutionBackendError) as raised:
                resolve_execution_backend(
                    execution_backend="github-hosted",
                    execution_profile=profile,
                    organization_runs_on=("linux", "amd64", "buildah", "medium"),
                )
            self.assertEqual(raised.exception.code, "unsupported_execution_backend_profile")

    def test_unknown_backend_and_unsafe_organization_selector_fail_closed(self) -> None:
        with self.assertRaises(ExecutionBackendError):
            resolve_execution_backend(
                execution_backend="anything-else",
                execution_profile="general-small",
                organization_runs_on=("linux", "amd64", "general", "small"),
            )
        with self.assertRaises(ExecutionBackendError):
            resolve_execution_backend(
                execution_backend="organization",
                execution_profile="general-small",
                organization_runs_on=("self-hosted",),
            )

    def test_only_selected_reusable_workflows_expose_optional_backend(self) -> None:
        for filename in ("reusable-resolve-source.yml", "reusable-node.yml", "reusable-python.yml"):
            workflow = yaml.load(
                (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                Loader=ActionsLoader,
            )
            backend = workflow["on"]["workflow_call"]["inputs"]["execution_backend"]
            self.assertFalse(backend["required"])
            self.assertEqual(backend["default"], "organization")
            self.assertEqual(backend["type"], "string")

    def test_portable_backend_planners_never_require_organization_capacity(self) -> None:
        for filename in ("reusable-resolve-source.yml", "reusable-node.yml", "reusable-python.yml"):
            with self.subTest(filename=filename):
                workflow = yaml.load(
                    (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                    Loader=ActionsLoader,
                )
                self.assertEqual(workflow["jobs"]["plan"]["runs-on"], ["ubuntu-latest"])

    def test_execution_jobs_consume_only_central_planner_output(self) -> None:
        source = yaml.load(
            (ROOT / ".github/workflows/reusable-resolve-source.yml").read_text(encoding="utf-8"),
            Loader=ActionsLoader,
        )
        self.assertEqual(source["jobs"]["admit"]["runs-on"], "${{ fromJSON(needs.plan.outputs.runs_on_json) }}")
        for filename in ("reusable-node.yml", "reusable-python.yml"):
            workflow = yaml.load(
                (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                Loader=ActionsLoader,
            )
            self.assertEqual(workflow["jobs"]["validate"]["runs-on"], "${{ fromJSON(needs.plan.outputs.runs_on_json) }}")


if __name__ == "__main__":
    unittest.main()
