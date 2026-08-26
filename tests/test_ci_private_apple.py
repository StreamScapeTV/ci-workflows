from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import yaml

from ci_workflows.ci_lifecycle import RelayRequest, WorkflowIdentity
from ci_workflows.ci_private_apple import (
    PrivateAppleCiError,
    _execution_environment,
    _receipt,
    execute_private_apple,
    recover_private_apple,
)
from ci_workflows.private_release_asset import PrivateReleaseAssetResult, PrivateReleaseAssetSpec
from ci_workflows.r2_diagnostics import R2DiagnosticResult
from ci_workflows.validation_model import load_actions_yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/private-apple-ci/action.yml"
WORKFLOW = ROOT / ".github/workflows/central-ci-dispatch.yml"
CONTRACT = ROOT / "contracts/ci-diagnostics.json"
DOC = ROOT / "docs/workflows/ci-diagnostics.md"
CI_RUN_ID = "11111111-2222-4333-8444-555555555555"
SOURCE_SHA = "a" * 40
RELEASE_COMMIT = "b" * 40
RELEASE_DIGEST = "c" * 64


class LifecycleStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def start(self, identity: WorkflowIdentity, ci_run_id: str | None = None) -> str:
        self.calls.append(("start", (identity, ci_run_id)))
        return ci_run_id or CI_RUN_ID

    def evidence(self, ci_run_id: str, observed_sha: str) -> None:
        self.calls.append(("evidence", (ci_run_id, observed_sha)))

    def finish(self, ci_run_id: str, **kwargs: object) -> None:
        self.calls.append(("finish", (ci_run_id, kwargs)))


class TokenClientStub:
    def __init__(self) -> None:
        self.repositories: list[object] = []

    def repository_contents_read_token(self, repository: object) -> str:
        self.repositories.append(repository)
        return "synthetic-repository-token"


class ProfileStub:
    capability = "apple-host-test"
    validation_scope = "protected-full"
    validation_plan_json = "{}"
    private_dependency_repository = ""
    private_dependency_sha = ""
    private_dependency_subdirectory = "."
    private_dependency_id = ""

    def release_asset(self) -> PrivateReleaseAssetSpec | None:
        return None


class ReleaseProfileStub(ProfileStub):
    def release_asset(self) -> PrivateReleaseAssetSpec:
        return PrivateReleaseAssetSpec.parse(
            {
                "repository": "StreamScapeTV/example-media",
                "tag": "v1.2.1",
                "commit_sha": RELEASE_COMMIT,
                "asset_name": "example-media-1.2.1-apple-binary.zip",
                "sha256": RELEASE_DIGEST,
                "archive_subpath": "ExampleMediaApple",
                "destination": "Vendor/ExampleMediaApple",
                "id": "example-media-binary",
            }
        )


def claimed_request() -> RelayRequest:
    return RelayRequest.from_claimed_run(
        {
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
    )


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
        "CIW_PRIVATE_LOG_PATH": str(
            runner_temp / "central-private-ci" / CI_RUN_ID / "private.log"
        ),
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
        receipt = _receipt(result)
        self.assertEqual(
            receipt,
            f"r2:ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz#sha256={'b' * 64}",
        )
        self.assertNotIn("OtherOrg", receipt)
        self.assertNotIn("develop", receipt)

    def test_execution_environment_removes_public_command_files_and_keeps_private_log_binding(self) -> None:
        base = {
            "GITHUB_OUTPUT": "/tmp/output",
            "GITHUB_ENV": "/tmp/env",
            "GITHUB_STEP_SUMMARY": "/tmp/summary",
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "CIW_PRIVATE_LOG_PATH": "/tmp/private.log",
        }
        values = _execution_environment(
            request=claimed_request(),
            source_sha=SOURCE_SHA,
            resolution=ProfileStub(),  # type: ignore[arg-type]
            dependency=None,
            base=base,
            workspace_environment={},
        )
        self.assertNotIn("GITHUB_OUTPUT", values)
        self.assertNotIn("GITHUB_ENV", values)
        self.assertNotIn("GITHUB_STEP_SUMMARY", values)
        self.assertEqual(values["GITHUB_REPOSITORY"], "OtherOrg/private-app")
        self.assertEqual(values["CIW_PRIVATE_LOG_PATH"], "/tmp/private.log")

    def test_success_uploads_r2_before_terminal_agent_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            lifecycle = LifecycleStub()
            request = claimed_request()
            events: list[str] = []

            def start(client, selected, selected_environment):
                self.assertIs(client, lifecycle)
                self.assertEqual(selected.ci_run_id, CI_RUN_ID)
                lifecycle.calls.append(("start", selected.ci_run_id))

            def checkout(**kwargs: object) -> Path:
                source = Path(kwargs["workspace"]) / "source"
                source.mkdir()
                return source

            def upload(**kwargs: object) -> R2DiagnosticResult:
                events.append("upload")
                self.assertTrue(Path(kwargs["log_path"]).is_file())
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
                mock.patch("ci_workflows.ci_private_apple._claim_request", return_value=request),
                mock.patch("ci_workflows.ci_private_apple._start_lifecycle", side_effect=start),
                mock.patch("ci_workflows.ci_private_apple._source_token_client", return_value=TokenClientStub()),
                mock.patch("ci_workflows.ci_private_apple._resolve_source_sha", return_value=SOURCE_SHA),
                mock.patch("ci_workflows.ci_private_apple._checkout_source", side_effect=checkout),
                mock.patch("ci_workflows.ci_private_apple.resolve_profile", return_value=ProfileStub()),
                mock.patch("ci_workflows.ci_private_apple._execute_validation", return_value=(True, True)),
                mock.patch("ci_workflows.ci_private_apple._r2_upload", side_effect=upload),
            ):
                self.assertTrue(execute_private_apple(env))

            self.assertEqual(events, ["upload", "finish"])
            finish_call = next(value for name, value in lifecycle.calls if name == "finish")
            kwargs = finish_call[1]  # type: ignore[index]
            self.assertEqual(kwargs["status"], "succeeded")
            self.assertEqual(kwargs["diagnostic_status"], "uploaded")
            self.assertTrue(str(kwargs["diagnostic_key"]).startswith("r2:ci-diagnostics/"))

    def test_release_asset_materializes_before_apple_and_cleans_before_r2_terminalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            lifecycle = LifecycleStub()
            request = claimed_request()
            token_client = TokenClientStub()
            events: list[str] = []
            destination_holder: dict[str, Path] = {}

            def checkout(**kwargs: object) -> Path:
                source = Path(kwargs["workspace"]) / "source"
                source.mkdir()
                return source

            def materialize(**kwargs: object) -> PrivateReleaseAssetResult:
                events.append("materialize")
                source = Path(kwargs["source_root"])
                destination = source / "Vendor/ExampleMediaApple"
                destination.mkdir(parents=True)
                destination_holder["path"] = destination
                selected = kwargs["spec"]
                return PrivateReleaseAssetResult(
                    source_root=source,
                    destination=destination,
                    release_commit=selected.commit_sha,  # type: ignore[union-attr]
                    sha256=selected.sha256,  # type: ignore[union-attr]
                    downloaded_bytes=123,
                )

            def validate(**_kwargs: object) -> tuple[bool, bool]:
                events.append("validate")
                self.assertTrue(destination_holder["path"].is_dir())
                return True, True

            def cleanup(result: PrivateReleaseAssetResult | None) -> None:
                events.append("release-cleanup")
                assert result is not None
                shutil.rmtree(result.destination)

            def upload(**_kwargs: object) -> R2DiagnosticResult:
                events.append("upload")
                self.assertFalse(destination_holder["path"].exists())
                return R2DiagnosticResult(
                    object_key=f"ci-diagnostics/{CI_RUN_ID}/32860000001-1.log.gz",
                    sha256="e" * 64,
                    compressed_bytes=100,
                )

            def finish(ci_run_id: str, **kwargs: object) -> None:
                events.append("finish")
                lifecycle.calls.append(("finish", (ci_run_id, kwargs)))

            lifecycle.finish = finish  # type: ignore[method-assign]
            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=lifecycle),
                mock.patch("ci_workflows.ci_private_apple._claim_request", return_value=request),
                mock.patch("ci_workflows.ci_private_apple._start_lifecycle"),
                mock.patch("ci_workflows.ci_private_apple._source_token_client", return_value=token_client),
                mock.patch("ci_workflows.ci_private_apple._resolve_source_sha", return_value=SOURCE_SHA),
                mock.patch("ci_workflows.ci_private_apple._checkout_source", side_effect=checkout),
                mock.patch("ci_workflows.ci_private_apple.resolve_profile", return_value=ReleaseProfileStub()),
                mock.patch("ci_workflows.ci_private_apple.materialize_private_release_asset", side_effect=materialize),
                mock.patch("ci_workflows.ci_private_apple._execute_validation", side_effect=validate),
                mock.patch("ci_workflows.ci_private_apple.cleanup_private_release_asset", side_effect=cleanup),
                mock.patch("ci_workflows.ci_private_apple._r2_upload", side_effect=upload),
            ):
                self.assertTrue(execute_private_apple(env))

            self.assertEqual(events, ["materialize", "validate", "release-cleanup", "upload", "finish"])
            self.assertEqual(
                token_client.repositories,
                ["OtherOrg/private-app", "StreamScapeTV/example-media"],
            )

    def test_r2_failure_cannot_terminalize_private_ci_without_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            request = claimed_request()
            lifecycle = LifecycleStub()

            def checkout(**kwargs: object) -> Path:
                source = Path(kwargs["workspace"]) / "source"
                source.mkdir()
                return source

            with (
                mock.patch("ci_workflows.ci_private_apple.AgentStateCiClient.from_environment", return_value=lifecycle),
                mock.patch("ci_workflows.ci_private_apple._claim_request", return_value=request),
                mock.patch("ci_workflows.ci_private_apple._start_lifecycle"),
                mock.patch("ci_workflows.ci_private_apple._source_token_client", return_value=TokenClientStub()),
                mock.patch("ci_workflows.ci_private_apple._resolve_source_sha", return_value=SOURCE_SHA),
                mock.patch("ci_workflows.ci_private_apple._checkout_source", side_effect=checkout),
                mock.patch("ci_workflows.ci_private_apple.resolve_profile", return_value=ProfileStub()),
                mock.patch("ci_workflows.ci_private_apple._execute_validation", return_value=(False, True)),
                mock.patch("ci_workflows.ci_private_apple._r2_upload", side_effect=PrivateAppleCiError("r2_unavailable")),
            ):
                self.assertFalse(execute_private_apple(env))

            finish_calls = [value for name, value in lifecycle.calls if name == "finish"]
            self.assertEqual(len(finish_calls), 1)
            kwargs = finish_calls[0][1]  # type: ignore[index]
            self.assertEqual(kwargs["diagnostic_status"], "failed")
            self.assertIsNone(kwargs["diagnostic_key"])

    def test_recovery_uploads_interrupted_log_before_terminalizing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            private_root = Path(env["RUNNER_TEMP"]) / "central-private-ci" / CI_RUN_ID
            private_root.mkdir(parents=True, mode=0o700)
            (private_root / "state.json").write_text(
                json.dumps(
                    {
                        "ci_run_id": CI_RUN_ID,
                        "started": True,
                        "terminalized": False,
                    }
                ),
                encoding="utf-8",
            )
            log = private_root / "private.log"
            log.write_text("private detail\n", encoding="utf-8")
            log.chmod(0o600)
            lifecycle = LifecycleStub()
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
                mock.patch("ci_workflows.ci_private_apple._r2_upload", side_effect=upload),
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
        action_text = ACTION.read_text(encoding="utf-8")
        self.assertIn("CIW_PRIVATE_LOG_PATH", action_text)
        self.assertIn("runner.temp", action_text)
        workflow = load_actions_yaml(WORKFLOW, ROOT).data
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
        self.assertTrue(contract["write_policy"]["read_back_after_upload"])
        self.assertTrue(contract["write_policy"]["sha256_verify_read_back"])
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
