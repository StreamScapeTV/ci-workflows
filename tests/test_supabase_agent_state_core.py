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
DATABASE_TEST_PATH = ROOT / "tests" / "test_supabase_agent_state_core_database.py"
RUNTIME_PATH = ROOT / "tests" / "support" / "supabase_agent_state_postgres.py"


class SupabaseAgentStateCoreSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.file_manifest = json.loads(FILE_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.rehome = json.loads(REHOME_PATH.read_text(encoding="utf-8"))
        cls.migration_paths = [ROOT / value for value in cls.contract["migration_order"]]
        cls.sql = "\n".join(path.read_text(encoding="utf-8") for path in cls.migration_paths)

    def test_exact_ordered_clean_source_history(self) -> None:
        expected = [
            "20260806172100_agent_state_core_schema.sql",
            "20260806172200_agent_state_core_rpc.sql",
            "20260806172300_agent_state_core_indexes.sql",
        ]
        self.assertEqual([path.name for path in self.migration_paths], expected)
        self.assertTrue(all(path.is_file() for path in self.migration_paths))
        self.assertEqual(
            sorted(path.name for path in (ROOT / "supabase" / "migrations").glob("*.sql")),
            expected,
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

    def test_claim_validation_redaction_and_rpc_grants(self) -> None:
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
        self.assertFalse(self.contract["redaction"]["context_event_payload_exposed"])
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

    def test_live_reconciliation_machine_contract(self) -> None:
        live = self.rehome["live_reconciliation"]
        self.assertTrue(live["live_reconciliation_required"])
        self.assertFalse(live["production_deployment_allowed"])
        self.assertFalse(live["ordinary_rpc_use_allowed"])
        self.assertTrue(live["direct_ad_hoc_repair_forbidden"])
        self.assertIsNone(live["strategy_selected_by_this_package"])
        self.assertTrue(live["observed_live_state"]["schema_already_exists"])
        self.assertEqual(
            [(item["version"], item["name"]) for item in live["observed_live_state"]["migration_ledger"]],
            [
                ("20260806174835", "agent_state_core_schema"),
                ("20260806175059", "agent_state_core_rpc"),
                ("20260806175243", "agent_state_core_rpc_hardening"),
                ("20260806175336", "agent_state_claim_regex_fix"),
                ("20260806175433", "agent_state_error_projection_fix"),
                ("20260806175808", "agent_state_foreign_key_indexes"),
            ],
        )
        source = live["source_package"]
        self.assertFalse(source["directly_deployable_to_observed_live_project"])
        self.assertEqual(
            [item["version"] for item in source["migration_history"]],
            ["20260806172100", "20260806172200", "20260806172300"],
        )
        self.assertEqual(
            [item["id"] for item in live["permitted_strategy_classes"]],
            [
                "reconstruct-observed-live-baseline",
                "owner-authorized-reset-recreation",
                "reviewed-baseline-or-repair",
            ],
        )

    def test_rehome_copy_semantics_do_not_directly_deploy_clean_history(self) -> None:
        migration_entries = [
            item for item in self.rehome["copy"] if item["source"].startswith("supabase/migrations/")
        ]
        self.assertEqual(len(migration_entries), 3)
        self.assertTrue(
            all(
                item["semantics"] == "candidate-migration-source-not-direct-production-deployment"
                for item in migration_entries
            )
        )
        steps = " ".join(self.rehome["canonical_review_steps"]).lower()
        prohibited = " ".join(self.rehome["prohibited"]).lower()
        self.assertIn("choose one permitted reconciliation strategy", steps)
        self.assertIn("not as directly deployable migrations", steps)
        self.assertIn("three clean source migrations", prohibited)
        self.assertIn("direct ad-hoc", prohibited)
        self.assertEqual(self.rehome["source_migration_order"], self.contract["migration_order"])

    def test_normal_discovery_is_network_free_and_reconstruction_is_explicit(self) -> None:
        helper = RUNTIME_PATH.read_text(encoding="utf-8")
        database_test = DATABASE_TEST_PATH.read_text(encoding="utf-8")
        self.assertIn('POSTGRES_VERSION = "17.6"', helper)
        self.assertIn(
            "2910b85283674da2dae6ac13fe5ebbaaf3c482446396cba32e6728d3cc736d86",
            helper,
        )
        self.assertIn("PostgreSQL source digest mismatch", helper)
        self.assertIn("AGENT_STATE_POSTGRES_BIN", helper)
        self.assertIn("AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION", helper)
        self.assertIn("unittest.SkipTest", helper)
        self.assertIn("shutil.rmtree(self.root", helper)
        self.assertIn("PostgresRuntime(ROOT)", database_test)
        for proof in (
            "atomic_start_failure",
            "atomic_terminal_failure",
            "overlap_responses",
            "disjoint_responses",
            "schema_sha256",
            "drop_database",
        ):
            self.assertIn(proof, database_test)

    def test_documents_state_ledger_mismatch_and_operating_gate(self) -> None:
        evidence = (ROOT / "docs" / "evidence" / "supabase-agent-state-core-validation.md").read_text(
            encoding="utf-8"
        )
        operator = (ROOT / "docs" / "operators" / "supabase-agent-state-core.md").read_text(
            encoding="utf-8"
        )
        architecture = (ROOT / "docs" / "architecture" / "supabase-agent-state-core.md").read_text(
            encoding="utf-8"
        )
        combined = "\n".join((evidence, operator, architecture)).lower()
        for version in (
            "20260806174835",
            "20260806175059",
            "20260806175243",
            "20260806175336",
            "20260806175433",
            "20260806175808",
            "20260806172100",
            "20260806172200",
            "20260806172300",
        ):
            self.assertIn(version, combined)
        self.assertIn("not directly deployable", combined)
        self.assertIn("ordinary rpc", combined)
        self.assertIn("direct ad-hoc", combined)
        self.assertIn("agent_state_run_postgres_reconstruction=1", combined)
        self.assertIn("issue #60", combined)

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
