from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_workflows.validation_runtime import (
    ValidationRuntimeError,
    detect_runtime,
    install_locked_artifact,
    select_artifact,
    select_wheel,
)
from validation_runtime_fixtures import (
    LINUX_RUNTIME,
    MAC_ARM_RUNTIME,
    MAC_X64_RUNTIME,
    SOURCE_FILENAME,
    SOURCE_URL,
    WHEEL_FILENAME,
    source_bytes,
    write_lock,
)


class MacOSValidationRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = self.root / "lock.json"
        self.target = self.root / "python"
        self.wheel = b"unused wheel payload"
        self.source = source_bytes()
        write_lock(
            self.lock,
            wheel_payload=self.wheel,
            source_payload=self.source,
        )

    def test_linux_wheel_and_macos_source_selection(self) -> None:
        wheel = select_wheel(self.lock, LINUX_RUNTIME)
        self.assertEqual(WHEEL_FILENAME, wheel.filename)
        self.assertEqual("wheel", wheel.format)
        for selected in (MAC_ARM_RUNTIME, MAC_X64_RUNTIME):
            with self.subTest(runtime=selected):
                source = select_artifact(self.lock, selected)
                self.assertEqual(SOURCE_FILENAME, source.filename)
                self.assertEqual("sdist-tar-gz", source.format)

    def test_linux_detection_remains_cpython_312_x86_64(self) -> None:
        self.assertEqual(
            LINUX_RUNTIME,
            detect_runtime(
                implementation="cpython",
                system="Linux",
                machine="amd64",
                version=(3, 12, 10),
            ),
        )

    def test_macos_source_installs_only_pure_python_yaml(self) -> None:
        install_locked_artifact(
            self.lock,
            self.target,
            runtime=MAC_ARM_RUNTIME,
            downloader=lambda url: self.source,
        )
        self.assertTrue((self.target / "yaml/__init__.py").is_file())
        self.assertTrue((self.target / "yaml/loader.py").is_file())
        self.assertFalse((self.target / "setup.py").exists())
        self.assertEqual(
            {"__init__.py", "loader.py"},
            {path.name for path in (self.target / "yaml").iterdir()},
        )

    def test_supported_darwin_architecture_normalization(self) -> None:
        cases = {
            "arm64": MAC_ARM_RUNTIME,
            "aarch64": MAC_ARM_RUNTIME,
            "x86_64": MAC_X64_RUNTIME,
            "amd64": MAC_X64_RUNTIME,
            "x64": MAC_X64_RUNTIME,
        }
        for machine, expected in cases.items():
            with self.subTest(machine=machine):
                self.assertEqual(
                    expected,
                    detect_runtime(
                        implementation="cpython",
                        system="Darwin",
                        machine=machine,
                        version=(3, 13, 5),
                    ),
                )

    def test_unsupported_host_properties_are_rejected(self) -> None:
        cases = (
            ("pypy", "Darwin", "arm64", (3, 13, 5), "implementation"),
            ("cpython", "Windows", "x86_64", (3, 13, 5), "operating system"),
            ("cpython", "Darwin", "ppc64", (3, 13, 5), "architecture"),
            ("cpython", "Darwin", "arm64", (3, 13, 4), "Python version"),
            ("cpython", "Darwin", "arm64", (3, 13, 6), "Python version"),
            ("cpython", "Darwin", "arm64", (3, 12, 10), "Python version"),
            ("cpython", "Linux", "arm64", (3, 12, 10), "architecture"),
            ("cpython", "Linux", "x86_64", (3, 13, 5), "Python version"),
        )
        for implementation, system, machine, version, expression in cases:
            with self.subTest(expression=expression, version=version):
                with self.assertRaisesRegex(
                    ValidationRuntimeError,
                    expression,
                ):
                    detect_runtime(
                        implementation=implementation,
                        system=system,
                        machine=machine,
                        version=version,
                    )

    def test_digest_mismatch_is_rejected(self) -> None:
        write_lock(
            self.lock,
            wheel_payload=self.wheel,
            source_payload=self.source,
            source_digest="0" * 64,
        )
        with self.assertRaisesRegex(ValidationRuntimeError, "digest mismatch"):
            install_locked_artifact(
                self.lock,
                self.target,
                runtime=MAC_ARM_RUNTIME,
                downloader=lambda url: self.source,
            )

    def test_wrong_host_filename_and_package_version_are_rejected(self) -> None:
        cases = (
            (f"https://example.invalid/{SOURCE_FILENAME}", SOURCE_FILENAME),
            (
                "https://files.pythonhosted.org/packages/example/other.tar.gz",
                SOURCE_FILENAME,
            ),
            (
                "https://files.pythonhosted.org/packages/example/other.tar.gz",
                "other.tar.gz",
            ),
        )
        for url, filename in cases:
            with self.subTest(url=url, filename=filename):
                write_lock(
                    self.lock,
                    wheel_payload=self.wheel,
                    source_payload=self.source,
                    source_filename=filename,
                    source_url=url,
                )
                with self.assertRaises(ValidationRuntimeError):
                    select_artifact(self.lock, MAC_ARM_RUNTIME)

        mismatched = source_bytes(
            root_version="6.0.4",
            metadata_version="6.0.3",
        )
        write_lock(
            self.lock,
            wheel_payload=self.wheel,
            source_payload=mismatched,
            package_version="6.0.4",
            source_filename="pyyaml-6.0.4.tar.gz",
            source_url=SOURCE_URL.replace("6.0.3", "6.0.4"),
        )
        with self.assertRaisesRegex(
            ValidationRuntimeError,
            "package version",
        ):
            install_locked_artifact(
                self.lock,
                self.target,
                runtime=MAC_ARM_RUNTIME,
                downloader=lambda url: mismatched,
            )


if __name__ == "__main__":
    unittest.main()
