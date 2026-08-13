from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from ci_workflows import oci_publish as runtime
from ci_workflows import oci_publish_guards as guards
from ci_workflows.oci_publish import OciPublishError


class RegistrySubprocessBoundTests(unittest.TestCase):
    def _run_child(
        self,
        script: str,
        *,
        stdout_limit: int,
        stderr_limit: int,
        overflow_code: str = "registry_inspection_failed",
        retain_output: bool = True,
        check: bool = True,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return runtime._run_bounded_subprocess(  # noqa: SLF001
            [sys.executable, "-c", script],
            input_bytes=input_bytes,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            overflow_code=overflow_code,
            retain_output=retain_output,
            check=check,
            env={"PATH": os.environ.get("PATH", "")},
        )

    def test_exact_independent_limits_are_accepted(self) -> None:
        result = self._run_child(
            "import os; os.write(1, b'o' * 4096); os.write(2, b'e' * 2048)",
            stdout_limit=4096,
            stderr_limit=2048,
        )
        self.assertEqual(result.stdout, b"o" * 4096)
        self.assertEqual(result.stderr, b"e" * 2048)

    def test_discarded_output_is_counted_but_not_retained(self) -> None:
        result = self._run_child(
            "import os; os.write(1, b'o' * 4096); os.write(2, b'e' * 4096)",
            stdout_limit=4096,
            stderr_limit=4096,
            retain_output=False,
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_overflow_stops_producer_before_completion_and_reaps_it(self) -> None:
        for descriptor in (1, 2):
            with self.subTest(descriptor=descriptor), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                pid_path = root / "producer.pid"
                complete_path = root / "complete"
                script = (
                    "import os, pathlib\n"
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
                    f"descriptor = {descriptor}\n"
                    "for _ in range(128):\n"
                    "    os.write(descriptor, b'x' * 65536)\n"
                    f"pathlib.Path({str(complete_path)!r}).write_text('complete')\n"
                )
                with self.assertRaisesRegex(
                    OciPublishError, "registry_auth_failed"
                ):
                    self._run_child(
                        script,
                        stdout_limit=4096,
                        stderr_limit=4096,
                        overflow_code="registry_auth_failed",
                    )

                self.assertTrue(pid_path.is_file())
                self.assertFalse(complete_path.exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pid_path.read_text()), 0)

    def test_overflow_stops_descendants_that_inherit_capture_pipes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            leader_pid_path = root / "leader.pid"
            child_pid_path = root / "child.pid"
            child_complete_path = root / "child-complete"
            child_script = (
                "import os, pathlib, time\n"
                f"pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()))\n"
                "time.sleep(30)\n"
                f"pathlib.Path({str(child_complete_path)!r}).write_text('complete')\n"
            )
            leader_script = (
                "import os, pathlib, subprocess, sys, time\n"
                f"pathlib.Path({str(leader_pid_path)!r}).write_text(str(os.getpid()))\n"
                f"child = subprocess.Popen([sys.executable, '-c', {child_script!r}])\n"
                f"marker = pathlib.Path({str(child_pid_path)!r})\n"
                "while not marker.exists(): time.sleep(0.001)\n"
                "os.write(1, b'x' * 65536)\n"
                "time.sleep(30)\n"
            )

            with self.assertRaisesRegex(
                OciPublishError, "registry_copy_failed"
            ):
                self._run_child(
                    leader_script,
                    stdout_limit=4096,
                    stderr_limit=4096,
                    overflow_code="registry_copy_failed",
                )

            self.assertTrue(leader_pid_path.is_file())
            self.assertTrue(child_pid_path.is_file())
            self.assertFalse(child_complete_path.exists())
            leader_pid = int(leader_pid_path.read_text())
            child_pid = int(child_pid_path.read_text())
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            with self.assertRaises(ProcessLookupError):
                os.killpg(leader_pid, 0)

    def test_raw_inspect_call_sites_apply_exact_capture_policy(self) -> None:
        raw = b'{"schemaVersion":2}'
        result = subprocess.CompletedProcess(["skopeo", "inspect"], 0, raw, b"")
        for inspect in (runtime._inspect_remote_digest, guards._inspect_remote_digest):  # noqa: SLF001
            with self.subTest(inspect=inspect.__module__), patch.object(
                runtime, "_run", return_value=result
            ) as run:
                digest = inspect(
                    "registry.example.invalid/product:1.2.3",
                    Path("registry-auth.json"),
                    object(),
                )
                self.assertTrue(digest.startswith("sha256:"))
                self.assertEqual(
                    run.call_args.kwargs,
                    {
                        "check": False,
                        "capacity_roots": ANY,
                        "stdout_limit": runtime._MAX_REGISTRY_RAW_MANIFEST_BYTES,  # noqa: SLF001
                        "stderr_limit": runtime._MAX_REGISTRY_INSPECTION_STDERR_BYTES,  # noqa: SLF001
                        "overflow_code": "registry_inspection_failed",
                        "expected_auth_state": None,
                    },
                )

    def test_stdin_and_nonzero_check_semantics_are_preserved(self) -> None:
        script = (
            "import os, sys; payload=sys.stdin.buffer.read(); "
            "os.write(1, payload); sys.exit(7)"
        )
        result = self._run_child(
            script,
            stdout_limit=64,
            stderr_limit=64,
            input_bytes=b"bounded-token",
            check=False,
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"bounded-token")
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self._run_child(
                script,
                stdout_limit=64,
                stderr_limit=64,
                input_bytes=b"bounded-token",
            )
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(raised.exception.stdout, b"bounded-token")


if __name__ == "__main__":
    unittest.main()
