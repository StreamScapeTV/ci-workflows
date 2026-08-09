from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class AgentStateRetirementTests(unittest.TestCase):
    def test_temporary_transport_files_are_absent(self) -> None:
        retired_paths = (
            ".github/workflows/agent-state-command.yml",
            "contracts/agent-state-command.json",
            "contracts/agent-state-projects.json",
            "docs/workflows/agent-state-command.md",
            "src/ci_workflows/agent_state_command.py",
            "src/ci_workflows/agent_state_contract.py",
            "src/ci_workflows/agent_state_transport.py",
            "tests/fixtures/agent-state-command/cases.json",
            "tests/fixtures/harness/callers/agent-state-lifecycle.yml",
        )
        self.assertTrue(all(not (ROOT / path).exists() for path in retired_paths))

    def test_public_api_has_no_agent_state_transport_or_secret(self) -> None:
        workflows = read_json("contracts/public-workflows.json")
        permissions = read_json("contracts/permission-profiles.json")
        types = read_json("contracts/public-workflow-types.json")

        self.assertTrue(
            all(not row["api_name"].startswith("agent-state.") for row in workflows["workflows"])
        )
        self.assertTrue(
            all(not profile["id"].startswith("agent-state-") for profile in permissions["profiles"])
        )
        self.assertNotIn("agent-state-transport", types["trust_classes"])
        self.assertNotIn("agent_state_api_token", types["secret_catalog"])

    def test_runner_contract_has_no_agent_state_execution_capability(self) -> None:
        runners = read_json("contracts/runner-profiles.json")
        self.assertNotIn("agent-state-control", {row["id"] for row in runners["profiles"]})
        self.assertTrue(
            all(not row["api"].startswith("agent-state.") for row in runners["workflow_bindings"])
        )
        self.assertNotIn("no-caller-source", runners["source_trust_values"])

    def test_inventory_removes_central_transport_and_retires_legacy_files(self) -> None:
        inventory = read_json("contracts/workflow-inventory.json")
        central = next(
            row for row in inventory["repositories"]
            if row["repository"] == "StreamScapeTV/ci-workflows"
        )
        self.assertNotIn(
            ".github/workflows/agent-state-command.yml",
            {row[0] for row in central["workflows"]},
        )
        legacy = [
            row for repository in inventory["repositories"]
            for row in repository["workflows"]
            if row[5] == "legacy-agent-state"
        ]
        self.assertTrue(legacy)
        self.assertTrue(
            all(
                row[2] == "legacy-file-present-operating-path-retired"
                and row[3] == "retire"
                and row[4] == "retire"
                for row in legacy
            )
        )


if __name__ == "__main__":
    unittest.main()
