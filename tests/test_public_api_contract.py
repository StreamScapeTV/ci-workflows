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
        self.assertEqual(len(self.data.workflows), 20)
        self.assertEqual(len(self.profiles), 11)
        self.assertEqual(len(self.data.types["trust_classes"]), 6)
        self.assertEqual("2.0.0", self.data.index["contract_version"])
        self.assertEqual(
            [row["api_name"] for row in self.data.workflows],
            sorted(row["api_name"] for row in self.data.workflows),
        )
        self.assertEqual(
            self.data.index["workflow_count"], len(self.data.workflows)
        )

    def test_every_trust_mode_has_valid_and_invalid_caller_evidence(self) -> None:
        fixtures = json.loads(
            (ROOT / "tests/fixtures/public-api/callers.json").read_text(
                encoding="utf-8"
            )
        )
        represented = set()
        valid_ids = set()
        for case in fixtures["valid"]:
            with self.subTest(case=case["id"]):
                self.assertIsNone(
                    contract.validate_caller(
                        case, self.data, self.workflows, self.profiles
                    )
                )
                represented.add(case["trust_class"])
                valid_ids.add(case["id"])
        self.assertEqual(represented, set(self.data.types["trust_classes"]))
        self.assertIn("bootstrap-tag-push-legacy", valid_ids)
        self.assertIn("bootstrap-existing-tag", valid_ids)
        for case in fixtures["invalid"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    contract.validate_caller(
                        case, self.data, self.workflows, self.profiles
                    ),
                    case["expected_error"],
                )

    def test_compatibility_classifier_covers_every_decision(self) -> None:
        fixtures = json.loads(
            (ROOT / "tests/fixtures/public-api/compatibility.json").read_text(
                encoding="utf-8"
            )
        )
        decisions = set()
        existing_tag_case = None
        for case in fixtures["cases"]:
            with self.subTest(case=case["id"]):
                decision = contract.classify_change(
                    case["baseline"],
                    case["current"],
                    case.get("acknowledgement"),
                )
                self.assertEqual(decision, case["expected"])
                decisions.add(decision)
                if case["id"] == "compatible-existing-tag-optional-tuple":
                    existing_tag_case = decision
        self.assertEqual("compatible", existing_tag_case)
        self.assertEqual(
            decisions,
            {
                "compatible",
                "conditional",
                "breaking-unacknowledged",
                "breaking-acknowledged",
            },
        )

    def test_main_is_initial_channel_and_fixed_references_remain_supported(self) -> None:
        allowed = set(
            self.data.types["reference_policy"][
                "bootstrap_mutable_allowed_trust_classes"
            ]
        )
        self.assertEqual(allowed, set(self.data.types["trust_classes"]))
        self.assertFalse(
            self.data.types["reference_policy"][
                "privileged_mutable_references_forbidden"
            ]
        )

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
                "permissions": self.profiles[row["permission_profile"]][
                    "caller_permissions"
                ],
                "secrets": row["secrets"],
                "inputs": inputs,
            }
            for reference in (
                "main",
                "0123456789abcdef0123456789abcdef01234567",
                "v1.2.3",
            ):
                case = {**base, "reference": reference}
                with self.subTest(api=api, reference=reference):
                    self.assertIsNone(
                        contract.validate_caller(
                            case, self.data, self.workflows, self.profiles
                        )
                    )

    def test_forbidden_infrastructure_fields_never_become_public_inputs(self) -> None:
        forbidden = set(
            self.data.types["defaults"]["forbidden_caller_fields"]
        )
        for row in self.data.workflows:
            inputs = {item["name"] for item in row["inputs"]}
            self.assertTrue(
                inputs.isdisjoint(forbidden),
                f"{row['api_name']} exposes {sorted(inputs & forbidden)}",
            )
            self.assertNotIn("self-hosted", row["semantic_runner_profile"])

    def test_products_and_consumers_are_inventory_bounded(self) -> None:
        for row in self.data.workflows:
            for product in row["supported_products"]:
                self.assertIn(product, self.data.products)
            for consumer in row["supported_consumers"]:
                self.assertTrue(
                    consumer in {"*", "StreamScapeTV/*"}
                    or consumer in self.data.consumers
                )
        oci_consumers = set(
            self.workflows["oci.publish"]["supported_consumers"]
        )
        self.assertEqual(
            oci_consumers,
            {
                "StreamScapeTV/iptv-backend",
                "StreamScapeTV/agent-state",
                "StreamScapeTV/flux",
            },
        )

    def test_existing_bootstrap_workflow_matches_its_versioned_api_record(self) -> None:
        row = self.workflows["release.tag-image-chart-bootstrap"]
        self.assertEqual("1.1.0", row["api_version"])
        self.assertEqual(row["status"], "deprecated-bootstrap-exception")
        self.assertEqual(row["deprecation"]["replacement"], "release.orchestrate")
        input_map = {item["name"]: item for item in row["inputs"]}
        self.assertEqual("tag-push", input_map["release_mode"]["default"])
        self.assertFalse(input_map["release_version"]["required"])
        self.assertFalse(input_map["release_source_sha"]["required"])
        self.assertIn("tag-push", row["permitted_events"])
        self.assertIn("workflow_dispatch-existing-tag", row["permitted_events"])
        self.assertIn(
            "ci_workflows.release_tag_authority.resolve_release_authority",
            row["implementation_components"],
        )
        self.assertIn(
            "ci_workflows.release_tag_authority.revalidate_release_authority",
            row["implementation_components"],
        )
        contract.validate_bootstrap_workflow(
            self.data, self.workflows, self.profiles
        )

    def test_release_manifest_schema_is_fail_closed(self) -> None:
        contract.validate_release_schema(ROOT)
        schema = json.loads(
            (ROOT / "contracts/release-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertFalse(
            schema["properties"]["shared_release"]["additionalProperties"]
        )

    def test_generated_reference_is_exact_when_present(self) -> None:
        rendered = contract.render(self.data)
        reference = ROOT / "docs/workflows/public-api-reference.md"
        if reference.exists():
            self.assertEqual(reference.read_text(encoding="utf-8"), rendered)
        self.assertIn("20", str(len(self.data.workflows)))
        self.assertIn("release.orchestrate", rendered)
        self.assertIn("flux.reconcile", rendered)
        self.assertNotIn("agent-state.lifecycle", rendered)
        self.assertIn("`release_mode` (default `tag-push`)", rendered)
        self.assertIn("`release_source_sha`", rendered)
        self.assertIn("`workflow_dispatch-existing-tag`", rendered)

    def test_breaking_changes_fail_without_a_complete_acknowledgement(self) -> None:
        baseline = copy.deepcopy(self.workflows["validation.python"])
        current = copy.deepcopy(baseline)
        current["permission_profile"] = "release-orchestration"
        self.assertEqual(
            contract.classify_change(baseline, current),
            "breaking-unacknowledged",
        )
        acknowledgement = {
            "id": "python-permission-change",
            "api_name": "validation.python",
            "kind": "permission-profile",
            "reason": "fixture",
            "migration_issue": "#999",
            "effective_version": "2.0.0",
        }
        self.assertEqual(
            contract.classify_change(baseline, current, acknowledgement),
            "breaking-acknowledged",
        )

    def test_self_check_runs_validator_renderer_and_tests(self) -> None:
        source = (
            ROOT / ".github/workflows/self-check.yml"
        ).read_text(encoding="utf-8")
        for required in (
            '"${VERIFIED_PYTHON}" scripts/ci/public_api_contract.py validate',
            '"${VERIFIED_PYTHON}" scripts/ci/public_api_contract.py render',
            '"${VERIFIED_PYTHON}" -m unittest discover -s tests -p \'test_*.py\' -v',
            "rm -f docs/workflows/public-api-reference.md",
        ):
            self.assertIn(required, source)
        self.assertNotIn(
            '"${VERIFIED_PYTHON}" -m unittest -v tests/test_public_api_contract.py',
            source,
        )

    @staticmethod
    def _example_input(name: str) -> object:
        values: dict[str, object] = {
            "source_mode": "manual",
            "requested_sha": "4" * 40,
            "admitted_sha": "0" * 40,
            "project_id": "iptv-backend",
            "action": "resume",
            "session_name": "gpt-agent-1",
            "request_id": "request-12345678",
            "pr_number": 1,
            "head_sha": "1" * 40,
            "base_sha": "2" * 40,
            "validation_profile": "default",
            "command_profile": "full",
            "platform": "ios",
            "device_family": "ios",
            "device_capability": "physical",
            "script_path": "scripts/validate.sh",
            "product_id": "iptv-backend-image",
            "release_mode": "tag-push",
            "release_version": "1.2.3",
            "release_source_sha": "5" * 40,
            "release_contract": "backend",
            "release_tag": "v1.2.3",
            "target_id": "backend-production",
            "operation": "reconcile",
            "policy_path": "scripts/policy.sh",
            "allowlist_path": "contracts/allowlist.json",
            "repository_scope": [],
            "run_id": 1,
            "expected_head_sha": "3" * 40,
            "dry_run": True,
            "image_name": "iptv-backend",
            "chart_name": "iptv-backend",
            "chart_path": "charts/iptv-backend",
        }
        return values.get(name, "value")


if __name__ == "__main__":
    unittest.main()
