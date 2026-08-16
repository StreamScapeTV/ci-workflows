from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows import runners  # noqa: E402


class RunnerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = runners.load_runner_contract(ROOT)
        cls.profiles = runners.profile_index(cls.contract)
        cls.valid = runners.read_json(ROOT / "tests/fixtures/runners/valid.json")["cases"]
        cls.invalid = runners.read_json(ROOT / "tests/fixtures/runners/invalid.json")["cases"]

    def test_contract_contains_exact_reviewed_profiles(self) -> None:
        self.assertEqual(
            set(self.profiles),
            {
                "general-tiny",
                "general-small",
                "general-medium",
                "mobile",
                "buildah-tiny",
                "buildah-small",
                "buildah-medium",
                "buildah-high",
                "apple",
                "physical-device",
                "flux-control",
            },
        )

    def test_schema_documents_required_capability_fields(self) -> None:
        schema = runners.read_json(ROOT / "contracts/schemas/runner-profiles.schema.json")
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        required = set(schema["$defs"]["profile"]["required"])
        self.assertTrue(
            {
                "id",
                "public_name",
                "internal_selectors",
                "os",
                "architecture",
                "tools",
                "privilege",
                "trust",
                "resources",
                "concurrency_cap",
                "allowed_workflow_apis",
                "forbidden_uses",
                "evidence_fields",
            }
            <= required
        )

    def test_positive_fixture_covers_every_profile(self) -> None:
        self.assertEqual({case["expected_profile"] for case in self.valid}, set(self.profiles))

    def test_positive_resolution_fixtures(self) -> None:
        for case in self.valid:
            with self.subTest(case=case["id"]):
                resolution = runners.resolve_runner_profile(
                    self.contract,
                    workflow_api=case["workflow_api"],
                    source_trust=case["source_trust"],
                    requested_profile=case.get("requested_profile"),
                    caller_inputs=case.get("caller_inputs"),
                    device_family=case.get("device_family"),
                    lock_evidence=case.get("lock_evidence"),
                )
                payload = resolution.as_dict()
                self.assertEqual(payload["profile"], case["expected_profile"])
                self.assertEqual(payload["execution_profile"], case["expected_execution_profile"])
                self.assertEqual(payload["runs_on"], case["expected_runs_on"])
                self.assertEqual(json.loads(payload["runs_on_json"]), case["expected_runs_on"])

    def test_negative_fixture_covers_every_profile(self) -> None:
        covered = {case.get("profile_under_test") for case in self.invalid}
        self.assertEqual(covered - {None}, set(self.profiles))

    def test_negative_policy_fixtures(self) -> None:
        for case in self.invalid:
            with self.subTest(case=case["id"]):
                with self.assertRaises(runners.RunnerContractError) as raised:
                    if case["mode"] == "selector":
                        runners.validate_direct_selector(self.contract, case["labels"])
                    else:
                        runners.resolve_runner_profile(
                            self.contract,
                            workflow_api=case["workflow_api"],
                            source_trust=case["source_trust"],
                            requested_profile=case.get("requested_profile"),
                            caller_inputs=case.get("caller_inputs"),
                            device_family=case.get("device_family"),
                            lock_evidence=case.get("lock_evidence"),
                        )
                self.assertEqual(raised.exception.code, case["expected_error"])

    def test_every_approved_direct_selector_resolves(self) -> None:
        for profile in self.contract["profiles"]:
            for selector in profile["internal_selectors"]:
                with self.subTest(profile=profile["id"], selector=selector):
                    self.assertEqual(
                        runners.validate_direct_selector(self.contract, selector),
                        profile["id"],
                    )

    def test_bare_self_hosted_and_docker_are_not_profiles(self) -> None:
        aliases = runners.profile_alias_index(self.contract)
        self.assertNotIn("self-hosted", aliases)
        self.assertNotIn("docker", aliases)
        self.assertNotIn("dind", aliases)

    def test_portable_compatibility_maps_only_to_general_small(self) -> None:
        aliases = runners.profile_alias_index(self.contract)
        self.assertEqual(aliases["portable"], "general-small")
        resolved = runners.resolve_runner_profile(
            self.contract,
            workflow_api="validation.python",
            source_trust="trusted-pr",
            requested_profile="portable",
        )
        self.assertEqual(resolved.profile, "general-small")
        self.assertEqual(resolved.execution_profile, "general-small")
        self.assertEqual(
            resolved.runs_on,
            ("linux", "amd64", "general", "small"),
        )
        self.assertNotIn("portable", resolved.runs_on)

    def test_general_selectors_are_exactly_sized(self) -> None:
        expected = {
            "general-tiny": ("linux", "amd64", "general", "tiny"),
            "general-small": ("linux", "amd64", "general", "small"),
            "general-medium": ("linux", "amd64", "general", "medium"),
        }
        for profile_id, selector in expected.items():
            with self.subTest(profile=profile_id):
                self.assertEqual(
                    tuple(self.profiles[profile_id]["default_internal_selector"]),
                    selector,
                )
                self.assertEqual(
                    runners.validate_direct_selector(self.contract, selector),
                    profile_id,
                )
        with self.assertRaisesRegex(runners.RunnerContractError, "ambiguous-general"):
            runners.validate_direct_selector(
                self.contract,
                ["linux", "amd64", "general"],
            )
        with self.assertRaisesRegex(runners.RunnerContractError, "ambiguous-general"):
            runners.validate_direct_selector(
                self.contract,
                ["linux", "amd64", "general", "tiny", "small"],
            )

    def test_generic_buildah_semantic_profile_maps_only_to_small(self) -> None:
        self.assertEqual(
            runners.profile_alias_index(self.contract)["buildah"],
            "buildah-small",
        )
        resolved = runners.resolve_runner_profile(
            self.contract,
            workflow_api="oci.build",
            source_trust="trusted-exact",
            requested_profile="buildah",
        )
        self.assertEqual(resolved.profile, "buildah-small")
        self.assertEqual(
            resolved.runs_on,
            ("linux", "amd64", "buildah", "small"),
        )
        with self.assertRaisesRegex(
            runners.RunnerContractError,
            "ambiguous-buildah",
        ):
            runners.validate_direct_selector(self.contract, ["buildah"])

    def test_linux_arc_selectors_match_the_hard_cutover(self) -> None:
        expected = {
            profile_id: [list(selector) for selector in selectors]
            for profile_id, selectors in runners.FINAL_LINUX_ARC_SELECTORS.items()
        }
        for profile_id, selectors in expected.items():
            with self.subTest(profile=profile_id):
                profile = self.profiles[profile_id]
                self.assertEqual(profile["internal_selectors"], selectors)
                self.assertEqual(
                    profile["default_internal_selector"],
                    selectors[0],
                )
                flattened = {
                    label
                    for selector in selectors
                    for label in selector
                }
                self.assertFalse(
                    flattened & runners.RETIRED_LINUX_SELECTOR_TOKENS
                )
                self.assertFalse(
                    any(label.startswith("homelab-") for label in flattened)
                )

    def test_flux_owned_arc_profiles_have_no_central_concurrency_cap(self) -> None:
        for profile_id, profile in self.profiles.items():
            if profile["capacity_owner"] != "flux-arc":
                continue
            with self.subTest(profile=profile_id):
                self.assertIsNone(profile["concurrency_cap"])

    def test_apple_selector_matches_the_current_capability_contract(self) -> None:
        profile = self.profiles["apple"]
        self.assertEqual(
            profile["internal_selectors"],
            [list(selector) for selector in runners.APPLE_CAPABILITY_SELECTORS],
        )
        self.assertEqual(
            profile["default_internal_selector"],
            ["macOS", "ARM64"],
        )
        self.assertEqual(
            runners.validate_direct_selector(self.contract, ["macOS", "ARM64"]),
            "apple",
        )
        self.assertTrue(
            all(
                "self-hosted" not in label.lower()
                for selector in profile["internal_selectors"]
                for label in selector
            )
        )

    def test_buildah_tier_selection_uses_measured_headroom(self) -> None:
        mib = 1024**2
        gib = 1024**3
        cases = [
            (400 * mib, 1 * gib, "buildah-tiny"),
            (900 * mib, 5 * gib, "buildah-small"),
            (int(2.5 * gib), 20 * gib, "buildah-medium"),
            (5 * gib, 30 * gib, "buildah-high"),
        ]
        for memory, storage, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    runners.select_buildah_tier(
                        self.contract,
                        peak_memory_bytes=memory,
                        peak_local_storage_bytes=storage,
                    ),
                    expected,
                )

    def test_buildah_capacity_excess_fails_closed(self) -> None:
        with self.assertRaisesRegex(runners.RunnerContractError, "buildah-capacity-exceeded"):
            runners.select_buildah_tier(
                self.contract,
                peak_memory_bytes=8 * 1024**3,
                peak_local_storage_bytes=44 * 1024**3,
            )

    def test_buildah_evidence_requires_exact_source_and_measurements(self) -> None:
        evidence = {
            "peak_memory_bytes": 1,
            "peak_local_storage_bytes": 1,
            "source_sha": "0123456789abcdef0123456789abcdef01234567",
            "workflow_api": "oci.build",
            "product_id": "agent-state-image",
        }
        runners.validate_buildah_evidence(self.contract, evidence)
        evidence.pop("source_sha")
        with self.assertRaisesRegex(runners.RunnerContractError, "missing-buildah-evidence"):
            runners.validate_buildah_evidence(self.contract, evidence)

    def test_android_device_overlay_resolves_mobile_only_with_lock(self) -> None:
        evidence = {
            "authorization_receipt": "authorization",
            "resource_lock_receipt": "lock",
            "device_family": "android",
            "discovered_device_id": "emulator-is-not-accepted-device-001",
            "tested_source_sha": "0123456789abcdef0123456789abcdef01234567",
            "cleanup_evidence": "cleanup",
        }
        result = runners.resolve_runner_profile(
            self.contract,
            workflow_api="validation.device",
            source_trust="trusted-exact",
            requested_profile="physical-device",
            device_family="android",
            lock_evidence=evidence,
        )
        self.assertEqual(result.execution_profile, "mobile")
        self.assertTrue(result.resource_lock_required)

    def test_control_profile_executes_no_caller_source(self) -> None:
        self.assertFalse(self.profiles["flux-control"]["trust"]["executes_caller_source"])

    def test_privileged_buildah_profiles_are_exact_source_only(self) -> None:
        for profile_id in self.contract["buildah_escalation"]["order"]:
            with self.subTest(profile=profile_id):
                profile = self.profiles[profile_id]
                self.assertTrue(profile["privilege"]["privileged_container"])
                self.assertEqual(profile["trust"]["allowed_source_trust"], ["trusted-exact"])

    def test_contract_offers_no_docker_or_dind_capability(self) -> None:
        for profile in self.contract["profiles"]:
            values = [profile["id"], *profile["public_labels"]]
            values.extend(tool["name"] for tool in profile["tools"])
            self.assertFalse(
                any(
                    "docker" in value.lower() or "dind" in value.lower()
                    for value in values
                )
            )

    def test_generated_outputs_are_current(self) -> None:
        runners.write_generated_outputs(ROOT, check=True)

    def test_generated_mapping_contains_only_contract_selectors(self) -> None:
        mapping = runners.read_json(ROOT / runners.MAPPINGS_PATH)
        approved = runners.approved_selector_index(self.contract)
        for profile_id, profile in mapping["profiles"].items():
            if profile["runs_on"] is not None:
                self.assertEqual(approved[tuple(profile["runs_on"])], profile_id)

    def test_every_inventory_workflow_has_mapping_or_exception(self) -> None:
        inventory = runners.load_workflow_inventory(ROOT)
        report = runners.generate_compatibility_report(self.contract, inventory)
        expected = sum(len(repository["workflows"]) for repository in inventory["repositories"])
        self.assertEqual(report["workflow_count"], expected)
        self.assertEqual(len(report["entries"]), expected)
        for entry in report["entries"]:
            self.assertTrue(entry["profiles"] or entry["exception"])

    def test_unknown_inventory_migration_fails_closed(self) -> None:
        inventory = {
            "workflow_columns": [
                "path",
                "name",
                "status",
                "disposition",
                "migration",
                "trust",
                "blob",
            ],
            "repositories": [
                {
                    "repository": "StreamScapeTV/example",
                    "workflows": [
                        [
                            ".github/workflows/ci.yml",
                            "CI",
                            "current",
                            "thin",
                            "new-class",
                            "read",
                            None,
                        ]
                    ],
                }
            ],
        }
        with self.assertRaisesRegex(runners.RunnerContractError, "unmapped-workflow"):
            runners.generate_compatibility_report(self.contract, inventory)

    def test_public_runner_reference_hides_infrastructure_identity(self) -> None:
        source = (ROOT / "RUNNERS.md").read_text(encoding="utf-8")
        self.assertNotIn("homelab-", source)
        self.assertNotIn("docker-capable-validation", source)
        self.assertIn("Never use bare `self-hosted`", source)
        self.assertIn(
            "Semantic profile IDs are not GitHub runner labels",
            source,
        )
        self.assertIn(
            "No deprecated Linux ARC scheduling alias remains registered",
            source,
        )
        self.assertIn("general-tiny", source)
        self.assertIn("general-small", source)
        self.assertIn("general-medium", source)
        self.assertIn("physical-device", source)

    def test_architecture_records_planner_and_flux_boundary(self) -> None:
        source = (ROOT / "docs/architecture/runners.md").read_text(encoding="utf-8")
        self.assertIn("trusted `general-tiny` planning job", source)
        self.assertIn("A composite action cannot safely resolve `runs-on`", source)
        self.assertIn("Flux owns", source)
        self.assertIn("generate --check", source)

    def test_cli_validate_and_generate_check(self) -> None:
        script = ROOT / "scripts/ci/runner_contract.py"
        for command in (["validate"], ["generate", "--check"]):
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, str(script), *command],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_generated_check_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                runners.CONTRACT_PATH,
                runners.INVENTORY_PATH,
                runners.MAPPINGS_PATH,
                runners.COMPATIBILITY_DOC_PATH,
            ):
                source = ROOT / relative
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            (root / runners.MAPPINGS_PATH).write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(runners.RunnerContractError, "generated-drift"):
                runners.write_generated_outputs(root, check=True)


if __name__ == "__main__":
    unittest.main()
