from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.request

IMAGE_SRC = Path("/opt/ci-broker/src")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(IMAGE_SRC if (IMAGE_SRC / "ci_workflows").is_dir() else ROOT / "src"))

from ci_workflows.ci_broker import BrokerError  # noqa: E402
from ci_workflows.ci_relay import RelayConfig, RelayGitHubClient, ThinCiRelay  # noqa: E402
from ci_workflows.ci_relay_server import RelayHttpServer  # noqa: E402

CI_RUN_ID = "00000000-0000-4000-8000-000000000019"
WEBHOOK_SECRET = "synthetic-webhook-secret"
PRIVATE_REPOSITORY = "ExampleOrg/private-app"
PRIVATE_REF = "develop"


class _AgentStateStub:
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


class _CaptureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CaptureHandler)
        self.requests: list[dict[str, object]] = []


class _CaptureHandler(BaseHTTPRequestHandler):
    server: _CaptureServer

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


class _LoopbackGitHubClient(RelayGitHubClient):
    def __init__(self, base_url: str) -> None:
        super().__init__(1, "unused-in-smoke")
        self._base_url = base_url

    def repository_token(self, repository: str) -> str:
        if repository != "StreamScapeTV/ci-workflows":
            raise BrokerError("unexpected_repository", 500)
        return "synthetic-dispatch-token"

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict[str, object] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, object | None]:
        raw = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self._base_url + path,
            data=raw,
            method=method,
            headers={
                "Authorization": f"Bearer {token or ''}",
                **({"Content-Type": "application/json"} if raw is not None else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            status = int(response.status)
            payload = response.read()
        if status not in expected:
            raise BrokerError(f"unexpected_loopback_status_{status}", 500)
        return status, (json.loads(payload.decode("utf-8")) if payload else None)


def _serve(server: ThreadingHTTPServer) -> tuple[threading.Thread, str]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return thread, f"http://{host}:{port}"


class BrokerHttpIntegrationTests(unittest.TestCase):
    def test_health_webhook_claim_and_outbound_dispatch_over_real_http(self) -> None:
        capture = _CaptureServer()
        capture_thread, capture_url = _serve(capture)
        self.addCleanup(capture.shutdown)
        self.addCleanup(capture.server_close)
        self.addCleanup(capture_thread.join, 5)

        state = _AgentStateStub()
        config = RelayConfig(
            dispatch_app_id=1,
            dispatch_app_private_key="synthetic",
            agent_state_url="https://example.invalid",
            agent_state_secret_key="synthetic",
            agent_state_webhook_secret=WEBHOOK_SECRET,
            port=0,
        )
        relay = ThinCiRelay(
            config,
            agent_state=state,  # type: ignore[arg-type]
            dispatch_github=_LoopbackGitHubClient(capture_url),
        )
        server = RelayHttpServer(("127.0.0.1", 0), relay)
        server_thread, server_url = _serve(server)
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
            "/repos/StreamScapeTV/ci-workflows/actions/workflows/.github%2Fworkflows%2Fcentral-ci-dispatch.yml/dispatches",
        )
        self.assertEqual(outbound["authorization"], "Bearer synthetic-dispatch-token")
        body = outbound["body"]
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertEqual(body["ref"], "main")
        inputs = body["inputs"]
        self.assertIsInstance(inputs, dict)
        assert isinstance(inputs, dict)
        self.assertEqual(inputs["ci_run_id"], CI_RUN_ID)
        self.assertEqual(len(inputs["active_key"]), 64)
        rendered = json.dumps(body, sort_keys=True)
        self.assertNotIn(PRIVATE_REPOSITORY, rendered)
        self.assertNotIn(PRIVATE_REF, rendered)
        self.assertNotIn("validation.apple", rendered)
        self.assertNotIn("synthetic-project", rendered)


if __name__ == "__main__":
    unittest.main()
