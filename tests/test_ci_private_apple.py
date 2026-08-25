from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

from ci_workflows.ci_lifecycle import WorkflowIdentity
from ci_workflows.ci_private_apple import (
    PrivateAppleError,
    _private_execution_environment,
    _r2_receipt,
    execute_private_apple,
    recover_private_apple,
)
from ci_workflows.r2_diagnostics import R2DiagnosticResult

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/private-apple-ci/action.yml"
WORKFLOW = ROOT / ".github/workflows/central-ci-dispatch.yml"
CONTRACT = ROOT / "contracts/ci-diagnostics.json"
DOC = ROOT / "docs/workflows/ci-diagnostics.md"
CI_RUN_ID = "11111111-2222-4333-8444-555555555555"


class LifecycleStub:
    def __init__(self, run: dict[str, object]) -> None:
        self.run = dict(run)
        self.calls: list[tuple[str, object]] = []

    def claim(self, ci_run_id: str) -> dict[str, object]:
        self.calls.append(("claim", ci_run_id))
        return {"ok": True, "run": dict(self.run), "replayed": True}

    def start(self, identity: WorkflowIdentity, ci_run_id: str | None = None) -> str:
        self.calls.append(("start", (identity, ci_run_id)))
        return ci_run_id or CI_RUN_ID

    def evidence(self, ci_run_id: str, observed_sha: str) -> None:
        self.calls.append(("evidence", (ci_run_id, observed_sha)))

    def finish(self, ci_run_id: str, **kwargs: object) -> None:
        self.calls.append(("finish", (ci_run_id, kwargs)))


class AppClientStub:
    def repository_contents_read_token(self, repository: object) -> str:
        return "synthetic-repository-token"


class ProviderStub:
    def repository(self, repository: str) -> dict[str, object]:
        return {"default_branch": "develop"}

    def branch_sha(self, repository: str, branch: str) -> str:
        return "a" * 40

    def commit(self, repository: str, sha: str) -> dict[str, object]:
        return {"sha": sha}


class ProfileStub:
    capability = "apple-host-test"
    validation_scope = "protected-full"
    validation_plan_json = "{}"
    private_dependency_repository = ""
    private_dependency_sha = ""
    private_dependency_subdirectory = "."
    private_dependency_id = ""


def claimed_run() -> dict[str, object]:
    return {
        "ci_run_id": CI_RUN_ID,
        "project_key": "iptv-apple",
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


def environment(root: Path) -> dict[str, str]:
    workspace = root / "workspace"
    runner_temp = root / "runner-temp"
    workspace.mkdir()
    runner_temp.mkdir()
    return {
        "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        "GITHUB_RUN_ID": "32860000001",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_JOB": "private_apple",
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(runner_temp),
        "RUNNER_OS": "macOS",
        "AGENT_STATE_SUPABASE_URL": "https://example.supabase.co",
        "AGENT_STATE_SUPABASE_SECRET_KEY": "synthetic-agent-state",
        "SOURCE_APP_ID": "123",
        "SOURCE_APP_PRIVATE_KEY": "synthetic-private-key",
        "R2_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
        "R2_BUCKET": "private-ci-logs",
        "R2_ACCESS_KEY_ID": "synthetic-r2-access",
        "R2_SECRET_ACCESS_KEY": "synthetic-r2-secret",
        "INPUT_CI_RUN_ID": CI_RUN_ID,
        "GITHUB_OUTPUT": str(root / "public-output"),
        "GITHUB_ENV": str(root / "public-env"),
        "GITHUB_STEP_SUMMARY": str(root / "public-summary"),
    }


class PrivateAppleExecutorTests(unittest.TestCase):
    def test_receipt_contains_only_r2_object_and_verified_digest(self) -> None:
        result = R2DiagnosticResult(
            object_key=f"ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz",
            sha256="b" * 64,
            compressed_bytes=123,
        )
        receipt = _r2_receipt(result)
        self.assertEqual(
            receipt,
            f"r2:ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz#sha256={'b' * 64}",
        )
        self.assertNotIn("OtherOrg", receipt)
        self.assertNotIn("develop", receipt)

    def test_execution_environment_removes_public_command_files(self) -> None:
        values = _private_execution_environment(
            {
                "GITHUB_OUTPUT": "/tmp/output",
                "GITHUB_ENV": "/tmp/env",
                "GITHUB_STEP_SUMMARY": "/tmp/summary",
                "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            },
            repository="OtherOrg/private-app",
            source_sha="a" * 40,
        )
        self.assertNotIn("GITHUB_OUTPUT", values)
        self.assertNotIn("GITHUB_ENV", values)
        self.assertNotIn("GITHUB_STEP_SUMMARY", values)
        self.assertEqual(values["GITHUB_REPOSITORY"], "OtherOrg/private-app")
        self.assertEqual(values["CIW_PRIVATE_LOG_ONLY"], "1")

    def test_success_uploads_before_terminal_agent_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            lifecycle = LifecycleStub(claimed_run())
            events: list[str] = []

            def exact_checkout(**kwargs: object) -> dict[str, str]:
                target = Path(kwargs["workspace"]) / str(kwargs["path"])
                target.mkdir(parents=True)
                (target / ".git").mkdir()
                return {"head_sha": "a" * 40}

            def upload(**kwargs: object) -> R2DiagnosticResult:
                events.append("upload")
                path = Path(kwargs["diagnostic_path"])
                self.assertTrue(path.is_file())
                return R2DiagnosticResult(
                    object_key=f"ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz",
                    sha256="c" * 64,
                    compressed_bytes=100,
                )

            def finish(ci_run_id: str, **kwargs: object) -> None:
                events.append("finish")
                lifecycle.calls.append(("finish", (ci_run_id, kwargs)))

            lifecycle.finish = finish  # type: ignore[method-assign]
            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=lifecycle),
                mock.patch("ci_workflows.ci_private_apple.GitHubAppRepositoryTokenClient", return_value=AppClientStub()),
                mock.patch("ci_workflows.ci_private_apple.GitHubSourceProvider", return_value=ProviderStub()),
                mock.patch("ci_workflows.ci_private_apple.exact_checkout", side_effect=exact_checkout),
                mock.patch("ci_workflows.ci_private_apple.resolve_profile", return_value=ProfileStub()),
                mock.patch("ci_workflows.ci_private_apple.prepare_workspace") as prepare,
                mock.patch("ci_workflows.ci_private_apple.execute_apple_validate") as validate,
                mock.patch("ci_workflows.ci_private_apple.cleanup_workspace"),
                mock.patch("ci_workflows.ci_private_apple.upload_private_diagnostic", side_effect=upload),
            ):
                state_root = Path(env["RUNNER_TEMP"]) / "state-root"
                state_root.mkdir()
                prepare.return_value = mock.Mock(
                    root=state_root,
                    state_id="private-apple",
                    environment={
                        "CI_WORKFLOW_ROOT": str(state_root),
                        "CI_WORKFLOW_STATE_ID": "private-apple",
                    },
                )
                validate.side_effect = [
                    mock.Mock(outputs={"result": "success"}),
                    mock.Mock(outputs={"cleanup_result": "success"}),
                    mock.Mock(outputs={"cleanup_result": "success"}),
                ]
                execute_private_apple(env)

            self.assertEqual(events, ["upload", "finish"])
            finish_call = next(value for name, value in lifecycle.calls if name == "finish")
            kwargs = finish_call[1]  # type: ignore[index]
            self.assertEqual(kwargs["status"], "succeeded")
            self.assertEqual(kwargs["diagnostic_status"], "uploaded")
            self.assertTrue(str(kwargs["diagnostic_key"]).startswith("r2:ci-diagnostics/"))
            self.assertFalse((Path(env["RUNNER_TEMP"]) / f"ciw-private-ci-{CI_RUN_ID}").exists())

    def test_r2_failure_fails_closed_without_invented_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            lifecycle = LifecycleStub(claimed_run())
            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=lifecycle),
                mock.patch("ci_workflows.ci_private_apple.GitHubAppRepositoryTokenClient", return_value=AppClientStub()),
                mock.patch("ci_workflows.ci_private_apple.GitHubSourceProvider", return_value=ProviderStub()),
                mock.patch("ci_workflows.ci_private_apple.exact_checkout", side_effect=PrivateAppleError("source_checkout_failed")),
                mock.patch("ci_workflows.ci_private_apple.upload_private_diagnostic", side_effect=RuntimeError("storage down")),
            ):
                with self.assertRaisesRegex(PrivateAppleError, "private_log_upload_failed"):
                    execute_private_apple(env)
            self.assertFalse(any(name == "finish" for name, _value in lifecycle.calls))

    def test_recovery_uploads_interrupted_log_before_terminalizing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            private_root = Path(env["RUNNER_TEMP"]) / f"ciw-private-ci-{CI_RUN_ID}"
            private_root.mkdir(mode=0o700)
            state = {
                "ci_run_id": CI_RUN_ID,
                "started": True,
                "terminalized": False,
            }
            (private_root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            log = private_root / "private-ci.log"
            log.write_text("private detail\n", encoding="utf-8")
            lifecycle = LifecycleStub(claimed_run())
            events: list[str] = []

            def upload(**_kwargs: object) -> R2DiagnosticResult:
                events.append("upload")
                return R2DiagnosticResult(
                    object_key=f"ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz",
                    sha256="d" * 64,
                    compressed_bytes=50,
                )

            def finish(ci_run_id: str, **kwargs: object) -> None:
                events.append("finish")
                lifecycle.calls.append(("finish", (ci_run_id, kwargs)))

            lifecycle.finish = finish  # type: ignore[method-assign]
            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=lifecycle),
                mock.patch("ci_workflows.ci_private_apple.upload_private_diagnostic", side_effect=upload),
            ):
                recover_private_apple(env)
            self.assertEqual(events, ["upload", "finish"])
            finish_call = next(value for name, value in lifecycle.calls if name == "finish")
            kwargs = finish_call[1]  # type: ignore[index]
            self.assertEqual(kwargs["status"], "failed")
            self.assertEqual(kwargs["error_summary"], "private_ci_interrupted")
            self.assertEqual(kwargs["diagnostic_status"], "uploaded")
            self.assertFalse(private_root.exists())


class PrivateAppleSourceContractTests(unittest.TestCase):
    def test_action_and_workflow_expose_no_private_identity_fields(self) -> None:
        action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
        self.assertEqual(set(action["inputs"]), {"phase", "ci_run_id"})
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(dispatch), {"active_key", "ci_run_id"})
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
        self.assertIn("agent state remains", text)
        self.assertIn("stores no log body", text)
        self.assertIn("cloudflare d1 is not part", text)


if __name__ == "__main__":
    unittest.main()
