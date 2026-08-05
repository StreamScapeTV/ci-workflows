from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ci" / "workflow_inventory.py"
SPEC = importlib.util.spec_from_file_location("workflow_inventory", MODULE_PATH)
assert SPEC and SPEC.loader
workflow_inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workflow_inventory
SPEC.loader.exec_module(workflow_inventory)


class AnalyzeWorkflowTests(unittest.TestCase):
    def test_extracts_workflow_shape_without_execution(self) -> None:
        source = """name: Release
on:
  push:
    tags: ['*']
permissions:
  contents: read
jobs:
  publish:
    runs-on: [self-hosted, buildah-high]
    steps:
      - uses: actions/checkout@abc
        with:
          persist-credentials: false
      - uses: actions/upload-artifact@def
      - run: helm version && buildah version
        env:
          TOKEN: ${{ secrets.REGISTRY_TOKEN }}
"""
        result = workflow_inventory.analyze_workflow(source)
        self.assertEqual(result["name"], "Release")
        self.assertEqual(result["triggers"], ["push"])
        self.assertEqual(result["permissions"], {"contents": "read"})
        self.assertEqual(result["runners"], ["[self-hosted, buildah-high]"])
        self.assertEqual(result["secrets"], ["REGISTRY_TOKEN"])
        self.assertEqual(result["artifact_actions"], ["actions/upload-artifact@def"])
        self.assertEqual(result["product_markers"], ["helm", "oci"])
        self.assertIn("persist-credentials-false", result["source_markers"])

    def test_inline_triggers_and_scalar_permissions(self) -> None:
        source = "name: Audit\non: [push, pull_request]\npermissions: read-all\njobs:\n  audit:\n    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-audit.yml@main\n"
        result = workflow_inventory.analyze_workflow(source)
        self.assertEqual(result["triggers"], ["pull_request", "push"])
        self.assertEqual(result["permissions"], "read-all")
        self.assertEqual(
            result["reusable_workflows"],
            ["StreamScapeTV/ci-workflows/.github/workflows/reusable-audit.yml@main"],
        )


class ContractTests(unittest.TestCase):
    def _consumer_document(self) -> dict:
        return {
            "schema_version": 1,
            "organization": "StreamScapeTV",
            "repositories": [
                {
                    "repository": "StreamScapeTV/zeta",
                    "integration_branch": "main",
                    "agent_state_project": "zeta",
                    "technologies": ["python"],
                    "products": ["service"],
                    "runner_capabilities": ["portable"],
                    "required_checks": [],
                    "migration_status": "planned",
                },
                {
                    "repository": "StreamScapeTV/alpha",
                    "integration_branch": "develop",
                    "agent_state_project": "alpha",
                    "technologies": ["node"],
                    "products": ["web"],
                    "runner_capabilities": ["portable"],
                    "required_checks": ["CI"],
                    "migration_status": "planned",
                },
            ],
        }

    def test_load_consumers_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consumers.json"
            path.write_text(json.dumps(self._consumer_document()), encoding="utf-8")
            consumers = workflow_inventory.load_consumers(path)
        self.assertEqual(
            [item.repository for item in consumers],
            ["StreamScapeTV/alpha", "StreamScapeTV/zeta"],
        )

    def test_duplicate_consumer_fails(self) -> None:
        document = self._consumer_document()
        document["repositories"].append(dict(document["repositories"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consumers.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(workflow_inventory.InventoryError, "duplicate repository"):
                workflow_inventory.load_consumers(path)

    def test_compare_reports_commit_and_workflow_drift(self) -> None:
        expected = {
            "repositories": [
                {
                    "repository": "StreamScapeTV/demo",
                    "exact_commit": "a" * 40,
                    "workflows": [{"path": ".github/workflows/ci.yml", "sha256": "old"}],
                }
            ]
        }
        actual = {
            "repositories": [
                {
                    "repository": "StreamScapeTV/demo",
                    "exact_commit": "b" * 40,
                    "workflows": [
                        {"path": ".github/workflows/ci.yml", "sha256": "new"},
                        {"path": ".github/workflows/release.yml", "sha256": "release"},
                    ],
                }
            ]
        }
        errors = workflow_inventory.compare_inventory(expected, actual)
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("integration commit changed" in item for item in errors))
        self.assertTrue(any("workflow changed" in item for item in errors))
        self.assertTrue(any("workflow added" in item for item in errors))

    def test_markdown_escapes_table_cells(self) -> None:
        document = {
            "repositories": [
                {
                    "repository": "StreamScapeTV/demo",
                    "integration_branch": "main",
                    "exact_commit": "a" * 40,
                    "products": ["web"],
                    "migration_status": "planned",
                    "workflows": [
                        {
                            "path": ".github/workflows/ci.yml",
                            "name": "A | B",
                            "triggers": ["push"],
                            "runners": ["portable"],
                            "product_markers": ["node"],
                            "disposition": "central public reusable workflow",
                            "migration_target": "reusable-node.yml",
                        }
                    ],
                }
            ]
        }
        rendered = workflow_inventory.render_markdown(document)
        self.assertIn("A \\| B", rendered)
        self.assertIn("main@" + "a" * 40, rendered)

    def test_normalize_sorts_repositories_and_workflows(self) -> None:
        document = {
            "repositories": [
                {
                    "repository": "StreamScapeTV/zeta",
                    "workflows": [{"path": "z.yml"}, {"path": "a.yml"}],
                },
                {"repository": "StreamScapeTV/alpha", "workflows": []},
            ]
        }
        normalized = workflow_inventory.normalize_inventory(document)
        self.assertEqual(
            [row["repository"] for row in normalized["repositories"]],
            ["StreamScapeTV/alpha", "StreamScapeTV/zeta"],
        )
        self.assertEqual(
            [row["path"] for row in normalized["repositories"][1]["workflows"]],
            ["a.yml", "z.yml"],
        )


if __name__ == "__main__":
    unittest.main()
