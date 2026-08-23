from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from typing import Any

from ci_workflows.ci_broker import (
    AgentStateClient,
    BrokerConfig,
    BrokerError,
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    CiBroker,
    GitHubAppClient,
    OpaqueEnvelope,
    ProductConfig,
    TOKEN_TTL_SECONDS,
)


class Response:
    def __init__(self, value: object, status: int = 200) -> None:
        self.status = status
        self._raw = json.dumps(value).encode("utf-8") if value is not None else b""

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._raw if amount < 0 else self._raw[:amount]

    def getcode(self) -> int:
        return self.status


class AgentStateStub:
    def __init__(self) -> None:
        self.claim_result: dict[str, Any] | None = None
        self.get_result: dict[str, Any] | None = None
        self.list_result: dict[str, Any] = {"ok": True, "runs": []}
        self.transitions: list[tuple[str, dict[str, object]]] = []
        self.registrations: list[dict[str, object]] = []

    def claim(self, _ci_run_id: str) -> dict[str, Any]:
        assert self.claim_result is not None
        return self.claim_result

    def transition(self, ci_run_id: str, patch: dict[str, object]) -> dict[str, Any]:
        self.transitions.append((ci_run_id, patch))
        run: dict[str, object] = {"ci_run_id": ci_run_id, **patch}
        if self.get_result and isinstance(self.get_result.get("run"), dict):
            run = {**self.get_result["run"], **patch}
        return {"ok": True, "code": "ok", "run": run}

    def register(self, registration: dict[str, object]) -> dict[str, Any]:
        self.registrations.append(registration)
        return {
            "ok": True,
            "code": "ok",
            "run": {
                "ci_run_id": "00000000-0000-4000-8000-000000000099",
                **registration,
                "status": "running",
            },
        }

    def get(self, _project_key: str, _ci_run_id: str) -> dict[str, Any]:
        assert self.get_result is not None
        return self.get_result

    def list(self, _project_key: str, _limit: int = 100) -> dict[str, Any]:
        return self.list_result


class SourceGithubStub:
    def __init__(self) -> None:
        self.config: dict[str, object] = sample_config()
        self.commit_message = "normal change"

    def repository_token(self, _repository: str) -> str:
        return "synthetic-source-token"

    def installation_token(self, _installation_id: int) -> str:
        return "synthetic-source-token"

    def get_commit(self, _repository: str, ref: str, _token: str) -> dict[str, Any]:
        sha = ref if len(ref) == 40 else "a" * 40
        return {"sha": sha, "commit": {"message": self.commit_message}}

    def get_private_config(self, _repository: str, _sha: str, _token: str) -> dict[str, Any]:
        return self.config


class DispatchGithubStub:
    def __init__(self) -> None:
        self.dispatches: list[dict[str, object]] = []
        self.next_run_id = 901

    def repository_token(self, _repository: str) -> str:
        return "synthetic-dispatch-token"

    def workflow(self, _repository: str, _workflow: str, _token: str) -> dict[str, Any]:
        return {"id": 801}

    def dispatch(self, **kwargs: object) -> dict[str, Any]:
        self.dispatches.append(dict(kwargs))
        return {"workflow_run_id": self.next_run_id, "run_url": "opaque", "html_url": "opaque"}


class OidcStub:
    def __init__(self, run_id: int = 901) -> None:
        self.run_id = run_id

    def verify(self, _token: str) -> dict[str, object]:
        return {"run_id": str(self.run_id)}


def sample_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "project_key": "sample-project",
        "profiles": {
            "host": {
                "workflow_key": "validation.apple",
                "capability": "apple-host-test",
                "workspace": "Sample.xcworkspace",
                "scheme": "Sample",
                "test_target": "SampleTests/SelectedIntegrationTests",
            }
        },
        "automatic": {"push": "host", "tag": "host"},
    }


def broker_config() -> BrokerConfig:
    return BrokerConfig(
        source_app_id=1,
        source_app_private_key="synthetic-private-key",
        source_webhook_secret="source-webhook-secret",
        dispatch_app_id=2,
        dispatch_app_private_key="synthetic-dispatch-key",
        agent_state_url="https://example.invalid",
        agent_state_secret_key="synthetic-supabase-key",
        agent_state_webhook_secret="agent-state-webhook-secret",
        r2_account_id="0" * 32,
        r2_bucket="synthetic-bucket",
        r2_read_access_key_id="synthetic-read-key",
        r2_read_secret_access_key="synthetic-read-secret",
    )


def agent_run(status: str = "accepted", external_run_id: int | None = None) -> dict[str, Any]:
    return {
        "ci_run_id": "00000000-0000-4000-8000-000000000001",
        "project_key": "sample-project",
        "origin": "agent_request",
        "status": status,
        "repository": "example/private-source",
        "external_repository": CENTRAL_REPOSITORY if external_run_id else None,
        "ref": "refs/heads/main",
        "workflow_key": "validation.apple",
        "test_profile": "host",
        "inputs": {},
        "requested_source_sha": None,
        "resolved_source_sha": "a" * 40 if external_run_id else None,
        "external_run_id": external_run_id,
    }


class ProductConfigTests(unittest.TestCase):
    def test_bounded_profile_parses_and_forbidden_execution_fields_fail_closed(self) -> None:
        parsed = ProductConfig.parse(sample_config())
        self.assertEqual(parsed.project_key, "sample-project")
        self.assertEqual(parsed.profile("host", "validation.apple").capability, "apple-host-test")

        command = sample_config()
        command["profiles"] = {
            "host": {
                **command["profiles"]["host"],  # type: ignore[index]
                "command": "xcodebuild whatever",
            }
        }
        with self.assertRaisesRegex(BrokerError, "private_ci_profile_invalid"):
            ProductConfig.parse(command)

        traversal = sample_config()
        traversal["profiles"] = {
            "host": {
                **traversal["profiles"]["host"],  # type: ignore[index]
                "workspace": "../Private.xcworkspace",
            }
        }
        with self.assertRaisesRegex(BrokerError, "invalid_workspace"):
            ProductConfig.parse(traversal)

    def test_unknown_capability_and_automatic_profile_are_rejected(self) -> None:
        capability = sample_config()
        capability["profiles"] = {
            "host": {
                **capability["profiles"]["host"],  # type: ignore[index]
                "capability": "shell",
            }
        }
        with self.assertRaisesRegex(BrokerError, "private_ci_capability_unsupported"):
            ProductConfig.parse(capability)

        automatic = sample_config()
        automatic["automatic"] = {"push": "missing"}
        with self.assertRaisesRegex(BrokerError, "private_ci_automatic_profile_missing"):
            ProductConfig.parse(automatic)


class EnvelopeTests(unittest.TestCase):
    def test_envelope_round_trip_tamper_wrong_id_and_expiry(self) -> None:
        envelope = OpaqueEnvelope("synthetic-secret")
        dedupe = {"kind": "agent_request", "project_key": "sample"}
        dispatch_id = envelope.dispatch_id(dedupe)
        payload = {"dedupe": dedupe, "profile": {"name": "host"}, "exp": 1_000 + 3_600}
        token = envelope.seal(dispatch_id, payload)
        self.assertEqual(envelope.open(dispatch_id, token, now=1_000), payload)

        index = len(token) // 2
        replacement = "A" if token[index] != "A" else "B"
        with self.assertRaisesRegex(BrokerError, "invalid_dispatch_token"):
            envelope.open(dispatch_id, token[:index] + replacement + token[index + 1 :], now=1_000)
        with self.assertRaisesRegex(BrokerError, "invalid_dispatch_token"):
            envelope.open(envelope.dispatch_id({"other": True}), token, now=1_000)
        with self.assertRaisesRegex(BrokerError, "dispatch_token_expired"):
            envelope.open(dispatch_id, token, now=5_000)
        self.assertEqual(TOKEN_TTL_SECONDS, 21_600)


class TransportTests(unittest.TestCase):
    def test_agent_state_transition_uses_secret_key_profile_and_p_patch(self) -> None:
        captured: dict[str, object] = {}

        def opener(request: Any, timeout: int) -> Response:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Response({"ok": True, "run": {}})

        client = AgentStateClient("https://example.invalid", "synthetic-secret", opener=opener)
        client.transition("00000000-0000-4000-8000-000000000001", {"status": "running"})
        self.assertTrue(str(captured["url"]).endswith("/rest/v1/rpc/transition_ci_run"))
        headers = {str(k).lower(): v for k, v in captured["headers"].items()}  # type: ignore[union-attr]
        self.assertEqual(headers["apikey"], "synthetic-secret")
        self.assertEqual(headers["content-profile"], "agent_api")
        self.assertEqual(
            captured["body"],
            {
                "p_ci_run_id": "00000000-0000-4000-8000-000000000001",
                "p_patch": {"status": "running"},
            },
        )

    def test_github_dispatch_requests_run_details_and_parses_workflow_run_id(self) -> None:
        class Client(GitHubAppClient):
            def __init__(self) -> None:
                self.body: dict[str, object] | None = None

            def _request(self, method: str, path: str, **kwargs: object) -> tuple[int, object]:
                self.body = kwargs["body"]  # type: ignore[assignment]
                self.assertions = (method, path, kwargs.get("expected"))
                return 200, {"workflow_run_id": 321, "run_url": "x", "html_url": "y"}

        client = Client()
        value = client.dispatch(
            repository=CENTRAL_REPOSITORY,
            workflow=CENTRAL_WORKFLOW,
            ref="main",
            inputs={"dispatch_id": "opaque", "dispatch_token": "opaque"},
            token="synthetic",
        )
        self.assertEqual(value["workflow_run_id"], 321)  # type: ignore[index]
        self.assertEqual(client.body["return_run_details"], True)  # type: ignore[index]
        self.assertEqual(client.body["ref"], "main")  # type: ignore[index]


class WebhookAndLifecycleTests(unittest.TestCase):
    def make_broker(self) -> tuple[CiBroker, AgentStateStub, SourceGithubStub, DispatchGithubStub]:
        state = AgentStateStub()
        source = SourceGithubStub()
        dispatch = DispatchGithubStub()
        broker = CiBroker(
            broker_config(),
            agent_state=state,  # type: ignore[arg-type]
            source_github=source,  # type: ignore[arg-type]
            dispatch_github=dispatch,  # type: ignore[arg-type]
            oidc=OidcStub(),  # type: ignore[arg-type]
        )
        return broker, state, source, dispatch

    @staticmethod
    def agent_webhook(ci_run_id: str) -> bytes:
        return json.dumps(
            {
                "type": "INSERT",
                "schema": "agent_private",
                "table": "ci_runs",
                "record": {"ci_run_id": ci_run_id, "origin": "agent_request", "status": "requested"},
            },
            separators=(",", ":"),
        ).encode()

    def test_agent_webhook_dispatches_and_records_central_execution_repository(self) -> None:
        broker, state, _source, dispatch = self.make_broker()
        run = agent_run()
        state.claim_result = {"ok": True, "run": run, "replayed": False}
        raw = self.agent_webhook(run["ci_run_id"])
        result = broker.handle_agent_state_webhook(
            raw,
            {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
        )
        self.assertTrue(result["dispatched"])
        self.assertEqual(len(dispatch.dispatches), 1)
        queued = state.transitions[-1][1]
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["external_repository"], CENTRAL_REPOSITORY)
        self.assertEqual(queued["external_run_id"], 901)
        self.assertEqual(
            queued["external_run_url"],
            f"https://github.com/{CENTRAL_REPOSITORY}/actions/runs/901",
        )

    def test_replayed_accepted_request_recovers_dispatch_but_queued_request_does_not(self) -> None:
        broker, state, _source, dispatch = self.make_broker()
        accepted = agent_run()
        state.claim_result = {"ok": True, "run": accepted, "replayed": True}
        raw = self.agent_webhook(accepted["ci_run_id"])
        broker.handle_agent_state_webhook(
            raw,
            {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
        )
        self.assertEqual(len(dispatch.dispatches), 1)

        queued = agent_run("queued", 901)
        state.claim_result = {"ok": True, "run": queued, "replayed": True}
        result = broker.handle_agent_state_webhook(
            raw,
            {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
        )
        self.assertTrue(result["replayed"])
        self.assertEqual(len(dispatch.dispatches), 1)

    def test_bad_agent_webhook_secret_and_admission_failure_fail_closed(self) -> None:
        broker, state, source, _dispatch = self.make_broker()
        run = agent_run()
        raw = self.agent_webhook(run["ci_run_id"])
        with self.assertRaisesRegex(BrokerError, "agent_state_webhook_unauthorized"):
            broker.handle_agent_state_webhook(raw, {"X-StreamScape-Webhook-Secret": "wrong"})

        state.claim_result = {"ok": True, "run": run, "replayed": False}
        source.config = {"schema_version": 1, "project_key": "wrong", "profiles": {}, "automatic": {}}
        with self.assertRaises(BrokerError):
            broker.handle_agent_state_webhook(
                raw,
                {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
            )
        self.assertEqual(state.transitions[-1][1]["status"], "cancelled")

    def test_github_push_hmac_skip_and_tag_dispatch(self) -> None:
        broker, _state, source, dispatch = self.make_broker()
        payload = {
            "ref": "refs/heads/main",
            "after": "a" * 40,
            "deleted": False,
            "repository": {"full_name": "example/private-source"},
            "installation": {"id": 22},
            "head_commit": {"message": "change [skip ci]"},
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            b"source-webhook-secret", raw, hashlib.sha256
        ).hexdigest()
        result = broker.handle_github_webhook(
            raw,
            {"X-GitHub-Event": "push", "X-Hub-Signature-256": signature},
        )
        self.assertTrue(result["skip_ci"])
        self.assertEqual(dispatch.dispatches, [])

        payload["ref"] = "refs/tags/v1.0.0"
        payload["head_commit"] = None
        source.commit_message = "release"
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            b"source-webhook-secret", raw, hashlib.sha256
        ).hexdigest()
        result = broker.handle_github_webhook(
            raw,
            {"X-GitHub-Event": "push", "X-Hub-Signature-256": signature},
        )
        self.assertTrue(result["dispatched"])
        self.assertEqual(len(dispatch.dispatches), 1)

    def test_action_start_uses_distinct_central_execution_identity(self) -> None:
        broker, state, _source, _dispatch = self.make_broker()
        run = agent_run()
        state.get_result = {"ok": True, "run": run}
        dedupe = {
            "kind": "agent_request",
            "project_key": "sample-project",
            "repository": "example/private-source",
            "ref": "refs/heads/main",
            "source_sha": "a" * 40,
            "workflow_key": "validation.apple",
            "test_profile": "host",
            "trigger_kind": "agent_dispatch",
            "ci_run_id": run["ci_run_id"],
        }
        dispatch_id = broker.envelopes.dispatch_id(dedupe)
        token = broker.envelopes.seal(
            dispatch_id,
            {
                "dedupe": dedupe,
                "profile": {
                    "name": "host",
                    "workflow_key": "validation.apple",
                    "capability": "apple-host-test",
                    "workspace": "Sample.xcworkspace",
                    "scheme": "Sample",
                    "test_target": "SampleTests/SelectedIntegrationTests",
                },
                "exp": int(time.time()) + 60,
            },
        )
        raw = json.dumps(
            {"dispatch_id": dispatch_id, "dispatch_token": token, "run_attempt": 1},
            separators=(",", ":"),
        ).encode()
        result = broker.action_start(raw, {"Authorization": "Bearer synthetic.oidc.token"})
        self.assertEqual(result["ci_run_id"], run["ci_run_id"])
        running = state.transitions[-1][1]
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["external_repository"], CENTRAL_REPOSITORY)
        self.assertEqual(running["external_run_id"], 901)


if __name__ == "__main__":
    unittest.main()
