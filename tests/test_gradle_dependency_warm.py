from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.ciw_types import CIWError
from ci_workflows.gradle_dependency_warm import warm_gradle_dependencies
from ci_workflows.runtime_primitives import ProcessResult

SHA = "a" * 40


class GradleDependencyWarmTests(unittest.TestCase):
    def _environment(self, root: Path) -> dict[str, str]:
        workspace = root / "workspace"
        source = workspace / "source"
        state = root / "state"
        for path in (
            source,
            state / "home",
            state / "gradle",
            state / "tmp",
            root / "jdk",
            root / "sdk",
        ):
            path.mkdir(parents=True, exist_ok=True)
        (source / ".git").mkdir()
        return {
            "GITHUB_WORKSPACE": str(workspace),
            "CI_WORKFLOW_STATE_ID": "state",
            "CI_WORKFLOW_ROOT": str(state),
            "PATH": "/reviewed/bin",
            "HOME": str(state / "home"),
            "GRADLE_USER_HOME": str(state / "gradle"),
            "TMPDIR": str(state / "tmp"),
            "JAVA_HOME": str(root / "jdk"),
            "ANDROID_SDK_ROOT": str(root / "sdk"),
            "GITHUB_TOKEN": "must-not-reach-gradle",
            "PRIVATE_DEPENDENCY_TOKEN": "must-not-reach-gradle",
        }

    @staticmethod
    def _copy_with_wrapper(source: Path, destination: Path) -> None:
        _ = source
        destination.mkdir(parents=True)
        wrapper = destination / "gradlew"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)

    def test_warm_resolves_all_configurations_without_product_task_and_cleans_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = self._environment(root)
            with (
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._verify_exact_source"
                ) as verify,
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._copy_source",
                    side_effect=self._copy_with_wrapper,
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm.run_process",
                    return_value=ProcessResult(0, "", "", False),
                ) as process,
            ):
                result = warm_gradle_dependencies(
                    admitted_sha=SHA,
                    working_directory=".",
                    gradle_wrapper_path="gradlew",
                    private_dependency_subdirectory=".",
                    environment=environment,
                )

            self.assertEqual(result.source_sha, SHA)
            self.assertEqual(result.cache_mode, "cold")
            self.assertGreaterEqual(result.wall_ms, 0)
            self.assertEqual(verify.call_count, 2)
            process.assert_called_once()
            argv = process.call_args.args[0]
            self.assertEqual(
                argv[1:],
                ("--no-daemon", "--write-verification-metadata", "sha256"),
            )
            self.assertNotIn("build", argv)
            self.assertNotIn("test", argv)
            runtime = process.call_args.kwargs["environment"]
            self.assertEqual(runtime["GRADLE_USER_HOME"], environment["GRADLE_USER_HOME"])
            self.assertNotIn("GITHUB_TOKEN", runtime)
            self.assertNotIn("PRIVATE_DEPENDENCY_TOKEN", runtime)
            self.assertFalse(
                (root / "state/tmp/gradle-dependency-warm-source").exists()
            )

    def test_warm_propagates_fixed_read_only_cache_and_verified_dependency_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = self._environment(root)
            cache = Path("/opt/gradle-ro-cache")
            dependency = root / "state/dependencies/media"
            dependency.mkdir(parents=True)
            environment["CI_PRIVATE_DEPENDENCY_PATH"] = str(dependency)
            with (
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._verify_exact_source"
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._copy_source",
                    side_effect=self._copy_with_wrapper,
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._read_only_cache",
                    return_value=cache,
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm.run_process",
                    return_value=ProcessResult(0, "", "", False),
                ) as process,
            ):
                result = warm_gradle_dependencies(
                    admitted_sha=SHA,
                    working_directory=".",
                    gradle_wrapper_path="gradlew",
                    private_dependency_subdirectory=".",
                    environment=environment,
                )

            self.assertEqual(result.cache_mode, "read-only-seed")
            runtime = process.call_args.kwargs["environment"]
            self.assertEqual(runtime["GRADLE_RO_DEP_CACHE"], str(cache))
            self.assertEqual(runtime["CI_PRIVATE_DEPENDENCY_PATH"], str(dependency))

    def test_warm_exposes_only_verified_private_dependency_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = self._environment(root)
            dependency_root = root / "state/dependencies/media"
            dependency_android = dependency_root / "android"
            dependency_android.mkdir(parents=True)
            environment["CI_PRIVATE_DEPENDENCY_PATH"] = str(dependency_root)
            with (
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._verify_exact_source"
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._copy_source",
                    side_effect=self._copy_with_wrapper,
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm.run_process",
                    return_value=ProcessResult(0, "", "", False),
                ) as process,
            ):
                warm_gradle_dependencies(
                    admitted_sha=SHA,
                    working_directory=".",
                    gradle_wrapper_path="gradlew",
                    private_dependency_subdirectory="android",
                    environment=environment,
                )

            runtime = process.call_args.kwargs["environment"]
            self.assertEqual(
                runtime["CI_PRIVATE_DEPENDENCY_PATH"],
                str(dependency_android),
            )

    def test_private_dependency_subdirectory_traversal_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = self._environment(root)
            dependency = root / "state/dependencies/media"
            dependency.mkdir(parents=True)
            environment["CI_PRIVATE_DEPENDENCY_PATH"] = str(dependency)
            with self.assertRaises(CIWError) as failure:
                warm_gradle_dependencies(
                    admitted_sha=SHA,
                    working_directory=".",
                    gradle_wrapper_path="gradlew",
                    private_dependency_subdirectory="../android",
                    environment=environment,
                )
            self.assertEqual(
                failure.exception.code,
                "private_dependency_subdirectory_invalid",
            )

    def test_arbitrary_read_only_cache_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = self._environment(root)
            arbitrary = root / "shared-cache"
            arbitrary.mkdir()
            environment["GRADLE_RO_DEP_CACHE"] = str(arbitrary)
            with (
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._verify_exact_source"
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._copy_source",
                    side_effect=self._copy_with_wrapper,
                ),
            ):
                with self.assertRaises(CIWError) as failure:
                    warm_gradle_dependencies(
                        admitted_sha=SHA,
                        working_directory=".",
                        gradle_wrapper_path="gradlew",
                        private_dependency_subdirectory=".",
                        environment=environment,
                    )
            self.assertEqual(failure.exception.code, "gradle_read_only_cache_invalid")
            self.assertFalse(
                (root / "state/tmp/gradle-dependency-warm-source").exists()
            )

    def test_failed_resolution_is_blocking_but_cleanup_still_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = self._environment(root)
            with (
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._verify_exact_source"
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm._copy_source",
                    side_effect=self._copy_with_wrapper,
                ),
                mock.patch(
                    "ci_workflows.gradle_dependency_warm.run_process",
                    return_value=ProcessResult(1, "", "missing dependency", False),
                ),
            ):
                with self.assertRaises(CIWError) as failure:
                    warm_gradle_dependencies(
                        admitted_sha=SHA,
                        working_directory=".",
                        gradle_wrapper_path="gradlew",
                        private_dependency_subdirectory=".",
                        environment=environment,
                    )
            self.assertEqual(failure.exception.code, "dependency_warm_failed")
            self.assertFalse(
                (root / "state/tmp/gradle-dependency-warm-source").exists()
            )


if __name__ == "__main__":
    unittest.main()
