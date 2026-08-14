from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ci" / "final_candidate_contract.py"
FIXTURES = ROOT / "tests" / "fixtures" / "final_candidate_prefix_cases.json"

spec = importlib.util.spec_from_file_location("final_candidate_contract", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FinalCandidateLifecycleContractTests(unittest.TestCase):
    def test_checked_in_contract_and_report_are_current(self) -> None:
        errors = module.validate(module.load_contract())
        self.assertEqual(errors, [])

    def test_checkpoint_prefix_never_suppresses_pull_request_validation(self) -> None:
        fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        for subject in fixtures["checkpoint_subjects"]:
            self.assertTrue(subject.startswith(module.EXPECTED_PREFIX))
            self.assertTrue(
                module.should_run_product_validation(
                    event_name="pull_request",
                    protected=False,
                    subject=subject,
                )
            )
            self.assertFalse(
                module.should_run_product_validation(
                    event_name="push",
                    protected=False,
                    subject=subject,
                )
            )
            self.assertTrue(
                module.should_run_product_validation(
                    event_name="push",
                    protected=True,
                    subject=subject,
                )
            )

    def test_native_skip_markers_are_rejected_by_policy_fixture(self) -> None:
        fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        for subject in fixtures["native_skip_subjects"]:
            self.assertTrue(module.has_native_skip_marker(subject), subject)
        for subject in fixtures["checkpoint_subjects"] + fixtures["ordinary_subjects"]:
            self.assertFalse(module.has_native_skip_marker(subject), subject)

    def test_noncompliant_rows_have_bounded_consumer_owners(self) -> None:
        contract = module.load_contract()
        rows = [
            (repo["repository"], workflow)
            for repo in contract["repositories"]
            for workflow in repo["workflows"]
            if "noncompliant-unprotected-feature-branch-product-validation"
            in workflow["trigger_classes"]
        ]
        self.assertEqual(
            {(repo, workflow["path"]) for repo, workflow in rows},
            {
                ("StreamScapeTV/StreamScapeWeb", ".github/workflows/branch-feedback.yml"),
                ("StreamScapeTV/finance-hub", ".github/workflows/ci.yml"),
            },
        )
        for _, workflow in rows:
            self.assertRegex(
                workflow["remediation_issue"],
                r"^https://github\.com/StreamScapeTV/[^/]+/issues/[0-9]+$",
            )


if __name__ == "__main__":
    unittest.main()
