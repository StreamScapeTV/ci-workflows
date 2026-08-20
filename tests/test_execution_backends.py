from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.execution_backends import (
    ExecutionBackendError,
    resolve_execution_backend,
)
from ci_workflows.validation_model import ActionsLoader


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
        selectors = {
            "buildah-medium": ("linux", "amd64", "buildah", "medium"),
            "buildah-high": ("linux", "amd64", "buildah", "high"),
        }
        for profile, selector in selectors.items():
            with self.subTest(profile=profile):
                with self.assertRaises(ExecutionBackendError) as raised:
                    resolve_execution_backend(
                        workflow_api="validation.python",
                        execution_backend="github-hosted",
                        execution_profile=profile,
                        organization_runs_on=selector,
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

    def test_selected_reusable_workflows_expose_only_bounded_backend_intent(self) -> None:
        expected = {
            "reusable-resolve-source.yml": ("plan", "admit"),
            "reusable-node.yml": ("plan", "validate"),
            "reusable-python.yml": ("plan", "validate"),
        }
        for filename, jobs in expected.items():
            with self.subTest(filename=filename):
                path = ROOT / ".github/workflows" / filename
                source = path.read_text(encoding="utf-8")
                workflow = yaml.load(source, Loader=ActionsLoader)
                inputs = workflow["on"]["workflow_call"]["inputs"]
                backend = inputs["execution_backend"]
                self.assertFalse(backend["required"])
                self.assertEqual(backend["default"], "organization")
                self.assertEqual(backend["type"], "string")
                self.assertEqual(tuple(workflow["jobs"]), jobs)
                self.assertNotIn("self-hosted", source)
                for forbidden in (
                    "runner_labels:",
                    "runs_on:",
                    "runner: ${{ inputs.",
                    "runs-on: ${{ inputs.execution_backend }}",
                ):
                    self.assertNotIn(forbidden, source)

    def test_source_admission_consumes_backend_plan_output(self) -> None:
        path = ROOT / ".github/workflows/reusable-resolve-source.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
        plan = workflow["jobs"]["plan"]
        admit = workflow["jobs"]["admit"]
        self.assertEqual(plan["runs-on"], ["linux", "amd64", "general", "tiny"])
        self.assertEqual(admit["needs"], "plan")
        self.assertEqual(
            admit["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
        )
        backend = plan["steps"][0]
        self.assertEqual(backend["with"]["workflow_api"], "source.resolve")
        self.assertEqual(
            backend["with"]["execution_backend"],
            "${{ inputs.execution_backend }}",
        )

    def test_node_and_python_execution_jobs_consume_exact_planner_selector(self) -> None:
        for filename in ("reusable-node.yml", "reusable-python.yml"):
            with self.subTest(filename=filename):
                path = ROOT / ".github/workflows" / filename
                workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
                plan = workflow["jobs"]["plan"]
                validate = workflow["jobs"]["validate"]
                self.assertEqual(
                    plan["runs-on"], ["linux", "amd64", "general", "small"]
                )
                self.assertEqual(
                    validate["runs-on"],
                    "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
                )
                planner = plan["steps"][0]
                self.assertEqual(
                    planner["with"]["execution_backend"],
                    "${{ inputs.execution_backend }}",
                )


if __name__ == "__main__":
    unittest.main()
