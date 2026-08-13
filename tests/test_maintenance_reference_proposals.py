from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Mapping

from ci_workflows.maintenance_conformance import conformance
from ci_workflows.maintenance_contract import MaintenanceError, load_contract
from tests.test_maintenance_runtime import FakeApi

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "StreamScapeTV/ci-workflows"
TARGET = "b" * 40
MAIN = "c" * 40


class ReferenceApi(FakeApi):
    def __init__(self) -> None:
        super().__init__()
        self.commit: Mapping[str, Any] | None = {"sha": TARGET}
        self.branch = {"protected": True, "commit": {"sha": MAIN}}
        self.comparison: Mapping[str, Any] = {
            "status": "ahead",
            "base_commit": {"sha": TARGET},
            "merge_base_commit": {"sha": TARGET},
        }

    def get_commit(self, repository: str, sha: str):
        return self.commit

    def compare_commits(self, repository: str, base_sha: str, head_sha: str):
        return self.comparison


def _prime_inventory(api: ReferenceApi, source: str) -> str:
    inventory = json.loads(
        (ROOT / "contracts/workflow-inventory.json").read_text(encoding="utf-8")
    )
    repository = next(
        row for row in inventory["repositories"] if row["repository"] == REPOSITORY
    )
    paths = [row[0] for row in repository["workflows"]]
    api.workflow_files[REPOSITORY] = paths
    selected = paths[0]
    for path in paths:
        api.file_text[(REPOSITORY, path)] = ""
    api.file_text[(REPOSITORY, selected)] = source
    return selected


class MaintenanceReferenceProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def test_mutable_shared_reference_gets_concrete_review_only_repin_proposal(self) -> None:
        api = ReferenceApi()
        path = _prime_inventory(
            api,
            "uses: StreamScapeTV/ci-workflows/.github/workflows/"
            "reusable-node.yml@main\n",
        )
        result = conformance(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            shared_reference_target_sha=TARGET,
            dry_run=True,
            request_id="repin-main",
        )
        reference = next(
            row
            for row in result.decisions
            if row["kind"] == "shared_workflow_reference"
        )
        proposal = next(
            row
            for row in result.decisions
            if row["kind"] == "shared_workflow_update_proposal"
        )
        self.assertEqual(reference["path"], path)
        self.assertEqual(reference["reference"], "main")
        self.assertFalse(reference["immutable"])
        self.assertEqual(proposal["current_reference"], "main")
        self.assertEqual(proposal["proposed_reference"], TARGET)
        self.assertTrue(proposal["review_only"])
        self.assertNotIn("content", proposal)

    def test_already_targeted_immutable_reference_needs_no_proposal(self) -> None:
        api = ReferenceApi()
        _prime_inventory(
            api,
            "uses: StreamScapeTV/ci-workflows/.github/workflows/"
            f"reusable-node.yml@{TARGET}\n",
        )
        result = conformance(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            shared_reference_target_sha=TARGET,
            dry_run=True,
            request_id="repin-current",
        )
        kinds = [row["kind"] for row in result.decisions]
        self.assertIn("shared_workflow_reference", kinds)
        self.assertNotIn("shared_workflow_update_proposal", kinds)

    def test_current_protected_main_head_is_valid_target(self) -> None:
        api = ReferenceApi()
        api.commit = {"sha": MAIN}
        _prime_inventory(
            api,
            "uses: StreamScapeTV/ci-workflows/.github/workflows/"
            "reusable-node.yml@main\n",
        )
        result = conformance(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            shared_reference_target_sha=MAIN,
            dry_run=True,
            request_id="repin-head",
        )
        proposal = next(
            row
            for row in result.decisions
            if row["kind"] == "shared_workflow_update_proposal"
        )
        self.assertEqual(proposal["proposed_reference"], MAIN)

    def test_target_must_be_exact_existing_protected_main_history(self) -> None:
        api = ReferenceApi()
        with self.assertRaisesRegex(
            MaintenanceError,
            "invalid_expected_head_sha",
        ):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                shared_reference_target_sha="main",
                dry_run=True,
                request_id="repin-mutable-target",
            )

        api.commit = None
        with self.assertRaisesRegex(
            MaintenanceError,
            "shared_reference_target_invalid",
        ):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                shared_reference_target_sha=TARGET,
                dry_run=True,
                request_id="repin-missing-target",
            )

        api = ReferenceApi()
        api.comparison = {
            "status": "diverged",
            "base_commit": {"sha": TARGET},
            "merge_base_commit": {"sha": "d" * 40},
        }
        with self.assertRaisesRegex(
            MaintenanceError,
            "shared_reference_target_invalid",
        ):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                shared_reference_target_sha=TARGET,
                dry_run=True,
                request_id="repin-diverged-target",
            )

        api = ReferenceApi()
        api.branch = {"protected": False, "commit": {"sha": MAIN}}
        with self.assertRaisesRegex(
            MaintenanceError,
            "shared_reference_target_invalid",
        ):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                shared_reference_target_sha=TARGET,
                dry_run=True,
                request_id="repin-unprotected-main",
            )

    def test_empty_target_keeps_inventory_reporting_without_commit_lookup(self) -> None:
        api = ReferenceApi()
        api.commit = None
        api.branch = None
        _prime_inventory(
            api,
            "uses: StreamScapeTV/ci-workflows/.github/workflows/"
            "reusable-node.yml@main\n",
        )
        result = conformance(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            dry_run=True,
            request_id="scan-only",
        )
        kinds = {row["kind"] for row in result.decisions}
        self.assertIn("shared_workflow_reference", kinds)
        self.assertNotIn("shared_workflow_update_proposal", kinds)


if __name__ == "__main__":
    unittest.main()
