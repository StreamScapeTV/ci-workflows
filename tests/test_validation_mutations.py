from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixture_builder import CHECKOUT_SHA, create_repository, valid_workflow, write_json, write_text
from ci_workflows.validation_harness import validate_repository


class ValidationMutationTest(unittest.TestCase):
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

    def test_workflow_shape_checkout_and_resource_failures(self) -> None:
        write_text(
            self.root / ".github/workflows/shape.yml",
            f"""name: ''
permissions:
  contents: execute
jobs:
  X:
    name: ''
    runs-on: ${{{{ inputs.runner }}}}
    timeout-minutes: 999
    strategy:
      matrix:
        axis: dynamic
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA} # v7.0.1
      - name: Incomplete checkout
        uses: actions/checkout@{CHECKOUT_SHA} # v7.0.1
        with:
          clean: true
          persist-credentials: false
""",
        )
        write_text(
            self.root / ".github/workflows/missing-runner.yml",
            """name: Missing runner
on:
  push:
permissions: {}
jobs:
  validate:
    name: Validate source
    timeout-minutes: 10
    steps: []
""",
        )
        write_text(
            self.root / ".github/workflows/invalid-runner.yml",
            """name: Invalid runner
on:
  push:
permissions: {}
jobs:
  validate:
    name: Validate source
    runs-on:
      unexpected: mapping
    timeout-minutes: 10
    steps: []
""",
        )
        write_text(
            self.root / ".github/workflows/invalid-call.yml",
            """name: Invalid reusable call
on:
  push:
permissions: {}
jobs:
  call:
    name: Call workflow
    uses: ./.github/workflows/reusable-sample.yml
    runs-on: portable
    timeout-minutes: 10
""",
        )
        rules = self.rules()
        for rule in {
            "opaque-workflow-name",
            "missing-trigger",
            "invalid-permission",
            "opaque-job-id",
            "opaque-job-name",
            "dynamic-runner",
            "excessive-timeout",
            "unbounded-matrix",
            "opaque-step-name",
            "unsafe-checkout",
            "unbounded-checkout",
            "mutable-checkout",
            "missing-runner",
            "invalid-runner",
            "invalid-reusable-job",
        }:
            self.assertIn(rule, rules)

    def test_composite_action_reference_and_duplication_failures(self) -> None:
        write_text(
            self.root / "actions/invalid/action.yml",
            """name: ''
runs:
  using: node20
""",
        )
        repeated = "\n".join(f"        echo line-{index}" for index in range(8))
        write_text(
            self.root / "actions/first/action.yml",
            f"""name: First composite
runs:
  using: composite
  steps:
    - uses: ./tools/missing
    - name: Invalid remote reference
      uses: docker://alpine:3.20
    - name: Repeated one
      shell: bash
      run: |
{repeated}
    - name: Repeated two
      shell: bash
      run: |
{repeated}
""",
        )
        write_text(
            self.root / "actions/a/action.yml",
            """name: Action A
runs:
  using: composite
  steps:
    - name: Call B
      uses: ./actions/b
""",
        )
        write_text(
            self.root / "actions/b/action.yml",
            """name: Action B
runs:
  using: composite
  steps:
    - name: Call A
      uses: ./actions/a
""",
        )
        payload = json.loads((self.root / "contracts/action-tool-lock.json").read_text())
        payload["third_party_actions"][0]["runtime"] = "node18"
        write_json(self.root / "contracts/action-tool-lock.json", payload)
        workflow = self.root / ".github/workflows/reusable-sample.yml"
        text = workflow.read_text()
        duplicate_block = "\n".join(f"          echo shared-{index}" for index in range(8))
        workflow.write_text(text + f"""
      - name: Shared implementation
        shell: bash
        run: |
{duplicate_block}
""")
        write_text(
            self.root / ".github/workflows/duplicate.yml",
            f"""name: Duplicate implementation
on:
  push:
permissions: {{}}
jobs:
  validate:
    name: Validate duplicate
    runs-on: portable
    timeout-minutes: 10
    steps:
      - name: Shared implementation
        shell: bash
        run: |
{duplicate_block}
""",
        )
        rules = self.rules()
        for rule in {
            "opaque-action-name",
            "invalid-internal-action",
            "opaque-step-name",
            "unapproved-internal-action",
            "missing-action-dependency",
            "invalid-action-reference",
            "composite-action-cycle",
            "duplicated-inline-run",
            "duplicated-implementation",
            "invalid-action-runtime",
        }:
            self.assertIn(rule, rules)

    def test_public_api_remote_reference_and_caller_failures(self) -> None:
        sample = self.root / ".github/workflows/reusable-sample.yml"
        sample.write_text(sample.read_text() + "\nconcurrency:\n  group: sample\n")
        write_text(
            self.root / ".github/workflows/reusable-unrecorded.yml",
            valid_workflow(),
        )
        fragment_path = self.root / "contracts/public-workflows/validation.json"
        fragment = json.loads(fragment_path.read_text())
        fragment["workflows"][0]["permission_profile"] = "missing-profile"
        write_json(fragment_path, fragment)
        caller = self.root / "tests/fixtures/harness/callers/ordinary-validation.yml"
        write_text(
            caller,
            """name: Invalid caller
on:
  pull_request:
permissions:
  contents: read
jobs:
  local_job:
    name: Not a thin call
    runs-on: portable
    timeout-minutes: 10
    steps: []
  mutable_call:
    name: Mutable remote call
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-sample.yml@develop
    with: {}
  unknown_call:
    name: Unknown API call
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-unknown.yml@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    with: {}
  foreign_call:
    name: Foreign API call
    uses: OtherOrg/ci-workflows/.github/workflows/reusable-sample.yml@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    with: {}
""",
        )
        rules = self.rules()
        for rule in {
            "caller-cancelling-concurrency",
            "missing-public-api-record",
            "unknown-permission-profile",
            "invalid-caller-fixture",
            "mutable-workflow-reference",
            "missing-caller-input",
            "unknown-called-api",
            "unapproved-reusable-workflow",
        }:
            self.assertIn(rule, rules)

    def test_publication_and_malformed_fixture_failures(self) -> None:
        path = self.root / ".github/workflows/reusable-sample.yml"
        text = path.read_text()
        text = text.replace("on:\n  workflow_call:", "on:\n  pull_request:\n  workflow_call:")
        text += """
      - name: Publish image
        shell: bash
        run: |
          docker push example.invalid/app:1.0.0
          skopeo inspect docker://example.invalid/app:1.0.0
"""
        path.write_text(text)
        write_text(self.root / ".github/workflows/broken.yaml", "name: [unterminated\n")
        config_path = self.root / "contracts/validation-harness.json"
        config = json.loads(config_path.read_text())
        config["required_service_scenarios"] = {"github": ["positive"]}
        write_json(config_path, config)
        write_text(self.root / "tests/fixtures/harness/service-scenarios.json", "not-json\n")
        rules = self.rules()
        self.assertIn("publication-from-pr", rules)
        self.assertIn("invalid-actions-yaml", rules)
        self.assertIn("missing-service-fixture", rules)

    def test_missing_tool_lock_is_rejected(self) -> None:
        (self.root / "requirements/validation.lock").unlink()
        self.assertIn("missing-tool-lock", self.rules())


if __name__ == "__main__":
    unittest.main()
