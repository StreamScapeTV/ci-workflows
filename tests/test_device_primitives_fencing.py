from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_workflows.device_primitives import (
    CommandOutcome,
    DeviceQuery,
    execute_device_operation,
)


class Discovery:
    def discover(self, *, environment):
        return [
            {
                "identifier": "private-shared-device",
                "platform": "android",
                "family": "phone",
                "runtime": "api-36",
                "kind": "physical",
                "state": "ready",
                "capabilities": ["playback"],
                "shared": True,
            }
        ]


class FailingLease:
    def acquire(self, *, resource_hash, owner_hash, ttl_seconds, environment):
        raise RuntimeError("collision")

    def release(self, token, *, environment):
        raise AssertionError("release must not run without an acquired lease")


class ForbiddenProcess:
    def run(self, argv, *, cwd, environment, timeout_seconds):
        raise AssertionError("process must not run without an acquired lease")


class RecordingState:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def snapshot(self, device, *, environment):
        self.calls.append("snapshot")
        return {}

    def restore(self, device, snapshot, *, environment):
        self.calls.append("restore")

    def cleanup(self, device, *, environment):
        self.calls.append("cleanup")

    def residue(self, device, *, environment):
        self.calls.append("residue")
        return ()


class DevicePrimitiveFencingTests(unittest.TestCase):
    def test_failed_shared_hardware_lease_performs_no_device_mutation(self) -> None:
        state = RecordingState()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "device-check"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
            result = execute_device_operation(
                discovery=Discovery(),
                query=DeviceQuery(
                    platform="android",
                    family="phone",
                    capabilities=("playback",),
                    kind="physical",
                ),
                source_root=root,
                command_path="device-check",
                arguments=(),
                process=ForbiddenProcess(),
                state=state,
                environment={
                    "GITHUB_REPOSITORY": "ExampleOrg/example",
                    "GITHUB_RUN_ID": "1",
                    "GITHUB_RUN_ATTEMPT": "1",
                },
                timeout_seconds=30,
                lease_backend=FailingLease(),
            )
        self.assertEqual("failure", result.result)
        self.assertEqual("device_lease_failed", result.failure_code)
        self.assertEqual("not-acquired", result.lease)
        self.assertEqual("not-started", result.restoration)
        self.assertEqual("success", result.cleanup)
        self.assertEqual([], state.calls)


if __name__ == "__main__":
    unittest.main()
