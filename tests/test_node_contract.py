from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.node_contract import (
    load_lockfile,
    load_node_contract,
    load_package_manifest,
    resolve_exact_node_version,
    resolve_validation_plan,
    version_satisfies,
    verify_manifest_engines,
)
from ci_workflows.node_types import NodeValidationError, NodeValidationRequest

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def request(
    repository: str,
    profile: str,
    command: str,
    *,
    version_file: str | None,
    node_version: str | None,
    working_directory: str = ".",
    install_profile: str = "npm-ci",
    output: str | None = None,
    verifier: str | None = None,
    public_environment: dict[str, str] | None = None,
) -> NodeValidationRequest:
    return NodeValidationRequest(
        repository=repository,
        admitted_sha=SHA,
        validation_profile=profile,
        version_file=version_file,
        node_version=node_version,
        working_directory=working_directory,
        install_profile=install_profile,
        command_profile=command,
        script_path=(
            "tool/ci_quality_gate.sh"
            if command == "source-audit"
            else None
        ),
        static_output_directory=output,
        output_verifier_path=verifier,
        public_environment=public_environment or {},
        artifact_exception_id=None,
        source_trust="trusted-exact",
    )


class NodeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_node_contract(ROOT)
        cls.fixtures = json.loads(
            (ROOT / "tests/fixtures/node-validation/cases.json").read_text(
                encoding="utf-8"
            )
        )

    def web_plan(self):
        return resolve_validation_plan(
            self.contract,
            request(
                "StreamScapeTV/StreamScapeWeb",
                "locked-node",
                "quality-test",
                version_file=".nvmrc",
                node_version=None,
            ),
        )

    def direct_plan(self):
        return resolve_validation_plan(
            self.contract,
            request(
                "StreamScapeTV/agent-state",
                "frontend-contract-static",
                "contract-test-build",
                version_file=None,
                node_version="22.18.0",
                working_directory="frontend",
                output="out",
                public_environment={
                    "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:7878",
                    "NEXT_PUBLIC_PROJECT": "iptv-apple",
                },
            ),
        )

    def dashboard_plan(self):
        return resolve_validation_plan(
            self.contract,
            request(
                "StreamScapeTV/agent-state-dashboard",
                "frontend-contract-static",
                "contract-test-build",
                version_file=".nvmrc",
                node_version=None,
                output="out",
            ),
        )

    def test_profiles_commands_and_consumer_matrix_are_exact(self) -> None:
        self.assertEqual(
            set(self.contract["profiles"]),
            {
                "locked-node",
                "next-static-export",
                "frontend-contract-static",
                "node-source-audit",
            },
        )
        self.assertEqual(
            set(self.contract["command_profiles"]),
            {
                "quality-test",
                "quality-test-build",
                "contract-test-build",
                "source-audit",
            },
        )
        self.assertEqual(
            set(self.contract["consumers"]),
            {
                "StreamScapeTV/StreamScapeWeb",
                "StreamScapeTV/agent-state",
                "StreamScapeTV/agent-state-dashboard",
                "StreamScapeTV/finance-hub",
            },
        )
        self.assertEqual(self.contract["runner_profile"], "portable")
        self.assertEqual(self.contract["cache_mode"], "disabled")
        self.assertEqual(self.contract["artifact_policy"], "zero-default")
        self.assertEqual(self.contract["lockfile_version"], 3)

    def test_current_consumer_shapes_resolve_without_product_branches(self) -> None:
        cases = (
            request(
                "StreamScapeTV/StreamScapeWeb",
                "locked-node",
                "quality-test",
                version_file=".nvmrc",
                node_version=None,
            ),
            request(
                "StreamScapeTV/StreamScapeWeb",
                "next-static-export",
                "quality-test-build",
                version_file=".nvmrc",
                node_version=None,
                output="out",
                verifier="scripts/verify-cloudflare-pages-output.ts",
                public_environment={
                    "NEXT_PUBLIC_API_URL": "https://example.invalid"
                },
            ),
            request(
                "StreamScapeTV/agent-state",
                "frontend-contract-static",
                "contract-test-build",
                version_file=None,
                node_version="22.18.0",
                working_directory="frontend",
                output="out",
                public_environment={
                    "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:7878",
                    "NEXT_PUBLIC_PROJECT": "iptv-apple",
                },
            ),
            request(
                "StreamScapeTV/agent-state-dashboard",
                "frontend-contract-static",
                "contract-test-build",
                version_file=".nvmrc",
                node_version=None,
                output="out",
            ),
            request(
                "StreamScapeTV/finance-hub",
                "node-source-audit",
                "source-audit",
                version_file=".node-version",
                node_version=None,
                install_profile="none",
            ),
        )
        plans = [
            resolve_validation_plan(self.contract, value)
            for value in cases
        ]
        self.assertEqual(
            [plan.node_version for plan in plans],
            ["22.18.0", "22.18.0", "22.18.0", "22.18.0", "22.16.0"],
        )
        self.assertEqual(
            [plan.version_file for plan in plans],
            [".nvmrc", ".nvmrc", None, ".nvmrc", ".node-version"],
        )
        self.assertEqual(
            [plan.version_authority for plan in plans],
            [
                "version-file",
                "version-file",
                "exact-api",
                "version-file",
                "version-file",
            ],
        )
        self.assertEqual({plan.runner_profile for plan in plans}, {"portable"})
        self.assertFalse(plans[2].adoption_ready)
        self.assertTrue(plans[0].adoption_ready)
        self.assertTrue(plans[3].adoption_ready)
        self.assertEqual(plans[4].install_profile, "none")

    def test_dashboard_consumer_reuses_bounded_static_contract(self) -> None:
        plan = self.dashboard_plan()
        self.assertEqual(plan.node_version, "22.18.0")
        self.assertEqual(plan.version_file, ".nvmrc")
        self.assertEqual(plan.version_authority, "version-file")
        self.assertEqual(plan.validation_profile, "frontend-contract-static")
        self.assertEqual(plan.command_profile, "contract-test-build")
        self.assertEqual(plan.runner_profile, "portable")
        self.assertEqual(plan.install_profile, "npm-ci")
        self.assertEqual(plan.output_mode, "static-generic")
        self.assertEqual(plan.static_output_directory, "out")
        self.assertIsNone(plan.output_verifier_path)
        self.assertEqual(plan.allowed_public_environment, ())
        self.assertEqual(plan.public_environment, {})
        self.assertTrue(plan.adoption_ready)
        self.assertEqual(
            [(command.stage, command.argv) for command in plan.commands],
            [
                ("tests", ("npm", "test")),
                ("build", ("npm", "run", "validate")),
            ],
        )
        with self.assertRaisesRegex(
            NodeValidationError,
            "public_environment_rejected",
        ):
            resolve_validation_plan(
                self.contract,
                request(
                    "StreamScapeTV/agent-state-dashboard",
                    "frontend-contract-static",
                    "contract-test-build",
                    version_file=".nvmrc",
                    node_version=None,
                    output="out",
                    public_environment={
                        "NEXT_PUBLIC_API_URL": "https://example.invalid"
                    },
                ),
            )

    def test_version_authority_round_trips_through_the_typed_plan(self) -> None:
        version_file_plan = self.web_plan()
        self.assertEqual(version_file_plan.version_file, ".nvmrc")
        self.assertEqual(version_file_plan.node_version, "22.18.0")
        self.assertEqual(version_file_plan.version_authority, "version-file")

        direct_plan = self.direct_plan()
        self.assertIsNone(direct_plan.version_file)
        self.assertEqual(direct_plan.node_version, "22.18.0")
        self.assertEqual(direct_plan.version_authority, "exact-api")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                resolve_exact_node_version(root, direct_plan),
                "22.18.0",
            )

    def test_exact_version_sources_and_engine_bounds(self) -> None:
        plan = self.web_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nvmrc").write_text("22.18.0\n", encoding="utf-8")
            self.assertEqual(
                resolve_exact_node_version(root, plan),
                "22.18.0",
            )
            (root / ".nvmrc").write_text("lts/*\n", encoding="utf-8")
            with self.assertRaisesRegex(
                NodeValidationError,
                "invalid_runtime_source",
            ):
                resolve_exact_node_version(root, plan)
        self.assertTrue(version_satisfies("22.18.0", ">=22.18.0 <23"))
        self.assertTrue(version_satisfies("10.9.2", ">=10.9.2 <11"))
        self.assertFalse(version_satisfies("23.0.0", ">=22.18.0 <23"))
        verify_manifest_engines(
            {
                "engines": {
                    "node": ">=22.18.0 <23",
                    "npm": ">=10.9.2 <11",
                }
            },
            "22.18.0",
            "10.9.2",
        )
        with self.assertRaisesRegex(NodeValidationError, "runtime_mismatch"):
            verify_manifest_engines(
                {"engines": {"node": ">=23 <24"}},
                "22.18.0",
                "10.9.2",
            )

    def test_version_file_missing_symlink_wrong_and_malformed_fail_closed(self) -> None:
        plan = self.web_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                NodeValidationError,
                "invalid_runtime_source",
            ):
                resolve_exact_node_version(root, plan)

            outside = root / "outside-version"
            outside.write_text("22.18.0\n", encoding="utf-8")
            (root / ".nvmrc").symlink_to(outside)
            with self.assertRaisesRegex(
                NodeValidationError,
                "invalid_runtime_source",
            ):
                resolve_exact_node_version(root, plan)
            self.assertEqual(outside.read_text(encoding="utf-8"), "22.18.0\n")
            (root / ".nvmrc").unlink()

            (root / ".nvmrc").write_text("22.17.0\n", encoding="utf-8")
            with self.assertRaisesRegex(NodeValidationError, "runtime_mismatch"):
                resolve_exact_node_version(root, plan)

            for malformed in (
                "lts/*\n",
                ">=22\n",
                "v22.18.0\n",
                "22.18.0  # comment\n",
                "22.18.0\n22.18.0\n",
                "22.18.0",
            ):
                with self.subTest(malformed=malformed):
                    (root / ".nvmrc").write_text(
                        malformed,
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        NodeValidationError,
                        "invalid_runtime_source",
                    ):
                        resolve_exact_node_version(root, plan)

    def test_node_version_file_authority_supports_dot_node_version(self) -> None:
        plan = resolve_validation_plan(
            self.contract,
            request(
                "StreamScapeTV/finance-hub",
                "node-source-audit",
                "source-audit",
                version_file=".node-version",
                node_version=None,
                install_profile="none",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".node-version").write_text(
                "22.16.0\n",
                encoding="utf-8",
            )
            self.assertEqual(
                resolve_exact_node_version(root, plan),
                "22.16.0",
            )

    def test_npm_only_lockfile_contract(self) -> None:
        plan = self.web_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(
                json.dumps({"engines": {"node": ">=22.18.0 <23"}}),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps({"lockfileVersion": 3, "packages": {}}),
                encoding="utf-8",
            )
            self.assertIsNotNone(load_package_manifest(root, plan))
            self.assertIsNotNone(load_lockfile(root, plan))
            (root / "yarn.lock").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(
                NodeValidationError,
                "unsupported_package_manager",
            ):
                load_package_manifest(root, plan)
            (root / "yarn.lock").unlink()
            (root / "package-lock.json").write_text(
                json.dumps({"lockfileVersion": 2, "packages": {}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(NodeValidationError, "lockfile_drift"):
                load_lockfile(root, plan)

    def test_fixture_manifest_covers_security_and_cleanup_failures(self) -> None:
        positive = {item["id"] for item in self.fixtures["positive"]}
        negative = {item["id"] for item in self.fixtures["negative"]}
        self.assertEqual(
            positive,
            {
                "web-locked-quality",
                "web-static-export",
                "agent-state-frontend-contract",
                "finance-source-audit",
            },
        )
        for required in (
            "ranged-version",
            "missing-lockfile",
            "alternate-package-manager",
            "npm-install",
            "secret-expression",
            "output-worker-bundle",
            "verifier-mutation",
            "source-mutation",
            "artifact-exception",
            "cleanup-residue",
            "caller-runner",
            "caller-deployment",
        ):
            self.assertIn(required, negative)


if __name__ == "__main__":
    unittest.main()
