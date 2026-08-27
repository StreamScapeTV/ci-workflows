#!/usr/bin/env python3
"""Focused broker transport tests."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from app import BrokerError, Config, Relay, Request  # noqa: E402

RUN_ID = "00000000-0000-4000-8000-000000000019"
SECRET = "synthetic-webhook-secret"


class State:
    def claim(self, run_id: str) -> dict[str, object]:
        return {
            "ok": True,
            "replayed": False,
            "run": {
                "ci_run_id": run_id,
                "origin": "agent_request",
                "status": "accepted",
                "repository": "ExampleOrg/private-app",
                "ref": "develop",
                "is_tag": False,
                "workflow_key": "validation.apple",
                "test_profile": "host",
                "inputs": {},
            },
        }

    def cancel(self, run_id: str) -> None:
        raise AssertionError("dispatch should not fail")


class GitHub:
    def __init__(self) -> None:
        self.inputs: dict[str, str] | None = None

    def dispatch(self, inputs: dict[str, str]) -> None:
        self.inputs = inputs


class BrokerTests(unittest.TestCase):
    def test_supported_rows_are_accepted(self) -> None:
        for key in (
            "validation.apple",
            "validation.android",
            "validation.python",
            "validation.node",
            "validation.flutter",
            "source.snapshot",
        ):
            request = Request.from_claim(
                {
                    "ci_run_id": RUN_ID,
                    "origin": "agent_request",
                    "status": "accepted",
                    "repository": "ExampleOrg/private-app",
                    "ref": "develop",
                    "is_tag": False,
                    "workflow_key": key,
                    "test_profile": "anything",
                    "inputs": {},
                }
            )
            self.assertEqual(set(request.dispatch_inputs()), {"active_key", "ci_run_id"})

    def test_webhook_dispatch_contains_only_opaque_ids(self) -> None:
        github = GitHub()
        relay = Relay(
            Config(1, "synthetic", "https://example.invalid", "synthetic", SECRET, 0),
            state=State(),  # type: ignore[arg-type]
            github=github,  # type: ignore[arg-type]
        )
        payload = json.dumps(
            {
                "type": "INSERT",
                "schema": "agent_private",
                "table": "ci_runs",
                "record": {"ci_run_id": RUN_ID, "origin": "agent_request", "status": "requested"},
            }
        ).encode()
        result = relay.webhook(payload, {"X-StreamScape-Webhook-Secret": SECRET})
        self.assertEqual(result, {"ok": True, "dispatched": True})
        assert github.inputs is not None
        self.assertEqual(set(github.inputs), {"active_key", "ci_run_id"})
        rendered = json.dumps(github.inputs)
        self.assertNotIn("ExampleOrg/private-app", rendered)
        self.assertNotIn("test_command", rendered)

    def test_retired_gitops_workflow_is_rejected(self) -> None:
        with self.assertRaisesRegex(BrokerError, "unsupported_ci_intent"):
            Request.from_claim(
                {
                    "ci_run_id": RUN_ID,
                    "origin": "agent_request",
                    "status": "accepted",
                    "repository": "ExampleOrg/private-app",
                    "ref": "develop",
                    "is_tag": False,
                    "workflow_key": "validation.gitops",
                    "inputs": {},
                }
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
