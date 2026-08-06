from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_SCRIPTS = ROOT / "scripts" / "ci"
if str(CI_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CI_SCRIPTS))

import workflow_inventory  # noqa: E402


class WorkflowInventorySourceTests(unittest.TestCase):
    def test_analyze_workflow_extracts_source_shape(self) -> None:
        source = """name: Example CI
on:
  pull_request:
  workflow_dispatch:
permissions:
  contents: read
  issues: write
jobs:
  validate:
    runs-on: portable
    steps:
      - uses: actions/checkout@0123456789012345678901234567890123456789
      - uses: StreamScapeTV/agent-state/.github/workflows/agent-state-lifecycle.yml@main
        secrets:
          token: ${{ secrets.AGENT_STATE_API_TOKEN }}
      - uses: actions/upload-artifact@1111111111111111111111111111111111111111
      - run: python3 -m pytest && helm template chart && buildah bud .
"""
        record = workflow_inventory.analyze_workflow(
            ".github/workflows/example.yml", source, "a" * 40
        )
        self.assertEqual(record["name"], "Example CI")
        self.assertEqual(record["triggers"], ["pull_request", "workflow_dispatch"])
        self.assertEqual(record["permissions"], ["contents:read", "issues:write"])
        self.assertEqual(record["runs_on"], ["portable"])
        self.assertEqual(record["secrets"], ["AGENT_STATE_API_TOKEN"])
        self.assertTrue(record["uploads_artifacts"])
        self.assertEqual(
            record["calls_reusable_workflows"],
            ["StreamScapeTV/agent-state/.github/workflows/agent-state-lifecycle.yml@main"],
        )
        self.assertEqual(record["products"], ["helm", "oci", "python"])
        self.assertEqual(record["blob_sha"], "a" * 40)

    def test_inline_trigger_list_and_absent_permissions_are_deterministic(self) -> None:
        source = 'name: Inline\n"on": [push, workflow_dispatch]\njobs:\n  one:\n    runs-on: [portable]\n'
        record = workflow_inventory.analyze_workflow(
            ".github/workflows/inline.yaml", source
        )
        self.assertEqual(record["triggers"], ["push", "workflow_dispatch"])
        self.assertEqual(record["permissions"], [])
        self.assertEqual(record["name"], "Inline")

    def test_validate_and_render_route_to_the_authoritative_v2_contract(self) -> None:
        self.assertEqual(
            workflow_inventory.main(["--root", str(ROOT), "validate"]),
            0,
        )
        self.assertEqual(
            workflow_inventory.main(["--root", str(ROOT), "render", "--check"]),
            0,
        )

    def test_compare_inventory_delegates_to_live_tree_contract(self) -> None:
        inventory = {
            "repositories": [
                {
                    "repository": "StreamScapeTV/example",
                    "workflows": [
                        [
                            ".github/workflows/current.yml",
                            "Current",
                            "current",
                            "thin",
                            "other",
                            "read",
                            "a" * 40,
                        ]
                    ],
                }
            ]
        }
        self.assertEqual(
            workflow_inventory.compare_inventory(
                inventory,
                {
                    "StreamScapeTV/example": {
                        ".github/workflows/current.yml": "a" * 40
                    }
                },
            ),
            [],
        )
        errors = workflow_inventory.compare_inventory(
            inventory,
            {
                "StreamScapeTV/example": {
                    ".github/workflows/new.yml": "b" * 40
                }
            },
        )
        self.assertTrue(any("workflow removed" in error for error in errors))
        self.assertTrue(any("workflow added" in error for error in errors))

    def test_compatibility_module_contains_no_second_inventory_schema(self) -> None:
        source = (CI_SCRIPTS / "workflow_inventory.py").read_text(encoding="utf-8")
        self.assertNotIn('"schema_version": 1', source)
        self.assertNotIn("def capture(", source)
        self.assertIn("inventory_contract.main", source)
        self.assertIn("inventory_live_check.main", source)


if __name__ == "__main__":
    unittest.main()
