"""Focused coverage for Flux-owned read-only Gradle dependency cache handoff."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.ciw_android import _runtime_environment
from ci_workflows.ciw_types import CIWContext, CIWError

ROOT = Path(__file__).resolve().parents[1]


class AndroidGradleReadOnlyCacheTests(unittest.TestCase):
    def _context(
        self,
        root: Path,
        *,
        gradle_home: Path | None = None,
        extra: dict[str, str] | None = None,
    ) -> tuple[CIWContext, Path]:
        home = root / "home"
        writable_gradle = gradle_home or root / "gradle-home"
        temporary = root / "tmp"
        sdk = root / "android-sdk"
        for directory in (home, writable_gradle, temporary, sdk):
            directory.mkdir(parents=True, exist_ok=True)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "GRADLE_USER_HOME": str(writable_gradle),
            "TMPDIR": str(temporary),
            "ANDROID_SDK_ROOT": str(sdk),
        }
        if extra:
            environment.update(extra)
        return (
            CIWContext(ROOT, environment, io.StringIO(), io.StringIO()),
            writable_gradle.resolve(),
        )

    def test_fixed_flux_seed_is_propagated_without_leaking_host_or_secret_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "gradle-ro-cache"
            seed.mkdir()
            context, writable_gradle = self._context(
                root,
                extra={
                    "GRADLE_RO_DEP_CACHE": str(seed),
                    "GITHUB_TOKEN": "must-not-reach-gradle",
                    "AWS_SECRET_ACCESS_KEY": "must-not-reach-gradle",
                    "UNRELATED_HOST_STATE": "/private/host/state",
                },
            )
            with mock.patch(
                "ci_workflows.ciw_android._GRADLE_RO_DEP_CACHE_PATH",
                seed,
            ):
                environment = _runtime_environment(context, None)

            self.assertEqual(environment["GRADLE_RO_DEP_CACHE"], str(seed.resolve()))
            self.assertEqual(environment["GRADLE_USER_HOME"], str(writable_gradle))
            self.assertNotEqual(environment["GRADLE_RO_DEP_CACHE"], environment["GRADLE_USER_HOME"])
            self.assertNotIn("GITHUB_TOKEN", environment)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)
            self.assertNotIn("UNRELATED_HOST_STATE", environment)

    def test_absent_or_unmounted_fixed_seed_degrades_to_cold_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_seed = root / "missing-gradle-ro-cache"
            context, _ = self._context(root)
            with mock.patch(
                "ci_workflows.ciw_android._GRADLE_RO_DEP_CACHE_PATH",
                missing_seed,
            ):
                environment = _runtime_environment(context, None)
            self.assertNotIn("GRADLE_RO_DEP_CACHE", environment)

            context.environment["GRADLE_RO_DEP_CACHE"] = str(missing_seed)
            with mock.patch(
                "ci_workflows.ciw_android._GRADLE_RO_DEP_CACHE_PATH",
                missing_seed,
            ):
                environment = _runtime_environment(context, None)
            self.assertNotIn("GRADLE_RO_DEP_CACHE", environment)

    def test_unexpected_nonempty_cache_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_seed = root / "expected-seed"
            unexpected_seed = root / "caller-selected-seed"
            expected_seed.mkdir()
            unexpected_seed.mkdir()
            context, _ = self._context(
                root,
                extra={"GRADLE_RO_DEP_CACHE": str(unexpected_seed)},
            )
            with (
                mock.patch(
                    "ci_workflows.ciw_android._GRADLE_RO_DEP_CACHE_PATH",
                    expected_seed,
                ),
                self.assertRaises(CIWError) as failure,
            ):
                _runtime_environment(context, None)
            self.assertEqual(failure.exception.code, "gradle_read_only_cache_invalid")

    def test_read_only_seed_cannot_alias_private_writable_gradle_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gradle_home = root / "gradle-home"
            context, _ = self._context(
                root,
                gradle_home=gradle_home,
                extra={"GRADLE_RO_DEP_CACHE": str(gradle_home)},
            )
            with (
                mock.patch(
                    "ci_workflows.ciw_android._GRADLE_RO_DEP_CACHE_PATH",
                    gradle_home,
                ),
                self.assertRaises(CIWError) as failure,
            ):
                _runtime_environment(context, None)
            self.assertEqual(failure.exception.code, "gradle_read_only_cache_invalid")


if __name__ == "__main__":
    unittest.main()
