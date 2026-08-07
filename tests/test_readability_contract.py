from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.readability import (
    ReadabilityError,
    load_readability_policy,
    render_readability_docs,
    validate_repository_readability,
    write_readability_docs,
)
from ci_workflows.validation_model import ParsedDocument

ROOT = Path(__file__).resolve().parents[1]


def workflow_document(path: str, jobs: dict[str, object], *, inputs: dict[str, object] | None = None) -> ParsedDocument:
    data = {
        "name": "Fixture workflow",
        "on": {"workflow_call": {"inputs": inputs or {}}},
        "permissions": {"contents": "read"},
        "jobs": jobs,
    }
    return ParsedDocument(
        path=ROOT / path,
        relative_path=path,
        raw="",
        data=data,
    )


def action_document(path: str, *, uses: str | None = None, inputs: dict[str, object] | None = None) -> ParsedDocument:
    step: dict[str, object] = {"name": "Run named function", "shell": "bash", "run": "true"}
    if uses is not None:
        step = {"name": "Call local action", "uses": uses}
    data = {
        "name": "Fixture action",
        "inputs": inputs or {},
        "runs": {"using": "composite", "steps": [step]},
    }
    return ParsedDocument(
        path=ROOT / path,
        relative_path=path,
        raw="",
        data=data,
    )


class ReadabilityContractTests(unittest.TestCase):
    def test_checked_in_policy_has_exact_reviewed_limits_and_documentation(self) -> None:
        policy = load_readability_policy(ROOT)
        self.assertEqual(1, policy.public_reusable_workflow_depth)
        self.assertEqual(0, policy.internal_leaf_reusable_children)
        self.assertEqual(1, policy.composite_action_depth)
        self.assertEqual(7, policy.public_workflow_jobs)
        self.assertEqual(40, policy.inline_run_lines)
        self.assertEqual(8, policy.duplicate_block_min_lines)
        self.assertEqual(12, policy.complex_loop_min_lines)
        self.assertEqual(16, policy.matrix_jobs)
        self.assertTrue(policy.shell_functions_forbidden)
        self.assertIn(".github/workflows/reusable-tag-image-chart.yml", policy.exceptions)
        self.assertEqual(
            render_readability_docs(contract_root=ROOT),
            (ROOT / "docs/architecture/readability-and-functions.md").read_text(encoding="utf-8"),
        )
        write_readability_docs(contract_root=ROOT, check=True)

    def test_public_job_guidance_opaque_ids_and_callback_inputs_are_enforced(self) -> None:
        seven_jobs = {
            f"phase_{index}": {
                "name": f"Phase {index}",
                "runs-on": "portable",
                "timeout-minutes": 10,
                "steps": [{"name": "Run phase", "run": "true"}],
            }
            for index in range(7)
        }
        valid = workflow_document(
            ".github/workflows/reusable-fixture.yml",
            seven_jobs,
        )
        self.assertEqual(
            (),
            validate_repository_readability(
                ROOT,
                {valid.relative_path: valid},
                {},
            ),
        )

        too_many = dict(seven_jobs)
        too_many["phase_7"] = too_many["phase_6"]
        opaque = workflow_document(
            ".github/workflows/reusable-too-many.yml",
            {"job": next(iter(seven_jobs.values())), **too_many},
            inputs={"callback": {"type": "string"}},
        )
        rules = {
            finding.rule
            for finding in validate_repository_readability(
                ROOT,
                {opaque.relative_path: opaque},
                {},
            )
        }
        self.assertIn("public-workflow-job-count", rules)
        self.assertIn("opaque-generic-job-id", rules)
        self.assertIn("callback-like-input", rules)

    def test_composite_action_depth_is_one(self) -> None:
        leaf = action_document("actions/leaf/action.yml")
        parent = action_document("actions/parent/action.yml", uses="./actions/leaf")
        findings = validate_repository_readability(
            ROOT,
            {},
            {
                leaf.relative_path: leaf,
                parent.relative_path: parent,
            },
        )
        self.assertIn(
            "composite-action-depth",
            {finding.rule for finding in findings},
        )

    def test_missing_exception_regression_test_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            payload = json.loads(
                (ROOT / "contracts/readability-policy.json").read_text(encoding="utf-8")
            )
            payload["exceptions"][0]["tests"] = ["tests/test_missing.py"]
            (root / "contracts/readability-policy.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            target = root / payload["exceptions"][0]["path"]
            target.parent.mkdir(parents=True)
            target.write_text("name: fixture\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ReadabilityError,
                "readability-exception-test-invalid",
            ):
                load_readability_policy(root)


if __name__ == "__main__":
    unittest.main()
