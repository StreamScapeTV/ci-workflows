from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Mapping, Sequence

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
        root.mkdir(exist_ok=True)
        (root / _OWNERSHIP_REGISTRY).write_text(
            json.dumps({"schema_version": 1, "owners": rows}, sort_keys=True),
            encoding="utf-8",
        )

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

    def test_runner_local_lock_contention_fails_closed(self) -> None:
        _, environment, workspace = self.host_environment()
        state = workspace.parent / "state"
        state.mkdir()
        with _simulator_ownership(environment, state):
            with self.assertRaises(apple.AppleValidationError) as captured:
                with _simulator_ownership(environment, state):
                    pass
        self.assertEqual(captured.exception.code, "simulator_ownership_locked")

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
                        root.mkdir()
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
        root.mkdir()
        (root / _OWNERSHIP_REGISTRY).write_text("{not-json", encoding="utf-8")
        state = workspace.parent / "state"
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
