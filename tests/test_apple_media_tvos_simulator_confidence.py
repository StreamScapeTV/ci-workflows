from __future__ import annotations

import unittest
from pathlib import Path

from ci_workflows.apple import resolve_plan
from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_execution import CommandOutcome
from ci_workflows.apple_simulator_script import (
    SIMULATOR_UDID_TOKEN,
    SimulatorLeaseArgumentRunner,
)
from ci_workflows.apple_types import AppleProfile, AppleValidationError, AppleValidationRequest

ROOT = Path(__file__).resolve().parents[1]
UDID = "12345678-1234-1234-1234-123456789ABC"


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, cwd, env, timeout_seconds):
        call = tuple(argv)
        self.calls.append(call)
        if call[:3] == ("xcrun", "simctl", "create"):
            return CommandOutcome(0, f"{UDID}\n", "")
        return CommandOutcome(0, "", "")


class MediaTvOSSimulatorConfidenceTests(unittest.TestCase):
    def plan(self):
        contract = load_apple_contract(ROOT)
        return resolve_plan(
            contract,
            AppleValidationRequest(
                repository="StreamScapeTV/streamscape-media",
                admitted_sha="a" * 40,
                consumer_contract="streamscape-media-tvos-simulator-confidence",
                validation_profile=AppleProfile.TVOS_SIMULATOR,
                source_trust="trusted-exact",
                platform="tvos",
                destination_profile="tvos-simulator-default",
            ),
        )

    def test_fragment_maps_media_to_one_central_tvos_simulator_task(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.task_profile, "media-tvos-simulator-confidence")
        self.assertEqual(plan.runner_profile.value, "apple")
        self.assertIsNotNone(plan.simulator)
        self.assertEqual(plan.simulator.platform, "tvOS Simulator")
        self.assertTrue(plan.requires_simulator)
        self.assertEqual(len(plan.commands), 2)
        regression, packet = plan.commands
        self.assertEqual(regression.script_path, "scripts/ci/test-tvos-simulator-packet.sh")
        self.assertEqual(packet.script_path, "scripts/ci/run-tvos-simulator-packet.sh")
        self.assertEqual(
            packet.fixed_arguments,
            ("--packet", "avfoundation-all", "--simulator", SIMULATOR_UDID_TOKEN),
        )
        self.assertEqual(
            plan.cleanup_paths,
            (".tmp/ci-evidence/tvos-simulator-avfoundation-all",),
        )
        self.assertEqual(plan.artifact_exception_id, None)

    def test_reserved_token_materializes_only_from_successful_central_create(self) -> None:
        delegate = RecordingRunner()
        runner = SimulatorLeaseArgumentRunner(delegate)
        with self.assertRaisesRegex(AppleValidationError, "unsafe_destination"):
            runner.run(
                ("bash", "packet.sh", "--simulator", SIMULATOR_UDID_TOKEN),
                cwd=ROOT,
                env={},
                timeout_seconds=1,
            )
        runner.run(
            (
                "xcrun",
                "simctl",
                "create",
                "CIW Apple TV",
                "com.apple.CoreSimulator.SimDeviceType.Apple-TV-4K-3rd-generation-4K",
                "com.apple.CoreSimulator.SimRuntime.tvOS-26-5",
            ),
            cwd=ROOT,
            env={},
            timeout_seconds=1,
        )
        runner.run(
            ("bash", "packet.sh", "--packet", "avfoundation-all", "--simulator", SIMULATOR_UDID_TOKEN),
            cwd=ROOT,
            env={},
            timeout_seconds=1,
        )
        self.assertEqual(delegate.calls[-1][-1], UDID)
        self.assertNotIn(SIMULATOR_UDID_TOKEN, delegate.calls[-1])

    def test_contract_is_confidence_only_and_has_no_native_or_physical_authority(self) -> None:
        fragment = (ROOT / "contracts/apple-validation-media-tvos-simulator-confidence.json").read_text(encoding="utf-8").casefold()
        self.assertNotIn("mpv", fragment)
        self.assertNotIn("vlc", fragment)
        self.assertNotIn("physical", fragment)
        self.assertNotIn("signing", fragment)
        self.assertNotIn("provision", fragment)
        executor = (ROOT / "src/ci_workflows/apple_simulator_script.py").read_text(encoding="utf-8").casefold()
        self.assertNotIn("streamscape-media", executor)
        self.assertNotIn("simctl shutdown all", executor)
        self.assertNotIn("simctl delete all", executor)


if __name__ == "__main__":
    unittest.main()
