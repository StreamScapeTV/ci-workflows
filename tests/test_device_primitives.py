from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_workflows.device_primitives import (
    CommandOutcome,
    DevicePrimitiveError,
    DeviceQuery,
    acquire_device_lease,
    discover_devices,
    execute_checked_in_command,
    execute_device_operation,
    normalize_device_record,
    release_device_lease,
    select_device,
)


PRIVATE_ID = "private-device-serial-001"


def row(
    *,
    identifier: str = PRIVATE_ID,
    platform: str = "Android",
    family: str = "Phone",
    runtime: str = "API-36",
    kind: str = "physical",
    state: str = "online",
    capabilities: tuple[str, ...] = ("Camera", "Playback", "camera"),
    shared: bool = True,
) -> dict[str, object]:
    return {
        "identifier": identifier,
        "platform": platform,
        "family": family,
        "runtime": runtime,
        "kind": kind,
        "state": state,
        "capabilities": capabilities,
        "shared": shared,
    }


class FakeDiscovery:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.environments: list[dict[str, str]] = []

    def discover(self, *, environment):
        self.environments.append(dict(environment))
        return list(self.rows)


class FakeLease:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = calls if calls is not None else []
        self.acquire_request: dict[str, object] | None = None
        self.released: list[str] = []
        self.fail_release = False

    def acquire(self, *, resource_hash, owner_hash, ttl_seconds, environment):
        self.calls.append("lease-acquire")
        self.acquire_request = {
            "resource_hash": resource_hash,
            "owner_hash": owner_hash,
            "ttl_seconds": ttl_seconds,
            "environment": dict(environment),
        }
        return "opaque-lease-token-123"

    def release(self, token, *, environment):
        self.calls.append("lease-release")
        if self.fail_release:
            raise RuntimeError("release failed")
        self.released.append(token)


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        calls: list[str] | None = None,
        fail: bool = False,
    ) -> None:
        self.returncode = returncode
        self.calls = calls if calls is not None else []
        self.fail = fail
        self.argv: tuple[str, ...] = ()
        self.environment: dict[str, str] = {}
        self.cwd: Path | None = None
        self.timeout_seconds = 0

    def run(self, argv, *, cwd, environment, timeout_seconds):
        self.calls.append("process")
        if self.fail:
            raise OSError("process unavailable")
        self.argv = tuple(argv)
        self.cwd = cwd
        self.environment = dict(environment)
        self.timeout_seconds = timeout_seconds
        return CommandOutcome(self.returncode, 7)


class FakeState:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = calls if calls is not None else []
        self.fail_restore = False
        self.fail_cleanup = False
        self.residue_values: tuple[str, ...] = ()
        self.snapshot_value = {"volume": "unchanged"}

    def snapshot(self, device, *, environment):
        self.calls.append("snapshot")
        return self.snapshot_value

    def restore(self, device, snapshot, *, environment):
        self.calls.append("restore")
        if self.fail_restore:
            raise RuntimeError("restore failed")
        self.asserted_snapshot = snapshot

    def cleanup(self, device, *, environment):
        self.calls.append("cleanup")
        if self.fail_cleanup:
            raise RuntimeError("cleanup failed")

    def residue(self, device, *, environment):
        self.calls.append("residue")
        return self.residue_values


def runner_environment() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "ExampleOrg/example",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
        "DEVICE_SECRET": "runner-owned-secret",
    }


def checked_in_script(root: Path) -> Path:
    script = root / "scripts" / "device-check"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


class DevicePrimitiveTests(unittest.TestCase):
    def test_discovery_normalizes_capabilities_and_hides_private_identity(self) -> None:
        discovery = FakeDiscovery([row()])
        devices = discover_devices(discovery, environment={"MARKER": "1"})
        self.assertEqual(1, len(devices))
        device = devices[0]
        self.assertEqual("android", device.platform)
        self.assertEqual("phone", device.family)
        self.assertEqual("api-36", device.runtime)
        self.assertEqual("ready", device.state)
        self.assertEqual(("camera", "playback"), device.capabilities)
        projection = device.public_projection()
        self.assertNotIn("identifier", projection)
        self.assertNotIn(PRIVATE_ID, repr(device))
        self.assertNotIn(PRIVATE_ID, str(projection))
        self.assertEqual({"MARKER": "1"}, discovery.environments[0])

    def test_discovery_accepts_simulator_and_rejects_duplicate_private_identity(self) -> None:
        simulator = normalize_device_record(
            row(
                identifier="sim-private-001",
                platform="iOS",
                family="phone",
                runtime="18.0",
                kind="simulator",
                state="booted",
                capabilities=("network",),
                shared=False,
            )
        )
        self.assertEqual("simulator", simulator.kind)
        self.assertFalse(simulator.shared)
        discovery = FakeDiscovery([row(), row()])
        with self.assertRaisesRegex(DevicePrimitiveError, "device_discovery_invalid"):
            discover_devices(discovery, environment={})

    def test_selection_filters_platform_family_runtime_kind_and_capabilities(self) -> None:
        devices = (
            normalize_device_record(
                row(identifier="physical-001", capabilities=("camera", "playback"))
            ),
            normalize_device_record(
                row(
                    identifier="simulator-001",
                    platform="iOS",
                    family="phone",
                    runtime="18.0",
                    kind="simulator",
                    capabilities=("camera", "playback"),
                    shared=False,
                )
            ),
        )
        query = DeviceQuery(
            platform="IOS",
            family="Phone",
            runtime="18.0",
            kind="simulator",
            capabilities=("PLAYBACK",),
        )
        selected = select_device(devices, query)
        self.assertEqual("ios", selected.platform)
        self.assertEqual("simulator", selected.kind)
        self.assertEqual("18.0", selected.runtime)

    def test_selection_is_deterministic_and_reports_offline(self) -> None:
        first = normalize_device_record(row(identifier="device-a", capabilities=("playback",)))
        second = normalize_device_record(row(identifier="device-b", capabilities=("playback",)))
        query = DeviceQuery(
            platform="android",
            family="phone",
            capabilities=("playback",),
        )
        selected_one = select_device((first, second), query)
        selected_two = select_device((second, first), query)
        self.assertEqual(selected_one.identity_hash, selected_two.identity_hash)
        offline = normalize_device_record(
            row(identifier="offline-a", state="offline", capabilities=("playback",))
        )
        with self.assertRaisesRegex(DevicePrimitiveError, "device_offline"):
            select_device((offline,), query)

    def test_lease_boundary_receives_only_opaque_device_and_owner_hashes(self) -> None:
        device = normalize_device_record(row())
        backend = FakeLease()
        environment = runner_environment()
        lease = acquire_device_lease(
            device,
            backend,
            environment=environment,
            ttl_seconds=300,
        )
        assert backend.acquire_request is not None
        request = backend.acquire_request
        self.assertEqual(64, len(str(request["resource_hash"])))
        self.assertEqual(64, len(str(request["owner_hash"])))
        self.assertNotIn(PRIVATE_ID, str(request))
        self.assertNotIn("ExampleOrg/example", str(request["owner_hash"]))
        self.assertNotIn("opaque-lease-token-123", str(lease.public_projection()))
        release_device_lease(lease, backend, environment=environment)
        self.assertEqual(["opaque-lease-token-123"], backend.released)

    def test_checked_in_command_passes_private_identifier_only_through_environment(self) -> None:
        device = normalize_device_record(row())
        process = FakeProcess()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = checked_in_script(root)
            outcome = execute_checked_in_command(
                device,
                source_root=root,
                command_path="scripts/device-check",
                arguments=("--mode", "smoke"),
                process=process,
                environment={"TOKEN": "from-runner"},
                timeout_seconds=90,
            )
        self.assertEqual(0, outcome.returncode)
        self.assertEqual(str(script), process.argv[0])
        self.assertNotIn(PRIVATE_ID, process.argv)
        self.assertEqual(PRIVATE_ID, process.environment["CIW_DEVICE_IDENTIFIER"])
        self.assertEqual(device.identity_hash, process.environment["CIW_DEVICE_IDENTITY_SHA256"])
        self.assertEqual("from-runner", process.environment["TOKEN"])
        self.assertEqual(90, process.timeout_seconds)

    def test_checked_in_command_rejects_traversal_symlink_and_non_executable(self) -> None:
        device = normalize_device_record(row())
        process = FakeProcess()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.write_text("#!/bin/sh\n", encoding="utf-8")
            outside.chmod(0o755)
            scripts = root / "scripts"
            scripts.mkdir()
            link = scripts / "link"
            link.symlink_to(outside)
            plain = scripts / "plain"
            plain.write_text("#!/bin/sh\n", encoding="utf-8")
            plain.chmod(0o644)
            with self.assertRaisesRegex(DevicePrimitiveError, "device_command_invalid"):
                execute_checked_in_command(
                    device,
                    source_root=root,
                    command_path="../outside",
                    arguments=(),
                    process=process,
                    environment={},
                    timeout_seconds=10,
                )
            with self.assertRaisesRegex(DevicePrimitiveError, "device_command_invalid"):
                execute_checked_in_command(
                    device,
                    source_root=root,
                    command_path="scripts/link",
                    arguments=(),
                    process=process,
                    environment={},
                    timeout_seconds=10,
                )
            with self.assertRaisesRegex(DevicePrimitiveError, "device_command_not_executable"):
                execute_checked_in_command(
                    device,
                    source_root=root,
                    command_path="scripts/plain",
                    arguments=(),
                    process=process,
                    environment={},
                    timeout_seconds=10,
                )

    def test_shared_hardware_operation_orders_lease_snapshot_execution_restore_cleanup_release(self) -> None:
        calls: list[str] = []
        discovery = FakeDiscovery([row(capabilities=("playback",))])
        lease = FakeLease(calls)
        process = FakeProcess(calls=calls)
        state = FakeState(calls)
        environment = runner_environment()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checked_in_script(root)
            result = execute_device_operation(
                discovery=discovery,
                query=DeviceQuery(
                    platform="android",
                    family="phone",
                    capabilities=("playback",),
                    kind="physical",
                ),
                source_root=root,
                command_path="scripts/device-check",
                arguments=(),
                process=process,
                state=state,
                environment=environment,
                timeout_seconds=30,
                lease_backend=lease,
                lease_ttl_seconds=600,
            )
        self.assertEqual("success", result.result)
        self.assertEqual("", result.failure_code)
        self.assertEqual(
            ["lease-acquire", "snapshot", "process", "restore", "cleanup", "residue", "lease-release"],
            calls,
        )
        projection = result.public_projection()
        self.assertNotIn(PRIVATE_ID, str(projection))
        self.assertNotIn("opaque-lease-token-123", str(projection))
        self.assertEqual("released", projection["lease"])
        self.assertEqual("success", projection["restoration"])
        self.assertEqual("success", projection["cleanup"])

    def test_command_failure_still_restores_cleans_and_releases(self) -> None:
        calls: list[str] = []
        lease = FakeLease(calls)
        process = FakeProcess(returncode=9, calls=calls)
        state = FakeState(calls)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checked_in_script(root)
            result = execute_device_operation(
                discovery=FakeDiscovery([row(capabilities=("playback",))]),
                query=DeviceQuery(
                    platform="android",
                    family="phone",
                    capabilities=("playback",),
                ),
                source_root=root,
                command_path="scripts/device-check",
                arguments=(),
                process=process,
                state=state,
                environment=runner_environment(),
                timeout_seconds=30,
                lease_backend=lease,
            )
        self.assertEqual("failure", result.result)
        self.assertEqual("device_command_failed", result.failure_code)
        self.assertEqual("success", result.restoration)
        self.assertEqual("success", result.cleanup)
        self.assertEqual("released", result.lease)
        self.assertEqual(9, result.command_returncode)
        self.assertEqual("lease-release", calls[-1])

    def test_cleanup_and_release_fail_closed_without_erasing_primary_failure(self) -> None:
        calls: list[str] = []
        lease = FakeLease(calls)
        lease.fail_release = True
        process = FakeProcess(returncode=5, calls=calls)
        state = FakeState(calls)
        state.residue_values = ("run-owned-state",)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checked_in_script(root)
            result = execute_device_operation(
                discovery=FakeDiscovery([row(capabilities=("playback",))]),
                query=DeviceQuery(
                    platform="android",
                    family="phone",
                    capabilities=("playback",),
                ),
                source_root=root,
                command_path="scripts/device-check",
                arguments=(),
                process=process,
                state=state,
                environment=runner_environment(),
                timeout_seconds=30,
                lease_backend=lease,
            )
        self.assertEqual("failure", result.result)
        self.assertEqual("device_command_failed", result.failure_code)
        self.assertEqual("failure", result.cleanup)
        self.assertEqual("release-failed", result.lease)

    def test_simulator_operation_needs_no_shared_hardware_lease(self) -> None:
        process = FakeProcess()
        state = FakeState()
        simulator = row(
            identifier="simulator-private-01",
            platform="tvos",
            family="tv",
            runtime="18.0",
            kind="simulator",
            capabilities=("playback",),
            shared=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checked_in_script(root)
            result = execute_device_operation(
                discovery=FakeDiscovery([simulator]),
                query=DeviceQuery(
                    platform="tvos",
                    family="tv",
                    runtime="18.0",
                    kind="simulator",
                    capabilities=("playback",),
                ),
                source_root=root,
                command_path="scripts/device-check",
                arguments=(),
                process=process,
                state=state,
                environment={},
                timeout_seconds=30,
            )
        self.assertEqual("success", result.result)
        self.assertEqual("not-required", result.lease)
        self.assertEqual("", result.lease_id)

    def test_shared_device_requires_lease_backend(self) -> None:
        process = FakeProcess()
        state = FakeState()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checked_in_script(root)
            result = execute_device_operation(
                discovery=FakeDiscovery([row(capabilities=("playback",))]),
                query=DeviceQuery(
                    platform="android",
                    family="phone",
                    capabilities=("playback",),
                ),
                source_root=root,
                command_path="scripts/device-check",
                arguments=(),
                process=process,
                state=state,
                environment=runner_environment(),
                timeout_seconds=30,
            )
        self.assertEqual("failure", result.result)
        self.assertEqual("device_lease_required", result.failure_code)
        self.assertEqual("success", result.cleanup)
        self.assertEqual("not-acquired", result.lease)

    def test_module_has_no_product_named_contract(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "ci_workflows"
            / "device_primitives.py"
        ).read_text(encoding="utf-8").casefold()
        for forbidden in ("iptv", "streamscape", "finance-hub", "directus"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
