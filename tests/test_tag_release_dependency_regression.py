from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-tag-image-chart.yml"


class TagReleaseDependencyRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_release_uses_only_the_live_high_buildah_capability(self) -> None:
        self.assertEqual(self.text.count("    runs-on: buildah-high\n"), 1)
        self.assertNotIn("self-hosted", self.text)

    def test_stale_lock_without_declared_dependencies_is_rejected(self) -> None:
        marker = "          python3 - <<'PY'\n"
        start = self.text.index(
            marker,
            self.text.index("Prepare locked Helm chart dependencies"),
        )
        script = textwrap.dedent(
            self.text[start + len(marker):].split("\n          PY", 1)[0]
        )

        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            chart = directory / "chart"
            chart.mkdir()
            (chart / "Chart.lock").write_text(
                "dependencies: []\ndigest: sha256:test\ngenerated: now\n",
                encoding="utf-8",
            )
            dependency_list = directory / "dependencies.txt"
            dependency_list.write_text(
                "NAME VERSION REPOSITORY STATUS\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "CHART_SOURCE": str(chart),
                "DEPENDENCY_LIST": str(dependency_list),
                "DEPENDENCY_REPOSITORIES": str(directory / "repositories.txt"),
                "DEPENDENCY_COUNT_FILE": str(directory / "count.txt"),
            }
            result = subprocess.run(
                [sys.executable, "-S", "-c", script],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Chart.lock exists but Chart.yaml declares no dependencies",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
