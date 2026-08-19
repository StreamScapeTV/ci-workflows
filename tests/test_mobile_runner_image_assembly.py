from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from ci_workflows.runner_images import RunnerImageError, build_plan, validate_release_tag

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER_PATH = ROOT / "runner-images/mobile/assemble.py"


def _load_assembler():
    spec = importlib.util.spec_from_file_location("runner_mobile_assemble", ASSEMBLER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load Mobile runner assembler")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    return info


class MobileRunnerArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assembler = _load_assembler()

    def test_safe_relative_symlink_survives_extract_and_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "ndk.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    _zip_info(
                        "android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang-19",
                        stat.S_IFREG | 0o755,
                    ),
                    "#!/bin/sh\nprintf 'clang version 19\\n'\n",
                )
                archive.writestr(
                    _zip_info(
                        "android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang",
                        stat.S_IFLNK | 0o777,
                    ),
                    "clang-19",
                )

            extracted = self.assembler.extract_zip(archive_path, root / "extract")
            source = extracted / "android-ndk"
            destination = root / "copied-ndk"
            self.assembler.copy_tree(source, destination)

            clang = destination / "toolchains/llvm/prebuilt/linux-x86_64/bin/clang"
            self.assertTrue(clang.is_symlink())
            self.assertEqual(os.readlink(clang), "clang-19")
            result = subprocess.run(
                [str(clang), "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout.strip(), "clang version 19")

    def test_symlink_that_escapes_extract_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    _zip_info("ndk/bin/clang", stat.S_IFLNK | 0o777),
                    "../../../outside",
                )

            with self.assertRaisesRegex(SystemExit, "unsafe archive symlink"):
                self.assembler.extract_zip(archive_path, root / "extract")


class RunnerImageReleaseTagContractTests(unittest.TestCase):
    def test_every_valid_repository_tag_is_used_verbatim_as_oci_tag(self) -> None:
        source_sha = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = root / "runner-images/general"
            context.mkdir(parents=True)
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (context / "smoke.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            for tag in ("1.0.1", "runner-images-2026.08.19-hotfix"):
                with self.subTest(tag=tag):
                    plan = build_plan(
                        root,
                        image_id="general",
                        source_sha=source_sha,
                        release_tag=tag,
                    )
                    self.assertEqual(
                        plan.remote_reference,
                        f"git.faruqi.dev/mimranfaruqi/github-actions-runner-general:{tag}",
                    )

    def test_release_workflow_accepts_any_repository_git_tag(self) -> None:
        release = (ROOT / ".github/workflows/runner-images-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('      - "*"', release)
        self.assertIn(
            "release_tag: ${{ needs.resolve.outputs.release_tag }}",
            release,
        )

    def test_latest_remains_forbidden_by_current_release_policy(self) -> None:
        with self.assertRaises(RunnerImageError):
            validate_release_tag("latest")


if __name__ == "__main__":
    unittest.main()
