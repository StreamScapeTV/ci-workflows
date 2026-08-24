from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/ci_broker.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("ci_broker_cli_diagnostics", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, value: object, status: int = 200) -> None:
        self.status = status
        self.raw = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self.raw if amount < 0 else self.raw[:amount]

    def getcode(self) -> int:
        return self.status


class BrokerCliDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = load_cli()

    def test_safe_remote_code_returns_only_bounded_stable_code(self) -> None:
        self.assertEqual(
            self.cli._safe_remote_code(b'{"ok":false,"code":"oidc_workflow_invalid","private":"ignored"}'),
            "oidc_workflow_invalid",
        )
        self.assertIsNone(self.cli._safe_remote_code(b'{"code":"bad-value"}'))
        self.assertIsNone(self.cli._safe_remote_code(b"not-json"))
        self.assertIsNone(
            self.cli._safe_remote_code(b"x" * (self.cli._DIAGNOSTIC_BYTES + 1))
        )

    def test_route_probe_surfaces_broker_code_without_response_payload(self) -> None:
        environment = {
            "CI_BROKER_URL": "https://broker.example",
            "CI_DISPATCH_ID": "opaque-dispatch-id",
            "CI_DISPATCH_TOKEN": "opaque-dispatch-token",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?x=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "opaque-request-token",
        }

        def opener(request, timeout: int):
            self.assertEqual(timeout, 30)
            if request.full_url.startswith("https://oidc.example/"):
                return Response({"value": "header.payload.signature"})
            self.assertEqual(request.full_url, "https://broker.example/actions/route")
            body = io.BytesIO(
                b'{"ok":false,"code":"oidc_workflow_invalid","detail":"must-not-leak"}'
            )
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", hdrs=None, fp=body
            )

        self.assertEqual(
            self.cli._diagnose_broker_callback(environment, opener),
            "broker_rejection_code=oidc_workflow_invalid",
        )

    def test_route_probe_classifies_non_broker_http_body_without_echoing_it(self) -> None:
        environment = {
            "CI_BROKER_URL": "https://broker.example",
            "CI_DISPATCH_ID": "opaque-dispatch-id",
            "CI_DISPATCH_TOKEN": "opaque-dispatch-token",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?x=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "opaque-request-token",
        }

        def opener(request, timeout: int):
            if request.full_url.startswith("https://oidc.example/"):
                return Response({"value": "header.payload.signature"})
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "Forbidden",
                hdrs=None,
                fp=io.BytesIO(b"<html>private edge response</html>"),
            )

        self.assertEqual(
            self.cli._diagnose_broker_callback(environment, opener),
            "broker_route_probe=http_403_no_broker_code",
        )


if __name__ == "__main__":
    unittest.main()
