from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import gradle_seed


def write_module_file(root: Path, relative: str, payload: bytes) -> Path:
    target = root / "caches" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def parse_stream(payload: bytes) -> list[tuple[dict[str, object], bytes]]:
    if not payload.startswith(gradle_seed.MAGIC):
        raise AssertionError("missing magic")
    offset = len(gradle_seed.MAGIC)
    frames: list[tuple[dict[str, object], bytes]] = []
    while True:
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        offset += 4
        if length == 0:
            break
        header = json.loads(payload[offset : offset + length].decode("ascii"))
        offset += length
        size = int(header["size"])
        content = payload[offset : offset + size]
        offset += size
        frames.append((header, content))
    if offset != len(payload):
        raise AssertionError("trailing bytes")
    return frames


class GradleSeedSelectionTests(unittest.TestCase):
    def test_selects_only_modules_delta_and_frames_sorted_sha256_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            alpha = write_module_file(
                home,
                "modules-2/files-2.1/z/example.jar",
                b"jar-data",
            )
            beta = write_module_file(
                home,
                "modules-2/metadata-2.106/a.bin",
                b"metadata",
            )
            write_module_file(home, "modules-2/gc.properties", b"mutable")
            write_module_file(home, "modules-2/cache.lock", b"mutable")
            write_module_file(home, "transforms-4/ignored.bin", b"ignored")
            (home / "daemon").mkdir()
            (home / "daemon" / "state.bin").write_bytes(b"ignored")
            (home / "configuration-cache").mkdir()
            (home / "configuration-cache" / "state.bin").write_bytes(b"ignored")

            files = gradle_seed.collect_seed_files(home)
            self.assertEqual(
                [
                    "modules-2/files-2.1/z/example.jar",
                    "modules-2/metadata-2.106/a.bin",
                ],
                [item.relative_path for item in files],
            )
            self.assertEqual(
                hashlib.sha256(alpha.read_bytes()).hexdigest(),
                files[0].sha256,
            )
            self.assertEqual(
                hashlib.sha256(beta.read_bytes()).hexdigest(),
                files[1].sha256,
            )

            frames = parse_stream(b"".join(gradle_seed.framed_seed_stream(files)))
            self.assertEqual(
                [item.relative_path for item in files],
                [str(header["path"]) for header, _content in frames],
            )
            for seed_file, (header, content) in zip(files, frames, strict=True):
                self.assertEqual(seed_file.size, header["size"])
                self.assertEqual(seed_file.sha256, header["sha256"])
                self.assertEqual(
                    (home / "caches" / seed_file.relative_path).read_bytes(),
                    content,
                )

    def test_symlink_and_unsupported_entries_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            modules = home / "caches" / "modules-2"
            modules.mkdir(parents=True)
            outside = home / "outside"
            outside.write_text("secret", encoding="utf-8")
            os.symlink(outside, modules / "linked")
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_symlink_rejected",
            ):
                gradle_seed.collect_seed_files(home)

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                modules = home / "caches" / "modules-2"
                modules.mkdir(parents=True)
                os.mkfifo(modules / "pipe")
                with self.assertRaisesRegex(
                    gradle_seed.GradleSeedError,
                    "gradle_seed_entry_unsupported",
                ):
                    gradle_seed.collect_seed_files(home)

    def test_top_level_modules_symlink_and_relative_home_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            caches = home / "caches"
            caches.mkdir()
            real = home / "real-modules"
            real.mkdir()
            os.symlink(real, caches / "modules-2")
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_path_rejected",
            ):
                gradle_seed.collect_seed_files(home)
        with self.assertRaisesRegex(
            gradle_seed.GradleSeedError,
            "gradle_seed_home_rejected",
        ):
            gradle_seed.collect_seed_files(Path("relative"))

    def test_four_gib_boundary_is_enforced_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            write_module_file(home, "modules-2/a.bin", b"1234")
            with (
                mock.patch.object(gradle_seed, "MAX_UPLOAD_BYTES", 3),
                self.assertRaisesRegex(
                    gradle_seed.GradleSeedError,
                    "gradle_seed_payload_too_large",
                ),
            ):
                gradle_seed.collect_seed_files(home)

    def test_file_change_after_collection_aborts_before_terminator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = write_module_file(home, "modules-2/a.bin", b"aaaa")
            files = gradle_seed.collect_seed_files(home)
            target.write_bytes(b"bbbb")
            with self.assertRaisesRegex(
                gradle_seed.GradleSeedError,
                "gradle_seed_file_changed",
            ):
                b"".join(gradle_seed.framed_seed_stream(files))

    def test_portable_framing_module_contains_no_github_identity_transport(self) -> None:
        source = Path(gradle_seed.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "GithubOidcRequester",
            "FluxSeedUploader",
            "promote_gradle_seed",
            "ACTIONS_ID_TOKEN",
            "OIDC_AUDIENCE",
            "Authorization",
            "actions.githubusercontent.com",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
