from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import gradle_seed


def write_seed(root: Path, relative: str, payload: bytes) -> Path:
    target = root / "caches" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


class GradleSeedFilesystemSecurityTests(unittest.TestCase):
    def test_external_file_hard_link_is_rejected_during_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "gradle"
            modules = home / "caches" / "modules-2"
            modules.mkdir(parents=True)
            outside = Path(directory) / "outside.bin"
            outside.write_bytes(b"outside-secret")
            os.link(outside, modules / "aliased.bin")

            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_hardlink_rejected",
            ):
                gradle_seed.collect_seed_files(home)

    def test_hard_link_added_after_collection_is_rejected_before_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "gradle"
            selected = write_seed(home, "modules-2/a.bin", b"seed")
            files = gradle_seed.collect_seed_files(home)
            os.link(selected, Path(directory) / "late-alias.bin")

            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_hardlink_rejected",
            ):
                b"".join(gradle_seed.framed_seed_stream(files))

    def test_directory_symlink_swap_between_stat_and_open_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "gradle"
            nested = home / "caches" / "modules-2" / "nested"
            nested.mkdir(parents=True)
            (nested / "seed.bin").write_bytes(b"seed")
            external = Path(directory) / "external"
            external.mkdir()
            (external / "outside.bin").write_bytes(b"outside-secret")
            saved = Path(directory) / "saved-nested"

            original = gradle_seed._open_child_directory
            swapped = False

            def swap_then_open(
                parent_fd,
                name,
                *,
                expected=None,
                code="gradle_seed_path_rejected",
            ):
                nonlocal swapped
                if name == "nested" and expected is not None and not swapped:
                    nested.rename(saved)
                    os.symlink(external, nested)
                    swapped = True
                return original(parent_fd, name, expected=expected, code=code)

            with (
                mock.patch.object(
                    gradle_seed,
                    "_open_child_directory",
                    side_effect=swap_then_open,
                ),
                self.assertRaisesRegex(
                    gradle_seed.GradleSeedError,
                    "gradle_seed_path_rejected",
                ),
            ):
                gradle_seed.collect_seed_files(home)
            self.assertTrue(swapped)

    def test_each_absolute_gradle_home_component_is_nofollow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            real_root = Path(directory) / "real"
            home = real_root / "gradle"
            write_seed(home, "modules-2/a.bin", b"seed")
            alias_root = Path(directory) / "alias"
            os.symlink(real_root, alias_root)

            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_home_rejected",
            ):
                gradle_seed.collect_seed_files(alias_root / "gradle")

    def test_response_counts_require_json_integers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "gradle"
            write_seed(home, "modules-2/a.bin", b"seed")
            files = gradle_seed.collect_seed_files(home)
            response = gradle_seed.UploadResponse(
                status=200,
                content_type="application/json",
                body=(
                    b'{"status":"promoted","sourceSha":"'
                    + b"a" * 40
                    + b'","generation":"sha256-'
                    + b"b" * 64
                    + b'","fileCount":1.0,"totalBytes":4.0}'
                ),
            )
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_response_counts_mismatch",
            ):
                gradle_seed._validated_response(
                    response,
                    source_sha="a" * 40,
                    files=files,
                )


if __name__ == "__main__":
    unittest.main()
