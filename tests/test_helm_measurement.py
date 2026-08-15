from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_measurement import (
    CONFIG_NAME,
    PID_NAME,
    STATE_NAME,
    _allocated_bytes,
    _finalize_evidence,
    _memory_bytes,
    _write_json,
    stop,
)
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class HelmMeasurementTests(unittest.TestCase):
    def test_allocated_bytes_does_not_follow_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            measured = root / "measured"
            measured.mkdir()
            outside = root / "outside.bin"
            outside.write_bytes(b"x" * (1024 * 1024))
            (measured / "link").symlink_to(outside)
            self.assertLess(_allocated_bytes(measured), 1024 * 1024)

    def test_cgroup_measurement_is_required(self) -> None:
        with patch(
            "ci_workflows.helm_measurement._read_positive_integer",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                HelmValidationError,
                "runner_measurement_unavailable",
            ):
                _memory_bytes()

    def test_small_realistic_evidence_selects_candidate_tiny(self) -> None:
        values = _finalize_evidence(
            ROOT,
            {"source_sha": SHA, "product_id": "iptv-backend-chart"},
            {
                "workspace_baseline_bytes": 64 * 1024 * 1024,
                "peak_runner_temp_bytes": 128 * 1024 * 1024,
                "peak_memory_bytes": 256 * 1024 * 1024,
            },
        )
        self.assertEqual(values["selected_profile"], "buildah-tiny")
        self.assertEqual(values["peak_memory_bytes"], str(256 * 1024 * 1024))
        self.assertIn('"workflow_api":"helm.publish"', values["runner_evidence_json"])

    def test_measurement_that_requires_larger_tier_fails_candidate(self) -> None:
        with self.assertRaisesRegex(
            HelmValidationError,
            "runner_measurement_tier_mismatch",
        ):
            _finalize_evidence(
                ROOT,
                {"source_sha": SHA, "product_id": "iptv-backend-chart"},
                {
                    "workspace_baseline_bytes": 4 * 1024 * 1024 * 1024,
                    "peak_runner_temp_bytes": 2 * 1024 * 1024 * 1024,
                    "peak_memory_bytes": 900 * 1024 * 1024,
                },
            )

    def test_stop_mismatch_still_removes_measurement_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            runner_temp = root / "runner-temp"
            workspace.mkdir()
            runner_temp.mkdir()
            state = runner_temp / STATE_NAME
            state.mkdir()
            _write_json(
                state / CONFIG_NAME,
                {
                    "workspace": str(workspace),
                    "runner_temp": str(runner_temp),
                    "source_sha": "b" * 40,
                    "product_id": "iptv-backend-chart",
                },
            )
            (state / PID_NAME).write_text("99999999\n", encoding="utf-8")
            environment = {
                "GITHUB_WORKSPACE": str(workspace),
                "RUNNER_TEMP": str(runner_temp),
                "INPUT_ADMITTED_SHA": SHA,
                "INPUT_PRODUCT_ID": "iptv-backend-chart",
            }
            with self.assertRaisesRegex(
                HelmValidationError,
                "runner_measurement_mismatch",
            ):
                stop(ROOT, environment)
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
