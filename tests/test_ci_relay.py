from __future__ import annotations

import json
import unittest

from ci_workflows.ci_broker import BrokerError
from ci_workflows.ci_relay import (
    RelayGitHubClient,
    RelayRequest,
    ThinCiRelay,
    active_identity_key,
)
from tests.test_ci_broker import AgentStateStub, broker_config


class DispatchStub:
    def __init__(self) -> None:
        self.dispatches: list[dict[str, object]] = []

    def repository_token(self, repository: str) -> str:
        self.dispatches.append({"token_repository": repository})
        return "synthetic-dispatch-token"

    def dispatch_relay(self, **kwargs: object) -> None:
        self.dispatches.append(dict(kwargs))


def claimed_run(**patch: object) -> dict[str, object]:
    value: dict[str, object] = {
        "ci_run_id": "00000000-0000-4000-8000-000000000001",
        "project_key": "sample-project",
        "origin": "agent_request",
        "status": "accepted",
        "repository": "OtherOrg/private-source",
        "ref": "develop",
        "is_tag": False,
        "workflow_key": "validation.apple",
        "test_profile": "host",
        "inputs": {},
        "requested_source_sha": None,
    }
    value.update(patch)
    return value


def webhook(ci_run_id: str = "00000000-0000-4000-8000-000000000001") -> bytes:
    return json.dumps(
        {
            "type": "INSERT",
            "schema": "agent_private",
            "table": "ci_runs",
            "record": {
                "ci_run_id": ci_run_id,
                "origin": "agent_request",
                "status": "requested",
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


class RelayRequestTests(unittest.TestCase):
    def test_private_identity_is_validated_but_dispatch_is_opaque(self) -> None:
        request = RelayRequest.from_claimed_run(claimed_run())
        self.assertEqual(request.repository, "OtherOrg/private-source")
        self.assertEqual(request.ref, "develop")
        self.assertFalse(request.is_tag)
        inputs = request.workflow_inputs()
        self.assertEqual(set(inputs), {"active_key", "ci_run_id"})
        self.assertEqual(inputs["ci_run_id"], request.ci_run_id)
        self.assertEqual(
            inputs["active_key"],
            active_identity_key(
                repository="OtherOrg/private-source",
                ref="develop",
                is_tag=False,
                workflow_key="validation.apple",
                profile="host",
            ),
        )
        rendered = json.dumps(inputs, sort_keys=True)
        for private in (
            "OtherOrg/private-source",
            "develop",
            "validation.apple",
            "sample-project",
            "host",
        ):
            self.assertNotIn(private, rendered)

    def test_active_identity_changes_for_each_non_sha_authority(self) -> None:
        base = active_identity_key(
            repository="OtherOrg/private-source",
            ref="develop",
            is_tag=False,
            workflow_key="validation.apple",
            profile="host",
        )
        for patch in (
            {"repository": "OtherOrg/second-source"},
            {"ref": "main"},
            {"is_tag": True},
            {"workflow_key": "validation.apple.next"},
            {"profile": "host-next"},
        ):
            values: dict[str, object] = {
                "repository": "OtherOrg/private-source",
                "ref": "develop",
                "is_tag": False,
                "workflow_key": "validation.apple",
                "profile": "host",
            }
            values.update(patch)
            with self.subTest(patch=patch):
                self.assertNotEqual(base, active_identity_key(**values))

    def test_tag_is_explicit_and_not_guessed_from_ref_shape(self) -> None:
        request = RelayRequest.from_claimed_run(
            claimed_run(ref="release/fancy-tag", is_tag=True)
        )
        self.assertEqual(request.ref, "release/fancy-tag")
        self.assertTrue(request.is_tag)
        self.assertEqual(set(request.workflow_inputs()), {"active_key", "ci_run_id"})

    def test_full_owner_name_repository_is_not_org_prefixed(self) -> None:
        request = RelayRequest.from_claimed_run(
            claimed_run(repository="AcmeCorp/private-app")
        )
        self.assertEqual(request.repository, "AcmeCorp/private-app")

    def test_sha_pinned_and_prefixed_ref_requests_fail_closed(self) -> None:
        with self.assertRaisesRegex(BrokerError, "requested_source_sha_unsupported"):
            RelayRequest.from_claimed_run(claimed_run(requested_source_sha="a" * 40))
        with self.assertRaisesRegex(BrokerError, "invalid_ref"):
            RelayRequest.from_claimed_run(claimed_run(ref="refs/heads/develop"))

    def test_only_current_reviewed_workflow_profile_and_empty_inputs_are_dispatchable(self) -> None:
        cases = (
            ({"workflow_key": "validation.python"}, "unsupported_ci_intent"),
            ({"test_profile": "release"}, "unsupported_ci_intent"),
            ({"inputs": {"scope": "integration"}}, "unsupported_ci_inputs"),
        )
        for patch, code in cases:
            with self.subTest(patch=patch):
                with self.assertRaisesRegex(BrokerError, code):
                    RelayRequest.from_claimed_run(claimed_run(**patch))

    def test_is_tag_is_required_boolean(self) -> None:
        with self.assertRaisesRegex(BrokerError, "invalid_is_tag"):
            RelayRequest.from_claimed_run(claimed_run(is_tag="false"))


class RelayGithubClientTests(unittest.TestCase):
    def test_dispatch_is_fire_and_forget_and_requests_no_run_details(self) -> None:
        class Client(RelayGitHubClient):
            def __init__(self) -> None:
                self.request: dict[str, object] | None = None

            def _request(self, method: str, path: str, **kwargs: object) -> tuple[int, object]:
                self.request = {"method": method, "path": path, **kwargs}
                return 204, None

        client = Client()
        inputs = {"ci_run_id": "opaque", "active_key": "a" * 64}
        result = client.dispatch_relay(
            repository="StreamScapeTV/ci-workflows",
            workflow=".github/workflows/central-ci-dispatch.yml",
            ref="main",
            inputs=inputs,
            token="synthetic",
        )
        self.assertIsNone(result)
        assert client.request is not None
        self.assertEqual(client.request["method"], "POST")
        self.assertEqual(client.request["expected"], (204,))
        body = client.request["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["ref"], "main")
        self.assertEqual(body["inputs"], inputs)
        self.assertNotIn("return_run_details", body)


class ThinCiRelayTests(unittest.TestCase):
    def make_relay(self) -> tuple[ThinCiRelay, AgentStateStub, DispatchStub]:
        state = AgentStateStub()
        dispatch = DispatchStub()
        relay = ThinCiRelay(
            broker_config(),
            agent_state=state,  # type: ignore[arg-type]
            dispatch_github=dispatch,  # type: ignore[arg-type]
        )
        return relay, state, dispatch

    def test_webhook_claims_once_and_dispatches_only_opaque_inputs(self) -> None:
        relay, state, dispatch = self.make_relay()
        state.claim_result = {"ok": True, "run": claimed_run(), "replayed": False}
        result = relay.handle_agent_state_webhook(
            webhook(),
            {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
        )
        self.assertEqual(result, {"ok": True, "dispatched": True, "recovered": False})
        self.assertEqual(state.transitions, [])
        self.assertEqual(state.registrations, [])
        self.assertEqual(len(dispatch.dispatches), 2)
        call = dispatch.dispatches[1]
        self.assertEqual(call["repository"], "StreamScapeTV/ci-workflows")
        self.assertEqual(call["workflow"], ".github/workflows/central-ci-dispatch.yml")
        self.assertEqual(call["ref"], "main")
        workflow_inputs = call["inputs"]
        assert isinstance(workflow_inputs, dict)
        self.assertEqual(set(workflow_inputs), {"active_key", "ci_run_id"})
        rendered = json.dumps(workflow_inputs, sort_keys=True)
        self.assertNotIn("OtherOrg/private-source", rendered)
        self.assertNotIn("develop", rendered)

    def test_replayed_accepted_request_recovers_dispatch(self) -> None:
        relay, state, dispatch = self.make_relay()
        state.claim_result = {"ok": True, "run": claimed_run(), "replayed": True}
        result = relay.handle_agent_state_webhook(
            webhook(),
            {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
        )
        self.assertEqual(result, {"ok": True, "dispatched": True, "recovered": True})
        self.assertEqual(len(dispatch.dispatches), 2)
        self.assertEqual(state.transitions, [])

    def test_replayed_nonaccepted_request_never_dispatches_duplicate_pipeline(self) -> None:
        relay, state, dispatch = self.make_relay()
        state.claim_result = {
            "ok": True,
            "run": claimed_run(status="running"),
            "replayed": True,
        }
        result = relay.handle_agent_state_webhook(
            webhook(),
            {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
        )
        self.assertEqual(result, {"ok": True, "replayed": True})
        self.assertEqual(dispatch.dispatches, [])
        self.assertEqual(state.transitions, [])

    def test_invalid_claimed_intent_is_cancelled_without_dispatch(self) -> None:
        relay, state, dispatch = self.make_relay()
        state.claim_result = {
            "ok": True,
            "run": claimed_run(requested_source_sha="b" * 40),
            "replayed": False,
        }
        with self.assertRaisesRegex(BrokerError, "requested_source_sha_unsupported"):
            relay.handle_agent_state_webhook(
                webhook(),
                {"X-StreamScape-Webhook-Secret": "agent-state-webhook-secret"},
            )
        self.assertEqual(dispatch.dispatches, [])
        self.assertEqual(
            state.transitions,
            [
                (
                    "00000000-0000-4000-8000-000000000001",
                    {
                        "status": "cancelled",
                        "error_summary": "requested_source_sha_unsupported",
                    },
                )
            ],
        )

    def test_bad_webhook_secret_is_rejected_before_claim_or_dispatch(self) -> None:
        relay, state, dispatch = self.make_relay()
        state.claim_result = {"ok": True, "run": claimed_run(), "replayed": False}
        with self.assertRaisesRegex(BrokerError, "agent_state_webhook_unauthorized"):
            relay.handle_agent_state_webhook(
                webhook(),
                {"X-StreamScape-Webhook-Secret": "wrong"},
            )
        self.assertEqual(dispatch.dispatches, [])
        self.assertEqual(state.transitions, [])


if __name__ == "__main__":
    unittest.main()
