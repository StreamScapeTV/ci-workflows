from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from ci_workflows.central_profile import CentralProfileResolution
from ci_workflows.ci_private_apple import (
    _execution_environment,
    _receipt,
    execute_private_apple,
    recover_private_apple,
)
from ci_workflows.ci_relay import RelayRequest
from ci_workflows.r2_diagnostics import R2DiagnosticError, R2DiagnosticResult

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/private-apple-ci/action.yml"
WORKFLOW = ROOT / ".github/workflows/central-ci-dispatch.yml"
DOC = ROOT / "docs/workflows/ci-diagnostics.md"
CONTRACT = ROOT / "contracts/ci-diagnostics.json"

CI_RUN_ID = "00000000-0000-4000-8000-000000000019"
SOURCE_SHA = "a" * 40


def request() -> RelayRequest:
    return RelayRequest.from_claimed_run(
        {
            "ci_run_id": CI_RUN_ID,
            "project_key": "private-project",
            "origin": "agent_request",
            "status": "accepted",
            "repository": "OtherOrg/private-app",
            "ref": "develop",
            "is_tag": False,
            "workflow_key": "validation.apple",
            "test_profile": "host",
            "inputs": {},
            "requested_source_sha": None,
        }
    )


def resolution() -> CentralProfileResolution:
    return CentralProfileResolution(
        project_key="private-project",
        workflow_key="validation.apple",
        profile="host",
        capability="apple-host-test",
        source_repository="OtherOrg/private-app",
        admitted_sha=SOURCE_SHA,
        validation_scope="protected-full",
        validation_plan_json="{}",
        private_dependency_repository="",
        private_dependency_sha="",
        private_dependency_subdirectory=".",
        private_dependency_id="",
    )


class FakeClient:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.finishes: list[dict[str, object]] = []

    def start(self, identity, ci_run_id: str) -> str:
        self.events.append(("start", identity.repository, identity.ref, ci_run_id))
        return ci_run_id

    def evidence(self, ci_run_id: str, source_sha: str) -> None:
        self.events.append(("evidence", ci_run_id, source_sha))

    def finish(self, ci_run_id: str, **kwargs: object) -> None:
        self.events.append(("finish", ci_run_id))
        self.finishes.append({"ci_run_id": ci_run_id, **kwargs})


class TokenClient:
    def repository_contents_read_token(self, repository: str) -> str:
        return "synthetic-private-source-token-1234567890"


def environment(root: Path) -> dict[str, str]:
    runner_temp = root / "runner-temp"
    workspace = root / "workspace"
    runner_temp.mkdir()
    workspace.mkdir()
    return {
        "INPUT_CI_RUN_ID": CI_RUN_ID,
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        "GITHUB_RUN_ID": "32860000001",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_JOB": "private",
        "RUNNER_OS": "macOS",
        "AGENT_STATE_SUPABASE_URL": "https://example.supabase.co",
        "AGENT_STATE_SUPABASE_SECRET_KEY": "synthetic-agent-state-secret",
        "SOURCE_APP_ID": "12345",
        "SOURCE_APP_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nsynthetic-private-key-material-1234567890\n-----END PRIVATE KEY-----",
        "R2_ACCOUNT_ID": "a" * 32,
        "R2_BUCKET": "ci-private-logs",
        "R2_ACCESS_KEY_ID": "synthetic-access",
        "R2_SECRET_ACCESS_KEY": "synthetic-secret",
        "GITHUB_OUTPUT": str(root / "must-not-use-output"),
        "GITHUB_ENV": str(root / "must-not-use-env"),
        "GITHUB_STEP_SUMMARY": str(root / "must-not-use-summary"),
    }


class PrivateAppleExecutorTests(unittest.TestCase):
    def test_execution_environment_removes_public_command_files(self) -> None:
        base = {
            "GITHUB_OUTPUT": "/tmp/output",
            "GITHUB_ENV": "/tmp/env",
            "GITHUB_STEP_SUMMARY": "/tmp/summary",
            "SAFE": "yes",
        }
        result = _execution_environment(
            request=request(),
            source_sha=SOURCE_SHA,
            resolution=resolution(),
            dependency=None,
            base=base,
            workspace_environment={"CI_WORKFLOW_ROOT": "/tmp/private"},
        )
        for forbidden in ("GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_STEP_SUMMARY"):
            self.assertNotIn(forbidden, result)
        self.assertEqual(result["GITHUB_REPOSITORY"], "OtherOrg/private-app")
        self.assertEqual(result["INPUT_ADMITTED_SHA"], SOURCE_SHA)

    def test_receipt_contains_only_r2_object_and_verified_digest(self) -> None:
        uploaded = R2DiagnosticResult(
            object_key=f"ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz",
            sha256="b" * 64,
            compressed_bytes=123,
        )
        self.assertEqual(
            _receipt(uploaded),
            f"r2:{uploaded.object_key}#sha256={'b' * 64}",
        )

    def test_success_uploads_before_terminal_agent_state(self) -> None:
        events: list[object] = []
        client = FakeClient(events)
        uploaded = R2DiagnosticResult(
            object_key=f"ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz",
            sha256="c" * 64,
            compressed_bytes=321,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = environment(root)
            source = Path(env["GITHUB_WORKSPACE"]) / "source"
            source.mkdir()
            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=client),
                mock.patch("ci_workflows.ci_private_apple._claim_request", return_value=request()),
                mock.patch("ci_workflows.ci_private_apple._source_token_client", return_value=TokenClient()),
                mock.patch("ci_workflows.ci_private_apple._resolve_source_sha", return_value=SOURCE_SHA),
                mock.patch("ci_workflows.ci_private_apple._checkout_source", return_value=source),
                mock.patch("ci_workflows.ci_private_apple.resolve_profile", return_value=resolution()),
                mock.patch("ci_workflows.ci_private_apple._execute_validation", return_value=(True, True)),
                mock.patch(
                    "ci_workflows.ci_private_apple._r2_upload",
                    side_effect=lambda **_kwargs: (events.append("r2-upload"), uploaded)[1],
                ),
            ):
                self.assertTrue(execute_private_apple(env))
            log = (
                Path(env["RUNNER_TEMP"])
                / "central-private-ci"
                / CI_RUN_ID
                / "private.log"
            ).read_text(encoding="utf-8")
        self.assertIn("[lifecycle] running registered", log)
        self.assertIn("[profile] bounded private profile resolved", log)
        self.assertLess(events.index("r2-upload"), events.index(("finish", CI_RUN_ID)))
        self.assertEqual(client.finishes[-1]["status"], "succeeded")
        self.assertIsNone(client.finishes[-1]["error_summary"])
        self.assertEqual(client.finishes[-1]["diagnostic_status"], "uploaded")
        self.assertEqual(client.finishes[-1]["diagnostic_key"], _receipt(uploaded))

    def test_r2_failure_fails_closed_without_invented_pointer(self) -> None:
        events: list[object] = []
        client = FakeClient(events)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = environment(root)
            source = Path(env["GITHUB_WORKSPACE"]) / "source"
            source.mkdir()
            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=client),
                mock.patch("ci_workflows.ci_private_apple._claim_request", return_value=request()),
                mock.patch("ci_workflows.ci_private_apple._source_token_client", return_value=TokenClient()),
                mock.patch("ci_workflows.ci_private_apple._resolve_source_sha", return_value=SOURCE_SHA),
                mock.patch("ci_workflows.ci_private_apple._checkout_source", return_value=source),
                mock.patch("ci_workflows.ci_private_apple.resolve_profile", return_value=resolution()),
                mock.patch("ci_workflows.ci_private_apple._execute_validation", return_value=(True, True)),
                mock.patch(
                    "ci_workflows.ci_private_apple._r2_upload",
                    side_effect=R2DiagnosticError("r2_upload_unavailable"),
                ),
            ):
                self.assertFalse(execute_private_apple(env))
        finish = client.finishes[-1]
        self.assertEqual(finish["status"], "failed")
        self.assertEqual(finish["error_summary"], "private_log_upload_failed")
        self.assertEqual(finish["diagnostic_status"], "failed")
        self.assertIsNone(finish["diagnostic_key"])

    def test_recovery_uploads_interrupted_log_before_terminalizing(self) -> None:
        events: list[object] = []
        client = FakeClient(events)
        uploaded = R2DiagnosticResult(
            object_key=f"ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz",
            sha256="d" * 64,
            compressed_bytes=99,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = environment(root)
            state_root = Path(env["RUNNER_TEMP"]) / "central-private-ci" / CI_RUN_ID
            state_root.mkdir(parents=True)
            (state_root / "state.json").write_text(
                json.dumps({"ci_run_id": CI_RUN_ID, "started": True, "terminalized": False}),
                encoding="utf-8",
            )
            (state_root / "private.log").write_text("private output\n", encoding="utf-8")
            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=client),
                mock.patch(
                    "ci_workflows.ci_private_apple._r2_upload",
                    side_effect=lambda **_kwargs: (events.append("r2-upload"), uploaded)[1],
                ),
            ):
                recover_private_apple(env)
            self.assertFalse(state_root.exists())
        self.assertLess(events.index("r2-upload"), events.index(("finish", CI_RUN_ID)))
        self.assertEqual(client.finishes[-1]["status"], "failed")
        self.assertEqual(client.finishes[-1]["error_summary"], "private_ci_interrupted")
        self.assertEqual(client.finishes[-1]["diagnostic_status"], "uploaded")


class PrivateAppleSourceContractTests(unittest.TestCase):
    def test_action_and_workflow_expose_no_private_identity_fields(self) -> None:
        action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(set(action["inputs"]), {"phase", "ci_run_id"})
        self.assertEqual(
            set(workflow["on"]["workflow_dispatch"]["inputs"]),
            {"active_key", "ci_run_id"},
        )
        rendered = WORKFLOW.read_text(encoding="utf-8")
        for forbidden in (
            "inputs.repository",
            "inputs.ref",
            "inputs.project_key",
            "inputs.workflow_key",
            "inputs.profile",
            "GITHUB_SOURCE_APP_ID",
            "CI_D1_",
            "upload-artifact",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("secrets.SOURCE_APP_ID", rendered)
        self.assertIn("secrets.R2_ACCOUNT_ID", rendered)

    def test_contract_and_docs_define_r2_as_private_log_authority(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["store"], "cloudflare-r2")
        self.assertEqual(contract["format"], "gzip-private-runner-log")
        self.assertFalse(contract["github_public_log"]["private_command_stdout_stderr"])
        self.assertTrue(contract["agent_state"]["raw_logs_forbidden"])
        text = DOC.read_text(encoding="utf-8").lower()
        self.assertIn("runner-local", text)
        self.assertIn("cloudflare r2", text)
        self.assertIn("read-back", text)
        self.assertIn("agent state stores no log body", text)
        self.assertIn("cloudflare d1 is not part", text)


if __name__ == "__main__":
    unittest.main()
