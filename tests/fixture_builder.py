"""Hermetic builders shared by validation-harness tests and later capability suites."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
REFERENCE_SHA = "a" * 40


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def minimal_public_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "file": ".github/workflows/reusable-sample.yml",
        "api_name": "validation.sample",
        "api_version": "1.0.0",
        "status": "implemented",
        "trust_class": "read-only-validation",
        "permission_profile": "validation-read",
        "semantic_runner_profile": "portable",
        "permitted_events": [
            "pull_request",
            "push",
            "workflow_dispatch",
            "workflow_call",
        ],
        "inputs": [{"name": "admitted_sha", "required": True}],
        "secrets": [],
        "outputs": ["result"],
        "stable_check_name": "CI / Sample",
        "timeout_minutes": 20,
        "matrix_max_jobs": 4,
        "supported_consumers": ["StreamScapeTV/example"],
        "supported_products": [],
        "repository_owned_hooks": [],
        "implementation_components": ["ci_workflows.sample.validate"],
    }
    record.update(overrides)
    return record


def valid_workflow(*, path: str = ".github/workflows/reusable-sample.yml") -> str:
    del path
    return f"""name: Sample reusable validation
on:
  workflow_call:
    inputs:
      admitted_sha:
        required: true
        type: string
    outputs:
      result:
        description: Validation result
        value: ${{{{ jobs.validate.outputs.result }}}}
permissions:
  contents: read
jobs:
  validate:
    name: CI / Sample
    runs-on: portable
    timeout-minutes: 20
    outputs:
      result: ${{{{ steps.execute.outputs.result }}}}
    steps:
      - name: Check out exact source
        uses: actions/checkout@{CHECKOUT_SHA} # v7.0.1
        with:
          ref: ${{{{ inputs.admitted_sha }}}}
          fetch-depth: 1
          clean: true
          persist-credentials: false
      - id: execute
        name: Validate exact head
        shell: bash
        run: |
          set -Eeuo pipefail
          test "$(git rev-parse HEAD)" = "${{{{ inputs.admitted_sha }}}}"
          PYTHONPATH=src python3 -m ci_workflows.sample
          echo "result=success" >> "${{{{ GITHUB_OUTPUT }}}}"
"""


def create_repository(
    root: Path,
    *,
    config_overrides: Mapping[str, Any] | None = None,
) -> None:
    record = minimal_public_record()
    write_json(
        root / "contracts/public-workflows.json",
        {
            "schema_version": 1,
            "workflow_count": 1,
            "fragment_contracts": ["contracts/public-workflows/validation.json"],
            "workflows": [
                {
                    "file": record["file"],
                    "api_name": record["api_name"],
                    "api_version": record["api_version"],
                    "status": record["status"],
                    "trust_class": record["trust_class"],
                    "fragment": "contracts/public-workflows/validation.json",
                }
            ],
        },
    )
    write_json(
        root / "contracts/public-workflows/validation.json",
        {"schema_version": 1, "group": "validation", "workflows": [record]},
    )
    write_json(
        root / "contracts/public-workflow-types.json",
        {
            "schema_version": 1,
            "defaults": {
                "max_reusable_workflow_depth": 2,
                "forbidden_caller_fields": [
                    "runner",
                    "runs_on",
                    "container_engine",
                    "registry_host",
                    "cluster",
                    "namespace",
                    "arbitrary_command",
                    "shell",
                    "callback_url",
                ],
            },
        },
    )
    write_json(
        root / "contracts/permission-profiles.json",
        {
            "schema_version": 1,
            "profiles": [
                {
                    "id": "validation-read",
                    "caller_permissions": {"contents": "read"},
                    "workflow_permissions": {"contents": "read"},
                }
            ],
        },
    )
    config: dict[str, Any] = {
        "schema_version": 1,
        "max_inline_run_lines": 20,
        "max_matrix_jobs": 8,
        "max_timeout_minutes": 240,
        "allowed_runner_profiles": ["portable"],
        "required_fixture_callers": [],
        "required_event_fixtures": [],
        "required_service_scenarios": {},
        "exceptions": [],
    }
    if config_overrides:
        config.update(config_overrides)
    write_json(root / "contracts/validation-harness.json", config)
    write_json(
        root / "contracts/action-tool-lock.json",
        {
            "schema_version": 1,
            "third_party_actions": [
                {
                    "uses": "actions/checkout",
                    "sha": CHECKOUT_SHA,
                    "release": "v7.0.1",
                    "runtime": "node24",
                }
            ],
            "approved_internal_actions": [".github/actions", "actions"],
            "python": {
                "minimum": "3.11",
                "packages": [
                    {
                        "name": "PyYAML",
                        "version": "6.0.3",
                        "sha256": "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f",
                    }
                ],
            },
        },
    )
    write_text(root / "requirements/validation.lock", "PyYAML==6.0.3\n")
    write_text(
        root / "docs/validation.md",
        "validation.sample .github/workflows/reusable-sample.yml\n",
    )
    write_text(root / ".github/workflows/reusable-sample.yml", valid_workflow())
    write_text(
        root / "src/ci_workflows/sample.py",
        '"""Sample named function."""\n\ndef validate() -> str:\n    return "success"\n\nif __name__ == "__main__":\n    print(validate())\n',
    )
    write_text(
        root / "tests/test_sample.py",
        "import unittest\n\nclass SampleTest(unittest.TestCase):\n    def test_true(self):\n        self.assertTrue(True)\n",
    )


def create_caller(
    root: Path,
    *,
    extra_with: str = "",
    permissions: str = "  contents: read\n",
    event: str = "pull_request",
) -> Path:
    path = root / "tests/fixtures/harness/callers/ordinary-validation.yml"
    write_text(
        path,
        f"""name: Sample caller
on:
  {event}:
permissions:
{permissions}jobs:
  validate_source:
    name: Validate exact source
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-sample.yml@{REFERENCE_SHA}
    with:
      admitted_sha: ${{{{ github.sha }}}}
{extra_with}""",
    )
    return path
