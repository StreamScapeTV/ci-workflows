from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/ci_broker.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("ci_broker_cli_retirement", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrokerCliRetirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = load_cli()
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_cli_exposes_only_thin_relay_server_and_self_check(self) -> None:
        parser = self.cli.parser()
        command = next(action for action in parser._actions if action.dest == "command")
        self.assertEqual(tuple(command.choices), ("server", "self-check"))
        self.assertIn("Thin Central CI webhook relay", parser.description)

    def test_superseded_callback_and_execution_helpers_are_unreachable(self) -> None:
        for name in (
            "_safe_remote_code",
            "_diagnose_broker_callback",
            "_request_oidc_token",
            "execute_apple_host",
        ):
            self.assertFalse(hasattr(self.cli, name), name)
        for forbidden in (
            "CI_BROKER_URL",
            "CI_DISPATCH_ID",
            "CI_DISPATCH_TOKEN",
            "ACTIONS_ID_TOKEN_REQUEST_URL",
            "/actions/start",
            "/actions/finish",
            "execute-apple-host",
            "fail-if-active",
            "cancel-if-active",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_runtime_delegates_only_to_relay_server(self) -> None:
        self.assertIn("from ci_workflows.ci_relay import RelayConfig", self.source)
        self.assertIn("from ci_workflows.ci_relay_server import self_check, serve", self.source)
        self.assertNotIn("ci_broker_action", self.source)
        self.assertNotIn("ci_callback_http", self.source)
        self.assertNotIn("ci_broker_start_guard", self.source)


if __name__ == "__main__":
    unittest.main()
