from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from ci_workflows.native_primitives import (
    ConfigureStep,
    NativePrimitiveError,
    cleanup_native_state,
    cmake_build,
    cmake_configure,
    cmake_install,
    create_deterministic_archive,
    inspect_native_outputs,
    run_configure_steps,
    run_make,
    run_ninja,
    sha256_file,
)
from ci_workflows.runtime_primitives import ProcessResult


class RecordingRunner:
    def __init__(self, result: ProcessResult | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result or ProcessResult(0, "", "", False)

    def __call__(self, argv, *, cwd, environment, timeout_seconds):
        self.calls.append(
            {
                "argv": tuple(argv),
                "cwd": Path(cwd),
                "environment": dict(environment),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.result


class NativePrimitiveTests(unittest.TestCase):
    def test_configure_steps_are_shell_free_and_ordered(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            runner = RecordingRunner()
            results = run_configure_steps(
                (
                    ConfigureStep(
                        "./configure",
                        ("--enable-static", "--host=x86_64-linux"),
                        root,
                    ),
                    ConfigureStep("config.status", ("--recheck",), root),
                ),
                environment={"CC": "clang"},
                runner=runner,
            )
        self.assertEqual(
            ("configure", "configure"),
            tuple(item.operation for item in results),
        )
        self.assertEqual(
            ("./configure", "--enable-static", "--host=x86_64-linux"),
            runner.calls[0]["argv"],
        )
        self.assertEqual(("config.status", "--recheck"), runner.calls[1]["argv"])
        self.assertNotIn("bash", runner.calls[0]["argv"])
        self.assertNotIn("sh", runner.calls[0]["argv"])

    def test_configure_failure_and_timeout_are_stable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            with self.assertRaisesRegex(NativePrimitiveError, "configure_failed"):
                run_configure_steps(
                    (ConfigureStep("./configure", (), root),),
                    environment={},
                    runner=RecordingRunner(ProcessResult(2, "", "bad", False)),
                )
            with self.assertRaisesRegex(NativePrimitiveError, "configure_timeout"):
                run_configure_steps(
                    (ConfigureStep("./configure", (), root),),
                    environment={},
                    runner=RecordingRunner(ProcessResult(None, "", "", True)),
                )

    def test_cmake_configure_sorts_definitions_and_keeps_paths_explicit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            source = root / "source"
            source.mkdir()
            build = root / "build"
            runner = RecordingRunner()
            cmake_configure(
                source_dir=source,
                build_dir=build,
                definitions={"ZETA": "2", "ALPHA": "1"},
                generator="Ninja",
                options=("-Wno-dev",),
                environment={"CC": "clang"},
                runner=runner,
            )
        self.assertEqual(
            (
                "cmake",
                "-S",
                str(source),
                "-B",
                str(build),
                "-G",
                "Ninja",
                "-DALPHA=1",
                "-DZETA=2",
                "-Wno-dev",
            ),
            runner.calls[0]["argv"],
        )

    def test_cmake_build_and_install_are_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            build = root / "build"
            build.mkdir()
            install = root / "install"
            runner = RecordingRunner()
            cmake_build(
                build_dir=build,
                jobs=8,
                target="all",
                configuration="Release",
                options=("-v",),
                environment={},
                runner=runner,
            )
            cmake_install(
                build_dir=build,
                install_dir=install,
                configuration="Release",
                component="runtime",
                environment={},
                runner=runner,
            )
        self.assertEqual(
            (
                "cmake",
                "--build",
                str(build),
                "--parallel",
                "8",
                "--target",
                "all",
                "--config",
                "Release",
                "--",
                "-v",
            ),
            runner.calls[0]["argv"],
        )
        self.assertEqual(
            (
                "cmake",
                "--install",
                str(build),
                "--prefix",
                str(install),
                "--config",
                "Release",
                "--component",
                "runtime",
            ),
            runner.calls[1]["argv"],
        )

    def test_make_and_ninja_targets_have_explicit_jobs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            runner = RecordingRunner()
            run_make(
                cwd=root,
                targets=("all", "install"),
                jobs=4,
                options=("V=1",),
                environment={},
                runner=runner,
            )
            run_ninja(
                cwd=root,
                targets=("check",),
                jobs=6,
                options=("-v",),
                environment={},
                runner=runner,
            )
        self.assertEqual(
            ("make", "-j4", "V=1", "all", "install"),
            runner.calls[0]["argv"],
        )
        self.assertEqual(
            ("ninja", "-j6", "-v", "check"),
            runner.calls[1]["argv"],
        )

    def test_make_and_ninja_reject_job_override_and_option_targets(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            calls = (
                lambda: run_make(
                    cwd=root,
                    targets=("all",),
                    jobs=2,
                    options=("-j99",),
                    environment={},
                    runner=RecordingRunner(),
                ),
                lambda: run_ninja(
                    cwd=root,
                    targets=("-t",),
                    jobs=2,
                    environment={},
                    runner=RecordingRunner(),
                ),
            )
            for call in calls:
                with self.assertRaises(NativePrimitiveError):
                    call()

    def test_archive_is_deterministic_and_checksum_matches(self):
        with tempfile.TemporaryDirectory() as raw:
            outer = Path(raw).resolve()
            root = outer / "root"
            root.mkdir()
            (root / "include").mkdir()
            header = root / "include" / "native.h"
            header.write_text("int native(void);\n", encoding="utf-8")
            library = root / "libnative.a"
            library.write_bytes(b"archive-bytes")
            first = outer / "first.tar.gz"
            second = outer / "second.tar.gz"
            a = create_deterministic_archive(
                root=root,
                members=("include", "libnative.a"),
                output_path=first,
            )
            os.utime(header, (123456789, 123456789))
            os.utime(library, (987654321, 987654321))
            b = create_deterministic_archive(
                root=root,
                members=("libnative.a", "include"),
                output_path=second,
            )
            self.assertEqual(a.sha256, b.sha256)
            self.assertEqual(sha256_file(first), a.sha256)
            with tarfile.open(first, "r:gz") as archive:
                self.assertEqual(
                    ["include", "include/native.h", "libnative.a"],
                    [item.name.rstrip("/") for item in archive.getmembers()],
                )

    def test_archive_rejects_symlink_and_output_inside_root(self):
        with tempfile.TemporaryDirectory() as raw:
            outer = Path(raw).resolve()
            root = outer / "root"
            root.mkdir()
            outside = outer / "outside"
            outside.write_text("sentinel", encoding="utf-8")
            (root / "link").symlink_to(outside)
            with self.assertRaisesRegex(
                NativePrimitiveError,
                "archive_symlink_rejected",
            ):
                create_deterministic_archive(
                    root=root,
                    members=("link",),
                    output_path=outer / "out.tar.gz",
                )
            (root / "file").write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(
                NativePrimitiveError,
                "archive_output_inside_root",
            ):
                create_deterministic_archive(
                    root=root,
                    members=("file",),
                    output_path=root / "out.tar.gz",
                )

    def test_inspect_native_outputs_classifies_and_hashes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "lib").mkdir()
            static = root / "lib" / "libnative.a"
            shared = root / "lib" / "libnative.so.1"
            executable = root / "native-tool"
            static.write_bytes(b"static")
            shared.write_bytes(b"shared")
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            outputs = inspect_native_outputs(
                root=root,
                outputs=(
                    "lib/libnative.a",
                    "lib/libnative.so.1",
                    "native-tool",
                ),
            )
        self.assertEqual(
            ("static-library", "shared-library", "executable"),
            tuple(item.kind for item in outputs),
        )
        self.assertEqual(hashlib.sha256(b"static").hexdigest(), outputs[0].sha256)
        self.assertNotIn(str(root), repr(outputs))

    def test_inspection_rejects_missing_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            outside = root.parent / "outside-native-output"
            outside.write_text("outside", encoding="utf-8")
            try:
                (root / "link").symlink_to(outside)
                for value in ("missing.a", "../outside-native-output", "link"):
                    with self.assertRaises(NativePrimitiveError):
                        inspect_native_outputs(root=root, outputs=(value,))
            finally:
                outside.unlink(missing_ok=True)

    def test_cleanup_removes_only_state_beneath_explicit_root(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            build = root / "build"
            install = root / "install"
            build.mkdir()
            install.mkdir()
            (build / "state").write_text("x", encoding="utf-8")
            removed = cleanup_native_state(root=root, paths=(build, install))
            self.assertEqual(2, removed)
            self.assertFalse(build.exists())
            self.assertFalse(install.exists())
            with self.assertRaisesRegex(NativePrimitiveError, "native_cleanup_failed"):
                cleanup_native_state(root=root, paths=(root.parent / "escape",))

    def test_module_api_has_no_product_or_library_names(self):
        source = (
            Path(__file__)
            .parents[1]
            .joinpath("src/ci_workflows/native_primitives.py")
            .read_text(encoding="utf-8")
            .casefold()
        )
        for forbidden in ("ffmpeg", "vlc", "mpv", "streamscape"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
