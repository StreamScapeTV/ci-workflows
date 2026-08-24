from __future__ import annotations

import json
import time
import unittest

from ci_workflows.ci_broker import BrokerError
from ci_workflows.ci_broker_dependencies import BrokerProductConfig
from ci_workflows.ci_broker_start_guard import GuardedDependencyCiBroker
from tests.test_ci_broker import (
    AgentStateStub,
    DispatchGithubStub,
    OidcStub,
    SourceGithubStub,
    agent_run,
    broker_config,
    sample_config,
)


def config_with_dependency() -> dict[str, object]:
    value = sample_config()
    profile = dict(value["profiles"]["host"])  # type: ignore[index]
    profile["private_dependency"] = {
        "repository": "StreamScapeTV/example-media",
        "sha": "b" * 40,
        "subdirectory": ".",
        "id": "example-media",
    }
    value["profiles"] = {"host": profile}
    return value


def action_request(broker: GuardedDependencyCiBroker) -> tuple[bytes, dict[str, str]]:
    run = agent_run()
    profile = BrokerProductConfig.parse(config_with_dependency()).profile("host")
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
            "profile": profile.as_payload(),
            "exp": int(time.time()) + 60,
        },
    )
    raw = json.dumps(
        {"dispatch_id": dispatch_id, "dispatch_token": token, "run_attempt": 1},
        separators=(",", ":"),
    ).encode()
    return raw, {"Authorization": "Bearer synthetic.oidc.token"}


class FailingDependencySource(SourceGithubStub):
    def repository_token(self, repository: str) -> str:
        if repository == "StreamScapeTV/example-media":
            raise BrokerError("remote_http_404", 502)
        return super().repository_token(repository)


class ExplodingDependencySource(SourceGithubStub):
    def repository_token(self, repository: str) -> str:
        if repository == "StreamScapeTV/example-media":
            raise RuntimeError("must-not-leak")
        return super().repository_token(repository)


class CompactDependencySource(SourceGithubStub):
    def __init__(self, observed_sha: str = "b" * 40) -> None:
        super().__init__()
        self.observed_sha = observed_sha
        self.exact_requests: list[tuple[str, str, str | None]] = []

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: object | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, object]:
        self.assert_no_unexpected_request_shape(method, body, expected)
        self.exact_requests.append((method, path, token))
        return 200, {"sha": self.observed_sha}

    @staticmethod
    def assert_no_unexpected_request_shape(
        method: str,
        body: object | None,
        expected: tuple[int, ...],
    ) -> None:
        if method != "GET" or body is not None or expected != (200,):
            raise AssertionError("unexpected exact-commit request shape")

    def get_commit(self, repository: str, ref: str, token: str) -> dict[str, object]:
        if repository == "StreamScapeTV/example-media":
            raise AssertionError("bulk commit endpoint must not verify exact dependency SHA")
        return super().get_commit(repository, ref, token)


class GuardedDependencyStartTests(unittest.TestCase):
    def make_broker(
        self, source: SourceGithubStub
    ) -> tuple[GuardedDependencyCiBroker, AgentStateStub]:
        state = AgentStateStub()
        broker = GuardedDependencyCiBroker(
            broker_config(),
            agent_state=state,  # type: ignore[arg-type]
            source_github=source,  # type: ignore[arg-type]
            dispatch_github=DispatchGithubStub(),  # type: ignore[arg-type]
            oidc=OidcStub(),  # type: ignore[arg-type]
        )
        return broker, state

    def test_dependency_broker_error_terminalizes_claimed_agent_request(self) -> None:
        broker, state = self.make_broker(FailingDependencySource())
        raw, headers = action_request(broker)

        with self.assertRaisesRegex(BrokerError, "remote_http_404"):
            broker.action_start(raw, headers)

        self.assertEqual(
            state.transitions,
            [
                (
                    "00000000-0000-4000-8000-000000000001",
                    {"status": "cancelled", "error_summary": "remote_http_404"},
                )
            ],
        )

    def test_unexpected_dependency_error_terminalizes_with_generic_code(self) -> None:
        broker, state = self.make_broker(ExplodingDependencySource())
        raw, headers = action_request(broker)

        with self.assertRaisesRegex(BrokerError, "private_dependency_admission_internal"):
            broker.action_start(raw, headers)

        self.assertEqual(
            state.transitions,
            [
                (
                    "00000000-0000-4000-8000-000000000001",
                    {
                        "status": "cancelled",
                        "error_summary": "private_dependency_admission_internal",
                    },
                )
            ],
        )

    def test_exact_dependency_uses_compact_git_commit_identity(self) -> None:
        source = CompactDependencySource()
        broker, state = self.make_broker(source)
        state.get_result = {"ok": True, "run": agent_run()}
        raw, headers = action_request(broker)

        result = broker.action_start(raw, headers)

        self.assertEqual(
            source.exact_requests,
            [
                (
                    "GET",
                    "/repos/StreamScapeTV/example-media/git/commits/" + "b" * 40,
                    "synthetic-source-token",
                )
            ],
        )
        dependency = result["private_dependency"]
        self.assertIsInstance(dependency, dict)
        assert isinstance(dependency, dict)
        self.assertEqual(dependency["repository"], "StreamScapeTV/example-media")
        self.assertEqual(dependency["sha"], "b" * 40)
        self.assertEqual(dependency["token"], "synthetic-source-token")
        self.assertEqual(state.transitions[-1][1]["status"], "running")

    def test_compact_dependency_identity_mismatch_terminalizes_request(self) -> None:
        source = CompactDependencySource(observed_sha="c" * 40)
        broker, state = self.make_broker(source)
        raw, headers = action_request(broker)

        with self.assertRaisesRegex(BrokerError, "private_dependency_source_mismatch"):
            broker.action_start(raw, headers)

        self.assertEqual(
            state.transitions,
            [
                (
                    "00000000-0000-4000-8000-000000000001",
                    {
                        "status": "cancelled",
                        "error_summary": "private_dependency_source_mismatch",
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
