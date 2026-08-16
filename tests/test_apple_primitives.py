from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ci_workflows.apple_primitives import (
    ApplePlatform,
    ApplePrimitiveError,
    CommandOutcome,
    SimulatorRequest,
    XcodeAction,
    XcodeBuildRequest,
    cleanup_output_paths,
    cleanup_simulator,
    create_boot_simulator,
    inspect_toolchain,
    plan_export_archive,
    plan_unsigned_package,
    plan_xcodebuild,
    run_command,
    select_xcode,
)


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv, *, cwd, env, timeout_seconds):
        argv = tuple(argv)
        env = dict(env)
        self.calls.append((argv, cwd, env, timeout_seconds))
        if argv == ("xcodebuild", "-version"):
            if env.get("DEVELOPER_DIR", "").endswith(
                "Xcode-B/Contents/Developer"
            ):
                return CommandOutcome(
                    0,
                    "Xcode 26.0\nBuild version 17A100\n",
                )
            return CommandOutcome(0, "Xcode 26.1\nBuild version 17B42\n")
        if argv == ("swift", "--version"):
            return CommandOutcome(
                0,
                "Apple Swift version 6.2 (swiftlang-6.2.0.1)\n",
            )
        if len(argv) == 4 and argv[:2] == ("xcrun", "--sdk"):
            return CommandOutcome(0, "26.0\n")
        if argv[:3] == ("xcrun", "simctl", "create"):
            return CommandOutcome(
                0,
                "11111111-2222-3333-4444-555555555555\n",
            )
        return CommandOutcome(0)


class BootFailureRunner(FakeRunner):
    def run(self, argv, *, cwd, env, timeout_seconds):
        outcome = super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        if tuple(argv)[:3] == ("xcrun", "simctl", "bootstatus"):
            return CommandOutcome(1, "", "boot failed")
        return outcome


class ApplePrimitivePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.state = self.root / "state"
        self.source.mkdir()
        self.state.mkdir()
        (self.source / "App.xcodeproj").mkdir()
        (self.source / "App.xcworkspace").mkdir()
        (self.source / "ExportOptions.plist").write_text(
            "<plist/>",
            encoding="utf-8",
        )
        (self.source / "Build/App.app").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ios_build_plan_is_unsigned_and_bounded(self) -> None:
        spec = plan_xcodebuild(
            XcodeBuildRequest(
                platform=ApplePlatform.IOS,
                action=XcodeAction.BUILD,
                container_kind="project",
                container_path="App.xcodeproj",
                scheme="App",
                destination="generic/platform=iOS Simulator",
                sdk="iphonesimulator",
                derived_data_path="ios/DerivedData",
            ),
            source_root=self.source,
            state_root=self.state,
        )
        self.assertIn("-project", spec.argv)
        self.assertIn("iphonesimulator", spec.argv)
        self.assertIn("generic/platform=iOS Simulator", spec.argv)
        self.assertIn("build", spec.argv)
        self.assertEqual(
            ("CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO"),
            spec.argv[-2:],
        )

    def test_tvos_test_plan_uses_caller_destination_plan_and_filters(self) -> None:
        spec = plan_xcodebuild(
            XcodeBuildRequest(
                platform=ApplePlatform.TVOS,
                action=XcodeAction.TEST,
                container_kind="workspace",
                container_path="App.xcworkspace",
                scheme="App TV",
                destination=(
                    "platform=tvOS Simulator,"
                    "id=11111111-2222-3333-4444-555555555555"
                ),
                sdk="appletvsimulator",
                derived_data_path="tvos/DerivedData",
                result_bundle_path="tvos/Results.xcresult",
                test_plan="TV Acceptance",
                only_testing=("AppTVTests/PlaybackTests",),
                skip_testing=("AppTVTests/SlowNetworkTests",),
            ),
            source_root=self.source,
            state_root=self.state,
        )
        self.assertIn("-workspace", spec.argv)
        self.assertIn("-resultBundlePath", spec.argv)
        self.assertIn("-testPlan", spec.argv)
        self.assertIn(
            "-only-testing:AppTVTests/PlaybackTests",
            spec.argv,
        )
        self.assertIn(
            "-skip-testing:AppTVTests/SlowNetworkTests",
            spec.argv,
        )
        self.assertIn("test", spec.argv)

    def test_macos_archive_and_authorized_export_plans(self) -> None:
        archive = plan_xcodebuild(
            XcodeBuildRequest(
                platform=ApplePlatform.MACOS,
                action=XcodeAction.ARCHIVE,
                container_kind="project",
                container_path="App.xcodeproj",
                scheme="App",
                configuration="Release",
                destination="generic/platform=macOS",
                sdk="macosx",
                derived_data_path="macos/DerivedData",
                archive_path="macos/App.xcarchive",
                signing_authorized=True,
            ),
            source_root=self.source,
            state_root=self.state,
        )
        self.assertIn("-archivePath", archive.argv)
        self.assertEqual("archive", archive.argv[-1])
        self.assertNotIn("CODE_SIGNING_ALLOWED=NO", archive.argv)
        export = plan_export_archive(
            archive_path="macos/App.xcarchive",
            export_path="macos/Export",
            export_options_plist="ExportOptions.plist",
            source_root=self.source,
            state_root=self.state,
            signing_authorized=True,
        )
        self.assertIn("-exportArchive", export.argv)
        self.assertTrue(export.signing_authorized)

    def test_unsigned_package_is_product_neutral_ditto_plan(self) -> None:
        spec = plan_unsigned_package(
            "Build/App.app",
            "packages/App.zip",
            source_root=self.source,
            state_root=self.state,
        )
        self.assertEqual(("ditto", "-c", "-k"), spec.argv[:3])
        self.assertFalse(spec.signing_authorized)

    def test_invalid_escape_and_unauthorized_signing_fail_closed(self) -> None:
        with self.assertRaises(ApplePrimitiveError):
            plan_unsigned_package(
                "../outside",
                "package.zip",
                source_root=self.source,
                state_root=self.state,
            )
        spec = plan_xcodebuild(
            XcodeBuildRequest(
                platform=ApplePlatform.IOS,
                action=XcodeAction.BUILD,
                container_kind="project",
                container_path="App.xcodeproj",
                scheme="App",
            ),
            source_root=self.source,
            state_root=self.state,
        )
        with self.assertRaises(ApplePrimitiveError) as raised:
            run_command(
                spec,
                FakeRunner(),
                environment={"DEVELOPMENT_TEAM": "PRIVATE"},
            )
        self.assertEqual("signing_not_authorized", raised.exception.code)


class ApplePrimitiveRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.runner = FakeRunner()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_inspect_toolchain_parses_xcode_swift_and_sdks(self) -> None:
        identity = inspect_toolchain(
            self.runner,
            cwd=self.cwd,
            environment={},
            sdks=("iphoneos", "macosx"),
        )
        self.assertEqual(
            ("26.1", "17B42", "6.2"),
            (
                identity.xcode_version,
                identity.xcode_build,
                identity.swift_version,
            ),
        )
        self.assertEqual(
            (("iphoneos", "26.0"), ("macosx", "26.0")),
            identity.sdk_versions,
        )

    def test_select_xcode_uses_developer_dir_without_shell_mutation(self) -> None:
        first = self.root / "Xcode-A/Contents/Developer"
        second = self.root / "Xcode-B/Contents/Developer"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        selected = select_xcode(
            (first, second),
            expected_version="26.0",
            expected_build="17A100",
            runner=self.runner,
            cwd=self.cwd,
            environment={"PATH": "/usr/bin"},
        )
        self.assertEqual(second.resolve(), selected)
        self.assertEqual("/usr/bin", self.runner.calls[-1][2]["PATH"])

    def test_simulator_create_boot_shutdown_delete_and_destination(self) -> None:
        lease = create_boot_simulator(
            SimulatorRequest(
                platform=ApplePlatform.IOS,
                name="CI iPhone",
                runtime_identifier=(
                    "com.apple.CoreSimulator.SimRuntime.iOS-26-0"
                ),
                device_type_identifier=(
                    "com.apple.CoreSimulator.SimDeviceType.iPhone-17"
                ),
            ),
            self.runner,
            cwd=self.cwd,
            environment={},
        )
        self.assertEqual(
            "platform=iOS Simulator,"
            "id=11111111-2222-3333-4444-555555555555",
            lease.destination,
        )
        cleanup_simulator(
            lease,
            self.runner,
            cwd=self.cwd,
            environment={},
        )
        commands = [call[0] for call in self.runner.calls]
        self.assertIn(
            ("xcrun", "simctl", "shutdown", lease.udid),
            commands,
        )
        self.assertIn(
            ("xcrun", "simctl", "delete", lease.udid),
            commands,
        )

    def test_failed_simulator_boot_deletes_created_device(self) -> None:
        runner = BootFailureRunner()
        with self.assertRaises(ApplePrimitiveError):
            create_boot_simulator(
                SimulatorRequest(
                    platform=ApplePlatform.TVOS,
                    name="CI Apple TV",
                    runtime_identifier=(
                        "com.apple.CoreSimulator.SimRuntime.tvOS-26-0"
                    ),
                    device_type_identifier=(
                        "com.apple.CoreSimulator.SimDeviceType."
                        "Apple-TV-4K-3rd-generation"
                    ),
                ),
                runner,
                cwd=self.cwd,
                environment={},
            )
        commands = [call[0] for call in runner.calls]
        self.assertIn(
            (
                "xcrun",
                "simctl",
                "delete",
                "11111111-2222-3333-4444-555555555555",
            ),
            commands,
        )

    def test_cleanup_removes_state_but_never_follows_symlink(self) -> None:
        state = self.root / "state"
        state.mkdir()
        derived = state / "DerivedData"
        results = state / "Results"
        derived.mkdir()
        results.mkdir()
        (derived / "cache").write_text("x", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep"
        marker.write_text("keep", encoding="utf-8")
        os.symlink(outside, results / "external")
        cleanup_output_paths(state, ("DerivedData", "Results"))
        self.assertFalse(derived.exists())
        self.assertFalse(os.path.lexists(results))
        self.assertEqual("keep", marker.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
