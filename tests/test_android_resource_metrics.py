"""Focused tests for bounded Android execution resource telemetry."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from ci_workflows.android_resource_metrics import AndroidResourceSampler


class AndroidResourceSamplerTests(unittest.TestCase):
    def _cgroup_fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        proc = root / "proc-self-cgroup"
        cgroup_root = root / "cgroup"
        directory = cgroup_root / "runner" / "job"
        directory.mkdir(parents=True)
        proc.write_text("0::/runner/job\n", encoding="ascii")
        memory = directory / "memory.current"
        processes = directory / "pids.current"
        memory.write_text("1024\n", encoding="ascii")
        processes.write_text("2\n", encoding="ascii")
        return proc, cgroup_root, memory, processes

    def test_sampler_records_peak_cgroup_memory_and_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, cgroup_root, memory, processes = self._cgroup_fixture(root)
            sampler = AndroidResourceSampler(
                proc_cgroup=proc,
                cgroup_root=cgroup_root,
                poll_interval_seconds=0.005,
            )
            with sampler:
                memory.write_text("8192\n", encoding="ascii")
                processes.write_text("7\n", encoding="ascii")
                time.sleep(0.025)
                memory.write_text("2048\n", encoding="ascii")
                processes.write_text("3\n", encoding="ascii")
            result = sampler.result
            self.assertEqual(result.measurement_source, "cgroup-v2-sampled")
            self.assertGreaterEqual(result.peak_memory_bytes or 0, 8192)
            self.assertGreaterEqual(result.peak_processes or 0, 7)
            self.assertGreaterEqual(result.wall_ms, 0)
            if result.child_cpu_ms is not None:
                self.assertGreaterEqual(result.child_cpu_ms, 0)

    def test_wall_and_child_cpu_deltas_are_deterministic_with_injected_readers(self) -> None:
        clocks = iter((1_000_000_000, 1_275_000_000))
        cpus = iter((2.25, 3.0))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sampler = AndroidResourceSampler(
                proc_cgroup=root / "missing-proc-cgroup",
                cgroup_root=root / "missing-cgroup",
                clock_ns=lambda: next(clocks),
                cpu_reader=lambda: next(cpus),
            )
            with sampler:
                pass
            result = sampler.result
            self.assertEqual(result.wall_ms, 275)
            self.assertEqual(result.child_cpu_ms, 750)
            self.assertIsNone(result.peak_memory_bytes)
            self.assertIsNone(result.peak_processes)
            self.assertEqual(result.measurement_source, "unavailable")

    def test_unsupported_metrics_are_explicit_and_never_fabricated_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sampler = AndroidResourceSampler(
                proc_cgroup=root / "missing-proc-cgroup",
                cgroup_root=root / "missing-cgroup",
            )
            with sampler:
                pass
            result = sampler.result
            self.assertIsNone(result.peak_memory_bytes)
            self.assertIsNone(result.peak_processes)
            self.assertEqual(result.measurement_source, "unavailable")

    def test_sampler_preserves_child_failure_and_still_stops(self) -> None:
        class ChildFailure(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, cgroup_root, _memory, _processes = self._cgroup_fixture(root)
            sampler = AndroidResourceSampler(
                proc_cgroup=proc,
                cgroup_root=cgroup_root,
                poll_interval_seconds=0.005,
            )
            marker = ChildFailure("child failed")
            with self.assertRaises(ChildFailure) as failure:
                with sampler:
                    raise marker
            self.assertIs(failure.exception, marker)
            self.assertGreaterEqual(sampler.result.wall_ms, 0)

    def test_disappearing_cgroup_files_do_not_change_execution_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, cgroup_root, memory, processes = self._cgroup_fixture(root)
            sampler = AndroidResourceSampler(
                proc_cgroup=proc,
                cgroup_root=cgroup_root,
                poll_interval_seconds=0.005,
            )
            with sampler:
                memory.unlink()
                processes.unlink()
                time.sleep(0.015)
            self.assertGreaterEqual(sampler.result.wall_ms, 0)

    def test_invalid_poll_interval_fails_before_start(self) -> None:
        with self.assertRaises(ValueError):
            AndroidResourceSampler(poll_interval_seconds=0)
        with self.assertRaises(ValueError):
            AndroidResourceSampler(poll_interval_seconds=6)


if __name__ == "__main__":
    unittest.main()
