from __future__ import annotations

from contextlib import redirect_stdout
import base64
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
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

# Public synthetic certificate only; its private key is not retained. Security's
# trust/private-key boundary is mocked, while public DER/subject decoding is real.
SIGNING_CERT_PEM = b"""-----BEGIN CERTIFICATE-----
MIICFTCCAbqgAwIBAgIDEjq8MAoGCCqGSM49BAMCMGgxNzA1BgNVBAMMLkFwcGxl
IERldmVsb3BtZW50OiBTeW50aGV0aWMgVGVzdCAoUEVSU09OMTIzNCkxEzARBgNV
BAsMCkFCQ0RFMTIzNDUxGDAWBgNVBAoMD0NlbnRyYWwgQ0kgVGVzdDAeFw0yNjEw
MTAyMjQxNDhaFw0zNjEwMDcyMjQxNDhaMGgxNzA1BgNVBAMMLkFwcGxlIERldmVs
b3BtZW50OiBTeW50aGV0aWMgVGVzdCAoUEVSU09OMTIzNCkxEzARBgNVBAsMCkFC
Q0RFMTIzNDUxGDAWBgNVBAoMD0NlbnRyYWwgQ0kgVGVzdDBZMBMGByqGSM49AgEG
CCqGSM49AwEHA0IABPgFeP+nJQrF++lMgQRg5kEOHJ7yNNUVI3GoNl0GtLG2+cpW
oUFLMLEcgBlbDCAN9C73cAjabH+gcYsRcEFkLJOjUzBRMB0GA1UdDgQWBBS8Jnvc
FJN18klDNHZBsreZkzF0TzAfBgNVHSMEGDAWgBS8JnvcFJN18klDNHZBsreZkzF0
TzAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0kAMEYCIQCNH4cHTUqDOlVt
G/NFli4LWtAOXn4rFSBKdirxBTUp6QIhAMcZVyvpOvU5BtTQwdb6YM9lDHu3LXaj
KRBi01qHh3eS
-----END CERTIFICATE-----
"""
SIGNING_CERT_DER = ssl.PEM_cert_to_DER_cert(SIGNING_CERT_PEM.decode('ascii'))
SIGNING_CERT_FINGERPRINT = hashlib.sha1(SIGNING_CERT_DER).hexdigest().upper()
SIGNING_CERT_NAME = 'Apple Development: Synthetic Test (PERSON1234)'


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
        class Sink:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

            def write(self, value: str) -> int:
                encoded = value.encode("utf-8")
                self.buffer.write(encoded)
                return len(value)

            def flush(self) -> None:
                return None

            def text(self) -> str:
                return self.buffer.getvalue().decode("utf-8", errors="replace")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "runner-temp").mkdir()
            marker = root / "marker"
            repo = self.make_repo(root, marker)
            bin_dir = self.make_xcrun(root, devices)
            env = self.env(root, repo, marker, bin_dir, host_class=host_class)
            if mutate_env:
                mutate_env(env)
            stdout = Sink()
            stderr = Sink()
            previous = Path.cwd()
            try:
                os.chdir(repo)
                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(device.sys, "stdout", stdout),
                    patch.object(device.sys, "stderr", stderr),
                    patch.object(device, "setup_signing", return_value=()),
                    patch.object(device, "cleanup_signing"),
                ):
                    returncode = device.main(["execute"])
            finally:
                os.chdir(previous)
            result = subprocess.CompletedProcess([str(HELPER), "execute"], returncode, stdout.text(), stderr.text())
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
                "transportType": "wired",
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

    def test_network_only_iphone_is_not_eligible(self) -> None:
        network = self.eligible()
        network["connectionProperties"]["transportType"] = "localNetwork"
        result, marker, secrets, context_exists = self.run_helper(devices=[network])
        self.assertEqual(result.returncode, 2)
        self.assertIn("found 0", result.stderr)
        self.assertIsNone(marker)
        self.assertIsNone(secrets)
        self.assertFalse(context_exists)

    def test_unknown_or_unproven_device_state_is_not_eligible(self) -> None:
        cases = {
            "missing-pairing": lambda value: value["connectionProperties"].pop("pairingState"),
            "unpaired": lambda value: value["connectionProperties"].__setitem__("pairingState", "unpaired"),
            "missing-developer-mode": lambda value: value["deviceProperties"].pop("developerModeStatus"),
            "unknown-developer-mode": lambda value: value["deviceProperties"].__setitem__("developerModeStatus", "unknown"),
            "disabled-developer-mode": lambda value: value["deviceProperties"].__setitem__("developerModeStatus", "disabled"),
            "local-network": lambda value: value["connectionProperties"].__setitem__("transportType", "localNetwork"),
            "unknown-wired-substring": lambda value: value["connectionProperties"].__setitem__("transportType", "not-wired-but-unknown"),
            "unknown-usb-substring": lambda value: value["connectionProperties"].__setitem__("transportType", "usb-via-unknown"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                runner_temp = root / "runner-temp"
                runner_temp.mkdir()
                marker = root / "marker"
                repo = self.make_repo(root, marker)
                candidate = self.eligible()
                mutate(candidate)
                bin_dir = self.make_xcrun(root, [candidate])
                result, _, _, _ = self.run_helper(devices=[candidate])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("found 0", result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((runner_temp / device.SECRET_NAME).exists())
                self.assertFalse((runner_temp / device.CONTEXT_NAME).exists())
                self.assertFalse((runner_temp / device.INVENTORY_NAME).exists())

    def test_exact_legacy_usb_transport_is_eligible(self) -> None:
        legacy_usb = self.eligible()
        legacy_usb["connectionProperties"]["transportType"] = "usb"
        result, marker, secrets, context_exists = self.run_helper(devices=[legacy_usb])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker, self.IDENTIFIER)
        self.assertIsNotNone(secrets)
        self.assertFalse(context_exists)

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
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(device, "LOCK_PATH", lock),
                patch.object(Path, "cwd", return_value=repo),
                patch.object(device, "setup_signing", return_value=()),
                patch.object(device, "cleanup_signing"),
            ):
                self.assertEqual(device.execute(), 0)
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


    def signing_env(self, runner_temp: Path) -> dict[str, str]:
        return {
            **os.environ,
            "RUNNER_TEMP": str(runner_temp),
            "CI_APPLE_TEAM_ID": "ABCDE12345",
            "CI_APPLE_DEVELOPMENT_P12_BASE64": base64.b64encode(b"\x30" + b"p" * 63).decode("ascii"),
            "CI_APPLE_DEVELOPMENT_P12_PASSWORD": "fake-p12-password",
        }

    def prepare_cleanup_state(self, root: Path) -> tuple[Path, Path, Path]:
        state, keychain, bundle = device._signing_paths(root)
        original = '/synthetic/login.keychain-db'
        device._write_signing_state(state, {
            'schema': device.SIGNING_STATE_SCHEMA, 'teamId': 'ABCDE12345',
            'originalDefaultKeychain': original, 'originalSearchKeychains': [original],
            'managedKeychain': str(keychain),
        })
        keychain.write_bytes(b'synthetic-keychain')
        bundle.write_bytes(b'synthetic-p12')
        return state, keychain, bundle

    def test_signing_setup_imports_owner_p12_and_proves_same_isolated_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runner_temp = Path(td)
            secret_path = runner_temp / device.SECRET_NAME
            secret_path.write_text("device-secret\n", encoding="utf-8")
            secret_path.chmod(0o600)
            original = "/Users/runner/Library/Keychains/login.keychain-db"
            managed = str(runner_temp / device.SIGNING_KEYCHAIN_NAME)
            fingerprint = SIGNING_CERT_FINGERPRINT
            active = {"managed": False}
            calls: list[list[str]] = []

            def private_command(argv: list[str], **_kwargs):
                self.assertNotIn("CI_APPLE_DEVELOPMENT_P12_BASE64", os.environ)
                self.assertNotIn("CI_APPLE_DEVELOPMENT_P12_PASSWORD", os.environ)
                calls.append(argv)
                if argv[:4] == ["security", "list-keychains", "-d", "user"] and len(argv) == 4:
                    return f'"{managed if active["managed"] else original}"\n'.encode()
                if argv[:4] == ["security", "default-keychain", "-d", "user"] and len(argv) == 4:
                    return f'"{managed if active["managed"] else original}"\n'.encode()
                if argv[:5] == ["security", "list-keychains", "-d", "user", "-s"]:
                    active["managed"] = argv[-1] == managed
                if argv[:5] == ["security", "default-keychain", "-d", "user", "-s"]:
                    active["managed"] = argv[-1] == managed
                if argv[:2] == ["security", "create-keychain"]:
                    Path(argv[-1]).write_bytes(b"keychain")
                if argv[:4] == ["security", "find-identity", "-v", "-p"]:
                    return f'  1) {fingerprint} "{SIGNING_CERT_NAME}"\n'.encode()
                if argv[:2] == ["security", "find-certificate"]:
                    return SIGNING_CERT_PEM
                if argv[:2] == ["security", "delete-keychain"]:
                    Path(argv[-1]).unlink(missing_ok=True)
                return b""

            env = self.signing_env(runner_temp)
            with patch.dict(os.environ, env, clear=True), patch.object(device, "_run_private", side_effect=private_command):
                redactions = device.setup_signing(runner_temp, secret_path)
                self.assertEqual(
                    (runner_temp / device.SIGNING_BUNDLE_NAME).read_bytes(), b"\x30" + b"p" * 63,
                )
                self.assertEqual((runner_temp / device.SIGNING_BUNDLE_NAME).stat().st_mode & 0o777, 0o600)
                device.cleanup_signing()

            self.assertTrue(any(cmd[:2] == ["security", "import"] for cmd in calls))
            self.assertTrue(any(cmd[:2] == ["security", "set-key-partition-list"] for cmd in calls))
            self.assertTrue(any(cmd[:3] == ["codesign", "--force", "--sign"] for cmd in calls))
            self.assertTrue(any(cmd[:3] == ["codesign", "--verify", "--strict"] for cmd in calls))
            self.assertNotIn("openssl", [cmd[0] for cmd in calls])
            self.assertNotIn("DELETE", [cmd[0] for cmd in calls])
            self.assertEqual(active["managed"], False)
            self.assertFalse((runner_temp / device.SIGNING_STATE_NAME).exists())
            self.assertFalse((runner_temp / device.SIGNING_KEYCHAIN_NAME).exists())
            self.assertFalse((runner_temp / device.SIGNING_BUNDLE_NAME).exists())
            self.assertIn(env["CI_APPLE_DEVELOPMENT_P12_PASSWORD"].encode(), redactions)
            self.assertIn(env["CI_APPLE_DEVELOPMENT_P12_BASE64"].encode(), redactions)
            self.assertIn(fingerprint.encode(), redactions)
            self.assertIn(fingerprint, secret_path.read_text())

    def test_identity_is_rejected_if_non_development_mismatched_or_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            keychain = Path(td) / "managed.keychain-db"
            cases = (
                (f'  1) {"A" * 40} "Apple Distribution: Owner Identity (ABCDE12345)"\n', "ABCDE12345"),
                # A matching-looking CN suffix cannot override the signed OU.
                (f'  1) {SIGNING_CERT_FINGERPRINT} "{SIGNING_CERT_NAME}"\n', "PERSON1234"),
                (f'  1) {"A" * 40} "Apple Development: First (ABCDE12345)"\n  2) {"B" * 40} "Apple Development: Second (ABCDE12345)"\n', "ABCDE12345"),
                ('  0 valid identities found\n', "ABCDE12345"),
            )
            for value, team in cases:
                def command(args, **_kwargs):
                    return SIGNING_CERT_PEM if args[1] == 'find-certificate' else value.encode()
                with self.subTest(value=value), patch.object(device, "_run_private", side_effect=command):
                    with self.assertRaisesRegex(device.DeviceError, "identity validation failed"):
                        device._selected_signing_identity(keychain, team)

    def test_valid_identity_failure_reasons_are_fixed_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            keychain = Path(td) / "managed.keychain-db"
            certificate = "Private Certificate Name"
            fingerprint = "C" * 40
            valid = lambda title: f'  1) {fingerprint} "{title}"\n  1 valid identities found\n'.encode()
            all_invalid = (
                f'  1) {fingerprint} "Apple Development: {certificate} (ABCDE12345)" '
                '(CSSMERR_TP_NOT_TRUSTED)\n  1 identities found\n'
            ).encode()
            cases = (
                (b'  0 valid identities found\n', b'  0 identities found\n', 'no-key-backed-identity'),
                (b'  0 valid identities found\n', all_invalid, 'no-valid-identity'),
                (valid(f'Apple Distribution: {certificate} (ABCDE12345)'), None, 'wrong-certificate-type'),
                (valid(f'Apple Development: {certificate} (OTHER12345)'), None, 'team-mismatch'),
                ((f'  1) {fingerprint} "Apple Development: {certificate} (ABCDE12345)"\n'
                 f'  2) {"D" * 40} "Apple Development: Second (ABCDE12345)"\n'
                 '  2 valid identities found\n').encode(), None, 'ambiguous-valid-identities'),
            )
            for valid_inventory, all_inventory, code in cases:
                observed: list[list[str]] = []

                def private_command(argv: list[str], **_kwargs):
                    observed.append(argv)
                    self.assertEqual(argv[-1], str(keychain))
                    return valid_inventory if '-v' in argv else all_inventory

                with (self.subTest(code=code), patch.object(device, '_run_private', side_effect=private_command),
                      patch.object(device, '_imported_certificate_metadata', return_value=('OTHER12345', '123ABC'))):
                    with self.assertRaises(device.DeviceError) as raised:
                        device._selected_signing_identity(keychain, "ABCDE12345")
                    self.assertEqual(
                        str(raised.exception),
                        f'Central Apple Development signing identity validation failed ({code})',
                    )
                    self.assertNotIn(certificate, str(raised.exception))
                    self.assertNotIn(fingerprint, str(raised.exception))
                    self.assertNotIn('ABCDE12345', str(raised.exception))
                    self.assertEqual(len(observed), 3 if code == 'no-valid-identity' else 2 if all_inventory is not None else 1)

    def test_security_identity_inventory_two_sections_preserve_validity_boundary(self) -> None:
        private_name = 'Apple Development: Private Person (ABCDE12345)'
        other_name = 'Apple Development: Second Private Person (ABCDE12345)'
        identity_a, identity_b = 'A' * 40, 'B' * 40
        inventory = (
            'Policy: Code Signing\n'
            '  Matching identities\n'
            f'    1) {identity_a} "{private_name}" (CSSMERR_TP_NOT_TRUSTED)\n'
            f'    2) {identity_b} "{other_name}"\n'
            '  2 identities found\n'
            '\n'
            '  Valid identities only\n'
            f'    1) {identity_b} "{other_name}"\n'
            '  1 valid identities found\n'
        ).encode()
        self.assertEqual(
            device._identity_records(inventory, valid_only=False),
            [(identity_a, private_name), (identity_b, other_name)],
        )
        self.assertEqual(device._identity_records(inventory), [(identity_b, other_name)])
        zero_valid = (
            'Policy: Code Signing\n'
            '  Matching identities\n'
            f'    1) {identity_a} "{private_name}" (CSSMERR_TP_NOT_TRUSTED)\n'
            '  1 identities found\n'
            '  Valid identities only\n'
            '  0 valid identities found\n'
        ).encode()
        self.assertEqual(device._identity_records(zero_valid, valid_only=False), [(identity_a, private_name)])
        self.assertEqual(device._identity_records(zero_valid), [])
        no_keys = (
            'Policy: Code Signing\n'
            '  Matching identities\n'
            '  0 identities found\n'
            '  Valid identities only\n'
            '  0 valid identities found\n'
        ).encode()
        self.assertEqual(device._identity_records(no_keys, valid_only=False), [])
        self.assertEqual(device._identity_records(no_keys), [])

    def test_dual_section_identity_inventory_classification_is_fixed_and_secret_free(self) -> None:
        private_name = 'Apple Development: Private Person (ABCDE12345)'
        fingerprint = 'F' * 40
        with tempfile.TemporaryDirectory() as td:
            keychain = Path(td) / 'private-managed-keychain'
            for keys, code in ((1, 'no-valid-identity'), (0, 'no-key-backed-identity')):
                matching_line = (
                    f'  1) {fingerprint} "{private_name}" (CSSMERR_TP_NOT_TRUSTED)\n'
                    if keys else ''
                )
                inventory = (
                    'Policy: Code Signing\n'
                    '  Matching identities\n'
                    + matching_line
                    + f'  {keys} identities found\n'
                    '  Valid identities only\n'
                    '  0 valid identities found\n'
                ).encode()
                observed = []

                def security_command(args: list[str], **_kwargs):
                    observed.append(args)
                    self.assertEqual(args[-1], str(keychain))
                    if '-v' in args:
                        return b'  0 valid identities found\n'
                    return inventory

                with self.subTest(code=code), patch.object(device, '_run_private', side_effect=security_command):
                    with self.assertRaises(device.DeviceError) as raised:
                        device._selected_signing_identity(keychain, 'ABCDE12345')
                    self.assertEqual(str(raised.exception),
                                     f'Central Apple Development signing identity validation failed ({code})')
                    for private_value in (private_name, fingerprint, 'ABCDE12345', str(keychain)):
                        self.assertNotIn(private_value, str(raised.exception))
                    self.assertEqual(len(observed), 3 if keys else 2)
                    self.assertIn('-v', observed[0])
                    self.assertNotIn('-v', observed[1])

    def test_missing_g3_intermediate_is_repaired_only_for_one_matching_identity(self) -> None:
        fingerprint = SIGNING_CERT_FINGERPRINT
        name = SIGNING_CERT_NAME
        invalid = (
            'Matching identities\n'
            f'1) {fingerprint} "{name}" (CSSMERR_TP_NOT_TRUSTED)\n'
            '1 identity found\n'
            'Valid identities only\n'
            '0 valid identities found\n'
        ).encode()
        valid = f'1) {fingerprint} "{name}"\n1 valid identity found\n'.encode()
        with tempfile.TemporaryDirectory() as td:
            keychain = Path(td) / 'managed.keychain-db'
            repaired = False
            commands: list[list[str]] = []

            def command(argv: list[str], **_kwargs):
                commands.append(argv)
                if '-v' in argv:
                    return valid if repaired else b'0 valid identities found\n'
                return invalid

            def repair(actual_keychain: Path) -> None:
                nonlocal repaired
                self.assertEqual(actual_keychain, keychain)
                repaired = True

            with (patch.object(device, '_run_private', side_effect=command),
                  patch.object(device, '_imported_certificate_trust_diagnostic',
                               return_value='VALID_LEAF_G3_ISSUER_NOT_IN_KEYCHAIN'),
                  patch.object(device, '_imported_certificate_metadata', return_value=('ABCDE12345', '123ABC')),
                  patch.object(device, 'import_pinned_wwdr_g3', side_effect=repair) as imported):
                self.assertEqual(device._selected_signing_identity(keychain, 'ABCDE12345'), fingerprint)
            imported.assert_called_once_with(keychain)
            self.assertTrue(repaired)
            self.assertEqual(sum('-v' in command for command in commands), 2)

    def test_pinned_g3_intermediate_import_is_hash_pinned_and_keychain_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            keychain = Path(td) / 'managed.keychain-db'
            commands: list[list[str]] = []

            def command(argv: list[str]) -> bytes:
                commands.append(argv)
                self.assertEqual(argv[:2], ['security', 'import'])
                self.assertEqual(argv[-2:], ['-k', str(keychain)])
                imported = Path(argv[2])
                self.assertEqual(imported.stat().st_mode & 0o777, 0o600)
                self.assertEqual(hashlib.sha256(imported.read_bytes()).hexdigest(), device.WWDR_G3_DER_SHA256)
                return b''

            device.import_pinned_wwdr_g3(keychain, command=command)
            self.assertEqual(len(commands), 1)
            self.assertFalse(Path(commands[0][2]).exists())
            self.assertNotIn('add-trusted-cert', commands[0])
            self.assertNotIn('trust-settings-import', commands[0])

    def test_dual_section_identity_inventory_rejects_mismatched_or_hostile_counts(self) -> None:
        valid_record = f'  1) {"A" * 40} "Private Person"\n'
        other_record = f'  1) {"B" * 40} "Other Private Person"\n'
        cases = (
            # More matching entries than Security actually reported.
            'Matching identities\n' + valid_record + '  2 identities found\n'
            'Valid identities only\n  0 valid identities found\n',
            # Misleading count in the valid-only section.
            'Matching identities\n' + valid_record + '  1 identities found\n'
            'Valid identities only\n  1 valid identities found\n',
            # Repeated or unsectioned summaries are not independently trustworthy.
            'Matching identities\n' + valid_record + '  1 identities found\n'
            '  1 identities found\n',
            valid_record + '  1 identities found\n  1 valid identities found\n',
            # An identity cannot be valid without being in the matching inventory.
            'Matching identities\n' + valid_record + '  1 identities found\n'
            'Valid identities only\n' + other_record + '  1 valid identities found\n',
            # Reject reversed or repeated Security sections.
            'Valid identities only\n  0 valid identities found\n'
            'Matching identities\n' + valid_record + '  1 identities found\n',
            'Matching identities\n' + valid_record + '  1 identities found\n'
            'Matching identities\n' + valid_record + '  1 identities found\n',
            # A summary cannot be followed by additional numbered identity rows.
            'Matching identities\n  0 identities found\n' + valid_record,
            # An unsectioned summary also closes its numbered-record list.
            '  0 valid identities found\n' + valid_record,
            # Oversized/malformed summaries must not be ignored as harmless headers.
            'Matching identities\n' + valid_record + '  10000000000 identities found\n',
        )
        for raw in cases:
            with self.subTest(input_case=cases.index(raw)):
                with self.assertRaisesRegex(device.DeviceError, 'identity inventory is invalid') as raised:
                    device._identity_records(raw.encode('utf-8'), valid_only=False)
                self.assertNotIn('Private Person', str(raised.exception))
                self.assertNotIn('Other Private Person', str(raised.exception))

    def test_identity_inventory_parser_rejects_private_malformed_output(self) -> None:
        invalid_rows = (
            b'  1) NOT_A_FINGERPRINT "Private Person"\n',
            b'  1) ' + b'A' * 40 + b' "Private Person" (bad\n',
            b'\xff',
            b'private\n' * 10000,
            b'  1) ' + b'A' * 40 + b' "Private Person"\n  2 valid identities found\n',
        )
        for raw in invalid_rows:
            with self.subTest(raw=raw[:24]):
                with self.assertRaisesRegex(device.DeviceError, 'identity inventory is invalid') as raised:
                    device._identity_records(raw)
                self.assertNotIn('Private Person', str(raised.exception))
                self.assertNotIn('NOT_A_FINGERPRINT', str(raised.exception))

    def test_valid_identity_selection_reads_public_leaf_without_nonvalidated_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            keychain = Path(td) / 'managed.keychain-db'
            fingerprint = SIGNING_CERT_FINGERPRINT
            valid = f'  1) {fingerprint} "{SIGNING_CERT_NAME}"\n  1 valid identities found\n'
            def command(args, **_kwargs):
                return SIGNING_CERT_PEM if args[1] == 'find-certificate' else valid.encode()
            with patch.object(device, '_run_private', side_effect=command) as runner:
                self.assertEqual(device._selected_signing_identity(keychain, 'ABCDE12345'), fingerprint)
                self.assertEqual([call.args[0] for call in runner.call_args_list], [
                    ['security', 'find-identity', '-v', '-p', 'codesigning', str(keychain)],
                    ['security', 'find-certificate', '-a', '-p', str(keychain)],
                ])

    def test_public_leaf_is_fingerprint_bound_and_decoded_in_private_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Another public certificate record precedes the selected identity.
            other_der = SIGNING_CERT_DER[:-1] + bytes([SIGNING_CERT_DER[-1] ^ 1])
            inventory = ssl.DER_cert_to_PEM_cert(other_der).encode('ascii') + SIGNING_CERT_PEM
            decode = device.ssl._ssl._test_decode_cert
            def private_decode(path):
                self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
                self.assertEqual(Path(path).parent.stat().st_mode & 0o777, 0o700)
                return decode(path)
            with (patch.object(device, '_run_private', return_value=inventory),
                  patch.object(device.ssl._ssl, '_test_decode_cert', side_effect=private_decode)):
                self.assertEqual(device._imported_certificate_metadata(root / 'keychain', SIGNING_CERT_FINGERPRINT),
                                 ('ABCDE12345', '123ABC'))
            self.assertEqual(list(root.iterdir()), [])

    def test_public_leaf_rejects_missing_malformed_ambiguous_and_unmatched_certificates(self) -> None:
        invalid_der = b'not-a-certificate' * 4
        cases = (
            (b'', SIGNING_CERT_FINGERPRINT),
            (b'x' * (device.MAX_CERTIFICATE_INVENTORY_BYTES + 1), SIGNING_CERT_FINGERPRINT),
            (b'private non-PEM output', SIGNING_CERT_FINGERPRINT),
            (SIGNING_CERT_PEM + b'private trailing output', SIGNING_CERT_FINGERPRINT),
            (SIGNING_CERT_PEM * 2, SIGNING_CERT_FINGERPRINT),
            (SIGNING_CERT_PEM * 17, SIGNING_CERT_FINGERPRINT),
            (SIGNING_CERT_PEM, 'F' * 40),
            (ssl.DER_cert_to_PEM_cert(invalid_der).encode(), hashlib.sha1(invalid_der).hexdigest().upper()),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for raw, fingerprint in cases:
                with self.subTest(case=cases.index((raw, fingerprint))), patch.object(device, '_run_private', return_value=raw):
                    with self.assertRaises(device.DeviceError) as caught:
                        device._imported_certificate_metadata(root / 'keychain', fingerprint)
                self.assertEqual(str(caught.exception), 'Central Apple Development certificate metadata could not be read')
                self.assertEqual(list(root.iterdir()), [])

    def test_public_leaf_requires_exactly_one_valid_subject_ou(self) -> None:
        subjects = (
            (),
            ((('organizationalUnitName', 'ABCDE12345'),), (('organizationalUnitName', 'OTHER12345'),)),
            ((('organizationalUnitName', 'not-a-team'),),),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for subject in subjects:
                with (self.subTest(subject=subject), patch.object(device, '_run_private', return_value=SIGNING_CERT_PEM),
                      patch.object(device.ssl._ssl, '_test_decode_cert', return_value={'subject': subject, 'serialNumber': '123ABC'})):
                    with self.assertRaisesRegex(device.DeviceError, 'certificate metadata could not be read'):
                        device._imported_certificate_metadata(root / 'keychain', SIGNING_CERT_FINGERPRINT)
                self.assertEqual(list(root.iterdir()), [])

    def test_signing_probe_error_is_fixed_and_does_not_reveal_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            keychain = Path(td) / 'managed.keychain-db'
            temp = Path(td)
            fingerprint = SIGNING_CERT_FINGERPRINT
            for command, code in (('codesign', 'codesign'), ('verify', 'verification')):
                with self.subTest(command=command):
                    def private_command(argv: list[str], **_kwargs):
                        if argv[:2] == ['security', 'find-identity']:
                            return f'  1) {fingerprint} "{SIGNING_CERT_NAME}"\n'.encode()
                        if argv[:2] == ['security', 'find-certificate']:
                            return SIGNING_CERT_PEM
                        if argv[0] == 'codesign' and (command == 'codesign' or '--verify' in argv):
                            raise device.DeviceError('private hidden command failure')
                        return b''
                    with patch.object(device, '_run_private', side_effect=private_command):
                        with self.assertRaisesRegex(device.DeviceError, rf'signing probe failed \({code}\)') as raised:
                            device._probe_signing_identity(temp, keychain, 'ABCDE12345')
                        self.assertNotIn('Private Person', str(raised.exception))
                        self.assertNotIn(fingerprint, str(raised.exception))

    def test_p12_credentials_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            good = self.signing_env(Path(td))
            bad = (
                {"CI_APPLE_DEVELOPMENT_P12_PASSWORD": ""},
                {"CI_APPLE_DEVELOPMENT_P12_BASE64": "%%%"},
                {"CI_APPLE_DEVELOPMENT_P12_BASE64": base64.b64encode(b"bad bundle").decode()},
                {"CI_APPLE_DEVELOPMENT_P12_BASE64": "A" * 500000},
                {"CI_APPLE_DEVELOPMENT_P12_PASSWORD": "bad\npassword"},
                {"CI_APPLE_TEAM_ID": "BAD"},
            )
            for mutation in bad:
                with self.subTest(mutation=mutation), patch.dict(os.environ, {**good, **mutation}, clear=True):
                    with self.assertRaisesRegex(device.DeviceError, "unavailable|invalid"):
                        device._p12_credentials()
            with patch.dict(os.environ, good, clear=True):
                self.assertEqual(device._p12_credentials()[0], "ABCDE12345")

    def test_failed_import_retains_cleanup_state_and_does_not_revoke_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runner_temp = Path(td)
            state_path = runner_temp / device.SIGNING_STATE_NAME
            keychain = runner_temp / device.SIGNING_KEYCHAIN_NAME
            bundle = runner_temp / device.SIGNING_BUNDLE_NAME
            secret_path = runner_temp / device.SECRET_NAME
            secret_path.write_text("device-secret\n", encoding="utf-8")
            secret_path.chmod(0o600)
            original = "/Users/runner/login.keychain-db"
            commands: list[list[str]] = []

            def private_command(argv: list[str], **_kwargs):
                commands.append(argv)
                if argv[:4] in (["security", "list-keychains", "-d", "user"], ["security", "default-keychain", "-d", "user"]) and len(argv) == 4:
                    return f'"{original}"\n'.encode()
                if argv[:2] == ["security", "create-keychain"]:
                    Path(argv[-1]).write_bytes(b"keychain")
                if argv[:2] == ["security", "import"]:
                    raise device.DeviceError("Central Apple Development signing command failed")
                if argv[:2] == ["security", "delete-keychain"]:
                    Path(argv[-1]).unlink(missing_ok=True)
                return b""

            with patch.dict(os.environ, self.signing_env(runner_temp), clear=True), patch.object(device, "_run_private", side_effect=private_command):
                with self.assertRaisesRegex(device.DeviceError, "command failed"):
                    device.setup_signing(runner_temp, secret_path)
                self.assertTrue(state_path.is_file())
                self.assertTrue(bundle.is_file())
                device.cleanup_signing()
            self.assertFalse(state_path.exists())
            self.assertFalse(bundle.exists())
            self.assertFalse(keychain.exists())
            self.assertFalse(any(cmd[0] in ("openssl", "curl") for cmd in commands))

    def test_failed_search_restore_still_restores_default_and_removes_p12(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state, keychain, bundle = device._signing_paths(root)
            calls = []
            fail_restore = True
            def command(args, **_kwargs):
                calls.append(args)
                if args[1] == 'list-keychains' and fail_restore:
                    raise device.DeviceError('synthetic restore failure')
                if args[1] == 'delete-keychain':
                    keychain.unlink()
                return b''
            with patch.dict(os.environ, {'RUNNER_TEMP': td}), patch.object(device, '_run_private', side_effect=command):
                self.prepare_cleanup_state(root)
                with self.assertRaisesRegex(device.DeviceError, 'signing cleanup failed'):
                    device.cleanup_signing()
                self.assertTrue(any(args[1] == 'default-keychain' for args in calls))
                self.assertFalse(bundle.exists())
                self.assertFalse(keychain.exists())
                self.assertTrue(state.exists())
                fail_restore = False
                device.cleanup_signing()
                self.assertFalse(state.exists())

    def test_missing_or_malformed_cleanup_state_cannot_retain_raw_p12(self) -> None:
        for missing in (False, True):
            with self.subTest(missing_state=missing), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                with patch.dict(os.environ, {'RUNNER_TEMP': td}), patch.object(device, '_run_private') as command:
                    state, keychain, bundle = self.prepare_cleanup_state(root)
                    if missing:
                        state.unlink()
                    else:
                        state.write_text('{invalid-json')
                    with self.assertRaises(device.DeviceError) as caught:
                        device.cleanup_signing()
                    self.assertFalse(bundle.exists())
                    self.assertTrue(keychain.exists())
                    self.assertEqual(state.exists(), not missing)
                    command.assert_not_called()
                self.assertNotIn(td, str(caught.exception))

    def test_unreadable_cleanup_state_is_safe_after_raw_p12_purge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.dict(os.environ, {'RUNNER_TEMP': td}):
                state, keychain, bundle = self.prepare_cleanup_state(root)
                stderr = io.StringIO()
                with (patch.object(device, '_read_signing_state', side_effect=PermissionError(f'private-state-path:{state}')),
                      patch.object(device, '_run_private') as command, patch.object(device.sys, 'stderr', stderr)):
                    self.assertEqual(device.main(['cleanup']), 2)
                self.assertEqual(stderr.getvalue(), 'Central Apple Development signing cleanup state is unavailable\n')
                self.assertFalse(bundle.exists())
                self.assertTrue(state.exists())
                self.assertTrue(keychain.exists())
                command.assert_not_called()

    def test_cleanup_unlink_errors_are_safe_and_do_not_stop_independent_restoration(self) -> None:
        for failed_file in ('bundle', 'state'):
            with self.subTest(file=failed_file), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                with patch.dict(os.environ, {'RUNNER_TEMP': td}):
                    state, keychain, bundle = self.prepare_cleanup_state(root)
                    failed_path = bundle if failed_file == 'bundle' else state
                    unlink = Path.unlink
                    events = []
                    def guarded_unlink(path, *args, **kwargs):
                        events.append(('unlink', path.name))
                        if path == failed_path:
                            raise PermissionError(f'private-unlink-path:{path}')
                        return unlink(path, *args, **kwargs)
                    def command(args, **_kwargs):
                        events.append(('security', args[1]))
                        if args[1] == 'delete-keychain':
                            keychain.unlink()
                        return b''
                    stderr = io.StringIO()
                    with (patch.object(Path, 'unlink', guarded_unlink),
                          patch.object(device, '_run_private', side_effect=command),
                          patch.object(device.sys, 'stderr', stderr)):
                        self.assertEqual(device.main(['cleanup']), 2)
                    self.assertEqual(stderr.getvalue(), 'Central Apple Development signing cleanup failed\n')
                    self.assertEqual(events[0], ('unlink', bundle.name))
                    self.assertIn(('security', 'list-keychains'), events)
                    self.assertIn(('security', 'default-keychain'), events)
                    self.assertFalse(keychain.exists())
                    self.assertTrue(state.exists())
                    self.assertEqual(bundle.exists(), failed_file == 'bundle')
                    with patch.object(device, '_run_private', side_effect=command):
                        device.cleanup_signing()
                    self.assertEqual(list(root.iterdir()), [])

    def test_p12_secrets_do_not_reach_product_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            entry = root / "device-test.sh"
            entry.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "if 'CI_APPLE_DEVELOPMENT_P12_BASE64' in os.environ: sys.exit(31)\n"
                "if 'CI_APPLE_DEVELOPMENT_P12_PASSWORD' in os.environ: sys.exit(32)\n",
                encoding="utf-8",
            )
            entry.chmod(0o755)
            env = self.signing_env(root)
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(device.run_entrypoint(entry, root, root / "device-context", "device-identifier"), 0)

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
