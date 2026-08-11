from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows import runners

ROOT = Path(__file__).resolve().parents[1]


class PythonRunnerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = runners.load_runner_contract(ROOT)
        cls.profiles = runners.profile_index(cls.contract)

    def test_python_binding_uses_only_reviewed_semantic_profiles(self) -> None:
        binding = runners.workflow_binding_index(self.contract)[
            "validation.python"
        ]
        self.assertEqual(binding["strategy"], "profile-contract")
        self.assertEqual(
            binding["profiles"],
            ["portable", "buildah-medium", "buildah-high"],
        )
        for profile_id in binding["profiles"]:
            self.assertIn(
                "validation.python",
                self.profiles[profile_id]["allowed_workflow_apis"],
            )

    def test_python_profiles_resolve_exact_internal_selectors(self) -> None:
        cases = (
            (
                "portable",
                "untrusted-fork",
                ["linux", "amd64", "general"],
            ),
            (
                "portable",
                "trusted-pr",
                ["linux", "amd64", "general"],
            ),
            (
                "buildah-medium",
                "trusted-exact",
                ["linux", "amd64", "buildah", "medium"],
            ),
            (
                "buildah-high",
                "trusted-exact",
                ["linux", "amd64", "buildah", "high"],
            ),
        )
        for profile, trust, expected in cases:
            with self.subTest(profile=profile, trust=trust):
                resolution = runners.resolve_runner_profile(
                    self.contract,
                    workflow_api="validation.python",
                    source_trust=trust,
                    requested_profile=profile,
                )
                self.assertEqual(list(resolution.runs_on), expected)

    def test_privileged_python_profiles_reject_pull_request_source(self) -> None:
        for profile in ("buildah-medium", "buildah-high"):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(
                    runners.RunnerContractError,
                    "source-trust-not-allowed",
                ):
                    runners.resolve_runner_profile(
                        self.contract,
                        workflow_api="validation.python",
                        source_trust="trusted-pr",
                        requested_profile=profile,
                    )

    def test_generated_mapping_and_compatibility_report_are_current(self) -> None:
        mapping = json.loads(
            (ROOT / "generated/runner-mappings.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            mapping["workflow_bindings"]["validation.python"],
            {
                "profiles": [
                    "portable",
                    "buildah-medium",
                    "buildah-high",
                ],
                "strategy": "profile-contract",
            },
        )
        report = (
            ROOT / "docs/inventory/runner-compatibility.md"
        ).read_text(encoding="utf-8")
        for repository, workflow in (
            ("StreamScapeTV/agent-state", ".github/workflows/test.yml"),
            ("StreamScapeTV/iptv-backend", ".github/workflows/backend-ci.yml"),
        ):
            self.assertIn(
                f"| `{repository}` | `{workflow}` | `python` | "
                "`portable`, `buildah-medium`, `buildah-high` |",
                report,
            )
        runners.write_generated_outputs(ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
