from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/supabase-agent-state-core.json"
MIGRATIONS = ROOT / "supabase/migrations"
FIXTURES = ROOT / "tests/fixtures/supabase-agent-state/core-cases.json"


class SupabaseAgentStateCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cls.fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        cls.files = sorted(MIGRATIONS.glob("*_agent_state_*.sql"))
        cls.sql = "\n".join(path.read_text(encoding="utf-8") for path in cls.files)

    def test_versioned_reconstructible_migrations(self) -> None:
        self.assertGreaterEqual(len(self.files), 5)
        names = [path.name for path in self.files]
        self.assertEqual(names, sorted(names))
        for path in self.files:
            self.assertRegex(path.name, r"^\d{14}_[a-z0-9_]+\.sql$")
            self.assertIn("Issue #52", path.read_text(encoding="utf-8"))

    def test_contract_surface(self) -> None:
        self.assertEqual(self.contract["private_schema"], "agent_private")
        self.assertEqual(self.contract["api_schema"], "agent_api")
        self.assertEqual(
            self.contract["actions"],
            ["resume", "start", "claim", "release", "reconcile_base", "block", "review", "done", "cancel"],
        )
        self.assertEqual(set(self.contract["reads"]), {"agent_api.resume", "agent_api.context", "agent_api.ownership_check"})
        self.assertEqual(self.fixtures["actions"], self.contract["actions"])

    def test_private_normalized_tables(self) -> None:
        for table in (
            "projects", "profiles", "project_slots", "work_sessions", "work_bindings",
            "work_evidence", "requests", "command_receipts", "claims", "events",
        ):
            self.assertIn(f"agent_private.{table}", self.sql)
        self.assertNotRegex(self.sql, r"create table\s+agent_private\.[a-z0-9_]+_(?:iptv|flux|media)")

    def test_transaction_and_replay_guards(self) -> None:
        for token in (
            "pg_advisory_xact_lock", "for update", "request_hash", "request_id_reuse_conflict",
            "work_sessions_one_current_per_slot", "work_bindings_active_issue",
            "work_bindings_active_branch", "find_claim_collision", "released_request_id",
        ):
            self.assertIn(token, self.sql)
        self.assertIn("requests_immutable", self.sql)
        self.assertIn("receipts_immutable", self.sql)
        self.assertIn("events_append_only", self.sql)

    def test_claim_kinds_and_modes(self) -> None:
        for kind in ("file", "package", "resource", "manifest", "device"):
            self.assertIn(f"'{kind}'", self.sql)
        self.assertIn("prefix_requires_file", self.sql)
        self.assertIn("unsafe_path", self.sql)
        self.assertIn("duplicate_claim", self.sql)

    def test_rpc_only_access(self) -> None:
        for role in ("public", "anon", "authenticated", "service_role"):
            self.assertRegex(self.sql.lower(), rf"revoke all on all tables in schema agent_private from[^;]*\b{role}\b")
        self.assertIn("grant execute on function agent_api.command(jsonb) to service_role", self.sql.lower())
        self.assertNotIn("grant select on", self.sql.lower())
        self.assertNotIn("grant insert on", self.sql.lower())
        self.assertNotIn("grant update on", self.sql.lower())
        self.assertNotIn("grant delete on", self.sql.lower())

    def test_security_definer_functions_are_bounded(self) -> None:
        for signature in (
            "agent_api.command", "agent_api.resume", "agent_api.context", "agent_api.ownership_check",
        ):
            self.assertIn(signature, self.sql)
        self.assertGreaterEqual(self.sql.count("security definer"), 4)
        self.assertGreaterEqual(self.sql.count("set search_path="), 4)
        self.assertIn("set row_security=off", self.sql)
        self.assertNotIn("execute format", self.sql.lower())

    def test_project_mappings_match_inventory(self) -> None:
        consumers = json.loads((ROOT / "contracts/consumers.json").read_text(encoding="utf-8"))
        expected = {
            item["repository"]: item["integration_branch"]
            for item in consumers["repositories"]
            if item["agent_state_mapping_status"] == "established"
        }
        recorded = {item["repository"]: item["integration_branch"] for item in self.contract["projects"]}
        self.assertEqual(recorded, expected)

    def test_out_of_scope_features_absent(self) -> None:
        for token in ("park", "activate", "takeover", "dependabot", "review_request", "agent r"):
            self.assertNotIn(token, self.sql.lower())
        self.assertEqual(self.contract["future_extensions"], ["#53", "#54", "#55"])


if __name__ == "__main__":
    unittest.main()
