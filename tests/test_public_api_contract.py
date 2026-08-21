from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import public_api as contract  # noqa: E402


class PublicApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = contract.validate(ROOT)
        cls.profiles = contract.permission_profiles(cls.data)
        cls.workflows = contract.validate_workflows(cls.data, cls.profiles)

    def test_registry_is_complete_and_deterministic(self) -> None:
        self.assertEqual(len(self.data.workflows), 25)
        self.assertEqual(len(self.profiles), 13)
        self.assertEqual(len(self.data.types["trust_classes"]), 6)
        self.assertEqual("3.0.0", self.data.index["contract_version"])
        self.assertEqual(
            [row["api_name"] for row in self.data.workflows],
            sorted(row["api_name"] for row in self.data.workflows),
        )
        self.assertEqual(self.data.index["workflow_count"], len(self.data.workflows))

    def test_every_trust_mode_has_valid_and_invalid_caller_evidence(self) -> None:
        fixtures = json.loads((ROOT / "tests/fixtures/public-api/callers.json").read_text(encoding="utf-8"))
        represented = set()
        valid_ids = set()
        for case in fixtures["valid"]:
            with self.subTest(case=case["id"]):
                self.assertIsNone(contract.validate_caller(case, self.data, self.workflows, self.profiles))
                represented.add(case["trust_class"])
                valid_ids.add(case["id"])
        self.assertEqual(represented, set(self.data.types["trust_classes"]))
        self.assertIn("bootstrap-tag-push-legacy", valid_ids)
        self.assertIn("bootstrap-existing-tag", valid_ids)
        for case in fixtures["invalid"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    contract.validate_caller(case, self.data, self.workflows, self.profiles),
                    case["expected_error"],
                )

    def test_compatibility_classifier_covers_every_decision(self) -> None:
        fixtures = json.loads((ROOT / "tests/fixtures/public-api/compatibility.json").read_text(encoding="utf-8"))
        decisions = set()
        for case in fixtures["cases"]:
            with self.subTest(case=case["id"]):
                decision = contract.classify_change(case["baseline"], case["current"], case.get("acknowledgement"))
                self.assertEqual(decision, case["expected"])
                decisions.add(decision)
        self.assertEqual(
            decisions,
            {"compatible", "conditional", "breaking-unacknowledged", "breaking-acknowledged"},
        )

    def test_main_is_initial_channel_and_fixed_references_remain_supported(self) -> None:
        allowed = set(self.data.types["reference_policy"]["bootstrap_mutable_allowed_trust_classes"])
        self.assertEqual(allowed, set(self.data.types["trust_classes"]))
        self.assertFalse(self.data.types["reference_policy"]["privileged_mutable_references_forbidden"])
        for api, row in self.workflows.items():
            inputs = {
                item["name"]: self._example_input(item["name"])
                for item in row["inputs"]
                if item["required"]
            }
            base = {
                "api_name": api,
                "trust_class": row["trust_class"],
                "event": row["permitted_events"][0],
                "permissions": self.profiles[row["permission_profile"]]["caller_permissions"],
                "secrets": row["secrets"],
                "inputs": inputs,
            }
            for reference in ("main", "0123456789abcdef0123456789abcdef01234567", "v1.2.3"):
                with self.subTest(api=api, reference=reference):
                    self.assertIsNone(contract.validate_caller({**base, "reference": reference}, self.data, self.workflows, self.profiles))

    def test_public_compatibility_is_application_identity_free(self) -> None:
        self.assertFalse(hasattr(self.data, "consumers"))
        self.assertFalse(hasattr(self.data, "products"))
        self.assertNotIn("product_id", self.data.types["input_catalog"])
        self.assertIn("product_id", self.data.types["defaults"]["forbidden_caller_fields"])
        for row in self.data.workflows:
            self.assertNotIn("supported_consumers", row)
            self.assertNotIn("supported_products", row)
            self.assertNotIn("product_id", {item["name"] for item in row["inputs"]})
        source = (ROOT / "src/ci_workflows/public_api_contract.py").read_text(encoding="utf-8")
        self.assertNotIn("contracts/consumers.json", source)
        self.assertNotIn("contracts/products.json", source)

    def test_forbidden_infrastructure_fields_never_become_public_inputs(self) -> None:
        forbidden = set(self.data.types["defaults"]["forbidden_caller_fields"])
        for row in self.data.workflows:
            inputs = {item["name"] for item in row["inputs"]}
            self.assertTrue(inputs.isdisjoint(forbidden), f"{row['api_name']} exposes {sorted(inputs & forbidden)}")
            self.assertNotIn("self-hosted", row["semantic_runner_profile"])

    def test_android_completion_apis_are_canonical_and_secret_scoped(self) -> None:
        routine = self.workflows["validation.android"]
        live = self.workflows["validation.android-live-service"]
        release = self.workflows["validation.android-release"]
        self.assertEqual(".github/workflows/reusable-android.yml", routine["file"])
        self.assertEqual(
            ".github/workflows/reusable-android-live-service.yml",
            live["file"],
        )
        self.assertEqual(
            ".github/workflows/reusable-android-release.yml",
            release["file"],
        )
        self.assertEqual("2.0.0", routine["api_version"])
        self.assertEqual("1.0.0", live["api_version"])
        self.assertEqual("1.0.0", release["api_version"])
        self.assertEqual("mobile", live["semantic_runner_profile"])
        self.assertEqual("mobile", release["semantic_runner_profile"])
        self.assertEqual(1, live["matrix_max_jobs"])
        self.assertEqual(1, release["matrix_max_jobs"])
        self.assertEqual(["private_dependency_token"], routine["secrets"])
        self.assertEqual(
            ["service_username", "service_password", "private_dependency_token"],
            live["secrets"],
        )
        self.assertEqual(["private_dependency_token"], release["secrets"])
        self.assertEqual("bounded-evidence", release["artifact_policy"])
        self.assertEqual(7, release["artifact_retention_max_days"])
        for api, row in self.workflows.items():
            if api != "validation.android-release":
                self.assertNotIn("artifact_policy", row)
                self.assertNotIn("artifact_retention_max_days", row)
        self.assertEqual(
            {"type": "json-array", "nullable": True},
            self.data.types["output_catalog"]["artifact_manifest_json"],
        )
        for secret in ("service_username", "service_password"):
            self.assertEqual(
                "test-environment",
                self.data.types["secret_catalog"][secret]["required_scope"],
            )
            self.assertTrue(
                self.data.types["secret_catalog"][secret]["exposed_to_product_source"]
            )
        for row in (live, release):
            self.assertNotIn("v1", row["file"])
            self.assertNotIn("v2", row["file"])
            self.assertNotIn("v3", row["file"])

    def test_device_api_v2_is_product_neutral_and_secret_minimized(self) -> None:
        row = self.workflows["validation.device"]
        self.assertEqual(".github/workflows/reusable-device.yml", row["file"])
        self.assertEqual("2.0.0", row["api_version"])
        self.assertEqual("device-validation", row["permission_profile"])
        self.assertEqual(["device_authorization_receipt"], row["secrets"])
        self.assertEqual(
            ["device_authorization_receipt"],
            self.profiles["device-validation"]["named_secrets_allowed"],
        )
        names = {item["name"] for item in row["inputs"]}
        self.assertEqual(
            {
                "admitted_sha",
                "device_family",
                "device_capability",
                "host_capacity",
                "prepare_script_path",
                "test_script_path",
                "evidence_script_path",
                "cleanup_script_path",
                "arguments_json",
                "environment_json",
                "max_duration_minutes",
                "evidence_exception_id",
                "request_id",
            },
            names,
        )
        self.assertTrue(
            {
                "host_capacity",
                "prepare_script_path",
                "test_script_path",
                "evidence_script_path",
                "cleanup_script_path",
                "arguments_json",
                "environment_json",
            }
            <= set(self.data.types["input_catalog"])
        )
        self.assertEqual(
            ["android", "ios", "tvos"],
            self.data.types["input_catalog"]["device_family"]["enum"],
        )
        self.assertNotIn("device_alias", self.data.types["input_catalog"])
        self.assertNotIn("live_test_credentials", self.data.types["secret_catalog"])
        self.assertEqual(
            {
                "prepare_script_path",
                "test_script_path",
                "evidence_script_path",
                "cleanup_script_path",
            },
            set(row["repository_owned_hooks"]),
        )
        self.assertTrue(names.isdisjoint({"device_alias", "command_profile", "script_path"}))

    def test_publication_contracts_use_caller_owned_paths_and_names(self) -> None:
        expected = {
            "oci.build": {"image_name", "dockerfile_path", "build_context"},
            "oci.publish": {"image_name", "dockerfile_path", "build_context"},
            "helm.validate": {"chart_name", "chart_path", "values_path", "policy_path"},
            "helm.publish": {"chart_name", "chart_path", "values_path", "policy_path"},
            "release.orchestrate": {"release_manifest_path"},
            "flux.assets": {"release_manifest_path", "policy_path"},
        }
        for api, required_names in expected.items():
            names = {item["name"] for item in self.workflows[api]["inputs"]}
            self.assertTrue(required_names <= names, f"{api} missing {sorted(required_names - names)}")
        for api in ("oci.build", "oci.publish", "helm.validate", "helm.publish"):
            self.assertEqual("2.0.0", self.workflows[api]["api_version"])
            self.assertEqual("migration-pending", self.workflows[api]["status"])

    def test_oci_resolved_inputs_output_is_typed_and_additive(self) -> None:
        self.assertEqual({"type": "json-object", "nullable": True}, self.data.types["output_catalog"]["resolved_inputs_json"])
        current = copy.deepcopy(self.workflows["oci.build"])
        self.assertIn("resolved_inputs_json", current["outputs"])
        baseline = copy.deepcopy(current)
        baseline["outputs"].remove("resolved_inputs_json")
        self.assertEqual("compatible", contract.classify_change(baseline, current))
        acknowledgements = self.data.types["breaking_change_acknowledgements"]
        self.assertEqual(10, len(acknowledgements))
        by_api = {item["api_name"]: item for item in acknowledgements}
        self.assertEqual(
            {
                "oci.build",
                "oci.publish",
                "helm.validate",
                "helm.publish",
                "release.orchestrate",
                "flux.assets",
                "flux.reconcile",
                "validation.android",
                "validation.apple",
                "validation.device",
            },
            set(by_api),
        )
        self.assertTrue(
            all(
                item["migration_issue"] == "#322"
                for api, item in by_api.items()
                if api not in {"validation.android", "validation.apple", "validation.device"}
            )
        )
        self.assertEqual("#332", by_api["validation.android"]["migration_issue"])
        self.assertEqual("2.0.0", by_api["validation.android"]["effective_version"])
        self.assertEqual("#336", by_api["validation.apple"]["migration_issue"])
        self.assertEqual("2.0.0", by_api["validation.apple"]["effective_version"])
        self.assertEqual("#341", by_api["validation.device"]["migration_issue"])
        self.assertEqual("2.0.0", by_api["validation.device"]["effective_version"])

    def test_existing_bootstrap_workflow_matches_its_versioned_api_record(self) -> None:
        row = self.workflows["release.tag-image-chart-bootstrap"]
        self.assertEqual("1.2.0", row["api_version"])
        self.assertEqual("deprecated-bootstrap-exception", row["status"])
        self.assertEqual("release.orchestrate", row["deprecation"]["replacement"])
        input_map = {item["name"]: item for item in row["inputs"]}
        self.assertEqual("tag-push", input_map["release_mode"]["default"])
        self.assertFalse(input_map["release_version"]["required"])
        self.assertFalse(input_map["release_source_sha"]["required"])
        self.assertFalse(input_map["image_recovery_authority"]["required"])
        self.assertEqual("", input_map["image_recovery_authority"]["default"])
        contract.validate_bootstrap_workflow(self.data, self.workflows, self.profiles)

    def test_release_manifest_schema_is_fail_closed_and_identity_free(self) -> None:
        contract.validate_release_schema(ROOT)
        schema = json.loads((ROOT / "contracts/release-manifest.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(2, schema["properties"]["schema_version"]["const"])
        self.assertNotIn("consumers", schema["required"])
        self.assertNotIn("consumers", schema["properties"])
        self.assertFalse(schema["properties"]["shared_release"]["additionalProperties"])

    def test_generated_reference_is_required_and_exact(self) -> None:
        rendered = contract.render(self.data)
        reference = ROOT / "docs/workflows/public-api-reference.md"
        self.assertTrue(reference.is_file())
        self.assertEqual(reference.read_text(encoding="utf-8"), rendered)
        self.assertIn("release.orchestrate", rendered)
        self.assertIn("flux.reconcile", rendered)
        self.assertNotIn("agent-state.lifecycle", rendered)
        self.assertNotIn("Consumers | Products", rendered)
        self.assertIn("Application repository/product identity is intentionally not part", rendered)
        self.assertIn("`release_manifest_path` (required)", rendered)
        self.assertIn("`image_name` (required)", rendered)
        self.assertIn("`workflow_dispatch-existing-tag`", rendered)
        self.assertIn("`validation_scope` (required)", rendered)
        self.assertIn("`validation.android-live-service` `1.0.0`", rendered)
        self.assertIn("`validation.android-release` `1.0.0`", rendered)
        self.assertIn("`validation.apple` `2.0.0`", rendered)
        self.assertIn("`validation.device` `2.0.0`", rendered)
        self.assertIn("`host_capacity` (required)", rendered)

    def test_breaking_changes_fail_without_a_complete_acknowledgement(self) -> None:
        baseline = copy.deepcopy(self.workflows["validation.python"])
        current = copy.deepcopy(baseline)
        current["permission_profile"] = "release-orchestration"
        self.assertEqual("breaking-unacknowledged", contract.classify_change(baseline, current))
        acknowledgement = {
            "id": "python-permission-change",
            "api_name": "validation.python",
            "kind": "permission-profile",
            "reason": "fixture",
            "migration_issue": "#999",
            "effective_version": "2.0.0",
        }
        self.assertEqual("breaking-acknowledged", contract.classify_change(baseline, current, acknowledgement))

    def test_self_check_validates_reference_without_mutating_it(self) -> None:
        source = (ROOT / ".github/workflows/self-check.yml").read_text(encoding="utf-8")
        commands = {line.strip() for line in source.splitlines()}
        for required in (
            '"${VERIFIED_PYTHON}" scripts/ci/public_api_contract.py validate',
            '"${VERIFIED_PYTHON}" scripts/ci/public_api_contract.py render --check',
            "cat docs/workflows/public-api-reference.md",
            '"${VERIFIED_PYTHON}" -m unittest discover -s tests -p \'test_*.py\' -v',
            'test -z "$(git status --porcelain --untracked-files=all)"',
        ):
            self.assertIn(required, commands)
        self.assertEqual(source.count("docs/workflows/public-api-reference.md"), 1)
        self.assertNotIn('"${VERIFIED_PYTHON}" scripts/ci/public_api_contract.py render', commands)

    @staticmethod
    def _example_input(name: str) -> object:
        values: dict[str, object] = {
            "source_mode": "manual",
            "requested_sha": "4" * 40,
            "admitted_sha": "0" * 40,
            "project_id": "example-project",
            "request_id": "request-12345678",
            "pr_number": 1,
            "validation_profile": "default",
            "validation_scope": "unit",
            "validation_plan_json": {"stages": []},
            "command_profile": "full",
            "platform": "ios",
            "device_family": "ios",
            "device_capability": "physical",
            "host_capacity": "apple",
            "prepare_script_path": "scripts/ci/device-prepare.sh",
            "test_script_path": "scripts/ci/device-test.sh",
            "evidence_script_path": "scripts/ci/device-evidence.sh",
            "cleanup_script_path": "scripts/ci/device-cleanup.sh",
            "arguments_json": [],
            "environment_json": {},
            "script_path": "scripts/validate.sh",
            "release_mode": "tag-push",
            "release_version": "1.2.3",
            "release_source_sha": "5" * 40,
            "image_recovery_authority": "",
            "release_contract": "default",
            "release_manifest_path": "ci/release.json",
            "release_tag": "v1.2.3",
            "target_id": "production",
            "operation": "reconcile",
            "policy_path": "policy/reconcile.json",
            "allowlist_path": "policy/targets.json",
            "values_path": "deploy/values.yaml",
            "repository_scope": [],
            "run_id": 1,
            "expected_head_sha": "3" * 40,
            "dry_run": True,
            "image_name": "example-image",
            "chart_name": "example-chart",
            "chart_path": "charts/example",
            "dockerfile_path": "Dockerfile",
            "build_context": ".",
        }
        return values.get(name, "value")


if __name__ == "__main__":
    unittest.main()
