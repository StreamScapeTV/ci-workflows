from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import ciw_helm
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]


class HelmCiwPublishBoundaryTests(unittest.TestCase):
    def test_generic_ciw_publish_execute_requires_release_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with (
                patch("ci_workflows.ciw_helm._state_root", return_value=state),
                patch("ci_workflows.ciw_helm._source_root") as source_root,
                patch("ci_workflows.ciw_helm.validate_and_package") as validate,
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "release_adapter_required",
                ):
                    ciw_helm.execute(
                        ROOT,
                        {},
                        operation="publish",
                        phase="execute",
                        source_relative="source",
                    )
            source_root.assert_not_called()
            validate.assert_not_called()

    def test_generic_ciw_publish_cleanup_and_residue_remain_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with (
                patch("ci_workflows.ciw_helm._state_root", return_value=state),
                patch("ci_workflows.ciw_helm.cleanup_helm_state") as cleanup,
            ):
                values = ciw_helm.execute(
                    ROOT,
                    {},
                    operation="publish",
                    phase="cleanup",
                    source_relative="source",
                )
            cleanup.assert_called_once_with(state)
            self.assertEqual(values["result"], "success")

            with (
                patch("ci_workflows.ciw_helm._state_root", return_value=state),
                patch("ci_workflows.ciw_helm.verify_no_helm_residue") as residue,
            ):
                values = ciw_helm.execute(
                    ROOT,
                    {},
                    operation="publish",
                    phase="residue",
                    source_relative="source",
                )
            residue.assert_called_once_with(state)
            self.assertEqual(values["result"], "success")


if __name__ == "__main__":
    unittest.main()
