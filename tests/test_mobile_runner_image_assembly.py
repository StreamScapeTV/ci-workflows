from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from ci_workflows.runner_images import RunnerImageError, build_plan, validate_release_tag

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER_PATH = ROOT / "runner-images/mobile/assemble.py"
DOCKERFILE_PATH = ROOT / "runner-images/mobile/Dockerfile"


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


def _docker_instruction_containing(source: str, marker: str) -> str:
    marker_index = source.index(marker)
    instructions = list(
        re.finditer(
            r"(?m)^(?:ARG|CMD|COPY|ENV|FROM|RUN|USER|WORKDIR)\b.*$",
            source,
        )
    )
    for index, instruction in enumerate(instructions):
        end = instructions[index + 1].start() if index + 1 < len(instructions) else len(source)
        if instruction.start() <= marker_index < end:
            return source[instruction.start() : end]
    raise AssertionError(f"marker is not inside a Dockerfile instruction: {marker}")


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

    def test_component_entrypoint_is_fixed_and_rejects_unknown_components(self) -> None:
        self.assertEqual(
            (
                "flutter",
                "cmdline-tools",
                "platform-tools",
                "platform-36",
                "platform-37",
                "build-tools-36",
                "build-tools-37",
                "ndk",
            ),
            self.assembler.COMPONENTS,
        )
        with self.assertRaisesRegex(SystemExit, "unsupported Mobile component"):
            self.assembler.main(["unknown"])


class MobileRunnerDockerfileLayerTests(unittest.TestCase):
    def test_transient_tool_archives_are_assembled_and_removed_sequentially_in_one_run(self) -> None:
        source = DOCKERFILE_PATH.read_text(encoding="utf-8")
        sequence = (
            (
                'download "${FLUTTER_URL}" "${FLUTTER_SHA256}" /tmp/flutter.tar.xz;',
                "/usr/local/bin/runner-mobile-assemble flutter;",
                "rm -f /tmp/flutter.tar.xz;",
            ),
            (
                'download "${ANDROID_CMDLINE_TOOLS_URL}" "${ANDROID_CMDLINE_TOOLS_SHA256}" /tmp/android-command-line-tools.zip;',
                "/usr/local/bin/runner-mobile-assemble cmdline-tools;",
                "rm -f /tmp/android-command-line-tools.zip;",
            ),
            (
                'download "${ANDROID_PLATFORM_TOOLS_URL}" "${ANDROID_PLATFORM_TOOLS_SHA256}" /tmp/android-platform-tools.zip;',
                "/usr/local/bin/runner-mobile-assemble platform-tools;",
                "rm -f /tmp/android-platform-tools.zip;",
            ),
            (
                'download "${ANDROID_PLATFORM_36_URL}" "${ANDROID_PLATFORM_36_SHA256}" /tmp/android-platform-36.zip;',
                "/usr/local/bin/runner-mobile-assemble platform-36;",
                "rm -f /tmp/android-platform-36.zip;",
            ),
            (
                'download "${ANDROID_PLATFORM_37_URL}" "${ANDROID_PLATFORM_37_SHA256}" /tmp/android-platform-37.zip;',
                "/usr/local/bin/runner-mobile-assemble platform-37;",
                "rm -f /tmp/android-platform-37.zip;",
            ),
            (
                'download "${ANDROID_BUILD_TOOLS_36_URL}" "${ANDROID_BUILD_TOOLS_36_SHA256}" /tmp/android-build-tools-36.zip;',
                "/usr/local/bin/runner-mobile-assemble build-tools-36;",
                "rm -f /tmp/android-build-tools-36.zip;",
            ),
            (
                'download "${ANDROID_BUILD_TOOLS_37_URL}" "${ANDROID_BUILD_TOOLS_37_SHA256}" /tmp/android-build-tools-37.zip;',
                "/usr/local/bin/runner-mobile-assemble build-tools-37;",
                "rm -f /tmp/android-build-tools-37.zip;",
            ),
            (
                'download "${ANDROID_NDK_URL}" "${ANDROID_NDK_SHA256}" /tmp/android-ndk.zip;',
                "/usr/local/bin/runner-mobile-assemble ndk;",
                "rm -f /tmp/android-ndk.zip;",
            ),
        )
        instruction = _docker_instruction_containing(source, sequence[0][0])
        self.assertTrue(instruction.startswith("RUN set -eux;"))
        self.assertLess(
            source.index("COPY --chmod=0755 assemble.py /usr/local/bin/runner-mobile-assemble"),
            source.index(sequence[0][0]),
        )

        previous_cleanup = -1
        for download_marker, assemble_marker, cleanup_marker in sequence:
            with self.subTest(component=assemble_marker):
                self.assertEqual(
                    instruction,
                    _docker_instruction_containing(source, download_marker),
                )
                self.assertEqual(
                    instruction,
                    _docker_instruction_containing(source, assemble_marker),
                )
                self.assertEqual(
                    instruction,
                    _docker_instruction_containing(source, cleanup_marker),
                )
                download_index = instruction.index(download_marker)
                assemble_index = instruction.index(assemble_marker)
                cleanup_index = instruction.index(cleanup_marker)
                self.assertLess(previous_cleanup, download_index)
                self.assertLess(download_index, assemble_index)
                self.assertLess(assemble_index, cleanup_index)
                previous_cleanup = cleanup_index

    def test_large_downloads_retry_transport_errors_without_http2(self) -> None:
        source = DOCKERFILE_PATH.read_text(encoding="utf-8")
        runner_download = _docker_instruction_containing(source, "${ACTIONS_RUNNER_URL}")
        tool_download = _docker_instruction_containing(
            source,
            'download "${FLUTTER_URL}" "${FLUTTER_SHA256}" /tmp/flutter.tar.xz',
        )
        for instruction in (runner_download, tool_download):
            with self.subTest(instruction=instruction[:80]):
                self.assertIn("--http1.1", instruction)
                self.assertIn("--retry 5", instruction)
                self.assertIn("--retry-all-errors", instruction)
                self.assertIn("--retry-delay 1", instruction)
                self.assertIn("--proto '=https'", instruction)
                self.assertIn("--tlsv1.2", instruction)


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

    def test_latest_remains_forbidden_as_repository_release_tag(self) -> None:
        with self.assertRaises(RunnerImageError):
            validate_release_tag("latest")


if __name__ == "__main__":
    unittest.main()
