from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import apple  # noqa: E402
from ci_workflows.apple_contract import build_plan  # noqa: E402
from ci_workflows.apple_execution import (  # noqa: E402
    _OWNERSHIP_DIRECTORY,
    _OWNERSHIP_LOCK,
    _OWNERSHIP_REGISTRY,
    CommandOutcome,
    _ownership_record,
    _simulator_device_name,
    _simulator_ownership,
    execute_apple_plan,
    select_simulator,
)
from ci_workflows.apple_types import AppleProfile  # noqa: E402
from tests.test_apple_validation import (  # noqa: E402
    GOOD_UDID,
    SECOND_UDID,
    FakeRunner,
)

STALE_IOS_UDID = "11111111-2222-3333-4444-555555555555"
STALE_TVOS_UDID = "66666666-7777-8888-9999-AAAAAAAAAAAA"
UNRELATED_UDID = "99999999-8888-7777-6666-555555555555"


class ForcedCancellation(BaseException):
    """Synthetic hard cancellation that bypasses ordinary exception cleanup."""


class CancellationRunner(FakeRunner):
    def __init__(
        self,
        *,
        devices: Sequence[Mapping[str, object]] = (),
    ) -> None:
        super().__init__(devices=devices)
        self.cancel_after_create = True

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ):
        outcome = super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        if tuple(argv)[:3] == ("xcrun", "simctl", "create") and self.cancel_after_create:
            raise ForcedCancellation("cancelled immediately after simulator creation")
        return outcome


class BootCancellationRunner(FakeRunner):
    def __init__(
        self,
        *,
        devices: Sequence[Mapping[str, object]] = (),
    ) -> None:
        super().__init__(devices=devices)
        self.cancel_after_boot = True

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ):
        outcome = super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        if tuple(argv)[:3] == ("xcrun", "simctl", "boot") and self.cancel_after_boot:
            raise ForcedCancellation("cancelled after ownership transition during boot")
        return outcome


class TransientCleanupRunner(FakeRunner):
    def __init__(
        self,
        *,
        devices: Sequence[Mapping[str, object]] = (),
        shutdown_failures: int = 0,
        delete_failures: int = 0,
    ) -> None:
        super().__init__(devices=devices)
        self.shutdown_failures = shutdown_failures
        self.delete_failures = delete_failures

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome:
        command = tuple(argv)
        if command[:3] == ("xcrun", "simctl", "shutdown") and self.shutdown_failures:
            self.calls.append(command)
            self.shutdown_failures -= 1
            return CommandOutcome(1, "", "transient shutdown failure")
        if command[:3] == ("xcrun", "simctl", "delete") and self.delete_failures:
            self.calls.append(command)
            self.delete_failures -= 1
            return CommandOutcome(1, "", "transient delete failure")
        return super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )


class AppleSimulatorOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = apple.load_apple_contract(ROOT)

    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temporary = tempfile.TemporaryDirectory()
        # See test_apple_validation: exercise the ownership checks with a real
        # directory, not macOS's /var compatibility symlink.
        root = Path(temporary.name).resolve()
        shutil.copytree(ROOT / "contracts", root / "contracts")
        shutil.copytree(ROOT / "tests" / "fixtures", root / "tests" / "fixtures")
        (root / "AGENTS.md").write_text("fixture rules\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "CIW"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "ciw@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
        ).strip()
        return temporary, root, sha

    def plan(self, sha: str = "a" * 40):
        return build_plan(
            self.contract,
            apple.AppleValidationRequest(
                repository="StreamScapeTV/ci-workflows",
                admitted_sha=sha,
                consumer_contract="ciw-apple-smoke",
                validation_profile=AppleProfile.IOS_SIMULATOR,
                source_trust="trusted-pr",
            ),
        )

    def scoped_plan(
        self,
        sha: str,
        profile: AppleProfile = AppleProfile.IOS_SIMULATOR,
    ):
        return apple.resolve_plan(
            self.contract,
            apple.AppleValidationRequest(
                repository="StreamScapeTV/ci-workflows",
                admitted_sha=sha,
                consumer_contract="ciw-apple-smoke",
                validation_profile=profile,
                source_trust="trusted-pr",
            ),
        )

    def host_environment(
        self,
    ) -> tuple[tempfile.TemporaryDirectory[str], dict[str, str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve() / "runner-workspace"
        workspace.mkdir()
        return temporary, {"RUNNER_WORKSPACE": str(workspace)}, workspace

    @staticmethod
    def write_registry(workspace: Path, rows: list[dict[str, str]]) -> None:
        root = workspace / _OWNERSHIP_DIRECTORY
        root.mkdir(mode=0o700, exist_ok=True)
        (root / _OWNERSHIP_REGISTRY).write_text(
            json.dumps({"schema_version": 1, "owners": rows}, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def device(plan, udid: str, *, available: bool = True, state: str = "Shutdown"):
        assert plan.simulator is not None
        return {
            "name": _simulator_device_name(plan),
            "udid": udid,
            "state": state,
            "isAvailable": available,
            "deviceTypeIdentifier": plan.simulator.device_type_identifier,
        }

    def test_contract_owned_name_is_independent_of_ephemeral_state_root(self) -> None:
        plan = self.plan()
        self.assertEqual(
            _simulator_device_name(plan, Path("/tmp/run-a")),
            _simulator_device_name(plan, Path("/tmp/run-b")),
        )
        self.assertTrue(
            _simulator_device_name(plan).startswith(
                f"{plan.simulator.device_name_prefix} "  # type: ignore[union-attr]
            )
        )

    def test_force_cancellation_recovers_only_exact_owned_simulator(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        state_root = Path(environment["RUNNER_WORKSPACE"]).parent
        sentinel = workspace / "outside-sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        unrelated = {
            "name": "Personal Unrelated Simulator",
            "udid": SECOND_UDID,
            "state": "Shutdown",
            "isAvailable": True,
            "deviceTypeIdentifier": (
                "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"
            ),
        }
        runner = CancellationRunner(devices=[unrelated])
        plan = self.plan(sha)

        with self.assertRaises(ForcedCancellation):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=state_root / "first-state",
                runner=runner,
                environment=environment,
            )

        expected_name = _simulator_device_name(plan)
        self.assertTrue(any(row.get("name") == expected_name for row in runner.devices))
        registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(registry["owners"][0]["status"], "pending-create")

        runner.cancel_after_create = False
        result = execute_apple_plan(
            plan=plan,
            source_root=source,
            state_root=state_root / "second-state",
            runner=runner,
            environment=environment,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
        self.assertTrue(any(row.get("udid") == SECOND_UDID for row in runner.devices))
        self.assertFalse(any(row.get("name") == expected_name for row in runner.devices))
        self.assertGreaterEqual(
            runner.calls.count(("xcrun", "simctl", "delete", GOOD_UDID)),
            2,
        )
        self.assertEqual(
            json.loads(registry_path.read_text(encoding="utf-8"))["owners"],
            [],
        )

    def test_boot_cancellation_recovers_owned_simulator_on_rerun(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        plan = self.scoped_plan(sha)
        runner = BootCancellationRunner()

        with self.assertRaises(ForcedCancellation):
            execute_apple_plan(
                plan=plan,
                source_root=source,
                state_root=workspace.parent / "boot-cancelled",
                runner=runner,
                environment=environment,
            )

        registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(registry["owners"][0]["status"], "owned")
        self.assertEqual(registry["owners"][0]["udid"], GOOD_UDID)
        runner.cancel_after_boot = False

        result = execute_apple_plan(
            plan=plan,
            source_root=source,
            state_root=workspace.parent / "boot-recovery",
            runner=runner,
            environment=environment,
        )

        self.assertEqual(result.status, "success")
        self.assertGreaterEqual(
            runner.calls.count(("xcrun", "simctl", "delete", GOOD_UDID)),
            2,
        )
        self.assertEqual(
            json.loads(registry_path.read_text(encoding="utf-8"))["owners"],
            [],
        )

    def test_prior_exact_head_stale_simulator_is_reconciled_before_selection(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        current_plan = self.scoped_plan(sha)
        prior_plan = self.scoped_plan("b" * 40)
        current_name = _simulator_device_name(current_plan)
        prior_name = _simulator_device_name(prior_plan)
        self.assertNotEqual(prior_name, current_name)
        assert prior_plan.simulator is not None
        assert current_plan.simulator is not None

        for status in ("pending-create", "owned"):
            with self.subTest(status=status):
                _, environment, workspace = self.host_environment()
                state_root = workspace.parent / f"state-{status}"
                stale = self.device(prior_plan, SECOND_UDID, state="Booted")
                unrelated = {
                    "name": "Personal Unrelated Simulator",
                    "udid": UNRELATED_UDID,
                    "state": "Shutdown",
                    "isAvailable": True,
                    "deviceTypeIdentifier": prior_plan.simulator.device_type_identifier,
                }
                row = _ownership_record(
                    prior_plan,
                    status=status,
                    udid=SECOND_UDID if status == "owned" else "",
                )
                self.write_registry(workspace, [row])
                runner = FakeRunner(devices=[stale, unrelated])

                result = execute_apple_plan(
                    plan=current_plan,
                    source_root=source,
                    state_root=state_root,
                    runner=runner,
                    environment=environment,
                )

                self.assertEqual(result.status, "success")
                stale_shutdown = ("xcrun", "simctl", "shutdown", SECOND_UDID)
                stale_delete = ("xcrun", "simctl", "delete", SECOND_UDID)
                current_create = (
                    "xcrun",
                    "simctl",
                    "create",
                    current_name,
                    current_plan.simulator.device_type_identifier,
                    current_plan.simulator.runtime_identifier,
                )
                self.assertLess(runner.calls.index(stale_shutdown), runner.calls.index(stale_delete))
                self.assertLess(runner.calls.index(stale_delete), runner.calls.index(current_create))
                self.assertNotIn(
                    ("xcrun", "simctl", "shutdown", UNRELATED_UDID),
                    runner.calls,
                )
                self.assertNotIn(
                    ("xcrun", "simctl", "delete", UNRELATED_UDID),
                    runner.calls,
                )
                self.assertTrue(
                    any(row.get("udid") == UNRELATED_UDID for row in runner.devices)
                )
                self.assertFalse(any(row.get("name") == prior_name for row in runner.devices))
                self.assertFalse(any(row.get("name") == current_name for row in runner.devices))
                registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
                self.assertEqual(
                    json.loads(registry_path.read_text(encoding="utf-8"))["owners"],
                    [],
                )

    def test_multiple_prior_owner_keys_mixed_ios_tvos_are_reaped(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        current_plan = self.scoped_plan(sha)
        stale_ios = self.scoped_plan("b" * 40, AppleProfile.IOS_SIMULATOR)
        stale_tvos = self.scoped_plan("c" * 40, AppleProfile.TVOS_SIMULATOR)
        assert current_plan.simulator is not None
        assert stale_ios.simulator is not None
        assert stale_tvos.simulator is not None
        rows = [
            _ownership_record(stale_ios, status="owned", udid=STALE_IOS_UDID),
            _ownership_record(stale_tvos, status="owned", udid=STALE_TVOS_UDID),
        ]
        self.write_registry(workspace, rows)
        unrelated = {
            "name": "Personal Unrelated Simulator",
            "udid": UNRELATED_UDID,
            "state": "Shutdown",
            "isAvailable": True,
            "deviceTypeIdentifier": stale_ios.simulator.device_type_identifier,
        }
        runner = FakeRunner(
            devices=[
                self.device(stale_ios, STALE_IOS_UDID, state="Booted"),
                self.device(stale_tvos, STALE_TVOS_UDID, available=False),
                unrelated,
            ]
        )

        result = execute_apple_plan(
            plan=current_plan,
            source_root=source,
            state_root=workspace.parent / "mixed-stale",
            runner=runner,
            environment=environment,
        )

        self.assertEqual(result.status, "success")
        for udid in (STALE_IOS_UDID, STALE_TVOS_UDID):
            self.assertIn(("xcrun", "simctl", "shutdown", udid), runner.calls)
            self.assertIn(("xcrun", "simctl", "delete", udid), runner.calls)
            self.assertFalse(any(row.get("udid") == udid for row in runner.devices))
        self.assertTrue(any(row.get("udid") == UNRELATED_UDID for row in runner.devices))
        self.assertNotIn(("xcrun", "simctl", "delete", UNRELATED_UDID), runner.calls)
        registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
        self.assertEqual(
            json.loads(registry_path.read_text(encoding="utf-8"))["owners"],
            [],
        )

    def test_prior_exact_head_missing_simulator_row_is_cleared_idempotently(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        current_plan = self.scoped_plan(sha)
        prior_plan = self.scoped_plan("b" * 40)
        assert prior_plan.simulator is not None
        unrelated = {
            "name": "Personal Unrelated Simulator",
            "udid": UNRELATED_UDID,
            "state": "Shutdown",
            "isAvailable": True,
            "deviceTypeIdentifier": prior_plan.simulator.device_type_identifier,
        }
        self.write_registry(
            workspace,
            [_ownership_record(prior_plan, status="owned", udid=SECOND_UDID)],
        )
        runner = FakeRunner(devices=[unrelated])

        first = execute_apple_plan(
            plan=current_plan,
            source_root=source,
            state_root=workspace.parent / "state-missing-stale",
            runner=runner,
            environment=environment,
        )
        second = execute_apple_plan(
            plan=current_plan,
            source_root=source,
            state_root=workspace.parent / "state-missing-stale-second",
            runner=runner,
            environment=environment,
        )

        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "success")
        self.assertNotIn(("xcrun", "simctl", "delete", SECOND_UDID), runner.calls)
        self.assertNotIn(
            ("xcrun", "simctl", "delete", UNRELATED_UDID),
            runner.calls,
        )
        self.assertTrue(any(row.get("udid") == UNRELATED_UDID for row in runner.devices))
        registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
        self.assertEqual(
            json.loads(registry_path.read_text(encoding="utf-8"))["owners"],
            [],
        )

    def test_transient_shutdown_and_delete_failures_are_retried_with_readback(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        current_plan = self.scoped_plan(sha)
        stale_plan = self.scoped_plan("b" * 40)
        self.write_registry(
            workspace,
            [_ownership_record(stale_plan, status="owned", udid=SECOND_UDID)],
        )
        runner = TransientCleanupRunner(
            devices=[self.device(stale_plan, SECOND_UDID, state="Booted")],
            shutdown_failures=1,
            delete_failures=1,
        )

        result = execute_apple_plan(
            plan=current_plan,
            source_root=source,
            state_root=workspace.parent / "transient-cleanup",
            runner=runner,
            environment=environment,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            runner.calls.count(("xcrun", "simctl", "delete", SECOND_UDID)),
            2,
        )
        self.assertGreaterEqual(
            runner.calls.count(("xcrun", "simctl", "shutdown", SECOND_UDID)),
            2,
        )
        registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
        self.assertEqual(
            json.loads(registry_path.read_text(encoding="utf-8"))["owners"],
            [],
        )

    def test_permanent_delete_failure_fails_closed_and_retains_registry_row(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        current_plan = self.scoped_plan(sha)
        stale_plan = self.scoped_plan("b" * 40)
        stale_name = _simulator_device_name(stale_plan)
        self.write_registry(
            workspace,
            [_ownership_record(stale_plan, status="owned", udid=SECOND_UDID)],
        )
        runner = TransientCleanupRunner(
            devices=[self.device(stale_plan, SECOND_UDID, state="Booted")],
            delete_failures=99,
        )

        with self.assertRaises(apple.AppleValidationError) as captured:
            execute_apple_plan(
                plan=current_plan,
                source_root=source,
                state_root=workspace.parent / "permanent-cleanup-failure",
                runner=runner,
                environment=environment,
            )

        self.assertEqual(captured.exception.code, "cleanup_failed")
        self.assertTrue(captured.exception.cleanup_failed)
        self.assertTrue(any(row.get("name") == stale_name for row in runner.devices))
        assert current_plan.simulator is not None
        self.assertNotIn(
            (
                "xcrun",
                "simctl",
                "create",
                _simulator_device_name(current_plan),
                current_plan.simulator.device_type_identifier,
                current_plan.simulator.runtime_identifier,
            ),
            runner.calls,
        )
        registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
        owners = json.loads(registry_path.read_text(encoding="utf-8"))["owners"]
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0]["udid"], SECOND_UDID)

    def test_full_registry_of_stale_rows_is_recovered_before_current_claim(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        current_plan = self.scoped_plan(sha)
        stale_plans = [self.scoped_plan(f"{index + 1:040x}") for index in range(8)]
        rows = [_ownership_record(plan, status="pending-create") for plan in stale_plans]
        self.assertEqual(len({row["owner_key"] for row in rows}), 8)
        self.write_registry(workspace, rows)
        runner = FakeRunner()

        result = execute_apple_plan(
            plan=current_plan,
            source_root=source,
            state_root=workspace.parent / "capacity-recovery",
            runner=runner,
            environment=environment,
        )

        self.assertEqual(result.status, "success")
        assert current_plan.simulator is not None
        self.assertIn(
            (
                "xcrun",
                "simctl",
                "create",
                _simulator_device_name(current_plan),
                current_plan.simulator.device_type_identifier,
                current_plan.simulator.runtime_identifier,
            ),
            runner.calls,
        )
        registry_path = workspace / _OWNERSHIP_DIRECTORY / _OWNERSHIP_REGISTRY
        self.assertEqual(
            json.loads(registry_path.read_text(encoding="utf-8"))["owners"],
            [],
        )

    def test_runner_local_lock_contention_fails_closed(self) -> None:
        _, environment, workspace = self.host_environment()
        state = workspace.parent / "state"
        state.mkdir()
        with _simulator_ownership(environment, state):
            with self.assertRaises(apple.AppleValidationError) as captured:
                with _simulator_ownership(environment, state):
                    pass
        self.assertEqual(captured.exception.code, "simulator_ownership_locked")

    def test_github_actions_sibling_runner_workspaces_share_host_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            host_home = base / "host-home"
            host_home.mkdir(mode=0o700)
            host_home.chmod(0o700)
            workspace_a = base / "actions-runner" / "_work"
            workspace_b = base / "actions-runner2" / "_work"
            workspace_a.mkdir(parents=True)
            workspace_b.mkdir(parents=True)
            state_a = base / "state-a"
            state_b = base / "state-b"
            state_a.mkdir()
            state_b.mkdir()
            environment_a = {
                "GITHUB_ACTIONS": "true",
                "RUNNER_WORKSPACE": str(workspace_a),
            }
            environment_b = {
                "GITHUB_ACTIONS": "true",
                "RUNNER_WORKSPACE": str(workspace_b),
            }
            account = SimpleNamespace(pw_dir=str(host_home))
            real_flock = fcntl.flock
            operations: list[int] = []

            def recording_flock(fd: int, operation: int) -> None:
                operations.append(operation)
                real_flock(fd, operation)

            with patch(
                "ci_workflows.apple_execution.pwd.getpwuid",
                return_value=account,
            ), patch(
                "ci_workflows.apple_execution.fcntl.flock",
                side_effect=recording_flock,
            ):
                expected_root = host_home / _OWNERSHIP_DIRECTORY
                with _simulator_ownership(environment_a, state_a) as first:
                    self.assertEqual(first.root, expected_root)
                    self.assertEqual(
                        stat.S_IMODE(os.stat(expected_root).st_mode),
                        0o700,
                    )
                with _simulator_ownership(environment_b, state_b) as second:
                    self.assertEqual(second.root, expected_root)

            acquisitions = [
                operation
                for operation in operations
                if operation != fcntl.LOCK_UN
            ]
            self.assertEqual(acquisitions, [fcntl.LOCK_EX, fcntl.LOCK_EX])

    def test_registry_root_and_file_symlinks_are_rejected_without_following(self) -> None:
        for target_kind in ("root", "registry", "lock"):
            with self.subTest(target_kind=target_kind):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory).resolve()
                    workspace = base / "runner-workspace"
                    workspace.mkdir()
                    environment = {"RUNNER_WORKSPACE": str(workspace)}
                    outside = base / "outside"
                    outside.mkdir()
                    state = base / "state"
                    state.mkdir()
                    root = workspace / _OWNERSHIP_DIRECTORY
                    if target_kind == "root":
                        root.symlink_to(outside, target_is_directory=True)
                    else:
                        root.mkdir(mode=0o700)
                        target = outside / target_kind
                        target.write_text("outside\n", encoding="utf-8")
                        name = (
                            _OWNERSHIP_REGISTRY
                            if target_kind == "registry"
                            else _OWNERSHIP_LOCK
                        )
                        (root / name).symlink_to(target)
                    with self.assertRaises(apple.AppleValidationError) as captured:
                        with _simulator_ownership(environment, state):
                            pass
                    self.assertIn(
                        captured.exception.code,
                        {
                            "simulator_ownership_invalid",
                            "simulator_ownership_corrupt",
                        },
                    )
                    outside_value = outside / target_kind
                    if outside_value.is_file():
                        self.assertEqual(
                            outside_value.read_text(encoding="utf-8"),
                            "outside\n",
                        )

    def test_runner_workspace_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            outside = base / "outside"
            outside.mkdir()
            workspace = base / "runner-workspace"
            workspace.symlink_to(outside, target_is_directory=True)
            state = base / "state"
            state.mkdir()
            with self.assertRaises(apple.AppleValidationError) as captured:
                with _simulator_ownership(
                    {"RUNNER_WORKSPACE": str(workspace)},
                    state,
                ):
                    pass
            self.assertEqual(captured.exception.code, "simulator_ownership_invalid")

    def test_corrupted_registry_fails_closed(self) -> None:
        _, environment, workspace = self.host_environment()
        root = workspace / _OWNERSHIP_DIRECTORY
        root.mkdir(mode=0o700)
        (root / _OWNERSHIP_REGISTRY).write_text("{not-json", encoding="utf-8")
        state = workspace.parent / "state"
        state.mkdir()
        with self.assertRaises(apple.AppleValidationError) as captured:
            with _simulator_ownership(environment, state):
                pass
        self.assertEqual(captured.exception.code, "simulator_ownership_corrupt")

    def test_non_ciw_registry_device_name_is_rejected(self) -> None:
        _, environment, workspace = self.host_environment()
        plan = self.plan()
        row = _ownership_record(plan, status="pending-create")
        row["device_name"] = f"Personal Simulator {row['owner_key'][:16]}"
        self.write_registry(workspace, [row])
        state = workspace.parent / "non-ciw-row"
        state.mkdir()

        with self.assertRaises(apple.AppleValidationError) as captured:
            with _simulator_ownership(environment, state):
                pass

        self.assertEqual(captured.exception.code, "simulator_ownership_corrupt")

    def test_stale_runtime_or_device_identity_is_rejected(self) -> None:
        for field, value in (
            ("runtime_identifier", "com.apple.CoreSimulator.SimRuntime.iOS-25-0"),
            (
                "device_type_identifier",
                "com.apple.CoreSimulator.SimDeviceType.iPhone-16-Pro",
            ),
        ):
            with self.subTest(field=field):
                _, environment, workspace = self.host_environment()
                state = workspace.parent / f"state-{field}"
                state.mkdir()
                source = workspace.parent / f"source-{field}"
                source.mkdir()
                plan = self.plan()
                row = _ownership_record(plan, status="pending-create")
                row[field] = value
                self.write_registry(workspace, [row])
                with _simulator_ownership(environment, state) as ownership:
                    with self.assertRaises(apple.AppleValidationError) as captured:
                        select_simulator(
                            plan,
                            source,
                            state,
                            FakeRunner(),
                            environment,
                            ownership=ownership,
                        )
                self.assertEqual(
                    captured.exception.code,
                    "simulator_ownership_identity_mismatch",
                )

    def test_cleanup_failure_preserves_primary_failure_with_host_registry(self) -> None:
        temporary, source, sha = self.make_repo()
        self.addCleanup(temporary.cleanup)
        _, environment, workspace = self.host_environment()
        with self.assertRaises(apple.AppleValidationError) as captured:
            execute_apple_plan(
                plan=self.plan(sha),
                source_root=source,
                state_root=workspace.parent / "primary-and-cleanup",
                runner=FakeRunner(
                    fail_token="AppleValidationSmoke.xcodeproj",
                    retain_deleted_device=True,
                ),
                environment=environment,
            )
        self.assertEqual(captured.exception.code, "xcodebuild_failed")
        self.assertTrue(captured.exception.cleanup_failed)


if __name__ == "__main__":
    unittest.main()
