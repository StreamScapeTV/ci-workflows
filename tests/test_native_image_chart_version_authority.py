from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/ci/native_image_chart_validate.py"
PREPARE = ROOT / "scripts/ci/native_image_chart_prepare.py"


class NativeImageChartVersionAuthorityTests(unittest.TestCase):
    def _source_repo(self, root: Path, *, chart_name: str = "fixture-chart") -> tuple[Path, str]:
        source = root / "source"
        chart = source / "charts/fixture-chart"
        chart.mkdir(parents=True)
        (source / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (source / "package.json").write_text(
            '{"name":"fixture","version":"9.9.9"}\n',
            encoding="utf-8",
        )
        (chart / "Chart.yaml").write_text(
            "apiVersion: v2\n"
            f"name: {chart_name}\n"
            "version: 8.8.8\n"
            'appVersion: "7.7.7"\n',
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=CI Fixture",
                "-c",
                "user.email=ci@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        sha = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return source, sha

    def _run_validator(self, source: Path, sha: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "SOURCE_ROOT": str(source),
                "SOURCE_SHA": sha,
                "IMAGE_NAME": "fixture-image",
                "CHART_NAME": "fixture-chart",
                "CHART_PATH": "charts/fixture-chart",
                "DOCKERFILE_PATH": "Dockerfile",
                "BUILD_CONTEXT": ".",
                "VERSION": "1.2.3",
            }
        )
        return subprocess.run(
            [sys.executable, str(VALIDATOR)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_committed_package_and_chart_versions_are_not_release_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, sha = self._source_repo(Path(temporary))
            result = self._run_validator(source, sha)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_chart_identity_still_has_to_match_caller_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, sha = self._source_repo(Path(temporary), chart_name="wrong-chart")
            result = self._run_validator(source, sha)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("chart name does not match release input", result.stderr)

    def test_packager_stamps_both_helm_versions_from_product_tag(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        self.assertIn('version=os.environ["VERSION"]', prepare)
        self.assertIn('app_version=os.environ["VERSION"]', prepare)
        self.assertIn(
            'f"{os.environ[\'IMAGE_NAME\']}:{os.environ[\'VERSION\']}"',
            prepare,
        )


if __name__ == "__main__":
    unittest.main()
