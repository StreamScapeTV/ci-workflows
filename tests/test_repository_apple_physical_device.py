from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/ci/repository_apple_physical_device.py"

spec = importlib.util.spec_from_file_location("repository_apple_physical_device", HELPER)
assert spec is not None and spec.loader is not None
device = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device)


class RepositoryApplePhysicalDeviceTests(unittest.TestCase):
    IDENTIFIER = "00008110-001C114E0A92001E"

    def make_repo(self, root: Path, marker: Path) -> Path:
        repo = root / "source"
        (repo / ".ci").mkdir(parents=True)
        entry = repo / ".ci/device-test.sh"
        entry.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "python3 - <<'PY'\n"
            "import json, os\n"
            "from pathlib import Path\n"
            "path=Path(os.environ['CI_DEVICE_CONTEXT_FILE'])\n"
            "value=json.loads(path.read_text())\n"
            "Path(os.environ['TEST_MARKER']).write_text(value['identifier'])\n"
            "print('device=' + value['identifier'])\n"
            "print('context=' + str(path))\n"
            "PY\n",
            encoding="utf-8",
        )
        entry.chmod(0o755)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", ".ci/device-test.sh"], cwd=repo, check=True)
        subprocess.run(["git", "update-index", "--chmod=+x", ".ci/device-test.sh"], cwd=repo, check=True)
        return repo

    def make_xcrun(self, root: Path, devices: list[dict[str, object]]) -> Path:
        bin_dir = root / "bin"
        bin_dir.mkdir(exist_ok=True)
        xcrun = bin_dir / "xcrun"
        payload = {"result": {"devices": devices}}
        xcrun.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"payload = {payload!r}\n"
            "try:\n"
            "    index = sys.argv.index('--json-output')\n"
            "    target = pathlib.Path(sys.argv[index + 1])\n"
            "except (ValueError, IndexError):\n"
            "    raise SystemExit(2)\n"
            "target.write_text(json.dumps(payload), encoding='utf-8')\n",
            encoding="utf-8",
        )
        xcrun.chmod(0o755)
        return bin_dir

    def env(self, root: Path, repo: Path, marker: Path, bin_dir: Path, *, host_class: str = "macos-high-capacity") -> dict[str, str]:
        now = datetime.now(timezone.utc)
        return {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CI_HOST_OS": "macos",
            "CI_HOST_CLASS": host_class,
            "CI_OPERATION": "device-test",
            "CI_APPLE_PHYSICAL_DEVICE_AUTHORIZED": "true",
            "CI_APPLE_PHYSICAL_DEVICE_AUTHORIZED_AT": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "CI_APPLE_PHYSICAL_DEVICE_EXPIRES_AT": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "CI_INPUTS_JSON": json.dumps({"schemaVersion": 1, "inputs": {"device_platform": "ios", "test_selectors": ["physical-core"]}}),
            "RUNNER_TEMP": str(root / "runner-temp"),
            "TEST_MARKER": str(marker),
        }

    def run_helper(self, *, devices: list[dict[str, object]], host_class: str = "macos-high-capacity", mutate_env=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runner-temp").mkdir()
            marker = root / "marker"
            repo = self.make_repo(root, marker)
            bin_dir = self.make_xcrun(root, devices)
            env = self.env(root, repo, marker, bin_dir, host_class=host_class)
            if mutate_env:
                mutate_env(env)
            result = subprocess.run(
                [sys.executable, str(HELPER), "execute"],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            secret_path = root / "runner-temp" / device.SECRET_NAME
            context_path = root / "runner-temp" / device.CONTEXT_NAME
            return result, marker.read_text() if marker.exists() else None, secret_path.read_text() if secret_path.exists() else None, context_path.exists()

    def eligible(self, identifier: str | None = None) -> dict[str, object]:
        return {
            "identifier": identifier or self.IDENTIFIER,
            "hardwareProperties": {
                "platform": "iOS",
                "reality": "physical",
                "productType": "iPhone15,3",
                "udid": identifier or self.IDENTIFIER,
            },
            "deviceProperties": {
                "developerModeStatus": "enabled",
                "name": "Private iPhone",
            },
            "connectionProperties": {
                "pairingState": "paired",
                "transportType": "USB",
            },
        }

    def test_exactly_one_attached_iphone_executes_fixed_entrypoint_with_redaction(self) -> None:
        result, marker, secrets, context_exists = self.run_helper(devices=[self.eligible()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker, self.IDENTIFIER)
        self.assertIsNotNone(secrets)
        assert secrets is not None
        self.assertIn(self.IDENTIFIER, secrets)
        self.assertFalse(context_exists)
        self.assertNotIn(self.IDENTIFIER, result.stdout)
        self.assertNotIn("central-apple-physical-device-context.json", result.stdout)
        self.assertIn("*", result.stdout)

    def test_non_iphone_iphoneos_device_is_not_eligible(self) -> None:
        ipad = self.eligible()
        ipad["hardwareProperties"]["productType"] = "iPad14,5"
        result, marker, secrets, context_exists = self.run_helper(devices=[ipad])
        self.assertEqual(result.returncode, 2)
        self.assertIn("found 0", result.stderr)
        self.assertIsNone(marker)
        self.assertIsNone(secrets)
        self.assertFalse(context_exists)

    def test_stream_redaction_masks_private_values_across_chunk_boundaries(self) -> None:
        secret = b"device-private-identifier"
        payload = b"x" * (64 * 1024 - 5) + secret + b"-tail"

        class Sink:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

        sink = Sink()
        with patch.object(device.sys, "stdout", sink):
            device.stream_redacted(io.BytesIO(payload), (secret,))
        value = sink.buffer.getvalue()
        self.assertEqual(len(value), len(payload))
        self.assertNotIn(secret, value)
        self.assertIn(b"*" * len(secret), value)

    def test_discovery_failure_or_timeout_removes_private_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runner_temp = Path(td)
            inventory = runner_temp / device.INVENTORY_NAME

            def fail_after_write(argv, **kwargs):
                target = Path(argv[argv.index("--json-output") + 1])
                target.write_text(json.dumps({"result": {"devices": [self.eligible()]}}), encoding="utf-8")
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="private")

            with patch.object(device.subprocess, "run", side_effect=fail_after_write):
                with self.assertRaisesRegex(device.DeviceError, "failed before product execution"):
                    device.discover_attached_iphone(runner_temp)
            self.assertFalse(inventory.exists() or inventory.is_symlink())

            def timeout_after_write(argv, **kwargs):
                target = Path(argv[argv.index("--json-output") + 1])
                target.write_text(json.dumps({"result": {"devices": [self.eligible()]}}), encoding="utf-8")
                raise subprocess.TimeoutExpired(argv, 60)

            with patch.object(device.subprocess, "run", side_effect=timeout_after_write):
                with self.assertRaisesRegex(device.DeviceError, "failed before product execution"):
                    device.discover_attached_iphone(runner_temp)
            self.assertFalse(inventory.exists() or inventory.is_symlink())

    def test_zero_or_multiple_devices_fail_before_product_execution(self) -> None:
        for devices in ([], [self.eligible(), self.eligible("00008110-001C114E0A92002F")]):
            with self.subTest(count=len(devices)):
                result, marker, secrets, context_exists = self.run_helper(devices=devices)
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires exactly one eligible attached iPhone", result.stderr)
                self.assertIsNone(marker)
                self.assertIsNone(secrets)
                self.assertFalse(context_exists)

    def test_wrong_host_or_expired_authorization_fails_before_discovery_and_product(self) -> None:
        wrong, marker, _, _ = self.run_helper(devices=[self.eligible()], host_class="macos-hosted")
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("high-capacity macOS host", wrong.stderr)
        self.assertIsNone(marker)

        def expired(env: dict[str, str]) -> None:
            now = datetime.now(timezone.utc)
            env["CI_APPLE_PHYSICAL_DEVICE_AUTHORIZED_AT"] = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
            env["CI_APPLE_PHYSICAL_DEVICE_EXPIRES_AT"] = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

        result, marker, _, _ = self.run_helper(devices=[self.eligible()], mutate_env=expired)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not currently active", result.stderr)
        self.assertIsNone(marker)

    def test_fence_contention_fails_before_discovery_or_product(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            marker = root / "marker"
            repo = self.make_repo(root, marker)
            bin_dir = self.make_xcrun(root, [self.eligible()])
            env = self.env(root, repo, marker, bin_dir)
            lock = root / "device.lock"
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.dict(os.environ, env, clear=True), patch.object(device, "LOCK_PATH", lock), patch.object(Path, "cwd", return_value=repo):
                    with self.assertRaisesRegex(device.DeviceError, "fence is already held"):
                        device.execute()
                self.assertFalse(marker.exists())
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def test_success_releases_host_global_fence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            marker = root / "marker"
            repo = self.make_repo(root, marker)
            bin_dir = self.make_xcrun(root, [self.eligible()])
            env = self.env(root, repo, marker, bin_dir)
            lock = root / "device.lock"
            with patch.dict(os.environ, env, clear=True), patch.object(device, "LOCK_PATH", lock), patch.object(Path, "cwd", return_value=repo):
                self.assertEqual(device.execute(), 0)
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def test_untracked_symlink_or_non_executable_entrypoint_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            (repo / ".ci").mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            entry = repo / ".ci/device-test.sh"
            entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            entry.chmod(0o755)
            with self.assertRaisesRegex(device.DeviceError, "tracked"):
                device.validate_entrypoint(repo)
            subprocess.run(["git", "add", ".ci/device-test.sh"], cwd=repo, check=True)
            subprocess.run(["git", "update-index", "--chmod=-x", ".ci/device-test.sh"], cwd=repo, check=True)
            entry.chmod(0o644)
            with self.assertRaisesRegex(device.DeviceError, "100755"):
                device.validate_entrypoint(repo)


if __name__ == "__main__":
    unittest.main()
