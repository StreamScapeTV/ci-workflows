from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.validation_model import (  # noqa: E402
    Finding,
    HarnessConfig,
    load_harness_config,
)
from ci_workflows.validation_policy import _validate_runner  # noqa: E402


class ValidationRunnerSelectorTests(unittest.TestCase):
    def test_harness_loads_exact_contract_owned_selector_arrays(self) -> None:
        config = load_harness_config(ROOT)
        self.assertIn(
            ("linux", "amd64", "general"),
            config.allowed_runner_selectors,
        )
        self.assertIn(
            ("linux", "amd64", "mobile"),
            config.allowed_runner_selectors,
        )
        self.assertIn(
            ("macOS", "ARM64"),
            config.allowed_runner_selectors,
        )
        self.assertNotIn(
            ("self-hosted", "macOS"),
            config.allowed_runner_selectors,
        )
        self.assertNotIn(("linux",), config.allowed_runner_selectors)
        self.assertNotIn(
            ("linux", "amd64"),
            config.allowed_runner_selectors,
        )

    def test_complete_capability_array_passes(self) -> None:
        config = load_harness_config(ROOT)
        findings: list[Finding] = []
        for selector in (
            ["linux", "amd64", "general"],
            ["linux", "amd64", "buildah", "high"],
            ["macOS", "ARM64"],
        ):
            with self.subTest(selector=selector):
                _validate_runner(
                    selector,
                    ".github/workflows/example.yml",
                    config,
                    findings,
                )
        self.assertEqual([], findings)

    def test_partial_and_mixed_capability_arrays_fail_closed(self) -> None:
        config = load_harness_config(ROOT)
        for selector in (
            ["linux"],
            ["linux", "amd64"],
            ["linux", "amd64", "general", "mobile"],
            ["linux", "amd64", "portable"],
        ):
            with self.subTest(selector=selector):
                findings: list[Finding] = []
                _validate_runner(
                    selector,
                    ".github/workflows/example.yml",
                    config,
                    findings,
                )
                self.assertTrue(findings)
                self.assertTrue(
                    all(
                        finding.rule == "unknown-runner-profile"
                        for finding in findings
                    )
                )

    def test_direct_semantic_profiles_fail_closed(self) -> None:
        config = HarnessConfig(
            max_inline_run_lines=40,
            max_matrix_jobs=16,
            max_timeout_minutes=240,
            allowed_runner_profiles=frozenset({"portable", "buildah-high"}),
            required_fixture_callers=frozenset(),
            required_event_fixtures=frozenset(),
            required_service_scenarios={},
            exceptions={},
            allowed_runner_selectors=frozenset(
                {("linux", "amd64", "general")}
            ),
        )
        for selector in (
            "portable",
            "buildah-high",
        ):
            with self.subTest(selector=selector):
                findings: list[Finding] = []
                _validate_runner(
                    selector,
                    ".github/workflows/example.yml",
                    config,
                    findings,
                )
                self.assertEqual(
                    ["semantic-runner-direct"],
                    [finding.rule for finding in findings],
                )

    def test_trusted_planner_output_remains_supported(self) -> None:
        config = HarnessConfig(
            max_inline_run_lines=40,
            max_matrix_jobs=16,
            max_timeout_minutes=240,
            allowed_runner_profiles=frozenset({"portable"}),
            required_fixture_callers=frozenset(),
            required_event_fixtures=frozenset(),
            required_service_scenarios={},
            exceptions={},
            allowed_runner_selectors=frozenset(
                {("linux", "amd64", "general")}
            ),
        )
        for selector in (
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
        ):
            with self.subTest(selector=selector):
                findings: list[Finding] = []
                _validate_runner(
                    selector,
                    ".github/workflows/example.yml",
                    config,
                    findings,
                )
                self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
