from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import ciw_native, native_primitives


SHA = "a" * 40


def _result(operation: str, cwd: Path) -> native_primitives.NativeCommandResult:
    return native_primitives.NativeCommandResult(operation, (), str(cwd), 0)


class NativeAdapterTests(unittest.TestCase):
    def environment(self, *, plan: object | None = None, **overrides: str) -> dict[str, str]:
        validation_plan = {
            "definitions": {"BUILD_TESTING": "ON"},
            "configure_options": ["-Wno-dev"],
            "test_target": "test",
            "jobs": 2,
        } if plan is None else plan
        values = {
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_VALIDATION_PLAN_JSON": json.dumps(validation_plan, separators=(",", ":")),
        }
        values.update(overrides)
        return values

    def test_representative_cmake_flow_reuses_one_isolated_build_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = workspace / "source"
            source.mkdir(parents=True)
            state_temp = root / "state" / "tmp"
            state_temp.mkdir(parents=True)
            calls: list[tuple[str, Path, object]] = []

            def configure(**kwargs):
                build_dir = Path(kwargs["build_dir"])
                build_dir.mkdir()
                calls.append(("configure", build_dir, kwargs))
                return _result("cmake_configure", Path(kwargs["source_dir"]))

            def build(**kwargs):
                build_dir = Path(kwargs["build_dir"])
                calls.append(("build", build_dir, kwargs))
                return _result("cmake_build", build_dir)

            with patch.object(ciw_native, "_state_temp", return_value=state_temp):
                outputs = ciw_native.execute_native_validate(
                    contract_root=root,
                    workspace=workspace,
                    environment=self.environment(),
                    configure=configure,
                    build=build,
                )

            self.assertEqual(outputs["result"], "success")
            self.assertEqual(outputs["source_sha"], SHA)
            self.assertEqual(json.loads(outputs["test_summary"])["test"], "success")
            self.assertEqual([item[0] for item in calls], ["configure", "build", "build"])
            build_dirs = {item[1] for item in calls}
            self.assertEqual(build_dirs, {state_temp / "native-cmake-build"})
            self.assertEqual(calls[1][2]["target"], "")
            self.assertEqual(calls[2][2]["target"], "test")
            self.assertEqual(calls[0][2]["definitions"], {"BUILD_TESTING": "ON"})
            self.assertEqual(calls[0][2]["options"], ("-Wno-dev",))
            self.assertEqual(calls[1][2]["jobs"], 2)

    def test_subdirectory_is_bounded_inside_exact_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            project = workspace / "source" / "native" / "lib"
            project.mkdir(parents=True)
            state_temp = root / "state" / "tmp"
            state_temp.mkdir(parents=True)
            seen: list[Path] = []

            def configure(**kwargs):
                seen.append(Path(kwargs["source_dir"]))
                Path(kwargs["build_dir"]).mkdir()
                return _result("cmake_configure", Path(kwargs["source_dir"]))

            def build(**kwargs):
                return _result("cmake_build", Path(kwargs["build_dir"]))

            with patch.object(ciw_native, "_state_temp", return_value=state_temp):
                ciw_native.execute_native_validate(
                    contract_root=root,
                    workspace=workspace,
                    environment=self.environment(INPUT_WORKING_DIRECTORY="native/lib"),
                    configure=configure,
                    build=build,
                )
            self.assertEqual(seen, [project.resolve()])

    def test_invalid_functional_inputs_fail_closed_before_execution(self) -> None:
        invalid = (
            ("invalid_admitted_sha", self.environment(INPUT_ADMITTED_SHA="HEAD")),
            ("invalid_working_directory", self.environment(INPUT_WORKING_DIRECTORY="../escape")),
            ("invalid_validation_plan", self.environment(INPUT_VALIDATION_PLAN_JSON="[]")),
            ("invalid_validation_plan", self.environment(plan={"unknown": True})),
            ("invalid_cmake_definitions", self.environment(plan={"definitions": []})),
            ("invalid_configure_options", self.environment(plan={"configure_options": {}})),
            ("invalid_build_options", self.environment(plan={"build_options": [1]})),
            ("invalid_test_target", self.environment(plan={"test_target": "bad\ntarget"})),
            ("invalid_jobs", self.environment(plan={"jobs": 0})),
            ("invalid_jobs", self.environment(plan={"jobs": "2"})),
        )
        for code, environment in invalid:
            with self.subTest(code=code, environment=environment):
                with self.assertRaisesRegex(ciw_native.NativeValidationError, f"^{code}$"):
                    ciw_native.request_from_environment(environment)

    def test_native_build_output_never_targets_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = workspace / "source"
            source.mkdir(parents=True)
            state_temp = root / "state" / "tmp"
            state_temp.mkdir(parents=True)
            seen_build_dirs: list[Path] = []

            def configure(**kwargs):
                build_dir = Path(kwargs["build_dir"])
                build_dir.mkdir()
                seen_build_dirs.append(build_dir)
                return _result("cmake_configure", source)

            def build(**kwargs):
                seen_build_dirs.append(Path(kwargs["build_dir"]))
                return _result("cmake_build", Path(kwargs["build_dir"]))

            with patch.object(ciw_native, "_state_temp", return_value=state_temp):
                ciw_native.execute_native_validate(
                    contract_root=root,
                    workspace=workspace,
                    environment=self.environment(),
                    configure=configure,
                    build=build,
                )

            for build_dir in seen_build_dirs:
                self.assertTrue(build_dir.is_relative_to(state_temp))
                self.assertFalse(build_dir.is_relative_to(source))


if __name__ == "__main__":
    unittest.main()
