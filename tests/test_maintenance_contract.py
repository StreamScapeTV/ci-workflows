from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.maintenance_contract import MaintenanceError, load_contract

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceContractTests(unittest.TestCase):
    def test_contract_is_exact_bounded_and_dry_run_first(self) -> None:
        contract = load_contract(ROOT)
        self.assertEqual(contract.organization, "StreamScapeTV")
        self.assertEqual(set(contract.operations), {"artifacts", "branches", "conformance", "runner_retry", "flux_reconcile"})
        self.assertEqual(contract.maintenance_runner_selector, ("linux", "amd64", "general"))
        self.assertEqual(contract.flux_runner_selector, ("linux", "amd64", "flux-control"))
        self.assertTrue(all(value["dry_run_default"] is True for value in contract.operations.values()))
        self.assertEqual(contract.project("iptv-android").integration_branch, "develop")
        self.assertEqual(contract.project("agent-state").repository, "StreamScapeTV/agent-state")
        self.assertEqual(len(contract.selected_projects("")), 11)

    def test_operation_metadata_records_trust_triggers_policy_source_and_outputs(self) -> None:
        contract = load_contract(ROOT)
        self.assertEqual(contract.operation("artifacts")["trust_class"], "trusted-maintenance")
        self.assertEqual(contract.operation("flux_reconcile")["trust_class"], "flux-authorized")
        self.assertEqual(contract.operation("artifacts")["trigger"]["recommended_cron"], "17,47 * * * *")
        self.assertEqual(contract.operation("runner_retry")["trigger"]["recommended_cron"], "*/10 * * * *")
        self.assertEqual(contract.operation("conformance")["policy_source"]["authority"], "central-inventory")
        self.assertEqual(set(contract.operation("conformance")["outputs"]), {"result", "mutation_count", "report_issue_url", "request_id"})
        self.assertIsNone(contract.operation("flux_reconcile")["trigger"]["recommended_cron"])

    def test_unknown_project_request_and_mutable_sha_fail_closed(self) -> None:
        contract = load_contract(ROOT)
        with self.assertRaisesRegex(MaintenanceError, "project_not_allowlisted"):
            contract.project("not-a-project")
        with self.assertRaisesRegex(MaintenanceError, "invalid_expected_head_sha"):
            contract.validate_sha("main")
        with self.assertRaisesRegex(MaintenanceError, "invalid_request_id"):
            contract.validate_request_id("contains whitespace")

    def test_flux_contract_stores_paths_not_targets_or_credentials(self) -> None:
        raw = json.loads((ROOT / "contracts/organization-maintenance.json").read_text(encoding="utf-8"))
        flux = raw["operations"]["flux_reconcile"]
        self.assertEqual(flux["repository"], "StreamScapeTV/flux")
        self.assertEqual(flux["policy_interface"], "central-flux-policy-v1")
        self.assertEqual(flux["policy_path"], "scripts/rollout/validate_request.py")
        rendered = json.dumps(raw, sort_keys=True).casefold()
        for forbidden in ("agent-state-api", "iptv-backend-worker", "directus-web", "kube-system"):
            self.assertNotIn(forbidden, rendered)

    def test_retired_agent_state_transport_cannot_disappear_from_boundary(self) -> None:
        raw = json.loads((ROOT / "contracts/organization-maintenance.json").read_text(encoding="utf-8"))
        retired = set(raw["retired_boundaries"]["agent_state_transport"])
        self.assertTrue({"agent-state-claim", "agent-state-lifecycle", "agent-state-ownership"} <= retired)

    def test_fixture_manifest_covers_required_safety_cases(self) -> None:
        cases = json.loads((ROOT / "tests/fixtures/organization-maintenance/cases.json").read_text(encoding="utf-8"))
        negative = set(cases["negative"])
        for name in ("changed-artifact-before-delete", "unmerged-branch", "protected-branch", "deterministic-product-failure", "flux-source-mutation", "flux-command-field-in-plan"):
            self.assertIn(name, negative)


if __name__ == "__main__":
    unittest.main()
