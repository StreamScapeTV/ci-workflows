from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_multistage import build_protected_full_plan
from ci_workflows.apple_plan_guard import validate_protected_full_plan_json
from ci_workflows.ci_broker_action import _apple_validation_plan, _shared_apple_environment
from ci_workflows.validation_model import load_actions_yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/central-ci-dispatch.yml"
POLICY = ROOT / "contracts/repository-policy.json"
CONTRACT = ROOT / "contracts/ci-broker.json"
BROKER_CORE = ROOT / "src/ci_workflows/ci_broker.py"
BROKER_ACTION = ROOT / "src/ci_workflows/ci_broker_action.py"


class BrokerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_actions_yaml(WORKFLOW, ROOT)
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.policy = json.loads(POLICY.read_text(encoding="utf-8"))
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cls.broker_core_text = BROKER_CORE.read_text(encoding="utf-8")
        cls.broker_action_text = BROKER_ACTION.read_text(encoding="utf-8")

    def test_dispatch_surface_is_manual_opaque_and_oidc_only(self) -> None:
        events = self.document.data["on"]
        self.assertEqual(set(events), {"workflow_dispatch"})
        inputs = events["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"dispatch_id", "dispatch_token"})
        self.assertTrue(all(value["required"] for value in inputs.values()))
        for forbidden in ("repository", "ref", "source_sha", "workflow_key", "test_profile"):
            self.assertNotIn(forbidden, inputs)
        permissions = self.document.data["permissions"]
        self.assertEqual(permissions, {"contents": "read", "id-token": "write"})

    def test_one_hosted_job_is_replay_serialized_by_dispatch_id(self) -> None:
        concurrency = self.document.data["concurrency"]
        self.assertIn("inputs.dispatch_id", concurrency["group"])
        self.assertIs(False, concurrency["cancel-in-progress"])
        self.assertEqual(set(self.document.data["jobs"]), {"execute"})
        job = self.document.data["jobs"]["execute"]
        self.assertEqual(job["runs-on"], ["macos-latest"])
        self.assertEqual(job["timeout-minutes"], 120)

    def test_actions_receive_broker_and_r2_writer_only(self) -> None:
        self.assertIn("secrets.CI_BROKER_URL", self.text)
        for name in (
            "R2_ACCOUNT_ID",
            "R2_BUCKET",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        ):
            self.assertIn(f"secrets.{name}", self.text)
        for forbidden in (
            "AGENT_STATE_SUPABASE",
            "AGENT_STATE_WEBHOOK",
            "GITHUB_SOURCE_APP",
            "GITHUB_DISPATCH_APP",
            "R2_READ_ACCESS_KEY_ID",
            "R2_READ_SECRET_ACCESS_KEY",
            "secrets: inherit",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_private_execution_has_terminal_fallback_and_unconditional_cleanup(self) -> None:
        self.assertIn("execute-apple-host", self.text)
        self.assertIn("fail-if-active", self.text)
        self.assertIn("cancel-if-active", self.text)
        self.assertIn("python3 scripts/ci/ci_broker.py cleanup", self.text)
        self.assertIn("if: ${{ always() }}", self.text)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.text)
        self.assertNotIn("upload-artifact", self.text)

    def test_broker_dispatch_uses_main_and_shared_apple_implementation(self) -> None:
        self.assertIn('CENTRAL_REF = "main"', self.broker_core_text)
        self.assertIn("execute_apple_validate", self.broker_action_text)
        self.assertIn("prepare_workspace", self.broker_action_text)
        self.assertIn('INPUT_VALIDATION_SCOPE="protected-full"', self.broker_action_text)
        self.assertIn('INPUT_SOURCE_TRUST="trusted-exact"', self.broker_action_text)
        self.assertNotIn('["xcodebuild",', self.broker_action_text)

    def test_broker_generated_host_plan_is_accepted_by_shared_apple_contract(self) -> None:
        raw = _apple_validation_plan(
            "Sample.xcworkspace",
            "Sample",
            "SampleTests/SelectedIntegrationTests",
        )
        validate_protected_full_plan_json(raw)
        plan = build_protected_full_plan(
            raw,
            repository="example/private-source",
            admitted_sha="a" * 40,
            source_trust="trusted-exact",
            contract=load_apple_contract(ROOT),
        )
        self.assertEqual(len(plan.stages), 1)
        self.assertEqual(plan.stages[0].platform, "macos")
        self.assertEqual(plan.stages[0].operation, "test")
        self.assertEqual(
            plan.stages[0].plan.commands[0].fixed_arguments,
            ("-only-testing:SampleTests/SelectedIntegrationTests",),
        )

        child_environment = _shared_apple_environment(
            repository="example/private-source",
            source_sha="a" * 40,
            source_token="opaque-token",
            workspace="Sample.xcworkspace",
            scheme="Sample",
            test_target="SampleTests/SelectedIntegrationTests",
            environment={
                "GITHUB_OUTPUT": "/tmp/output",
                "GITHUB_ENV": "/tmp/env",
                "GITHUB_STEP_SUMMARY": "/tmp/summary",
            },
        )
        self.assertEqual(child_environment["GITHUB_REPOSITORY"], "example/private-source")
        self.assertEqual(child_environment["INPUT_SOURCE_TRUST"], "trusted-exact")
        self.assertNotIn("GITHUB_OUTPUT", child_environment)
        self.assertNotIn("GITHUB_ENV", child_environment)
        self.assertNotIn("GITHUB_STEP_SUMMARY", child_environment)

    def test_repository_policy_and_broker_contract_match_workflow(self) -> None:
        record = self.policy["workflow_admission"]["workflows"][
            ".github/workflows/central-ci-dispatch.yml"
        ]
        self.assertEqual(record["trust_class"], "broker-dispatch")
        self.assertEqual(record["allowed_events"], ["workflow_dispatch"])
        self.assertEqual(
            self.contract["dispatch"]["inputs"],
            ["dispatch_id", "dispatch_token"],
        )
        self.assertEqual(self.contract["dispatch"]["token_ttl_seconds"], 21600)
        self.assertEqual(self.contract["dispatch"]["duplicate_concurrency_key"], "dispatch_id")
        self.assertEqual(self.contract["agent_state"]["execution_repository_field"], "external_repository")


if __name__ == "__main__":
    unittest.main()
