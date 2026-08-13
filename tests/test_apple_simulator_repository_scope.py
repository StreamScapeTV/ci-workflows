from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from ci_workflows import apple
from ci_workflows.apple_execution import _simulator_device_name
from ci_workflows.apple_types import AppleProfile

ROOT = Path(__file__).resolve().parents[1]


class AppleSimulatorRepositoryScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = apple.load_apple_contract(ROOT)

    def plan(self, repository: str, consumer: str) -> apple.AppleValidationPlan:
        request = apple.AppleValidationRequest(
            repository=repository,
            admitted_sha="a" * 40,
            consumer_contract=consumer,
            validation_profile=AppleProfile.IOS_SIMULATOR,
            source_trust="trusted-pr",
        )
        return apple.resolve_plan(self.contract, request)

    def test_same_repository_scope_is_stable(self) -> None:
        plan = self.plan("StreamScapeTV/ci-workflows", "ciw-apple-smoke")
        self.assertEqual(
            _simulator_device_name(plan, Path("/tmp/run-a")),
            _simulator_device_name(plan, Path("/tmp/run-b")),
        )
        scope = hashlib.sha256(b"StreamScapeTV/ci-workflows").hexdigest()[:12]
        self.assertIn(scope, _simulator_device_name(plan))

    def test_different_repositories_have_distinct_names(self) -> None:
        central = self.plan("StreamScapeTV/ci-workflows", "ciw-apple-smoke")
        product = self.plan("StreamScapeTV/iptv-apple", "iptv-apple")
        self.assertIsNotNone(central.simulator)
        self.assertIsNotNone(product.simulator)
        assert central.simulator is not None
        assert product.simulator is not None
        self.assertEqual(
            central.simulator.runtime_identifier,
            product.simulator.runtime_identifier,
        )
        self.assertEqual(
            central.simulator.device_type_identifier,
            product.simulator.device_type_identifier,
        )
        self.assertNotEqual(
            _simulator_device_name(central),
            _simulator_device_name(product),
        )


if __name__ == "__main__":
    unittest.main()
