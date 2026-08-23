from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_workflows.ciw_types import CIWError
from ci_workflows.gradle_dependency_warm import _runtime_environment


class GradleDependencyWarmHostedToolchainTests(unittest.TestCase):
    @staticmethod
    def _base_environment(root: Path) -> tuple[dict[str, str], Path]:
        state = root / "state"
        for path in (state / "home", state / "gradle", state / "tmp"):
            path.mkdir(parents=True, exist_ok=True)
        return (
            {
                "PATH": "/reviewed/bin",
                "HOME": str(state / "home"),
                "GRADLE_USER_HOME": str(state / "gradle"),
                "TMPDIR": str(state / "tmp"),
            },
            state,
        )

    def test_runner_owned_java_and_android_symlinks_resolve_to_real_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment, state = self._base_environment(root)
            java_real = root / "hostedtoolcache/java-25"
            sdk_real = root / "hostedtoolcache/android-sdk"
            java_real.mkdir(parents=True)
            sdk_real.mkdir(parents=True)
            java_link = root / "runner-java"
            sdk_link = root / "runner-android-sdk"
            java_link.symlink_to(java_real, target_is_directory=True)
            sdk_link.symlink_to(sdk_real, target_is_directory=True)
            environment["JAVA_HOME"] = str(java_link)
            environment["ANDROID_SDK_ROOT"] = str(sdk_link)

            runtime = _runtime_environment(environment, state, ".")

            self.assertEqual(runtime["JAVA_HOME"], str(java_real))
            self.assertEqual(runtime["ANDROID_SDK_ROOT"], str(sdk_real))
            self.assertEqual(runtime["ANDROID_HOME"], str(sdk_real))

    def test_state_owned_home_symlink_remains_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment, state = self._base_environment(root)
            real_home = root / "outside-home"
            real_home.mkdir()
            linked_home = root / "linked-home"
            linked_home.symlink_to(real_home, target_is_directory=True)
            environment["HOME"] = str(linked_home)

            with self.assertRaises(CIWError) as failure:
                _runtime_environment(environment, state, ".")

            self.assertEqual(failure.exception.code, "runtime_environment_invalid")


if __name__ == "__main__":
    unittest.main()
