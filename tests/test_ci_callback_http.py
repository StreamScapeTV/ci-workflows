from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock
import urllib.request

from ci_workflows.ci_callback_http import CENTRAL_HTTP_USER_AGENT, central_urlopen

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/ci_broker.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("ci_broker_cli_http_identity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CallbackHttpTests(unittest.TestCase):
    def test_central_urlopen_sets_fixed_api_user_agent(self) -> None:
        request = urllib.request.Request(
            "https://broker.example/actions/start",
            data=b"{}",
            method="POST",
            headers={"User-Agent": "caller-selected-value"},
        )
        sentinel = object()
        with mock.patch(
            "ci_workflows.ci_callback_http.urllib.request.urlopen",
            return_value=sentinel,
        ) as opener:
            self.assertIs(central_urlopen(request, timeout=17), sentinel)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["user-agent"], CENTRAL_HTTP_USER_AGENT)
        opener.assert_called_once_with(request, timeout=17)

    def test_actions_cli_routes_callback_commands_through_central_urlopen(self) -> None:
        cli = load_cli()
        calls: list[tuple[str, object]] = []

        def record_execute(*, opener):
            calls.append(("execute", opener))

        def record_fail(*, opener):
            calls.append(("fail", opener))

        def record_cancel(*, opener):
            calls.append(("cancel", opener))

        cli.execute_apple_host = record_execute
        cli.fail_if_active = record_fail
        cli.cancel_if_active = record_cancel
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["execute-apple-host"]), 0)
            self.assertEqual(cli.main(["fail-if-active"]), 0)
            self.assertEqual(cli.main(["cancel-if-active"]), 0)

        self.assertEqual(
            calls,
            [
                ("execute", central_urlopen),
                ("fail", central_urlopen),
                ("cancel", central_urlopen),
            ],
        )


if __name__ == "__main__":
    unittest.main()
