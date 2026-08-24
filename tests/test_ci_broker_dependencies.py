from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from ci_workflows.ci_broker import BrokerError
from ci_workflows.ci_broker_action import BrokerActionError
from ci_workflows.ci_broker_dependencies import (
    ActionPrivateDependency,
    BrokerPrivateDependency,
    BrokerProductConfig,
    DependencyCiBroker,
    _checkout_dependency,
)
from ci_workflows.dependencies import DependencyResult
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


class ProductDependencyConfigTests(unittest.TestCase):
    def test_profile_is_backward_compatible_without_dependency(self) -> None:
        profile = BrokerProductConfig.parse(sample_config()).profile(
            "host", "validation.apple"
        )
        self.assertIsNone(profile.private_dependency)
        self.assertNotIn("private_dependency", profile.as_payload())

    def test_exact_private_dependency_is_bounded_and_in_payload(self) -> None:
        profile = BrokerProductConfig.parse(config_with_dependency()).profile("host")
        self.assertIsNotNone(profile.private_dependency)
        assert profile.private_dependency is not None
        self.assertEqual(profile.private_dependency.repository, "StreamScapeTV/example-media")
        self.assertEqual(profile.private_dependency.sha, "b" * 40)
        self.assertEqual(
            profile.as_payload()["private_dependency"],
            {
                "repository": "StreamScapeTV/example-media",
                "sha": "b" * 40,
                "subdirectory": ".",
                "id": "example-media",
            },
        )

    def test_dependency_rejects_unapproved_repo_sha_traversal_and_id(self) -> None:
        cases = (
            ({"repository": "outside/example", "sha": "b" * 40, "subdirectory": ".", "id": "example-media"}, "private_ci_dependency_repository_unsupported"),
            ({"repository": "StreamScapeTV/example-media", "sha": "short", "subdirectory": ".", "id": "example-media"}, "invalid_source_sha"),
            ({"repository": "StreamScapeTV/example-media", "sha": "b" * 40, "subdirectory": "../escape", "id": "example-media"}, "invalid_dependency_subdirectory"),
            ({"repository": "StreamScapeTV/example-media", "sha": "b" * 40, "subdirectory": ".", "id": "Bad_Id"}, "invalid_dependency_id"),
        )
        for raw, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(BrokerError, code):
                BrokerPrivateDependency.parse(raw)

    def test_dependency_rejects_missing_or_extra_fields(self) -> None:
        with self.assertRaisesRegex(BrokerError, "private_ci_dependency_invalid"):
            BrokerPrivateDependency.parse(
                {
                    "repository": "StreamScapeTV/example-media",
                    "sha": "b" * 40,
                    "subdirectory": ".",
                }
            )
        with self.assertRaisesRegex(BrokerError, "private_ci_dependency_invalid"):
            BrokerPrivateDependency.parse(
                {
                    "repository": "StreamScapeTV/example-media",
                    "sha": "b" * 40,
                    "subdirectory": ".",
                    "id": "example-media",
                    "token": "forbidden-in-config",
                }
            )


class BrokerDependencyActionTests(unittest.TestCase):
    def make_broker(self) -> tuple[DependencyCiBroker, AgentStateStub, SourceGithubStub]:
        state = AgentStateStub()
        source = SourceGithubStub()
        broker = DependencyCiBroker(
            broker_config(),
            agent_state=state,  # type: ignore[arg-type]
            source_github=source,  # type: ignore[arg-type]
            dispatch_github=DispatchGithubStub(),  # type: ignore[arg-type]
            oidc=OidcStub(),  # type: ignore[arg-type]
        )
        return broker, state, source

    def test_action_start_mints_and_returns_dependency_scoped_token(self) -> None:
        broker, state, _source = self.make_broker()
        run = agent_run()
        state.get_result = {"ok": True, "run": run}
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

        result = broker.action_start(
            raw,
            {"Authorization": "Bearer synthetic.oidc.token"},
        )
        dependency = result["private_dependency"]
        self.assertIsInstance(dependency, dict)
        assert isinstance(dependency, dict)
        self.assertEqual(dependency["repository"], "StreamScapeTV/example-media")
        self.assertEqual(dependency["sha"], "b" * 40)
        self.assertEqual(dependency["token"], "synthetic-source-token")
        self.assertEqual(state.transitions[-1][1]["status"], "running")

    def test_actions_dependency_parser_keeps_token_out_of_shape_validation(self) -> None:
        parsed = ActionPrivateDependency.parse(
            {
                "repository": "StreamScapeTV/example-media",
                "sha": "b" * 40,
                "subdirectory": ".",
                "id": "example-media",
                "token": "transient-token",
            }
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.token, "transient-token")
        self.assertEqual(parsed.dependency_id, "example-media")
        self.assertIsNone(ActionPrivateDependency.parse(None))
        with self.assertRaisesRegex(BrokerActionError, "start_response_invalid"):
            ActionPrivateDependency.parse(
                {
                    "repository": "StreamScapeTV/example-media",
                    "sha": "b" * 40,
                    "subdirectory": ".",
                    "id": "example-media",
                    "token": "transient-token",
                    "extra": "no",
                }
            )

    def test_exact_checkout_reuses_canonical_dependency_evidence(self) -> None:
        dependency = ActionPrivateDependency(
            repository="StreamScapeTV/example-media",
            sha="b" * 40,
            subdirectory=".",
            dependency_id="example-media",
            token="transient-token",
        )
        result = DependencyResult(
            dependency_id="example-media",
            repository="StreamScapeTV/example-media",
            head_sha="b" * 40,
            relative_path="dependencies/example-media",
            expected_subpath=".",
            remotes_erased=True,
            credentials_erased=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary)
            diagnostic = io.StringIO()
            with mock.patch(
                "ci_workflows.ci_broker_dependencies.checkout_private_dependency",
                return_value=result,
            ) as checkout:
                environment = _checkout_dependency(
                    dependency=dependency,
                    state_root=state_root,
                    contract_root=state_root,
                    diagnostic=diagnostic,
                )
            checkout.assert_called_once_with(
                state_root=state_root,
                repository="StreamScapeTV/example-media",
                admitted_sha="b" * 40,
                dependency_id="example-media",
                expected_subpath=".",
                fetch_depth=1,
                token="transient-token",
                contract_root=state_root,
            )
            self.assertEqual(environment["INPUT_PRIVATE_DEPENDENCY_VERIFIED"], "true")
            self.assertEqual(environment["INPUT_PRIVATE_DEPENDENCY_REMOTES_ERASED"], "true")
            self.assertEqual(environment["INPUT_PRIVATE_DEPENDENCY_CREDENTIALS_ERASED"], "true")
            self.assertEqual(environment["INPUT_PRIVATE_DEPENDENCY_HEAD_SHA"], "b" * 40)
            self.assertEqual(
                environment["CI_PRIVATE_DEPENDENCY_PATH"],
                str(state_root / "dependencies/example-media"),
            )


if __name__ == "__main__":
    unittest.main()
