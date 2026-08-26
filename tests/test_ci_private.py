from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from ci_workflows.central_profile import CentralProfileResolution
from ci_workflows.ci_lifecycle import RelayRequest, WorkflowIdentity
from ci_workflows.ci_private import (
    PrivateCiError,
    _checkout_source,
    _execute_apple,
    _execution_environment,
    _receipt,
    execute_private_ci,
    recover_private_ci,
)
from ci_workflows.private_release_asset import (
    PrivateReleaseAssetResult,
    PrivateReleaseAssetSpec,
)
from ci_workflows.r2_diagnostics import R2DiagnosticResult

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


def claimed_request(
    *,
    workflow_key: str = "validation.apple",
    project_key: str = "iptv-apple",
) -> RelayRequest:
    return RelayRequest.from_claimed_run(
        {
            "ci_run_id": CI_RUN_ID,
            "project_key": project_key,
            "origin": "agent_request",
            "status": "accepted",
            "repository": "OtherOrg/private-app",
            "ref": "develop",
            "is_tag": False,
            "workflow_key": workflow_key,
            "test_profile": "host",
            "inputs": {},
            "requested_source_sha": None,
        }
    )


def resolution(
    *,
    canonical_inputs: dict[str, str] | None = None,
    release_asset: bool = False,
) -> CentralProfileResolution:
    values: dict[str, object] = {
        "project_key": "iptv-apple",
        "test_profile": "host",
        "workflow_key": "validation.apple",
        "capability": "apple-host-test",
        "source_repository": "OtherOrg/private-app",
        "admitted_sha": SOURCE_SHA,
        "validation_scope": "protected-full",
        "validation_plan_json": "{}",
        "executor_family": "macos",
        "canonical_inputs_json": json.dumps(
            canonical_inputs or {},
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    if release_asset:
        values.update(
            private_release_asset_repository="StreamScapeTV/example-media",
            private_release_asset_tag="v1.2.1",
            private_release_asset_commit_sha=RELEASE_COMMIT,
            private_release_asset_name="example-media-1.2.1-apple-binary.zip",
            private_release_asset_sha256=RELEASE_DIGEST,
            private_release_asset_archive_subpath="ExampleMediaApple",
            private_release_asset_destination="Vendor/ExampleMediaApple",
            private_release_asset_id="example-media-binary",
        )
    return CentralProfileResolution(**values)  # type: ignore[arg-type]


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
        "GITHUB_JOB": "private",
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


class GenericPrivateExecutorTests(unittest.TestCase):
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

    def test_execution_environment_removes_public_command_files(self) -> None:
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
            resolution=resolution(),
            dependency=None,
            base=base,
            workspace_environment={},
        )
        self.assertNotIn("GITHUB_OUTPUT", values)
        self.assertNotIn("GITHUB_ENV", values)
        self.assertNotIn("GITHUB_STEP_SUMMARY", values)
        self.assertEqual(values["GITHUB_REPOSITORY"], "OtherOrg/private-app")
        self.assertEqual(values["CIW_PRIVATE_LOG_PATH"], "/tmp/private.log")

    def test_source_checkout_delegates_to_shared_exact_checkout_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            stale = workspace / "source"
            stale.mkdir()
            (stale / "stale").write_text("x", encoding="utf-8")
            log = io.StringIO()
            with mock.patch(
                "ci_workflows.ci_private.exact_checkout",
                return_value={"head_sha": SOURCE_SHA, "verified": "true"},
            ) as checkout:
                result = _checkout_source(
                    repository="OtherOrg/private-app",
                    source_sha=SOURCE_SHA,
                    token="synthetic-token",
                    workspace=workspace,
                    log=log,
                )

            self.assertEqual(result, workspace / "source")
            self.assertFalse((workspace / "source" / "stale").exists())
            checkout.assert_called_once_with(
                repository="OtherOrg/private-app",
                admitted_sha=SOURCE_SHA,
                path="source",
                fetch_depth=1,
                token="synthetic-token",
                workspace=workspace,
            )

    def test_apple_adapter_projects_bounded_inputs_without_transport_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            env["INPUT_PHASE"] = "execute"
            env["DO_NOT_COPY"] = "preserved"
            state_root = root / "state"
            state_root.mkdir()
            state = SimpleNamespace(
                root=state_root,
                state_id="workspace-state",
                environment={"APPLE_WORKSPACE_STATE": "ready"},
            )
            selected = resolution(
                canonical_inputs={
                    "command_profile": "streamscape-media-apple",
                    "validation_profile": "swift-package",
                }
            )
            execute_environments: list[dict[str, str]] = []

            def apple_validate(args: object, context: object) -> SimpleNamespace:
                phase = getattr(args, "phase")
                if phase == "execute":
                    execute_environments.append(dict(context.environment))  # type: ignore[attr-defined]
                    return SimpleNamespace(outputs={"result": "success"})
                return SimpleNamespace(outputs={"result": "success"})

            with (
                mock.patch("ci_workflows.ci_private._workspace_state", return_value=state),
                mock.patch("ci_workflows.ci_private._dependency_receipts", return_value=None),
                mock.patch("ci_workflows.ci_private.execute_apple_validate", side_effect=apple_validate),
                mock.patch("ci_workflows.ci_private.cleanup_workspace"),
                mock.patch("ci_workflows.ci_private._append_state_logs"),
            ):
                self.assertEqual(
                    _execute_apple(
                        request=claimed_request(),
                        source_sha=SOURCE_SHA,
                        resolution=selected,
                        token_client=TokenClientStub(),
                        environment=env,
                        log=io.StringIO(),
                    ),
                    (True, True),
                )

            forwarded = execute_environments[0]
            self.assertEqual(
                forwarded["INPUT_COMMAND_PROFILE"], "streamscape-media-apple"
            )
            self.assertEqual(forwarded["INPUT_VALIDATION_PROFILE"], "swift-package")
            self.assertEqual(forwarded["APPLE_WORKSPACE_STATE"], "ready")
            self.assertEqual(forwarded["DO_NOT_COPY"], "preserved")
            self.assertNotIn("INPUT_CI_RUN_ID", forwarded)
            self.assertNotIn("INPUT_PHASE", forwarded)
            self.assertNotIn("INPUT_EXECUTION_BACKEND", forwarded)
            self.assertNotIn("GITHUB_OUTPUT", forwarded)
            self.assertNotIn("GITHUB_ENV", forwarded)
            self.assertNotIn("GITHUB_STEP_SUMMARY", forwarded)

    def test_success_uploads_r2_before_terminal_agent_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = environment(root)
            lifecycle = LifecycleStub()
            request = claimed_request()
            events: list[str] = []

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
                mock.patch(
                    "ci_workflows.ci_private.AgentStateCiClient.from_environment",
                    return_value=lifecycle,
                ),
                mock.patch("ci_workflows.ci_private._claim_request", return_value=request),
                mock.patch("ci_workflows.ci_private._start_lifecycle"),
                mock.patch(
                    "ci_workflows.ci_private._source_token_client",
                    return_value=TokenClientStub(),
                ),
                mock.patch(
                    "ci_workflows.ci_private._resolve_source_sha",
                    return_value=SOURCE_SHA,
                ),
                mock.patch("ci_workflows.ci_private._checkout_source", side_effect=checkout),
                mock.patch("ci_workflows.ci_private.resolve_profile", return_value=resolution()),
                mock.patch("ci_workflows.ci_private._execute_family", return_value=(True, True)),
                mock.patch(
                    "ci_workflows.ci_private._upload_private_log",
                    side_effect=upload,
                ),
            ):
                self.assertTrue(execute_private_ci(env))

            self.assertEqual(events, ["upload", "finish"])
            finish_call = next(value for name, value in lifecycle.calls if name == "finish")
            kwargs = finish_call[1]  # type: ignore[index]
            self.assertEqual(kwargs["status"], "succeeded")
            self.assertEqual(kwargs["diagnostic_status"], "uploaded")
            self.assertTrue(
                str(kwargs["diagnostic_key"]).startswith("r2:ci-diagnostics/")
            )

    def test_release_asset_cleans_before_r2_terminalization(self) -> None:
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
                self.assertIsInstance(selected, PrivateReleaseAssetSpec)
                return PrivateReleaseAssetResult(
                    source_root=source,
                    destination=destination,
                    release_commit=selected.commit_sha,
                    sha256=selected.sha256,
                    downloaded_bytes=123,
                )

            def validate(**_kwargs: object) -> tuple[bool, bool]:
                events.append("validate")
                self.assertTrue(destination_holder["path"].is_dir())
                return True, True

            def cleanup(result: PrivateReleaseAssetResult) -> None:
                events.append("release-cleanup")
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
                mock.patch(
                    "ci_workflows.ci_private.AgentStateCiClient.from_environment",
                    return_value=lifecycle,
                ),
                mock.patch("ci_workflows.ci_private._claim_request", return_value=request),
                mock.patch("ci_workflows.ci_private._start_lifecycle"),
                mock.patch(
                    "ci_workflows.ci_private._source_token_client",
                    return_value=token_client,
                ),
                mock.patch(
                    "ci_workflows.ci_private._resolve_source_sha",
                    return_value=SOURCE_SHA,
                ),
                mock.patch("ci_workflows.ci_private._checkout_source", side_effect=checkout),
                mock.patch(
                    "ci_workflows.ci_private.resolve_profile",
                    return_value=resolution(release_asset=True),
                ),
                mock.patch(
                    "ci_workflows.ci_private.materialize_private_release_asset",
                    side_effect=materialize,
                ),
                mock.patch("ci_workflows.ci_private._execute_family", side_effect=validate),
                mock.patch(
                    "ci_workflows.ci_private.cleanup_private_release_asset",
                    side_effect=cleanup,
                ),
                mock.patch(
                    "ci_workflows.ci_private._upload_private_log",
                    side_effect=upload,
                ),
            ):
                self.assertTrue(execute_private_ci(env))

            self.assertEqual(
                events,
                ["materialize", "validate", "release-cleanup", "upload", "finish"],
            )
            self.assertEqual(
                token_client.repositories,
                ["OtherOrg/private-app", "StreamScapeTV/example-media"],
            )

    def test_r2_failure_cannot_succeed_or_publish_a_diagnostic_pointer(self) -> None:
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
                mock.patch(
                    "ci_workflows.ci_private.AgentStateCiClient.from_environment",
                    return_value=lifecycle,
                ),
                mock.patch("ci_workflows.ci_private._claim_request", return_value=request),
                mock.patch("ci_workflows.ci_private._start_lifecycle"),
                mock.patch(
                    "ci_workflows.ci_private._source_token_client",
                    return_value=TokenClientStub(),
                ),
                mock.patch(
                    "ci_workflows.ci_private._resolve_source_sha",
                    return_value=SOURCE_SHA,
                ),
                mock.patch("ci_workflows.ci_private._checkout_source", side_effect=checkout),
                mock.patch("ci_workflows.ci_private.resolve_profile", return_value=resolution()),
                mock.patch("ci_workflows.ci_private._execute_family", return_value=(False, True)),
                mock.patch(
                    "ci_workflows.ci_private._upload_private_log",
                    side_effect=PrivateCiError("r2_unavailable"),
                ),
            ):
                self.assertFalse(execute_private_ci(env))

            finish_calls = [value for name, value in lifecycle.calls if name == "finish"]
            self.assertEqual(len(finish_calls), 1)
            kwargs = finish_calls[0][1]  # type: ignore[index]
            self.assertEqual(kwargs["status"], "failed")
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
                mock.patch(
                    "ci_workflows.ci_private.AgentStateCiClient.from_environment",
                    return_value=lifecycle,
                ),
                mock.patch(
                    "ci_workflows.ci_private._upload_private_log", side_effect=upload
                ),
            ):
                recover_private_ci(env)

            self.assertEqual(events, ["upload", "finish"])
            finish_call = next(value for name, value in lifecycle.calls if name == "finish")
            kwargs = finish_call[1]  # type: ignore[index]
            self.assertEqual(kwargs["status"], "failed")
            self.assertEqual(kwargs["error_summary"], "private_ci_interrupted")
            self.assertEqual(kwargs["diagnostic_status"], "uploaded")
            self.assertFalse(private_root.exists())


if __name__ == "__main__":
    unittest.main()
