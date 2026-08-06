from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "supabase-agent-state-core.json"
FILE_MANIFEST_PATH = ROOT / "contracts" / "supabase-agent-state-core-files.json"
REHOME_PATH = ROOT / "contracts" / "supabase-agent-state-core-rehome.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "supabase-agent-state" / "core-cases.json"


class SupabaseAgentStateCoreSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.file_manifest = json.loads(FILE_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.rehome = json.loads(REHOME_PATH.read_text(encoding="utf-8"))
        cls.migration_paths = [ROOT / value for value in cls.contract["migration_order"]]
        cls.sql = "\n".join(path.read_text(encoding="utf-8") for path in cls.migration_paths)

    def test_exact_ordered_migration_history(self) -> None:
        self.assertEqual(
            [path.name for path in self.migration_paths],
            [
                "20260806172100_agent_state_core_schema.sql",
                "20260806172200_agent_state_core_rpc.sql",
                "20260806172300_agent_state_core_indexes.sql",
            ],
        )
        self.assertTrue(all(path.is_file() for path in self.migration_paths))
        self.assertEqual(
            sorted(path.name for path in (ROOT / "supabase" / "migrations").glob("*.sql")),
            [path.name for path in self.migration_paths],
        )
        for path in self.migration_paths:
            source = path.read_text(encoding="utf-8")
            self.assertIn("Issue #52", source)
            self.assertRegex(source, r"(?m)^begin;\s*$")
            self.assertRegex(source, r"(?m)^commit;\s*$")
        self.assertNotIn("pg_get_functiondef", self.sql)
        self.assertNotRegex(self.sql.lower(), r"\bexecute\s+(?:format|v_definition)")

    def test_contract_and_action_fixture_parity(self) -> None:
        actions = [
            "resume",
            "start",
            "claim",
            "release",
            "reconcile_base",
            "block",
            "review",
            "done",
            "cancel",
        ]
        self.assertEqual(list(self.contract["actions"]), actions)
        self.assertEqual({item["action"] for item in self.fixture["positive_actions"]}, set(actions))
        self.assertEqual(set(self.fixture["negative_by_action"]), set(actions))
        negative_ids = {item["id"] for item in self.fixture["negative_cases"]}
        for action, case_ids in self.fixture["negative_by_action"].items():
            self.assertTrue(case_ids, action)
            self.assertLessEqual(set(case_ids), negative_ids)
        self.assertEqual(self.contract["source_status"], "pre-deployment-review-package")
        self.assertEqual(self.contract["future_extensions"], ["#53", "#54", "#55"])

    def test_normalized_schema_and_project_mappings(self) -> None:
        for table in (
            "projects",
            "profiles",
            "project_slots",
            "work_sessions",
            "work_bindings",
            "work_evidence",
            "requests",
            "command_receipts",
            "claims",
            "events",
        ):
            self.assertRegex(self.sql, rf"create table agent_private\.{table}\b")
        self.assertNotRegex(self.sql, r"create table\s+agent_private\.[a-z0-9_]+_(?:iptv|flux|media)")
        consumers = json.loads((ROOT / "contracts" / "consumers.json").read_text(encoding="utf-8"))
        expected = {
            item["repository"]: item["integration_branch"]
            for item in consumers["repositories"]
            if item["agent_state_mapping_status"] == "established"
        }
        expected["StreamScapeTV/organization-rules"] = "main"
        expected["StreamScapeTV/agent-state-supabase"] = "main"
        recorded = {item["repository"]: item["integration_branch"] for item in self.contract["projects"]}
        self.assertEqual(recorded, expected)
        for repository in expected:
            self.assertIn(repository, self.sql)

    def test_atomicity_replay_collision_and_transition_guards(self) -> None:
        for token in (
            "pg_advisory_xact_lock",
            "for update",
            "request_hash",
            "request_id_reuse_conflict",
            "work_sessions_one_current_per_slot",
            "work_bindings_active_issue",
            "work_bindings_active_branch",
            "find_claim_collision",
            "released_request_id",
            "review_status_required",
            "stale_base_assertion",
            "stale_pr_assertion",
            "stale_head_assertion",
        ):
            self.assertIn(token, self.sql)
        self.assertIn("requests_immutable", self.sql)
        self.assertIn("receipts_immutable", self.sql)
        self.assertIn("events_append_only", self.sql)
        self.assertTrue(self.contract["atomicity"]["start_plus_initial_claims"])
        self.assertTrue(self.contract["atomicity"]["done_plus_release"])
        self.assertTrue(self.contract["atomicity"]["cancel_plus_release"])

    def test_claim_validation_and_redaction_guards(self) -> None:
        for kind in ("file", "package", "resource", "manifest", "device"):
            self.assertIn(f"'{kind}'", self.sql)
        for token in (
            "prefix_requires_file",
            "unsafe_path",
            "unsafe_claim_identifier",
            "duplicate_claim",
            "sensitive_text_rejected",
        ):
            self.assertIn(token, self.sql)
        self.assertIn("context_event_limit", self.contract["redaction"])
        self.assertFalse(self.contract["redaction"]["context_event_payload_exposed"])
        self.assertNotIn("payload", self.contract["redaction"]["receipt_forbidden_fields"])

    def test_rpc_only_grants_and_bounded_security_definers(self) -> None:
        lower = self.sql.lower()
        for role in ("public", "anon", "authenticated", "service_role"):
            self.assertRegex(
                lower,
                rf"revoke all on all tables in schema agent_private from[^;]*\b{role}\b",
            )
        normalized = re.sub(r"\s+", " ", lower)
        for signature in (
            "agent_api.command(jsonb)",
            "agent_api.resume(text, text, text)",
            "agent_api.context(text, text, text)",
            "agent_api.ownership_check(text, text, bigint, text, bigint, text)",
        ):
            self.assertIn(f"grant execute on function {signature} to service_role", normalized)
        self.assertNotIn("grant select on", lower)
        self.assertNotIn("grant insert on", lower)
        self.assertNotIn("grant update on", lower)
        self.assertNotIn("grant delete on", lower)
        self.assertGreaterEqual(lower.count("security definer"), 4)
        self.assertGreaterEqual(lower.count("set search_path"), 4)
        self.assertGreaterEqual(lower.count("set row_security = off"), 4)

    def test_disposable_runtime_is_pinned_and_cleanup_owned(self) -> None:
        helper = (
            ROOT / "tests" / "support" / "supabase_agent_state_postgres.py"
        ).read_text(encoding="utf-8")
        database_test = (
            ROOT / "tests" / "test_supabase_agent_state_core_database.py"
        ).read_text(encoding="utf-8")
        self.assertIn('POSTGRES_VERSION = "17.6"', helper)
        self.assertIn(
            "2910b85283674da2dae6ac13fe5ebbaaf3c482446396cba32e6728d3cc736d86",
            helper,
        )
        self.assertIn("PostgreSQL source digest mismatch", helper)
        self.assertIn("shutil.rmtree(self.root", helper)
        for proof in (
            "atomic_start_failure",
            "atomic_terminal_failure",
            "overlap_responses",
            "disjoint_responses",
            "schema_sha256",
            "drop_database",
        ):
            self.assertIn(proof, database_test)

    def test_source_evidence_does_not_claim_live_deployment(self) -> None:
        evidence = (
            ROOT / "docs" / "evidence" / "supabase-agent-state-core-validation.md"
        ).read_text(encoding="utf-8")
        operator = (
            ROOT / "docs" / "operators" / "supabase-agent-state-core.md"
        ).read_text(encoding="utf-8")
        self.assertIn("pre-deployment transition package", evidence)
        self.assertIn("does not claim", evidence)
        self.assertIn("No production Supabase project is contacted", operator)
        for stale in (
            "all six migrations applied",
            "connected project: `agent state`",
            "live direct-rpc proof",
            "live version",
        ):
            self.assertNotIn(stale, (evidence + "\n" + operator).lower())

    def test_rehome_manifest_is_exact_and_bounded(self) -> None:
        self.assertEqual(
            self.rehome["canonical_destination"],
            {
                "repository": "StreamScapeTV/agent-state-supabase",
                "issue": 3,
                "integration_branch": "main",
            },
        )
        self.assertEqual(self.rehome["migration_order"], self.contract["migration_order"])
        copied = {item["source"] for item in self.rehome["copy"]}
        manifest_paths = {item["path"] for item in self.file_manifest["files"]}
        self.assertEqual(
            copied - {"contracts/supabase-agent-state-core-files.json"},
            manifest_paths,
        )
        prohibited = " ".join(self.rehome["prohibited"]).lower()
        for token in ("parallel migration history", "direct production ddl", "structured multi-work"):
            self.assertIn(token, prohibited)

    def test_exact_file_manifest_hashes(self) -> None:
        self.assertEqual(self.file_manifest["hash_algorithm"], "sha256")
        self.assertEqual(
            self.file_manifest["excluded_self"],
            "contracts/supabase-agent-state-core-files.json",
        )
        for item in self.file_manifest["files"]:
            path = ROOT / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, item["sha256"], item["path"])
            self.assertEqual(item["bytes"], path.stat().st_size, item["path"])

    def test_later_program_behavior_is_absent(self) -> None:
        lower = self.sql.lower()
        for token in (
            " park ",
            " activate ",
            " takeover ",
            " dependabot ",
            " review_request ",
            " agent r ",
        ):
            self.assertNotIn(token, lower)


if __name__ == "__main__":
    unittest.main()
