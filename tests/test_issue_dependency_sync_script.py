from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.issue_dependencies import (
    IssueRef,
    NativeDependency,
    RepositoryRecord,
)

SCRIPT_PATH = ROOT / "scripts" / "ci" / "sync_issue_dependencies.py"
spec = importlib.util.spec_from_file_location("sync_issue_dependencies_script", SCRIPT_PATH)
assert spec and spec.loader
sync_script = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sync_script
spec.loader.exec_module(sync_script)


class DummyGateway:
    def __init__(self, token: str) -> None:
        self.token = token

    def list_repositories(self):
        return ()

    def read_file(self, repository, path, ref):
        return None

    def get_issue(self, ref):
        raise AssertionError("no managed repositories expected")

    def list_blocked_by(self, ref):
        raise AssertionError("no managed repositories expected")

    def add_blocked_by(self, dependent, blocker):
        raise AssertionError("no managed repositories expected")

    def remove_blocked_by(self, dependent, blocker):
        raise AssertionError("no managed repositories expected")


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ScriptContractTests(unittest.TestCase):
    def valid_env(self):
        return {
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_SHA": "a" * 40,
            "GH_TOKEN": "token-value",
        }

    def test_run_accepts_only_protected_central_schedule_or_dispatch(self):
        summary = sync_script.run(env=self.valid_env(), gateway_factory=DummyGateway)
        self.assertEqual(summary["repositories_scanned"], 0)

        for key, value in (
            ("GITHUB_REPOSITORY", "StreamScapeTV/other"),
            ("GITHUB_REF", "refs/heads/feature"),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("GITHUB_SHA", "not-a-sha"),
        ):
            env = self.valid_env()
            env[key] = value
            with self.subTest(key=key):
                with self.assertRaises(sync_script.DependencySyncError):
                    sync_script.run(env=env, gateway_factory=DummyGateway)

        dispatch = self.valid_env()
        dispatch["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        sync_script.run(env=dispatch, gateway_factory=DummyGateway)

    def test_main_rejects_all_arguments(self):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            self.assertEqual(sync_script.main(["--repo", "x"]), 2)
        self.assertIn("accepts no arguments", stderr.getvalue())

    def test_token_is_required_and_not_exposed_in_error(self):
        for token in ("", " token", "token\nvalue"):
            with self.subTest(token=repr(token)):
                env = self.valid_env()
                env["GH_TOKEN"] = token
                with self.assertRaises(sync_script.GitHubApiError) as ctx:
                    sync_script.run(env=env, gateway_factory=DummyGateway)
                stripped = token.strip()
                if stripped:
                    self.assertNotIn(stripped, str(ctx.exception))

    def test_repository_enumeration_is_fixed_to_streamscapetv(self):
        gateway = sync_script.GitHubRestGateway("secret-token")
        pages = [
            [
                {
                    "full_name": "StreamScapeTV/b",
                    "default_branch": "main",
                },
                {
                    "full_name": "StreamScapeTV/a",
                    "default_branch": "develop",
                },
            ]
        ]
        with mock.patch.object(
            gateway, "_request_json", side_effect=pages
        ) as request:
            repos = gateway.list_repositories()
        self.assertEqual(
            repos,
            (
                RepositoryRecord("StreamScapeTV/a", "develop"),
                RepositoryRecord("StreamScapeTV/b", "main"),
            ),
        )
        self.assertEqual(
            request.call_args.args[1],
            "/orgs/StreamScapeTV/repos?type=all&per_page=100&page=1",
        )

    def test_read_file_allows_only_dependency_manifest_suffixes(self):
        gateway = sync_script.GitHubRestGateway("secret-token")
        with mock.patch.object(gateway, "_request_json", return_value=None) as request:
            self.assertIsNone(
                gateway.read_file(
                    "StreamScapeTV/demo",
                    "ISSUE_DEPENDENCIES.yml",
                    "main",
                )
            )
            self.assertIsNone(
                gateway.read_file(
                    "StreamScapeTV/demo",
                    "ISSUE_DEPENDENCIES.yaml",
                    "main",
                )
            )
        self.assertEqual(request.call_count, 2)
        for path in ("AGENTS.md", "README.md"):
            with self.subTest(path=path):
                with self.assertRaises(sync_script.GitHubApiError):
                    gateway.read_file("StreamScapeTV/demo", path, "main")

    def test_http_errors_are_sanitized(self):
        gateway = sync_script.GitHubRestGateway("top-secret")
        error = urllib.error.HTTPError(
            "https://api.github.com/repos/StreamScapeTV/demo/issues/1",
            403,
            "Forbidden top-secret",
            {},
            None,
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(sync_script.GitHubApiError) as ctx:
                gateway._request_json("GET", "/repos/StreamScapeTV/demo/issues/1")
        rendered = str(ctx.exception)
        self.assertNotIn("top-secret", rendered)
        self.assertIn("HTTP 403", rendered)

    def test_native_dependency_listing_normalizes_issue_identity(self):
        gateway = sync_script.GitHubRestGateway("secret-token")
        dependent = IssueRef("StreamScapeTV/demo", 10)
        payload = [[{
            "id": 3003,
            "number": 3,
            "repository_url": "https://api.github.com/repos/StreamScapeTV/other",
            "html_url": "https://github.com/StreamScapeTV/other/pull/3",
            "pull_request": {"url": "https://api.github.com/repos/StreamScapeTV/other/pulls/3"},
        }]]
        with mock.patch.object(gateway, "_request_json", side_effect=payload):
            native = gateway.list_blocked_by(dependent)
        self.assertEqual(
            native,
            (NativeDependency("https://github.com/StreamScapeTV/other/issues/3", 3003),),
        )

    def test_add_and_remove_use_native_dependency_api(self):
        gateway = sync_script.GitHubRestGateway("secret-token")
        dependent = IssueRef("StreamScapeTV/demo", 10)
        blocker_record = sync_script.IssueRecord(
            IssueRef("StreamScapeTV/other", 3),
            3003,
            "open",
            None,
            False,
        )
        native = NativeDependency(
            "https://github.com/StreamScapeTV/other/issues/3",
            3003,
        )
        with mock.patch.object(gateway, "_request_json", return_value={}) as request:
            gateway.add_blocked_by(dependent, blocker_record)
            gateway.remove_blocked_by(dependent, native)
        add_call = request.call_args_list[0]
        self.assertEqual(add_call.args[0], "POST")
        self.assertEqual(
            add_call.args[1],
            "/repos/StreamScapeTV/demo/issues/10/dependencies/blocked_by",
        )
        self.assertEqual(add_call.kwargs["payload"], {"issue_id": 3003})
        remove_call = request.call_args_list[1]
        self.assertEqual(remove_call.args[0], "DELETE")
        self.assertEqual(
            remove_call.args[1],
            "/repos/StreamScapeTV/demo/issues/10/dependencies/blocked_by/3003",
        )


if __name__ == "__main__":
    unittest.main()
