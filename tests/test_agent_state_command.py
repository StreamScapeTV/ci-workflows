from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import agent_state_command as command  # noqa: E402


class FakeGitHub:
    def __init__(self, mapping: command.RepositoryMapping) -> None:
        self.mapping = mapping
        self.integration_sha = "a" * 40
        self.pull_head = "b" * 40

    def repository(self, repository: str) -> Mapping[str, Any]:
        return {"full_name": repository, "archived": False}

    def branch_sha(self, repository: str, branch: str) -> str:
        assert repository == self.mapping.repository
        assert branch == self.mapping.integration_branch
        return self.integration_sha

    def issue(self, repository: str, number: int) -> Mapping[str, Any]:
        return {"number": number, "state": "open"}

    def pull(self, repository: str, number: int) -> Mapping[str, Any]:
        return {
            "number": number,
            "state": "open",
            "head": {"sha": self.pull_head},
            "base": {"ref": self.mapping.integration_branch},
        }


class FakeAgentState:
    def __init__(self, mapping: command.RepositoryMapping) -> None:
        self.mapping = mapping
        self.calls: list[tuple[str, str]] = []

    def context(self, repository: str, project: str) -> Mapping[str, Any]:
        return {
            "accepted": True,
            "decision": "allowed",
            "repository": repository,
            "project": project,
            "integration_ref": self.mapping.integration_branch,
        }

    def direct(
        self,
        requested: command.Command,
        target: command.TargetContext,
    ) -> Mapping[str, Any]:
        self.calls.append(("direct", requested.action))
        result: dict[str, Any] = {
            "request_id": requested.request_id,
            "receipt_id": "R-12345678",
            "agent_id": requested.agent_id,
            "agent": {"status": "in-progress", "files": list(requested.files)},
        }
        result[
            {
                "start": "registered",
                "claim": "claimed",
                "release": "released",
                "block": "block",
                "review": "review",
                "done": "done",
                "cancel": "cancel",
            }[requested.action]
        ] = True
        if requested.action in {"block", "review", "done", "cancel"}:
            result["status"] = {
                "block": "blocked",
                "review": "review",
                "done": "done",
                "cancel": "cancelled",
            }[requested.action]
        return result

    def lifecycle_compat(
        self,
        requested: command.Command,
        target: command.TargetContext,
    ) -> Mapping[str, Any]:
        self.calls.append(("lifecycle", requested.action))
        if requested.action == "resume":
            return {
                "accepted": True,
                "decision": "allowed",
                "instruction": "no_active_assignment_session",
                "active_sessions": [],
                "read_only": True,
            }
        return {
            "accepted": True,
            "decision": "allowed",
            "reconciled_base": True,
            "request_id": "internal-compat-id",
            "receipt_id": "R-reconcile",
            "current_base_sha": target.integration_sha,
        }


class AgentStateCommandContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contracts = command.load_contracts(ROOT)
        cls.fixtures = json.loads(
            (ROOT / "tests/fixtures/agent-state-command/cases.json").read_text(
                encoding="utf-8"
            )
        )

    def test_every_supported_action_has_a_valid_fixture(self) -> None:
        expected = set(self.contracts.command["actions"])
        actual = {row["inputs"]["action"] for row in self.fixtures["valid"]}
        self.assertEqual(actual, expected)
        for row in self.fixtures["valid"]:
            prepared = command.validate_inputs(row["inputs"], self.contracts)
            self.assertEqual(prepared.action, row["inputs"]["action"])

    def test_invalid_fixtures_fail_with_exact_instruction(self) -> None:
        for row in self.fixtures["invalid"]:
            with self.subTest(case=row["id"]):
                with self.assertRaises(command.CommandFailure) as caught:
                    command.validate_inputs(row["inputs"], self.contracts)
                self.assertEqual(caught.exception.instruction, row["expected"])

    def test_all_actions_execute_through_the_declared_transport(self) -> None:
        dispatch = command.DispatchContext(actor="mimranfaruqi", central_sha="c" * 40)
        mapping = self.contracts.repositories["StreamScapeTV/iptv-backend"]
        github = FakeGitHub(mapping)
        agent_state = FakeAgentState(mapping)
        for row in self.fixtures["valid"]:
            with self.subTest(action=row["inputs"]["action"]):
                prepared = command.validate_inputs(row["inputs"], self.contracts)
                result = command.execute(
                    prepared,
                    dispatch,
                    self.contracts,
                    github,
                    agent_state,
                )
                self.assertTrue(result["accepted"])
                self.assertEqual(result["request_id"], prepared.request_id)
                if prepared.action != "resume":
                    self.assertTrue(result["receipt_id"])

        expected_calls = [
            (
                "lifecycle"
                if self.contracts.command["actions"][row["inputs"]["action"]][
                    "transport"
                ]
                == "github-lifecycle-compat"
                else "direct",
                row["inputs"]["action"],
            )
            for row in self.fixtures["valid"]
        ]
        self.assertEqual(agent_state.calls, expected_calls)

    def test_dispatch_requires_protected_main_and_authorized_actor(self) -> None:
        base = {
            "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_ACTOR": "mimranfaruqi",
            "GITHUB_TRIGGERING_ACTOR": "mimranfaruqi",
            "GITHUB_SHA": "a" * 40,
        }
        accepted = command.validate_dispatch_environment(base, self.contracts.command)
        self.assertEqual(accepted.actor, "mimranfaruqi")
        for key, value, instruction in (
            (
                "GITHUB_REF",
                "refs/heads/issue/37",
                "agent_state_command_requires_protected_main",
            ),
            ("GITHUB_ACTOR", "unauthorized", "unauthorized_dispatch_actor"),
            (
                "GITHUB_REPOSITORY",
                "StreamScapeTV/iptv-backend",
                "untrusted_dispatch_repository",
            ),
        ):
            environment = dict(base)
            environment[key] = value
            if key == "GITHUB_ACTOR":
                environment["GITHUB_TRIGGERING_ACTOR"] = value
            with self.subTest(key=key):
                with self.assertRaises(command.CommandFailure) as caught:
                    command.validate_dispatch_environment(
                        environment, self.contracts.command
                    )
                self.assertEqual(caught.exception.instruction, instruction)

    def test_project_mapping_and_api_context_must_agree(self) -> None:
        raw = dict(self.fixtures["valid"][0]["inputs"])
        raw["project"] = "agent-state"
        with self.assertRaises(command.CommandFailure) as caught:
            command.validate_inputs(raw, self.contracts)
        self.assertEqual(caught.exception.instruction, "repository_project_mismatch")

        prepared = command.validate_inputs(
            self.fixtures["valid"][0]["inputs"], self.contracts
        )
        mapping = self.contracts.repositories[prepared.repository]
        github = FakeGitHub(mapping)

        class RejectedContext(FakeAgentState):
            def context(self, repository: str, project: str) -> Mapping[str, Any]:
                return {
                    "accepted": False,
                    "decision": "rejected",
                    "instruction": "repository_project_mismatch",
                }

        with self.assertRaises(command.CommandFailure) as caught:
            command.execute(
                prepared,
                command.DispatchContext("mimranfaruqi", "c" * 40),
                self.contracts,
                github,
                RejectedContext(mapping),
            )
        self.assertEqual(caught.exception.instruction, "repository_project_mismatch")

    def test_exact_base_and_pr_head_are_revalidated(self) -> None:
        mapping = self.contracts.repositories["StreamScapeTV/iptv-backend"]
        github = FakeGitHub(mapping)
        state = FakeAgentState(mapping)
        dispatch = command.DispatchContext("mimranfaruqi", "c" * 40)

        start = command.validate_inputs(
            next(
                row["inputs"]
                for row in self.fixtures["valid"]
                if row["inputs"]["action"] == "start"
            ),
            self.contracts,
        )
        github.integration_sha = "d" * 40
        with self.assertRaises(command.CommandFailure) as caught:
            command.execute(start, dispatch, self.contracts, github, state)
        self.assertEqual(caught.exception.instruction, "stale_base_sha")

        review = command.validate_inputs(
            next(
                row["inputs"]
                for row in self.fixtures["valid"]
                if row["inputs"]["action"] == "review"
            ),
            self.contracts,
        )
        github.integration_sha = "a" * 40
        github.pull_head = "e" * 40
        with self.assertRaises(command.CommandFailure) as caught:
            command.execute(review, dispatch, self.contracts, github, state)
        self.assertEqual(caught.exception.instruction, "stale_head_sha")

    def test_branch_is_bound_to_issue_and_session_nonce(self) -> None:
        raw = dict(
            next(
                row["inputs"]
                for row in self.fixtures["valid"]
                if row["inputs"]["action"] == "start"
            )
        )
        raw["branch"] = "issue/38-agent-state-command-a1b2"
        with self.assertRaises(command.CommandFailure) as caught:
            command.validate_inputs(raw, self.contracts)
        self.assertEqual(caught.exception.instruction, "issue_branch_issue_mismatch")

        raw["branch"] = "issue/37-agent-state-command-z9y8"
        with self.assertRaises(command.CommandFailure) as caught:
            command.validate_inputs(raw, self.contracts)
        self.assertEqual(
            caught.exception.instruction,
            "issue_branch_must_end_with_session_nonce",
        )

    def test_sanitized_api_result_redacts_urls_and_secret_assignments(self) -> None:
        prepared = command.validate_inputs(
            next(
                row["inputs"]
                for row in self.fixtures["valid"]
                if row["inputs"]["action"] == "claim"
            ),
            self.contracts,
        )
        result = command.sanitize_result(
            prepared,
            {
                "claimed": True,
                "request_id": prepared.request_id,
                "receipt_id": "R-redacted",
                "instruction": "see https://private.internal/path token=secret-value",
                "warnings": ["Bearer abc.def.ghi"],
                "agent": {"password": "private", "status": "in-progress"},
            },
            self.contracts.command,
        )
        rendered = json.dumps(result)
        self.assertNotIn("private.internal", rendered)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("abc.def.ghi", rendered)
        self.assertNotIn('"password"', rendered)
        self.assertIn("<redacted", rendered)

    def test_request_id_is_stable_and_mutation_result_requires_receipt(self) -> None:
        request_id = "req-stable-1234"
        self.assertEqual(
            command.synthetic_command_id(request_id),
            command.synthetic_command_id(request_id),
        )
        self.assertNotEqual(
            command.synthetic_command_id(request_id),
            command.synthetic_command_id(request_id + "-other"),
        )
        prepared = command.validate_inputs(
            next(
                row["inputs"]
                for row in self.fixtures["valid"]
                if row["inputs"]["action"] == "claim"
            ),
            self.contracts,
        )
        with self.assertRaises(command.CommandFailure) as caught:
            command.sanitize_result(
                prepared,
                {"claimed": True, "request_id": prepared.request_id},
                self.contracts.command,
            )
        self.assertEqual(
            caught.exception.instruction, "accepted_mutation_missing_receipt"
        )

    def test_retry_uses_the_same_request_and_is_bounded(self) -> None:
        calls: list[Mapping[str, Any] | None] = []
        sleeps: list[float] = []

        class RetryHttp:
            def __init__(self) -> None:
                self.sleeper = sleeps.append
                self.count = 0

            def request(
                self,
                method: str,
                url: str,
                *,
                service: str,
                token: str | None = None,
                payload: Mapping[str, Any] | None = None,
                timeout: int = 30,
            ) -> tuple[int, Mapping[str, str], Any]:
                calls.append(payload)
                self.count += 1
                if self.count < 3:
                    raise command.HttpStatusError(
                        "Agent State",
                        423,
                        {"Retry-After": "1"},
                        {
                            "decision": "retry",
                            "retryable": True,
                            "retry_after_seconds": 1,
                        },
                    )
                return 200, {}, {
                    "claimed": True,
                    "receipt_id": "R-retry",
                    "request_id": "req-12345678",
                }

        client = command.AgentStateClient(
            "https://agent-state.internal",
            None,
            RetryHttp(),  # type: ignore[arg-type]
            retry_attempts=5,
            retry_after_max=30,
        )
        payload = {"request_id": "req-12345678"}
        result = client.request("POST", "/test", payload=payload)
        self.assertTrue(result["claimed"])
        self.assertEqual(calls, [payload, payload, payload])
        self.assertEqual(sleeps, [1, 1])

    def test_outage_and_redaction_never_expose_private_values(self) -> None:
        class FailingOpener:
            def __call__(self, request: Any, timeout: int = 30) -> Any:
                raise urllib.error.URLError(
                    "https://private.agent-state.internal/token-secret"
                )

        http = command.JsonHttpClient(opener=FailingOpener())
        with self.assertRaises(command.CommandFailure) as caught:
            http.request(
                "GET",
                "https://private.agent-state.internal/api",
                service="Agent State",
                token="token-secret",
            )
        rendered = json.dumps(
            command.result_for_failure(
                {
                    "request_id": "req-12345678",
                    "repository": "StreamScapeTV/iptv-backend",
                    "project": "iptv-backend",
                    "action": "resume",
                },
                caught.exception,
            )
        )
        self.assertNotIn("private.agent-state.internal", rendered)
        self.assertNotIn("token-secret", rendered)
        self.assertIn("agent_state_unavailable", rendered)

    def test_workflow_inputs_and_project_map_match_the_contract(self) -> None:
        source = (ROOT / ".github/workflows/agent-state-command.yml").read_text(
            encoding="utf-8"
        )
        input_block = source.split("    inputs:\n", 1)[1].split("\npermissions:", 1)[0]
        workflow_inputs = {
            line.strip()[:-1]
            for line in input_block.splitlines()
            if line.startswith("      ")
            and not line.startswith("        ")
            and line.strip().endswith(":")
        }
        self.assertEqual(workflow_inputs, set(self.contracts.command["inputs"]))
        action_block = input_block.split("      action:\n", 1)[1].split(
            "      session_name:\n", 1
        )[0]
        action_options = {
            line.strip()[2:]
            for line in action_block.splitlines()
            if line.strip().startswith("- ")
        }
        self.assertEqual(action_options, set(self.contracts.command["actions"]))
        self.assertEqual(
            list(self.contracts.repositories),
            sorted(self.contracts.repositories, key=str.casefold),
        )

    def test_workflow_is_parameterized_bounded_and_artifact_free(self) -> None:
        source = (ROOT / ".github/workflows/agent-state-command.yml").read_text(
            encoding="utf-8"
        )
        for required in (
            "workflow_dispatch:",
            "runs-on: agent-state",
            'test "${GITHUB_REF}" = "refs/heads/main"',
            "PYTHONPATH=src python3 -m ci_workflows.agent_state_command",
            "AGENT_STATE_COMMAND_RESULT",
            "AGENT_STATE_GITHUB_TOKEN",
        ):
            if required == "AGENT_STATE_COMMAND_RESULT":
                module = (
                    ROOT / "src/ci_workflows/agent_state_command.py"
                ).read_text(encoding="utf-8")
                self.assertIn(required, module)
            else:
                self.assertIn(required, source)
        for forbidden in (
            "issue_comment:",
            "pull_request_target:",
            "secrets: inherit",
            "upload-artifact",
            "payload:",
            "request:",
            "arbitrary_command",
            "runs-on: [self-hosted",
        ):
            self.assertNotIn(forbidden, source)

    def test_source_never_checks_out_or_executes_consumer_code(self) -> None:
        source = (ROOT / ".github/workflows/agent-state-command.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(source.count("actions/checkout@"), 1)
        self.assertIn("ref: ${{ github.sha }}", source)
        self.assertNotIn("repository: ${{ inputs.repository }}", source)
        module = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "src/ci_workflows").glob("agent_state_*.py"))
        )
        for forbidden in ("subprocess", "os.system", "shell=True", "eval(", "exec("):
            self.assertNotIn(forbidden, module)


if __name__ == "__main__":
    unittest.main()
