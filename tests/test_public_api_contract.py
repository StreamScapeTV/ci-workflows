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


SUPPORTED_APIS = {
    "oci.reproducibility",
    "release.gradle-maven",
    "release.native-image-chart",
    "release.public-native-image-chart",
    "release.tag-image-chart-bootstrap",
    "source.resolve",
    "validation.android",
    "validation.android-live-service",
    "validation.android-release",
    "validation.apple",
    "validation.device",
    "validation.flutter",
    "validation.gitops",
    "validation.node",
    "validation.python",
    "validation.script",
}
RETIRED_APIS = {
    "flux.assets",
    "flux.reconcile",
    "helm.publish",
    "helm.validate",
    "maintenance.artifacts",
    "maintenance.branches",
    "maintenance.runner-retry",
    "network.download",
    "oci.build",
    "oci.publish",
    "release.orchestrate",
}
RETIRED_WORKFLOWS = {
    ".github/workflows/reusable-network-download.yml",
    ".github/workflows/reusable-oci-build.yml",
    ".github/workflows/reusable-oci-publish.yml",
    ".github/workflows/reusable-helm-validate.yml",
    ".github/workflows/reusable-helm-publish.yml",
}


class PublicApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = contract.validate(ROOT)
        cls.profiles = contract.permission_profiles(cls.data)
        cls.workflows = contract.validate_workflows(cls.data, cls.profiles)

    def test_registry_is_terminal_complete_and_deterministic(self) -> None:
        self.assertEqual(16, len(self.data.workflows))
        self.assertEqual(7, len(self.profiles))
        self.assertEqual(4, len(self.data.types["trust_classes"]))
        self.assertEqual("4.0.0", self.data.index["contract_version"])
        self.assertEqual(SUPPORTED_APIS, set(self.workflows))
        self.assertTrue(RETIRED_APIS.isdisjoint(self.workflows))
        self.assertEqual(
            {"implemented", "deprecated-bootstrap-exception"},
            {row["status"] for row in self.data.workflows},
        )
        self.assertEqual(
            [row["api_name"] for row in self.data.workflows],
            sorted(row["api_name"] for row in self.data.workflows),
        )
        self.assertEqual(self.data.index["workflow_count"], len(self.data.workflows))
        self.assertNotIn(
            "contracts/public-workflows/operations.json",
            self.data.index["fragment_contracts"],
        )

    def test_supported_files_exist_and_retired_public_wrappers_are_gone(self) -> None:
        for row in self.data.workflows:
            self.assertTrue((ROOT / row["file"]).is_file(), row["api_name"])
        for relative in RETIRED_WORKFLOWS:
            self.assertFalse((ROOT / relative).exists(), relative)
        bootstrap = {
            row["path"]
            for row in json.loads(
                (ROOT / "contracts/bootstrap-public-workflows.json").read_text(
                    encoding="utf-8"
                )
            )["allowed"]
        }
        self.assertTrue(RETIRED_WORKFLOWS.isdisjoint(bootstrap))
        self.assertEqual(
            {row["file"] for row in self.data.workflows},
            bootstrap,
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
                        case,
                        self.data,
                        self.workflows,
                        self.profiles,
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
                    case["expected_error"],
                    contract.validate_caller(
                        case,
                        self.data,
                        self.workflows,
                        self.profiles,
                    ),
                )

    def test_compatibility_classifier_covers_every_decision(self) -> None:
        fixtures = json.loads(
            (ROOT / "tests/fixtures/public-api/compatibility.json").read_text(
                encoding="utf-8"
            )
        )
        decisions = set()
        for case in fixtures["cases"]:
            with self.subTest(case=case["id"]):
                decision = contract.classify_change(
                    case["baseline"],
                    case["current"],
                    case.get("acknowledgement"),
                )
                self.assertEqual(case["expected"], decision)
                decisions.add(decision)
        self.assertEqual(
            {
                "compatible",
                "conditional",
                "breaking-unacknowledged",
                "breaking-acknowledged",
            },
            decisions,
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
                with self.subTest(api=api, reference=reference):
                    self.assertIsNone(
                        contract.validate_caller(
                            {**base, "reference": reference},
                            self.data,
                            self.workflows,
                            self.profiles,
                        )
                    )

    def test_public_compatibility_is_application_identity_free(self) -> None:
        self.assertFalse(hasattr(self.data, "consumers"))
        self.assertFalse(hasattr(self.data, "products"))
        self.assertNotIn("product_id", self.data.types["input_catalog"])
        self.assertIn(
            "product_id",
            self.data.types["defaults"]["forbidden_caller_fields"],
        )
        for row in self.data.workflows:
            self.assertNotIn("supported_consumers", row)
            self.assertNotIn("supported_products", row)
            self.assertNotIn("product_id", {item["name"] for item in row["inputs"]})

    def test_forbidden_infrastructure_fields_never_become_public_inputs(self) -> None:
        forbidden = set(self.data.types["defaults"]["forbidden_caller_fields"])
        for row in self.data.workflows:
            inputs = {item["name"] for item in row["inputs"]}
            self.assertTrue(
                inputs.isdisjoint(forbidden),
                f"{row['api_name']} exposes {sorted(inputs & forbidden)}",
            )
            self.assertNotIn("self-hosted", row["semantic_runner_profile"])

    def test_public_type_catalog_has_no_retired_facade_only_fields(self) -> None:
        inputs = set(self.data.types["input_catalog"])
        outputs = set(self.data.types["output_catalog"])
        secrets = set(self.data.types["secret_catalog"])
        self.assertTrue(
            {
                "url",
                "relative_path",
                "archive_format",
                "maximum_bytes",
                "release_manifest_path",
                "target_id",
                "operation",
                "repository_scope",
                "run_id",
                "policy_path",
                "allowlist_path",
                "platform_set",
            }.isdisjoint(inputs)
        )
        self.assertTrue(
            {
                "download_result_json",
                "extraction_result_json",
                "resolved_inputs_json",
                "release_manifest_sha256",
                "handoff_state",
                "reconciliation_state",
                "mutation_count",
                "retry_run_id",
            }.isdisjoint(outputs)
        )
        self.assertTrue(
            {
                "flux_handoff_token",
                "flux_kubeconfig",
                "flux_sops_age_key",
                "organization_maintenance_token",
                "organization_read_token",
                "organization_update_token",
            }.isdisjoint(secrets)
        )

    def test_android_completion_apis_are_canonical_and_secret_scoped(self) -> None:
        routine = self.workflows["validation.android"]
        live = self.workflows["validation.android-live-service"]
        release = self.workflows["validation.android-release"]
        self.assertEqual("2.1.0", routine["api_version"])
        self.assertEqual("1.1.0", live["api_version"])
        self.assertEqual("1.1.0", release["api_version"])
        self.assertEqual("mobile", live["semantic_runner_profile"])
        self.assertEqual("mobile", release["semantic_runner_profile"])
        self.assertEqual(
            ["private_dependency_token", "maven_package_read_token"],
            routine["secrets"],
        )
        self.assertEqual(
            [
                "service_username",
                "service_password",
                "private_dependency_token",
                "maven_package_read_token",
            ],
            live["secrets"],
        )
        self.assertEqual("bounded-evidence", release["artifact_policy"])
        self.assertEqual(7, release["artifact_retention_max_days"])

    def test_device_api_v2_is_product_neutral_and_secret_minimized(self) -> None:
        row = self.workflows["validation.device"]
        self.assertEqual("2.0.0", row["api_version"])
        self.assertEqual("device-validation", row["permission_profile"])
        self.assertEqual(["device_authorization_receipt"], row["secrets"])
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

    def test_demonstrated_publication_surface_is_retained_without_speculative_layers(self) -> None:
        self.assertEqual(
            "buildah-small",
            self.workflows["oci.reproducibility"]["semantic_runner_profile"],
        )
        self.assertEqual(
            "2.0.0",
            self.workflows["release.native-image-chart"]["api_version"],
        )
        self.assertEqual(
            "1.0.0",
            self.workflows["release.public-native-image-chart"]["api_version"],
        )
        self.assertEqual(
            "1.0.0",
            self.workflows["release.gradle-maven"]["api_version"],
        )
        self.assertTrue(RETIRED_APIS.isdisjoint(self.workflows))

    def test_existing_bootstrap_workflow_matches_supported_replacement(self) -> None:
        row = self.workflows["release.tag-image-chart-bootstrap"]
        self.assertEqual("1.2.0", row["api_version"])
        self.assertEqual("deprecated-bootstrap-exception", row["status"])
        self.assertEqual("release.native-image-chart", row["deprecation"]["replacement"])
        input_map = {item["name"]: item for item in row["inputs"]}
        self.assertEqual("tag-push", input_map["release_mode"]["default"])
        self.assertFalse(input_map["release_version"]["required"])
        self.assertFalse(input_map["release_source_sha"]["required"])
        self.assertEqual("", input_map["image_recovery_authority"]["default"])
        contract.validate_bootstrap_workflow(self.data, self.workflows, self.profiles)

    def test_release_manifest_schema_is_fail_closed_and_identity_free(self) -> None:
        contract.validate_release_schema(ROOT)
        schema = json.loads(
            (ROOT / "contracts/release-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(2, schema["properties"]["schema_version"]["const"])
        self.assertNotIn("consumers", schema["required"])
        self.assertNotIn("consumers", schema["properties"])
        self.assertFalse(schema["properties"]["shared_release"]["additionalProperties"])

    def test_generated_reference_is_required_exact_and_placeholder_free(self) -> None:
        rendered = contract.render(self.data)
        reference = ROOT / "docs/workflows/public-api-reference.md"
        self.assertTrue(reference.is_file())
        self.assertEqual(reference.read_text(encoding="utf-8"), rendered)
        self.assertIn("oci.reproducibility", rendered)
        self.assertIn("release.native-image-chart", rendered)
        self.assertIn("validation.python", rendered)
        for retired in RETIRED_APIS:
            self.assertNotIn(f"`{retired}`", rendered)
        self.assertNotIn("migration-pending", rendered)
        self.assertNotIn("planned designs", rendered.lower().split("## compatibility", 1)[0])

    def test_breaking_changes_require_acknowledgement(self) -> None:
        baseline = copy.deepcopy(self.workflows["release.native-image-chart"])
        current = copy.deepcopy(baseline)
        current["permission_profile"] = "public-oci-publication"
        self.assertEqual(
            "breaking-unacknowledged",
            contract.classify_change(baseline, current),
        )
        acknowledgement = {
            "id": "example-breaking-change",
            "api_name": "release.native-image-chart",
            "kind": "permission-profile-change",
            "reason": "fixture",
            "migration_issue": "#505",
            "effective_version": "3.0.0",
        }
        self.assertEqual(
            "breaking-acknowledged",
            contract.classify_change(baseline, current, acknowledgement),
        )

    def test_breaking_acknowledgements_reference_only_supported_api_changes(self) -> None:
        acknowledgements = self.data.types["breaking_change_acknowledgements"]
        self.assertEqual(7, len(acknowledgements))
        self.assertTrue(
            {item["api_name"] for item in acknowledgements} <= SUPPORTED_APIS
        )
        self.assertTrue(
            RETIRED_APIS.isdisjoint(
                {item["api_name"] for item in acknowledgements}
            )
        )

    @staticmethod
    def _example_input(name: str):
        values = {
            "source_mode": "manual",
            "requested_sha": "0123456789abcdef0123456789abcdef01234567",
            "expected_branch": "main",
            "release_contract": "default-release",
            "history_depth": 1,
            "execution_backend": "organization",
            "admitted_sha": "0123456789abcdef0123456789abcdef01234567",
            "validation_profile": "host",
            "validation_scope": "unit",
            "validation_plan_json": {},
            "dependency_prebuild_plan_json": {},
            "consumer_contract": "default-contract",
            "change_base_sha": "0123456789abcdef0123456789abcdef01234567",
            "policy_script_profile": "default-policy",
            "python_version": "3.12",
            "version_file": "VERSION",
            "dependency_file": "requirements.lock",
            "working_directory": ".",
            "gradle_wrapper_path": "gradlew",
            "install_profile": "none",
            "command_profile": "unit",
            "script_path": "ci/validate.sh",
            "output_verifier_path": "ci/verify.sh",
            "public_environment": {},
            "artifact_exception_id": "artifact-exception",
            "private_dependency_id": "private-dependency",
            "private_dependency_repository": "StreamScapeTV/example",
            "private_dependency_sha": "0123456789abcdef0123456789abcdef01234567",
            "private_dependency_subdirectory": ".",
            "platform": "linux",
            "scheme": "Example",
            "destination_profile": "simulator",
            "device_family": "android",
            "device_capability": "physical",
            "host_capacity": "mobile",
            "prepare_script_path": "scripts/device-prepare.sh",
            "test_script_path": "scripts/device-test.sh",
            "evidence_script_path": "scripts/device-evidence.sh",
            "cleanup_script_path": "scripts/device-cleanup.sh",
            "arguments_json": [],
            "environment_json": {},
            "max_duration_minutes": 60,
            "evidence_exception_id": "device-evidence",
            "request_id": "request-12345678",
            "release_mode": "tag-push",
            "release_version": "1.2.3",
            "release_source_sha": "0123456789abcdef0123456789abcdef01234567",
            "image_recovery_authority": "",
            "image_name": "example-image",
            "chart_name": "example-chart",
            "chart_path": "charts/example",
            "dockerfile_path": "Dockerfile",
            "build_context": ".",
            "publish_latest_image": False,
            "static_output_directory": "dist",
        }
        if name not in values:
            raise AssertionError(f"missing example input for {name}")
        return values[name]


if __name__ == "__main__":
    unittest.main()
