from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixture_builder import (
    CHECKOUT_SHA,
    create_caller,
    create_repository,
    valid_workflow,
    write_json,
    write_text,
)
from ci_workflows.validation_harness import (
    discover_repository,
    load_actions_yaml,
    render_summary,
    validate_repository,
)


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
        self.assertIn(source, inventory.workflows)

    def test_action_pin_release_comment_and_allowlist_fail_closed(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text()
        path.write_text(text.replace(CHECKOUT_SHA + " # v7.0.1", "main"))
        rules = self.rules()
        self.assertIn("unpinned-action", rules)
        self.assertIn("missing-action-release-comment", rules)
        path.write_text(
            text.replace(
                "actions/checkout@" + CHECKOUT_SHA + " # v7.0.1",
                "other/action@" + "b" * 40 + " # v1",
            )
        )
        self.assertIn("unapproved-action", self.rules())

    def test_readability_rules_reject_opaque_oversized_and_complex_yaml(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text()
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
        path.write_text(text)
        rules = self.rules()
        self.assertIn("opaque-job-id", rules)
        self.assertIn("opaque-job-name", rules)
        self.assertIn("oversized-inline-run", rules)
        self.assertIn("complex-yaml-logic", rules)

    def test_runner_timeout_and_matrix_rules(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text()
        text = text.replace(
            "runs-on: portable",
            "runs-on: [self-hosted, consumer-label]",
        )
        text = text.replace("    timeout-minutes: 20\n", "")
        text = text.replace(
            "    outputs:\n",
            "    strategy:\n      matrix: ${{ fromJSON(inputs.matrix) }}\n    outputs:\n",
        )
        path.write_text(text)
        rules = self.rules()
        self.assertIn("bare-self-hosted", rules)
        self.assertIn("unknown-runner-profile", rules)
        self.assertIn("missing-timeout", rules)
        self.assertIn("opaque-matrix", rules)

    def test_permissions_secrets_and_high_risk_checkout_rules(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text()
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
        path.write_text(text)
        rules = self.rules()
        self.assertIn("implicit-permissions", rules)
        self.assertIn("secrets-inherit", rules)
        self.assertIn("untrusted-privileged-checkout", rules)
        self.assertIn("public-workflow-trigger", rules)

    def test_checkout_source_artifact_release_and_cleanup_rules(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text()
        text = text.replace("          clean: true\n", "")
        text = text.replace(
            "          persist-credentials: false",
            "          persist-credentials: true",
        )
        text = text.replace(
            "          ref: ${{ inputs.admitted_sha }}",
            "          ref: main",
        )
        text = text.replace(
            '          test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"\n',
            "",
        )
        text += f"""
      - name: Upload result
        uses: actions/upload-artifact@{'b' * 40} # v4
        with:
          name: credentials
          path: auth.json
          retention-days: 30
      - name: Publish latest
        run: |
          docker push example.invalid/app:latest
"""
        path.write_text(text)
        rules = self.rules()
        self.assertIn("unclean-checkout", rules)
        self.assertIn("persisted-checkout-credentials", rules)
        self.assertIn("missing-exact-head-assertion", rules)
        self.assertIn("unregistered-artifact", rules)
        self.assertIn("excessive-artifact-retention", rules)
        self.assertIn("mutable-release-tag", rules)
        self.assertIn("missing-publication-readback", rules)
        self.assertIn("missing-always-cleanup", rules)

    def test_public_contract_shape_permissions_components_and_docs_drift(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text()
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
        path.write_text(text)
        (self.root / "src/ci_workflows/sample.py").unlink()
        (self.root / "docs/validation.md").write_text("unrelated\n")
        rules = self.rules()
        self.assertIn("workflow-input-drift", rules)
        self.assertIn("workflow-secret-drift", rules)
        self.assertIn("workflow-output-drift", rules)
        self.assertIn("workflow-permission-drift", rules)
        self.assertIn("stable-check-drift", rules)
        self.assertIn("missing-implementation-component", rules)
        self.assertIn("public-api-doc-drift", rules)

    def test_reusable_workflow_call_graph_rejects_missing_cycle_depth_and_internal_nesting(self) -> None:
        first = self.root / ".github/workflows/reusable-sample.yml"
        first.write_text(
            """name: Sample reusable validation
on:
  workflow_call:
    inputs:
      admitted_sha:
        required: true
        type: string
    outputs:
      result:
        description: Validation result
        value: ${{ jobs.call_leaf.outputs.result }}
permissions:
  contents: read
jobs:
  call_leaf:
    name: CI / Sample
    uses: ./.github/workflows/internal-leaf.yml
    timeout-minutes: 20
"""
        )
        write_text(
            self.root / ".github/workflows/internal-leaf.yml",
            """name: Internal leaf
on:
  workflow_call:
permissions:
  contents: read
jobs:
  call_back:
    name: Call back
    uses: ./.github/workflows/reusable-sample.yml
    timeout-minutes: 10
""",
        )
        rules = self.rules()
        self.assertIn("reusable-workflow-cycle", rules)
        self.assertIn("nested-internal-workflow", rules)
        self.assertIn("reusable-workflow-depth", rules)
        (self.root / ".github/workflows/internal-leaf.yml").unlink()
        self.assertIn("missing-workflow-dependency", self.rules())

    def test_caller_fixture_contract_validation(self) -> None:
        create_repository(
            self.root,
            config_overrides={
                "required_fixture_callers": ["ordinary-validation.yml"]
            },
        )
        create_caller(
            self.root,
            extra_with="      runner: self-hosted\n",
            permissions="  issues: write\n",
            event="schedule",
        )
        rules = self.rules()
        self.assertIn("forbidden-caller-input", rules)
        self.assertIn("unknown-caller-input", rules)
        self.assertIn("caller-permission-drift", rules)
        self.assertIn("caller-event-drift", rules)
        caller = (
            self.root
            / "tests/fixtures/harness/callers/ordinary-validation.yml"
        )
        caller.write_text(
            caller.read_text().replace(
                "    with:",
                "    secrets: inherit\n    with:",
            )
        )
        self.assertIn("secrets-inherit", self.rules())

    def test_required_fixture_and_service_scenario_coverage(self) -> None:
        create_repository(
            self.root,
            config_overrides={
                "required_fixture_callers": ["ordinary-validation.yml"],
                "required_event_fixtures": ["push"],
                "required_service_scenarios": {
                    "github": ["positive", "outage"]
                },
            },
        )
        rules = self.rules()
        self.assertIn("missing-caller-fixture", rules)
        self.assertIn("missing-event-fixture", rules)
        self.assertIn("missing-service-fixture", rules)
        create_caller(self.root)
        write_json(
            self.root / "tests/fixtures/harness/events.json",
            {"events": {"push": {"event_name": "push"}}},
        )
        write_json(
            self.root / "tests/fixtures/harness/service-scenarios.json",
            {"services": {"github": {"scenarios": [{"name": "positive"}]}}},
        )
        self.assertIn("missing-service-scenario", self.rules())

    def test_tool_lock_drift_is_rejected(self) -> None:
        (self.root / "requirements/validation.lock").write_text("PyYAML>=6\n")
        payload = json.loads(
            (self.root / "contracts/action-tool-lock.json").read_text()
        )
        payload["python"]["packages"][0]["sha256"] = "bad"
        write_json(self.root / "contracts/action-tool-lock.json", payload)
        self.assertIn("tool-lock-drift", self.rules())


if __name__ == "__main__":
    unittest.main()
