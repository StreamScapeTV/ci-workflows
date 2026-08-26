#!/usr/bin/env python3
"""Black-box-ish HTTP smoke for the standalone broker runtime."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app import BrokerError, BrokerHttpServer, RelayConfig, ThinCiRelay  # noqa: E402

CI_RUN_ID = "00000000-0000-4000-8000-000000000019"
WEBHOOK_SECRET = "synthetic-webhook-secret"
PRIVATE_REPOSITORY = "ExampleOrg/private-app"
PRIVATE_REF = "develop"


class AgentStateStub:
    def __init__(self) -> None:
        self.claims: list[str] = []
        self.transitions: list[tuple[str, dict[str, object]]] = []

    def claim(self, ci_run_id: str) -> dict[str, object]:
        self.claims.append(ci_run_id)
        return {
            "ok": True,
            "replayed": False,
            "run": {
                "ci_run_id": ci_run_id,
                "project_key": "synthetic-project",
                "origin": "agent_request",
                "status": "accepted",
                "repository": PRIVATE_REPOSITORY,
                "ref": PRIVATE_REF,
                "is_tag": False,
                "workflow_key": "validation.apple",
                "test_profile": "host",
                "inputs": {},
                "requested_source_sha": None,
            },
        }

    def transition(self, ci_run_id: str, patch: dict[str, object]) -> dict[str, object]:
        self.transitions.append((ci_run_id, dict(patch)))
        return {"ok": True}


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), CaptureHandler)
        self.requests: list[dict[str, object]] = []


class CaptureHandler(BaseHTTPRequestHandler):
    server: CaptureServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        self.server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "body": json.loads(raw.decode("utf-8")),
            }
        )
        self.send_response(204)
        self.end_headers()


class LoopbackGitHubClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def repository_token(self, repository: str) -> str:
        if repository != "StreamScapeTV/ci-workflows":
            raise BrokerError("unexpected_repository", 500)
        return "synthetic-dispatch-token"

    def dispatch_relay(
        self,
        *,
        repository: str,
        workflow: str,
        ref: str,
        inputs: dict[str, str],
        token: str,
    ) -> None:
        path = (
            f"/repos/{repository}/actions/workflows/"
            ".github%2Fworkflows%2Fcentral-ci-dispatch.yml/dispatches"
        )
        raw = json.dumps({"ref": ref, "inputs": inputs}, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=raw,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 204:
                raise BrokerError("unexpected_dispatch_status", 500)


def serve(server: ThreadingHTTPServer) -> tuple[threading.Thread, str]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return thread, f"http://{host}:{port}"


class BrokerHttpSmoke(unittest.TestCase):
    def test_health_webhook_claim_and_outbound_dispatch_over_real_http(self) -> None:
        capture = CaptureServer()
        capture_thread, capture_url = serve(capture)
        self.addCleanup(capture.shutdown)
        self.addCleanup(capture.server_close)
        self.addCleanup(capture_thread.join, 5)

        state = AgentStateStub()
        relay = ThinCiRelay(
            RelayConfig(
                dispatch_app_id=1,
                dispatch_app_private_key="synthetic",
                agent_state_url="https://example.invalid",
                agent_state_secret_key="synthetic",
                agent_state_webhook_secret=WEBHOOK_SECRET,
                port=0,
            ),
            agent_state=state,  # type: ignore[arg-type]
            dispatch_github=LoopbackGitHubClient(capture_url),  # type: ignore[arg-type]
        )
        server = BrokerHttpServer(("127.0.0.1", 0), relay)
        server_thread, server_url = serve(server)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, 5)

        with urllib.request.urlopen(server_url + "/healthz", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read().decode("utf-8")), {"ok": True})

        raw = json.dumps(
            {
                "type": "INSERT",
                "schema": "agent_private",
                "table": "ci_runs",
                "record": {
                    "ci_run_id": CI_RUN_ID,
                    "origin": "agent_request",
                    "status": "requested",
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            server_url + "/hooks/agent-state",
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-StreamScape-Webhook-Secret": WEBHOOK_SECRET,
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            result = json.loads(response.read().decode("utf-8"))

        self.assertEqual(result, {"ok": True, "dispatched": True, "recovered": False})
        self.assertEqual(state.claims, [CI_RUN_ID])
        self.assertEqual(state.transitions, [])
        self.assertEqual(len(capture.requests), 1)

        outbound = capture.requests[0]
        self.assertEqual(
            outbound["path"],
            "/repos/StreamScapeTV/ci-workflows/actions/workflows/"
            ".github%2Fworkflows%2Fcentral-ci-dispatch.yml/dispatches",
        )
        self.assertEqual(outbound["authorization"], "Bearer synthetic-dispatch-token")
        body = outbound["body"]
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(body["ref"], "main")
        inputs = body["inputs"]
        self.assertIsInstance(inputs, dict)
        assert isinstance(inputs, dict)
        self.assertEqual(set(inputs), {"active_key", "ci_run_id"})
        self.assertEqual(inputs["ci_run_id"], CI_RUN_ID)
        self.assertEqual(len(inputs["active_key"]), 64)
        rendered = json.dumps(body, sort_keys=True)
        for private in (
            PRIVATE_REPOSITORY,
            PRIVATE_REF,
            "validation.apple",
            "synthetic-project",
            "host",
        ):
            self.assertNotIn(private, rendered)


class BrokerSemanticContractTests(unittest.TestCase):
    def test_closed_host_intents_project_only_opaque_dispatch_inputs(self) -> None:
        from app import RelayRequest

        for workflow_key in (
            "validation.apple",
            "validation.android",
            "validation.python",
        ):
            request = RelayRequest.from_claimed_run(
                {
                    "ci_run_id": CI_RUN_ID,
                    "project_key": "synthetic-project",
                    "origin": "agent_request",
                    "status": "accepted",
                    "repository": PRIVATE_REPOSITORY,
                    "ref": PRIVATE_REF,
                    "is_tag": False,
                    "workflow_key": workflow_key,
                    "test_profile": "host",
                    "inputs": {},
                    "requested_source_sha": None,
                }
            )
            self.assertEqual(set(request.workflow_inputs()), {"active_key", "ci_run_id"})

    def test_unreviewed_intent_and_requested_sha_fail_closed(self) -> None:
        from app import RelayRequest

        base = {
            "ci_run_id": CI_RUN_ID,
            "project_key": "synthetic-project",
            "origin": "agent_request",
            "status": "accepted",
            "repository": PRIVATE_REPOSITORY,
            "ref": PRIVATE_REF,
            "is_tag": False,
            "workflow_key": "validation.apple",
            "test_profile": "host",
            "inputs": {},
            "requested_source_sha": None,
        }
        for patch, code in (
            ({"workflow_key": "validation.native"}, "unsupported_ci_intent"),
            ({"test_profile": "release"}, "unsupported_ci_intent"),
            ({"inputs": {"command": "anything"}}, "unsupported_ci_inputs"),
            ({"requested_source_sha": "a" * 40}, "requested_source_sha_unsupported"),
        ):
            value = {**base, **patch}
            with self.assertRaisesRegex(BrokerError, code):
                RelayRequest.from_claimed_run(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
