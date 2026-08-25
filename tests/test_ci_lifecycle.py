from __future__ import annotations

import json
from pathlib import Path
import unittest

import yaml

from ci_workflows.ci_lifecycle import (
    AgentStateCiClient,
    CiLifecycleError,
    WorkflowIdentity,
)

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/ci-lifecycle/action.yml"


class Response:
    def __init__(self, value: object) -> None:
        self.status = 200
        self._raw = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._raw if amount < 0 else self._raw[:amount]

    def getcode(self) -> int:
        return self.status


class RecordingOpener:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.registered_id = "00000000-0000-4000-8000-000000000099"

    def __call__(self, request: object, timeout: int = 0) -> Response:
        assert hasattr(request, "full_url")
        assert hasattr(request, "data")
        url = str(request.full_url)  # type: ignore[attr-defined]
        data = json.loads(bytes(request.data).decode("utf-8"))  # type: ignore[attr-defined]
        headers = dict(request.header_items())  # type: ignore[attr-defined]
        self.requests.append(
            {"url": url, "data": data, "headers": headers, "timeout": timeout}
        )
        if url.endswith("/register_ci_run"):
            return Response(
                {"ok": True, "run": {"ci_run_id": self.registered_id, "status": "running"}}
            )
        ci_run_id = data.get("p_ci_run_id", self.registered_id)
        patch = data.get("p_patch", {})
        return Response(
            {"ok": True, "run": {"ci_run_id": ci_run_id, **patch}}
        )


def environment() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        "GITHUB_RUN_ID": "32790000001",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SERVER_URL": "https://github.com",
    }


def identity(*, is_tag: bool = False, repository: str = "OtherOrg/private-app", ref: str = "develop") -> WorkflowIdentity:
    return WorkflowIdentity.from_values(
        project_key="private-app",
        repository=repository,
        ref=ref,
        is_tag=is_tag,
        workflow_key="validation.apple",
        profile="host",
        environment=environment(),
    )


class WorkflowIdentityTests(unittest.TestCase):
    def test_full_owner_repository_and_human_ref_build_run_identity(self) -> None:
        value = identity()
        self.assertEqual(value.repository, "OtherOrg/private-app")
        self.assertEqual(value.ref, "develop")
        self.assertFalse(value.is_tag)
        self.assertEqual(value.execution_repository, "StreamScapeTV/ci-workflows")
        self.assertEqual(value.run_id, 32790000001)
        self.assertEqual(value.run_attempt, 2)
        self.assertEqual(
            value.run_url,
            "https://github.com/StreamScapeTV/ci-workflows/actions/runs/32790000001",
        )
        self.assertNotIn("observed_source_sha", value.registration())

    def test_tag_is_explicit_and_not_guessed_from_ref_shape(self) -> None:
        value = identity(is_tag=True, ref="release/fancy-name")
        self.assertTrue(value.registration()["is_tag"])
        self.assertEqual(value.registration()["ref"], "release/fancy-name")

    def test_prefixed_git_ref_is_rejected(self) -> None:
        with self.assertRaisesRegex(CiLifecycleError, "invalid_ref"):
            identity(ref="refs/heads/develop")


class AgentStateCiClientTests(unittest.TestCase):
    def make_client(self) -> tuple[AgentStateCiClient, RecordingOpener]:
        opener = RecordingOpener()
        client = AgentStateCiClient(
            "https://example.supabase.co",
            "synthetic-secret",
            opener=opener,
        )
        return client, opener

    def test_webhook_run_start_transitions_existing_row_with_github_identity(self) -> None:
        client, opener = self.make_client()
        ci_run_id = "00000000-0000-4000-8000-000000000001"

        result = client.start(identity(), ci_run_id)

        self.assertEqual(result, ci_run_id)
        request = opener.requests[-1]
        self.assertTrue(str(request["url"]).endswith("/transition_ci_run"))
        patch = request["data"]["p_patch"]  # type: ignore[index]
        self.assertEqual(patch["status"], "running")
        self.assertEqual(patch["repository"], "OtherOrg/private-app")
        self.assertEqual(patch["ref"], "develop")
        self.assertIs(patch["is_tag"], False)
        self.assertEqual(patch["external_run_id"], 32790000001)
        self.assertEqual(patch["external_run_attempt"], 2)
        self.assertNotIn("observed_source_sha", patch)

    def test_native_tag_run_registers_new_short_lived_ci_row_without_sha_identity(self) -> None:
        client, opener = self.make_client()

        result = client.start(identity(is_tag=True, ref="ci-broker-1.0.5"))

        self.assertEqual(result, opener.registered_id)
        request = opener.requests[-1]
        self.assertTrue(str(request["url"]).endswith("/register_ci_run"))
        registration = request["data"]["p_registration"]  # type: ignore[index]
        self.assertEqual(registration["repository"], "OtherOrg/private-app")
        self.assertEqual(registration["ref"], "ci-broker-1.0.5")
        self.assertIs(registration["is_tag"], True)
        self.assertEqual(registration["workflow_key"], "validation.apple")
        self.assertEqual(registration["test_profile"], "host")
        self.assertNotIn("requested_source_sha", registration)
        self.assertNotIn("observed_source_sha", registration)

    def test_observed_sha_is_written_only_as_checkout_evidence(self) -> None:
        client, opener = self.make_client()
        ci_run_id = "00000000-0000-4000-8000-000000000001"
        sha = "a" * 40

        client.evidence(ci_run_id, sha)

        patch = opener.requests[-1]["data"]["p_patch"]  # type: ignore[index]
        self.assertEqual(patch, {"observed_source_sha": sha})

    def test_finish_writes_bounded_diagnostic_pointer_with_terminal_status(self) -> None:
        client, opener = self.make_client()
        ci_run_id = "00000000-0000-4000-8000-000000000001"

        client.finish(
            ci_run_id,
            status="failed",
            error_summary="tests_failed",
            diagnostic_status="stored",
            diagnostic_key="ci/32790000001/errors",
        )

        patch = opener.requests[-1]["data"]["p_patch"]  # type: ignore[index]
        self.assertEqual(
            patch,
            {
                "status": "failed",
                "error_summary": "tests_failed",
                "diagnostic_status": "stored",
                "diagnostic_key": "ci/32790000001/errors",
            },
        )
        self.assertNotIn("logs", patch)

    def test_private_mode_requires_verified_r2_receipt_before_terminal_rpc(self) -> None:
        opener = RecordingOpener()
        client = AgentStateCiClient.from_environment(
            {
                "AGENT_STATE_SUPABASE_URL": "https://example.supabase.co",
                "AGENT_STATE_SUPABASE_SECRET_KEY": "synthetic-secret",
                "CIW_PRIVATE_LOG_PATH": "/tmp/private.log",
            },
            opener=opener,
        )
        ci_run_id = "00000000-0000-4000-8000-000000000001"

        with self.assertRaisesRegex(CiLifecycleError, "private_log_not_uploaded"):
            client.finish(
                ci_run_id,
                status="failed",
                error_summary="tests_failed",
                diagnostic_status="failed",
                diagnostic_key=None,
            )
        self.assertEqual(opener.requests, [])

        receipt = (
            "r2:ci-diagnostics/00000000-0000-4000-8000-000000000001/"
            f"32790000001-2.log.gz#sha256={'a' * 64}"
        )
        client.finish(
            ci_run_id,
            status="failed",
            error_summary="tests_failed",
            diagnostic_status="uploaded",
            diagnostic_key=receipt,
        )
        patch = opener.requests[-1]["data"]["p_patch"]  # type: ignore[index]
        self.assertEqual(patch["diagnostic_status"], "uploaded")
        self.assertEqual(patch["diagnostic_key"], receipt)

    def test_rpc_uses_fixed_agent_api_headers_without_secret_in_payload(self) -> None:
        client, opener = self.make_client()
        client.start(identity())

        request = opener.requests[-1]
        headers = {str(key).lower(): value for key, value in request["headers"].items()}  # type: ignore[union-attr]
        self.assertEqual(headers["apikey"], "synthetic-secret")
        self.assertEqual(headers["content-profile"], "agent_api")
        self.assertEqual(headers["accept-profile"], "agent_api")
        self.assertNotIn("synthetic-secret", json.dumps(request["data"]))


class LifecycleActionTests(unittest.TestCase):
    def test_action_is_one_shared_transport_surface_without_secret_inputs(self) -> None:
        document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
        self.assertEqual(document["runs"]["using"], "composite")
        self.assertEqual(set(document["runs"]["steps"][0]["env"]), {
            "PYTHONDONTWRITEBYTECODE",
            "INPUT_PROJECT_KEY",
            "INPUT_REPOSITORY",
            "INPUT_REF",
            "INPUT_IS_TAG",
            "INPUT_WORKFLOW_KEY",
            "INPUT_PROFILE",
            "INPUT_CI_RUN_ID",
            "INPUT_OBSERVED_SHA",
            "INPUT_TERMINAL_STATUS",
            "INPUT_ERROR_SUMMARY",
            "INPUT_DIAGNOSTIC_STATUS",
            "INPUT_DIAGNOSTIC_KEY",
        })
        for forbidden in (
            "agent_state_supabase_url",
            "agent_state_supabase_secret_key",
            "secret_name",
            "log_body",
        ):
            self.assertNotIn(forbidden, document["inputs"])
        text = ACTION.read_text(encoding="utf-8")
        self.assertIn("scripts/ci/ciw.py", text)
        self.assertIn("lifecycle", text)
        self.assertNotIn("scripts/ci/ci_lifecycle.py", text)
        self.assertNotIn("secrets.", text)


if __name__ == "__main__":
    unittest.main()
