from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixture_builder import CHECKOUT_SHA, create_repository, write_text
from ci_workflows.validation_harness import (
    discover_repository,
    load_actions_yaml,
    render_summary,
    validate_repository,
)

ROOT = Path(__file__).resolve().parents[1]


class ValidationHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        create_repository(self.root)

    def rules(self) -> set[str]:
        return {
            finding.rule
            for finding in validate_repository(
                self.root, include_public_api_validator=False
            ).findings
        }

    def test_valid_repository_passes_and_discovers_tests(self) -> None:
        result = validate_repository(
            self.root, include_public_api_validator=False
        )
        self.assertEqual((), result.findings)
        self.assertEqual(1, result.test_count)
        self.assertEqual(1, result.workflow_count)
        self.assertIn('"status": "passed"', render_summary(result))

    def test_yaml_loader_preserves_on_and_supports_yaml_extension(self) -> None:
        source = self.root / ".github/workflows/example.yaml"
        write_text(
            source,
            "name: Example\non:\n  push:\npermissions: {}\njobs: {}\n",
        )
        parsed = load_actions_yaml(source, self.root)
        self.assertIn("on", parsed.data)
        self.assertNotIn(True, parsed.data)
        inventory = discover_repository(self.root)
        self.assertIn(
            source.resolve(),
            tuple(path.resolve() for path in inventory.workflows),
        )

    def test_action_references_require_valid_syntax_not_global_pins(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        original = path.read_text(encoding="utf-8")
        path.write_text(
            original.replace(
                f"actions/checkout@{CHECKOUT_SHA} # v7.0.1",
                "actions/checkout@v7",
            ),
            encoding="utf-8",
        )
        rules = self.rules()
        self.assertNotIn("unpinned-action", rules)
        self.assertNotIn("missing-action-release-comment", rules)
        self.assertNotIn("unapproved-action", rules)

        path.write_text(
            original.replace(
                f"actions/checkout@{CHECKOUT_SHA} # v7.0.1",
                "not-a-valid-uses-reference",
            ),
            encoding="utf-8",
        )
        self.assertIn("invalid-action-reference", self.rules())

    def test_readability_rules_reject_opaque_oversized_and_complex_yaml(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text(encoding="utf-8")
        long_block = "\n".join(
            f"          echo line-{index}" for index in range(30)
        )
        text = text.replace(
            "  validate:\n    name: CI / Sample",
            "  V:\n    name: ''",
        )
        text = text.replace(
            "          set -Eeuo pipefail",
            "          function hidden() {\n            echo hidden\n          }\n"
            + long_block,
        )
        path.write_text(text, encoding="utf-8")
        rules = self.rules()
        self.assertIn("opaque-job-id", rules)
        self.assertIn("opaque-job-name", rules)
        self.assertIn("oversized-inline-run", rules)
        self.assertIn("complex-yaml-logic", rules)

    def test_runner_timeout_and_matrix_rules(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "runs-on: [linux, amd64, general, small]",
            "runs-on: [self-hosted, consumer-label]",
        )
        text = text.replace("    timeout-minutes: 20\n", "")
        text = text.replace(
            "    outputs:\n",
            "    strategy:\n      matrix: ${{ fromJSON(inputs.matrix) }}\n    outputs:\n",
        )
        path.write_text(text, encoding="utf-8")
        rules = self.rules()
        self.assertIn("bare-self-hosted", rules)
        self.assertIn("unknown-runner-profile", rules)
        self.assertIn("missing-timeout", rules)
        self.assertIn("opaque-matrix", rules)

    def test_runner_timeout_values_remain_positive_and_bounded(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        original = path.read_text(encoding="utf-8")
        cases = (
            ("0", "missing-timeout"),
            ("-1", "missing-timeout"),
            ('"20"', "missing-timeout"),
            ("true", "missing-timeout"),
            ("241", "excessive-timeout"),
        )
        for value, expected_rule in cases:
            with self.subTest(value=value):
                path.write_text(
                    original.replace(
                        "timeout-minutes: 20",
                        f"timeout-minutes: {value}",
                        1,
                    ),
                    encoding="utf-8",
                )
                self.assertIn(expected_rule, self.rules())
        path.write_text(original, encoding="utf-8")

    def test_permissions_secrets_and_high_risk_checkout_rules(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "on:\n  workflow_call:",
            "on:\n  pull_request_target:\n  workflow_call:",
        )
        text = text.replace(
            "permissions:\n  contents: read",
            "permissions: write-all",
        )
        text = text.replace(
            "    name: CI / Sample",
            "    name: CI / Sample\n    secrets: inherit",
        )
        path.write_text(text, encoding="utf-8")
        rules = self.rules()
        self.assertIn("implicit-permissions", rules)
        self.assertIn("secrets-inherit", rules)
        self.assertIn("untrusted-privileged-checkout", rules)
        self.assertIn("public-workflow-trigger", rules)

    def test_checkout_privacy_release_and_cleanup_rules_remain(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text(encoding="utf-8")
        text = text.replace("          clean: true\n", "")
        text = text.replace(
            "          persist-credentials: false",
            "          persist-credentials: true",
        )
        text = text.replace(
            '          test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"\n',
            "",
        )
        text += """
      - name: Publish latest
        run: |
          docker push example.invalid/app:latest
"""
        path.write_text(text, encoding="utf-8")
        rules = self.rules()
        self.assertIn("unclean-checkout", rules)
        self.assertIn("persisted-checkout-credentials", rules)
        self.assertIn("missing-exact-head-assertion", rules)
        self.assertIn("mutable-release-tag", rules)
        self.assertIn("missing-publication-readback", rules)
        self.assertIn("missing-always-cleanup", rules)
        self.assertNotIn("unregistered-artifact", rules)
        self.assertNotIn("excessive-artifact-retention", rules)

    def test_public_contract_shape_permissions_components_and_docs_drift(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text(encoding="utf-8")
        text = text.replace("      admitted_sha:", "      renamed_sha:")
        text = text.replace(
            "    outputs:\n      result:",
            "    secrets:\n      token:\n        required: true\n"
            "    outputs:\n      renamed_result:",
        )
        text = text.replace(
            "permissions:\n  contents: read",
            "permissions:\n  issues: write",
        )
        text = text.replace("    name: CI / Sample", "    name: Other check")
        path.write_text(text, encoding="utf-8")
        (self.root / "src/ci_workflows/sample.py").unlink()
        (self.root / "docs/validation.md").write_text("unrelated\n", encoding="utf-8")
        rules = self.rules()
        self.assertIn("workflow-input-drift", rules)
        self.assertIn("workflow-secret-drift", rules)
        self.assertIn("workflow-output-drift", rules)
        self.assertIn("workflow-permission-drift", rules)
        self.assertIn("stable-check-drift", rules)
        self.assertIn("missing-implementation-component", rules)
        self.assertIn("public-api-doc-drift", rules)

    def test_required_fixture_and_service_scenario_coverage(self) -> None:
        config = self.root / "contracts/validation-harness.json"
        payload = __import__("json").loads(config.read_text(encoding="utf-8"))
        payload["required_fixture_callers"] = ["missing.yml"]
        payload["required_event_fixtures"] = ["missing-event"]
        payload["required_service_scenarios"] = {"postgres": ["missing"]}
        config.write_text(
            __import__("json").dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        rules = self.rules()
        self.assertIn("missing-caller-fixture", rules)
        self.assertIn("missing-event-fixture", rules)
        self.assertIn("missing-service-fixture", rules)

    def test_private_reusable_helpers_use_main_without_central_clone(self) -> None:
        for name in (
            "reusable-node.yml",
            "reusable-python.yml",
            "reusable-apple.yml",
        ):
            source = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertNotIn("path: .ciw", source)
            self.assertNotRegex(
                source,
                r"StreamScapeTV/ci-workflows/actions/[^\s@]+@[0-9a-f]{40}",
            )
            for line in source.splitlines():
                if "uses: StreamScapeTV/ci-workflows/actions/" in line:
                    self.assertTrue(line.rstrip().endswith("@main"), line)

    def test_bootstrap_chart_digest_uses_verified_remote_manifest_bytes(self) -> None:
        source = (
            ROOT / ".github/workflows/reusable-tag-image-chart.yml"
        ).read_text(encoding="utf-8")
        manifest_fetch = source.index(
            'chart_manifest="${STATE_ROOT}/chart-remote-manifest.json"'
        )
        semantic_validation = source.index(
            'raise SystemExit("remote chart content layer digest differs")',
            manifest_fetch,
        )
        digest_line = source.index(
            'chart_digest="sha256:$(sha256sum "${chart_manifest}" | awk \'{print $1}\')"',
            semantic_validation,
        )
        self.assertLess(manifest_fetch, semantic_validation)
        self.assertLess(semantic_validation, digest_line)
        self.assertIn(
            '[[ "${chart_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]',
            source,
        )


if __name__ == "__main__":
    unittest.main()
