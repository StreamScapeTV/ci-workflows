from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.ciw_native import configure_native, execute_native
from ci_workflows.ciw_types import CIWContext, CIWError
from ci_workflows.native_primitives import NativeArchive, NativeCommandResult, NativeOutput


class NativeCIWAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.project = self.workspace / "source"
        self.state = root / "state"
        self.project.mkdir(parents=True)
        self.state.mkdir()
        self.context = CIWContext(
            root=self.workspace,
            environment={
                "GITHUB_WORKSPACE": str(self.workspace),
                "CI_WORKFLOW_ROOT": str(self.state),
            },
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def args(**values: object) -> argparse.Namespace:
        defaults: dict[str, object] = {
            "phase": "cmake-configure",
            "project_root": "source",
            "source_directory": None,
            "state_directory": None,
            "install_directory": None,
            "configure_steps_json": None,
            "definitions_json": None,
            "options_json": None,
            "targets_json": None,
            "members_json": None,
            "outputs_json": None,
            "cleanup_paths_json": None,
            "generator": None,
            "target": None,
            "configuration": None,
            "component": None,
            "jobs": None,
            "archive_format": None,
            "archive_output": None,
            "cwd_scope": None,
        }
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_parser_exposes_named_native_phases_without_arbitrary_shell(self) -> None:
        parser = argparse.ArgumentParser()
        configure_native(parser)
        namespace = parser.parse_args(
            [
                "--phase",
                "cmake-configure",
                "--project-root",
                "source",
                "--definitions-json",
                '{"BUILD_TESTING":"ON"}',
            ]
        )
        self.assertEqual(namespace.phase, "cmake-configure")
        self.assertFalse(hasattr(namespace, "shell"))
        self.assertFalse(hasattr(namespace, "command"))

    def test_cmake_configure_delegates_to_primitive_with_bounded_state(self) -> None:
        expected = NativeCommandResult("cmake_configure", ("cmake",), str(self.project), 0)
        with patch("ci_workflows.ciw_native.cmake_configure", return_value=expected) as primitive:
            result = execute_native(
                self.args(
                    phase="cmake-configure",
                    source_directory=".",
                    state_directory="native/build",
                    definitions_json='{"BUILD_TESTING":"ON","MODE":"Release"}',
                    generator="Ninja",
                    options_json='["--fresh"]',
                ),
                self.context,
            )
        call = primitive.call_args.kwargs
        self.assertEqual(call["source_dir"], self.project.resolve())
        self.assertEqual(call["build_dir"], self.state / "native" / "build")
        self.assertEqual(call["definitions"], {"BUILD_TESTING": "ON", "MODE": "Release"})
        self.assertEqual(call["generator"], "Ninja")
        self.assertEqual(call["options"], ("--fresh",))
        self.assertEqual(result.outputs["result"], "success")
        self.assertIn('"operation":"cmake_configure"', result.outputs["native_result_json"])

    def test_make_delegates_explicit_targets_and_rejects_escape(self) -> None:
        expected = NativeCommandResult("make", ("make", "-j4", "all"), str(self.project), 0)
        with patch("ci_workflows.ciw_native.run_make", return_value=expected) as primitive:
            execute_native(
                self.args(
                    phase="make",
                    source_directory=".",
                    targets_json='["all","check"]',
                    options_json='["V=1"]',
                    jobs=4,
                ),
                self.context,
            )
        call = primitive.call_args.kwargs
        self.assertEqual(call["cwd"], self.project.resolve())
        self.assertEqual(call["targets"], ("all", "check"))
        self.assertEqual(call["jobs"], 4)
        with self.assertRaises(CIWError) as raised:
            execute_native(
                self.args(
                    phase="make",
                    source_directory="../outside",
                    targets_json='["all"]',
                    jobs=1,
                ),
                self.context,
            )
        self.assertEqual(raised.exception.code, "cwd_invalid")

    def test_archive_and_inspection_return_only_stable_metadata(self) -> None:
        install = self.state / "native" / "install"
        install.mkdir(parents=True)
        archive = NativeArchive(
            path=str(self.state / "native-output.tar.gz"),
            format="tar.gz",
            size_bytes=42,
            sha256="a" * 64,
            members=("lib/libsample.a",),
        )
        with patch("ci_workflows.ciw_native.create_deterministic_archive", return_value=archive) as primitive:
            result = execute_native(
                self.args(
                    phase="archive",
                    install_directory="native/install",
                    members_json='["lib/libsample.a"]',
                ),
                self.context,
            )
        self.assertEqual(primitive.call_args.kwargs["root"], install.resolve())
        self.assertNotIn(str(self.workspace), result.outputs["native_result_json"])
        output = NativeOutput("lib/libsample.a", "static-library", 12, "b" * 64, False)
        with patch("ci_workflows.ciw_native.inspect_native_outputs", return_value=(output,)):
            result = execute_native(
                self.args(
                    phase="inspect",
                    install_directory="native/install",
                    outputs_json='["lib/libsample.a"]',
                ),
                self.context,
            )
        self.assertIn('"kind":"static-library"', result.outputs["native_result_json"])
        self.assertIn('"sha256":"' + "b" * 64 + '"', result.outputs["native_result_json"])

    def test_cleanup_is_confined_to_existing_workflow_state_root(self) -> None:
        generated = self.state / "native" / "build"
        generated.mkdir(parents=True)
        with patch("ci_workflows.ciw_native.cleanup_native_state", return_value=1) as primitive:
            result = execute_native(
                self.args(phase="cleanup", cleanup_paths_json='["native/build"]'),
                self.context,
            )
        self.assertEqual(primitive.call_args.kwargs["root"], self.state.resolve())
        self.assertEqual(primitive.call_args.kwargs["paths"], (generated.resolve(),))
        self.assertIn('"removed_paths":1', result.outputs["native_result_json"])
        with self.assertRaises(CIWError):
            execute_native(
                self.args(phase="cleanup", cleanup_paths_json='["../workspace/source"]'),
                self.context,
            )


if __name__ == "__main__":
    unittest.main()
