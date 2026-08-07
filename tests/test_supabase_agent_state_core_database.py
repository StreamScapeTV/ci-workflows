from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from support.supabase_agent_state_postgres import PostgresRuntime, SqlResult  # noqa: E402
FIXTURES = ROOT / "tests" / "fixtures" / "supabase-agent-state" / "core-cases.json"


def _json_sql(value: Any) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if "$agent_state$" in payload:
        raise AssertionError("fixture contains reserved dollar-quote delimiter")
    return f"$agent_state${payload}$agent_state$::jsonb"


def _last_line(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("SQL command returned no output")
    return lines[-1]


def _parse_json(stdout: str) -> Any:
    return json.loads(_last_line(stdout))


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


class SupabaseAgentStateDatabaseTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))
        cls.runtime = PostgresRuntime(ROOT)
        cls.runtime.__enter__()
        cls.database_a = "agent_state_core_a"
        cls.database_b = "agent_state_core_b"
        cls.runtime.create_database(cls.database_a)
        cls.runtime.create_database(cls.database_b)
        cls.migrations_a = cls.runtime.apply_migrations(cls.database_a)
        cls.migrations_b = cls.runtime.apply_migrations(cls.database_b)

    @classmethod
    def tearDownClass(cls) -> None:
        runtime = getattr(cls, "runtime", None)
        if runtime is not None:
            for database in (getattr(cls, "database_a", ""), getattr(cls, "database_b", "")):
                if database:
                    try:
                        runtime.drop_database(database)
                    except Exception:
                        pass
            runtime.__exit__(None, None, None)

    def call(self, request: Any, *, database: str | None = None) -> dict[str, Any]:
        target = database or self.database_a
        result = self.runtime.psql(
            target,
            f"select agent_api.command({_json_sql(request)})::text;",
            role="service_role",
        )
        response = _parse_json(result.stdout)
        self.assertIsInstance(response, dict)
        return response

    def call_error(
        self,
        request: Any,
        expected: str,
        *,
        database: str | None = None,
    ) -> SqlResult:
        target = database or self.database_a
        result = self.runtime.psql(
            target,
            f"select agent_api.command({_json_sql(request)})::text;",
            role="service_role",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected, result.stderr)
        return result

    def scalar(self, sql: str, *, database: str | None = None, role: str | None = None) -> str:
        result = self.runtime.psql(database or self.database_a, sql, role=role)
        return _last_line(result.stdout)

    def error_sql(self, sql: str, expected: str, *, role: str | None = None) -> SqlResult:
        result = self.runtime.psql(self.database_a, sql, role=role, check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected, result.stderr)
        return result

    def base_request(self, **overrides: Any) -> dict[str, Any]:
        fixture = self.fixture
        request = {
            "contract_version": fixture["contract_version"],
            "project": fixture["project"],
            "repository": fixture["repository"],
        }
        request.update(overrides)
        return request

    def session_request(self, agent: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        request = self.base_request(
            session_name=agent["session_name"],
            agent_id=agent["agent_id"],
            issue_number=agent["issue_number"],
            branch=agent["branch"],
        )
        request.update(overrides)
        return request

    def assert_redacted(self, response: Any, secret: str | None = None) -> None:
        forbidden_keys = {
            "project_id",
            "session_id",
            "request_hash",
            "database_url",
            "authorization",
            "sql",
        }
        for item in _walk(response):
            if isinstance(item, str):
                self.assertNotIn(item.lower(), forbidden_keys)
                if secret:
                    self.assertNotIn(secret, item)

    def test_reconstruction_contract_and_transactions(self) -> None:
        fixture = self.fixture
        primary = fixture["primary"]
        checked_negative: set[str] = set()

        # Empty-database reconstruction is deterministic and uses only committed migrations.
        self.assertEqual([path.name for path in self.migrations_a], [path.name for path in self.migrations_b])
        expected_migrations = [
            "20260806172100_agent_state_core_schema.sql",
            "20260806172200_agent_state_core_rpc.sql",
            "20260806172300_agent_state_core_indexes.sql",
        ]
        self.assertEqual([path.name for path in self.migrations_a], expected_migrations)
        dump_a = self.runtime.schema_dump(self.database_a)
        dump_b = self.runtime.schema_dump(self.database_b)
        self.assertEqual(dump_a, dump_b)
        schema_sha256 = hashlib.sha256(dump_a.encode("utf-8")).hexdigest()
        self.assertRegex(schema_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(self.scalar("select count(*) from agent_private.projects;"), "12")
        self.assertEqual(self.scalar("select count(*) from agent_private.project_slots;"), "216")

        # Every positive action is exercised through the approved service_role RPC.
        resume_empty = self.call(
            self.base_request(
                contract_version=1,
                request_id="issue52-test-resume-empty-0001",
                action="resume",
                session_name=primary["session_name"],
            )
        )
        self.assertEqual(resume_empty["decision"], "no-active-work")
        self.assert_redacted(resume_empty)

        start_request = self.base_request(
            request_id="issue52-test-start-0001",
            action="start",
            session_name=primary["session_name"],
            agent_id=primary["agent_id"],
            task=primary["task"],
            issue_number=primary["issue_number"],
            branch=primary["branch"],
            branch_nonce="a1b2",
            base_sha=fixture["base_sha"],
            claims=primary["initial_claims"],
        )
        started = self.call(start_request)
        self.assertTrue(started["accepted"])
        self.assertEqual(started["decision"], "started")
        self.assertEqual(len(started["session"]["claims"]), 6)
        self.assert_redacted(started)

        replay = self.call(start_request)
        self.assertEqual(replay, started)
        conflicting = dict(start_request)
        conflicting["task"] = "Changed request under immutable ID"
        conflict_result = self.call_error(conflicting, "agent_state:request_id_reuse_conflict")
        checked_negative.add("conflicting-request-reuse")
        self.assertNotIn(conflicting["task"], conflict_result.stderr)

        resume_active = self.call(
            self.base_request(
                request_id="issue52-test-resume-active-0001",
                action="resume",
                session_name=primary["session_name"],
            )
        )
        self.assertEqual(resume_active["decision"], "resume")

        claimed = self.call(
            self.session_request(
                primary,
                request_id="issue52-test-claim-0001",
                action="claim",
                claims=[primary["extra_claim"]],
            )
        )
        self.assertEqual(claimed["decision"], "claimed")
        self.assertEqual(claimed["claim_count"], 1)

        released = self.call(
            self.session_request(
                primary,
                request_id="issue52-test-release-0001",
                action="release",
                claims=[primary["extra_claim"]],
            )
        )
        self.assertEqual(released["decision"], "released")
        self.assertEqual(released["released_count"], 1)

        reconciled = self.call(
            self.session_request(
                primary,
                request_id="issue52-test-reconcile-0001",
                action="reconcile_base",
                expected_base_sha=fixture["base_sha"],
                base_sha=fixture["new_base_sha"],
            )
        )
        self.assertEqual(reconciled["decision"], "base-reconciled")
        self.assertEqual(reconciled["session"]["base_sha"], fixture["new_base_sha"])

        blocked = self.call(
            self.session_request(
                primary,
                request_id="issue52-test-block-0001",
                action="block",
                reason="Disposable blocker verification",
            )
        )
        self.assertEqual(blocked["decision"], "blocked")

        reviewed = self.call(
            self.session_request(
                primary,
                request_id="issue52-test-review-0001",
                action="review",
                pr_number=primary["pr_number"],
                head_sha=fixture["head_sha"],
                summary="Disposable exact-head review verification",
            )
        )
        self.assertEqual(reviewed["decision"], "review")

        context = _parse_json(
            self.runtime.psql(
                self.database_a,
                "select agent_api.context('ci-workflows','StreamScapeTV/ci-workflows',"
                f"'{primary['agent_id']}')::text;",
                role="service_role",
            ).stdout
        )
        self.assertEqual(context["session"]["agent_id"], primary["agent_id"])
        self.assertNotIn("payload", json.dumps(context, sort_keys=True))
        self.assert_redacted(context)

        ownership = _parse_json(
            self.runtime.psql(
                self.database_a,
                "select agent_api.ownership_check('ci-workflows','StreamScapeTV/ci-workflows',"
                f"{primary['issue_number']},'{primary['branch']}',{primary['pr_number']},"
                f"'{fixture['head_sha']}')::text;",
                role="service_role",
            ).stdout
        )
        self.assertTrue(ownership["owned"])
        self.assert_redacted(ownership)

        done = self.call(
            self.session_request(
                primary,
                request_id="issue52-test-done-0001",
                action="done",
                pr_number=primary["pr_number"],
                head_sha=fixture["head_sha"],
                merge_sha=fixture["merge_sha"],
                summary="Disposable terminal verification",
            )
        )
        self.assertEqual(done["decision"], "done")
        self.assertEqual(done["released_count"], 6)
        self.assertEqual(done["session"]["claims"], [])

        terminal_result = self.call_error(
            self.session_request(
                primary,
                request_id="issue52-test-terminal-mutation-0001",
                action="block",
                reason="Must fail after terminal state",
            ),
            "agent_state:session_assertion_failed",
        )
        checked_negative.add("terminal-session-mutation")
        self.assertNotIn("Must fail", terminal_result.stderr)

        # Atomic start-plus-claim rolls back the entire command on an injected claim failure.
        self.runtime.psql(
            self.database_a,
            """
            create function agent_private.test_reject_claim() returns trigger
            language plpgsql as $$begin
              if new.value='resource:atomic-start-failure' then
                raise exception 'test:atomic_start_failure';
              end if;
              return new;
            end$$;
            create trigger test_reject_claim before insert on agent_private.claims
            for each row execute function agent_private.test_reject_claim();
            """,
        )
        atomic_agent = {
            "session_name": "Agent 7",
            "agent_id": "gpt-agent-7-20260806-2000-a7b8",
            "issue_number": 910007,
            "branch": "issue/910007-atomic-start-a7b8",
        }
        atomic_start_id = "issue52-test-atomic-start-0001"
        atomic_start = self.session_request(
            atomic_agent,
            request_id=atomic_start_id,
            action="start",
            task="Atomic start rollback verification",
            base_sha=fixture["new_base_sha"],
            claims=[{"kind": "resource", "mode": "exact", "value": "resource:atomic-start-failure"}],
        )
        self.call_error(atomic_start, "test:atomic_start_failure")
        checked_negative.add("atomic-start-rollback")
        self.assertEqual(
            self.scalar(
                f"select count(*) from agent_private.requests where request_id='{atomic_start_id}';"
            ),
            "0",
        )
        self.assertEqual(
            self.scalar(
                f"select count(*) from agent_private.work_sessions where agent_id='{atomic_agent['agent_id']}';"
            ),
            "0",
        )
        self.runtime.psql(
            self.database_a,
            "drop trigger test_reject_claim on agent_private.claims; "
            "drop function agent_private.test_reject_claim();",
        )

        # Atomic terminal-plus-release rolls back released claims and the request ledger.
        atomic_start["request_id"] = "issue52-test-atomic-start-ok-0001"
        atomic_start["claims"] = [
            {"kind": "resource", "mode": "exact", "value": "resource:atomic-terminal"}
        ]
        atomic_started = self.call(atomic_start)
        self.assertEqual(atomic_started["decision"], "started")
        atomic_review = self.call(
            self.session_request(
                atomic_agent,
                request_id="issue52-test-atomic-review-0001",
                action="review",
                pr_number=920007,
                head_sha=fixture["head_sha"],
            )
        )
        self.assertEqual(atomic_review["decision"], "review")
        self.runtime.psql(
            self.database_a,
            f"""
            create function agent_private.test_reject_terminal() returns trigger
            language plpgsql as $$begin
              if new.agent_id='{atomic_agent['agent_id']}' and new.status='done' then
                raise exception 'test:atomic_terminal_failure';
              end if;
              return new;
            end$$;
            create trigger test_reject_terminal before update on agent_private.work_sessions
            for each row execute function agent_private.test_reject_terminal();
            """,
        )
        failed_done_id = "issue52-test-atomic-done-fail-0001"
        failed_done = self.session_request(
            atomic_agent,
            request_id=failed_done_id,
            action="done",
            pr_number=920007,
            head_sha=fixture["head_sha"],
            merge_sha=fixture["merge_sha"],
            summary="Atomic terminal rollback verification",
        )
        self.call_error(failed_done, "test:atomic_terminal_failure")
        checked_negative.add("atomic-terminal-rollback")
        self.assertEqual(
            self.scalar(
                "select status from agent_private.work_sessions "
                f"where agent_id='{atomic_agent['agent_id']}';"
            ),
            "review",
        )
        self.assertEqual(
            self.scalar(
                "select count(*) from agent_private.claims c join agent_private.work_sessions s "
                "on s.id=c.session_id where c.active and "
                f"s.agent_id='{atomic_agent['agent_id']}';"
            ),
            "1",
        )
        self.assertEqual(
            self.scalar(
                f"select count(*) from agent_private.requests where request_id='{failed_done_id}';"
            ),
            "0",
        )
        self.runtime.psql(
            self.database_a,
            "drop trigger test_reject_terminal on agent_private.work_sessions; "
            "drop function agent_private.test_reject_terminal();",
        )
        failed_done["request_id"] = "issue52-test-atomic-done-ok-0001"
        self.assertEqual(self.call(failed_done)["decision"], "done")

        # A separate cancellation path releases all claims atomically.
        cancel_agent = {
            "session_name": "Agent 2",
            "agent_id": "gpt-agent-2-20260806-2000-b2c3",
            "issue_number": 910002,
            "branch": "issue/910002-cancel-flow-b2c3",
        }
        self.assertEqual(
            self.call(
                self.session_request(
                    cancel_agent,
                    request_id="issue52-test-cancel-start-0001",
                    action="start",
                    task="Cancellation release verification",
                    base_sha=fixture["new_base_sha"],
                    claims=[{"kind": "device", "mode": "exact", "value": "device:cancel-flow"}],
                )
            )["decision"],
            "started",
        )
        cancelled = self.call(
            self.session_request(
                cancel_agent,
                request_id="issue52-test-cancel-0001",
                action="cancel",
                summary="Disposable cancellation verification",
            )
        )
        self.assertEqual(cancelled["decision"], "cancelled")
        self.assertEqual(cancelled["released_count"], 1)
        self.assertEqual(cancelled["session"]["claims"], [])

        # Concurrent overlapping work converges to one owner and one deterministic rejection.
        agents = fixture["concurrency"]["agents"]
        overlap_requests = [
            self.session_request(
                agents[0],
                request_id="issue52-test-overlap-a-0001",
                action="start",
                task="Concurrent overlap contender A",
                base_sha=fixture["new_base_sha"],
                claims=[
                    {
                        "kind": "file",
                        "mode": "prefix",
                        "value": fixture["concurrency"]["overlap_prefix"],
                    }
                ],
            ),
            self.session_request(
                agents[1],
                request_id="issue52-test-overlap-b-0001",
                action="start",
                task="Concurrent overlap contender B",
                base_sha=fixture["new_base_sha"],
                claims=[
                    {
                        "kind": "file",
                        "mode": "exact",
                        "value": fixture["concurrency"]["overlap_prefix"] + "/child.sql",
                    }
                ],
            ),
        ]
        overlap_processes = []
        for request in overlap_requests:
            process = self.runtime.popen_psql(self.database_a, "")
            overlap_processes.append(
                (
                    process,
                    "set role service_role;\n"
                    f"select agent_api.command({_json_sql(request)})::text;\n",
                )
            )
        overlap_responses = []
        for process, sql in overlap_processes:
            stdout, stderr = process.communicate(sql, timeout=120)
            self.assertEqual(process.returncode, 0, stderr)
            overlap_responses.append(_parse_json(stdout))
        accepted = [response for response in overlap_responses if response["accepted"]]
        rejected = [response for response in overlap_responses if not response["accepted"]]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["decision"], "claim-conflict")
        self.assertEqual(
            rejected[0]["collision"]["owner_agent_id"], accepted[0]["session"]["agent_id"]
        )

        # Concurrent disjoint claims both succeed despite project-level serialization.
        disjoint_requests = [
            self.session_request(
                agents[2],
                request_id="issue52-test-disjoint-a-0001",
                action="start",
                task="Concurrent disjoint contender A",
                base_sha=fixture["new_base_sha"],
                claims=[
                    {
                        "kind": "file",
                        "mode": "exact",
                        "value": fixture["concurrency"]["disjoint_a"],
                    }
                ],
            ),
            self.session_request(
                agents[3],
                request_id="issue52-test-disjoint-b-0001",
                action="start",
                task="Concurrent disjoint contender B",
                base_sha=fixture["new_base_sha"],
                claims=[
                    {
                        "kind": "file",
                        "mode": "exact",
                        "value": fixture["concurrency"]["disjoint_b"],
                    }
                ],
            ),
        ]
        disjoint_processes = []
        for request in disjoint_requests:
            process = self.runtime.popen_psql(self.database_a, "")
            disjoint_processes.append(
                (
                    process,
                    "set role service_role;\n"
                    f"select agent_api.command({_json_sql(request)})::text;\n",
                )
            )
        disjoint_responses = []
        for process, sql in disjoint_processes:
            stdout, stderr = process.communicate(sql, timeout=120)
            self.assertEqual(process.returncode, 0, stderr)
            disjoint_responses.append(_parse_json(stdout))
        self.assertTrue(all(response["accepted"] for response in disjoint_responses))
        self.assertEqual({response["decision"] for response in disjoint_responses}, {"started"})

        # Every non-file claim kind has deterministic exact-identity collision behavior.
        kind_owner = {
            "session_name": "Agent 6",
            "agent_id": "gpt-agent-6-20260806-2000-h6i7",
            "issue_number": 910010,
            "branch": "issue/910010-kind-owner-h6i7",
        }
        kind_claims = [
            {"kind": "package", "mode": "exact", "value": "package:collision-proof"},
            {"kind": "resource", "mode": "exact", "value": "resource:collision-proof"},
            {"kind": "manifest", "mode": "exact", "value": "manifest:collision-proof"},
            {"kind": "device", "mode": "exact", "value": "device:collision-proof"},
        ]
        self.assertEqual(
            self.call(
                self.session_request(
                    kind_owner,
                    request_id="issue52-test-kind-owner-start-0001",
                    action="start",
                    task="Non-file collision owner",
                    base_sha=fixture["new_base_sha"],
                    claims=kind_claims,
                )
            )["decision"],
            "started",
        )
        kind_contender = {
            "session_name": "Agent 9",
            "agent_id": "gpt-agent-9-20260806-2000-i9j0",
            "issue_number": 910011,
            "branch": "issue/910011-kind-contender-i9j0",
        }
        for index, claim in enumerate(kind_claims, start=1):
            response = self.call(
                self.session_request(
                    kind_contender,
                    request_id=f"issue52-test-kind-conflict-{index:04d}",
                    action="start",
                    task=f"{claim['kind']} collision contender",
                    base_sha=fixture["new_base_sha"],
                    claims=[claim],
                )
            )
            self.assertFalse(response["accepted"])
            self.assertEqual(response["decision"], "claim-conflict")
            self.assertEqual(response["collision"]["kind"], claim["kind"])
            self.assertEqual(response["collision"]["owner_agent_id"], kind_owner["agent_id"])
            checked_negative.add(f"{claim['kind']}-conflict")
        self.assertEqual(
            self.call(
                self.session_request(
                    kind_owner,
                    request_id="issue52-test-kind-owner-cancel-0001",
                    action="cancel",
                    summary="Non-file collision cleanup",
                )
            )["decision"],
            "cancelled",
        )

        # Negative request fixtures exercise malformed, stale, unsafe, grant, and immutability paths.
        self.call_error([], "agent_state:request_must_be_object")
        checked_negative.add("request-not-object")

        unknown = self.base_request(
            request_id="issue52-test-unknown-field-0001",
            action="resume",
            session_name="Agent 9",
            unexpected=True,
        )
        self.call_error(unknown, "agent_state:unknown_field")
        checked_negative.add("unknown-top-field")

        def invalid_start(request_id: str, *, session_name: str = "Agent 9", **changes: Any):
            request = self.base_request(
                request_id=request_id,
                action="start",
                session_name=session_name,
                agent_id="gpt-agent-9-20260806-2000-i9j0",
                task="Negative fixture",
                issue_number=910009,
                branch="issue/910009-negative-i9j0",
                base_sha=fixture["new_base_sha"],
                claims=[],
            )
            request.update(changes)
            return request

        self.call_error(
            invalid_start(
                "issue52-test-unknown-claim-0001",
                claims=[{"kind": "file", "mode": "exact", "value": "safe.sql", "extra": 1}],
            ),
            "agent_state:unknown_field",
        )
        checked_negative.add("unknown-claim-field")
        self.call_error(
            invalid_start("issue52-test-overlong-task-0001", task="x" * 2001),
            "agent_state:field_length",
        )
        checked_negative.add("overlong-task")
        fake_secret = "password=DO_NOT_EXPOSE_ISSUE52"
        sensitive_error = self.call_error(
            invalid_start("issue52-test-sensitive-task-0001", task=fake_secret),
            "agent_state:sensitive_text_rejected",
        )
        checked_negative.add("sensitive-task")
        self.assertNotIn(fake_secret, sensitive_error.stderr)
        self.call_error(
            invalid_start(
                "issue52-test-project-mismatch-0001",
                repository="StreamScapeTV/not-the-project",
            ),
            "agent_state:project_mismatch",
        )
        checked_negative.add("wrong-project-repository")
        self.call_error(
            invalid_start("issue52-test-invalid-session-0001", session_name="Agent zero"),
            "agent_state:invalid_session_name",
        )
        checked_negative.add("invalid-session-name")
        self.call_error(
            invalid_start(
                "issue52-test-profile-mismatch-0001",
                agent_id="cod-agent-9-20260806-2000-i9j0",
            ),
            "agent_state:invalid_agent_id",
        )
        checked_negative.add("wrong-agent-profile")
        self.call_error(
            invalid_start(
                "issue52-test-slot-mismatch-0001",
                agent_id="gpt-agent-8-20260806-2000-i9j0",
            ),
            "agent_state:invalid_agent_id",
        )
        checked_negative.add("wrong-agent-slot")

        unsafe_claims = {
            "unsafe-absolute-path": "/absolute/path.sql",
            "unsafe-traversal-path": "safe/../escape.sql",
            "unsafe-double-separator": "safe//ambiguous.sql",
            "unsafe-backslash": "safe\\windows.sql",
        }
        for index, (case_id, value) in enumerate(unsafe_claims.items(), start=1):
            self.call_error(
                invalid_start(
                    f"issue52-test-unsafe-path-{index:04d}",
                    claims=[{"kind": "file", "mode": "exact", "value": value}],
                ),
                "agent_state:unsafe_path",
            )
            checked_negative.add(case_id)
        duplicate = {"kind": "file", "mode": "exact", "value": "duplicate.sql"}
        self.call_error(
            invalid_start(
                "issue52-test-duplicate-claim-0001",
                claims=[duplicate, duplicate],
            ),
            "agent_state:duplicate_claim",
        )
        checked_negative.add("duplicate-claim")
        self.call_error(
            invalid_start(
                "issue52-test-prefix-non-file-0001",
                claims=[{"kind": "package", "mode": "prefix", "value": "package:bad"}],
            ),
            "agent_state:prefix_requires_file",
        )
        checked_negative.add("prefix-non-file")
        self.call_error(
            invalid_start("issue52-test-stale-base-start-0001", base_sha=fixture["base_sha"]),
            "agent_state:stale_base_assertion",
        )
        checked_negative.add("stale-base-start")

        negative_agent = {
            "session_name": "Agent 5",
            "agent_id": "gpt-agent-5-20260806-2000-g5h6",
            "issue_number": 910008,
            "branch": "issue/910008-negative-flow-g5h6",
        }
        self.assertEqual(
            self.call(
                self.session_request(
                    negative_agent,
                    request_id="issue52-test-negative-start-0001",
                    action="start",
                    task="Negative transition fixture",
                    base_sha=fixture["new_base_sha"],
                    claims=[],
                )
            )["decision"],
            "started",
        )
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-stale-base-reconcile-0001",
                action="reconcile_base",
                expected_base_sha=fixture["base_sha"],
                base_sha=fixture["new_base_sha"],
            ),
            "agent_state:stale_base_assertion",
        )
        checked_negative.add("stale-base-reconcile")
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-stale-pr-claim-0001",
                action="claim",
                pr_number=999999,
                claims=[{"kind": "file", "mode": "exact", "value": "stale-pr.sql"}],
            ),
            "agent_state:stale_pr_assertion",
        )
        checked_negative.add("stale-pr-claim")
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-stale-head-claim-0001",
                action="claim",
                head_sha=fixture["head_sha"],
                claims=[{"kind": "file", "mode": "exact", "value": "stale-head.sql"}],
            ),
            "agent_state:stale_head_assertion",
        )
        checked_negative.add("stale-head-claim")
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-wrong-issue-0001",
                action="block",
                issue_number=999999,
                reason="wrong issue",
            ),
            "agent_state:session_assertion_failed",
        )
        checked_negative.add("wrong-issue")
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-wrong-branch-0001",
                action="block",
                branch="issue/910008-wrong-branch-z9y8",
                reason="wrong branch",
            ),
            "agent_state:session_assertion_failed",
        )
        checked_negative.add("wrong-branch")
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-done-before-review-0001",
                action="done",
                merge_sha=fixture["merge_sha"],
                summary="must fail",
            ),
            "agent_state:review_status_required",
        )
        checked_negative.add("done-before-review")
        self.assertEqual(
            self.call(
                self.session_request(
                    negative_agent,
                    request_id="issue52-test-negative-review-0001",
                    action="review",
                    pr_number=920008,
                    head_sha=fixture["head_sha"],
                )
            )["decision"],
            "review",
        )
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-stale-pr-review-0001",
                action="review",
                pr_number=920009,
                head_sha=fixture["head_sha"],
            ),
            "agent_state:stale_pr_assertion",
        )
        checked_negative.add("stale-pr-review")
        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-stale-head-review-0001",
                action="review",
                pr_number=920008,
                head_sha="5555555555555555555555555555555555555555",
            ),
            "agent_state:stale_head_assertion",
        )
        checked_negative.add("stale-head-review")
        self.assertEqual(
            self.call(
                self.session_request(
                    negative_agent,
                    request_id="issue52-test-negative-cancel-0001",
                    action="cancel",
                    summary="Negative fixture cleanup",
                )
            )["decision"],
            "cancelled",
        )

        self.call_error(
            self.session_request(
                negative_agent,
                request_id="issue52-test-ambiguous-release-0001",
                action="release",
                all=True,
                claims=[{"kind": "file", "mode": "exact", "value": "ambiguous.sql"}],
            ),
            "agent_state:session_assertion_failed",
        )
        # Use a live disjoint session to prove ambiguous release input is rejected before mutation.
        live_disjoint = agents[2]
        self.call_error(
            self.session_request(
                live_disjoint,
                request_id="issue52-test-ambiguous-release-live-0001",
                action="release",
                all=True,
                claims=[
                    {
                        "kind": "file",
                        "mode": "exact",
                        "value": fixture["concurrency"]["disjoint_a"],
                    }
                ],
            ),
            "agent_state:ambiguous_release_request",
        )
        checked_negative.add("ambiguous-release")

        # Grants: ordinary roles cannot access tables; only service_role can call the API.
        self.error_sql(
            "select count(*) from agent_private.projects;",
            "permission denied",
            role="service_role",
        )
        checked_negative.add("direct-table-service-role")
        self.error_sql(
            "select agent_api.resume('ci-workflows','StreamScapeTV/ci-workflows','Agent 1');",
            "permission denied",
            role="anon",
        )
        checked_negative.add("rpc-anon")
        self.error_sql(
            "select agent_api.resume('ci-workflows','StreamScapeTV/ci-workflows','Agent 1');",
            "permission denied",
            role="authenticated",
        )
        checked_negative.add("rpc-authenticated")

        self.error_sql(
            "update agent_private.requests set action='cancel' "
            "where request_id='issue52-test-start-0001';",
            "agent_state:immutable_record",
        )
        checked_negative.add("immutable-request")
        self.error_sql(
            "delete from agent_private.command_receipts "
            "where request_id='issue52-test-start-0001';",
            "agent_state:immutable_record",
        )
        checked_negative.add("immutable-receipt")
        self.error_sql(
            "delete from agent_private.events "
            "where request_id='issue52-test-start-0001';",
            "agent_state:immutable_record",
        )
        checked_negative.add("append-only-event")

        # Receipts/events are one-to-one and responses expose no private IDs or request hashes.
        receipt_count = int(self.scalar("select count(*) from agent_private.command_receipts;"))
        request_count = int(self.scalar("select count(*) from agent_private.requests;"))
        event_count = int(self.scalar("select count(*) from agent_private.events;"))
        self.assertEqual(receipt_count, request_count)
        self.assertEqual(event_count, receipt_count)
        forbidden_projection = self.scalar(
            "select count(*) from agent_private.command_receipts "
            "where response ?| array['project_id','session_id','request_hash','database_url','authorization','sql'];"
        )
        self.assertEqual(forbidden_projection, "0")

        # Dispose all remaining sessions through bounded cancellation before the database is dropped.
        for agent in agents:
            status = self.scalar(
                "select coalesce(max(status),'') from agent_private.work_sessions "
                f"where agent_id='{agent['agent_id']}';"
            )
            if status in {"active", "blocked", "review"}:
                response = self.call(
                    self.session_request(
                        agent,
                        request_id=f"issue52-test-cleanup-{agent['issue_number']}-0001",
                        action="cancel",
                        summary="Disposable concurrency cleanup",
                    )
                )
                self.assertEqual(response["decision"], "cancelled")
        active_count = self.scalar(
            "select count(*) from agent_private.work_sessions "
            "where status in('active','blocked','review');"
        )
        self.assertEqual(active_count, "0")
        self.assertEqual(
            self.scalar("select count(*) from agent_private.claims where active;"),
            "0",
        )

        expected_negative = {case["id"] for case in fixture["negative_cases"]}
        self.assertEqual(checked_negative, expected_negative)

        # Drop both disposable databases to prove complete cleanup and absence of retained test rows.
        self.runtime.drop_database(self.database_b)
        self.runtime.drop_database(self.database_a)
        self.assertEqual(
            self.runtime.psql(
                "postgres",
                "select count(*) from pg_database "
                "where datname in('agent_state_core_a','agent_state_core_b');",
            ).stdout,
            "0",
        )
        self.database_a = ""
        self.database_b = ""

        print(
            json.dumps(
                {
                    "postgres_version": "17.6",
                    "migration_count": len(expected_migrations),
                    "schema_sha256": schema_sha256,
                    "positive_actions": sorted({item["action"] for item in fixture["positive_actions"]}),
                    "negative_cases": len(expected_negative),
                    "overlap_responses": "one accepted, one rejected",
                    "disjoint_responses": "both accepted",
                    "active_sessions_after_cleanup": 0,
                    "active_claims_after_cleanup": 0,
                    "disposable_databases_after_cleanup": 0,
                    "drop_database": "passed",
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
