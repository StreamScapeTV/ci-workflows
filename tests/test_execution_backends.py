from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.execution_backends import ExecutionBackendError, resolve_execution_backend
from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]


class ExecutionBackendTests(unittest.TestCase):
    def test_contract_has_only_default_and_fixed_hosted_selectors(self) -> None:
        contract = json.loads((ROOT / "contracts/runner-execution-backends.json").read_text())
        self.assertEqual(contract["default_backend"], "organization")
        self.assertEqual(contract["allowed_backends"], ["organization", "github-hosted"])
        self.assertEqual(contract["github-hosted"]["runs_on"], ["ubuntu-latest"])
        self.assertEqual(
            contract["github-hosted"]["profile_runs_on"],
            {
                "general-tiny": ["ubuntu-latest"],
                "general-small": ["ubuntu-latest"],
                "mobile": ["ubuntu-latest"],
                "service-small": ["ubuntu-latest"],
                "apple": ["macos-latest"],
            },
        )

    def test_organization_preserves_existing_selector_exactly(self) -> None:
        resolved = resolve_execution_backend(
            execution_backend="organization",
            execution_profile="general-small",
            organization_runs_on=("linux", "amd64", "general", "small"),
        )
        self.assertEqual(resolved.runs_on, ("linux", "amd64", "general", "small"))

    def test_hosted_maps_reviewed_profiles_to_fixed_hosted_selectors(self) -> None:
        cases = {
            "general-tiny": ("ubuntu-latest",),
            "general-small": ("ubuntu-latest",),
            "mobile": ("ubuntu-latest",),
            "service-small": ("ubuntu-latest",),
            "apple": ("macos-latest",),
        }
        for profile, expected in cases.items():
            with self.subTest(profile=profile):
                if profile == "apple":
                    organization = ("macOS", "ARM64")
                elif profile == "mobile":
                    organization = ("linux", "amd64", "mobile")
                elif profile == "service-small":
                    organization = ("linux", "amd64", "service", "small")
                else:
                    organization = ("linux", "amd64", "general", "small")
                resolved = resolve_execution_backend(
                    execution_backend="github-hosted",
                    execution_profile=profile,
                    organization_runs_on=organization,
                )
                self.assertEqual(resolved.runs_on, expected)
                self.assertEqual(
                    resolved.as_dict()["runs_on_json"],
                    json.dumps(list(expected), separators=(",", ":")),
                )

    def test_hosted_rejects_unproven_specialized_profiles_instead_of_falling_back(self) -> None:
        selectors = {
            "buildah-medium": ("linux", "amd64", "buildah", "medium"),
            "buildah-high": ("linux", "amd64", "buildah", "high"),
            "flux-control": ("linux", "amd64", "flux-control"),
        }
        for profile, selector in selectors.items():
            with self.subTest(profile=profile), self.assertRaises(ExecutionBackendError) as raised:
                resolve_execution_backend(
                    execution_backend="github-hosted",
                    execution_profile=profile,
                    organization_runs_on=selector,
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

    def test_portable_reusable_workflows_expose_optional_backend(self) -> None:
        for filename in (
            "reusable-resolve-source.yml",
            "reusable-node.yml",
            "reusable-python.yml",
            "reusable-gitops-validation.yml",
            "reusable-script.yml",
            "reusable-android.yml",
        ):
            with self.subTest(filename=filename):
                workflow = yaml.load(
                    (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                    Loader=ActionsLoader,
                )
                backend = workflow["on"]["workflow_call"]["inputs"]["execution_backend"]
                self.assertFalse(backend["required"])
                self.assertEqual(backend["default"], "organization")
                self.assertEqual(backend["type"], "string")

    def test_source_planners_are_backend_aware_after_issue_449(self) -> None:
        source = yaml.load(
            (ROOT / ".github/workflows/reusable-resolve-source.yml").read_text(encoding="utf-8"),
            Loader=ActionsLoader,
        )
        hosted = source["jobs"]["plan"]
        organization = source["jobs"]["plan_organization"]
        self.assertEqual(hosted["runs-on"], ["ubuntu-latest"])
        self.assertEqual(hosted["if"], "${{ inputs.execution_backend == 'github-hosted' }}")
        self.assertEqual(organization["runs-on"], ["linux", "amd64", "general", "tiny"])
        self.assertEqual(
            organization["if"],
            "${{ inputs.execution_backend != 'github-hosted' }}",
        )

    def test_node_python_planners_are_backend_aware_after_issue_454(self) -> None:
        for filename in ("reusable-node.yml", "reusable-python.yml"):
            with self.subTest(filename=filename):
                workflow = yaml.load(
                    (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                    Loader=ActionsLoader,
                )
                hosted = workflow["jobs"]["plan"]
                organization = workflow["jobs"]["plan_organization"]
                self.assertEqual(hosted["runs-on"], ["ubuntu-latest"])
                self.assertEqual(
                    hosted["if"],
                    "${{ inputs.execution_backend == 'github-hosted' }}",
                )
                self.assertEqual(
                    organization["runs-on"],
                    ["linux", "amd64", "general", "small"],
                )
                self.assertEqual(
                    organization["if"],
                    "${{ inputs.execution_backend != 'github-hosted' }}",
                )

    def test_new_portable_families_have_backend_aware_planners(self) -> None:
        expected_organization = {
            "reusable-gitops-validation.yml": ["linux", "amd64", "general", "small"],
            "reusable-script.yml": ["linux", "amd64", "general", "small"],
            "reusable-android.yml": ["linux", "amd64", "mobile"],
        }
        for filename, organization_selector in expected_organization.items():
            with self.subTest(filename=filename):
                workflow = yaml.load(
                    (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                    Loader=ActionsLoader,
                )
                hosted = workflow["jobs"]["plan"]
                organization = workflow["jobs"]["plan_organization"]
                self.assertEqual(hosted["runs-on"], ["ubuntu-latest"])
                self.assertEqual(hosted["if"], "${{ inputs.execution_backend == 'github-hosted' }}")
                self.assertEqual(organization["runs-on"], organization_selector)
                self.assertEqual(
                    organization["if"],
                    "${{ inputs.execution_backend != 'github-hosted' }}",
                )

    def test_execution_jobs_consume_only_successful_planner_output(self) -> None:
        source = yaml.load(
            (ROOT / ".github/workflows/reusable-resolve-source.yml").read_text(encoding="utf-8"),
            Loader=ActionsLoader,
        )
        source_admit = source["jobs"]["admit"]
        self.assertEqual(source_admit["needs"], ["plan", "plan_organization"])
        self.assertEqual(
            source_admit["if"],
            "${{ always() && (needs.plan.result == 'success' || needs.plan_organization.result == 'success') }}",
        )
        self.assertEqual(
            source_admit["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
        )

        for filename in (
            "reusable-node.yml",
            "reusable-python.yml",
            "reusable-gitops-validation.yml",
            "reusable-script.yml",
            "reusable-android.yml",
        ):
            with self.subTest(filename=filename):
                workflow = yaml.load(
                    (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"),
                    Loader=ActionsLoader,
                )
                execute = workflow["jobs"]["validate"]
                self.assertEqual(execute["needs"], ["plan", "plan_organization"])
                self.assertEqual(
                    execute["if"],
                    "${{ always() && (needs.plan.result == 'success' || needs.plan_organization.result == 'success') }}",
                )
                self.assertEqual(
                    execute["runs-on"],
                    "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
                )


if __name__ == "__main__":
    unittest.main()
