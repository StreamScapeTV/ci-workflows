from __future__ import annotations

import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ci_workflows.validation_runtime as runtime_module
from ci_workflows.validation_runtime import (
    ValidationRuntimeError,
    install_locked_artifact,
)
from validation_runtime_fixtures import (
    MAC_ARM_RUNTIME,
    regular_member,
    source_bytes,
    typed_member,
    write_lock,
)


class SourceArchiveSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = self.root / "lock.json"
        self.target = self.root / "python"
        self.wheel = b"unused wheel payload"

    def assert_failure(self, payload: bytes, expression: str) -> None:
        write_lock(
            self.lock,
            wheel_payload=self.wheel,
            source_payload=payload,
        )
        with self.assertRaisesRegex(
            ValidationRuntimeError,
            expression,
        ):
            install_locked_artifact(
                self.lock,
                self.target,
                runtime=MAC_ARM_RUNTIME,
                downloader=lambda url: payload,
            )
        self.assertFalse(self.target.exists())

    def test_absolute_and_traversal_paths_are_rejected(self) -> None:
        for name in (
            "/absolute.py",
            "pyyaml-6.0.3/lib/yaml/../escape.py",
        ):
            with self.subTest(name=name):
                self.assert_failure(
                    source_bytes(
                        extra_members=[regular_member(name, b"escape\n")]
                    ),
                    "unsafe source archive member",
                )

    def test_symlink_and_hardlink_members_are_rejected(self) -> None:
        for member_type, expression in (
            (tarfile.SYMTYPE, "symlink"),
            (tarfile.LNKTYPE, "hardlink"),
        ):
            with self.subTest(expression=expression):
                self.assert_failure(
                    source_bytes(
                        extra_members=[
                            typed_member(
                                "pyyaml-6.0.3/lib/yaml/link.py",
                                member_type,
                                linkname=(
                                    "pyyaml-6.0.3/lib/yaml/__init__.py"
                                ),
                            )
                        ]
                    ),
                    expression,
                )

    def test_devices_fifos_and_unsupported_members_are_rejected(self) -> None:
        cases = (
            (tarfile.CHRTYPE, "device"),
            (tarfile.BLKTYPE, "device"),
            (tarfile.FIFOTYPE, "fifo"),
            (tarfile.CONTTYPE, "unsupported"),
            (tarfile.GNUTYPE_SPARSE, "unsupported"),
            (b"V", "unsupported"),
        )
        for member_type, expression in cases:
            with self.subTest(member_type=member_type):
                self.assert_failure(
                    source_bytes(
                        extra_members=[
                            typed_member(
                                "pyyaml-6.0.3/lib/yaml/special",
                                member_type,
                            )
                        ]
                    ),
                    expression,
                )

    def test_duplicate_target_paths_are_rejected(self) -> None:
        for name in (
            "pyyaml-6.0.3/lib/yaml/loader.py",
            "pyyaml-6.0.3/lib/yaml/Loader.py",
        ):
            with self.subTest(name=name):
                self.assert_failure(
                    source_bytes(
                        extra_members=[regular_member(name, b"duplicate\n")]
                    ),
                    "duplicate source destination",
                )

    def test_member_count_and_expanded_size_bounds_are_enforced(self) -> None:
        count_payload = source_bytes(
            extra_members=[
                regular_member("pyyaml-6.0.3/extra.txt", b"x")
            ]
        )
        write_lock(
            self.lock,
            wheel_payload=self.wheel,
            source_payload=count_payload,
        )
        with mock.patch.object(
            runtime_module,
            "MAX_ARCHIVE_MEMBERS",
            4,
        ), self.assertRaisesRegex(
            ValidationRuntimeError,
            "member-count",
        ):
            install_locked_artifact(
                self.lock,
                self.target,
                runtime=MAC_ARM_RUNTIME,
                downloader=lambda url: count_payload,
            )
        self.assertFalse(self.target.exists())

        size_payload = source_bytes()
        write_lock(
            self.lock,
            wheel_payload=self.wheel,
            source_payload=size_payload,
        )
        with mock.patch.object(
            runtime_module,
            "MAX_EXPANDED_BYTES",
            32,
        ), self.assertRaisesRegex(
            ValidationRuntimeError,
            "expanded-size",
        ):
            install_locked_artifact(
                self.lock,
                self.target,
                runtime=MAC_ARM_RUNTIME,
                downloader=lambda url: size_payload,
            )
        self.assertFalse(self.target.exists())

    def test_missing_yaml_package_is_rejected(self) -> None:
        self.assert_failure(
            source_bytes(include_yaml=False),
            "yaml/__init__.py",
        )


if __name__ == "__main__":
    unittest.main()
