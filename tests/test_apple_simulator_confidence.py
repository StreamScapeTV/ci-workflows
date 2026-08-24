from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_simulator_confidence import (
    build_simulator_confidence_packet,
    confidence_outputs,
)
from ci_workflows.apple_simulator_script import SIMULATOR_UDID_TOKEN
from ci_workflows.apple_types import AppleValidationError

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class AppleSimulatorConfidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_apple_contract(ROOT)

    def packet(self, platform: str = "tvos") -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "packet_id": "runtime-confidence",
                "platform": platform,
                "steps": [
                    {
                        "id": "focused-regression",
                        "script_path": "scripts/ci/focused-regression.sh",
                        "arguments": ["--case", "startup"],
                    },
                    {
                        "id": "runtime-packet",
                        "script_path": "scripts/ci/runtime-packet.sh",
                        "arguments": ["--packet", "all"],
                    },
                ],
            },
            separators=(",", ":"),
        )

    def build(self, raw: str | None = None):
        return build_simulator_confidence_packet(
            raw if raw is not None else self.packet(),
            repository="StreamScapeTV/example-product",
            admitted_sha=SHA,
            source_trust="trusted-exact",
            contract=self.contract,
        )

    def test_tvos_packet_reuses_one_central_owned_simulator_for_all_steps(self) -> None:
        packet = self.build()
        self.assertEqual("tvos", packet.platform)
        self.assertEqual("tvOS Simulator", packet.apple_plan.simulator.platform)
        self.assertEqual(2, len(packet.apple_plan.commands))
        for command in packet.apple_plan.commands:
            self.assertEqual("bash-script", command.kind)
            self.assertEqual(("--simulator", SIMULATOR_UDID_TOKEN), command.fixed_arguments[-2:])
        outputs = packet.planning_outputs()
        self.assertEqual('["macos-latest"]', outputs["runs_on_json"])
        self.assertEqual("simulator-confidence-only", outputs["confidence_scope"])
        self.assertEqual("github-hosted-macos", outputs["runner_profile"])

    def test_ios_packet_uses_reviewed_central_ios_runtime(self) -> None:
        packet = self.build(self.packet("ios"))
        self.assertEqual("iOS Simulator", packet.apple_plan.simulator.platform)
        self.assertEqual("com.apple.CoreSimulator.SimRuntime.iOS-26-5", packet.apple_plan.simulator.runtime_identifier)

    def test_packet_rejects_untrusted_source_and_unsafe_shapes(self) -> None:
        with self.assertRaises(AppleValidationError):
            build_simulator_confidence_packet(
                self.packet(),
                repository="StreamScapeTV/example-product",
                admitted_sha=SHA,
                source_trust="trusted-pr",
                contract=self.contract,
            )
        for mutation in (
            {"platform": "macos"},
            {"steps": []},
            {"extra": "value"},
        ):
            raw = json.loads(self.packet())
            raw.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(AppleValidationError):
                self.build(json.dumps(raw))

    def test_packet_rejects_malformed_empty_and_oversized_json_with_stable_code(self) -> None:
        for raw in ("", "{", "x" * (32 * 1024 + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(AppleValidationError) as raised:
                self.build(raw)
            self.assertEqual("command_profile_rejected", raised.exception.code)

    def test_packet_rejects_path_traversal_shellish_or_reserved_udid_arguments(self) -> None:
        bad_rows = [
            {"script_path": "../escape.sh", "arguments": []},
            {"script_path": "scripts/ci/run.sh", "arguments": ["$(id)"]},
            {"script_path": "scripts/ci/run.sh", "arguments": [SIMULATOR_UDID_TOKEN]},
        ]
        for replacement in bad_rows:
            raw = json.loads(self.packet())
            raw["steps"][0].update(replacement)
            with self.subTest(replacement=replacement), self.assertRaises(AppleValidationError):
                self.build(json.dumps(raw))

    def test_confidence_evidence_cannot_claim_physical_signing_or_release_authority(self) -> None:
        packet = self.build()
        values = confidence_outputs(
            {
                "result": "success",
                "runner_profile": "apple",
                "test_summary": '{"status":"success"}',
            },
            packet,
        )
        summary = json.loads(values["test_summary"])
        self.assertEqual("github-hosted-macos", values["runner_profile"])
        self.assertEqual("simulator-confidence-only", summary["confidence_scope"])
        self.assertFalse(summary["physical_device_authority"])
        self.assertFalse(summary["signing_authority"])
        self.assertFalse(summary["release_authority"])


if __name__ == "__main__":
    unittest.main()
