from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import ciw_gradle_seed
from ci_workflows.ciw_types import CIWContext, CIWError
from ci_workflows.gradle_seed import GradleSeedError, GradleSeedResult

SOURCE_SHA = "a" * 40


class GradleSeedCliDiagnosticsTests(unittest.TestCase):
    def _context(self, directory: str) -> tuple[CIWContext, io.StringIO]:
        root = Path(directory)
        state = root / "workspace-test"
        gradle = state / "gradle"
        gradle.mkdir(parents=True)
        stdout = io.StringIO()
        context = CIWContext(
            root=root,
            environment={
                "CI_WORKFLOW_ROOT": str(state),
                "CI_WORKFLOW_STATE_ID": state.name,
                "GRADLE_USER_HOME": str(gradle),
                "GITHUB_OUTPUT": str(root / "github-output"),
            },
            stdout=stdout,
            stderr=io.StringIO(),
        )
        return context, stdout

    def test_selected_delta_is_reported_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context, stdout = self._context(directory)

            def fake_sync(*, report_selection, **_kwargs):
                report_selection(17, 123456)
                return GradleSeedResult(
                    source_sha=SOURCE_SHA,
                    generation="sha256-" + "b" * 64,
                    file_count=17,
                    total_bytes=123456,
                    evidence_id="c" * 64,
                )

            with mock.patch.object(ciw_gradle_seed, "sync_gradle_seed", side_effect=fake_sync):
                result = ciw_gradle_seed.execute_gradle_seed_upload(
                    argparse.Namespace(source_sha=SOURCE_SHA),
                    context,
                )

            self.assertEqual(
                "gradle-seed delta file_count=17 total_bytes=123456\n",
                stdout.getvalue(),
            )
            self.assertEqual("promoted", result.outputs["result"])

    def test_selected_delta_survives_stable_busy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context, stdout = self._context(directory)

            def fake_sync(*, report_selection, **_kwargs):
                report_selection(29, 654321)
                raise GradleSeedError("gradle_seed_writer_busy")

            with mock.patch.object(ciw_gradle_seed, "sync_gradle_seed", side_effect=fake_sync):
                with self.assertRaises(CIWError) as raised:
                    ciw_gradle_seed.execute_gradle_seed_upload(
                        argparse.Namespace(source_sha=SOURCE_SHA),
                        context,
                    )

            self.assertEqual("gradle_seed_writer_busy", raised.exception.code)
            self.assertEqual(
                "gradle-seed delta file_count=29 total_bytes=654321\n",
                stdout.getvalue(),
            )


if __name__ == "__main__":
    unittest.main()
